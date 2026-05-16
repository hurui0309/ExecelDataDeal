"""Classifier Agent — 纯决策：看预览 → 判断策略 → 输出决策 JSON"""

import time
import logging

from services.llm_client import LLMClient, parse_json_response
from strategies import BUILTIN_DESCRIPTIONS


logger = logging.getLogger("datadeal")


# 策略列表的固定部分 prompt 模板
_STRATEGY_LIST_PROMPT = """请判断这个 sheet 的数据格式属于哪种已知策略：

{strategy_descriptions}
- SKIP: 目录/空文件/无有效数据/纯说明备注页（如仅含"搜索马克数据网"等说明文字，无表格结构，或sheet名为"必看说明"/"数据说明"/"使用说明"等纯文字说明sheet）
- UNKNOWN: 无法判断，需要深入分析

判断要点：
1. 优先匹配动态策略（上方以 strategy_ 开头且非预置5种的），若文件格式与某个动态策略的描述吻合则选它
2. 看数据行是否成对出现（每隔一行只有1~2列有值）→ strategy_paired_row_bilingual
3. 看列数是否较多(>8列)且同一行出现两组**独立**表头（左右是不同主题的独立表格，中间可能有空列分隔，也可能没有空列但表头结构明显不同，如左侧是"产量"相关列、右侧是"面积"相关列）→ strategy_horizontal_split。注意：如果左右只是同一表的不同指标类别（如"总量指标"和"速度指标"共享同一行数据），则不是横向分区，应选 strategy_multi_header
4. 看第一行是否已经是列名且第二行起全是数据 → strategy_standard
5. 看表头是否占2行及以上，或者有分组标题行（如"一、xxx"）但**各组共享同一个表头行** → strategy_multi_header
6. 看是否有上下堆叠的多个独立子表 → strategy_vertical_subtable。关键信号：数据中间出现连续空行(1+行)，空行后出现新的标题行（仅第一列有值）+ 新的表头行（多列有值，如"指标/单位/年份"），形成第二个及更多独立表格
7. 否则 → strategy_simple_header

**重要区分：strategy_multi_header vs strategy_vertical_subtable**
- 如果"一、xxx"等分组行后面直接是数据行（如"农场数, 个, 2067, 2093..."），没有重新出现表头行 → 这是共享表头的分组数据 → strategy_multi_header
- 如果数据中间出现**连续空行**，空行后又出现**新的标题行+表头行**，形成上下堆叠的独立表格 → 这是纵向多子表 → strategy_vertical_subtable
- 即使两个子表的表头内容相同（如都是"指标/单位/年份"），只要被空行+标题行分隔开，就是纵向多子表
- 典型模式：第一个表数据结束后，出现2-3行空行，然后是新标题（如"xxx(二)"），再是表头行，然后是新数据

附加信息：
- 如果选了 strategy_vertical_subtable，有几个子表？每个子表的起止行？
- 如果选了 strategy_horizontal_split，请给出每个横向子表的列范围（col_start, col_end）、表头行范围（header_start, header_end）和数据起始行（data_start）
- 如果选了 strategy_multi_header 或 strategy_simple_header，表头从第几行开始？

请严格以 JSON 格式输出（不要输出其他内容）：
{{
  "strategy": "策略名",
  "params": {{
    "header_start": 0,
    "header_end": 0,
    "data_start": 0
  }},
  "regions": [],
  "table_name_hint": "",
  "confidence": 0.95
}}

其中：
- header_start: 表头起始行号（0-based，含），如果无表头则为-1
- header_end: 表头结束行号（0-based，含），如果无表头则为-1
- data_start: 第一个数据行号（0-based，不含表头和标题行）
- regions: 仅当 strategy=strategy_horizontal_split 时填写，格式为：
  [{{"col_start": 0, "col_end": 5, "label": "产量表", "header_start": 0, "header_end": 2, "data_start": 3}}, ...]
  每个元素描述一个横向子表的列范围和表头行号；如果不确定列范围，填空数组[]
- table_name_hint: 留空即可，后续由翻译模块自动生成；不要填 ods_xxx 之类的占位符"""


