"""Worker Agent — 纯执行：接收决策结果 → 翻译 → 解析 → 写库"""

import os
import re
import json
import time
import logging
import traceback

from services import name_translate, mysql_writer
from services.llm_client import LLMClient, parse_json_response
from services.mysql_writer import sanitize_column_name
from strategies import get_strategy


logger = logging.getLogger("datadeal")


def _fallback_table_name(file_path: str, sheet_name: str, sheet_index: int) -> str:
    """
    翻译失败时的兜底表名：ods_ + 文件名(去扩展名) + _ + sheet名 + _s + sheet_index
    只保留字母数字和下划线，避免不合法字符。
    """
    base = os.path.splitext(os.path.basename(file_path))[0]
    safe_base = re.sub(r'[^\w]', '_', base).strip('_')
    safe_sheet = re.sub(r'[^\w]', '_', sheet_name).strip('_') if sheet_name else ""
    parts = [f"ods_{safe_base}"]
    if safe_sheet:
        parts.append(safe_sheet)
    parts.append(f"s{sheet_index}")
    name = "_".join(parts)
    # 去掉连续下划线
    name = re.sub(r'_+', '_', name)
    if len(name) > 64:
        name = name[:64]
    return name


def run(decision: dict, file_path: str, sheet_index: int, sheet_name: str,
        config: dict, llm_client: LLMClient, preview_info: dict | None = None) -> dict:
    """
    Worker Agent：执行解析、翻译、写库。

    参数:
        decision: Classifier 的决策结果
            {"strategy": "strategy_xxx", "params": {...}, "table_name_hint": "..."}
        file_path: 文件路径
        sheet_index: Sheet 序号
        sheet_name: Sheet 名称
        config: 全局配置
        llm_client: LLMClient 实例
        preview_info: 可选的预览信息（来自 orchestrator 已 preview 过的结果），
            提供则避免再次读取 Excel；不提供则按需 preview。

    返回:
        {
            "success": bool,
            "table_name": str,
            "strategy": str,
            "rows_written": int,
            "error": str|None,
            "parse_time_ms": int,
        }
    """
    start_time = time.time()
    strategy_name = decision.get("strategy", "unknown")
    # LLM 可能返回 "params": null —— 这里兜底为 dict
    params = decision.get("params") or {}
    table_name_hint = decision.get("table_name_hint", "") or ""

    # 提前定义，防止 except 分支引用未赋值变量
    table_name = ""
    table_description = ""
    column_descriptions: dict = {}

    # 将 classifier 返回的顶层 regions 合并进 params
    if decision.get("regions") and "regions" not in params:
        params["regions"] = decision["regions"]

    db_config = config["database"].copy()
    data_dir = config["scan"]["data_dir"]

    # 计算相对路径（去掉 data_dir 前缀）
    source_rel_path = ""
    try:
        source_rel_path = os.path.relpath(file_path, data_dir).replace("\\", "/")
    except Exception:
        source_rel_path = ""

    # 子表策略标记（horizontal_split / vertical_subtable / multi_header 返回 subtables）
    is_subtable_strategy = strategy_name in ("strategy_horizontal_split", "strategy_vertical_subtable")

    try:
        # Step 0: 对于子表策略，先做 LLM 辅助检测（需要在翻译前完成，因为需要 params）
        if strategy_name == "strategy_vertical_subtable" and not params.get("subtable_regions"):
            from services.excel_preview import read_first_cols
            n_cols = config["parse"].get("subtable_detect_cols", 3)
            fc_result = read_first_cols(file_path, sheet_index, n_cols=n_cols, max_rows=5000)
            fc_data = fc_result.get("first_col_data", [])
            if fc_data:
                subtable_regions = _llm_detect_subtable_regions(
                    fc_data, file_path, sheet_name, llm_client
                )
                if subtable_regions is not None:
                    params["subtable_regions"] = subtable_regions
                    logger.info(f"      LLM 子表区域检测: {len(subtable_regions)} 个子表")

        if strategy_name == "strategy_horizontal_split" and not params.get("regions"):
            from services.excel_preview import run as preview_run_full
            full_preview = preview_run_full(file_path, sheet_index, preview_rows=60)
            preview_data = full_preview.get("preview_data", [])
            if preview_data:
                regions = _llm_detect_horizontal_regions(
                    preview_data, file_path, sheet_name, llm_client,
                    max_col=full_preview.get("max_col", 0)
                )
                if regions:
                    params["regions"] = regions
                    logger.info(f"      LLM 横向分区检测: {len(regions)} 个区域")

        # Step 1: 调用策略解析出中文列名（子表策略需要先解析再逐表翻译）
        # 支持策略层 fallback 契约：返回 {"action": "fallback", "to": "strategy_xxx"}
        # 时由 worker 改用目标策略再次执行（最多 1 跳）
        t_parse = time.time()
        strategy_module = get_strategy(strategy_name)
        parse_result = strategy_module.run(
            file_path=file_path,
            sheet_name=sheet_name,
            table_name="tmp",
            column_names=None,
            params=params,
            llm_client=llm_client,
        )
        if isinstance(parse_result, dict) and parse_result.get("action") == "fallback":
            target = parse_result.get("to") or ""
            reason = parse_result.get("reason", "")
            logger.info(f"      策略 fallback: {strategy_name} → {target} ({reason})")
            if target and target != strategy_name:
                # 把"实际执行的策略名"覆盖到 strategy_name，让后续日志/标记一致
                strategy_name = target
                # 子表标记按新策略重新计算
                is_subtable_strategy = strategy_name in (
                    "strategy_horizontal_split", "strategy_vertical_subtable"
                )
                strategy_module = get_strategy(strategy_name)
                parse_result = strategy_module.run(
                    file_path=file_path,
                    sheet_name=sheet_name,
                    table_name="tmp",
                    column_names=None,
                    params=params,
                    llm_client=llm_client,
                )
                # 防御：再次 fallback 不再追逐
                if isinstance(parse_result, dict) and parse_result.get("action") == "fallback":
                    logger.warning(
                        f"      连续 fallback 被忽略：{strategy_name} 又请求 →"
                        f" {parse_result.get('to')}（仅允许 1 跳）"
                    )
                    raise RuntimeError(
                        f"strategy {strategy_name} returned nested fallback (depth>1)"
                    )
            else:
                raise RuntimeError(
                    f"strategy {strategy_name} returned invalid fallback (to={target!r})"
                )
        logger.info(f"      [耗时] 策略解析(首次): {(time.time() - t_parse) * 1000:.0f}ms")

        # Step 2: 翻译列名
        has_subtables = "subtables" in parse_result
        # 准备一份 preview_info 给翻译用：优先使用 orchestrator 透传，否则现拉
        if preview_info is None:
            from services.excel_preview import run as preview_run
            t_preview = time.time()
            preview_info = preview_run(file_path, sheet_index,
                                       preview_rows=config["parse"]["preview_rows"])
            logger.info(f"      [耗时] Worker预览: {(time.time() - t_preview) * 1000:.0f}ms")
        if has_subtables:
            # 子表策略（horizontal_split / vertical_subtable / multi_header 纵向拆分）：
            # 每个子表独立翻译，避免跨子表去重加后缀

            t_translate = time.time()

            # 第一次翻译：获取表名（用第一个子表的列名）
            first_subtable = parse_result["subtables"][0]
            # 给第一个子表也传它自己的局部数据预览（前几行），避免共用原始 Excel 预览
            # 导致 LLM 缺乏数据上下文而翻译失败
            first_sub_preview = first_subtable.get("rows", [])[:5] or preview_info.get("preview_data", [])
            translate_result = name_translate.run(
                file_path=file_path,
                sheet_name=sheet_name,
                preview_data=first_sub_preview,
                column_hints=first_subtable["columns"],
                llm_client=llm_client,
                preview_rows=config["parse"].get("translate_preview_rows", 5),
            )
            table_name = translate_result.get("table_name", "")
            table_description = translate_result.get("table_description", "")
            column_descriptions = translate_result.get("column_descriptions", {})

            # 翻译第一个子表的列名
            first_en_cols = translate_result.get("column_names", [])
            if first_en_cols and len(first_en_cols) == len(first_subtable["columns"]):
                first_subtable["columns"] = first_en_cols

            # 后续子表独立翻译
            for sub_idx in range(1, len(parse_result["subtables"])):
                sub = parse_result["subtables"][sub_idx]
                # 给子表传它自己的局部数据预览（前几行），避免所有子表共用第一份预览
                sub_preview = sub.get("rows", [])[:5] or preview_info.get("preview_data", [])
                sub_translate = name_translate.run(
                    file_path=file_path,
                    sheet_name=sheet_name,
                    preview_data=sub_preview,
                    column_hints=sub["columns"],
                    llm_client=llm_client,
                    preview_rows=config["parse"].get("translate_preview_rows", 5),
                )
                sub_en_cols = sub_translate.get("column_names", [])
                if sub_en_cols and len(sub_en_cols) == len(sub["columns"]):
                    sub["columns"] = sub_en_cols
                # 合并 column_descriptions
                sub_col_desc = sub_translate.get("column_descriptions", {})
                if sub_col_desc:
                    # 子表间可能有相同列名，加子表后缀区分
                    for k, v in sub_col_desc.items():
                        if k in column_descriptions:
                            column_descriptions[f"{k}_p{sub_idx + 1}"] = v
                        else:
                            column_descriptions[k] = v

            # 翻译失败兜底
            if not table_name or table_name == "ods_unknown":
                table_name = _fallback_table_name(file_path, sheet_name, sheet_index)
                logger.warning(f"      表名翻译失败(子表)，使用兜底表名: {table_name}")

            # 对未成功翻译的子表用 sanitize 兜底
            for sub in parse_result["subtables"]:
                cols = sub.get("columns", [])
                has_cn = any(not str(c).isascii() for c in cols)
                if has_cn:
                    sub["columns"] = [
                        sanitize_column_name(c) if not str(c).isascii() else c
                        for c in cols
                    ]

            logger.info(f"      [耗时] 翻译(子表独立): {(time.time() - t_translate) * 1000:.0f}ms")

        else:
            # 非子表策略：先翻译，再用翻译后的列名解析
            t_translate = time.time()
            translate_result = name_translate.run(
                file_path=file_path,
                sheet_name=sheet_name,
                preview_data=preview_info.get("preview_data", []),
                llm_client=llm_client,
                preview_rows=config["parse"].get("translate_preview_rows", 5),
            )
            logger.info(f"      [耗时] 翻译首次: {(time.time() - t_translate) * 1000:.0f}ms")

            table_name = translate_result.get("table_name", "")
            column_names_en = translate_result.get("column_names", [])
            column_descriptions = translate_result.get("column_descriptions", {})
            table_description = translate_result.get("table_description", "")
            translate_failed = not table_name or table_name == "ods_unknown" or not column_names_en

            # 检查列数是否与策略解析匹配：首次翻译只看预览数据，可能漏列
            cn_columns = parse_result.get("columns", [])
            if not translate_failed and cn_columns and len(column_names_en) != len(cn_columns):
                logger.warning(f"      翻译列数不匹配: 策略解析 {len(cn_columns)} 列, 翻译返回 {len(column_names_en)} 列, 触发二次翻译")
                translate_failed = True

            # 首次翻译失败，用策略解析出的中文列名做二次翻译
            if translate_failed and cn_columns:
                t_retry = time.time()
                retry_result = name_translate.run(
                    file_path=file_path,
                    sheet_name=sheet_name,
                    preview_data=preview_info.get("preview_data", []),
                    column_hints=cn_columns,
                    llm_client=llm_client,
                    preview_rows=config["parse"].get("translate_preview_rows", 5),
                )
                logger.info(f"      [耗时] 翻译二次: {(time.time() - t_retry) * 1000:.0f}ms")
                retry_table = retry_result.get("table_name", "")
                retry_cols = retry_result.get("column_names", [])
                if retry_table and retry_table != "ods_unknown":
                    table_name = retry_table
                if retry_cols and len(retry_cols) == len(cn_columns):
                    # 列数完全匹配才采用，避免半英文半 sanitized 中文的尴尬混搭
                    column_names_en = list(retry_cols)
                    retry_col_desc = retry_result.get("column_descriptions", {})
                    retry_table_desc = retry_result.get("table_description", "")
                    if retry_col_desc:
                        column_descriptions = retry_col_desc
                    if retry_table_desc:
                        table_description = retry_table_desc
                else:
                    if retry_cols:
                        logger.warning(
                            f"      二次翻译列数仍不匹配 ({len(retry_cols)} vs {len(cn_columns)})，整体回退 sanitize 兜底"
                        )
                    column_names_en = []  # 触发下面的 sanitize 兜底

            # 翻译仍然失败 — 用兜底表名 + 中文列名 sanitize 继续写库
            if not table_name or table_name == "ods_unknown" or not column_names_en:
                fallback_name = _fallback_table_name(file_path, sheet_name, sheet_index)
                if not table_name or table_name == "ods_unknown":
                    table_name = fallback_name
                    logger.warning(f"      表名翻译失败，使用兜底表名: {table_name}")
                if not column_names_en:
                    cn_cols = parse_result.get("columns", [])
                    if cn_cols:
                        column_names_en = [sanitize_column_name(c) for c in cn_cols]
                        logger.warning(
                            f"      列名翻译失败，整体使用 sanitize 兜底({len(column_names_en)}列)"
                        )
                    else:
                        elapsed = int((time.time() - start_time) * 1000)
                        return {
                            "success": False,
                            "table_name": table_name or fallback_name,
                            "strategy": strategy_name,
                            "rows_written": 0,
                            "error": "列名翻译失败且策略未解析出中文列名，跳过写库",
                            "parse_time_ms": elapsed,
                        }

            # 用翻译后的英文列名重新解析
            if column_names_en:
                t_reparse = time.time()
                parse_result = strategy_module.run(
                    file_path=file_path,
                    sheet_name=sheet_name,
                    table_name="tmp",
                    column_names=column_names_en,
                    params=params,
                    llm_client=llm_client,
                )
                logger.info(f"      [耗时] 策略解析(二次): {(time.time() - t_reparse) * 1000:.0f}ms")

        # 翻译仍然失败（子表策略）— 用兜底表名继续写库
        if has_subtables and "subtables" in parse_result:
            if not table_name or table_name == "ods_unknown":
                table_name = _fallback_table_name(file_path, sheet_name, sheet_index)
                logger.warning(f"      表名翻译失败(子表)，使用兜底表名: {table_name}")

        # 如果 orchestrator 已指定最终表名（含 sheet 后缀和全局去重），优先使用
        if table_name_hint and table_name_hint not in ("ods_unknown", "ods_xxx"):
            table_name = table_name_hint

        # Step 3: 处理结果（可能含子表）
        t_write = time.time()
        if "subtables" in parse_result:
            # 有子表的情况（horizontal_split / vertical_subtable）
            total_written = 0
            subtable_results = []  # 记录每个子表的实际写入信息
            for idx, subtable in enumerate(parse_result["subtables"]):
                if len(parse_result["subtables"]) > 1:
                    suffix = f"_p{idx + 1}"
                    # 子表名拼接后可能超 64，截断时保留后缀以保证唯一性
                    if len(table_name) + len(suffix) > 64:
                        sub_name = table_name[:64 - len(suffix)] + suffix
                    else:
                        sub_name = f"{table_name}{suffix}"
                else:
                    sub_name = table_name
                write_result = mysql_writer.run(
                    table_name=sub_name,
                    columns=subtable["columns"],
                    rows=subtable["rows"],
                    db_config=db_config,
                    batch_size=config["parse"]["batch_size"],
                    source_file=file_path,
                    sheet_name=sheet_name,
                    source_rel_path=source_rel_path,
                    column_comments=column_descriptions,
                    table_description=table_description,
                )
                actual_sub_name = write_result.get("table_name", sub_name)
                subtable_results.append({
                    "table_name": actual_sub_name,
                    "rows_written": write_result.get("rows_written", 0),
                    "success": write_result.get("success", False),
                    "error": write_result.get("error"),
                    "label": subtable.get("label"),
                })
                total_written += write_result.get("rows_written", 0)
                if not write_result.get("success"):
                    return {
                        "success": False,
                        "table_name": actual_sub_name,
                        "strategy": strategy_name,
                        "rows_written": total_written,
                        "error": write_result.get("error"),
                        "skip": write_result.get("skip", False),
                        "parse_time_ms": int((time.time() - start_time) * 1000),
                    }

            elapsed = int((time.time() - start_time) * 1000)
            logger.info(f"      [耗时] 写入(子表): {(time.time() - t_write) * 1000:.0f}ms")
            # 返回第一个子表的 table_name 作为主表名（兼容单子表场景）
            # 同时携带所有子表的写入信息，供 orchestrator 记录日志
            # 构建 column_names JSON 映射
            column_names_json = json.dumps(column_descriptions, ensure_ascii=False) if column_descriptions else None
            return {
                "success": True,
                "table_name": subtable_results[0]["table_name"] if subtable_results else table_name,
                "strategy": strategy_name,
                "rows_written": total_written,
                "error": None,
                "parse_time_ms": elapsed,
                "subtable_results": subtable_results,
                "table_description": table_description,
                "column_names_json": column_names_json,
            }

        else:
            # 单表情况
            write_result = mysql_writer.run(
                table_name=table_name,
                columns=parse_result["columns"],
                rows=parse_result["rows"],
                db_config=db_config,
                batch_size=config["parse"]["batch_size"],
                source_file=file_path,
                sheet_name=sheet_name,
                source_rel_path=source_rel_path,
                column_comments=column_descriptions,
                table_description=table_description,
            )

            elapsed = int((time.time() - start_time) * 1000)
            logger.info(f"      [耗时] 写入(单表): {(time.time() - t_write) * 1000:.0f}ms")
            # 使用 mysql_writer 实际建表的表名（可能被截断至 64 字符）
            actual_table_name = write_result.get("table_name", table_name)
            # 构建 column_names JSON 映射
            column_names_json = json.dumps(column_descriptions, ensure_ascii=False) if column_descriptions else None
            return {
                "success": write_result.get("success", False),
                "table_name": actual_table_name,
                "strategy": strategy_name,
                "rows_written": write_result.get("rows_written", 0),
                "error": write_result.get("error"),
                "skip": write_result.get("skip", False),
                "parse_time_ms": elapsed,
                "table_description": table_description,
                "column_names_json": column_names_json,
            }

    except Exception as e:
        elapsed = int((time.time() - start_time) * 1000)
        # 异常时的兜底表名：优先使用已确定的 table_name_hint / table_name，
        # 二者都为空时退化为 ods_<file>_s<idx>，避免向 orchestrator 传空字符串
        fallback = table_name or table_name_hint or _fallback_table_name(file_path, sheet_name, sheet_index)
        return {
            "success": False,
            "table_name": fallback,
            "strategy": strategy_name,
            "rows_written": 0,
            "error": f"{type(e).__name__}: {str(e)}\n{traceback.format_exc()}",
            "parse_time_ms": elapsed,
        }


