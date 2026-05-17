"""检查全部数据的关键合并点"""
import sys, os, re
sys.path.insert(0, os.path.dirname(__file__))

from services.table_layout import preprocess_sheet

EXCEL = r'c:\Users\31039\ExecelDataDeal\data\纵向多子表\232个城市主要经济指标(一~四).xlsx'
SHEET = '1-4'

result = preprocess_sheet(EXCEL, SHEET, read_border=False, do_truncate_footnotes=True)
data = result['data']

print(f"合并后总行数: {len(data)} (原始187行)")
print(f"\n=== 逐行检查（仅非空行）===")

# 预期的关键合并词
expected_merges = [
    '万平方公里', '不变价格计算', '不换价格计算', '按当年价格计算', '按当年价值计算',
    '固定资产原值', '固定资产净值', '企业利润和税金', '措施投资', '措施新增固定资产',
    '资产投资', '新增固定资产', '括私人建房', '万平方米', '消费品零售额',
    '对非农业居民零售额', '年底职工人数', '工资总额', '劳动力数',
]

sparse_count = 0
full_null_count = 0

for i, row in enumerate(data):
    non_empty = [(c, str(v).strip()) for c, v in enumerate(row) if v is not None and str(v).strip()]
    if not non_empty:
        continue
    
    col0 = str(row[0]).strip() if row[0] else ''
    col1 = str(row[1]).strip() if len(row) > 1 and row[1] else ''
    
    # 检查是否有预期合并词
    has_merge = any(kw in col0 or kw in col1 for kw in expected_merges)
    
    # 检查稀疏行（只有1-2个非空，无数值）
    is_sparse = (0 < len(non_empty) <= 2 and 
                 not any(re.match(r"^-?\d+\.?\d*$", str(v).strip().replace(",","")) for _, v in non_empty))
    
    # 检查整行NULL（第一列有值但其余全NULL，且第一列不是分类标题）
    if col0 and not col1 and len(non_empty) <= 2 and is_sparse:
        if not col0.endswith('：') and not col0.startswith('注'):
            sparse_count += 1
    
    # 仅打印关键行
    if has_merge or is_sparse:
        marker = " <<MERGED>>" if has_merge else " <<SPARSE>>"
        display = [str(v)[:30] if v is not None else '' for v in row]
        print(f"Row {i:2d}: {display}{marker}")

print(f"\n稀疏行总数: {sparse_count}")

# 检查是否有续行导致的整行NULL数据（数据库中的问题）
print(f"\n=== 检查数据库问题模式 ===")
print("在数据库中，续行被独立入库导致整行NULL。合并后不应再出现此模式。")
# 模拟：如果一个数据行的col0是空的，且col1也是空的，且后面的列也是空的，那是问题行
problem_rows = 0
for i, row in enumerate(data):
    non_empty = [v for v in row if v is not None and str(v).strip()]
    if len(non_empty) == 0:
        continue
    # 检查是否有"只有col0有值但其余全是NULL"的行（在数据区中这是续行残留的标志）
    col0 = str(row[0]).strip() if row[0] else ''
    rest_empty = all(v is None or str(v).strip() == '' for v in row[1:])
    if col0 and rest_empty and not col0.endswith('：') and not col0.startswith('注') and not col0.startswith('在'):
        # 这可能是续行残留或分类标题
        if not re.match(r'^[一二三四五六七八九十]+、', col0):
            problem_rows += 1
            print(f"  疑似问题行 Row {i}: col0='{col0}'")

print(f"疑似问题行总数: {problem_rows}")
