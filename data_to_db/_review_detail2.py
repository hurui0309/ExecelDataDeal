"""审查入库结果 - 查看特定表的数据用于审查"""
import pymysql

conn = pymysql.connect(host='localhost', port=3306, user='root', password='123456', database='ods_data', charset='utf8mb4')
cursor = conn.cursor()

# 查看交换比价表
for t in ['ods_agri_industrial_exchange_ratio_1_2_1952_1986_p1', 'ods_agri_industrial_exchange_ratio_1_2_1952_1986_p2']:
    cursor.execute(f'SHOW COLUMNS FROM `{t}`')
    cols = [c[0] for c in cursor.fetchall()]
    cursor.execute(f'SELECT * FROM `{t}`')
    rows = cursor.fetchall()
    print(f"\n{'='*80}")
    print(f"表: {t} ({len(rows)} rows)")
    print(f"列: {cols}")
    for r in rows:
        d = dict(zip(cols, r))
        data_cols = [c for c in cols if not c.startswith('_')]
        data_vals = {c: d[c] for c in data_cols}
        print(f"  {data_vals}")

# 查看城市经济指标表 - 每个表前10行
for i in range(1, 5):
    t = f'ods_city_232_key_econ_indicators_1_4_p{i}'
    cursor.execute(f'SHOW COLUMNS FROM `{t}`')
    cols = [c[0] for c in cursor.fetchall()]
    cursor.execute(f'SELECT * FROM `{t}` LIMIT 10')
    rows = cursor.fetchall()
    print(f"\n{'='*80}")
    print(f"表: {t} ({cols})")
    for r in rows:
        d = dict(zip(cols, r))
        data_cols = [c for c in cols if not c.startswith('_')]
        data_vals = {c: d[c] for c in data_cols}
        print(f"  {data_vals}")

# 查看1985年社会商品零售总额表
for i in range(1, 3):
    t = f'ods_1985_region_social_commodity_retail_total_1_2_p{i}'
    cursor.execute(f'SHOW COLUMNS FROM `{t}`')
    cols = [c[0] for c in cursor.fetchall()]
    cursor.execute(f'SELECT * FROM `{t}` LIMIT 10')
    rows = cursor.fetchall()
    print(f"\n{'='*80}")
    print(f"表: {t} ({cols})")
    for r in rows:
        d = dict(zip(cols, r))
        data_cols = [c for c in cols if not c.startswith('_')]
        data_vals = {c: d[c] for c in data_cols}
        print(f"  {data_vals}")

# 查看1981年主要农产品表
for i in range(1, 4):
    t = f'ods_grain_output_region_ratio_1981_1_3_p{i}'
    cursor.execute(f'SHOW COLUMNS FROM `{t}`')
    cols = [c[0] for c in cursor.fetchall()]
    cursor.execute(f'SELECT * FROM `{t}`')
    rows = cursor.fetchall()
    print(f"\n{'='*80}")
    print(f"表: {t} ({len(rows)} rows, cols={cols})")
    for r in rows:
        d = dict(zip(cols, r))
        data_cols = [c for c in cols if not c.startswith('_')]
        data_vals = {c: d[c] for c in data_cols}
        print(f"  {data_vals}")

cursor.close()
conn.close()
