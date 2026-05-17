"""调试1985年零售表的子表检测"""
import sys
sys.path.insert(0, r'c:\Users\31039\ExecelDataDeal\data_to_db')

from services.excel_reader import read_sheet
from services.excel_utils import is_empty_row
from strategies.strategy_vertical_subtable import _detect_vertical_subtables, _find_splits_by_content, _find_header_end_by_content
from services.border_info import read_border_info, detect_vertical_splits_by_border
from services.table_layout import detect_header_range

path = r'c:\Users\31039\ExecelDataDeal\data\纵向多子表\1985年各地区社会商品零售总额(一~二).xlsx'
sheet = '1-2'
data, _ = read_sheet(path, sheet, read_border=True)
while data and is_empty_row(data[0]):
    data.pop(0)
while data and is_empty_row(data[-1]):
    data.pop()

print(f"总行数: {len(data)}")
for i, row in enumerate(data):
    non_empty = [v for v in row if v is not None and str(v).strip() != ""]
    print(f"  Row {i}: {non_empty}")

header_start, header_end = detect_header_range(data)
print(f"\ndetect_header_range: {header_start}-{header_end}")
he_content = _find_header_end_by_content(data)
print(f"_find_header_end_by_content: {he_content}")

# 内容分割点
splits = _find_splits_by_content(data, he_content if he_content is not None else header_end)
print(f"内容分割点: {splits}")

# 框线分割点
border_info = read_border_info(path, sheet)
rows_info = border_info.get('rows', []) if border_info else []
border_splits = detect_vertical_splits_by_border(rows_info, header_end) if rows_info else []
print(f"框线分割点: {border_splits}")

# 纵向子表
regions = _detect_vertical_subtables(data, rows_info if rows_info else None)
print(f"\n纵向子表: {len(regions)} 个")
for r in regions:
    print(f"  {r}")
