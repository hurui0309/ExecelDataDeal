# 项目数据库与配置参考

## 数据库连接配置

配置文件路径：`data_to_db/config.yaml`

```yaml
database:
  host: "localhost"
  port: 3306
  user: "root"
  password: "123456"
  database: "ods_data"
  charset: "utf8mb4"
```

## 核心表结构

### ods_parse_log — 解析行为日志表

所有 Excel 解析的记录都存储在此表中，是 Review 的主要数据来源。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INT | 自增主键 |
| table_name | VARCHAR(64) | MySQL 实际建表名（唯一键） |
| source_path | VARCHAR(1024) | 源文件路径 |
| source_filename | VARCHAR(1024) | 源文件名 |
| sheet_name | VARCHAR(1024) | Sheet 名称 |
| sheet_index | INT | Sheet 序号 |
| subtable_index | INT | 子表序号（0=非子表） |
| subtable_label | VARCHAR(128) | 子表标签 |
| parse_strategy | VARCHAR(64) | 解析策略名 |
| agent | VARCHAR(32) | 执行 Agent 名 |
| status | VARCHAR(16) | 状态：SUCCESS / SKIP / ERROR / UNKNOWN |
| original_row_count | INT | 原始行数 |
| actual_row_count | INT | 实际写入行数 |
| column_count | INT | 列数 |
| column_names | TEXT | 列名映射 JSON（英文字段名→中文列名） |
| table_description | TEXT | 表描述 |
| error_message | TEXT | 错误信息 |
| file_size_bytes | BIGINT | 文件大小 |
| is_xls | TINYINT(1) | 是否 xls 格式 |
| has_merged_cells | TINYINT(1) | 是否含合并单元格 |
| merged_cells_count | INT | 合并单元格数量 |
| parse_time_ms | INT | 解析耗时 |
| created_at | DATETIME | 创建时间 |

### 业务数据表

每个成功解析的 Excel Sheet 会生成一张 MySQL 表，结构特征：
- `id` INT AUTO_INCREMENT PRIMARY KEY
- 数据列：VARCHAR(512) 或 TEXT（大宽表自动降级）
- `_source_file` VARCHAR(1024) — 源文件路径
- `_sheet_name` VARCHAR(256) — Sheet 名
- `_row_number` INT — 原始行号
- `_created_at` DATETIME — 入仓时间
- `_table_description` TEXT — 表描述

## 解析策略列表

| 策略名 | 说明 | 审查关注点 |
|--------|------|-----------|
| strategy_standard | 标准表格：单行表头，数据整齐 | 行数是否一致 |
| strategy_simple_header | 简单表头 | 是否误识别了多行表头 |
| strategy_multi_header | 多行表头：表头跨多行，需合并 | 表头合并是否正确 |
| strategy_horizontal_split | 横向分区：左右并排多个子表 | 子表拆分边界是否正确 |
| strategy_vertical_subtable | 纵向子表：上下堆叠多个子表 | 子表划分是否遗漏 |
| strategy_merge_fill | 合并单元格填充 | 填充是否完整、有无空行 |
| strategy_paired_row_bilingual | 中英双语配对行 | ⚠️ 列结构是否异常（2列→4列问题） |
| SKIP | 无效数据/纯说明页 | — |
| ERROR | 解析出错 | 错误原因 |

## Excel 文件路径解析

`auto_compare` 命令自动处理 Excel 路径：
1. 先尝试 parse_log 中的 `source_path` 绝对路径
2. 不存在则在 `sample_files/` 目录下查找同名文件
3. 再尝试前缀模糊匹配（取文件名前20字符）

## 已知问题模式

| 模式 | 表现 | 影响策略 |
|------|------|---------|
| 说明页空表 | 数据存入列注释，0行数据 | simple_header |
| paired_row 列结构异常 | 2列Excel→4列DB，数据错位 | paired_row_bilingual |
| 合并单元格拆分空行 | 拆分后部分行全null | merge_fill |
| 纯数字列名 | 年份列直接用数字作列名 | standard |
| "单位说明"大量行 | 行政区划参照表可能有3000+行 | standard |

## 数据根目录

- 配置项：`scan.data_dir`
- 默认值：`../sample_files`（相对于 data_to_db 目录）
- 实际待清洗数据目录：`待清洗数据/`
