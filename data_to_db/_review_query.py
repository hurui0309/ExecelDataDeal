"""审查入库结果 - 查询数据库记录和数据表"""
import pymysql

conn = pymysql.connect(host='localhost', port=3306, user='root', password='123456', database='ods_data', charset='utf8mb4')
cursor = conn.cursor()

# 1. 日志表结构
cursor.execute('SHOW COLUMNS FROM ods_parse_log')
cols = [c[0] for c in cursor.fetchall()]
print("=== ods_parse_log 列 ===")
print(cols)

# 2. 日志记录
cursor.execute('SELECT * FROM ods_parse_log ORDER BY id')
rows = cursor.fetchall()
col_names = cols
print(f"\n=== 解析日志 ({len(rows)} 条) ===")
for r in rows:
    d = dict(zip(col_names, r))
    print(f"  id={d['id']}, file={d.get('source_filename','?')}, sheet={d.get('sheet_name','?')}, "
          f"status={d.get('status','?')}, strategy={d.get('parse_strategy','?')}, "
          f"table={d.get('table_name','?')}, sub_idx={d.get('subtable_index','?')}")

# 3. 数据表列表
cursor.execute('SHOW TABLES')
all_tables = [t[0] for t in cursor.fetchall()]
data_tables = [t for t in all_tables if t != 'ods_parse_log']
print(f"\n=== 数据表 ({len(data_tables)}) ===")

for t in data_tables:
    cursor.execute(f'SELECT COUNT(*) FROM `{t}`')
    cnt = cursor.fetchone()[0]
    cursor.execute(f'SHOW COLUMNS FROM `{t}`')
    tcols = [c[0] for c in cursor.fetchall()]
    print(f"  {t}: {cnt} rows, cols={tcols}")

    # 显示前3行
    cursor.execute(f'SELECT * FROM `{t}` LIMIT 3')
    sample = cursor.fetchall()
    for s in sample:
        print(f"    {s}")

cursor.close()
conn.close()
