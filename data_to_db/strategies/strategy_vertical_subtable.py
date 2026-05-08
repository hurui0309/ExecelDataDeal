"""策略: strategy_vertical_subtable — 纵向多子表（上下堆叠多个表）

核心思路：框线信息作为高优先级结构信号，内容分析作为补充。
- 横向实线下框线 → 表头/数据边界、子表分隔
- 横向点划线下框线 → 子表分隔（极强信号）
- 开头连续多行实线 → 仅格式，忽略结构意义
"""

import re
from services.excel_utils import (
    is_empty_row, is_xls_file, is_header_like_row, rename_id_col,
)
from services.excel_reader import read_sheet
from services.border_info import (
    read_border_info, detect_header_end_by_border,
    detect_vertical_splits_by_border, detect_horizontal_split_cols,
)


DESCRIPTION = (
    '纵向多子表。特征：上下堆叠多个独立子表，子表之间用连续空行分隔，每个子表前有'
    '独立的标题行（仅第一列有值）和表头行（多列有值，如"指标/单位/年份"）。'
    '关键信号：数据中间出现连续空行(1+行)后，又出现新的标题行+表头行。'
    '即使各子表表头内容相同，只要被空行+标题行分隔开，就是纵向多子表。'
    '典型模式：第一个表数据结束后，出现2-3行空行，然后新标题（如"xxx(二)"），'
    '再是表头行，然后新数据。注意：如果分组行（如"一、xxx"）后直接是数据行、'
    '没有空行和新表头，则是 strategy_multi_header。'
)


