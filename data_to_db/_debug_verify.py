"""调试 _verify_split_has_subtable"""
import sys
sys.path.insert(0, r"c:\Users\31039\ExecelDataDeal\data_to_db")

from services.excel_reader import read_sheet
from services.excel_utils import is_empty_row, is_header_like_row
from services.border_info import read_border_info, detect_vertical_splits_by_border
from strategies.strategy_vertical_subtable import _verify_split_has_subtable, _find_header_end_by_content
from services.table_layout import _is_data_like_row

path = r"c:\Users\31039\ExecelDataDeal\data\纵向多子表\人口和自然资源(一~二).xlsx"
sheet = "1-2"

data, _ = read_sheet(path, sheet, read_border=True)
while data and is_empty_row(data[0]):
    data.pop(0)
while data and is_empty_row(data[-1]):
    data.pop()

header_end = _find_header_end_by_content(data)
print(f"header_end: {header_end}")

border_info = read_border_info(path, sheet)
ri = border_info["rows"][:len(data)]
border_splits = detect_vertical_splits_by_border(ri, header_end)
print(f"框线分割点: {border_splits}")

for s in border_splits:
    print(f"\n=== 验证分割点 s={s} ===")
    # 手动执行 _verify_split_has_subtable 的逻辑
    n = len(data)
    j = s + 1
    while j < min(s + 10, n) and is_empty_row(data[j]):
        j += 1
    print(f"  第一个非空行: j={j}, data={list(data[j])}")
    
    non_empty_j = [v for v in data[j] if v is not None and str(v).strip() != ""]
    print(f"  non_empty: {non_empty_j}")
    
    if len(non_empty_j) == 1:
        k = j + 1
        while k < min(s + 10, n) and is_empty_row(data[k]):
            k += 1
        print(f"  标题行后第一个非空行: k={k}, data={list(data[k])}")
        print(f"  is_header_like_row: {is_header_like_row(data[k])}")
        
        if is_header_like_row(data[k]):
            m = k + 1
            while m < min(s + 10, n) and is_empty_row(data[m]):
                m += 1
            print(f"  表头行后第一个非空行: m={m}, data={list(data[m])}")
            non_empty_m = [v for v in data[m] if v is not None and str(v).strip() != ""]
            print(f"  non_empty_m: {non_empty_m}")
            if len(non_empty_m) == 1:
                p = m + 1
                while p < min(s + 10, n) and is_empty_row(data[p]):
                    p += 1
                print(f"  分类行后第一个非空行: p={p}, data={list(data[p])}")
                print(f"  _is_data_like_row: {_is_data_like_row(data[p])}")
    
    result = _verify_split_has_subtable(data, s, header_end)
    print(f"  验证结果: {result}")
