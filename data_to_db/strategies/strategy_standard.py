"""策略: strategy_standard — 标准面板数据（第一行为列名，后续为数据）。

与其他策略统一走 services.excel_reader.read_sheet，享受合并单元格填充、
xls/xlsx/biff8 多路径回退能力。
"""

from __future__ import annotations
from services.excel_reader import read_sheet
from services.excel_utils import is_empty_row
from services.table_layout import clean_cell as _clean_cell, is_footnote_row as _is_footnote_row


# 由 strategies/__init__.py 自动汇总
DESCRIPTION = '标准面板数据。特征：第一行就是列名（如"年份,地区,指标"），第二行起全是数据行，无合并单元格，无标题行。'


def run(file_path: str, sheet_name: str, table_name: str, column_names: list | None = None,
        params: dict | None = None, llm_client=None) -> dict:
    """解析标准面板数据：第一行为列名，后续全是数据。

    返回:
        {"columns": list[str], "rows": list[list], "original_row_count": int}
    """
    data, _ = read_sheet(file_path, sheet_name, read_border=False)
    if not data:
        return {"columns": [], "rows": [], "original_row_count": 0}

    # 去首尾空行
    while data and is_empty_row(data[0]):
        data.pop(0)
    while data and is_empty_row(data[-1]):
        data.pop()
    if not data:
        return {"columns": [], "rows": [], "original_row_count": 0}

    # 第一行为列名
    header_row = data[0]
    columns = [str(c).strip() if c is not None else "" for c in header_row]
    if column_names and len(column_names) == len(columns):
        columns = list(column_names)

    rows: list[list] = []
    for row in data[1:]:
        row_list = [_clean_cell(v) for v in row]
        # 截断脚注行（与其他策略保持一致）
        if _is_footnote_row(row_list):
            break
        # 行长度对齐到列数
        if len(row_list) < len(columns):
            row_list += [None] * (len(columns) - len(row_list))
        else:
            row_list = row_list[:len(columns)]
        rows.append(row_list)

    return {
        "columns": columns,
        "rows": rows,
        "original_row_count": len(rows),
    }
