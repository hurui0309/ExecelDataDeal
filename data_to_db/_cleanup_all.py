"""清理纵向多子表全部旧入库记录，准备重新入库"""
import pymysql

conn = pymysql.connect(host='localhost', port=3306, user='root', password='123456', database='ods_data', charset='utf8mb4')
cursor = conn.cursor()

# 查找所有纵向多子表相关的表
patterns = ['%city%232%', '%exchange%ratio%', '%population%1982%', '%grain%output%1981%', '%social_commodity%']
total_dropped = 0
for pat in patterns:
    cursor.execute(f"SHOW TABLES LIKE '{pat}'")
    tables = cursor.fetchall()
    for (tbl,) in tables:
        cursor.execute(f"DROP TABLE IF EXISTS `{tbl}`")
        print(f"  已删除: {tbl}")
        total_dropped += 1

# 删除 parse_log 记录
cursor.execute("DELETE FROM ods_parse_log WHERE source_path LIKE '%纵向多子表%'")
deleted = cursor.rowcount
print(f"  已删除 parse_log 记录: {deleted} 条")

conn.commit()
conn.close()
print(f"清理完成: 删除 {total_dropped} 个表, {deleted} 条日志")
