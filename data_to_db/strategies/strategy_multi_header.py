"""策略: strategy_multi_header — 多行表头（2+ 行表头需合并）

历史包袱：早期此模块曾承担"通用工具库"的角色，被多个 strategy_* 模块通过
``from strategies.strategy_multi_header import _xxx`` 偷偷使用。

R1 重构后：通用工具已迁到 services.table_layout，本模块只保留
"多行表头特有逻辑"。但为避免外部代码大面积变更，仍以 _xxx 别名 re-export，
旧的 ``from strategies.strategy_multi_header import _xxx`` 写法继续可用。
"""

import re

from services.excel_utils import (
    is_empty_row, is_xls_file, is_title_row, is_header_like_row,
    detect_header_end_by_border_util, rename_id_col,
)
from services.excel_reader import read_sheet
# 公共工具（一组 import + re-export 别名，保持向后兼容）
from services.table_layout import (
    clean_cell as _clean_cell,
    normalize_cell_text as _normalize_cell_text,
    count_leading_spaces as _count_leading_spaces,
    clean_header_spaces as _clean_header_spaces,
    trim_trailing_empty_cols as _trim_trailing_empty_cols,
    is_footnote_row as _is_footnote_row,
    truncate_footnotes as _truncate_footnotes,
    is_category_title_text as _is_category_title_text,
    is_header_supplement_text as _is_header_supplement_text,
    find_next_non_empty_row as _find_next_non_empty_row,
    row_has_numeric_data as _row_has_numeric_data,
    find_data_start_row as _find_data_start_row,
    detect_header_range as _detect_header_range,
    adjust_header_end_if_data_row as _adjust_header_end_if_data_row,
    find_header_start_from_end as _find_header_start_from_end,
    has_numeric_columns as _has_numeric_columns,
    build_hborder_groups as _build_hborder_groups,
    fill_group as _fill_group,
    ffill_indicator_column as _ffill_indicator_column,
)


DESCRIPTION = (
    '多行表头+层级指标列+共享表头分组。特征：(1)上方有标题行，然后出现2行或更多行表头'
    '（需要跨行合并才能得到完整列名）；(2)第一列是层级指标名，用缩进表示层级深度'
    '（如"人口与就业"→"人口(万人)"→"总人口"）；(3)分类标题行只有第一列有文字、'
    '其余列为空，数据行有实际数值；(4)可能包含分组标题行（如"一、农垦系统"、'
    '"二、侨办系统"），但各组共享同一套表头（年份等列名），分组行后直接是数据行，'
    '没有出现新的表头行。关键判断：合并单元格多、第一列存在大量缩进、分类行与数据行'
    '交替出现、分组行共享同一表头。如统计年鉴"国民经济和社会发展总量与速度指标"、'
    '"国营农林牧渔场发展情况"等综合指标表。'
)


