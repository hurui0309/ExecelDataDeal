---
name: optimize-parse
description: >
  基于审查报告优化解析策略的技能。读取最新 review 报告，归纳问题，优化策略代码，
  重新处理问题文件，验证落库结果，迭代优化直至通过（最多 3 轮），生成优化报告并提交代码。
  触发词：优化解析、优化策略、fix 解析、修复问题、optimize、优化代码、修复落库问题。
---

# Optimize Parse Skill — 解析策略优化

## 目的

基于审查报告发现的数据质量问题，自动优化解析策略代码，重新处理问题文件并验证结果，形成 "发现问题 → 优化代码 → 重新入库 → 验证结果" 的闭环。

## 触发条件

当用户提出以下类型的请求时，激活此技能：
- "优化解析策略" / "修复落库问题" / "优化代码"
- "根据 review 报告优化" / "fix 解析"
- 任何涉及根据审查结果改进解析代码的请求

## 核心原则

1. **报告驱动**：必须基于 `review_reports/` 下最新报告中的问题，不要凭空优化
2. **最小改动**：只修改与问题直接相关的代码，不做无关重构
3. **验证闭环**：每次优化后必须重新入库并验证结果
4. **最多 3 轮**：如果 3 轮后仍有问题，在报告中明确说明遗留问题
5. **记录完整**：每轮优化的方案、改动文件、效果必须记录

## 工作流程

### Step 1: 读取最新审查报告

读取 `review_reports/` 下最新的 `.md` 文件，重点关注：
- "问题分类汇总" 部分：每类问题的描述、根因、典型样例
- "需人工确认的记录" 表格：具体的 table_name 和问题
- "总结与建议" 部分：优先级排序的改进建议

**将问题归纳为可操作的优化项**，每个优化项包含：
- 问题类型和根因
- 受影响的策略/模块
- 具体优化方案（改哪个文件、改什么逻辑）
- 代表性文件列表（用于 Step 3 测试）

### Step 2: 优化解析策略代码

根据 Step 1 归纳的优化项，修改相关代码。**常见优化场景**：

#### 场景 A: Classifier 分类不准（如"必看说明"应 SKIP 但入库）

**修改文件**: `data_to_db/agents/classifier.py` 中的 `_STRATEGY_LIST_PROMPT`

优化方式：
1. 在 SKIP 的判断要点中增加新的识别规则
2. 在判断要点列表中增加新的优先匹配条件
3. 在 "附加信息" 中补充更多上下文

#### 场景 B: 策略解析逻辑有误（如 paired_row 对目录表不适合）

**修改文件**: `data_to_db/strategies/strategy_xxx.py`

优化方式：
1. 修改策略的 `run()` 函数逻辑
2. 增加策略 fallback 机制：当检测到不适用场景时，返回 `{"action": "fallback", "to": "strategy_standard"}`
3. 增加数据后处理（如过滤全 null 行、合并单元格拆分后的空行清理）

#### 场景 C: 数据后处理不足（如分组标题行误入库、全 null 行未过滤）

**修改文件**: `data_to_db/strategies/strategy_xxx.py` 或 `data_to_db/services/table_layout.py`

优化方式：
1. 在策略的 `run()` 返回结果前增加过滤逻辑
2. 在 `table_layout.py` 中增加通用过滤函数

#### 场景 D: 列名不规范（如纯数字列名）

**修改文件**: `data_to_db/services/name_translate.py`

优化方式：
1. 在列名翻译流程中增加数字列名的特殊处理
2. 在 sanitize 阶段加前缀（如 `y_1978`）

**每次修改代码后，记录**：
- 修改的文件路径
- 修改的内容摘要
- 期望解决的问题

### Step 3: 准备测试数据

**推荐方式**：使用 `fix_prepare.py` 一键完成清空+复制+清理：

```bash
# 从 review 报告自动提取问题文件
cd d:/projects/ExecelDataDeal && python .codebuddy/skills/optimize-parse/scripts/fix_prepare.py --from-report review_reports/review_XXXXXXXX_XXXXXX.md --drop-tables

# 或手动指定文件名（支持模糊匹配）
python .codebuddy/skills/optimize-parse/scripts/fix_prepare.py --files "粮食播种" "统计年鉴" --drop-tables
```

