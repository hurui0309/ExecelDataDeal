"""策略: strategy_horizontal_split — 横向分区（左右并排多个表，空列分隔）"""

from services.excel_utils import is_empty_row, is_xls_file
from services.excel_reader import read_sheet


DESCRIPTION = (
    '横向分区。特征：列数较多(通常>8列)，左右有2个或多个**独立**的表，中间用1列或'
    '多列空列分隔。关键判断：同一行出现两组不同的表头，且左右表有不同的数据行。'
    '注意：如果左右只是同一表的不同指标类别（如"总量指标"和"速度指标"共享同一行数据），'
    '则不是横向分区。'
)


def run(file_path: str, sheet_name: str, table_name: str, column_names: list = None,
        params: dict = None, llm_client=None) -> dict:
    """
    解析横向分区：按分割列或 LLM 区域拆分为多个子表，每个子表支持多行表头。

    params:
        split_col_index: 分割列索引（自动检测时的后备）
        header_start: 表头起始行索引（0-based，全局后备）
        header_end: 表头结束行索引（0-based，全局后备）
        data_start: 数据起始行索引（0-based，优先于 header_end 推导）
        regions: LLM 提供的区域列表，每个区域包含:
            col_start, col_end, label, header_start, header_end, data_start

    返回:
        {
            "subtables": [
                {"columns": list[str], "rows": list[list], "label": str},
                ...
            ],
            "original_row_count": int,
        }
    """
    params = params or {}
    regions_from_llm = params.get("regions")
    split_col_index = params.get("split_col_index")
    header_start = params.get("header_start")
    header_end = params.get("header_end")
    data_start = params.get("data_start")

    # 如果提供了 data_start，推导 header_end
    if data_start is not None and data_start > 0 and header_end is None:
        header_end = data_start - 1

    is_xls = is_xls_file(file_path)

    data, row_has_hborder = read_sheet(file_path, sheet_name, read_border=True)

    if not data:
        return {"subtables": [], "original_row_count": 0}

    # 同步 row_has_hborder 长度
    if row_has_hborder and len(row_has_hborder) != len(data):
        if len(row_has_hborder) > len(data):
            row_has_hborder = row_has_hborder[:len(data)]
        else:
            row_has_hborder = list(row_has_hborder) + [False] * (len(data) - len(row_has_hborder))

    # 去首尾空行（同时调整行号索引 + 同步裁剪 row_has_hborder）
    trimmed_count = 0
    while data and is_empty_row(data[0]):
        data.pop(0)
        if row_has_hborder:
            row_has_hborder.pop(0)
        trimmed_count += 1

    # 调整全局表头行号
    if trimmed_count > 0:
        if header_start is not None:
            header_start = max(0, header_start - trimmed_count)
        if header_end is not None:
            header_end = max(0, header_end - trimmed_count)
        # 调整 LLM 区域中的行号
        if regions_from_llm:
            for r in regions_from_llm:
                for key in ("header_start", "header_end", "data_start"):
                    if key in r and r[key] is not None and r[key] >= 0:
                        r[key] = max(0, r[key] - trimmed_count)

    while data and is_empty_row(data[-1]):
        data.pop()
        if row_has_hborder:
            row_has_hborder.pop()

    # 截断脚注行
    from services.table_layout import truncate_footnotes
    old_len = len(data)
    data = truncate_footnotes(data)
    if row_has_hborder:
        row_has_hborder = row_has_hborder[:len(data)]

    if not data:
        return {"subtables": [], "original_row_count": 0}

    # 路径选择：
    #   (a) 如果 LLM 给了 regions 且任一 region 含表头信息（header_start/end/data_start），
    #       完全走 LLM regions（最权威，绝不丢失 region 级表头）；
    #   (b) 否则尝试框线检测分割列；
    #   (c) 若框线也没切出来，再回退到 LLM regions（即使没表头）/ 启发式空列。
    has_region_header = bool(regions_from_llm) and any(
        any(k in r and r[k] is not None for k in ("header_start", "header_end", "data_start"))
        for r in regions_from_llm
    )

    if has_region_header:
        subtables = _extract_from_llm_regions(
            data, regions_from_llm, column_names, row_has_hborder,
            file_path, sheet_name, trimmed_count, llm_client,
            global_header_start=header_start, global_header_end=header_end,
            global_data_start=data_start,
        )
        total_rows = sum(len(st["rows"]) for st in subtables)
        return {"subtables": subtables, "original_row_count": total_rows}

    split_by_border = _detect_split_cols_by_border(file_path, sheet_name, data)

    # 优先使用框线检测的区域划分（框线列范围比 LLM 更准确）
    if split_by_border:
        regions = _build_regions_from_split_cols(data, split_by_border)
        # 用 LLM 的 col_start/col_end 区间和当前 region 做"列重叠匹配"，
        # 把 LLM 给的 label / header 信息合并到对应区间
        llm_match: dict[int, dict] = {}
        if regions_from_llm:
            for i, (s_col, e_col, _) in enumerate(regions):
                # 找最大重叠的 LLM region
                best_idx = None
                best_overlap = 0
                for li, lr in enumerate(regions_from_llm):
                    l_s = lr.get("col_start", 0)
                    l_e = lr.get("col_end", e_col) + 1   # LLM 习惯 inclusive
                    overlap = max(0, min(e_col, l_e) - max(s_col, l_s))
                    if overlap > best_overlap:
                        best_overlap = overlap
                        best_idx = li
                if best_idx is not None and best_overlap > 0:
                    llm_match[i] = regions_from_llm[best_idx]

        subtables = []
        for idx, (start_col, end_col, default_label) in enumerate(regions):
            region_data = [row[start_col:end_col] for row in data]
            region_data = _trim_left_empty_cols(region_data)
            region_data = _trim_right_empty_cols(region_data)
            region_cols = column_names[start_col:end_col] if column_names else None
            matched = llm_match.get(idx, {})
            r_h_start = matched.get("header_start")
            r_h_end = matched.get("header_end")
            r_d_start = matched.get("data_start")
            label = matched.get("label", default_label) or default_label
            result = _extract_region(region_data, r_h_start, r_h_end, region_cols,
                                      r_d_start, row_has_hborder,
                                      file_path, sheet_name, trimmed_count, llm_client)
            if result["rows"]:
                subtables.append({
                    "columns": result["columns"],
                    "rows": result["rows"],
                    "label": label or f"p{idx + 1}",
                })
        if subtables:
            total_rows = sum(len(st["rows"]) for st in subtables)
            return {"subtables": subtables, "original_row_count": total_rows}

    # 框线未检测到分割列，尝试 LLM 提供的区域划分
    if regions_from_llm:
        subtables = _extract_from_llm_regions(
            data, regions_from_llm, column_names, row_has_hborder,
            file_path, sheet_name, trimmed_count, llm_client,
            global_header_start=header_start,
            global_header_end=header_end,
            global_data_start=data_start,
        )
    else:
        # 自动检测分割列（空列 > 表头结构）—— 框线已在上文检测过
        if split_col_index is None:
            split_col_index = _detect_split_col(data)

        if split_col_index is None:
            # 没有检测到分区，按多行表头处理
            result = _extract_region(data, header_start, header_end, column_names, data_start, row_has_hborder,
                                     file_path, sheet_name, trimmed_count, llm_client)
            return {
                "subtables": [{"columns": result["columns"], "rows": result["rows"], "label": "whole"}],
                "original_row_count": result["original_row_count"],
            }

        # 拆分为多个分区（按空列分隔）
        subtables = []
        regions = _split_regions(data, split_col_index)

        for idx, (start_col, end_col, label) in enumerate(regions):
            region_data = [row[start_col:end_col] for row in data]
            region_data = _trim_left_empty_cols(region_data)
            region_data = _trim_right_empty_cols(region_data)
            region_cols = column_names[start_col:end_col] if column_names else None
            # 每个区域独立检测表头范围
            result = _extract_region(region_data, None, None, region_cols, None, None,
                                     file_path, sheet_name, trimmed_count, llm_client)
            if result["rows"]:
                subtables.append({
                    "columns": result["columns"],
                    "rows": result["rows"],
                    "label": label or f"p{idx + 1}",
                })

    total_rows = sum(len(st["rows"]) for st in subtables)
    return {
        "subtables": subtables,
        "original_row_count": total_rows,
    }


