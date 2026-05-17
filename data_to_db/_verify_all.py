"""验证全部入库结果"""
import pymysql

conn = pymysql.connect(host='localhost', port=3306, user='root', password='123456', database='ods_data', charset='utf8mb4')
cursor = conn.cursor()

# 查找所有纵向多子表相关的表
cursor.execute("SHOW TABLES LIKE 'ods_%'")
tables = cursor.fetchall()
print(f"=== 全部入库表: {len(tables)} 个 ===\n")

for (tbl,) in tables:
    cursor.execute(f"SELECT COUNT(*) FROM `{tbl}`")
    cnt = cursor.fetchone()[0]
    cursor.execute(f"SHOW COLUMNS FROM `{tbl}`")
    cols = [c[0] for c in cursor.fetchall()]
    print(f"{tbl}: {cnt} rows, cols={cols[:8]}")

conn.close()
