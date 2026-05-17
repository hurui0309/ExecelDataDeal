"""测试修复后的交换比价表解析"""
import sys
sys.path.insert(0, r'c:\Users\31039\ExecelDataDeal\data_to_db')

from services.excel_reader import read_sheet
from services.excel_utils import is_empty_row
from strategies.strategy_vertical_subtable import _detect_vertical_subtables, _find_splits_by_content, _find_header_end_by_content
from services.table_layout import detect_header_range
from services.border_info import read_border_info
from strategies.strategy_multi_header import _merge_multi_row_header

# 读取交换比价表
path = r'c:\Users\31039\ExecelDataDeal\data\纵向多子表\主要农产品与工业品的交换比价(一~二).xlsx'
sheet = '1-2'
data, row_has_hborder = read_sheet(path, sheet, read_border=True)

# 去首尾空行
while data and is_empty_row(data[0]):
    data.pop(0)
while data and is_empty_row(data[-1]):
    data.pop()

print(f"总行数: {len(data)}")

# 检测 header_end
header_start, header_end = detect_header_range(data)
print(f"detect_header_range: header_start={header_start}, header_end={header_end}")

# 测试 _find_header_end_by_content
header_end_content = _find_header_end_by_content(data)
print(f"_find_header_end_by_content: header_end={header_end_content}")

# 框线检测
border_info = read_border_info(path, sheet)
rows_info = border_info.get('rows', []) if border_info else []
# Trim rows_info
if border_info:
    ri = border_info["rows"]
    ri = ri[:len(data)]

# 检测纵向子表
regions = _detect_vertical_subtables(data, rows_info if rows_info else None)
print(f"\n纵向子表区域: {len(regions)} 个")
for idx, r in enumerate(regions):
    print(f"  区域{idx}: {r}")
    # 测试表头合并
    h_start = r.get("header_start", 0)
    h_end = r.get("header_end", h_start)
    columns = _merge_multi_row_header(data, h_start, h_end)
    print(f"    合并列名: {columns}")
    # 显示前3行数据
    d_start = r.get("data_start", h_end + 1)
    for i in range(d_start, min(d_start + 3, r.get("data_end", len(data)))):
        non_empty = [v for v in data[i] if v is not None and str(v).strip() != ""]
        print(f"    数据 Row {i}: {non_empty}")
