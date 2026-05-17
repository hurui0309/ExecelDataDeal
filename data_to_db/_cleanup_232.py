"""清理232城市表的旧入库记录"""
import pymysql

conn = pymysql.connect(host='localhost', port=3306, user='root', password='123456', database='ods_data', charset='utf8mb4')
cursor = conn.cursor()

# 查找相关表
cursor.execute("SHOW TABLES LIKE '%city%232%'")
tables = cursor.fetchall()
print(f"找到 {len(tables)} 个相关表: {[t[0] for t in tables]}")

for (tbl,) in tables:
    cursor.execute(f"DROP TABLE IF EXISTS `{tbl}`")
    print(f"  已删除: {tbl}")

# 查看 parse_log 表结构
cursor.execute("DESCRIBE ods_parse_log")
cols = cursor.fetchall()
col_names = [c[0] for c in cols]
print(f"\nods_parse_log 列: {col_names}")

# 用正确的列名删除
# 尝试不同的列名
for col in col_names:
    if 'file' in col.lower() or 'path' in col.lower() or 'source' in col.lower():
        try:
            cursor.execute(f"DELETE FROM ods_parse_log WHERE `{col}` LIKE '%232%'")
            print(f"  用列 `{col}` 删除 parse_log 记录: {cursor.rowcount} 条")
            break
        except Exception as e:
            print(f"  用列 `{col}` 失败: {e}")

# 也尝试 table_name
cursor.execute("DELETE FROM ods_parse_log WHERE table_name LIKE '%city%232%'")
print(f"  用 table_name 删除 parse_log 记录: {cursor.rowcount} 条")

conn.commit()
conn.close()
print("清理完成")
