"""审查入库结果 - 详细对比每个数据表"""
import pymysql

conn = pymysql.connect(host='localhost', port=3306, user='root', password='123456', database='ods_data', charset='utf8mb4')
cursor = conn.cursor()

# 获取所有数据表
cursor.execute('SHOW TABLES')
all_tables = [t[0] for t in cursor.fetchall() if t[0] != 'ods_parse_log']

for t in all_tables:
    cursor.execute(f'SHOW COLUMNS FROM `{t}`')
    cols = [c[0] for c in cursor.fetchall()]
    
    cursor.execute(f'SELECT * FROM `{t}`')
    rows = cursor.fetchall()
    
    print(f"\n{'='*80}")
    print(f"表: {t} ({len(rows)} rows)")
    print(f"列: {cols}")
    print(f"{'='*80}")
    for r in rows:
        d = dict(zip(cols, r))
        # 只打印数据列（排除元数据列）
        data_cols = [c for c in cols if not c.startswith('_')]
        data_vals = {c: d[c] for c in data_cols}
        print(f"  {data_vals}")

cursor.close()
conn.close()
