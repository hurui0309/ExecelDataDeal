---
name: data-review
description: >
  落库结果 Review 技能。当用户需要对 Excel 数据清洗入仓的结果进行质量审查时触发此技能。
  逐条读取 ods_parse_log 解析记录，对比数据库表结构/表数据与 Excel 原始数据，
  由大模型判断数据落库是否符合预期，输出置信度、不符合原因，生成 Markdown 审查报告。
  触发词：review、审查、检查落库结果、数据质量、落库质量、review 结果、检查数据。
---

# Data Review Skill — 落库结果审查

## 目的

对 Excel 数据清洗入仓的结果进行自动化质量审查，通过对比数据库中的表结构/表数据与 Excel 原始数据，判断落库是否符合预期，输出 Markdown 审查报告。

## 触发条件

当用户提出以下类型的请求时，激活此技能：
- "review 落库结果" / "检查数据质量" / "审查数据"
- "review 一下" / "帮我看看落库有没有问题"
- 任何涉及对已入库数据与原始 Excel 数据进行对比验证的请求

## 核心原则

1. **全量审查**：早期测试阶段，每条 SUCCESS 记录都必须审查到
2. **一次命令获取尽量多的信息**：优先使用 `full_review` 一键扫描全部记录
3. **分层深入**：`full_review` 全量扫描 → 仅对有问题的表用 `auto_compare` 深挖
4. **报告必须落盘**：审查完成后**必须**将报告写入 `review_reports/review_YYYYMMDD_HHmmss.md`

## 工作流程

### Step 1: 全量扫描（1 次命令）

使用 `full_review` 一次扫描所有 SUCCESS 记录：

```bash
cd d:/projects/ExecelDataDeal && python .codebuddy/skills/data-review/scripts/db_reader.py full_review
```

输出包含：
- `total`: 总记录数
- `statistics`: ok/warn/error 三级统计
- `records[]`: 每条记录的完整审查数据：
  - DB 信息：行数、列信息、首行/尾行样本、高 null 率列
  - Excel 信息：max_row/max_col/merged_count、前 5 行预览
  - 自动检测：列数不一致、空表、行数差异、高 null 率列、合并单元格、特殊策略、纯数字列名
  - `severity`: ok(无问题) / warn(小问题) / error(严重问题)
  - `issues[]`: 检测到的问题列表

**此命令一次输出所有 82 条记录的完整对比数据，无需逐条调用。**

### Step 2: 逐条审查（基于 full_review 输出）

对 Step 1 返回的每条记录，基于以下信息进行审查判断：

| 维度 | 检查内容 | full_review 中的数据 |
|------|---------|---------------------|
| 列完整性 | DB 列是否覆盖 Excel 所有有效列 | col_info + excel_summary.max_col 对比 |
| 数据准确性 | 首行/尾行样本是否与 Excel 预览一致 | sample_head + sample_tail + excel_head_rows |
| 表头识别 | 表头行是否正确识别 | column_names vs excel_head_rows[0] |
| 列名翻译 | 英文字段名是否合理翻译中文列名 | col_info 中的 comment |
| 行数一致性 | DB 行数 vs Excel 行数 | db_row_count vs excel_summary.max_row |
| 合并单元格 | 是否正确展开填充 | issues 中自动检测 |
| 空行/空列 | 无效空行是否过滤 | high_null_cols |
| 策略适配 | 策略选择是否恰当 | parse_strategy + issues 标记 |

**输出格式**（每条记录）：

```markdown
### [序号] table_name

- **源文件**: source_filename
- **Sheet**: sheet_name
- **解析策略**: parse_strategy
- **数据库行数**: actual_row_count
- **置信度**: 0.0 ~ 1.0
- **审查结论**: ✅ 符合预期 / ⚠️ 部分符合 / ❌ 不符合预期 / 🔍 需人工确认
- **问题详情**:（如无问题则写"无"）
```

**置信度规则**：
- 0.9~1.0: 完全匹配，无问题
- 0.7~0.89: 小问题（列名翻译不精准等，不影响使用）
- 0.5~0.69: 中等问题（行数偏差、部分列遗漏）
- 0.3~0.49: 严重问题（表头识别错误、数据错位）
- 0.0~0.29: 完全不符合（空表、数据完全错误）
- 置信度 < 0.5 → 🔍 需人工确认

