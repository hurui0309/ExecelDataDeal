"""调试1981年农产品表的分割点检测"""
import sys
sys.path.insert(0, r"c:\Users\31039\ExecelDataDeal\data_to_db")

from services.excel_reader import read_sheet
from services.excel_utils import is_empty_row, is_header_like_row
from strategies.strategy_vertical_subtable import _find_header_end_by_content, _find_splits_by_content, _detect_vertical_subtables
from services.border_info import read_border_info, detect_vertical_splits_by_border

path = r"c:\Users\31039\ExecelDataDeal\data\纵向多子表\1981年主要农产品产量中各地区产量占的比重(一~三).xlsx"
sheet = "1-3"

data, _ = read_sheet(path, sheet, read_border=True)
while data and is_empty_row(data[0]):
    data.pop(0)
while data and is_empty_row(data[-1]):
    data.pop()

print(f"=== 数据 {len(data)} 行, {len(data[0]) if data else 0} 列 ===")
for i, row in enumerate(data[:8]):
    print(f"  Row {i}: {list(row)[:10]}")

header_end = _find_header_end_by_content(data)
print(f"\nheader_end: {header_end}")

# 这个表走了 strategy_horizontal_split，因为 _detect_split_col 检测到横向分区
from strategies.strategy_horizontal_split import _detect_split_col
split_col = _detect_split_col(data)
print(f"_detect_split_col: {split_col}")

# 内容分割点
splits = _find_splits_by_content(data, header_end)
print(f"内容分割点: {splits}")

# 框线分割点
border_info = read_border_info(path, sheet)
if border_info:
    ri = border_info["rows"][:len(data)]
    border_splits = detect_vertical_splits_by_border(ri, header_end)
    print(f"框线分割点: {border_splits}")

# 完整子表检测
rows_info = ri if border_info else None
regions = _detect_vertical_subtables(data, rows_info)
print(f"子表区域: {len(regions)} 个")
for r in regions:
    print(f"  {r}")