def _extract_from_llm_regions(data: list, regions: list, column_names: list = None,
                               row_has_hborder: list = None,
                               file_path: str = '', sheet_name: str = '',
                               trim_start: int = 0, llm_client=None,
                               *, global_header_start: int | None = None,
                               global_header_end: int | None = None,
                               global_data_start: int | None = None) -> list:
    """从 LLM 提供的区域信息中提取子表。

    region 内未给 header_start/header_end/data_start 时，用全局后备值（来自
    params 顶层），这样横向分区可以共享一份"全局表头位置"，又能让某个 region
    单独覆盖自己的表头。
    """
    subtables = []
    n_cols = max(len(r) for r in data) if data else 0

    for idx, region in enumerate(regions):
        col_start = region.get("col_start", 0)
        col_end = region.get("col_end", n_cols)
        label = region.get("label", f"p{idx + 1}")
        r_header_start = region.get("header_start", global_header_start)
        r_header_end = region.get("header_end", global_header_end)
        r_data_start = region.get("data_start", global_data_start)

        # 如果提供了 data_start，推导 header_end
        if r_data_start is not None and r_data_start > 0 and r_header_end is None:
            r_header_end = r_data_start - 1

        # 边界保护
        col_start = max(0, min(col_start, n_cols))
        # LLM 可能把 col_end 理解为 inclusive（含该列），做 +1 调整
        # 但不要扩展到下一个 region 的 col_start，避免包含空列间隔
        col_end = min(col_end + 1, n_cols)
        col_end = max(col_start + 1, min(col_end, n_cols))

        # 按列范围提取区域数据
        region_data = [row[col_start:col_end] for row in data]
        region_data = _trim_left_empty_cols(region_data)
        region_data = _trim_right_empty_cols(region_data)
        region_cols = column_names[col_start:col_end] if column_names else None

        # 用区域自身的表头信息提取
        # row_has_hborder 是全局行的框线信息，对子区域同样适用
        result = _extract_region(region_data, r_header_start, r_header_end, region_cols, r_data_start, row_has_hborder,
                                 file_path, sheet_name, trim_start, llm_client)
        
        # P5: post-validation — 验证子表质量
        if result["rows"] and _validate_subtable(result):
            subtables.append({
                "columns": result["columns"],
                "rows": result["rows"],
                "label": label,
            })

    return subtables


