"""调试人口表的框线分割点"""
import sys
sys.path.insert(0, r"c:\Users\31039\ExecelDataDeal\data_to_db")

from services.excel_reader import read_sheet
from services.excel_utils import is_empty_row, is_header_like_row
from services.border_info import read_border_info, detect_header_end_by_border, detect_vertical_splits_by_border
from strategies.strategy_vertical_subtable import _find_header_end_by_content, _find_splits_by_content

path = r"c:\Users\31039\ExecelDataDeal\data\纵向多子表\人口和自然资源(一~二).xlsx"
sheet = "1-2"

data, _ = read_sheet(path, sheet, read_border=True)
while data and is_empty_row(data[0]):
    data.pop(0)
while data and is_empty_row(data[-1]):
    data.pop()

border_info = read_border_info(path, sheet)
if border_info:
    ri = border_info["rows"]
    if len(ri) > len(data):
        ri = ri[:len(data)]

    # 框线检测 header_end
    raw_he = detect_header_end_by_border(ri)
    print(f"框线 header_end: {raw_he}")

    # 框线检测纵向分割点
    header_end_by_content = _find_header_end_by_content(data)
    print(f"内容 header_end: {header_end_by_content}")

    header_end = min(raw_he, header_end_by_content) if raw_he is not None and header_end_by_content is not None else (raw_he or header_end_by_content)
    print(f"合并 header_end: {header_end}")

    border_splits = detect_vertical_splits_by_border(ri, header_end)
    print(f"\n框线纵向分割点: {border_splits}")

    content_splits = _find_splits_by_content(data, header_end)
    print(f"内容纵向分割点: {content_splits}")

    all_splits = sorted(set(border_splits + content_splits))
    print(f"合并纵向分割点: {all_splits}")

    # 打印分割点附近的框线信息
    print(f"\n=== 所有行的框线信息 ===")
    for i, r in enumerate(ri):
        if r.get('bottom_solid') or r.get('bottom_dash'):
            print(f"  Row {i}: bottom_solid={r.get('bottom_solid')}, bottom_ratio={r.get('bottom_ratio', 0):.0%}, bottom_style={r.get('bottom_style')}")
