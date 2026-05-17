"""测试所有文件的解析结果"""
import sys
sys.path.insert(0, r'c:\Users\31039\ExecelDataDeal\data_to_db')

from services.excel_reader import read_sheet
from services.excel_utils import is_empty_row
from strategies.strategy_vertical_subtable import _detect_vertical_subtables
from services.border_info import read_border_info
from strategies.strategy_multi_header import _merge_multi_row_header

files = [
    (r'c:\Users\31039\ExecelDataDeal\data\纵向多子表\人口和自然资源(一~二).xlsx', '1-2', '人口和自然资源'),
    (r'c:\Users\31039\ExecelDataDeal\data\纵向多子表\主要农产品与工业品的交换比价(一~二).xlsx', '1-2', '交换比价'),
    (r'c:\Users\31039\ExecelDataDeal\data\纵向多子表\1981年主要农产品产量中各地区产量占的比重(一~三).xlsx', '1-3', '1981年农产品'),
    (r'c:\Users\31039\ExecelDataDeal\data\纵向多子表\1985年各地区社会商品零售总额(一~二).xlsx', '1-2', '1985年零售'),
    (r'c:\Users\31039\ExecelDataDeal\data\纵向多子表\232个城市主要经济指标(一~四).xlsx', '1-4', '232城市'),
]

for path, sheet, name in files:
    try:
        data, _ = read_sheet(path, sheet, read_border=True)
    except Exception as e:
        print(f"\n=== {name}: 读取失败 {e} ===")
        continue
    while data and is_empty_row(data[0]):
        data.pop(0)
    while data and is_empty_row(data[-1]):
        data.pop()

    border_info = read_border_info(path, sheet)
    rows_info = None
    if border_info:
        ri = border_info["rows"]
        rows_info = ri[:len(data)] if len(ri) > len(data) else ri

    regions = _detect_vertical_subtables(data, rows_info)
    print(f"\n=== {name} ({len(data)} 行) ===")
    print(f"  子表数: {len(regions)}")
    for idx, r in enumerate(regions):
        h_start = r.get("header_start", 0)
        h_end = r.get("header_end", h_start)
        d_start = r.get("data_start", h_end + 1)
        d_end = r.get("data_end", len(data))
        try:
            columns = _merge_multi_row_header(data, h_start, h_end)
        except Exception as e:
            columns = [f"ERROR: {e}"]
        data_rows = d_end - d_start
        print(f"  子表{idx}: label={r.get('label','?')}, header={h_start}-{h_end}, data={d_start}-{d_end} ({data_rows}行)")
        col_preview = str(columns[:6]) + ('...' if len(columns) > 6 else '')
        print(f"    列名: {col_preview}")