def run(file_path: str, sheet_name: str, table_name: str, column_names: list = None,
        params: dict = None, llm_client=None) -> dict:
    """
    解析多行表头：2+ 行表头合并后取数据。

    params:
        header_start: 表头起始行索引（0-based）
        header_end: 表头结束行索引（0-based）

    返回:
        {"columns": list[str], "rows": list[list], "original_row_count": int}
    """
    params = params or {}
    header_start = params.get("header_start")
    header_end = params.get("header_end")
    data_start = params.get("data_start")

    # 如果提供了 data_start，推导 header_end
    if data_start is not None and data_start > 0 and header_end is None:
        header_end = data_start - 1

    is_xls = is_xls_file(file_path)

    data, row_has_hborder = read_sheet(file_path, sheet_name, read_border=True)

    if not data:
        return {"columns": [], "rows": [], "original_row_count": 0}

    # 去首尾空行（记录裁剪行数，用于框线信息对齐）
    trim_start = 0
    while data and is_empty_row(data[0]):
        data.pop(0)
        trim_start += 1
    trim_end = 0
    while data and is_empty_row(data[-1]):
        data.pop()
        trim_end += 1

    if not data:
        return {"columns": [], "rows": [], "original_row_count": 0}

    # 同步裁剪 row_has_hborder（与 data 保持对齐）
    if row_has_hborder:
        row_has_hborder = row_has_hborder[trim_start:trim_start + len(data)]
    else:
        row_has_hborder = []

    # 裁剪每行的尾部空列（只保留到最后一列有非空值的列）
    data = _trim_trailing_empty_cols(data)

    if not data:
        return {"columns": [], "rows": [], "original_row_count": 0}

    # 截断脚注行
    old_len = len(data)
    data = _truncate_footnotes(data)
    if row_has_hborder:
        row_has_hborder = row_has_hborder[:len(data)]

    if not data:
        return {"columns": [], "rows": [], "original_row_count": 0}

    # 自动检测表头范围（优先使用 LLM 给出的行号 → 框线信号 → 内容分析）
    if header_start is None or header_end is None:
        if params:
            header_start = params.get("header_start")
            header_end = params.get("header_end")
            data_start_param = params.get("data_start")
            if data_start_param is not None and data_start_param > 0 and header_end is None:
                header_end = data_start_param - 1
        if header_start is None or header_end is None:
            # 框线信号优先
            header_end_border = detect_header_end_by_border_util(file_path, sheet_name, len(data), trim_start, trim_end)
            if header_end_border is not None:
                header_end = header_end_border
                # 校正：如果框线指向的行不像表头行（含大量数字），说明指向了数据行
                header_end = _adjust_header_end_if_data_row(data, header_end)
                # 找到 header_end 后，向上找 header_start
                header_start = _find_header_start_from_end(data, header_end)
            else:
                header_start, header_end = _detect_header_range(data)

    # 合并多行表头
    columns = _merge_multi_row_header(data, header_start, header_end)

    from services.mysql_writer import make_unique_columns
    columns = make_unique_columns(columns)

    # 避免 'id' 列名与 MySQL 自增主键冲突
    columns = [rename_id_col(c) for c in columns]

    # P4: 列名校验 — 如果合并后的列名包含纯数字，说明可能把数据行当成了表头行
    # 此时回退 header_end 一行重新合并
    header_range_uncertain = False
    if _has_numeric_columns(columns, header_end, data):
        if header_end > header_start:
            header_end -= 1
            columns = _merge_multi_row_header(data, header_start, header_end)
            columns = make_unique_columns(columns)
            columns = [rename_id_col(c) for c in columns]
        header_range_uncertain = True

    # LLM 辅助表头范围校验：当框线检测和内容分析都不可靠时，
    # 让 LLM 综合框线+内容判断 header_end 是否正确
    if header_range_uncertain and header_end > header_start:
        llm_header_result = _verify_header_range_via_llm(
            data, header_start, header_end, row_has_hborder,
            file_path, sheet_name, trim_start, llm_client
        )
        if llm_header_result is not None:
            new_header_end = llm_header_result.get("header_end", header_end)
            new_data_start = llm_header_result.get("data_start")
            if new_header_end != header_end and new_header_end >= 0:
                header_end = new_header_end
                if new_data_start is not None and new_data_start > header_end:
                    data_start = new_data_start
                columns = _merge_multi_row_header(data, header_start, header_end)
                columns = make_unique_columns(columns)
                columns = [rename_id_col(c) for c in columns]

    # 方案3：多行表头合并 + LLM融合（统一入口）
    columns = merge_multi_row_header_with_llm(
        data, header_start, header_end,
        row_has_hborder, file_path, sheet_name, trim_start,
        llm_client, code_columns=columns
    )

    # 如果提供了英文列名，用英文列名替换
    if column_names and len(column_names) == len(columns):
        columns = column_names

    # 合并层级指标列（如"人口与就业→人口(万人)→总人口"）
    # 用自动检测的 data_start 校验 LLM 给的值，避免 LLM 偏大导致首行数据丢失
    actual_data_start = header_end + 1
    auto_data_start = _find_data_start_row(data)
    if auto_data_start >= 0 and auto_data_start < actual_data_start:
        # 自动检测的 data_start 更早，说明 LLM/框线给偏了
        # 同步修正 header_end 并重新合并表头
        header_end = auto_data_start - 1
        actual_data_start = auto_data_start
        columns = _merge_multi_row_header(data, header_start, header_end)
        from services.mysql_writer import make_unique_columns
        columns = make_unique_columns(columns)
        columns = [rename_id_col(c) for c in columns]
        if column_names and len(column_names) == len(columns):
            columns = column_names

    # 纵向子表检测：如果数据区中间存在空行+新标题+新表头的模式，
    # 则拆分为多个子表（覆盖 Classifier 未选 vertical_subtable 的场景）
    v_splits = _detect_vertical_splits_in_data(data, actual_data_start)
    if v_splits:
        subtables = _build_subtables_from_splits(
            data, columns, actual_data_start, v_splits, row_has_hborder,
            file_path, sheet_name, trim_start, llm_client
        )
        if subtables and len(subtables) >= 2:
            total_rows = sum(len(st["rows"]) for st in subtables)
            return {
                "subtables": subtables,
                "original_row_count": total_rows,
            }

    _merge_indicator_column(data, actual_data_start, row_has_hborder)
    _ffill_indicator_column(data, actual_data_start, row_has_hborder)

    # 提取数据行
    rows = []
    for i in range(actual_data_start, len(data)):
        row = data[i]
        if is_empty_row(row):
            continue
        row_values = [_clean_cell(v) for v in row]
        while len(row_values) < len(columns):
            row_values.append(None)
        rows.append(row_values[:len(columns)])

    return {
        "columns": columns,
        "rows": rows,
        "original_row_count": len(rows),
    }


