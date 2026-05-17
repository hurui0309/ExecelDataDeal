"""验证修复后的入库结果"""
import pymysql

conn = pymysql.connect(host='localhost', port=3306, user='root', password='123456',
                       database='ods_data', charset='utf8mb4')
cursor = conn.cursor()

# 1. 查看所有数据表
cursor.execute("SHOW TABLES")
tables = [t[0] for t in cursor.fetchall() if t[0] != 'ods_parse_log']
print(f"=== 数据表 ({len(tables)} 个) ===")
for t in tables:
    cursor.execute(f"SELECT COUNT(*) FROM `{t}`")
    cnt = cursor.fetchone()[0]
    cursor.execute(f"SHOW COLUMNS FROM `{t}`")
    cols = [c[0] for c in cursor.fetchall()]
    print(f"  {t}: {cnt} 行, 列: {cols}")

# 2. 重点检查交换比价表
print("\n=== 交换比价表 ===")
for t in tables:
    if 'exchange' in t.lower():
        cursor.execute(f"SHOW COLUMNS FROM `{t}`")
        cols = [c[0] for c in cursor.fetchall()]
        print(f"  表 {t} 列名: {cols}")
        cursor.execute(f"SELECT * FROM `{t}` LIMIT 3")
        rows = cursor.fetchall()
        for r in rows:
            print(f"    {r}")

# 3. 重点检查人口表
print("\n=== 人口和自然资源表 ===")
for t in tables:
    if 'population' in t.lower():
        cursor.execute(f"SHOW COLUMNS FROM `{t}`")
        cols = [c[0] for c in cursor.fetchall()]
        print(f"  表 {t} 列名: {cols}")
        cursor.execute(f"SELECT * FROM `{t}` LIMIT 5")
        rows = cursor.fetchall()
        for r in rows:
            print(f"    {r}")
        cursor.execute(f"SELECT COUNT(*) FROM `{t}`")
        cnt = cursor.fetchone()[0]
        print(f"  总行数: {cnt}")

# 4. 查看解析日志
print("\n=== 解析日志 ===")
cursor.execute("SELECT id, source_filename, sheet_name, status, parse_strategy, table_name, subtable_index FROM ods_parse_log ORDER BY id")
rows = cursor.fetchall()
for r in rows:
    print(f"  id={r[0]}, file={r[1]}, sheet={r[2]}, status={r[3]}, strategy={r[4]}, table={r[5]}, sub_idx={r[6]}")

cursor.close()
conn.close()
