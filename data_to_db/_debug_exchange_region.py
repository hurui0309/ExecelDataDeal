"""模拟 LLM 检测到的横向分区后各区域的表头检测"""
import sys
sys.path.insert(0, r"c:\Users\31039\ExecelDataDeal\data_to_db")

from services.excel_reader import read_sheet
from services.excel_utils import is_empty_row
from strategies.strategy_horizontal_split import _trim_left_empty_cols, _trim_right_empty_cols, _extract_region
from strategies.strategy_multi_header import _detect_header_range, _adjust_header_end_if_data_row

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

# 假设 LLM 检测到2个区域（2列 + 5列）
# 区域1: col 0-1 (农产品+工业品)
region1 = [row[0:2] for row in data]
region1 = _trim_left_empty_cols(region1)
region1 = _trim_right_empty_cols(region1)

print("=== 区域1: col 0-1 ===")
for i, row in enumerate(region1[:8]):
    print(f"  Row {i}: {list(row)}")

# 区域2: col 2-6 (年份+数据)
region2 = [row[2:7] for row in data]
region2 = _trim_left_empty_cols(region2)
region2 = _trim_right_empty_cols(region2)

print("\n=== 区域2: col 2-6 ===")
for i, row in enumerate(region2[:8]):
    print(f"  Row {i}: {list(row)}")

# 检测每个区域的表头范围
for name, rd in [("区域1", region1), ("区域2", region2)]:
    hs, he = _detect_header_range(rd)
    print(f"\n{name} _detect_header_range: header_start={hs}, header_end={he}")
    
    # 模拟框线检测
    if row_has_hborder:
        border_rows = []
        for i in range(min(15, len(row_has_hborder))):
            if row_has_hborder[i]:
                border_rows.append(i)
        
        if border_rows:
            best_candidate = None
            best_gap = 0
            for br in border_rows:
                gap = 0
                for j in range(br + 1, min(br + 10, len(row_has_hborder))):
                    if not row_has_hborder[j]:
                        gap += 1
                    else:
                        break
                if gap > best_gap:
                    best_gap = gap
                    best_candidate = br
            
            print(f"  框线 best_candidate={best_candidate}, best_gap={best_gap}")
            if best_candidate is not None:
                adj = _adjust_header_end_if_data_row(rd, best_candidate)
                print(f"  框线 _adjust_header_end_if_data_row: {best_candidate} → {adj}")