def _validate_subtable(result: dict) -> bool:
    """
    P5: 验证子表质量。
    - 列名不能全是 col_N（说明表头提取失败）
    - 行数不能只有 1-2 行（说明数据提取失败）
    """
    columns = result.get("columns", [])
    rows = result.get("rows", [])
    
    # 行数过少
    if len(rows) <= 1:
        return False
    
    # 检查列名质量：如果有意义的列名（非col_N）占比过低
    import re
    meaningful = sum(1 for c in columns if not re.match(r'^col_\d+$', c))
    if meaningful < len(columns) * 0.3 and len(columns) > 2:
        return False
    
    return True


def _extract_region(data: list, header_start: int, header_end: int,
                    column_names: list = None, data_start: int = None,
                    row_has_hborder: list = None,
                    file_path: str = '', sheet_name: str = '',
                    trim_start: int = 0, llm_client=None) -> dict:
    """从区域数据中提取表头和数据行（支持多行表头 + data_start + 框线信息 + LLM融合）"""
    from strategies.strategy_multi_header import (
        _merge_multi_row_header, _detect_header_range, _merge_indicator_column,
        _ffill_indicator_column, _clean_cell, _has_numeric_columns,
        _adjust_header_end_if_data_row, merge_multi_row_header_with_llm,
    )
    from services.mysql_writer import make_unique_columns
    from services.excel_utils import detect_header_end_by_border_util, rename_id_col

    if not data:
        return {"columns": [], "rows": [], "original_row_count": 0}

    # 优先用 data_start 推导 header_end
    if data_start is not None and data_start > 0:
        if header_end is None or header_end >= data_start:
            header_end = data_start - 1

    # 自动检测表头范围：框线优先 > 内容分析
    if header_start is None or header_end is None:
        # 尝试用框线信息检测 header_end
        header_end_border = None
        if row_has_hborder:
            # 改进的框线检测：不仅看第一个分隔线，而是找表头/数据分界的最佳候选
            # 策略：找所有有底部水平分隔线的行，选择其后紧跟最多非分隔线行的那个
            # 这比只看前10行更健壮
            border_rows = []
            for i in range(min(15, len(row_has_hborder))):
                if row_has_hborder[i]:
                    border_rows.append(i)

            if border_rows:
                # 在所有分隔线行中，找最佳 header_end 候选
                # 最佳候选 = 该行有分隔线 + 后续最多连续非分隔线行
                best_candidate = None
                best_gap = 0
                for br in border_rows:
                    # 计算该行之后连续的非分隔线行数
                    gap = 0
                    for j in range(br + 1, min(br + 10, len(row_has_hborder))):
                        if not row_has_hborder[j]:
                            gap += 1
                        else:
                            break
                    if gap > best_gap:
                        best_gap = gap
                        best_candidate = br

                if best_candidate is not None and best_gap >= 2:
                    header_end_border = best_candidate

        if header_end_border is not None:
            header_end = header_end_border
            header_end = _adjust_header_end_if_data_row(data, header_end)
            # 从 header_end 向上找 header_start（结合框线信号）
            header_start = _find_header_start_from_end(data, header_end, row_has_hborder)
        else:
            header_start, header_end = _detect_header_range(data)

    # 合并多行表头
    columns = _merge_multi_row_header(data, header_start, header_end)
    columns = make_unique_columns(columns)
    columns = [rename_id_col(c) for c in columns]

    # 列名校验：如果合并后含纯数字则回退一行
    header_range_uncertain = False
    if _has_numeric_columns(columns, header_end, data):
        if header_end > header_start:
            header_end -= 1
            columns = _merge_multi_row_header(data, header_start, header_end)
            columns = make_unique_columns(columns)
            columns = [rename_id_col(c) for c in columns]
        header_range_uncertain = True

    # LLM 辅助表头范围校验
    if header_range_uncertain and header_end > header_start:
        from strategies.strategy_multi_header import _verify_header_range_via_llm
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

    if column_names and len(column_names) == len(columns):
        columns = column_names

    # 合并层级指标列
    actual_data_start = header_end + 1
    # 用自动检测校验 data_start，避免 LLM 偏大导致首行数据丢失
    from strategies.strategy_multi_header import _find_data_start_row
    auto_data_start = _find_data_start_row(data)
    if auto_data_start >= 0 and auto_data_start < actual_data_start:
        actual_data_start = auto_data_start

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

    # 过滤全空列（横向分区间隔空列）：列名为 col_N/col_empty 且该列数据全为空
    if rows and len(rows) > 0:
        import re
        empty_col_indices = []
        for ci, col in enumerate(columns):
            if not re.match(r'^col_\d+$', col) and col != 'col_empty':
                continue
            # 检查该列在所有数据行中是否全为空
            all_empty = all(
                rows[ri][ci] is None or str(rows[ri][ci]).strip() == ''
                for ri in range(len(rows)) if ci < len(rows[ri])
            )
            if all_empty:
                empty_col_indices.append(ci)
        if empty_col_indices:
            columns = [c for ci, c in enumerate(columns) if ci not in empty_col_indices]
            rows = [[v for ci, v in enumerate(row) if ci not in empty_col_indices] for row in rows]

    return {
        "columns": columns,
        "rows": rows,
        "original_row_count": len(rows),
    }


