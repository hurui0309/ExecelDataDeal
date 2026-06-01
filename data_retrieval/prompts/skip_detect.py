"""说明页判断 Prompt 模板"""

SKIP_DETECT_PROMPT = """你是一个 Excel 表格内容识别专家。请判断以下 Sheet 是否为纯说明/目录/备注页（即不包含有效数据表格的页面）。

【文件路径】: {file_path}
【Sheet 名】: {sheet_name}
【Sheet 序号】: {sheet_index}
【总行数】: {max_row}
【总列数】: {max_col}

【前 {preview_rows} 行预览数据】:
{preview_str}

判断标准：
- 纯说明页：全部是文字说明、备注、使用指南、免责声明等，完全没有数据表格结构
- 目录页：列出其他 Sheet 的索引或目录
- 空/无效页：只有标题行没有实际数据，或数据行数极少（≤2行有效数据）
- 有效数据页：包含清晰的表头行和数据行，有统计/数值等信息

请严格以 JSON 格式输出（不要输出其他内容）：
{{
  "is_skip": true,
  "reason": "这是纯说明页，内容为数据使用指南..."
}}

或：
{{
  "is_skip": false,
  "reason": ""
}}"""
