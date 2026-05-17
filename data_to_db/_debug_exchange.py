"""调试交换比价表的表头解析"""
import openpyxl

path = r"c:\Users\31039\ExecelDataDeal\data\纵向多子表\主要农产品与工业品的交换比价(一~二).xlsx"
wb = openpyxl.load_workbook(path, data_only=True)
ws = wb["1-2"]

print("=== 主要农产品与工业品的交换比价(一~二) ===")
for i, row in enumerate(ws.iter_rows(values_only=True)):
    if i >= 30:
        break
    vals = list(row)
    print(f"  Row {i}: {vals}")

# 检查合并单元格
print(f"\n合并单元格: {ws.merged_cells.ranges}")
wb.close()