def _find_header_start_from_end(data: list, header_end: int,
                                 row_has_hborder: list = None) -> int:
    """已知 header_end，向上找 header_start（内容分析）。

    从 header_end 向上逐行检查，遇到空行/标题行跳过，
    遇到单值长文本（>4字符非短中文）视为非表头行则停止，
    其余行纳入表头范围。
    """
    from services.excel_utils import is_title_row
    import re
    header_start = header_end

    for i in range(header_end - 1, -1, -1):
        if is_empty_row(data[i]):
            continue
        if is_title_row(data[i]):
            continue
        non_empty = [v for v in data[i] if v is not None and str(v).strip() != ""]
        if len(non_empty) == 1:
            text = str(non_empty[0]).strip()
            text_compressed = re.sub(r'\s+', '', text)
            if len(text_compressed) > 4 and not re.match(r'^[\u4e00-\u9fff]{1,4}$', text_compressed):
                break
        header_start = i
    return header_start




def _trim_left_empty_cols(data: list) -> list:
    """去掉每行左侧连续的全空列（分割列残余）"""
    if not data:
        return data
    # 找到第一个非全空列
    n_cols = max(len(r) for r in data)
    first_non_empty = 0
    for c in range(n_cols):
        all_empty = True
        for row in data:
            if c < len(row) and row[c] is not None and str(row[c]).strip() != "":
                all_empty = False
                break
        if not all_empty:
            first_non_empty = c
            break
    if first_non_empty == 0:
        return data
    return [row[first_non_empty:] for row in data]


