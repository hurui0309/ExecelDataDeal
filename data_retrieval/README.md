# 数据检索系统

> 基于大模型语义理解的 Excel 数据智能检索系统

---

## 总体架构

```
┌──────────────┐     ┌──────────────────┐     ┌──────────────────┐
│  第一阶段     │     │   第二阶段        │     │   第三阶段         │
│  语义提取     │ ──→ │   归纳汇总        │ ──→ │   智能检索         │
│  (离线批处理) │     │   (离线聚合)      │     │   (在线查询)       │
└──────────────┘     └──────────────────┘     └──────────────────┘
```

## 第一阶段：语义提取

逐 Excel/Sheet 使用 LLM 提取语义元数据，存入 `ods_sheet_metadata`。

**运行**：
```bash
cd data_retrieval
python phase1_extract.py phase1_config.yaml
```

**状态**：✅ 已实现

---

## 第二阶段：归纳汇总

基于 `ods_sheet_metadata` 构建分类索引、关键词倒排索引、字段同义词映射。

### 增量更新机制

`ods_sheet_metadata` 持续有第一阶段写入新数据。第二阶段通过 `ods_index_state` 表记录断点：

- 首次运行：全量处理所有 SUCCESS 记录（11,881 条）
- 后续运行：仅处理 `id > last_indexed_id` 的新增记录
- 索引表使用 `ON DUPLICATE KEY UPDATE` 合并新旧数据
- 设置 `incremental: false` 可强制重建全量索引

### 索引表

| 表名 | 用途 |
|------|------|
| `ods_index_state` | 索引构建断点状态 |
| `ods_category_index` | 按领域分类的多级索引树 |
| `ods_keyword_index` | 关键词→Sheet ID 倒排索引 |
| `ods_field_synonym` | LLM 发现的字段同义词映射 |

**运行**：
```bash
cd data_retrieval
python phase2_index.py phase2_config.yaml
```

**配置项**：
```yaml
index:
  incremental: true          # 增量模式
  discover_synonyms: true    # 是否发现同义词（首次建议开启）
  synonym_concurrency: 2     # 同义词发现并发数
  synonym_batch_size: 50     # 每批处理字段数
```

**状态**：✅ 已实现

---

## 第三阶段：智能检索

用户输入自然语言，系统自动匹配最相关的表及字段。

### 检索流程

```
用户输入: "帮我查1985年各地区社会商品零售总额"
    │
    ├─ Step 1: 意图解析 (LLM) → 时间/地理/指标/关键词
    ├─ Step 2: 关键词召回 → ods_keyword_index 倒排索引
    ├─ Step 3: 分类召回 → ods_category_index 分类过滤
    ├─ Step 4: 描述召回 → table_description LIKE 模糊匹配
    ├─ Step 5: 字段匹配 → fields_json 字段语义匹配 + 同义词
    └─ Step 6: LLM 重排序 → 相关性评分 (候选>3时启用)
```

### 使用方式

**CLI 检索**：
```bash
python phase3_search.py "帮我查1985年各地区社会商品零售总额"
```

**API 服务**：
```bash
python search_api.py --port 5100

# 接口
POST /api/search        # {"query": "...", "top_k": 5}
GET  /api/categories    # 浏览分类索引
GET  /api/keywords?q=   # 关键词搜索提示
GET  /api/synonyms?q=   # 同义词查询
GET  /api/health        # 健康检查
```

**状态**：✅ 已实现

---

## 文件结构

```
data_retrieval/
├── phase1_extract.py         # 第一阶段：语义提取
├── phase1_config.yaml        # 第一阶段配置
├── phase2_index.py           # 第二阶段：索引构建
├── phase2_config.yaml        # 第二阶段配置
├── phase3_search.py          # 第三阶段：智能检索
├── search_api.py             # Flask API 服务
├── prompts/
│   ├── semantic_extract.py   # 语义提取 Prompt
│   └── skip_detect.py        # 说明页判断 Prompt
└── logs/                     # 日志目录
```

---

## 数据库表清单

| 表名 | 用途 | 阶段 |
|------|------|------|
| `ods_sheet_metadata` | Sheet 语义元数据 | 第一阶段 |
| `ods_index_state` | 索引断点状态 | 第二阶段 |
| `ods_category_index` | 分类索引树 | 第二阶段 |
| `ods_keyword_index` | 关键词倒排索引 | 第二阶段 |
| `ods_field_synonym` | 字段同义词映射 | 第二阶段 |
