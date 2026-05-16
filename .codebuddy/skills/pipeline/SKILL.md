---
name: pipeline
description: >
  数据入库 → 审查 → 优化的完整流水线技能。串联 run-parse、data-review、optimize-parse 三个阶段，
  支持用户指定运行范围：入库+审查、审查+优化、或全部运行。
  触发词：流水线、pipeline、全部流程、入库并审查、审查并优化、完整流程、跑一遍。
---

# Pipeline Skill — 入库+审查+优化 一条龙

## 目的

将数据入库(`run-parse`)、审查(`data-review`)、优化(`optimize-parse`)三个阶段串成流水线，用户可指定运行范围：
- **parse+review**: 入库 + 审查（入库完自动审查）
- **review+optimize**: 审查 + 优化（基于已有数据审查并优化）
- **all**: 全部运行（入库 → 审查 → 优化）

## 触发条件

当用户提出以下类型的请求时，激活此技能：
- "流水线运行" / "完整流程" / "跑一遍" / "pipeline"
- "入库并审查" / "解析+review"
- "审查并优化" / "review+优化"
- "入库+审查+优化" / "全流程"
- 任何涉及多阶段组合执行的请求

## 核心原则

1. **阶段可选**：根据用户意图确定运行哪些阶段，不强制全流程
2. **阶段衔接**：前一阶段的输出自动作为后一阶段的输入（如文件夹名、审查报告路径）
3. **阶段独立**：每个阶段复用已有 Skill 的完整逻辑，不简化跳步
4. **失败即停**：任一阶段出现严重错误（如入库全失败），停止后续阶段并汇报

## 阶段识别规则

从用户输入判断运行范围：

| 用户意图 | 运行阶段 | 说明 |
|---------|---------|------|
| 提到"入库"/"解析"/"入仓" + "审查"/"review" | parse → review | 入库后自动审查 |
| 提到"审查"/"review" + "优化"/"fix" | review → optimize | 审查后自动优化 |
| 提到"全部"/"流水线"/"pipeline"/"完整流程"/"跑一遍" | parse → review → optimize | 全流程 |
| 仅提到"入库"/"解析" | parse | 仅入库（不加审查） |
| 仅提到"审查"/"review" | review | 仅审查 |
| 仅提到"优化"/"fix" | optimize | 仅优化 |

**注意**：如果用户只说了"入库+审查"但没指定文件夹，需要追问文件夹路径。

## 流水线工作流

### 阶段判定

首先根据用户意图确定运行哪些阶段，然后按顺序执行。

---

## 阶段 A: 入库 (parse)

**执行条件**：用户意图包含"入库"阶段

**必须参数**：目标文件夹路径

### A1: 确认目标文件夹

从用户输入中提取目标文件夹路径。支持以下形式：
- 绝对路径：`d:/projects/ExecelDataDeal/待清洗数据`
- 相对路径：`待清洗数据`、`../to_fix_data`、`sample_files`
- 文件夹别名：`to_fix_data`、`sample_files`、`待清洗数据`

**路径解析规则**：
- 如果不是绝对路径，基于项目根目录 `d:/projects/ExecelDataDeal/` 解析
- 确认目录存在且包含 `.xlsx`/`.xls` 文件
- 如果目录不存在或无 Excel 文件，报错停止

### A2: 拉取最新代码

```bash
cd d:/projects/ExecelDataDeal

# 确保在开发分支上工作
git checkout feature_codebuddy_20260508

# 拉取最新代码
git pull origin feature_codebuddy_20260508
```

**冲突处理**：如有合并冲突，按以下步骤处理：
1. 列出冲突文件：`git diff --name-only --diff-filter=U`
2. 查看冲突内容：`git diff <file>`
3. 根据改动性质决定保留策略：
   - **仅远程改了该区域**：保留远程改动
   - **仅本地改了该区域**：保留本地改动
   - **两边都改了同一行**：保留本地改动，同时手动合并远程新增的内容
   - **冲突过于复杂**：停止，提示用户手动处理