def run(file_path: str, sheet_name: str, table_name: str, column_names: list = None,
        params: dict = None, llm_client=None) -> dict:
    params = params or {}
    subtable_regions = params.get("subtable_regions")

    data, row_has_hborder = read_sheet(file_path, sheet_name, read_border=True)

    if not data:
        return {"subtables": [], "original_row_count": 0}

    # 去首尾空行
    trim_start = 0
    while data and is_empty_row(data[0]):
        data.pop(0)
        trim_start += 1
    trim_end = 0
    while data and is_empty_row(data[-1]):
        data.pop()
        trim_end += 1

    # 同步裁剪 row_has_hborder
    if row_has_hborder:
        row_has_hborder = row_has_hborder[trim_start:trim_start + len(data)]
    else:
        row_has_hborder = []

    if not data:
        return {"subtables": [], "original_row_count": 0}

    # 横向分区检测：如果数据明显左右分区，请 worker 切到 strategy_horizontal_split
    from strategies.strategy_horizontal_split import _detect_split_col
    split_col = _detect_split_col(data)
    if split_col is not None:
        return {"action": "fallback", "to": "strategy_horizontal_split",
                "reason": f"detect_split_col={split_col}（数据左右分区）"}

    # 截断脚注行
    from services.table_layout import truncate_footnotes
    old_len = len(data)
    data = truncate_footnotes(data)
    if row_has_hborder:
        row_has_hborder = row_has_hborder[:len(data)]

    if not data:
        return {"subtables": [], "original_row_count": 0}

    # LLM 可能返回 subtable_regions=[] 表示"共享表头"
    # 但框线信号更可靠，先尝试框线自动检测，如果检测到子表则优先使用
    # 注意：LLM 返回的行号基于 read_first_cols（未 trim 前导空行），
    # 需要减去 trim_start 对齐到 trim 后的数据
    if subtable_regions and isinstance(subtable_regions, list) and trim_start > 0:
        for r in subtable_regions:
            for key in ("header_start", "header_end", "data_start", "data_end"):
                if key in r and r[key] is not None:
                    r[key] = r[key] - trim_start

    if subtable_regions is not None and len(subtable_regions) == 0:
        # LLM 判断为共享表头，但先让框线检测确认
        border_info = read_border_info(file_path, sheet_name)
        rows_info = None
        if border_info:
            ri = border_info["rows"]
            total_rows = len(ri)
            ri = ri[trim_start:total_rows - trim_end] if trim_end > 0 else ri[trim_start:]
            if len(ri) > len(data):
                ri = ri[:len(data)]
            rows_info = ri

        auto_regions = _detect_vertical_subtables(data, rows_info)
        if auto_regions:
            subtable_regions = auto_regions
        else:
            return {"action": "fallback", "to": "strategy_multi_header",
                    "reason": "LLM/框线均未检测到子表分隔（共享表头）"}

    # ── 自动检测纵向子表边界 ──
    if not subtable_regions:
        # 读取框线信息
        border_info = read_border_info(file_path, sheet_name)
        rows_info = None
        if border_info:
            ri = border_info["rows"]
            total_rows = len(ri)
            ri = ri[trim_start:total_rows - trim_end] if trim_end > 0 else ri[trim_start:]
            if len(ri) > len(data):
                ri = ri[:len(data)]
            rows_info = ri

        subtable_regions = _detect_vertical_subtables(data, rows_info)
        if not subtable_regions:
            return {"action": "fallback", "to": "strategy_multi_header",
                    "reason": "未检测到子表分隔（无空行+新表头）"}

    # ── 按区域提取子表 ──
    from services.mysql_writer import make_unique_columns
    from services.excel_utils import rename_id_col
    from strategies.strategy_multi_header import (
        _merge_multi_row_header, _merge_indicator_column, _ffill_indicator_column,
        _clean_cell, _has_numeric_columns, _verify_header_range_via_llm,
        merge_multi_row_header_with_llm,
    )

    subtables = []
    for idx, region in enumerate(subtable_regions):
        label = region.get("label", f"p{idx + 1}")
        h_start = region.get("header_start", 0)
        h_end = region.get("header_end", h_start)
        d_start = region.get("data_start", h_end + 1)
        d_end = region.get("data_end", len(data))

        columns = _merge_multi_row_header(data, h_start, h_end)
        columns = make_unique_columns(columns)
        columns = [rename_id_col(c) for c in columns]

        # 列名校验：合并后含纯数字则回退一行
        header_range_uncertain = False
        if _has_numeric_columns(columns, h_end, data):
            if h_end > h_start:
                h_end -= 1
                columns = _merge_multi_row_header(data, h_start, h_end)
                columns = make_unique_columns(columns)
                columns = [rename_id_col(c) for c in columns]
            header_range_uncertain = True

        # LLM 辅助表头范围校验
        if header_range_uncertain and h_end > h_start:
            llm_header_result = _verify_header_range_via_llm(
                data, h_start, h_end, row_has_hborder, file_path, sheet_name, trim_start
            )
            if llm_header_result is not None:
                new_h_end = llm_header_result.get("header_end", h_end)
                new_d_start = llm_header_result.get("data_start")
                if new_h_end != h_end and new_h_end >= 0:
                    h_end = new_h_end
                    if new_d_start is not None and new_d_start > h_end:
                        d_start = new_d_start
                    columns = _merge_multi_row_header(data, h_start, h_end)
                    columns = make_unique_columns(columns)
                    columns = [rename_id_col(c) for c in columns]

        # 方案3：多行表头合并 + LLM融合（统一入口）
        columns = merge_multi_row_header_with_llm(
            data, h_start, h_end,
            row_has_hborder, file_path, sheet_name, trim_start,
            llm_client, code_columns=columns
        )

        if column_names and len(column_names) == len(columns):
            columns = column_names

        sub_data = [list(row) for row in data[d_start:min(d_end, len(data))]]
        sub_hborder = row_has_hborder[d_start:min(d_end, len(data))] if row_has_hborder else None
        _merge_indicator_column(sub_data, 0, sub_hborder)
        _ffill_indicator_column(sub_data, 0, sub_hborder)

        rows = []
        for row in sub_data:
            if is_empty_row(row):
                continue
            row_values = [_clean_cell(v) for v in row]
            while len(row_values) < len(columns):
                row_values.append(None)
            rows.append(row_values[:len(columns)])

        if rows:
            subtables.append({"columns": columns, "rows": rows, "label": label})

    total_rows = sum(len(st["rows"]) for st in subtables)
    return {"subtables": subtables, "original_row_count": total_rows}


# ──────────────────────── 子表检测（框线优先） ────────────────────────