### Step 3: 深度审查（仅针对有问题表）

对 Step 2 中判定为 ⚠️ 或 ❌ 的记录，使用 `auto_compare` 进行深度审查：

```bash
cd d:/projects/ExecelDataDeal && python .codebuddy/skills/data-review/scripts/db_reader.py auto_compare <table_name>
```

`auto_compare` 提供：
- 完整的 parse_log 元信息（含 column_names 映射）
- 更多 DB 样本行（3 行）
- 更多 Excel 预览行（20 行）
- 详细的空值统计

**仅在 full_review 发现问题时才调用此命令，减少工具调用次数。**

### Step 4: 总结归纳

1. **不符合预期的种类分类**：
   - 表头识别错误 / 列名翻译问题 / 行数不匹配 / 数据内容偏差
   - 合并单元格未正确处理 / 空行空列未过滤 / 策略选择不当 / 其他

2. **每种类型**：问题描述 + 出现次数 + 典型样例

3. **统计汇总**：总审查数 / 各置信度区间分布 / 各问题类型占比

### Step 5: 写入报告（必须执行！）

**这一步绝对不能跳过！** 审查完成后必须将报告写入文件。

1. 确保目录存在：`review_reports/`
2. 生成文件：`review_reports/review_YYYYMMDD_HHmmss.md`
3. 报告结构：

```markdown
# 落库结果审查报告

- 审查时间: YYYY-MM-DD HH:MM:SS
- 审查范围: ods_parse_log 中 status=SUCCESS 的记录
- 审查记录数: N 条（全量审查）
- 统计: ✅ X 条 | ⚠️ Y 条 | ❌ Z 条 | 🔍 W 条

---

## 一、全局概览

（策略分布、severity 统计等）

---

## 二、逐条审查结果

（Step 2 中每条记录的审查结果）

---

## 三、问题分类汇总

（Step 4 中的分类统计）

---

## 四、需人工确认的记录

| 序号 | table_name | 源文件 | Sheet | 置信度 | 主要问题 |
|------|-----------|--------|-------|--------|---------|

---

## 五、总结与建议

- 总体数据质量评估
- 改进建议
```

4. **用 `write_to_file` 工具将报告写入磁盘**

## 脚本使用说明

### db_reader.py

| 命令 | 用途 | 推荐场景 |
|------|------|---------|
| `full_review` | 全量扫描所有记录 | **Step 1 首选** — 一次命令审查全部 |
| `auto_compare <table>` | 单表深度对比 | **Step 3 深挖** — 仅对有问题的表 |
| `summary` | 全局概览 + 异常检测 | 快速概览（不如 full_review 全面） |
| `batch_compare` | 批量快速扫描 | 按策略筛选时使用 |
| `parse_log` | 完整 parse_log JSON | 需要查看 column_names 等详细信息 |
| `table_info <table>` | 仅 DB 表结构+样本 | 不需要对比 Excel 时 |
| `excel_preview` | 仅 Excel 预览 | 不需要 DB 数据时 |
| `list_tables` | 列出所有业务表 | 全局浏览 |

**典型调用序列**：
```bash
# Step 1: 全量扫描（一条命令搞定所有记录）
python .codebuddy/skills/data-review/scripts/db_reader.py full_review

# Step 3: 对有问题的表深挖（按需调用）
python .codebuddy/skills/data-review/scripts/db_reader.py auto_compare ods_xxx_problem_table
```

## 常见问题与注意事项

1. **路径问题**：`full_review` / `auto_compare` 会自动尝试解析 Excel 路径（绝对路径 → sample_files/ 目录 → 模糊匹配），无需手动指定
2. **数据库连接**：默认从 `data_to_db/config.yaml` 读取，确保 MySQL 服务可用
3. **PowerShell 编码**：不要用 PowerShell 管道处理 JSON 输出，直接读取脚本 stdout
4. **full_review 输出较大**：82 条记录约 3000-5000 行 JSON，可直接在上下文中审查
5. **报告必须落盘**：审查完成但报告未写入文件 = 审查未完成
6. **全量审查**：早期测试阶段必须全量审查，不可抽样跳过
