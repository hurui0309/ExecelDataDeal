"""Excel 解析公共工具函数"""

import re

# ──────────────────────── 边框样式常量 ────────────────────────

DASH_STYLES = frozenset({
    'dashDot', 'dashDotDot', 'dashed', 'dotted', 'slantDashDot',
    'mediumDashed', 'mediumDashDot', 'mediumDashDotDot',
})

SOLID_STYLES = frozenset({'thin', 'medium', 'thick', 'double'})

# xlrd 边框样式索引
BORDER_SOLID_XLRD = frozenset({1, 2, 5, 6})   # thin, medium, thick, double
BORDER_DASH_XLRD = frozenset({3, 4, 8, 9, 10, 11, 12, 13})  # dashed, dotted, etc.


# ──────────────────────── 通用工具函数 ────────────────────────

def is_empty_row(row) -> bool:
    """判断是否为空行（所有值为 None 或空白字符串）"""
    return all(v is None or (isinstance(v, str) and v.strip() == "") for v in row)


def is_xls_file(file_path: str) -> bool:
    """判断文件是否为 .xls 格式（排除 .xlsx）"""
    return file_path.lower().endswith(".xls") and not file_path.lower().endswith(".xlsx")


def is_title_row(row) -> bool:
    """判断是否为标题行：所有非空单元格的值相同（合并单元格展开后的表名行）"""
    non_empty = [str(v).strip() for v in row if v is not None and str(v).strip() != ""]
    if len(non_empty) < 2:
        return False
    return len(set(non_empty)) == 1


def is_header_like_row(row) -> bool:
    """
    判断是否为表头行：多列非空，且包含非数字文本或表头关键词。

    判定规则（优先级从高到低）：
    1. 包含表头关键词（指标、单位、年份等）→ True
    2. 纯数字列占比 > 40% → False（数据行）
    3. 包含年份模式（如 1978年、2000-2020）→ True
    4. 文本列数 >= 2 且多于纯数字列 → True
    5. 其他 → False
    """
    non_empty = [v for v in row if v is not None and str(v).strip() != ""]
    if len(non_empty) < 2:
        return False

    header_keywords = ['指标', '单位', '年份', '项目', '地区', '类别', '合计', '总计', '名称',
                        '年', '月', '日', '序号', '编号', '代码']
    for v in non_empty:
        vs = str(v).strip()
        if any(kw in vs for kw in header_keywords):
            return True

    pure_number_count = sum(
        1 for v in non_empty
        if re.match(r'^-?\d+(\.\d+)?$', str(v).strip().replace(',', '').replace(' ', '').replace('\u3000', ''))
    )
    if pure_number_count > len(non_empty) * 0.4:
        return False

    year_pattern = re.compile(r'19\d{2}年?|20\d{2}年?|\d{4}[-–]\d{4}')
    for v in non_empty:
        if year_pattern.search(str(v).strip()):
            return True

    text_count = len(non_empty) - pure_number_count
    if text_count >= 2 and text_count > pure_number_count:
        return True

    return False


def detect_header_end_by_border_util(file_path: str, sheet_name, data_len: int,
                                      trim_start: int = 0, trim_end: int = 0) -> int | None:
    """用框线信息检测 header_end 行。需传入 trim_start/trim_end 对齐裁剪后的行号。"""
    from services.border_info import read_border_info, detect_header_end_by_border
    border_info = read_border_info(file_path, sheet_name)
    if not border_info:
        return None
    rows_info = border_info["rows"]
    total = len(rows_info)
    rows_info = rows_info[trim_start:total - trim_end] if trim_end > 0 else rows_info[trim_start:]
    if len(rows_info) > data_len:
        rows_info = rows_info[:data_len]
    return detect_header_end_by_border(rows_info)


def rename_id_col(col_name: str) -> str:
    """避免列名 'id' 与 MySQL 自增主键冲突，改名为 'row_id'。"""
    if col_name.lower() == 'id':
        return 'row_id'
    return col_name

    