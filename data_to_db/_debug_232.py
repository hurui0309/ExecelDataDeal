"""调试232城市表：分析Excel原始数据和当前入库结果"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from services.excel_reader import read_sheet
import pymysql

EXCEL = r'c:\Users\31039\ExecelDataDeal\data\纵向多子表\232个城市主要经济指标(一~四).xlsx'
SHEET = '1-4'

# 1. 读取 Excel 原始数据
data, row_has_hborder = read_sheet(EXCEL, SHEET)
print(f"=== Excel 原始数据: {len(data)} 行 x {len(data[0]) if data else 0} 列 ===\n")

# 打印前30行（含换行符检测）
for i, row in enumerate(data[:30]):
    # 检查单元格内换行
    has_newline = any(v is not None and '\n' in str(v) for v in row)
    newline_cells = [(j, repr(str(v))) for j, v in enumerate(row) if v is not None and '\n' in str(v)]
    display = [str(v)[:20] if v is not None else '' for v in row]
    marker = " <<NEWLINE>> " + str(newline_cells) if has_newline else ""
    print(f"Row {i:2d}: {display}{marker}")

print("\n\n=== 换行单元格详情 ===")
for i, row in enumerate(data[:30]):
    for j, v in enumerate(row):
        if v is not None and '\n' in str(v):
            print(f"  Row {i}, Col {j}: {repr(str(v))}")

# 2. 查询数据库当前入库结果
print("\n\n=== 数据库当前入库结果 ===")
conn = pymysql.connect(host='localhost', port=3306, user='root', password='123456', database='ods_data', charset='utf8mb4')
cursor = conn.cursor()

# 查找相关表
cursor.execute("SHOW TABLES LIKE '%city%232%'")
tables = cursor.fetchall()
print(f"相关表: {tables}")

for (tbl,) in tables:
    cursor.execute(f"SELECT COUNT(*) FROM `{tbl}`")
    cnt = cursor.fetchone()[0]
    cursor.execute(f"SHOW COLUMNS FROM `{tbl}`")
    cols = [c[0] for c in cursor.fetchall()]
    print(f"\n表 {tbl}: {cnt} 行, 列: {cols}")
    # 显示前3行
    cursor.execute(f"SELECT * FROM `{tbl}` LIMIT 3")
    rows = cursor.fetchall()
    for r in rows:
        print(f"  {r}")

conn.close()