_SUBTABLE_DETECT_PROMPT = """你是一个 Excel 纵向子表分析员。以下是文件 {file_path} 的 Sheet "{sheet_name}" 的前{n_cols}列纵向数据（行号从0开始，已过滤尾部空行）：

{col_data}

请分析这个表格的结构，判断：
1. 是否为纵向多子表（上下堆叠多个独立子表，每个子表有自己的表头）
2. 还是共享表头的分组数据（分组标题行如"一、xxx"后直接是数据行，没有独立表头）

**重要区分**：
- 如果"一、xxx"等分组行后面直接是数据行（如"农场数, 个, 2067, 2093..."），没有重新出现表头行 → 这是共享表头 → 返回 is_shared_header=true
- 如果"一、xxx"等分组行后面又出现了新的表头行（如出现了新的"指标/单位/年份"列名行），形成独立的表格 → 这是纵向多子表 → 返回 subtable_regions
- 大多数带"一、/二、/"分组行的统计年鉴表格都是共享表头，应返回 is_shared_header=true

如果是纵向多子表，请给出每个子表的区域划分。
如果是共享表头的分组数据，请返回 is_shared_header=true。

请严格以 JSON 格式输出：
{{
  "is_shared_header": false,
  "subtable_regions": [
    {{"label": "子表1描述", "header_start": 0, "header_end": 2, "data_start": 3, "data_end": 10}},
    {{"label": "子表2描述", "header_start": 11, "header_end": 13, "data_start": 14, "data_end": 20}}
  ]
}}

行号说明（0-based）：
- header_start: 子表表头起始行（含）
- header_end: 子表表头结束行（含）
- data_start: 子表数据起始行（含）
- data_end: 子表数据结束行（不含）"""


