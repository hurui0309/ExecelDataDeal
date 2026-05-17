"""详细验证所有纵向多子表入库结果"""
import pymysql

conn = pymysql.connect(host='localhost', port=3306, user='root', password='123456', database='ods_data', charset='utf8mb4')
cursor = conn.cursor()

# 1. 所有ODS表概览
cursor.execute("SHOW TABLES LIKE 'ods_%'")
all_tables = cursor.fetchall()
print(f"=== Total ODS tables: {len(all_tables)} ===\n")
for (tbl,) in all_tables:
    cursor.execute(f"SELECT COUNT(*) FROM `{tbl}`")
    cnt = cursor.fetchone()[0]
    print(f"  {tbl}: {cnt} rows")

# 2. 人口表详细验证
print("\n=== Population table ===")
cursor.execute("SHOW TABLES LIKE '%population%'")
for (tbl,) in cursor.fetchall():
    cursor.execute(f"SELECT COUNT(*) FROM `{tbl}`")
    cnt = cursor.fetchone()[0]
    cursor.execute(f"SHOW COLUMNS FROM `{tbl}`")
    cols = [c[0] for c in cursor.fetchall()]
    indicator_col = cols[1] if len(cols) > 1 else cols[0]
    has_unit = 'unit' in cols
    # 检查整行NULL: 第2列(indicator)有值但第3列(unit)为NULL
    if has_unit:
        cursor.execute(f"SELECT id, `{indicator_col}` FROM `{tbl}` WHERE `{indicator_col}` IS NOT NULL AND `unit` IS NULL")
        null_rows = cursor.fetchall()
        status = f"[X] {len(null_rows)} indicator-without-unit" if null_rows else "[OK]"
    else:
        # 无unit列，检查是否有整行除id外全NULL
        cursor.execute(f"SELECT COUNT(*) FROM `{tbl}` WHERE `{indicator_col}` IS NULL")
        null_cnt = cursor.fetchone()[0]
        status = f"[X] {null_cnt} all-null-rows" if null_cnt > 0 else "[OK] no-unit-col"
    print(f"  {tbl}: {cnt} rows  {status}  cols={cols[:5]}...")
    # 显示前5行
    cursor.execute(f"SELECT * FROM `{tbl}` LIMIT 5")
    for r in cursor.fetchall():
        vals = [str(v)[:20] if v is not None else 'NULL' for v in r[:4]]
        print(f"    {vals}")

# 3. 232城市表续行合并效果
print("\n=== 232 city tables - merge verification ===")
cursor.execute("SHOW TABLES LIKE '%city%232%'")
for (tbl,) in cursor.fetchall():
    cursor.execute(f"SELECT COUNT(*) FROM `{tbl}`")
    cnt = cursor.fetchone()[0]
    cursor.execute(f"SHOW COLUMNS FROM `{tbl}`")
    cols = [c[0] for c in cursor.fetchall()]
    indicator_col = cols[1] if len(cols) > 1 else cols[0]

    # 检查续行合并特征
    cursor.execute(f"SELECT id, `{indicator_col}`, `unit` FROM `{tbl}`")
    rows = cursor.fetchall()
    merge_keywords = ['1980', '不变价格', '万平方', '固定资产原值', '固定资产净值', '全民所有制独立']
    merged_indicators = []
    for r in rows:
        indicator = str(r[1]) if r[1] else ''
        for kw in merge_keywords:
            if kw in indicator:
                merged_indicators.append((r[0], indicator, r[2]))
                break

    # 检查整行NULL
    cursor.execute(f"SELECT id, `{indicator_col}` FROM `{tbl}` WHERE `{indicator_col}` IS NOT NULL AND `unit` IS NULL")
    null_rows = cursor.fetchall()

    print(f"\n  {tbl}: {cnt} rows")
    print(f"    NULL-check: {'[X] ' + str(len(null_rows)) + ' indicator-without-unit' if null_rows else '[OK]'}")
    if null_rows:
        for r in null_rows:
            print(f"      id={r[0]} indicator='{r[1]}'")
    print(f"    Merged indicators ({len(merged_indicators)}):")
    for id_, ind, unit in merged_indicators:
        print(f"      id={id_} indicator='{ind}' unit='{unit}'")

conn.close()
print("\n=== Verification complete ===")