4. `git add <resolved_files>`
5. `git commit -m "merge: 解决 git pull 冲突"`

### A3: 清理旧 parse_log 记录

```bash
cd d:/projects/ExecelDataDeal && python .codebuddy/skills/run-parse/scripts/run_parse_helper.py clean-log --folder "<文件夹名>"
```

**注意**：
- 如果是 `to_fix_data`，加 `--drop-tables`
- 如果是 `sample_files` 或 `待清洗数据`，**不要** DROP 表

### A4: 运行 main.py

```bash
cd d:/projects/ExecelDataDeal/data_to_db && python main.py config.yaml "<目标文件夹相对路径>"
```

### A5: 验证入库结果

```bash
cd d:/projects/ExecelDataDeal && python .codebuddy/skills/run-parse/scripts/run_parse_helper.py summary --folder "<文件夹名>"
```

**汇报**：处理文件总数、成功/失败/跳过数量。如果有 ERROR，判断是否严重到需要停止后续阶段。

**阶段衔接**：将文件夹名传递给审查阶段（用于筛选相关记录）。

---

## 阶段 B: 审查 (review)

**执行条件**：用户意图包含"审查"阶段

**前置条件**：数据库中有 SUCCESS 记录（来自入库阶段或已有数据）

### B1: 全量扫描

```bash
cd d:/projects/ExecelDataDeal && python .codebuddy/skills/data-review/scripts/db_reader.py full_review
```

输出包含：total、statistics(ok/warn/error)、每条记录的完整审查数据。

### B2: 逐条审查

对 full_review 输出的每条记录进行审查判断，输出：
- 置信度 (0.0~1.0)
- 审查结论: 符合预期 / 部分符合 / 不符合预期 / 需人工确认
- 问题详情

**置信度规则**：
- 0.9~1.0: 完全匹配，无问题
- 0.7~0.89: 小问题（不影响使用）
- 0.5~0.69: 中等问题（行数偏差、部分列遗漏）
- 0.3~0.49: 严重问题（表头识别错误、数据错位）
- 0.0~0.29: 完全不符合（空表、数据完全错误）

### B3: 深度审查（仅针对有问题表）

对 B2 中判定为部分符合或不符合的记录：

```bash
cd d:/projects/ExecelDataDeal && python .codebuddy/skills/data-review/scripts/db_reader.py auto_compare <table_name>
```

### B4: 总结归纳

1. 不符合预期的种类分类（表头识别错误 / 列名翻译 / 行数不匹配 / 合并单元格 / 空行空列 / 策略选择 / 其他）
2. 每种类型：问题描述 + 出现次数 + 典型样例
3. 统计汇总：总审查数 / 各置信度区间分布 / 各问题类型占比

### B5: 写入审查报告（必须！）

确保目录存在 `review_reports/`，生成 `review_reports/review_YYYYMMDD_HHmmss.md`

报告结构：
```markdown
# 落库结果审查报告

- 审查时间: YYYY-MM-DD HH:MM:SS
- 审查范围: ods_parse_log 中 status=SUCCESS 的记录
- 审查记录数: N 条（全量审查）
- 统计: ✅ X 条 | ⚠️ Y 条 | ❌ Z 条 | 🔍 W 条

---

## 一、全局概览

## 二、逐条审查结果

## 三、问题分类汇总

## 四、需人工确认的记录

| 序号 | table_name | 源文件 | Sheet | 置信度 | 主要问题 |
|------|-----------|--------|-------|--------|---------|

## 五、总结与建议
```

**阶段衔接**：将审查报告路径传递给优化阶段。

---

## 阶段 C: 优化 (optimize)

**执行条件**：用户意图包含"优化"阶段

**前置条件**：`review_reports/` 下有审查报告（来自审查阶段或已有报告）