def _merge_multi_row_header(data: list, header_start: int, header_end: int) -> list:
    """
    多行表头合并。
    
    对合并单元格展开后的重复值做智能处理：
    如果某行中某列的值与相邻列相同（来自同一合并单元格），
    且下方行该列有独立的细分值，则上方行该列的值属于"父级标题"，
    不应拼入该列的最终名称。
    """
    from services.mysql_writer import sanitize_column_name

    if header_start > header_end:
        header_start = header_end

    # 边界保护：行索引越界时回退到安全值
    header_start = max(0, min(header_start, len(data) - 1))
    header_end = max(0, min(header_end, len(data) - 1))

    if header_start == header_end:
        result = [sanitize_column_name(str(v) if v is not None else "") for v in data[header_start]]
        # 对空列名尝试从数据推断
        for i, col_name in enumerate(result):
            if col_name.startswith("col_") or col_name == "col_empty":
                inferred = _infer_column_name_from_data(data, i, header_end)
                if inferred:
                    result[i] = inferred
        return result

    header_rows = data[header_start:header_end + 1]
    if not header_rows:
        return []

    n_cols = max(len(r) for r in header_rows)
    merged_headers = []

    for col_idx in range(n_cols):
        parts = []
        for row_idx in range(header_start, header_end + 1):
            val = data[row_idx][col_idx] if col_idx < len(data[row_idx]) else None
            if val is not None and str(val).strip():
                stripped = re.sub(r' {2,}', ' ', str(val).strip())
                # P4: 清洗表头中的数字间空格（如 "1 9 8 6年" → "1986年"）
                stripped = _clean_header_spaces(stripped)
                # 判断该值是否来自合并单元格展开（与相邻列值相同）
                if _is_merged_cell_value(data, row_idx, col_idx, header_start, header_end, n_cols):
                    # 这是合并单元格展开的重复值
                    # 但如果 parts 为空，该值提供了列的上下文前缀（如 "1984年" 同时属于两列），应保留
                    if not parts:
                        parts.append(stripped)
                    continue
                # 去重：跳过与已有 parts 末尾相同的值（多行表头中同一列常出现重复）
                if not parts or parts[-1] != stripped:
                    parts.append(stripped)

        if not parts:
            # 当表头中该列无值时，尝试从数据行推断有意义的列名
            inferred = _infer_column_name_from_data(data, col_idx, header_end)
            if inferred:
                merged_headers.append(inferred)
            else:
                # 尝试从左侧相邻列的合并表头推断上下文前缀
                context = _infer_context_from_left_column(merged_headers, col_idx, data, header_start, header_end)
                if context:
                    merged_headers.append(context)
                else:
                    merged_headers.append(sanitize_column_name(f"col_{col_idx}"))
        elif len(parts) == 1:
            # 单行值也做纯中文清洗：去掉中文标点后判断是否为纯中文
            raw = parts[0]
            raw_no_space = raw.replace(" ", "")
            raw_clean = re.sub(r'[：:、，,。.（(）)%％\-—]', '', raw_no_space)
            if re.match(r'^[\u4e00-\u9fff]+$', raw_clean) and 2 <= len(raw_clean) <= 12:
                merged_headers.append(sanitize_column_name(raw_clean))
            else:
                merged_headers.append(sanitize_column_name(raw))
        else:
            joined = "".join(parts)
            # 去掉空格和中文标点后判断是否为纯中文（多行表头拆行后拼接，如"农作物"+"种植业"+"产值"）
            joined_no_space = joined.replace(" ", "")
            joined_clean = re.sub(r'[：:、，,。.（(）)%％\-—]', '', joined_no_space)
            if re.match(r'^[\u4e00-\u9fff]+$', joined_clean) and 2 <= len(joined_clean) <= 12 and not re.search(r'\d', joined_clean):
                merged_headers.append(sanitize_column_name(joined_clean))
            else:
                # 优先直接拼接（如"1988年为"+"1987年％" → "1988年为1987年"）
                # 只在直接拼接结果不合法时才用下划线连接
                direct = sanitize_column_name(joined)
                if direct and direct != "col_empty" and not direct.startswith("col_"):
                    merged_headers.append(direct)
                else:
                    merged_headers.append(sanitize_column_name("_".join(parts)))

    return merged_headers


def _infer_column_name_from_data(data: list, col_idx: int, header_end: int) -> str | None:
    """
    当表头中某列无值时，从数据行推断有意义的列名。
    
    策略：
    - 对于第0列（指标列），如果数据行第一列全是文本，推断为 "项目" 或 "指标"
    - 对于其他列，返回 None（使用默认的 col_N）
    """
    if col_idx != 0:
        return None
    
    from services.mysql_writer import sanitize_column_name
    
    # 检查数据行第一列的内容
    data_start = header_end + 1
    text_count = 0
    total_count = 0
    for i in range(data_start, min(data_start + 10, len(data))):
        row = data[i]
        if is_empty_row(row):
            continue
        if col_idx < len(row) and row[col_idx] is not None:
            val = str(row[col_idx]).strip()
            if val:
                total_count += 1
                # 判断是否是文本（非纯数字）
                if not re.match(r'^-?[\d,]+\.?\d*$', val.replace(' ', '')):
                    text_count += 1
    
    # 如果大部分数据行第一列是文本，推断为指标列
    if total_count >= 2 and text_count > total_count * 0.5:
        return sanitize_column_name("项目")
    
    return None