`--drop-tables` 会同时 DROP 旧的 DB 表（`mysql_writer.run()` 会自动 DROP+CREATE，不加也可以）。

**手动方式**（如需更精细控制）：

1. **清空 `to_fix_data/` 目录**：
   ```bash
   Remove-Item -Path "d:/projects/ExecelDataDeal/to_fix_data/*" -Recurse -Force
   ```

2. **复制代表性文件到 `to_fix_data/`**：
   - 从 `sample_files/` 中找到 Step 1 中问题记录对应的源文件
   - 使用 `db_reader.py parse_log` 或 `summary` 获取 `source_filename`
   - 复制到 `to_fix_data/`

   **注意**：一个 Excel 文件可能有多个 Sheet，其中只有部分 Sheet 有问题，但 main.py 会处理文件的所有 Sheet，这是正常的——其他 Sheet 因为已有 SUCCESS 记录会被自动跳过。

3. **清理旧 parse_log 记录**：
   ```bash
   python .codebuddy/skills/optimize-parse/scripts/fix_prepare.py --clean-log-only --files "任意值"
   ```
   或直接 SQL：
   ```sql
   DELETE FROM ods_parse_log WHERE source_path LIKE '%to_fix_data%';
   ```

4. **验证文件就位**：
   ```bash
   ls d:/projects/ExecelDataDeal/to_fix_data/
   ```

### Step 5: 运行 main.py 处理 to_fix_data

```bash
cd d:/projects/ExecelDataDeal/data_to_db && python main.py config.yaml ../to_fix_data
```

**关键参数**：
- 第一个参数 `config.yaml`：配置文件
- 第二个参数 `../to_fix_data`：覆盖 config 中的 `data_dir`，只处理 `to_fix_data/` 下的文件

**运行后检查**：
- 查看日志输出，确认是否有 ERROR
- 关注 LLM 分类结果和策略选择是否改变
- 记录处理耗时

### Step 6: 验证落库结果

**推荐方式**：使用 `fix_verify.py` 一键验证所有 to_fix_data 入库记录：

```bash
cd d:/projects/ExecelDataDeal && python .codebuddy/skills/optimize-parse/scripts/fix_verify.py
# 详细模式
python .codebuddy/skills/optimize-parse/scripts/fix_verify.py --detail
```

输出包含：状态分布、逐条 auto_compare 对比、severity 分级（✅/⚠️/❌）、验证汇总。

**手动方式**（对特定表深挖）：

```bash
# 查看新入库的记录
cd d:/projects/ExecelDataDeal && python .codebuddy/skills/data-review/scripts/db_reader.py summary

# 对特定表进行深度对比
python .codebuddy/skills/data-review/scripts/db_reader.py auto_compare <table_name>
```

**验证维度**：
1. **之前的错误是否已修复**：
   - 空表 → 现在是否 SKIP 或有数据？
   - 列结构异常 → 现在列数是否正确？
   - 全 null 行 → 现在是否被过滤？
   - 纯数字列名 → 现在是否有前缀？
2. **数据准确性**：DB 样本数据是否与 Excel 原始数据一致
3. **无回退**：原来正确的表是否仍正确（未被优化影响）

**判断标准**：
- ✅ 通过：之前的问题已修复，新数据与 Excel 一致
- ⚠️ 部分通过：主要问题修复，但仍有小问题
- ❌ 未通过：问题未修复或引入新问题

### Step 7: 迭代决策

- **全部通过** → 进入 Step 8 生成报告
- **部分通过/未通过且轮次 < 3** → 回到 Step 2 继续优化，并在报告中记录本轮结果
- **3 轮后仍有问题** → 进入 Step 8，在报告中明确说明遗留问题

### Step 8: 生成优化报告

将报告写入 `optimize_reports/optimize_YYYYMMDD_HHmmss.md`，确保目录存在：