def _detect_vertical_subtables(data: list, rows_info: list | None) -> list:
    """
    自动检测纵向子表边界。框线优先，内容补充。

    返回: list[dict] 每个子表区域信息，或空列表表示无纵向子表。
    """
    if len(data) < 4:
        return []

    # ── Step 1: 框线检测 header_end ──
    header_end_by_border = None
    if rows_info:
        raw_he = detect_header_end_by_border(rows_info)
        if raw_he is not None:
            # 校正：如果框线指向的行是数据行，回退一行
            from strategies.strategy_multi_header import _adjust_header_end_if_data_row
            header_end_by_border = _adjust_header_end_if_data_row(data, raw_he)
        else:
            header_end_by_border = None

    # ── Step 2: 内容检测 header_end（作为后备） ──
    header_end_by_content = _find_header_end_by_content(data)

    # 合并：如果两者都有值，取较小值（更保守的表头范围）
    # 因为框线可能指向数据行/分类行，内容检测更可靠
    if header_end_by_border is not None and header_end_by_content is not None:
        header_end = min(header_end_by_border, header_end_by_content)
    elif header_end_by_border is not None:
        header_end = header_end_by_border
    elif header_end_by_content is not None:
        header_end = header_end_by_content
    else:
        header_end = None
    if header_end is None:
        return []

    # ── Step 3: 框线检测纵向分割点 ──
    split_rows_by_border = []
    if rows_info:
        raw_border_splits = detect_vertical_splits_by_border(rows_info, header_end)
        # 过滤：分割点后面必须符合子表分隔模式
        # 模式1: 后面有空行（真正的子表分隔）
        # 模式2: 后面直接是标题行+表头行（无空行分隔的子表）
        for s in raw_border_splits:
            has_subtable_after = False
            for j in range(s + 1, min(s + 6, len(data))):
                if is_empty_row(data[j]):
                    has_subtable_after = True
                    break
                non_empty = [v for v in data[j] if v is not None and str(v).strip() != ""]
                if len(non_empty) == 1:
                    # 单列行 → 可能是标题行，检查后面是否有表头行
                    k = j + 1
                    while k < min(s + 6, len(data)) and is_empty_row(data[k]):
                        k += 1
                    if k < len(data) and is_header_like_row(data[k]):
                        has_subtable_after = True
                        break
            if has_subtable_after:
                split_rows_by_border.append(s)

    # ── Step 4: 内容检测纵向分割点 ──
    split_rows_by_content = _find_splits_by_content(data, header_end)

    # 合并分割点（去重、合并接近的分割点）
    all_splits = sorted(set(split_rows_by_border + split_rows_by_content))
    # 合并距离 <=2 的分割点
    merged_splits = []
    for s in all_splits:
        if not merged_splits or s - merged_splits[-1] > 2:
            merged_splits.append(s)
    all_splits = merged_splits

    if not all_splits:
        return []

    # ── Step 5: 构建子表区域 ──
    return _build_regions_from_splits(data, header_end, all_splits, rows_info)


def _find_header_end_by_content(data: list) -> int | None:
    """内容分析找 header_end：找到第一个数据行的前一行。
    跳过分类行（只有第一列有值的行）和空行。"""
    from strategies.strategy_multi_header import _find_data_start_row
    data_start = _find_data_start_row(data)
    if data_start < 0:
        return None
    header_end = data_start - 1
    # 跳过空行和分类行（只有第一列有值的行）
    while header_end >= 0:
        if is_empty_row(data[header_end]):
            header_end -= 1
            continue
        # 检查是否是分类行（只有第一列有值，其余列为空）
        non_empty = [v for v in data[header_end] if v is not None and str(v).strip() != ""]
        if len(non_empty) == 1:
            # 单列行是分类行或标题行，不是表头行，跳过
            header_end -= 1
            continue
        # 检查是否是多列表头行
        if is_header_like_row(data[header_end]):
            break
        # 不是表头行也不是分类行，可能是数据行（不该到这里），跳过
        header_end -= 1
    return header_end if header_end >= 0 else None


