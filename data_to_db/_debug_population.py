"""调试人口和自然资源表的子表检测"""
import sys
sys.path.insert(0, r"c:\Users\31039\ExecelDataDeal\data_to_db")

from services.excel_reader import read_sheet
from services.excel_utils import is_empty_row, is_header_like_row
from strategies.strategy_vertical_subtable import _detect_vertical_subtables, _find_header_end_by_content, _find_splits_by_content
from services.border_info import read_border_info, detect_header_end_by_border

path = r"c:\Users\31039\ExecelDataDeal\data\纵向多子表\人口和自然资源(一~二).xlsx"
sheet = "1-2"

data, row_has_hborder = read_sheet(path, sheet, read_border=True)
print(f"=== 读取数据 {len(data)} 行 ===")
for i, row in enumerate(data[:15]):
    non_empty = [v for v in row if v is not None and str(v).strip() != ""]
    print(f"  Row {i}: {list(row)[:5]}... non_empty={non_empty[:5]}")

# 去首尾空行
trim_start = 0
while data and is_empty_row(data[0]):
    data.pop(0)
    trim_start += 1
while data and is_empty_row(data[-1]):
    data.pop()

print(f"\n=== 去空行后 {len(data)} 行, trim_start={trim_start} ===")
for i, row in enumerate(data[:15]):
    non_empty = [v for v in row if v is not None and str(v).strip() != ""]
    print(f"  Row {i}: {list(row)[:5]}... non_empty={non_empty[:5]}")

# 检查 row_has_hborder
if row_has_hborder:
    row_has_hborder = row_has_hborder[trim_start:trim_start + len(data)]
    print(f"\n=== row_has_hborder (前15行) ===")
    for i, hb in enumerate(row_has_hborder[:15]):
        print(f"  Row {i}: hborder={hb}")

# 框线检测 header_end
border_info = read_border_info(path, sheet)
if border_info:
    ri = border_info["rows"]
    total_rows = len(ri)
    ri = ri[trim_start:total_rows]
    if len(ri) > len(data):
        ri = ri[:len(data)]
    
    raw_he = detect_header_end_by_border(ri)
    print(f"\n=== 框线检测 header_end: {raw_he} ===")
    
    # 打印框线信息
    for i, r in enumerate(ri[:15]):
        print(f"  Row {i}: bottom_solid={r.get('bottom_solid')}, bottom_ratio={r.get('bottom_ratio', 0):.0%}, bottom_style={r.get('bottom_style')}")

# 内容检测 header_end
header_end_by_content = _find_header_end_by_content(data)
print(f"\n=== 内容检测 header_end: {header_end_by_content} ===")

# 检测纵向子表
if border_info:
    rows_info = ri
else:
    rows_info = None

regions = _detect_vertical_subtables(data, rows_info)
print(f"\n=== 检测到 {len(regions)} 个子表区域 ===")
for r in regions:
    print(f"  {r}")