```markdown
# 解析策略优化报告

- 优化时间: YYYY-MM-DD HH:MM:SS
- 基于审查报告: review_reports/review_XXXXXXXX_XXXXXX.md
- 优化轮次: N 轮
- 优化结果: ✅ 全部通过 / ⚠️ 部分通过 / ❌ 未通过

---

## 一、问题归纳

（从审查报告中提取的问题类型、根因、受影响策略）

---

## 二、优化方案与执行

### 第 1 轮

**优化方案**：
- [方案1描述]
- [方案2描述]

**修改文件**：
- `data_to_db/xxx.py`: [修改内容摘要]
- `data_to_db/yyy.py`: [修改内容摘要]

**测试文件**：
- [文件名1.xlsx] → 对应表 [ods_xxx]
- [文件名2.xlsx] → 对应表 [ods_yyy]

**执行结果**：
- 处理文件数: N
- 成功: M, 失败: K, 跳过: J
- 耗时: Xms

**验证结果**：
| 表名 | 之前问题 | 优化后状态 | 验证结论 |
|------|---------|-----------|---------|

### 第 2 轮（如有）
...

---

## 三、优化效果汇总

| 问题类型 | 修复前 | 修复后 | 状态 |
|---------|--------|--------|------|
| [问题1] | [修复前描述] | [修复后描述] | ✅/⚠️/❌ |

---

## 四、遗留问题

（3 轮后仍未解决的问题，如有）

---

## 五、代码变更摘要

（所有修改的文件和关键改动点）
```

### Step 9: 提交代码

**仅提交 `data_to_db/` 和 `.codebuddy/` 目录下的变更**，其他文件夹（如 `to_fix_data/`、`sample_files/`、`待清洗数据/`）不提交。

```bash
cd d:/projects/ExecelDataDeal

# 确保在开发分支上
git checkout feature_codebuddy_20260508

# 推送前先拉取最新代码，避免冲突
git pull origin feature_codebuddy_20260508

# 确保有 git 用户配置（新同学首次需设置）
git config user.name "你的名字"
git config user.email "你的邮箱"

git add data_to_db/ .codebuddy/ optimize_reports/
git status  # 确认变更内容
git commit -m "详细描述本次优化的内容"

# 推送
git push origin feature_codebuddy_20260508
```

**冲突处理**：如果 `git pull` 时有合并冲突：
1. 列出冲突文件：`git diff --name-only --diff-filter=U`
2. 查看冲突内容：`git diff <file>`
3. 根据改动性质决定保留策略：
   - **仅远程改了该区域**：保留远程改动
   - **仅本地改了该区域**：保留本地改动
   - **两边都改了同一行**：保留本地改动，同时手动合并远程新增的内容
   - **冲突过于复杂**：停止，提示用户手动处理
4. `git add <resolved_files>`
5. `git commit -m "merge: 解决 git pull 冲突"`
6. 继续 push

**commit message 格式**：
```
fix: [简要描述主要修复的问题]

- 优化1: [具体描述]
- 优化2: [具体描述]
- 修改文件: [列出关键修改文件]
- 优化效果: [如"4个空表问题已修复，paired_row策略增加fallback"]

Based on review report: review_XXXXXXXX_XXXXXX.md
Optimize report: optimize_reports/optimize_XXXXXXXX_XXXXXX.md
```

## 常见问题与注意事项

1. **to_fix_data 文件选择**：优先选择直接体现问题的文件。一个文件可能覆盖多个问题（如同一个 Excel 的"必看说明"和"原始数据"两个 Sheet）
2. **parse_log 去重机制**：Orchestrator 只跳过 `status=SUCCESS` 的记录。删除旧记录后，即使同名表已存在，`mysql_writer.run()` 会先 DROP 再 CREATE，所以不会冲突
3. **策略 fallback**：策略可以通过返回 `{"action": "fallback", "to": "strategy_xxx"}` 自动切换到另一策略（最多 1 跳），这是安全的降级机制
4. **LLM 非确定性**：Classifier 和 name_translate 都调用 LLM，结果可能有波动。如果优化后结果不一致，可多次运行观察
5. **不要修改 config.yaml 中的数据库配置**：确保 MySQL 服务可用
6. **optimize_reports 目录**：如果不存在需创建 `mkdir -p optimize_reports`
7. **不要删除 review_reports**：优化报告引用审查报告，两者都需要保留
