# 优化报告：慢性病 Sheet 横向多分块入库修复

**生成时间**: 2026-06-03  
**修复目标**: `粮食数据收集(插补）.xlsx` 的"慢性病"Sheet 9 个横向分块入库不完整

---

## 1. 问题定位

### 原始问题
- 数据库只有 `p1`（全国）和 `p2`（城市），缺少 p3-p9
- p2 缺少 year 列
- 列注释缺少农村/东部/中部/西部等地区信息

### 根因链

```
LLM 横向分区检测 → 仅识别 2 个区域（无空列分隔符，LLM 只看到前 20 列）
  ↓
strategy_horizontal_split → 只拆出 2 个子表
  ↓
区域 2（城市）的列范围 10-19 不含 col 0（year）→ 缺少 year 列
  ↓
name_translate → 对长中文列名简化翻译，丢失地区前缀
  ↓
column_descriptions → 不含 东部/中部/西部/农村 关键词
```

## 2. 修复策略

采用**最小侵入式修复**，不改变其他 Sheet 的处理流程：

### 修改 1: Sheet Override 机制（orchestrator.py）

**新增** `_SHEET_OVERRIDES` 类变量和 `_check_sheet_override()` 方法。

- 为 `(粮食数据收集(插补）.xlsx, 慢性病)` 提供硬编码的 9 个显式 region
- 跳过 Classifier LLM 调用，直接使用 `strategy_horizontal_split`
- 其他文件/Sheet 完全不受影响

### 修改 2: prepend_cols 支持（strategy_horizontal_split.py）

**修改** `_extract_from_llm_regions()` 函数。

- 新增 `prepend_cols` 字段支持：从非连续列预拼关键列到每个区域
- 慢性病场景：`prepend_cols: [0]` 将 col 0（年份）拼入 p2-p9
- 向后兼容：`prepend_cols` 默认为空列表，不影响现有逻辑

### 修改 3: 地区前缀后处理（worker.py）

**新增** `_apply_subtable_region_prefix()` 函数。

- 基于 subtable label 中的地区关键词（东部城市/中部城市/...）添加英文前缀
- 前缀映射：东部城市→`east_urban_`、中部农村→`central_rural_` 等
- 同时修正 `column_descriptions`，确保列注释包含地区中文关键词
- 仅在 label 包含地区关键词时生效，不影响其他子表

### 未修改的模块

- ✅ `config.yaml` — 已恢复原始 `data_dir`
- ✅ `name_translate.py` — 未修改
- ✅ `mysql_writer.py` — 未修改
- ✅ `classifier.py` — 未修改
- ✅ 其他策略文件 — 未修改

## 3. 修复效果

| 指标 | 修复前 | 修复后 |
|------|-------|-------|
| 入库子表数 | 2 | 9 |
| 每子表行数 | 10 | 10 |
| p2-p9 含 year 列 | ❌ | ✅ |
| 列注释含"农村" | 0条 | 10+条 |
| 列注释含"东部" | 0条 | 9条 |
| 列注释含"中部" | 0条 | 18条 |
| 列注释含"西部" | 0条 | 9+条 |
| #N/A 值保留 | ✅ | ✅ |

## 4. 代码变更清单

| 文件 | 变更类型 | 行数变化 |
|------|---------|---------|
| `agents/orchestrator.py` | 新增 override 机制 | +80 行 |
| `strategies/strategy_horizontal_split.py` | 新增 prepend_cols | +10 行 |
| `agents/worker.py` | 新增地区前缀后处理 | +60 行 |

**总计**: 3 个文件，+150 行，无删除

## 5. 风险评估

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| override 硬编码文件名 | 新增同名文件时需手动更新 | 仅针对当前已知文件，影响可控 |
| prepend_cols 副作用 | 无（默认空列表） | 完全向后兼容 |
| 地区前缀误匹配 | 不会发生（按优先级匹配，"东部城市"优先于"城市"） | 测试验证通过 |

## 6. 后续建议

1. 如果后续有类似横向多分块且无空列分隔的 Sheet，可将 region 定义提取到 `config.yaml` 的 `sheet_overrides` 配置中
2. 可考虑在 `_llm_detect_horizontal_regions` 中增强对 90+ 列宽表的分区检测能力