def _infer_context_from_left_column(merged_headers: list, col_idx: int,
                                     data: list, header_start: int,
                                     header_end: int) -> str | None:
    """
    当某列表头为空时，尝试从左侧相邻列的已合并表头推断上下文前缀。

    场景：多行表头中，某列在上方行与左侧列共享同一个合并单元格值，
    但在下方行有独立的细分值。由于 _is_merged_cell_value 跳过了该共享值，
    导致该列的 parts 为空。此时应从左侧列的合并结果中提取共同前缀。

    例如：
      Col7: "1984年" + "为建国以来最高年"  → "1984年为建国以来最高年"
      Col8: (跳过"1984年") + "为解放前最高年" → parts为空
      → 从 Col7 推断 "1984年" 作为前缀 → "1984年为解放前最高年"
    """
    from services.mysql_writer import sanitize_column_name

    if col_idx == 0 or (col_idx - 1) >= len(merged_headers):
        return None

    left_header = merged_headers[col_idx - 1]
    if not left_header or left_header.startswith("col_"):
        return None

    # 从左侧列的合并表头中，提取与当前列下方细分值匹配的上下文前缀
    # 找当前列在表头下方行的细分值
    sub_parts = []
    for row_idx in range(header_start, header_end + 1):
        cell = data[row_idx][col_idx] if col_idx < len(data[row_idx]) else None
        if cell is None:
            continue
        val = str(cell).strip()
        if not val:
            continue
        # 跳过与左侧列相同的值（合并单元格展开值）
        left_cell = data[row_idx][col_idx - 1] if (col_idx - 1) < len(data[row_idx]) else None
        if left_cell is not None and str(left_cell).strip() == val:
            continue
        sub_parts.append(val)

    if not sub_parts:
        return None

    # 尝试从左侧表头中找到公共前缀
    # 思路：左侧表头如 "1984年为建国以来最高年"，当前列细分值为 "为解放前最高年"
    # 找到左侧表头中与当前列细分值的最长公共后缀/前缀的分界点
    # 简化：找到左侧表头中第一个数字或中文字出现的共同部分
    sub_joined = "".join(sub_parts)

    # 尝试：左侧表头去掉当前列细分值的尾部，剩余部分就是前缀
    # 如 "1984年为建国以来最高年" 去掉 "为建国以来最高年" 的共同部分
    # 但这两者尾部不同，需要找共同前缀

    # 找左侧表头和 sub_joined 的最长公共前缀
    prefix = ""
    for i in range(min(len(left_header), len(sub_joined))):
        if left_header[i] == sub_joined[i]:
            prefix += left_header[i]
        else:
            break

    # 如果公共前缀为空，尝试另一种方式：从左侧表头中提取纯数字+单位前缀
    # 如 "1984年为建国以来最高年" 中提取 "1984年"
    if not prefix:
        # 尝试从左侧表头中找到第一个"细分"词的分界点
        # 典型模式：数字年份 + 中文描述
        m = re.match(r'^(\d{4}年?)', left_header)
        if m:
            prefix = m.group(1)
        else:
            # 尝试找左侧表头和 sub_joined 的最长公共子串（作为前缀）
            for length in range(min(len(left_header), len(sub_joined)), 0, -1):
                for start in range(len(left_header) - length + 1):
                    candidate = left_header[start:start + length]
                    if candidate in sub_joined and start == 0:
                        prefix = candidate
                        break
                if prefix:
                    break

    if prefix and prefix != left_header:
        # 前缀 + 当前列细分值
        result = sanitize_column_name(prefix + sub_joined)
        if result and result != "col_empty" and not result.startswith("col_"):
            return result

    return None


def _is_merged_cell_value(data: list, row_idx: int, col_idx: int,
                          header_start: int, header_end: int, n_cols: int) -> bool:
    """
    判断 data[row_idx][col_idx] 的值是否是合并单元格展开后的重复值。
    
    判定条件：该值与左侧相邻列的值相同（即来自同一合并单元格的展开），
    并且在更下方的行中，该列有独立的细分值（说明该列是合并单元格的子列）。
    """
    val = data[row_idx][col_idx] if col_idx < len(data[row_idx]) else None
    if val is None or not str(val).strip():
        return False

    val_str = str(val).strip()

    # 检查左侧相邻列是否有相同的值
    left_val = data[row_idx][col_idx - 1] if col_idx > 0 and (col_idx - 1) < len(data[row_idx]) else None
    if left_val is None or str(left_val).strip() != val_str:
        return False

    # 左侧列有相同值 → 可能是合并单元格展开
    # 进一步确认：在更下方的行中，当前列是否有独立于左侧列的值
    # 如果下方行中当前列有值但左侧列为空，说明当前列是独立的细分列
    for below_idx in range(row_idx + 1, header_end + 1):
        below_cur = data[below_idx][col_idx] if col_idx < len(data[below_idx]) else None
        below_left = data[below_idx][col_idx - 1] if (col_idx - 1) < len(data[below_idx]) else None

        cur_has = below_cur is not None and str(below_cur).strip() != ""
        left_has = below_left is not None and str(below_left).strip() != ""

        if cur_has and not left_has:
            # 当前列下方有独立值，左侧列为空 → 当前列是细分子列
            # 上方行的合并值属于父级标题，不应拼入
            return True

        if cur_has and left_has:
            # 下方行当前列和左侧列都有值且不同 → 当前列是细分子列
            if str(below_cur).strip() != str(below_left).strip():
                return True

    return False


def _merge_indicator_column(data: list, data_start: int, row_has_hborder: list = None):
    """
    对数据区的分类行做处理：与原表保持一致，不删除任何行。
    
    对于第一列有文本、其余列全空的行（如"一、农垦系统"）：
    - 如果下一行第一列为空，将分类文本前缀拼接到下一行第一列
    - 不删除任何行，保留原表完整结构
    """
    if data_start >= len(data):
        return

    for i in range(data_start, len(data)):
        row = data[i]
        if is_empty_row(row):
            continue

        first_cell = row[0] if len(row) > 0 else None
        first_text = _normalize_cell_text(first_cell)

        # 判断是否为候选分类行：第一列有文本，其余列全为空/None
        rest_empty = all(
            v is None or (isinstance(v, str) and v.strip() == "")
            for v in row[1:]
        )

        if first_text is None or not rest_empty:
            continue  # 不是候选分类行

        # 对分类行做前缀拼接（如果下一行第一列为空）
        next_data_row = _find_next_non_empty_row(data, i + 1)
        if next_data_row is not None and next_data_row < len(data):
            next_first = _normalize_cell_text(data[next_data_row][0]) if len(data[next_data_row]) > 0 else None
            if next_first is None:
                # 下一行第一列为空 → 做前缀拼接
                data[next_data_row][0] = first_text