def _llm_detect_subtable_regions(first_col_data: list, file_path: str,
                                  sheet_name: str, llm_client: LLMClient) -> list | None:
    """调用 LLM 检测纵向子表区域"""
    # 格式化前几列数据
    col_lines = []
    for i, row in enumerate(first_col_data):
        cells = [str(v)[:40] if v is not None else "" for v in row]
        col_lines.append(f"Row{i}: {', '.join(cells)}")
    col_data_str = "\n".join(col_lines)

    n_cols = len(first_col_data[0]) if first_col_data else 0
    prompt = _SUBTABLE_DETECT_PROMPT.format(
        file_path=file_path,
        sheet_name=sheet_name,
        n_cols=n_cols,
        col_data=col_data_str,
    )

    try:
        t0 = time.time()
        content = llm_client.chat_with_retry("standard", [{"role": "user", "content": prompt}])
        logger.info(f"      [耗时] LLM子表检测: {(time.time() - t0) * 1000:.0f}ms")

        result = parse_json_response(content)
        if result is None:
            logger.warning(f"      LLM子表检测 JSON解析失败: {content[:200]}")
            return None

        # 共享表头 → 返回空列表（让策略回退到 multi_header）
        if result.get("is_shared_header", False):
            return []

        regions = result.get("subtable_regions", [])
        if not regions:
            return None

        # 校验每个 region 的必要字段，并对越界做 clamp
        n_rows = len(first_col_data)
        valid_regions = []
        for r in regions:
            if not all(k in r for k in ("header_start", "header_end", "data_start")):
                continue
            # clamp 行号到合法区间
            try:
                hs = max(0, min(int(r["header_start"]), n_rows - 1))
                he = max(hs, min(int(r["header_end"]), n_rows - 1))
                ds = max(he + 1, min(int(r["data_start"]), n_rows))
                de = int(r.get("data_end", n_rows))
                de = max(ds, min(de, n_rows))
            except (TypeError, ValueError):
                continue
            r["header_start"], r["header_end"] = hs, he
            r["data_start"], r["data_end"] = ds, de
            r.setdefault("label", f"p{len(valid_regions) + 1}")
            valid_regions.append(r)

        return valid_regions if valid_regions else None

    except Exception as e:
        logger.warning(f"      LLM子表检测异常: {e}")
        return None