**如果审查阶段发现所有记录都符合预期（全部 ✅）**：跳过优化阶段，告知用户"审查结果全部通过，无需优化"。

### C1: 读取最新审查报告

读取 `review_reports/` 下最新的 `.md` 文件（如果刚完成审查阶段，使用该阶段的报告）。

将问题归纳为可操作的优化项：问题类型和根因、受影响的策略/模块、具体优化方案、代表性文件列表。

### C2: 优化解析策略代码

根据 C1 归纳的优化项，修改相关代码。常见场景：
- **Classifier 分类不准** → 修改 `data_to_db/agents/classifier.py`
- **策略解析逻辑有误** → 修改 `data_to_db/strategies/strategy_xxx.py`
- **数据后处理不足** → 修改策略或 `data_to_db/services/table_layout.py`
- **列名不规范** → 修改 `data_to_db/services/name_translate.py`

每次修改记录：文件路径、修改摘要、期望解决的问题。

### C3: 准备测试数据

```bash
cd d:/projects/ExecelDataDeal && python .codebuddy/skills/optimize-parse/scripts/fix_prepare.py --from-report <审查报告路径> --drop-tables
```

### C4: 运行 main.py 处理 to_fix_data

```bash
cd d:/projects/ExecelDataDeal/data_to_db && python main.py config.yaml ../to_fix_data
```

### C5: 验证落库结果

```bash
cd d:/projects/ExecelDataDeal && python .codebuddy/skills/optimize-parse/scripts/fix_verify.py
```

**判断标准**：
- ✅ 通过：之前的问题已修复，新数据与 Excel 一致
- ⚠️ 部分通过：主要问题修复，但仍有小问题
- ❌ 未通过：问题未修复或引入新问题

### C6: 迭代决策

- **全部通过** → 进入 C7 生成报告
- **部分通过/未通过且轮次 < 3** → 回到 C2 继续优化
- **3 轮后仍有问题** → 进入 C7，在报告中说明遗留问题

### C7: 生成优化报告

写入 `optimize_reports/optimize_YYYYMMDD_HHmmss.md`，包含：
- 问题归纳、优化方案与执行（每轮）、优化效果汇总、遗留问题、代码变更摘要

### C8: 提交代码

```bash
cd d:/projects/ExecelDataDeal

# 确保在开发分支上
git checkout feature_codebuddy_20260508

# 确保有 git 用户配置（新同学首次需设置）
git config user.name "你的名字"
git config user.email "你的邮箱"

# 添加改动
git add data_to_db/ .codebuddy/ optimize_reports/
git status  # 确认变更内容
git commit -m "fix: [详细描述本次优化内容]"

# 推送
git push origin feature_codebuddy_20260508
```

---

## 常见组合场景

### 场景 1: "对 sample_files 入库并审查"

→ 阶段 A (入库 sample_files) + 阶段 B (审查)

### 场景 2: "审查并优化"

→ 阶段 B (审查) + 阶段 C (优化)

### 场景 3: "完整流水线，处理待清洗数据"

→ 阶段 A (入库 待清洗数据) + 阶段 B (审查) + 阶段 C (优化)

### 场景 4: "只审查"

→ 阶段 B (审查)

## 常见问题与注意事项

1. **文件夹参数**：入库阶段必须指定文件夹，如果用户没提供需要追问
2. **阶段间数据传递**：文件夹名和审查报告路径在阶段间自动传递，无需重复输入
3. **失败处理**：入库阶段如果全失败（0 个 SUCCESS），不进入审查阶段；审查阶段如果全部通过，不进入优化阶段
4. **大文件夹警告**：`待清洗数据/` 有 899 个 xlsx 文件，入库可能需要数小时，提醒用户
5. **MySQL 连接**：全流程依赖 MySQL，确保服务可用
6. **LLM API**：入库和优化阶段依赖 LLM API，确保可用
7. **不要修改 config.yaml**：仅通过命令行参数覆盖