def _find_splits_by_content(data: list, header_end: int) -> list[int]:
    """
    内容分析找纵向分割点：空行 + 标题行 + 表头行模式。
    返回每个分割点的行索引（数据区域中，子表数据结束的位置）。
    """
    from strategies.strategy_multi_header import _is_footnote_row
    splits = []
    search_start = header_end + 2  # 至少跳过第一行数据
    i = search_start
    while i < len(data):
        if not is_empty_row(data[i]):
            i += 1
            continue

        # 找到空行，向后找非空行（跳过脚注行）
        j = i
        while j < len(data) and (is_empty_row(data[j]) or _is_footnote_row(data[j])):
            j += 1
        if j >= len(data):
            break

        non_empty_j = [v for v in data[j] if v is not None and str(v).strip() != ""]
        if len(non_empty_j) == 1:
            # 单列行 → 可能是标题行，检查后面是否有表头行
            k = j + 1
            while k < len(data) and (is_empty_row(data[k]) or _is_footnote_row(data[k])):
                k += 1
            if k < len(data) and is_header_like_row(data[k]):
                splits.append(i - 1)  # 数据结束在空行前一行
                i = k + 1
                continue

        if is_header_like_row(data[j]):
            splits.append(i - 1)
            i = j + 1
            continue

        i = j + 1

    return splits


def _build_regions_from_splits(data: list, header_end: int, split_rows: list,
                                rows_info: list = None) -> list:
    """从分割点构建子表区域列表。

    split_rows: 数据区域中，子表数据结束的行索引列表。
    每个分割点之后是新子表的标题+表头区域。
    """
    regions = []

    # 第一个子表的标题行
    first_title_idx = -1
    for i in range(header_end - 1, -1, -1):
        if is_empty_row(data[i]):
            continue
        non_empty = [v for v in data[i] if v is not None and str(v).strip() != ""]
        if len(non_empty) == 1:
            first_title_idx = i
            break
        else:
            break

    # 第一个子表：header区域 + 数据到第一个分割点
    header_start = first_title_idx if first_title_idx >= 0 else 0
    # 向上找 header_start（包含多行表头的补充行）
    for i in range(header_end, -1, -1):
        if is_empty_row(data[i]):
            continue
        if i <= first_title_idx:
            break
        non_empty = [v for v in data[i] if v is not None and str(v).strip() != ""]
        if len(non_empty) > 1 or is_header_like_row(data[i]):
            header_start = i
        else:
            break

    first_data_end = split_rows[0] + 1 if split_rows else len(data)
    label1 = _extract_label(data[first_title_idx]) if first_title_idx >= 0 else "p1"
    regions.append({
        "label": label1,
        "header_start": header_start,
        "header_end": header_end,
        "data_start": header_end + 1,
        "data_end": first_data_end,
    })

    # 后续子表：每个分割点之后是一个新的子表
    # 构建分割点附近的局部框线信息（用于 _find_subtable_start_after_split）
    for idx, split_row in enumerate(split_rows):
        # 在分割点之后找标题行和表头行（传入框线信息）
        title_idx, new_header_end = _find_subtable_start_after_split(
            data, split_row + 1, rows_info
        )
        if new_header_end < 0:
            continue

        # 下一个分割点或数据末尾
        if idx + 1 < len(split_rows):
            next_data_end = split_rows[idx + 1] + 1
        else:
            next_data_end = len(data)

        label = _extract_label(data[title_idx]) if title_idx >= 0 else f"p{idx + 2}"
        new_header_start = title_idx if title_idx >= 0 else new_header_end
        # 向上扩展 header_start（结合框线信号辅助判断表头边界）
        new_header_start = _find_header_start_with_border(
            data, new_header_end, split_row, rows_info
        )

        regions.append({
            "label": label,
            "header_start": new_header_start,
            "header_end": new_header_end,
            "data_start": new_header_end + 1,
            "data_end": next_data_end,
        })

    return regions if len(regions) >= 2 else []