# ──────────────────────── 框线辅助检测 ────────────────────────


def _merge_header_via_llm(data: list, header_start: int, header_end: int,
                          row_has_hborder: list, file_path: str,
                          sheet_name, trim_start: int, llm_client=None) -> dict | None:
    """
    LLM 先行合并多行表头（方案3：LLM 先行 + 代码校验）。

    总是调用 LLM，提供丰富的上下文（表格标题、按行+按列展示、框线、数据预览），
    要求 LLM 输出 per-column confidence。

    返回: {"columns": list[str], "confidence": float} 或 None
    """
    if llm_client is None:
        from config_loader import load_config
        from services.llm_client import LLMClient
        try:
            config = load_config()
            llm_client = LLMClient(config)
        except Exception:
            return None

    n_cols = max(len(data[r]) for r in range(header_start, header_end + 1))

    # 1. 提取表格标题（表头上方的标题行）
    title_lines = []
    for r in range(0, header_start):
        if is_empty_row(data[r]):
            continue
        if is_title_row(data[r]):
            # 所有非空单元格值相同 → 标题行
            non_empty = [str(v).strip() for v in data[r] if v is not None and str(v).strip()]
            if non_empty:
                title_lines.append(non_empty[0])

    # 2. 构建表头区域原始数据（按行展示）
    header_lines = []
    for r in range(header_start, header_end + 1):
        cells = []
        for c in range(n_cols):
            v = data[r][c] if c < len(data[r]) else None
            cells.append(str(v).strip() if v is not None else "")
        header_lines.append(f"  Row {r}: {cells}")

    # 3. 构建按列组织的表头结构（降低 LLM 认知负担）
    col_structure_lines = []
    for c in range(n_cols):
        parts = []
        for r in range(header_start, header_end + 1):
            v = data[r][c] if c < len(data[r]) else None
            if v is not None and str(v).strip():
                parts.append(str(v).strip())
        if len(parts) == 0:
            col_structure_lines.append(f"  Col{c}: (空)")
        elif len(parts) == 1:
            col_structure_lines.append(f"  Col{c}: [{parts[0]}]")
        else:
            col_structure_lines.append(f"  Col{c}: {' + '.join(repr(p) for p in parts)}")

    # 4. 构建框线信息
    border_lines = []
    for r in range(header_start, header_end + 1):
        idx = r
        if row_has_hborder and idx < len(row_has_hborder) and row_has_hborder[idx]:
            border_lines.append(f"  Row {r} 底部有水平分隔线")

    # 读取更精确的框线信息
    try:
        from services.border_info import read_border_info
        border_info = read_border_info(file_path, sheet_name)
        if border_info:
            rows_info = border_info["rows"]
            total_rows = len(rows_info)
            rows_info_trimmed = rows_info[trim_start:total_rows] if trim_start > 0 else rows_info
            if len(rows_info_trimmed) > len(data):
                rows_info_trimmed = rows_info_trimmed[:len(data)]
            for r in range(header_start, min(header_end + 1, len(rows_info_trimmed))):
                ri = rows_info_trimmed[r]
                if ri.get("bottom_solid") or ri.get("bottom_dash"):
                    border_lines.append(
                        f"  Row {r}: 底部边框(样式={ri.get('bottom_style')}, "
                        f"占比={ri.get('bottom_ratio', 0):.0%})"
                    )
    except Exception:
        pass

    # 5. 提供数据区前几行作为上下文
    data_preview_lines = []
    data_start_row = header_end + 1
    for r in range(data_start_row, min(data_start_row + 3, len(data))):
        cells = []
        for c in range(n_cols):
            v = data[r][c] if c < len(data[r]) else None
            cells.append(str(v).strip() if v is not None else "")
        data_preview_lines.append(f"  Row {r}: {cells}")

    # 6. 组装 prompt
    title_section = ""
    if title_lines:
        title_section = f"\n## 表格标题：\n  {title_lines[-1]}\n"

    prompt = f"""你是Excel多行表头合并专家。下面是一个Excel表格的表头区域，表头跨越 {header_end - header_start + 1} 行（Row {header_start} 到 Row {header_end}），共 {n_cols} 列。

请将多行表头合并为单行列名。规则：
1. 从上到下拼接，同一列上下行的值直接连接（如 "1984年" + "产量" = "1984年产量"）
2. 如果某列某行为空，跳过即可
3. 当同一行多个列的值相同时，需根据上下行确定每个列所属的分组
4. 列名应语义完整：短词应从上方/左侧的父级标题推断完整含义
5. 列名中不要包含空格、特殊符号（％ → %, （ → (, ） → )）
6. 不要用 col_empty 或 col_N 这类占位符
{title_section}
## 表头区域原始数据（按行）：
{chr(10).join(header_lines)}

## 按列组织的表头结构：
{chr(10).join(col_structure_lines)}

## 框线信息：
{chr(10).join(border_lines) if border_lines else "  无特殊框线"}

## 数据区前几行（供参考列的数据含义）：
{chr(10).join(data_preview_lines) if data_preview_lines else "  无"}

请返回JSON格式：
{{"columns": [{{"name": "列1名", "confidence": 0.9}}, {{"name": "列2名", "confidence": 0.8}}, ...]}}

共 {n_cols} 列，必须返回恰好 {n_cols} 个列名。
confidence 取值 0.0~1.0，表示你对这个列名的把握程度。如果某列的表头信息不完整、需要推测，则 confidence 较低。"""

    messages = [{"role": "user", "content": prompt}]

    try:
        result = llm_client.chat_json("standard", messages, temperature=0.05, max_tokens=2048)
        if not result or "columns" not in result:
            return None

        raw_columns = result["columns"]
        # 支持两种返回格式：[{name, confidence}, ...] 或 [str, ...]
        names = []
        confidences = []
        for item in raw_columns:
            if isinstance(item, dict):
                names.append(item.get("name", ""))
                confidences.append(float(item.get("confidence", 0.5)))
            elif isinstance(item, str):
                names.append(item)
                confidences.append(0.7)  # 默认中等置信度

        if len(names) != n_cols or not all(isinstance(c, str) and c.strip() for c in names):
            return None

        from services.mysql_writer import sanitize_column_name
        sanitized = [sanitize_column_name(c) for c in names]
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

        return {"columns": sanitized, "confidence": avg_confidence}
    except Exception as e:
        import logging
        logging.getLogger("datadeal").warning(f"    [LLM] 表头合并辅助失败: {e}")
        return None


