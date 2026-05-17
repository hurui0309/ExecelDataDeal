"""调试人口表的分割点检测"""
import sys
sys.path.insert(0, r"c:\Users\31039\ExecelDataDeal\data_to_db")

from services.excel_reader import read_sheet
from services.excel_utils import is_empty_row, is_header_like_row
from strategies.strategy_vertical_subtable import _find_splits_by_content, _find_header_end_by_content

path = r"c:\Users\31039\ExecelDataDeal\data\纵向多子表\人口和自然资源(一~二).xlsx"
sheet = "1-2"

data, _ = read_sheet(path, sheet, read_border=False)

# 去首尾空行
while data and is_empty_row(data[0]):
    data.pop(0)
while data and is_empty_row(data[-1]):
    data.pop()

print(f"=== 数据 {len(data)} 行 ===")

# 找 header_end
header_end = _find_header_end_by_content(data)
print(f"\n=== header_end = {header_end} ===")
print(f"  Row {header_end}: {list(data[header_end])}")

# 找分割点
splits = _find_splits_by_content(data, header_end)
print(f"\n=== 分割点: {splits} ===")
for s in splits:
    print(f"  split at row {s}: {list(data[s])}")
    # 打印前后几行
    for r in range(max(0, s-1), min(len(data), s+5)):
        ne = [v for v in data[r] if v is not None and str(v).strip() != ""]
        print(f"    Row {r}: is_empty={is_empty_row(data[r])}, is_header_like={is_header_like_row(data[r]) if not is_empty_row(data[r]) else 'N/A'}, non_empty={ne[:3]}")
