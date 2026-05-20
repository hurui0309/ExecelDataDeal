"""策略: strategy_paired_row_bilingual — 成对行映射表（通用）

处理数据行成对（或多行一组）出现的对照表/映射表：
- 主数据行：有多列有值（如中文指标名）
- 续行：只有部分列有值（如英文翻译），其余为空
- 续行中的非空列值追加为独立列，而非拼接到同一列

典型场景：《中国统计年鉴》与香港特别行政区统计刊物中使用的指标对照表
  Row4: "1,8,12" | "建筑业" | "建造业"           ← 主行
  Row5:          |          |   "Construction"      ← 续行（只有英文翻译）

输出列: [对应表号, 内地名词, 香港名词, 香港名词_续]
输出行: "1,8,12" | "建筑业" | "建造业" | "Construction"
"""

from __future__ import annotations

import os
from services.excel_utils import (
    is_empty_row, is_xls_file, is_title_row, is_header_like_row,
    detect_header_end_by_border_util,
)
from services.excel_reader import read_sheet


DESCRIPTION = (
    '成对行映射表。特征：数据行成对出现——主数据行有多列有值（如中文指标名），'
    '续行只有部分列有值（如英文翻译），其余为空。续行非空列的值追加为独立列而非'
    '拼接到同一列。关键判断：每隔一行就出现只有1~2列有值的行。'
    '如统计名词中英文对照表、指标翻译表。'
)


def _safe_str(val):
    """将单元格值安全转换为字符串，处理 None 和空值"""
    if val is None:
        return ''
    s = str(val).strip()
    if s == 'None':
        return ''
    return s



def _get_cell(row_data, col_idx):
    """安全获取行数据中指定列的值"""
    if row_data is None:
        return ''
    if col_idx < len(row_data):
        return _safe_str(row_data[col_idx])
    return ''


# ──────────────────────── 表头/标题行检测 ────────────────────────


def _find_title_row_above(data: list, header_row: int) -> int:
    """在 header_row 上方寻找标题行（所有非空值相同的行）。"""
    for i in range(header_row - 1, -1, -1):
        if is_empty_row(data[i]):
            continue
        non_empty = [str(v).strip() for v in data[i] if v is not None and str(v).strip() != ""]
        if len(non_empty) >= 2 and len(set(non_empty)) == 1:
            return i
        if len(non_empty) == 1:
            return i
    return 0


def _detect_header_row(data: list) -> int | None:
    """
    自动检测表头行：
    跳过标题行（所有非空值相同），找到第一个看起来像表头的行（文本为主，非数字）。
    """
    for i, row in enumerate(data):
        if is_empty_row(row):
            continue
        if is_title_row(row):
            continue
        non_empty = [v for v in row if v is not None and str(v).strip()]
        if non_empty:
            text_count = sum(1 for v in non_empty if not _is_numeric(v))
            if text_count >= len(non_empty) * 0.5:
                return i
    # fallback: 第一个非标题的非空行
    for i, row in enumerate(data):
        if not is_empty_row(row) and not is_title_row(row):
            return i
    return None




def _is_numeric(value) -> bool:
    """判断值是否为数字"""
    if value is None:
        return False
    try:
        float(str(value).strip())
        return True
    except (ValueError, TypeError):
        return False


# ──────────────────────── 数据提取 ────────────────────────


def _detect_record_row_span(all_rows, data_start_row, header_n_cols,
                              candidates=(2, 3, 4), sample_size=24) -> int:
    """自动判定每条逻辑记录占几行。

    规则：
        - "主行" 应当 ≥ 2 列有值；"续行" 应当 ≤ 主行的 60% 列数（明显短）；
        - 模式 (1 主行 + (k-1) 续行) 重复 ≥ 70% 时才认为是 k；
        - 默认回退到 2（绝大多数双语对照表）。

    candidates 顺序代表"先尝试 2 行→3 行→4 行"，命中第一个达标的。
    """
    total = len(all_rows)
    if data_start_row >= total:
        return 2

    def _is_main(row) -> bool:
        non_empty = [v for v in row[:header_n_cols] if v is not None and str(v).strip()]
        return len(non_empty) >= 2

    def _is_cont(row, main_row_count) -> bool:
        # 续行：非空列数比主行少很多（≤ 主行 60%），且至少 1 列非空
        non_empty = sum(1 for v in row[:header_n_cols]
                         if v is not None and str(v).strip())
        return 1 <= non_empty <= max(1, int(main_row_count * 0.6))

    best_span = 2
    best_score = 0.0
    for k in candidates:
        if k < 2:
            continue
        groups = 0
        ok = 0
        for i in range(data_start_row, min(data_start_row + sample_size * k, total), k):
            main_row = all_rows[i] if i < total else None
            if main_row is None or not _is_main(main_row):
                groups += 1
                continue
            groups += 1
            main_count = sum(1 for v in main_row[:header_n_cols]
                              if v is not None and str(v).strip())
            cont_ok = True
            for offset in range(1, k):
                cont_idx = i + offset
                if cont_idx >= total:
                    cont_ok = False
                    break
                if not _is_cont(all_rows[cont_idx], main_count):
                    cont_ok = False
                    break
            if cont_ok:
                ok += 1
        if groups == 0:
            continue
        score = ok / groups
        if score >= 0.7 and score > best_score:
            best_score = score
            best_span = k
    return best_span