_HORIZONTAL_DETECT_PROMPT = """你是一个 Excel 横向分区分析员。以下是文件 {file_path} 的 Sheet "{sheet_name}" 的前60行预览数据（行号从0开始，列号从0开始）：

{preview_data}

总列数: {max_col}

请分析这个表格是否为横向并排的多个独立子表（左右分区），可能的情况：
1. 左右子表之间有空列分隔（最常见）
2. 左右子表之间无空列分隔，但表头结构明显不同（例如左侧2列是地区+指标，中间5列是"产量"相关数据，右侧5列是"面积"相关数据）
3. 表头行出现重复的列名结构（如两组"名次/地区/产量"），说明同一行内有多个并排的独立表格

**重要提示**：
- 如果左右两栏只是同一指标的不同年份或不同类别（如"1985年/1986年"），那不是横向分区，是一个表
- 只有当左右是**不同主题**的独立表格时才是横向分区
- 请仔细检查表头行，如果表头中有重复的列名组（如"名次,地区,产量"出现两次以上），必须识别为横向分区

如果是横向分区，请给出每个子表的列范围和表头信息。每个子表可能有不同的多行表头。

请严格以 JSON 格式输出：
{{
  "is_horizontal_split": true,
  "regions": [
    {{
      "col_start": 0,
      "col_end": 5,
      "label": "产量表",
      "header_start": 0,
      "header_end": 2,
      "data_start": 3
    }},
    {{
      "col_start": 5,
      "col_end": 10,
      "label": "面积表",
      "header_start": 0,
      "header_end": 1,
      "data_start": 2
    }}
  ]
}}

如果不是横向分区（只有一个表），请输出：
{{
  "is_horizontal_split": false
}}

字段说明：
- col_start: 子表起始列号（0-based，含）
- col_end: 子表结束列号（0-based，不含）
- label: 子表名称/描述
- header_start: 子表表头起始行号（0-based，含）
- header_end: 子表表头结束行号（0-based，含）
- data_start: 子表数据起始行号（0-based，含）"""