def _trim_right_empty_cols(data: list) -> list:
    """去掉每行右侧连续的全空列"""
    if not data:
        return data
    n_cols = max(len(r) for r in data)
    last_non_empty = n_cols - 1
    for c in range(n_cols - 1, -1, -1):
        all_empty = True
        for row in data:
            if c < len(row) and row[c] is not None and str(row[c]).strip() != "":
                all_empty = False
                break
        if not all_empty:
            last_non_empty = c
            break
    if last_non_empty == n_cols - 1:
        return data
    return [row[:last_non_empty + 1] for row in data]




def _detect_split_col(data: list) -> int | None:
    """检测横向分区，返回第一个分割列索引。
    
    优先级：框线信号（点划线/右侧框线比率突降） > 空列检测 > 表头重复结构
    """
    if not data:
        return None
    n_cols = max(len(r) for r in data)
    if n_cols < 4:
        return None

    # 方法0: 框线信号检测已移至 run() 中优先调用 _detect_split_cols_by_border

    # 方法1: 基于空列检测
    col_empty_count = [0] * n_cols
    total_rows = len(data)
    for row in data:
        for i in range(min(len(row), n_cols)):
            if row[i] is None or str(row[i]).strip() == "":
                col_empty_count[i] += 1

    empty_streak = 0
    for i in range(n_cols):
        if col_empty_count[i] / max(total_rows, 1) > 0.8:
            empty_streak += 1
        else:
            if empty_streak >= 2:
                return i - empty_streak
            empty_streak = 0

    # 方法2: 基于表头行重复结构检测
    split_by_header = _detect_split_by_header_structure(data, n_cols)
    if split_by_header is not None:
        return split_by_header

    return None