def _detect_continuation_columns(all_rows, data_start_row, header_n_cols, record_row_span, sample_size=20):
    """
    自动检测续行中的非空列，确定哪些列需要追加为独立列。

    逻辑：扫描前 sample_size 组数据行，统计续行中每列非空的次数。
    如果某列在续行中非空次数 > 主行中同列非空次数，或续行非空率 > 阈值，
    则该列需要追加为独立列（列名加 _续 后缀）。

    返回: dict，key=列索引，value=True 表示该列需要追加为独立列
    """
    total_rows = len(all_rows)
    main_nonempty = [0] * header_n_cols
    cont_nonempty = [0] * header_n_cols
    pair_count = 0

    for i in range(data_start_row, min(data_start_row + sample_size * record_row_span, total_rows), record_row_span):
        main_row = all_rows[i] if i < total_rows else None
        if main_row is None:
            continue

        # 统计主行非空列
        for c in range(header_n_cols):
            val = _get_cell(main_row, c)
            if val:
                main_nonempty[c] += 1

        # 统计续行非空列
        for offset in range(1, record_row_span):
            cont_idx = i + offset
            cont_row = all_rows[cont_idx] if cont_idx < total_rows else None
            if cont_row is None:
                continue
            for c in range(header_n_cols):
                val = _get_cell(cont_row, c)
                if val:
                    cont_nonempty[c] += 1

        pair_count += 1

    if pair_count == 0:
        return {}

    # 判断哪些列需要追加：续行非空次数 > 0 且（续行非空率 >= 主行非空率 * 0.3 或续行有独立内容）
    cont_cols = {}
    for c in range(header_n_cols):
        if cont_nonempty[c] > 0:
            # 续行中该列有非空值，且主行中该列也有值（表示续行提供了额外信息）
            cont_cols[c] = True

    return cont_cols


