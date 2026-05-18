---
name: run-parse
description: >
  接收文件夹路径，拉取最新代码，清理旧记录，运行 Excel 数据解析入仓。
  触发词：运行解析、解析文件夹、入仓、入库、run parse、处理数据、parse folder、
  运行 main.py、处理 Excel、数据入仓。
---

# Run Parse Skill — 指定文件夹运行解析入仓

## 目的

接收用户指定的文件夹路径，拉取最新代码，清理该文件夹相关的旧 parse_log 记录，然后运行 `data_to_db/main.py` 对该文件夹下的 Excel 文件执行数据清洗入仓。

## 触发条件

当用户提出以下类型的请求时，激活此技能：
- "运行解析" / "解析这个文件夹" / "处理数据" / "数据入仓"
- "运行 main.py" / "parse 这个目录"
- 任何涉及对指定文件夹执行 Excel → MySQL 入库的请求

## 核心原则

1. **参数必填**：必须明确目标文件夹路径，不能凭空猜测
2. **先拉代码**：运行前必须 `git pull` 确保代码最新
3. **先清记录**：运行前必须删除该文件夹相关的 parse_log 记录，否则已有 SUCCESS 记录的 Sheet 会被跳过
4. **验证结果**：运行后检查入库状态，汇报成功/失败/跳过情况

## 工作流程

### Step 1: 确认目标文件夹

从用户输入中提取目标文件夹路径。支持以下形式：
- 绝对路径：`d:/projects/ExecelDataDeal/待清洗数据`
- 相对路径：`待清洗数据`、`../to_fix_data`、`sample_files`
- 文件夹别名：`to_fix_data`、`sample_files`、`待清洗数据`

**路径解析规则**：
- 如果不是绝对路径，基于项目根目录 `d:/projects/ExecelDataDeal/` 解析
- 确认目录存在且包含 `.xlsx`/`.xls` 文件
- 如果目录不存在或无 Excel 文件，报错停止

**输出**：确认目标文件夹的绝对路径和其中的 Excel 文件数量。

### Step 2: 拉取最新代码

```bash
cd d:/projects/ExecelDataDeal

# 确保在开发分支上工作
git checkout feature_codebuddy_20260508

# 拉取最新代码
git pull origin feature_codebuddy_20260508
```

**冲突处理**：
- 如果 pull 成功无冲突 → 继续
- 如果有合并冲突，按以下步骤处理：
  1. 列出冲突文件：`git diff --name-only --diff-filter=U`
  2. 对每个冲突文件，查看冲突内容：`git diff <file>`
  3. 根据改动性质决定保留策略：
     - **仅远程改了该区域**：保留远程改动
     - **仅本地改了该区域**：保留本地改动
     - **两边都改了同一行**：保留本地改动，同时手动合并远程新增的内容（不丢失任一方的新增逻辑）
     - **无法判断或冲突过于复杂**：停止，提示用户手动处理，给出冲突文件列表和冲突内容
  4. `git add <resolved_files>`
  5. `git commit -m "merge: 解决 git pull 冲突"`
  6. 继续 Step 3

### Step 3: 清理旧 parse_log 记录

**必须删除目标文件夹对应的 parse_log 记录**，否则 `Orchestrator._is_already_parsed()` 会跳过已有 SUCCESS 记录的 Sheet。

使用 `run_parse_helper.py` 脚本：

```bash
cd d:/projects/ExecelDataDeal && python .codebuddy/skills/run-parse/scripts/run_parse_helper.py clean-log --folder "待清洗数据"
```

或手动 SQL：

```sql
-- 删除 source_path 包含目标文件夹名的记录
DELETE FROM ods_parse_log WHERE source_path LIKE '%待清洗数据%';
```

**注意**：
- 必须使用文件夹名作为模糊匹配条件（因为 source_path 存的是相对路径）
- 如果是 `to_fix_data`，同时考虑 DROP 旧表（因为 `mysql_writer.run()` 会自动 DROP+CREATE）
- 如果是 `sample_files` 或 `待清洗数据` 等大目录，**不要** DROP 表，只删 parse_log 记录

**验证清理结果**：
```bash
python .codebuddy/skills/run-parse/scripts/run_parse_helper.py check-log --folder "待清洗数据"
```

### Step 4: 运行 main.py

```bash
cd d:/projects/ExecelDataDeal/data_to_db && python main.py config.yaml "<目标文件夹相对路径>"
```

**示例**：
```bash
# 处理 to_fix_data
cd d:/projects/ExecelDataDeal/data_to_db && python main.py config.yaml ../to_fix_data

# 处理 待清洗数据
cd d:/projects/ExecelDataDeal/data_to_db && python main.py config.yaml "../待清洗数据"

# 处理 sample_files
cd d:/projects/ExecelDataDeal/data_to_db && python main.py config.yaml ../sample_files
```

**关键参数**：
- 第一个参数 `config.yaml`：配置文件
- 第二个参数：覆盖 config 中 `scan.data_dir` 的目标文件夹路径

**运行时长预估**：
- 每个 Excel 文件约需 10-60 秒（取决于 Sheet 数量和 LLM 响应速度）
- 大量文件时可能需要较长时间，请耐心等待

**监控**：
- 日志输出在 `data_to_db/logs/datadeal_YYYYMMDD_HHMMSS.log`
- 可用 `tail -f` 实时查看

### Step 5: 验证入库结果

**使用 helper 脚本快速查看**：

```bash
cd d:/projects/ExecelDataDeal && python .codebuddy/skills/run-parse/scripts/run_parse_helper.py summary --folder "待清洗数据"
```

**或使用 db_reader.py 手动查看**：

```bash
# 总览
cd d:/projects/ExecelDataDeal && python .codebuddy/skills/data-review/scripts/db_reader.py summary

# 全量审查
python .codebuddy/skills/data-review/scripts/db_reader.py full_review
```

**汇报内容**：
1. 处理文件总数
2. 成功(SUCCESS) / 失败(ERROR) / 跳过(SKIP) 各多少
3. 如果有 ERROR，列出错误信息
4. 如果有 SKIP，列出跳过原因

## 常见问题与注意事项

1. **文件名含中文**：路径中有中文时，确保终端编码支持 UTF-8。PowerShell 可能需要 `chcp 65001`
2. **MySQL 连接**：确保 MySQL 服务运行中，`config.yaml` 中配置正确
3. **磁盘空间**：大量文件入库时注意 MySQL 磁盘空间
4. **LLM API**：入仓依赖 LLM API 进行分类和列名翻译，确保 API 可用
5. **不要修改 config.yaml**：仅通过 main.py 的命令行参数覆盖 `data_dir`
6. **唯一键冲突**：`ods_parse_log` 有 `uk_source_sheet_sub` 唯一键，如果清理不彻底可能导致 INSERT 失败。遇到时需手动删除冲突记录
7. **大文件夹处理**：`待清洗数据/` 有 899 个 xlsx 文件，处理时间可能很长。建议先处理少量文件测试，确认无误后再全量处理