def _detect_split_by_header_structure(data: list, n_cols: int) -> int | None:
    """
    P5: 基于表头行重复结构检测横向分区。
    
    如果表头行中出现重复的列名序列（如"名次/地区/产量"出现两次以上），
    则认为是多个并排的独立表格。
    """
    if not data or n_cols < 6:
        return None
    
    # 检查前5行（通常是表头区域）
    header_rows = data[:min(5, len(data))]
    
    # 找出非空值最多的行作为"关键表头行"
    key_row = None
    max_non_empty = 0
    for row in header_rows:
        non_empty = sum(1 for v in row if v is not None and str(v).strip() != "")
        if non_empty > max_non_empty:
            max_non_empty = non_empty
            key_row = row
    
    if key_row is None or max_non_empty < 4:
        return None
    
    # 提取关键表头行的非空值及其列索引
    header_values = []
    for i, v in enumerate(key_row):
        vs = str(v).strip() if v is not None else ""
        if vs:
            header_values.append((i, vs.lower()))
    
    if len(header_values) < 4:
        return None
    
    # 尝试找到重复的列名序列
    # 检查是否存在一个长度L的序列，在表头中出现了2次以上
    for seq_len in range(2, len(header_values) // 2 + 1):
        # 取第一个序列
        first_seq = [vs for _, vs in header_values[:seq_len]]
        
        # 在后续位置查找相同序列
        for start in range(seq_len, len(header_values) - seq_len + 1):
            candidate = [vs for _, vs in header_values[start:start + seq_len]]
            # 计算相似度
            matches = sum(1 for a, b in zip(first_seq, candidate) if a == b)
            similarity = matches / seq_len if seq_len > 0 else 0
            
            if similarity >= 0.7:
                # 找到了重复结构，返回分割列位置
                # 分割点在第一个序列的末尾和第二个序列的开头之间
                split_col = header_values[seq_len - 1][0] + 1
                # 检查分割列到下一个序列开头之间是否有空列
                next_start_col = header_values[start][0]
                if split_col <= next_start_col:
                    return split_col
    
    return None


def _split_regions(data: list, split_col_index: int) -> list[tuple]:
    """按分割列拆分出多个区域 [(start_col, end_col, label), ...]"""
    n_cols = max(len(r) for r in data) if data else 0
    regions = []

    # 查找所有分割列
    col_empty_count = [0] * n_cols
    total_rows = len(data)
    for row in data:
        for i in range(min(len(row), n_cols)):
            if row[i] is None or str(row[i]).strip() == "":
                col_empty_count[i] += 1

    split_cols = []
    empty_streak = 0
    for i in range(n_cols):
        if col_empty_count[i] / max(total_rows, 1) > 0.8:
            empty_streak += 1
        else:
            if empty_streak >= 2:
                split_cols.append(i - empty_streak)
            empty_streak = 0

    # 构建区域
    boundaries = [0] + split_cols + [n_cols]
    for i in range(len(boundaries) - 1):
        start = boundaries[i]
        end = boundaries[i + 1]
        label = f"p{i + 1}"
        regions.append((start, end, label))

    return regions


# ──────────────────────── 框线辅助检测 ────────────────────────

def _detect_split_cols_by_border(file_path: str, sheet_name, data: list) -> list[int]:
    """用框线信息检测横向分割列（点划线或框线比率突降）。"""
    from services.border_info import read_border_info, detect_horizontal_split_cols
    border_info = read_border_info(file_path, sheet_name)
    if not border_info:
        return []
    cols_info = border_info["cols"]
    rows_info = border_info["rows"]
    n_data_cols = max(len(r) for r in data) if data else 0
    if len(cols_info) > n_data_cols:
        cols_info = cols_info[:n_data_cols]
    return detect_horizontal_split_cols(cols_info, rows_info)


def _build_regions_from_split_cols(data: list, split_cols: list[int]) -> list[tuple]:
    """从分割列列表构建区域 [(start_col, end_col, label), ...]"""
    n_cols = max(len(r) for r in data) if data else 0
    if not split_cols:
        return [(0, n_cols, "p1")]
    boundaries = [0] + split_cols + [n_cols]
    return [(boundaries[i], boundaries[i + 1], f"p{i + 1}") for i in range(len(boundaries) - 1)]

    