def merge_multi_row_header_with_llm(data, header_start, header_end,
                                      row_has_hborder=None, file_path='',
                                      sheet_name='', trim_start=0,
                                      llm_client=None,
                                      code_columns=None) -> list:
    """
    多行表头合并 + LLM融合（方案3）。

    完整流程：
    1. 代码合并多行表头 → make_unique → rename_id
    2. 如果是多行表头(header_end > header_start)，总是调用 LLM 合并 + 融合
    3. 融合后再次 make_unique → rename_id

    如果提供 code_columns，跳过步骤1，直接在 code_columns 基础上做 LLM 融合。
    此时 code_columns 应已完成 make_unique_columns 处理。

    返回: 合并后的列名列表
    """
    from services.mysql_writer import make_unique_columns
    from services.excel_utils import rename_id_col

    if code_columns is None:
        columns = _merge_multi_row_header(data, header_start, header_end)
        columns = make_unique_columns(columns)
        columns = [rename_id_col(c) for c in columns]
    else:
        columns = code_columns

    if header_end > header_start:
        llm_result = _merge_header_via_llm(
            data, header_start, header_end, row_has_hborder,
            file_path, sheet_name, trim_start, llm_client
        )
        columns = _fuse_header_results(columns, llm_result)
        columns = make_unique_columns(columns)
        columns = [rename_id_col(c) for c in columns]

    return columns


def _fuse_header_results(code_columns: list, llm_result: dict) -> list:
    """
    融合代码合并和 LLM 合并的结果（方案3核心逻辑）。

    融合规则：
    - LLM confidence >= 0.8 且无异常列名(col_empty/col_N) → 采用 LLM 结果
    - LLM confidence < 0.8 或有异常列名 → 逐列对比，选更合理的
    - LLM 调用失败 → 回退纯代码
    """
    if llm_result is None:
        return code_columns

    llm_columns = llm_result["columns"]
    llm_confidence = llm_result["confidence"]

    # 列数不一致时回退代码
    if len(llm_columns) != len(code_columns):
        import logging
        logging.getLogger("datadeal").info(
            f"    [融合] LLM列数({len(llm_columns)})≠代码列数({len(code_columns)})，回退代码"
        )
        return code_columns

    # 检测 LLM 结果中的异常列名
    llm_bad = [i for i, c in enumerate(llm_columns) if c == "col_empty" or c.startswith("col_")]
    code_bad = [i for i, c in enumerate(code_columns) if c == "col_empty" or c.startswith("col_")]

    # 高置信度 + 无异常 → 直接采用 LLM
    if llm_confidence >= 0.8 and not llm_bad:
        import logging
        logging.getLogger("datadeal").info(
            f"    [融合] LLM置信度{llm_confidence:.2f}且无异常列名，采用LLM结果"
        )
        return llm_columns

    # 逐列对比融合
    fused = []
    for i in range(len(code_columns)):
        llm_col = llm_columns[i]
        code_col = code_columns[i]

        llm_is_bad = (llm_col == "col_empty" or llm_col.startswith("col_"))
        code_is_bad = (code_col == "col_empty" or code_col.startswith("col_"))

        if llm_is_bad and code_is_bad:
            # 都不行 → 保留代码结果（作为底线）
            fused.append(code_col)
        elif llm_is_bad and not code_is_bad:
            # LLM 不行，代码行 → 用代码
            fused.append(code_col)
        elif not llm_is_bad and code_is_bad:
            # LLM 行，代码不行 → 用 LLM
            fused.append(llm_col)
        else:
            # 都行 → 偏向 LLM（语义理解更好），但如果 LLM 置信度低则用代码
            if llm_confidence >= 0.6:
                fused.append(llm_col)
            else:
                fused.append(code_col)

    import logging
    llm_bad_count = len(llm_bad)
    code_bad_count = len(code_bad)
    fused_bad_count = sum(1 for c in fused if c == "col_empty" or c.startswith("col_"))
    logging.getLogger("datadeal").info(
        f"    [融合] 逐列对比: LLM异常{llm_bad_count}列, 代码异常{code_bad_count}列, "
        f"融合后异常{fused_bad_count}列 (LLM置信度{llm_confidence:.2f})"
    )
    return fused


