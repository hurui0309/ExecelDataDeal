"""完整调试交换比价表的策略解析流程"""
import sys
sys.path.insert(0, r"c:\Users\31039\ExecelDataDeal\data_to_db")

from services.excel_reader import read_sheet
from services.excel_utils import is_empty_row, is_header_like_row
from strategies.strategy_horizontal_split import _detect_split_col, _detect_split_cols_by_border, _extract_region
from services.border_info import read_border_info, detect_header_end_by_border
from strategies.strategy_multi_header import _detect_header_range, _merge_multi_row_header, _adjust_header_end_if_data_row, _find_data_start_row
from services.mysql_writer import make_unique_columns
from services.excel_utils import rename_id_col

path = r"c:\Users\31039\ExecelDataDeal\data\纵向多子表\主要农产品与工业品的交换比价(一~二).xlsx"
sheet = "1-2"

data, row_has_hborder = read_sheet(path, sheet, read_border=True)

# 去首尾空行
while data and is_empty_row(data[0]):
    data.pop(0)
while data and is_empty_row(data[-1]):
    data.pop()

print(f"=== 数据 {len(data)} 行 ===")

# 检测横向分区
split_col = _detect_split_col(data)
print(f"\n=== 横向分区检测 split_col={split_col} ===")

# 框线检测分区
split_by_border = _detect_split_cols_by_border(path, sheet, data)
print(f"=== 框线分区 split_cols={split_by_border} ===")

# 检测 header 范围
header_start, header_end = _detect_header_range(data)
print(f"\n=== 内容检测 header_start={header_start}, header_end={header_end} ===")

# 框线检测 header_end
border_info = read_border_info(path, sheet)
if border_info:
    ri = border_info["rows"]
    if len(ri) > len(data):
        ri = ri[:len(data)]
    raw_he = detect_header_end_by_border(ri)
    print(f"=== 框线检测 header_end={raw_he} ===")
    if raw_he is not None:
        adj_he = _adjust_header_end_if_data_row(data, raw_he)
        print(f"=== 调整后 header_end={adj_he} ===")
    
    # 打印前15行的框线
    print("\n=== 框线信息（前15行） ===")
    for i, r in enumerate(ri[:15]):
        print(f"  Row {i}: bottom_solid={r.get('bottom_solid')}, bottom_ratio={r.get('bottom_ratio', 0):.0%}, bottom_style={r.get('bottom_style')}")

# 检查 row_has_hborder
print(f"\n=== row_has_hborder (前15行) ===")
for i, hb in enumerate((row_has_hborder or [])[:15]):
    print(f"  Row {i}: hborder={hb}")

# _find_data_start_row
data_start = _find_data_start_row(data)
print(f"\n=== _find_data_start_row = {data_start} ===")

# is_header_like_row 检查
print(f"\n=== is_header_like_row 检查 ===")
for i in range(8):
    result = is_header_like_row(data[i])
    non_empty = [v for v in data[i] if v is not None and str(v).strip() != ""]
    print(f"  Row {i}: is_header_like={result}, non_empty={non_empty}")
