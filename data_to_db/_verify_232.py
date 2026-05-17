"""验证232城市表入库结果"""
import pymysql

conn = pymysql.connect(host='localhost', port=3306, user='root', password='123456', database='ods_data', charset='utf8mb4')
cursor = conn.cursor()

# 查找232城市表
cursor.execute("SHOW TABLES LIKE '%city%232%'")
tables = cursor.fetchall()
print(f"=== 232城市表入库结果: {len(tables)} 个子表 ===\n")

for (tbl,) in tables:
    cursor.execute(f"SELECT COUNT(*) FROM `{tbl}`")
    cnt = cursor.fetchone()[0]
    cursor.execute(f"SHOW COLUMNS FROM `{tbl}`")
    cols = [c[0] for c in cursor.fetchall()]
    
    # 获取指标列名称
    indicator_col = cols[1] if len(cols) > 1 else cols[0]
    
    print(f"\n表 {tbl}: {cnt} 行")
    print(f"  列: {cols}")
    
    # 检查整行NULL（关键问题）
    cursor.execute(f"SELECT id, `{indicator_col}` FROM `{tbl}` WHERE `{indicator_col}` IS NOT NULL AND `unit` IS NULL")
    null_rows = cursor.fetchall()
    if null_rows:
        print(f"  [X] indicator has value but unit is NULL: {len(null_rows)}")
        for r in null_rows[:5]:
            print(f"    id={r[0]}, indicator='{r[1]}'")
    else:
        print(f"  [OK] No all-NULL rows")
    
    # 显示前5行
    cursor.execute(f"SELECT * FROM `{tbl}` LIMIT 5")
    rows = cursor.fetchall()
    for r in rows:
        vals = [str(v)[:25] if v is not None else 'NULL' for v in r[:8]]
        print(f"  {vals}")
    
    # 显示最后3行
    cursor.execute(f"SELECT * FROM `{tbl}` ORDER BY id DESC LIMIT 3")
    rows = cursor.fetchall()
    print(f"  ... 末尾:")
    for r in reversed(rows):
        vals = [str(v)[:25] if v is not None else 'NULL' for v in r[:8]]
        print(f"  {vals}")

conn.close()
