# 项目规则

## 临时文件管理

所有临时文件（debug、test、cleanup、verify、review 等）**必须**放到 `tmp/` 文件夹下，不得删除，也不得放在其他目录。

### 适用文件命名模式
- `_debug*.py` — 调试脚本
- `_test*.py` — 测试脚本
- `_cleanup*.py` — 清理脚本
- `_verify*.py` — 验证脚本
- `_review*.py` — 审查脚本
- `_excel_read.py` 等临时工具脚本

### 要求
1. 新建临时脚本时，直接保存到 `tmp/` 目录
2. 发现其他目录下的临时脚本，移动到 `tmp/`
3. 临时文件不用删除，保留以便后续参考
4. `tmp/` 目录不提交到 git（已在 `.gitignore` 中排除）