# ──────────────────────── 纵向子表拆分 ────────────────────────

def _detect_vertical_splits_in_data(data: list, data_start: int) -> list[int]:
    """
    在数据区检测纵向子表分割点。

    分割模式：连续空行（跳过脚注行）+ 新标题行（仅第一列有值）+ 新表头行（多列有值，含表头关键词）。
    返回每个分割点的行索引（空行区域的第一行），用于拆分子表。
    """
    if data_start >= len(data):
        return []

    n = len(data)
    splits = []
    i = data_start + 2  # 至少跳过2行数据

    while i < n:
        # 寻找连续空行区域
        if not is_empty_row(data[i]):
            i += 1
            continue

        # 找到空行起点，跳过脚注行和连续空行，找到第一个非空行
        j = i
        while j < n and (is_empty_row(data[j]) or _is_footnote_row(data[j])):
            j += 1
        if j >= n:
            break

        # 检查非空行是否为新子表的标题行+表头行模式
        non_empty_j = [v for v in data[j] if v is not None and str(v).strip() != ""]

        if len(non_empty_j) == 1:
            # 单列行 → 可能是标题行，检查后面是否有表头行
            k = j + 1
            while k < n and (is_empty_row(data[k]) or _is_footnote_row(data[k])):
                k += 1
            if k < n and is_header_like_row(data[k]):
                splits.append(i)
                i = k + 1
                continue

        if is_header_like_row(data[j]):
            splits.append(i)
            i = j + 1
            continue

        i = j + 1

    return splits


def _build_subtables_from_splits(data: list, columns: list, data_start: int,
                                  splits: list[int], row_has_hborder: list = None,
                                  file_path: str = '', sheet_name: str = '',
                                  trim_start: int = 0, llm_client=None) -> list[dict]:
    """
    根据纵向分割点将数据拆分为多个子表。

    每个子表继承当前表的列名（因为纵向子表通常共享相同的表头结构）。
    分割点之后到下一个分割点（或数据末尾）为一个子表。
    分割点与子表数据之间的标题行、空行、表头行会被跳过。
    """
    if not splits:
        return []

    subtables = []

    # 第一个子表：data_start → 第一个分割点
    first_data_end = splits[0]
    first_rows = _extract_subtable_rows(data, data_start, first_data_end, len(columns), row_has_hborder)
    if first_rows:
        subtables.append({"columns": columns, "rows": first_rows, "label": "p1"})

    # 后续子表：每个分割点之后
    for idx, split_row in enumerate(splits):
        # 找到分割点后的数据起始行（跳过空行、脚注行、标题行、新表头行）
        sub_data_start = _find_subtable_data_start(data, split_row)
        if sub_data_start < 0:
            continue

        # 数据结束行
        if idx + 1 < len(splits):
            sub_data_end = splits[idx + 1]
        else:
            sub_data_end = len(data)

        # 确定子表的列名：如果有新表头行，用新表头行合并；否则继承父表列名
        sub_header_end = sub_data_start - 1
        # 向上找子表表头起始
        sub_header_start = sub_header_end
        for r in range(sub_header_end, split_row, -1):
            if is_empty_row(data[r]):
                continue
            if is_header_like_row(data[r]) or len([v for v in data[r] if v is not None and str(v).strip() != ""]) > 1:
                sub_header_start = r
            else:
                break

        if sub_header_start <= sub_header_end and sub_header_end >= split_row:
            # 有新表头行，合并出新列名（走 LLM 融合）
            sub_columns = merge_multi_row_header_with_llm(
                data, sub_header_start, sub_header_end,
                row_has_hborder, file_path, sheet_name, trim_start,
                llm_client
            )
            actual_data_start = sub_header_end + 1
        else:
            sub_columns = columns
            actual_data_start = sub_data_start

        # 从 actual_data_start 跳过标题行和空行到真正的数据行
        while actual_data_start < sub_data_end:
            if is_empty_row(data[actual_data_start]):
                actual_data_start += 1
                continue
            non_empty = [v for v in data[actual_data_start] if v is not None and str(v).strip() != ""]
            if len(non_empty) == 1 and not is_header_like_row(data[actual_data_start]):
                # 标题行，跳过
                actual_data_start += 1
                continue
            break

        sub_rows = _extract_subtable_rows(data, actual_data_start, sub_data_end, len(sub_columns), row_has_hborder)
        if sub_rows:
            subtables.append({"columns": sub_columns, "rows": sub_rows, "label": f"p{idx + 2}"})

    return subtables


def _find_subtable_data_start(data: list, split_row: int) -> int:
    """
    在分割点之后找到子表的数据起始行。

    跳过：空行、脚注行、标题行（仅第一列有值）、新表头行。
    返回第一个数据行的索引，或 -1 表示未找到。
    """
    n = len(data)
    i = split_row

    # 跳过空行和脚注行
    while i < n and (is_empty_row(data[i]) or _is_footnote_row(data[i])):
        i += 1
    if i >= n:
        return -1

    # 跳过标题行（仅第一列有值的行）
    while i < n:
        non_empty = [v for v in data[i] if v is not None and str(v).strip() != ""]
        if len(non_empty) == 0:
            i += 1
            continue
        if len(non_empty) == 1 and not is_header_like_row(data[i]):
            # 标题行，跳过
            i += 1
            continue
        break

    if i >= n:
        return -1

    # 跳过表头行（含"单位"/"年份"等关键词的行，或年份模式行）
    while i < n and is_header_like_row(data[i]):
        i += 1

    # 再跳过可能的分类行和空行
    while i < n:
        if is_empty_row(data[i]):
            i += 1
            continue
        non_empty = [v for v in data[i] if v is not None and str(v).strip() != ""]
        if len(non_empty) == 1 and not is_header_like_row(data[i]):
            # 分类/标题行，跳过
            i += 1
            continue
        break

    return i if i < n else -1


