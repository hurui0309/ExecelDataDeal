"""Service: table_layout — 表格结构通用工具

把原本散落在 strategy_multi_header 内部、被多个策略偷偷 import 的"私有"工具函数
统一迁到这里，作为正式的公共 API。各 strategy 模块通过

    from services.table_layout import clean_cell, ...

调用即可，不再彼此 import 私有函数。

迁移自 strategy_multi_header._xxx，函数名去掉前缀下划线。
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Iterable

from services.excel_utils import is_empty_row, is_title_row


# ──────────────────────── 单元格 / 行 清洗 ────────────────────────


def clean_cell(v):
    """清洗单元格值：None/NaN/空字符串 → None；千位分隔符（逗号/空格）合并为纯数字。"""
    if v is None:
        return None
    s = str(v).strip()
    if s.lower() == "nan" or s == "":
        return None
    # 千位分隔符（逗号）合并：6,095 → 6095, 1,180.1 → 1180.1
    if "," in s:
        merged = re.sub(r"(\d),(\d)", r"\1\2", s)
        if re.match(r"^-?\d+(\.\d+)?$", merged):
            s = merged
    # 千位分隔符（空格）合并：1 974 → 1974
    if " " in s:
        merged = re.sub(r"(\d{1,3})\s+(?=\d{3}(\b|$|\.))", r"\1", s)
        if merged == s:
            merged_loose = re.sub(r"(\d{1,3})\s+(?=\d)", r"\1", s)
            if (re.match(r"^-?\d+(\.\d+)?$", merged_loose)
                    and len(merged_loose.replace("-", "").replace(".", "")) >= 4):
                merged = merged_loose
        if re.match(r"^-?\d+(\.\d+)?$", merged):
            s = merged
        else:
            # 修复"34 5" → "34.5", "0 47" → "0.47"
            decimal_fix = re.sub(r"^(\d+)\s+(\d{1,2})$", r"\1.\2", s)
            if decimal_fix != s and re.match(r"^-?\d+\.\d+$", decimal_fix):
                s = decimal_fix
    return s


def normalize_cell_text(cell_value) -> str | None:
    """提取并清洗单元格文本：strip + 中间连续空格压缩为 1 个。空 → None。"""
    if cell_value is None:
        return None
    text = str(cell_value)
    stripped = text.strip()
    if not stripped:
        return None
    return re.sub(r" {2,}", " ", stripped)


def count_leading_spaces(cell_value) -> int:
    """统计单元格内容的前导空格数（用于多行表头层级判断）。"""
    if cell_value is None:
        return 0
    text = str(cell_value)
    return len(text) - len(text.lstrip(" "))


def clean_header_spaces(text: str) -> str:
    """清洗表头文本中的数字间空格：'1 9 8 6年' → '1986年'。"""
    return re.sub(r"(\d)\s+(?=\d)", r"\1", text)


def trim_trailing_empty_cols(data: list) -> list:
    """裁剪每行的尾部空列，只保留到所有行中最后一列有非空值的列。"""
    if not data:
        return data
    max_non_empty = 0
    for row in data:
        for i in range(len(row) - 1, -1, -1):
            if row[i] is not None and str(row[i]).strip() != "":
                if i + 1 > max_non_empty:
                    max_non_empty = i + 1
                break
    return [row[:max_non_empty] for row in data]


# ──────────────────────── 脚注 / 标题 / 分类行 ────────────────────────

# 脚注/资料来源 行的开头模式（顺序：先匹配中文常见，再英文/特殊符号）
# 字符类用 unicode 转义明确标注中英文冒号、中英文括号，避免源文件编码混淆
_COLON = r"[:\uff1a]"     # 半角 : / 全角 :
_LPAREN = r"[(\uff08]"    # 半角 ( / 全角 (
_RPAREN = r"[)\uff09]"    # 半角 ) / 全角 )
_FOOTNOTE_PATTERNS: tuple[re.Pattern, ...] = tuple(re.compile(p, re.IGNORECASE) for p in (
    rf"^\u6ce8{_COLON}",                                      # 注:
    rf"^\u5907\u6ce8{_COLON}?",                              # 备注:
    rf"^\u8bf4\u660e{_COLON}?",                              # 说明:
    rf"^\u9644\u6ce8{_COLON}?",                              # 附注:
    rf"^\u6570\u636e\u6765\u6e90{_COLON}?",                # 数据来源:
    rf"^\u8d44\u6599\u6765\u6e90{_COLON}?",                # 资料来源:
    rf"^\u6765\u6e90{_COLON}",                                # 来源:
    rf"^[Nn]ote[s]?{_COLON}",
    rf"^Source{_COLON}",
    r"^\u672c\u8868",                                          # 本表
    r"^[\*\u203b\u2605\u25cf\u25b2]",                     # * ※ ★ ● ▲
    r"^[\u2460-\u2473\u2776-\u277f]",                       # ①-⑳ + ❶-❿
    rf"^{_LPAREN}\s*[\d\u4e00-\u4e5d\u5341]+\s*{_RPAREN}\s*[^\u6570\u767e\u5343\u4e07\u4ebf]",
))


def is_footnote_row(row) -> bool:
    """判断行是否为脚注/数据来源行。基于第一非空单元格的开头模式。"""
    first = None
    for v in row:
        if v is not None and str(v).strip():
            first = str(v).strip()
            break
    if first is None:
        return False
    return any(p.match(first) for p in _FOOTNOTE_PATTERNS)


def truncate_footnotes(data: list) -> list:
    """从尾部反向找到第一个非脚注行，裁剪掉它后的所有脚注行（含其间空行）。

    与原实现相比修正了一个 bug：原版返回 data[:footnote_start]，但 footnote_start 之前
    的连续空行没有剥离，会让最终数据末尾多带几行 [None, None, ...]。这里再从
    footnote_start 往前回退，跳过紧邻的空行。
    """
    footnote_start = None
    for i in range(len(data) - 1, -1, -1):
        if is_empty_row(data[i]):
            continue
        if is_footnote_row(data[i]):
            footnote_start = i
        else:
            break
    if footnote_start is None:
        return data
    # 同时剥离 footnote_start 之前紧邻的空行
    cut = footnote_start
    while cut > 0 and is_empty_row(data[cut - 1]):
        cut -= 1
    return data[:cut]


def is_category_title_text(text: str) -> bool:
    """判断文本是否匹配分类标题行的常见模式（中文序号 / 括号序号 / 数字序号）。"""
    if re.match(r"^[一二三四五六七八九十]+、", text):
        return True
    if re.match(r"^[（(][一二三四五六七八九十]+[）)]", text):
        return True
    if re.match(r"^\d+[.．、]", text) and len(text) > 3:
        return True
    return False


def is_header_supplement_text(text: str) -> bool:
    """判断单列文本行是否为表头补充行（应纳入表头范围）。

    典型场景：多行表头中某行只有一列有值，是对表头的补充说明，
    如 '1988年为'（表示该列 1988 年值为 1987 年的百分比）、'1987年％' 等。
    """
    if re.search(r"\d{4}年", text) and re.search(r"[为比％%]", text):
        return True
    if re.search(r"\d{4}年", text) and re.search(r"[％%]$", text):
        return True
    return False


# ──────────────────────── 数据行 / 表头边界 ────────────────────────


def find_next_non_empty_row(data: list, start_idx: int) -> int | None:
    """找到 start_idx 之后第一个非空行的索引，找不到返回 None。"""
    for i in range(start_idx, len(data)):
        if not is_empty_row(data[i]):
            return i
    return None


def row_has_numeric_data(row) -> bool:
    """行中（除第一列外）是否含有数字数据。"""
    for v in row[1:]:
        if v is None:
            continue
        vs = str(v).strip().replace(",", "").replace(" ", "").replace("\u3000", "")
        if vs and re.match(r"^-?\d+\.?\d*$", vs):
            return True
    return False


@lru_cache(maxsize=1)
def _get_unit_patterns() -> tuple[str, ...]:
    """缓存 config.yaml 的 unit_patterns（避免每行加载 yaml）。"""
    try:
        from config_loader import load_config
        cfg = load_config()
        return tuple(cfg.get("parse", {}).get("unit_patterns", []) or [])
    except Exception:
        return ()


def _is_data_like_row(row) -> bool:
    """是否像数据行：≥2 列非空，非分类/单位行，且第二列起含 ≥1 个数字。"""
    non_empty = [v for v in row if v is not None and str(v).strip() != ""]
    if len(non_empty) < 2:
        return False
    first_val = str(non_empty[0]).strip()

    # 分类行（一、二、）若其余列没数字 → 非数据行
    if re.match(r"^[一二三四五六七八九十]+、", first_val):
        rest = non_empty[1:]
        if not any(re.match(r"^-?[\d,]+\.?\d*$", str(v).strip()) for v in rest):
            return False

    # 排除单位行（大量值匹配 "万人/个/%"）
    unit_patterns = _get_unit_patterns()
    if unit_patterns:
        unit_count = 0
        for v in non_empty[1:]:
            vs = str(v).strip()
            for pat in unit_patterns:
                if re.match(pat, vs):
                    unit_count += 1
                    break
        if unit_count >= len(non_empty) * 0.5 and unit_count >= 2:
            return False

    # 至少 1 个数字列（除第一列），且不是纯年份
    numeric_count = 0
    for v in non_empty[1:]:
        vs = str(v).strip()
        vs_clean = vs.replace(",", "").replace(" ", "").replace("\u3000", "")
        if re.match(r"^-?\d+\.?\d*$", vs_clean):
            has_thousand_sep = ("," in vs or "\u3000" in vs
                                or (len(vs) > 4 and " " in vs))
            if not re.match(r"^\d{4}年?$", vs_clean) or has_thousand_sep:
                numeric_count += 1
    return numeric_count >= 1


def find_data_start_row(data: list) -> int:
    """找到第一个数据行索引；若全表无数据行返回 -1。

    寻找规则：连续 N(≥2) 行有数据行特征的起始行；
    若极端只有 1 行数据，但前面是分类行，也接受。
    """
    n_rows = len(data)
    if n_rows == 0:
        return -1
    for i in range(n_rows):
        if _is_data_like_row(data[i]):
            consecutive = sum(
                1 for j in range(i, min(i + 6, n_rows)) if _is_data_like_row(data[j])
            )
            if consecutive >= 1:
                return i

    # 兜底：旧逻辑 — 命中分类行前缀或第二列含数字
    for i, row in enumerate(data):
        non_empty = [v for v in row if v is not None and str(v).strip() != ""]
        if not non_empty:
            continue
        first_val = str(non_empty[0]).strip()
        if re.match(r"^[一二三四五六七八九十]+、", first_val):
            return i
        if len(non_empty) >= 2:
            for v in non_empty[1:]:
                vs = str(v).strip()
                if re.match(r"^-?[\d,]+\.\d+$", vs) or re.match(r"^-?[\d,]+$", vs):
                    if not re.match(r"^\d{4}年?$", vs.replace(",", "")):
                        return i
    return -1


def detect_header_range(data: list) -> tuple[int, int]:
    """检测表头范围 (header_start, header_end)，二者均为 0-based 闭区间。"""
    data_start = find_data_start_row(data)
    if data_start < 0:
        return 0, 0

    header_end = data_start - 1
    while header_end >= 0 and is_empty_row(data[header_end]):
        header_end -= 1
    if header_end < 0:
        return 0, 0

    # 跳过尾部分类标记 / 标题行（保留紧邻多列表头行的 1-4 字补充行）
    while header_end >= 0:
        non_empty = [v for v in data[header_end] if v is not None and str(v).strip() != ""]
        if len(non_empty) == 1:
            text = str(non_empty[0]).strip()
            if re.match(r"^[\u4e00-\u9fff]{1,4}$", text):
                above_idx = header_end - 1
                while above_idx >= 0 and is_empty_row(data[above_idx]):
                    above_idx -= 1
                if above_idx >= 0:
                    above_non_empty = [v for v in data[above_idx]
                                        if v is not None and str(v).strip() != ""]
                    if len(above_non_empty) > 1:
                        break
                header_end -= 1
                while header_end >= 0 and is_empty_row(data[header_end]):
                    header_end -= 1
                continue
            if re.match(r"^[\u4e00-\u9fff]", text) and len(text) > 4:
                above_idx = header_end - 1
                while above_idx >= 0 and is_empty_row(data[above_idx]):
                    above_idx -= 1
                if above_idx >= 0:
                    above_non_empty = [v for v in data[above_idx]
                                        if v is not None and str(v).strip() != ""]
                    if len(above_non_empty) > 1:
                        break
                below_idx = header_end + 1
                if below_idx < len(data):
                    below_row = data[below_idx]
                    below_non_empty = [v for v in below_row
                                        if v is not None and str(v).strip() != ""]
                    if len(below_non_empty) >= 2:
                        for v in below_non_empty[1:]:
                            vs = str(v).strip()
                            if re.match(r"^-?[\d,]+\.\d+$", vs) or re.match(r"^-?[\d,]+$", vs):
                                break
                header_end -= 1
                while header_end >= 0 and is_empty_row(data[header_end]):
                    header_end -= 1
                continue
        if is_title_row(data[header_end]):
            header_end -= 1
            while header_end >= 0 and is_empty_row(data[header_end]):
                header_end -= 1
            continue
        break

    if header_end < 0:
        return 0, 0

    header_start = header_end
    for i in range(header_end - 1, -1, -1):
        if is_empty_row(data[i]):
            continue
        if is_title_row(data[i]):
            continue
        non_empty_i = [v for v in data[i] if v is not None and str(v).strip() != ""]
        if len(non_empty_i) == 1:
            text = str(non_empty_i[0]).strip()
            if re.match(r"^[\u4e00-\u9fff]{1,4}$", text):
                below_idx = i + 1
                while below_idx <= header_end and is_empty_row(data[below_idx]):
                    below_idx += 1
                if below_idx <= header_end:
                    below_non_empty = [v for v in data[below_idx]
                                        if v is not None and str(v).strip() != ""]
                    if len(below_non_empty) > 1:
                        header_start = i
                        continue
                continue
            if len(text) > 4:
                below_idx = i + 1
                while below_idx <= header_end and is_empty_row(data[below_idx]):
                    below_idx += 1
                if below_idx <= header_end:
                    below_non_empty = [v for v in data[below_idx]
                                        if v is not None and str(v).strip() != ""]
                    if len(below_non_empty) > 1:
                        header_start = i
                        continue
                break
        header_start = i
    return header_start, header_end


def adjust_header_end_if_data_row(data: list, header_end: int) -> int:
    """框线返回的 header_end 若实际指向数据行（非首列含大量数字），回退一行。"""
    if header_end < 0 or header_end >= len(data):
        return header_end
    row = data[header_end]
    non_empty = [v for v in row if v is not None and str(v).strip() != ""]
    if len(non_empty) < 2:
        return header_end
    numeric_count = 0
    for v in non_empty[1:]:
        vs = str(v).strip().replace(",", "").replace(" ", "").replace("\u3000", "")
        if re.match(r"^-?\d+\.?\d*$", vs) and not re.match(r"^\d{4}年?$", vs):
            numeric_count += 1
    if numeric_count >= max(2, len(non_empty) * 0.3):
        return header_end - 1
    return header_end


def find_header_start_from_end(data: list, header_end: int) -> int:
    """已知 header_end，向上找 header_start。"""
    header_start = header_end
    for i in range(header_end - 1, -1, -1):
        if is_empty_row(data[i]):
            continue
        if is_title_row(data[i]):
            continue
        non_empty = [v for v in data[i] if v is not None and str(v).strip() != ""]
        if len(non_empty) == 1:
            text = str(non_empty[0]).strip()
            text_compressed = re.sub(r"\s+", "", text)
            if (len(text_compressed) > 4
                    and not re.match(r"^[\u4e00-\u9fff]{1,4}$", text_compressed)):
                if not is_header_supplement_text(text):
                    break
        header_start = i
    return header_start


def has_numeric_columns(columns: list, header_end: int, data: list) -> bool:
    """合并出来的列名是否大量为纯数字（说明把数据行误作表头行）。"""
    if not columns:
        return False
    numeric_count = 0
    for col in columns:
        col_clean = re.sub(r"^col_\d+$", "", col)
        if not col_clean:
            continue
        if re.match(r"^-?\d+(\.\d+)?$", col) or re.match(r"^\d+_\d+$", col):
            numeric_count += 1
    return numeric_count > len(columns) * 0.3


# ──────────────────────── 第一列 ffill（基于框线分组） ────────────────────────


def build_hborder_groups(row_has_hborder: list, data_start: int, data_len: int) -> list[list[int]]:
    """基于水平分隔线把数据区行分组：第 i 行底边有线 → 第 i 行是当前组最后一行。"""
    groups: list[list[int]] = []
    current: list[int] = []
    for i in range(data_start, data_len):
        current.append(i)
        if i < len(row_has_hborder) and row_has_hborder[i]:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def fill_group(data: list, group: list[int]):
    """对一个框线分组内的第一列做填充（同组第一列只有 1 种非空值时才填充）。"""
    if len(group) <= 1:
        return
    non_empty_values: list[str] = []
    for i in group:
        row = data[i]
        if not row or is_empty_row(row):
            continue
        first_val = row[0]
        if first_val is not None and str(first_val).strip() != "":
            val_str = str(first_val).strip()
            if val_str not in non_empty_values:
                non_empty_values.append(val_str)
    if len(non_empty_values) != 1:
        return
    fill_value = non_empty_values[0]
    for i in group:
        row = data[i]
        if not row or is_empty_row(row):
            continue
        first_val = row[0]
        if first_val is None or str(first_val).strip() == "":
            has_other_data = any(v is not None and str(v).strip() != "" for v in row[1:])
            if has_other_data:
                data[i] = [fill_value] + list(row[1:])


def ffill_indicator_column(data: list, data_start: int,
                            row_has_hborder: list | None = None):
    """对数据区第一列做空值前向填充（in-place）。

    如果有水平框线信息，按"分组内统一填充"；否则退化为简单 ffill。
    """
    if data_start >= len(data):
        return
    if row_has_hborder and len(row_has_hborder) >= len(data):
        groups = build_hborder_groups(row_has_hborder, data_start, len(data))
        for g in groups:
            fill_group(data, g)
        return
    # 简单 ffill
    last_value = None
    for i in range(data_start, len(data)):
        row = data[i]
        if not row or is_empty_row(row):
            continue
        first_val = row[0]
        if first_val is not None and str(first_val).strip() != "":
            last_value = first_val
        elif last_value is not None:
            has_other = any(v is not None and str(v).strip() != "" for v in row[1:])
            if has_other:
                data[i] = [last_value] + list(row[1:])


# ──────────────────────── 公共预处理 ────────────────────────


def preprocess_sheet(file_path: str, sheet_name,
                      *, read_border: bool = False,
                      do_truncate_footnotes: bool = True,
                      do_trim_trailing_cols: bool = False) -> dict:
    """一次性公共预处理：load → 去首尾空行 → 截脚注 →（可选）裁尾部空列。

    对应 6 个 strategy_*.run() 入口几乎一致的"前置准备"段落，统一收口在此。

    返回:
        {
            "data": list[list],          # 处理后的二维数据
            "row_has_hborder": list[bool] | [],  # 与 data 等长（read_border=False 时为 []）
            "trim_start": int,           # 首部裁剪行数（用于把外部 row 索引转换到 trim 后）
            "trim_end": int,             # 尾部裁剪行数
            "footnote_trim": int,        # 截掉的脚注行数（含夹在其间的空行）
        }

    注意：trim_start/trim_end 是为了把"基于原始 sheet 行号的外部参数"换算到 trim 后的索引，
    调用方应当用 ``new_idx = old_idx - trim_start`` 调整 LLM/regions 中的行号。
    """
    from services.excel_reader import read_sheet
    data, row_has_hborder = read_sheet(file_path, sheet_name, read_border=read_border)
    if data is None:
        data = []
    if row_has_hborder is None:
        row_has_hborder = []

    # 同步 row_has_hborder 长度到 data
    if read_border and row_has_hborder:
        if len(row_has_hborder) > len(data):
            row_has_hborder = row_has_hborder[:len(data)]
        elif len(row_has_hborder) < len(data):
            row_has_hborder = list(row_has_hborder) + [False] * (len(data) - len(row_has_hborder))
    else:
        row_has_hborder = []

    # 去首部空行
    trim_start = 0
    while data and is_empty_row(data[0]):
        data.pop(0)
        if row_has_hborder:
            row_has_hborder.pop(0)
        trim_start += 1
    # 去尾部空行
    trim_end = 0
    while data and is_empty_row(data[-1]):
        data.pop()
        if row_has_hborder:
            row_has_hborder.pop()
        trim_end += 1

    # 截脚注
    footnote_trim = 0
    if do_truncate_footnotes and data:
        old_len = len(data)
        data = truncate_footnotes(data)
        footnote_trim = old_len - len(data)
        if row_has_hborder:
            row_has_hborder = row_has_hborder[:len(data)]

    # 裁尾部空列（部分策略需要）
    if do_trim_trailing_cols and data:
        data = trim_trailing_empty_cols(data)

    return {
        "data": data,
        "row_has_hborder": row_has_hborder,
        "trim_start": trim_start,
        "trim_end": trim_end,
        "footnote_trim": footnote_trim,
    }


__all__ = [
    # 单元格 / 行
    "clean_cell", "normalize_cell_text", "count_leading_spaces",
    "clean_header_spaces", "trim_trailing_empty_cols",
    # 脚注 / 标题
    "is_footnote_row", "truncate_footnotes",
    "is_category_title_text", "is_header_supplement_text",
    # 数据行 / 表头边界
    "find_next_non_empty_row", "row_has_numeric_data",
    "find_data_start_row", "detect_header_range",
    "adjust_header_end_if_data_row", "find_header_start_from_end",
    "has_numeric_columns",
    # 第一列填充
    "build_hborder_groups", "fill_group", "ffill_indicator_column",
    # 预处理一站式
    "preprocess_sheet",
]
