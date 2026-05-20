"""策略: strategy_simple_header — 简单单行表头（指定表头行，后续为数据）"""
from __future__ import annotations

import re
from services.excel_utils import (
    is_empty_row, is_xls_file, detect_header_end_by_border_util,
)
from services.excel_reader import read_sheet


DESCRIPTION = '简单单行表头。特征：上方有1~3行标题/空白/注释，然后出现一行表头，下方全是数据。只有一行有效表头。'


def run(file_path: str, sheet_name: str, table_name: str, column_names: list = None,
        params: dict = None, llm_client=None) -> dict:
    """
    解析简单单行表头：指定表头行，取后续为数据。

    params:
        header_row_index: 表头行索引（0-based），默认自动检测
        data_start: 数据起始行索引（0-based）

    返回:
        {"columns": list[str], "rows": list[list], "original_row_count": int}
    """
    params = params or {}
    header_row_index = params.get("header_row_index")
    data_start = params.get("data_start")

    # 如果提供了 data_start，推导 header_row_index
    # 但用自动检测校验，避免 LLM 偏大导致首行数据丢失
    if data_start is not None and data_start > 0:
        header_row_index = data_start - 1

    data, _ = read_sheet(file_path, sheet_name, read_border=False)

    if not data:
        return {"columns": [], "rows": [], "original_row_count": 0}

    # 去首尾空行（记录裁剪行数，用于框线信息对齐）
    trim_start = 0
    while data and is_empty_row(data[0]):
        data.pop(0)
        trim_start += 1
    while data and is_empty_row(data[-1]):
        data.pop()

    if not data:
        return {"columns": [], "rows": [], "original_row_count": 0}

    # 截断脚注行
    from strategies.strategy_multi_header import _truncate_footnotes
    old_len = len(data)
    data = _truncate_footnotes(data)
    trim_end = old_len - len(data)

    if not data:
        return {"columns": [], "rows": [], "original_row_count": 0}

    # 自动检测 header_row_index：优先 params → 框线信号 → 默认 0
    if header_row_index is None:
        header_end_border = detect_header_end_by_border_util(file_path, sheet_name, len(data), trim_start, trim_end)
        if header_end_border is not None:
            # 校正：如果框线指向的行是数据行，回退一行
            from services.table_layout import adjust_header_end_if_data_row
            header_end_border = adjust_header_end_if_data_row(data, header_end_border)
            # 边框检测到的 header_end 是最后一行表头的索引
            # 多行表头时，请 worker 切到 strategy_multi_header
            if header_end_border >= 1:
                return {"action": "fallback", "to": "strategy_multi_header",
                        "reason": f"border 检测到 header_end={header_end_border}（多行表头）"}
            header_row_index = header_end_border
        else:
            # 无框线信息时，用内容分析检测是否有多行表头
            from services.table_layout import find_data_start_row
            data_start = find_data_start_row(data)
            if data_start >= 2:
                return {"action": "fallback", "to": "strategy_multi_header",
                        "reason": f"内容检测 data_start={data_start}（多行表头）"}
            header_row_index = 0

    return _extract_from_data(data, header_row_index, column_names)


def _merge_thousand_separated_rows(rows, header_col_count):
    """
    处理千位分隔符导致的列拆分。
    当数据行列数大于表头列数时，尝试将符合千位分隔符特征的相邻列合并。
    支持英文逗号、中文空格、全角逗号等千位分隔符。
    """
    merged_rows = []
    for row in rows:
        if len(row) <= header_col_count:
            merged_rows.append(row)
            continue
        
        new_row = []
        i = 0
        while i < len(row):
            cell = row[i]
            cell_str = str(cell).strip() if cell is not None else ""
            
            # 检查是否为千位分隔符拆分的数字：前一部分1-3位数字，后一部分3位数字
            # 支持英文逗号(,)、中文空格(\u3000)、全角逗号(，) 作为分隔符
            if (i + 1 < len(row) and
                re.match(r'^-?\d{1,3}$', cell_str) and
                isinstance(row[i+1], (int, float, str))):
                
                next_str = str(row[i+1]).strip() if row[i+1] is not None else ""
                
                # 模式1: 数字,数字 (如 6,095 → 两列分别为 6 和 095)
                if re.match(r'^\d{3}$', next_str):
                    combined = cell_str + next_str
                    j = i + 2
                    while j < len(row) and re.match(r'^\d{3}$', str(row[j]).strip() if row[j] is not None else ""):
                        combined += str(row[j]).strip()
                        j += 1
                    try:
                        new_row.append(int(combined))
                    except ValueError:
                        new_row.append(combined)
                    i = j
                    continue
                
                # 模式2: 单元格内含千位分隔符 (如 "6,095" 或 "1 974" 或 "1，095")
                # 这种情况在 _clean_cell 中已经处理了空格分隔符
                # 这里处理逗号分隔符
                
            # 模式3: 当前单元格本身就是千位分隔符数字 (如 "6,095")
            if re.match(r'^-?\d{1,3}[,\u3000，]\d{3}([,\u3000，]\d{3})*$', cell_str):
                # 去掉千位分隔符
                cleaned = re.sub(r'[,\u3000，]', '', cell_str)
                try:
                    new_row.append(int(cleaned))
                except ValueError:
                    new_row.append(cell)
                i += 1
                continue
            
            new_row.append(cell)
            i += 1
                
        # 如果合并后列数等于表头列数，则接受合并结果，否则保留原始行以确保安全
        if len(new_row) == header_col_count:
            merged_rows.append(new_row)
        else:
            merged_rows.append(row)
            
    return merged_rows


def _extract_from_data(data, header_row_index, column_names):
    if not data or header_row_index >= len(data):
        return {"columns": [], "rows": [], "original_row_count": 0}
    
    # 对数据区的第一列做空值前向填充
    from strategies.strategy_multi_header import _ffill_indicator_column
    _ffill_indicator_column(data, header_row_index + 1)

    header_row = data[header_row_index]
    columns = [str(c).strip() if c is not None else '' for c in header_row]
    
    if column_names:
        columns = column_names
    
    header_col_count = len([c for c in columns if c != ''])
    if header_col_count == 0:
        header_col_count = len(columns)

    rows = []
    for r_idx, row in enumerate(data[header_row_index + 1:]):
        if all(c is None or str(c).strip() == '' for c in row):
            continue
        # 使用 _clean_cell 统一处理：None/NaN/空字符串 → None，千位分隔符空格合并
        from strategies.strategy_multi_header import _clean_cell
        row_data = [_clean_cell(c) for c in row]
        rows.append(row_data)
    
    # 新增：处理千位分隔符导致的列拆分
    max_data_cols = max([len(r) for r in rows]) if rows else 0
    if max_data_cols > header_col_count:
        rows = _merge_thousand_separated_rows(rows, header_col_count)

    final_rows = []
    for row in rows:
        if len(row) > header_col_count:
            final_rows.append(row[:header_col_count])
        elif len(row) < header_col_count:
            final_rows.append(row + [''] * (header_col_count - len(row)))
        else:
            final_rows.append(row)

    return {
        "columns": columns,
        "rows": final_rows,
        "original_row_count": len(final_rows)
    }

    