def _find_subtable_start_after_split(data: list, start_row: int,
                                     rows_info: list = None) -> tuple:
    """
    在分割点之后寻找新子表的标题行和表头行。
    结合框线信息辅助判断表头边界（header_end）。
    返回: (title_idx, header_end)
    """
    from strategies.strategy_multi_header import _is_footnote_row, _adjust_header_end_if_data_row
    n = len(data)
    i = start_row

    # 跳过空行和脚注行
    while i < n and (is_empty_row(data[i]) or _is_footnote_row(data[i])):
        i += 1
    if i >= n:
        return -1, -1

    # ── 框线辅助：在分割点后局部区域检测 header_end ──
    header_end_by_border = None
    if rows_info:
        # 只在分割点后的小范围内（最多15行）找框线边界
        local_rows = rows_info[start_row:min(start_row + 15, len(rows_info))]
        if local_rows:
            raw_he = detect_header_end_by_border(local_rows)
            if raw_he is not None:
                # 将局部索引转回全局索引
                raw_he_global = start_row + raw_he
                # 校正：如果框线指向的是数据行，回退一行
                header_end_by_border = _adjust_header_end_if_data_row(data, raw_he_global)

    # 检查是否是标题行（仅第一列有值）
    non_empty = [v for v in data[i] if v is not None and str(v).strip() != ""]
    if len(non_empty) == 1:
        # 可能是标题行，找后面的表头行
        j = i + 1
        while j < n and (is_empty_row(data[j]) or _is_footnote_row(data[j])):
            j += 1
        if j < n and is_header_like_row(data[j]):
            # 找表头结束行：结合框线信号和内容分析
            header_end = _find_header_end_for_subtable(data, j, n, header_end_by_border)
            return i, header_end
        # 下一行不是表头行
        if is_header_like_row(data[i]):
            return -1, i
        return -1, -1

    # 当前不是标题行，检查是否直接是表头行
    if is_header_like_row(data[i]):
        header_end = _find_header_end_for_subtable(data, i, n, header_end_by_border)
        return -1, header_end

    return -1, -1


def _find_header_end_for_subtable(data: list, header_start_row: int,
                                   n: int, header_end_by_border: int = None) -> int:
    """
    为子表找 header_end：结合框线信号和内容分析。

    优先使用框线信号（如果可用且合理），否则用内容分析（is_header_like_row 向下扩展）。
    """
    # 内容分析：从 header_start_row 向下找连续的表头行
    header_end_by_content = header_start_row
    while header_end_by_content + 1 < n and is_header_like_row(data[header_end_by_content + 1]):
        header_end_by_content += 1

    # 如果没有框线信号，直接用内容分析结果
    if header_end_by_border is None:
        return header_end_by_content

    # 如果框线信号在表头起始行之前或等于起始行，不可靠，用内容分析
    if header_end_by_border < header_start_row:
        return header_end_by_content

    # 如果框线信号比内容分析更保守（更小的 header_end），且在合理范围内
    # 框线指向的行应该是表头区域内的一行
    if header_end_by_border <= header_end_by_content:
        # 框线和内容分析都指向表头区域，取较保守的（框线优先）
        return header_end_by_border

    # 框线信号比内容分析更大：可能内容分析遗漏了后面的表头行
    # 验证框线指向的行是否也像表头行
    if header_end_by_border < n and is_header_like_row(data[header_end_by_border]):
        return header_end_by_border

    # 框线不可靠，回退到内容分析
    return header_end_by_content


def _find_header_start_with_border(data: list, header_end: int, lower_bound: int,
                                    rows_info: list = None) -> int:
    """
    从 header_end 向上找 header_start（内容分析）。

    从 header_end 向上逐行检查，遇到空行跳过，
    遇到标题行（单值长文本）停止，其余行纳入表头范围。
    框线信号的价值在于确定 header_end（调用前已完成），此处不再用于 header_start 判断。
    """
    header_start = header_end

    for i in range(header_end, lower_bound, -1):
        if is_empty_row(data[i]):
            continue
        non_empty = [v for v in data[i] if v is not None and str(v).strip() != ""]
        # 标题行：仅1个非空值且非表头行 → 停止
        if len(non_empty) == 1 and not is_header_like_row(data[i]):
            break
        if len(non_empty) > 1 or is_header_like_row(data[i]):
            header_start = i
        else:
            break

    return header_start







# ──────────────────────── 工具函数 ────────────────────────


def _extract_label(row) -> str:
    for v in row:
        if v is not None and str(v).strip() != "":
            label = str(v).strip()
            return label[:28] + ".." if len(label) > 30 else label
    return "unknown"

    