def run(file_path, sheet_name, table_name, column_names=None, params=None, llm_client=None):
    """解析策略: paired_row_bilingual — 成对行映射表（通用）

    处理数据行成对出现的对照表/映射表：
    - 主数据行有多列有值，续行只有部分列有值
    - 续行中的非空列值追加为独立列（列名加 _续 后缀）

    params:
        title_row: 标题行索引（0-based），默认自动检测
        header_row: 表头行索引（0-based），默认自动检测
        data_start_row: 数据起始行索引（0-based），默认自动检测
        record_row_span: 每条逻辑记录占几行，默认 2
        cont_columns: 需要追加为独立列的续行列索引列表，默认自动检测
        cont_suffix: 续行列名后缀，默认 "_续"
    """
    if params is None:
        params = {}

    title_row = params.get('title_row')
    header_row = params.get('header_row')
    data_start_row = params.get('data_start_row')
    # record_row_span 默认 None，启用自动检测；外部仍可显式覆盖（含 2/3/4）
    record_row_span = params.get('record_row_span')
    cont_columns = params.get('cont_columns')  # list of col indices
    cont_suffix = params.get('cont_suffix', '_续')

    # 读取工作表数据
    all_rows, _ = read_sheet(file_path, sheet_name, read_border=False)

    # 截断脚注行
    from strategies.strategy_multi_header import _truncate_footnotes
    all_rows = _truncate_footnotes(all_rows)
    total_rows = len(all_rows)

    # 去首尾空行（记录裁剪行数，用于框线信息对齐）
    trim_start = 0
    while all_rows and is_empty_row(all_rows[0]):
        all_rows.pop(0)
        trim_start += 1
    trim_end = 0
    while all_rows and is_empty_row(all_rows[-1]):
        all_rows.pop()
        trim_end += 1
    total_rows = len(all_rows)

    if not all_rows:
        return {"columns": [], "rows": [], "original_row_count": 0}

    # 如果行号参数未指定，尝试用框线信息辅助检测
    if header_row is None or data_start_row is None:
        header_end_border = detect_header_end_by_border_util(
            file_path, sheet_name, total_rows, trim_start, trim_end)
        if header_end_border is not None:
            if data_start_row is None:
                data_start_row = header_end_border + 1
            if header_row is None:
                header_row = header_end_border

    # 内容启发式回退
    if header_row is None:
        header_row = _detect_header_row(all_rows)

    if header_row is None or header_row >= len(all_rows):
        return {"columns": [], "rows": [], "original_row_count": 0}

    if data_start_row is None:
        # 默认数据行 = 表头行 + 1
        data_start_row = header_row + 1

    if title_row is None:
        title_row = _find_title_row_above(all_rows, header_row)

    # 对数据区的第一列做空值前向填充
    from strategies.strategy_multi_header import _ffill_indicator_column
    _ffill_indicator_column(all_rows, data_start_row)

    # 取表头
    header_row_data = all_rows[header_row]
    n_header_cols = len([v for v in header_row_data if v is not None and str(v).strip()])
    if n_header_cols == 0:
        n_header_cols = len(header_row_data)
    n_cols = max(n_header_cols, len(header_row_data))

    # 生成基础列名
    from services.mysql_writer import sanitize_column_name, make_unique_columns
    base_columns = [sanitize_column_name(str(v) if v is not None else "") for v in header_row_data]
    # 补齐空列名
    for i in range(len(base_columns)):
        if not base_columns[i]:
            base_columns[i] = f"col_{i}"
    base_columns = make_unique_columns(base_columns)

    # 自动检测每条逻辑记录占几行（默认 2，3-4 行模式也支持）
    if record_row_span is None:
        record_row_span = _detect_record_row_span(
            all_rows, data_start_row, len(base_columns)
        )
    record_row_span = max(2, int(record_row_span))

    # 自动检测续行中需要追加的列
    if cont_columns is None:
        cont_col_map = _detect_continuation_columns(all_rows, data_start_row, len(base_columns), record_row_span)
    else:
        cont_col_map = {c: True for c in cont_columns}

    # 构建最终列名：基础列 + 续行列
    columns = list(base_columns)
    cont_col_indices = sorted(cont_col_map.keys())
    cont_col_name_map = {}  # col_idx -> new column index in output
    for c in cont_col_indices:
        suffix = cont_suffix
        new_col_name = base_columns[c] + suffix
        # 确保列名唯一
        existing = set(columns)
        if new_col_name in existing:
            idx = 2
            while f"{base_columns[c]}{suffix}_{idx}" in existing:
                idx += 1
            new_col_name = f"{base_columns[c]}{suffix}_{idx}"
        cont_col_name_map[c] = len(columns)
        columns.append(new_col_name)

    # 如果 LLM 提供了 column_names，优先使用（需匹配列数）
    if column_names and len(column_names) == len(columns):
        columns = column_names
    elif column_names and len(column_names) < len(columns):
        # LLM 列名不足，补齐
        from services.mysql_writer import sanitize_column_name
        padded = list(column_names)
        for i in range(len(column_names), len(columns)):
            padded.append(sanitize_column_name(columns[i]))
        columns = padded
    elif column_names and len(column_names) > len(columns):
        columns = column_names[:len(columns)]

    # 提取数据行并合并续行
    rows = []
    raw_record_count = 0

    for i in range(data_start_row, total_rows, record_row_span):
        main_row = all_rows[i] if i < total_rows else None
        if main_row is None:
            continue

        raw_record_count += 1

        # 提取主行数据
        record = []
        for c in range(len(base_columns)):
            record.append(_get_cell(main_row, c))

        # 提取续行数据并追加到独立列
        for c in cont_col_indices:
            cont_val = ''
            for offset in range(1, record_row_span):
                cont_idx = i + offset
                cont_row = all_rows[cont_idx] if cont_idx < total_rows else None
                if cont_row is not None:
                    val = _get_cell(cont_row, c)
                    if val:
                        if cont_val:
                            cont_val += ' ' + val
                        else:
                            cont_val = val
            record.append(cont_val)

        # 过滤全空记录
        if not any(v for v in record):
            continue

        rows.append(record)

    return {
        "columns": columns,
        "rows": rows,
        "original_row_count": raw_record_count
    }

    