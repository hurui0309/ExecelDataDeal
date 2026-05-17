"""完整模拟交换比价表的解析流程"""
import sys
sys.path.insert(0, r"c:\Users\31039\ExecelDataDeal\data_to_db")

from services.excel_reader import read_sheet
from services.excel_utils import is_empty_row, is_header_like_row
from strategies.strategy_horizontal_split import _detect_split_col, _detect_split_cols_by_border, _extract_region
from services.border_info import read_border_info

path = r"c:\Users\31039\ExecelDataDeal\data\纵向多子表\主要农产品与工业品的交换比价(一~二).xlsx"
sheet = "1-2"

data, row_has_hborder = read_sheet(path, sheet, read_border=True)

# 去首尾空行
trimmed_count = 0
while data and is_empty_row(data[0]):
    data.pop(0)
    trimmed_count += 1
while data and is_empty_row(data[-1]):
    data.pop()

print(f"=== 数据 {len(data)} 行, trimmed_count={trimmed_count} ===")

# 检测横向分区
split_col = _detect_split_col(data)
print(f"split_col={split_col}")

split_by_border = _detect_split_cols_by_border(path, sheet, data)
print(f"split_by_border={split_by_border}")

# 模拟 strategy_horizontal_split 的 run() 路径
# 走框线分割路径
from strategies.strategy_horizontal_split import _build_regions_from_split_cols, _trim_left_empty_cols, _trim_right_empty_cols
regions = _build_regions_from_split_cols(data, split_by_border) if split_by_border else []

print(f"\n=== 框线区域: {regions} ===")

# 对每个区域提取数据
for idx, (start_col, end_col, label) in enumerate(regions):
    region_data = [row[start_col:end_col] for row in data]
    region_data = _trim_left_empty_cols(region_data)
    region_data = _trim_right_empty_cols(region_data)
    
    print(f"\n=== 区域 {idx}: cols {start_col}-{end_col}, label={label} ===")
    print(f"  区域数据: {len(region_data)} 行")
    for i, row in enumerate(region_data[:8]):
        print(f"  Row {i}: {list(row)}")
    
    # 调用 _extract_region
    result = _extract_region(region_data, None, None, None, None, row_has_hborder,
                              path, sheet, trimmed_count, llm_client=None)
    print(f"\n  列名: {result['columns']}")
    print(f"  行数: {len(result['rows'])}")
    for i, row in enumerate(result['rows'][:5]):
        print(f"  数据 Row {i}: {row}")