def _llm_detect_horizontal_regions(preview_data: list, file_path: str,
                                    sheet_name: str, llm_client: LLMClient,
                                    max_col: int = 0) -> list | None:
    """调用 LLM 检测横向分区区域"""
    # 格式化预览数据
    preview_lines = []
    for i, row in enumerate(preview_data):
        cells = [str(v)[:30] if v is not None else "" for v in row[:25]]
        preview_lines.append(f"Row{i}: {', '.join(cells)}")
    preview_str = "\n".join(preview_lines)

    prompt = _HORIZONTAL_DETECT_PROMPT.format(
        file_path=file_path,
        sheet_name=sheet_name,
        preview_data=preview_str,
        max_col=max_col,
    )

    try:
        t0 = time.time()
        content = llm_client.chat_with_retry("standard", [{"role": "user", "content": prompt}])
        logger.info(f"      [耗时] LLM横向分区检测: {(time.time() - t0) * 1000:.0f}ms")
        logger.debug(f"      [LLM横向分区] 原始返回: {content[:500]}")

        result = parse_json_response(content)
        if result is None:
            logger.warning(f"      LLM横向分区 JSON解析失败: {content[:200]}")
            return None

        if not result.get("is_horizontal_split", False):
            return None

        regions = result.get("regions", [])
        if not regions or len(regions) < 2:
            return None

        # 校验每个 region 的必要字段，并 clamp col 范围
        n_cols = max_col if max_col else (max(len(r) for r in preview_data) if preview_data else 0)
        valid_regions = []
        for r in regions:
            if "col_start" not in r or "col_end" not in r:
                continue
            try:
                cs = max(0, min(int(r["col_start"]), max(0, n_cols - 1)))
                ce = int(r["col_end"])
                if n_cols:
                    ce = max(cs + 1, min(ce, n_cols))
            except (TypeError, ValueError):
                continue
            r["col_start"], r["col_end"] = cs, ce
            r.setdefault("header_start", None)
            r.setdefault("header_end", None)
            r.setdefault("data_start", None)
            r.setdefault("label", f"p{len(valid_regions) + 1}")
            valid_regions.append(r)

        return valid_regions if len(valid_regions) >= 2 else None

    except Exception as e:
        logger.warning(f"      LLM横向分区检测异常: {e}")
        return None

    