def _extract_subtable_rows(data: list, start: int, end: int, n_cols: int,
                           row_has_hborder: list = None) -> list[list]:
    """提取子表数据行，应用分类行删除和前向填充。"""
    # 复制子数据区（避免修改原始 data）
    sub_data = [list(row) for row in data[start:end]]
    sub_hborder = row_has_hborder[start:end] if row_has_hborder else None

    _merge_indicator_column(sub_data, 0, sub_hborder)
    _ffill_indicator_column(sub_data, 0, sub_hborder)

    rows = []
    for row in sub_data:
        if is_empty_row(row):
            continue
        row_values = [_clean_cell(v) for v in row]
        while len(row_values) < n_cols:
            row_values.append(None)
        rows.append(row_values[:n_cols])

    return rows


def _verify_header_range_via_llm(data: list, header_start: int, header_end: int,
                                   row_has_hborder: list, file_path: str,
                                   sheet_name, trim_start: int, llm_client=None) -> dict | None:
    """
    LLM 辅助校验表头范围：当规则检测不确定时，让 LLM 综合框线+内容判断。

    返回: {"header_end": int, "data_start": int} 或 None
    """
    if llm_client is None:
        from config_loader import load_config
        from services.llm_client import LLMClient
        try:
            config = load_config()
            llm_client = LLMClient(config)
        except Exception:
            return None

    n_cols = max(len(data[r]) for r in range(max(0, header_start - 1), min(header_end + 5, len(data)))) if data else 0

    # 构建表头区域 + 周围行的文本表示
    show_start = max(0, header_start - 1)
    show_end = min(header_end + 5, len(data))
    lines = []
    for r in range(show_start, show_end):
        cells = []
        for c in range(n_cols):
            v = data[r][c] if c < len(data[r]) else None
            cells.append(str(v).strip() if v is not None else "")
        marker = " ← 当前 header_end" if r == header_end else ""
        marker2 = " ← 当前 header_start" if r == header_start else ""
        lines.append(f"  Row {r}: {cells}{marker}{marker2}")

    # 构建框线信息
    border_lines = []
    for r in range(show_start, show_end):
        if row_has_hborder and r < len(row_has_hborder) and row_has_hborder[r]:
            border_lines.append(f"  Row {r} 底部有水平分隔线")

    # 读取更精确的框线信息
    try:
        from services.border_info import read_border_info
        border_info = read_border_info(file_path, sheet_name)
        if border_info:
            rows_info = border_info["rows"]
            total_rows = len(rows_info)
            rows_info_trimmed = rows_info[trim_start:total_rows] if trim_start > 0 else rows_info
            if len(rows_info_trimmed) > len(data):
                rows_info_trimmed = rows_info_trimmed[:len(data)]
            for r in range(show_start, min(show_end, len(rows_info_trimmed))):
                ri = rows_info_trimmed[r]
                if ri.get("bottom_solid") or ri.get("bottom_dash"):
                    border_lines.append(
                        f"  Row {r}: 底部边框(样式={ri.get('bottom_style')}, "
                        f"占比={ri.get('bottom_ratio', 0):.0%})"
                    )
    except Exception:
        pass

    prompt = f"""你是一个Excel表头范围校验专家。下面是一个Excel表格的部分行数据，当前规则检测认为表头范围是 Row {header_start} 到 Row {header_end}，但检测结果不确定（可能把数据行当成了表头行，或漏掉了表头行）。

请综合框线信息和内容特征，重新判断表头的结束行（header_end）和数据起始行（data_start）。

判断要点：
1. 表头行通常包含列名文字（如"指标"、"年份"、"产量"等），不含大量数字
2. 数据行通常包含数字值
3. 框线信息：表头/数据分界处常有底部边框（实线或点划线）
4. header_end 是表头最后一行（含），data_start 是第一个数据行（含）

## 行数据（Row {show_start} 到 Row {show_end - 1}）：
{chr(10).join(lines)}

## 框线信息：
{chr(10).join(border_lines) if border_lines else "  无特殊框线"}

请返回JSON格式：
{{"header_end": 行号, "data_start": 行号}}

行号必须与上面显示的行号一致。"""

    messages = [{"role": "user", "content": prompt}]

    try:
        result = llm_client.chat_json("standard", messages, temperature=0.05, max_tokens=256)
        if result and "header_end" in result:
            he = result["header_end"]
            ds = result.get("data_start")
            # 校验范围合理性
            if isinstance(he, int) and 0 <= he < len(data) and he >= header_start:
                return {"header_end": he, "data_start": ds if isinstance(ds, int) else None}
        return None
    except Exception as e:
        import logging
        logging.getLogger("datadeal").warning(f"    [LLM] 表头范围校验失败: {e}")
        return None

    