def _build_prompt(preview_info: dict, file_path: str,
                  first_col_data: list = None) -> str:
    """构建完整 prompt：策略列表从 BUILTIN 获取"""
    # 格式化预览数据
    preview_rows = preview_info.get("preview_data", [])
    preview_lines = []
    for i, row in enumerate(preview_rows):
        cells = [str(v)[:25] if v is not None else "" for v in row[:20]]
        preview_lines.append(f"Row{i}: {', '.join(cells)}")
    preview_str = "\n".join(preview_lines)

    # 获取所有策略及描述
    desc_lines = [f"- {name}: {desc}" for name, desc in BUILTIN_DESCRIPTIONS.items()]
    strategy_descriptions = "\n".join(desc_lines)

    header = f"""你是一个 Excel 数据入仓分类员。以下是文件 {file_path} 的 Sheet "{preview_info.get('sheet_name', '')}" 的前 {len(preview_rows)} 行预览数据：

{preview_str}

文件元信息：
- 总行数: {preview_info.get('max_row', 0)}, 总列数: {preview_info.get('max_col', 0)}
- 合并单元格数: {preview_info.get('merged_count', 0)}
- 文件大小: {preview_info.get('file_size', 0)}
"""

    # 追加前N列纵向预览（帮助识别表头/数据行位置）
    if first_col_data:
        n_cols = len(first_col_data[0]) if first_col_data else 0
        col_lines = []
        for i, row in enumerate(first_col_data):
            cells = [str(v)[:40] if v is not None else "" for v in row]
            col_lines.append(f"Row{i}: {', '.join(cells)}")
        header += f"""
前{n_cols}列纵向数据（共{len(first_col_data)}行，行号从0开始）：
{chr(10).join(col_lines)}
"""

    return header + _STRATEGY_LIST_PROMPT.format(strategy_descriptions=strategy_descriptions)


def run(file_path: str, sheet_index: int, preview_info: dict,
        llm_client: LLMClient, first_col_data: list | None = None) -> dict:
    """
    Classifier Agent：看预览数据，输出决策 dict。

    返回结构由 ``agents.decision.ClassifierDecision`` 定义并校验：

    .. code-block:: python

        {
            "strategy": "strategy_multi_header",
            "params": {"header_start": 2, "header_end": 4, "data_start": 5},
            "table_name_hint": "",
            "confidence": 0.95,
            "reasoning": "..."
        }

    或 SKIP/UNKNOWN（不携带 params 时仍是合法值）。

    参数:
        file_path: 文件路径
        sheet_index: Sheet 序号
        preview_info: excel_preview 的返回结果（必须含 preview_data）
        llm_client: LLMClient 实例
        first_col_data: 前两列纵向数据（来自 excel_preview.read_first_cols）
    """
    from agents.decision import ClassifierDecision
    valid = set(BUILTIN_DESCRIPTIONS.keys())

    # 入参校验
    if not isinstance(preview_info, dict) or not preview_info.get("preview_data"):
        return ClassifierDecision(
            strategy="UNKNOWN",
            error="preview_info 缺少 preview_data，无法分类",
        ).to_dict()
    if first_col_data is not None and not isinstance(first_col_data, list):
        first_col_data = None

    prompt = _build_prompt(preview_info, file_path, first_col_data)

    t0 = time.time()
    content = llm_client.chat_with_retry("standard", [{"role": "user", "content": prompt}])
    logger.info(f"    [耗时] Classifier LLM调用: {(time.time() - t0) * 1000:.0f}ms")
    logger.debug(f"    [Classifier] LLM原始返回: {content[:1000]}")
    raw = parse_json_response(content)

    if raw is None:
        return ClassifierDecision(
            strategy="UNKNOWN",
            error=f"JSON 解析失败: {content[:200]}",
        ).to_dict()

    decision = ClassifierDecision.from_raw_dict(raw, valid_strategies=valid)
    if decision.error:
        logger.warning(f"    Classifier schema 校验告警: {decision.error}")

    return decision.to_dict()

    