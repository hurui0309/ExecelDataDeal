---
name: data-retrieval
description: >
  数据需求检索与报告生成技能。当用户提出数据需求（如"帮我查XXX数据"、"有没有关于XXX的表格"、
  "找一下包含XXX字段的Excel"）时触发。使用 Phase 3 智能检索引擎在 ods_sheet_metadata
  和索引表中检索匹配的 Excel 文件和表字段，生成 Markdown 报告输出到 search_results 目录，
  文件名按 日期_批次号_查询摘要 格式命名。
trigger_keywords:
  - 查数据、查表、查Excel、找数据、找表、找字段、检索数据、数据检索、查询数据
  - 帮我查、帮我找、帮我检索、搜索数据、搜索表
  - 有没有关于、包含什么字段、哪些表有
  - 数据需求、数据查询、查找Excel
---

# 数据需求检索与报告生成

## 用途

根据用户的自然语言数据需求，在已解析入库的 Excel 元数据库中进行智能检索，匹配最相关的数据表和字段，生成结构化 Markdown 报告。

## 触发条件

当用户使用以下表达时触发本技能：
- "帮我查一下1985年各地区社会商品零售总额"
- "有没有关于粮食产量的表"
- "找一下包含播种面积字段的Excel"
- "检索粮食安全相关数据"
- 任何包含数据查询、表查找、字段检索意图的请求

## 执行流程

### Step 1: 确认查询意图

从用户输入中提取核心查询诉求，如果查询模糊，可以向用户确认：
- 时间范围（如具体年份）
- 地理范围（如全国/省份/地级市）
- 核心指标（如产量/消费量/价格）
- 数据主题（如农业/经济/人口）

### Step 2: 执行检索

运行检索脚本：

```bash
cd d:/projects/ExecelDataDeal/data_retrieval
python ../.codebuddy/skills/data-retrieval/scripts/search_and_report.py "<用户查询文本>"
```

脚本自动完成：
1. 加载 Phase 3 搜索引擎（连接`ods_data`数据库）
2. LLM 意图解析 → 关键词/分类/描述多路召回
3. 字段级匹配 + LLM 重排序
4. 生成 Markdown 报告

### Step 3: 输出结果

报告输出到 `data_retrieval/search_results/` 目录：

```
search_results/
├── 20260525_001_粮食产量.md       ← Markdown 报告
├── 20260525_001.json              ← JSON 数据
├── 20260525_002_社会商品零售总额.md
├── 20260525_002.json
└── ...
```

**命名规则**: `YYYYMMDD_批次号_查询摘要.md`
- 批次号: 当日自增（001, 002, 003...）

### Step 4: 向用户展示结果

执行完成后，向用户摘要展示：
1. 匹配到的表数量
2. 每个表的文件名、Sheet名、评分配额
3. Markdown 报告完整路径
4. 关键匹配字段预览

## 报告内容

每份 Markdown 报告包含：

1. **检索结果总览** - 所有匹配表的评分排序表格
2. **各表详情** - 每个表的：
   - 匹配评分（0-1.0，带可视化进度条）
   - 文件路径、Sheet 序号
   - 分类标签、表描述
   - 匹配方式说明
   - **匹配字段表** - 最相关的字段及语义描述
   - **全部字段** - 可折叠的全部列信息

## 依赖

- MySQL 数据库 `ods_data`（需可访问）
- 表 `ods_sheet_metadata`（第一阶段产出）
- 表 `ods_category_index`、`ods_keyword_index`（第二阶段产出）
- LLM API（配置在 `phase2_config.yaml`）
- Python 依赖: `pymysql`, `openai`, `pyyaml`

## 前置条件

首次使用前，确保已执行：
```bash
# 第一阶段：语义提取（生成 ods_sheet_metadata）
cd data_retrieval && python phase1_extract.py

# 第二阶段：构建索引（生成 category/keyword 索引）
cd data_retrieval && python phase2_index.py
```

## 注意事项

- 检索结果质量取决于 `ods_sheet_metadata` 中元数据的覆盖度
- 如果 LLM API 不可用，检索会降级为纯关键词匹配（精度会下降）
- 同一日多次查询会自动递增批次号，不会覆盖历史报告
- JSON 文件保留完整数据结构，可供程序化读取
