# 数据检索系统参考

## 数据库连接

```
host: localhost:3306
database: ods_data
user: root / password: 123456
```

## 核心表

| 表名 | 用途 | 关键字段 |
|------|------|----------|
| `ods_sheet_metadata` | Sheet 语义元数据 | `file_path`, `sheet_name`, `table_description`, `table_category`, `table_keywords`, `fields_json`, `time_range_start`, `geo_coverage` |
| `ods_category_index` | 分类索引 | `category_path`, `sheet_ids`, `sheet_count` |
| `ods_keyword_index` | 关键词倒排索引 | `keyword`, `keyword_norm`, `sheet_ids`, `doc_freq` |
| `ods_field_synonym` | 字段同义词映射 | `canonical_name`, `synonyms`, `confidence` |

## 检索流程

```
用户输入 → LLM意图解析
    ├→ 关键词召回 (ods_keyword_index 倒排索引)
    ├→ 分类召回 (ods_category_index 分类匹配)
    ├→ 描述召回 (table_description LIKE 模糊匹配)
    ├→ 字段匹配 (fields_json + ods_field_synonym 同义词)
    └→ LLM重排序 (候选>3时启用)
```

## 搜索结果 JSON 格式

```json
{
  "rank": 1,
  "score": 0.95,
  "file_name": "xxx.xlsx",
  "sheet_name": "Sheet1",
  "file_path": "待清洗数据/...",
  "table_category": "经济/零售",
  "table_description": "该表记录了...",
  "matched_fields": [
    {"name": "地区", "description": "地区名称", "data_type": "string", "unit": null},
    {"name": "零售总额", "description": "社会商品零售总额", "data_type": "number", "unit": "亿元"}
  ],
  "all_fields": [...],
  "match_reason": "keyword | LLM: 高度匹配"
}
```

## 报告输出目录

```
data_retrieval/search_results/
```

命名: `YYYYMMDD_批次号_查询摘要.md`
