"""清理数据库所有数据表"""
import pymysql

conn = pymysql.connect(host='localhost', port=3306, user='root', password='123456',
                       database='ods_data', charset='utf8mb4')
cursor = conn.cursor()

# 获取所有表
cursor.execute("SHOW TABLES")
tables = [t[0] for t in cursor.fetchall()]

for t in tables:
    cursor.execute(f"DROP TABLE IF EXISTS `{t}`")
    print(f"  删除表: {t}")

conn.commit()
cursor.close()
conn.close()
print("清理完成")
