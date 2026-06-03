# 审查报告：慢性病 Sheet 入库不完整修复

**生成时间**: 2026-06-03  
**审查范围**: `粮食数据收集(插补）.xlsx` 的"慢性病"Sheet 横向多分块入库

---

## 1. 问题描述

原始 Excel "慢性病" Sheet 横向包含 9 个独立分块：

| 序号 | 分块名称 | 列范围 |
|------|---------|--------|
| 1 | 全国慢性病患病率 | 0-9 |
| 2 | 城市慢性病患病率 | 10-19 |
| 3 | 农村慢性病患病率 | 20-29 |
| 4 | 15岁及以上东部城市居民慢性病患病率 | 30-39 |
| 5 | 15岁及以上中部城市居民慢性病患病率 | 40-49 |
| 6 | 15岁及以上西部城市居民慢性病患病率 | 50-59 |
| 7 | 15岁及以上东部农村居民慢性病患病率 | 60-69 |
| 8 | 15岁及以上中部农村居民慢性病患病率 | 70-79 |
| 9 | 15岁及以上西部农村居民慢性病患病率 | 80-89 |

**修复前**：数据库只有 `ods_chronic_disease_prevalence_1998_2003_p1`（全国）和 `_p2`（城市），后续 7 个分块完全缺失。

## 2. 根因分析

1. **LLM 横向分区检测只识别到 2 个区域**：该 Sheet 没有空列分隔符，90 列紧密排列，LLM 仅检测到前 2 个块
2. **城市及后续块缺少 year 列**：year 在 col 0（属于全国块），其他块的列范围不包含 col 0
3. **翻译环节列名缺少地区前缀**：LLM 对长中文列名（如"15岁及以上东部城市居民慢性病患病率区域编码"）简化翻译，丢失地区信息

## 3. 修复方案（最小改动）

### 3.1 代码修改

| 文件 | 修改内容 | 影响范围 |
|------|---------|---------|
| `orchestrator.py` | 添加 `_SHEET_OVERRIDES` 和 `_check_sheet_override()`，为慢性病 Sheet 提供 9 个显式 region | 仅影响该 Sheet |
| `orchestrator.py` | 在 `_process_sheet` 中添加 override 分支，跳过 Classifier 直接用指定策略 | 仅 override 命中时生效 |
| `strategy_horizontal_split.py` | `_extract_from_llm_regions` 中添加 `prepend_cols` 支持，将 col 0（年份）拼入非首块 | 通用增强，向后兼容 |
| `worker.py` | 添加 `_apply_subtable_region_prefix()` 后处理，基于 label 补充地区英文前缀 | 仅子表策略且 label 含地区关键词时生效 |

### 3.2 显式 Region 定义

```
Region 1: cols 0-9,   label="全国慢性病患病率"
Region 2: cols 10-19, label="城市慢性病患病率",   prepend_cols=[0]
Region 3: cols 20-29, label="农村慢性病患病率",   prepend_cols=[0]
Region 4: cols 30-39, label="15岁及以上东部城市...", prepend_cols=[0]
Region 5: cols 40-49, label="15岁及以上中部城市...", prepend_cols=[0]
Region 6: cols 50-59, label="15岁及以上西部城市...", prepend_cols=[0]
Region 7: cols 60-69, label="15岁及以上东部农村...", prepend_cols=[0]
Region 8: cols 70-79, label="15岁及以上中部农村...", prepend_cols=[0]
Region 9: cols 80-89, label="15岁及以上西部农村...", prepend_cols=[0]
```

## 4. 修复后验证

### 4.1 ods_parse_log 完整性

| ID | 表名 | subtable_index | subtable_label | status | actual_row_count |
|----|------|---------------|----------------|--------|-----------------|
| 139 | ods_chronic_disease_prevalence_慢性病_p1 | 1 | 全国慢性病患病率 | SUCCESS | 10 |
| 140 | ods_chronic_disease_prevalence_慢性病_p2 | 2 | 城市慢性病患病率 | SUCCESS | 10 |
| 141 | ods_chronic_disease_prevalence_慢性病_p3 | 3 | 农村慢性病患病率 | SUCCESS | 10 |
| 142 | ods_chronic_disease_prevalence_慢性病_p4 | 4 | 15岁及以上东部城市居民慢性病患病率 | SUCCESS | 10 |
| 143 | ods_chronic_disease_prevalence_慢性病_p5 | 5 | 15岁及以上中部城市居民慢性病患病率 | SUCCESS | 10 |
| 144 | ods_chronic_disease_prevalence_慢性病_p6 | 6 | 15岁及以上西部城市居民慢性病患病率 | SUCCESS | 10 |
| 145 | ods_chronic_disease_prevalence_慢性病_p7 | 7 | 15岁及以上东部农村居民慢性病患病率 | SUCCESS | 10 |
| 146 | ods_chronic_disease_prevalence_慢性病_p8 | 8 | 15岁及以上中部农村居民慢性病患病率 | SUCCESS | 10 |
| 147 | ods_chronic_disease_prevalence_慢性病_p9 | 9 | 15岁及以上西部农村居民慢性病患病率 | SUCCESS | 10 |

### 4.2 列注释包含地区信息

| 地区关键词 | 匹配数 | 示例 |
|-----------|--------|------|
| 东部 | 9条 | `east_urban_obesity_rate`: 东部城市肥胖率 |
| 中部 | 18条 | `central_rural_chronic_disease_diabetes_rate`: 中部农村慢性病患病率糖尿病 |
| 西部 | 9+条 | `west_urban_obesity_rate`: 西部城市肥胖率 |
| 农村 | 10+条 | `rural_chronic_disease_region_code`: 农村慢性病患病率区域编码 |
| 城市 | 10+条 | `city_chronic_disease_obesity_rate`: 城市慢性病患病率肥胖率 |

### 4.3 数据正确性对比

| 表 | 年份 | 关键数据值 | 与原始 Excel 一致 |
|----|------|-----------|------------------|
| p1 全国 | 2018 | 342.9, 53.1, 39, 181.4, 22.9, 20, 3.2, 7.8 | ✅ |
| p3 农村 | 2018 | #N/A, 352.1, 38.8, 37.6, 173.1, 26.7, 23.8, 3.5 | ✅ |
| p4 东部城市 | 2018 | #N/A, 328.1, 70, 35.7, 205.6, 16.7, 13.2, 2.6 | ✅ |
| p7 东部农村 | 2018 | #N/A, 338.9, 48.2, 35.1, 190.4, 21, 19.3, 3 | ✅ |
| p9 西部农村 | 2023 | #N/A, 348.4, 46.9, 30.8, 211, 20.4, 17.1, 2.2 | ✅ |

### 4.4 #N/A 和空区域编码保留

- `#N/A` 值：原表中 1998/2003 年的东部/中部/西部数据为 `#N/A`，入库后保留为字符串 `#N/A` ✅
- 空区域编码：原表中区域编码列为空，入库后为 `None` ✅

## 5. 结论

**修复成功**。9 个横向分块全部完整入库，数据与原始 Excel 一致，列注释包含完整的地区信息。`#N/A` 和空值按原样保留。
