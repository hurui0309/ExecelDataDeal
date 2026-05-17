"""调试交换比价表的表头合并问题"""
import sys
sys.path.insert(0, r"c:\Users\31039\ExecelDataDeal\data_to_db")

from services.excel_reader import read_sheet
from services.excel_utils import is_empty_row, is_header_like_row
from strategies.strategy_multi_header import _merge_multi_row_header
from services.mysql_writer import sanitize_column_name, make_unique_columns
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
for i, row in enumerate(data[:10]):
    print(f"  Row {i}: {list(row)[:7]}")

# 模拟表头合并：假设 header_start=3, header_end=4（Row3="农产品"行，Row4="工业品/年份"行）
print("\n=== 模拟 header_start=3, header_end=4 ===")
columns = _merge_multi_row_header(data, 3, 4)
print(f"  合并后列名: {columns}")

columns = make_unique_columns(columns)
columns = [rename_id_col(c) for c in columns]
print(f"  唯一化后: {columns}")

# 模拟表头合并：假设 header_start=3, header_end=5
print("\n=== 模拟 header_start=3, header_end=5 ===")
columns2 = _merge_multi_row_header(data, 3, 5)
print(f"  合并后列名: {columns2}")

columns2 = make_unique_columns(columns2)
columns2 = [rename_id_col(c) for c in columns2]
print(f"  唯一化后: {columns2}")

# 模拟表头合并：假设 header_start=4, header_end=4（仅Row4）
print("\n=== 模拟 header_start=4, header_end=4 ===")
columns3 = _merge_multi_row_header(data, 4, 4)
print(f"  合并后列名: {columns3}")

columns3 = make_unique_columns(columns3)
columns3 = [rename_id_col(c) for c in columns3]
print(f"  唯一化后: {columns3}")

# 检查 sanitize_column_name 对年份的处理
print("\n=== sanitize_column_name 测试 ===")
for name in ["1952年", "1957年", "1978年", "1985年", "1986年", "农产品", "工业品", "百公斤", "项目"]:
    print(f"  '{name}' → '{sanitize_column_name(name)}'")

# 检查 Row 4 和 Row 5 合并的逻辑
print("\n=== Row 4 和 Row 5 逐列数据 ===")
for c in range(7):
    v4 = data[4][c] if c < len(data[4]) else None
    v5 = data[5][c] if c < len(data[5]) else None
    v6 = data[6][c] if c < len(data[6]) else None
    print(f"  Col {c}: Row4={repr(v4)}, Row5={repr(v5)}, Row6={repr(v6)}")
