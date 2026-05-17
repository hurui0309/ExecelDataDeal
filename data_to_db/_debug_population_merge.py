"""调试人口表的续行合并"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from services.excel_reader import read_sheet
from services.table_layout import merge_continuation_rows, truncate_footnotes
from services.excel_utils import is_empty_row

EXCEL = r'c:\Users\31039\ExecelDataDeal\data\纵向多子表\人口和自然资源(一~二).xlsx'
SHEET = '1-2'

data, row_has_hborder = read_sheet(EXCEL, SHEET, read_border=True)
print(f"原始数据: {len(data)} 行")

# 模拟策略流程：去首尾空行
while data and is_empty_row(data[0]):
    data.pop(0)
while data and is_empty_row(data[-1]):
    data.pop()
print(f"去空行后: {len(data)} 行")

# 截脚注
data = truncate_footnotes(data)
print(f"截脚注后: {len(data)} 行")

# 打印全部数据
for i, row in enumerate(data):
    non_empty = [(c, str(v).strip()[:25]) for c, v in enumerate(row) if v is not None and str(v).strip()]
    display = [str(v)[:25] if v is not None else '' for v in row]
    sparse = " <<SPARSE>>" if 0 < len(non_empty) <= 2 else ""
    print(f"Row {i:2d}: {display}{sparse}")

# 测试续行合并
import copy
test_data = copy.deepcopy(data)
merge_continuation_rows(test_data, None)
print(f"\n合并后: {len(test_data)} 行")
for i, row in enumerate(test_data[:20]):
    non_empty = [(c, str(v).strip()[:25]) for c, v in enumerate(row) if v is not None and str(v).strip()]
    display = [str(v)[:25] if v is not None else '' for v in row]
    sparse = " <<SPARSE>>" if 0 < len(non_empty) <= 2 else ""
    print(f"Row {i:2d}: {display}{sparse}")
