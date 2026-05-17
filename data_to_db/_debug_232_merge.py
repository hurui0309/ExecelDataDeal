"""验证续行合并效果"""
import sys, os, re
sys.path.insert(0, os.path.dirname(__file__))

from services.table_layout import preprocess_sheet

EXCEL = r'c:\Users\31039\ExecelDataDeal\data\纵向多子表\232个城市主要经济指标(一~四).xlsx'
SHEET = '1-4'

result = preprocess_sheet(EXCEL, SHEET, read_border=False, do_truncate_footnotes=True)
data = result['data']

print(f"=== 合并后数据: {len(data)} 行 x {len(data[0]) if data else 0} 列 ===\n")

# 打印前50行，重点看之前有问题的行
for i, row in enumerate(data[:50]):
    non_empty = [(c, str(v).strip()) for c, v in enumerate(row) if v is not None and str(v).strip()]
    # 检查是否有之前的问题模式
    display = [str(v)[:30] if v is not None else '' for v in row]
    
    # 检查指标列（col 0）是否有已知的合并结果
    marker = ""
    col0 = str(row[0]).strip() if row[0] else ''
    if '不变价格计算' in col0 or '不换价格计算' in col0:
        marker = " <<MERGED>>"
    if '万平方公里' in col0 or '万平方公里' in str(row[1]) if len(row) > 1 and row[1] else False:
        marker = " <<MERGED>>"
    
    # 检查是否是整行NULL（不该出现的）
    if 0 < len(non_empty) <= 2 and not any(re.match(r"^-?\d+\.?\d*$", str(v).strip().replace(",","")) for _, v in non_empty):
        marker += " <<SPARSE>>"
    
    print(f"Row {i:2d}: {display}{marker}")
