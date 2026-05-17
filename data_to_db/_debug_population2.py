"""查看人口和自然资源表的完整数据"""
import openpyxl

path = r"c:\Users\31039\ExecelDataDeal\data\纵向多子表\人口和自然资源(一~二).xlsx"
wb = openpyxl.load_workbook(path, data_only=True)
ws = wb["1-2"]

print("=== 人口和自然资源(一~二) 完整数据 ===")
for i, row in enumerate(ws.iter_rows(values_only=True)):
    vals = list(row)
    print(f"  Row {i}: {vals}")
wb.close()
