"""语义提取 Prompt 模板"""

SEMANTIC_EXTRACT_PROMPT = """你是一个数据表语义分析专家。请深入分析以下 Excel Sheet 的内容，提取表的语义信息和每个字段的详细描述。

【文件路径】: {file_path}
【文件名】: {file_name}
【Sheet 名】: {sheet_name}
【Sheet 序号】: {sheet_index}
【总行数】: {max_row}
【总列数】: {max_col}
【合并单元格数】: {merged_count}
【文件大小】: {file_size}

【前 {preview_rows} 行预览数据】:
{preview_str}

请仔细分析以上数据，提取以下信息：

1. **表内容描述**：这个表格描述的是什么业务内容/主题？请用一段话（50-200字）概括表的含义、数据的用途和覆盖范围。
2. **分类标签**：这个表属于什么领域/类别？（如：经济/人口/农业/工业/教育/医疗等，可以多个逗号分隔）
3. **关键词**：提取5-10个最能描述这个表内容的关键词
4. **时间范围**：数据覆盖的时间范围（如能识别的话）
5. **地理覆盖**：数据覆盖的地理范围
6. **指标类型**：主要统计指标的性质（金额/数量/比率/指数等）
7. **字段语义**：对每一列（跳过明显的空列和无意义列），给出：
   - 列名（原始文本）
   - 该列的语义描述（50字以内，解释这个字段代表什么）
   - 数据类型（string/number/date/other）
   - 是否是维度字段（如地区、年份、类别等用于分组的字段）
   - 如果是数值列，推测其单位（亿元、万吨、%、人等）
   - 该列的前几个样本值

注意：
- 对于多层表头，请合并理解后给出每列的完整含义
- 如果某列明显是空列或无意义列，可以跳过
- column_index 从 0 开始计数，对应预览数据中的列序号
- 不需要给出 column_names 翻译，只需保留原始中文列名作为 column_name

请严格以 JSON 格式输出（不要输出其他内容）：
{{
  "table_description": "该表记录了1985年全国各地区社会商品零售总额，按地区分类统计，数据来源于统计年鉴...",
  "table_category": "经济,零售,消费",
  "table_keywords": ["1985年", "社会商品零售总额", "地区", "零售额", "消费"],
  "time_range": {{
    "start": "1985",
    "end": "1985",
    "granularity": "year"
  }},
  "geo_coverage": "全国各省/自治区/直辖市",
  "measure_type": "金额",
  "fields": [
    {{
      "column_index": 0,
      "column_name": "地区",
      "semantic_description": "地区名称，表示统计数据的行政区划地域范围",
      "data_type": "string",
      "is_dimension": true,
      "unit": null,
      "sample_values": ["全国总计", "北京", "天津", "上海"]
    }},
    {{
      "column_index": 1,
      "column_name": "社会商品零售总额",
      "semantic_description": "该地区当年的社会商品零售总额",
      "data_type": "number",
      "is_dimension": false,
      "unit": "亿元",
      "sample_values": ["4305.0", "1275.0", "689.0"]
    }}
  ]
}}

其中 time_range 字段说明：
- start: 数据起始年份/日期（字符串格式）
- end: 数据结束年份/日期
- granularity: 时间粒度（year/month/day/unknown）
- 如果无法识别时间范围，将 start 和 end 都设为 null，granularity 设为 "unknown"

字段说明：
- column_index: 列序号（0-based），必须与预览数据中的列序号一致
- column_name: 原始中文列名/表头文本
- semantic_description: 大模型理解的字段语义描述，用于后续检索匹配
- data_type: 数据类型（string/number/date/other）
- is_dimension: 是否为维度字段（用于分组、筛选的字段，如地区、年份、类别）
- unit: 数值字段的单位（如亿元、万吨、%），非数值字段为null
- sample_values: 该列前几个样本值（最多5个），用于验证和辅助理解"""


def build_semantic_prompt(
    file_path: str,
    file_name: str,
    sheet_name: str,
    sheet_index: int,
    max_row: int,
    max_col: int,
    merged_count: int,
    file_size: int,
    preview_data: list,
    preview_rows: int = 20,
) -> str:
    """构建语义提取 Prompt。

    参数:
        file_path: 文件完整路径
        file_name: 文件名
        sheet_name: Sheet 名称
        sheet_index: Sheet 序号（0-based）
        max_row: 总行数
        max_col: 总列数
        merged_count: 合并单元格数
        file_size: 文件大小（字节）
        preview_data: 前 N 行预览数据 list[list]
        preview_rows: 实际预览行数

    返回:
        完整的 prompt 字符串
    """
    # 格式化预览数据
    preview_lines = []
    for i, row in enumerate(preview_data):
        cells = []
        for v in row:
            if v is None:
                cells.append("")
            else:
                s = str(v)
                if len(s) > 40:
                    s = s[:40] + "..."
                cells.append(s)
        preview_lines.append(f"Row{i}: {', '.join(cells)}")
    preview_str = "\n".join(preview_lines)

    return SEMANTIC_EXTRACT_PROMPT.format(
        file_path=file_path,
        file_name=file_name,
        sheet_name=sheet_name,
        sheet_index=sheet_index,
        max_row=max_row,
        max_col=max_col,
        merged_count=merged_count,
        file_size=f"{file_size:,} bytes ({file_size / 1024:.1f} KB)",
        preview_rows=preview_rows,
        preview_str=preview_str,
    )


def build_skip_prompt(
    file_path: str,
    sheet_name: str,
    sheet_index: int,
    max_row: int,
    max_col: int,
    preview_data: list,
    preview_rows: int = 20,
) -> str:
    """构建说明页判断 Prompt。

    参数:
        file_path: 文件完整路径
        sheet_name: Sheet 名称
        sheet_index: Sheet 序号
        max_row: 总行数
        max_col: 总列数
        preview_data: 前 N 行预览数据
        preview_rows: 实际预览行数

    返回:
        完整的 prompt 字符串
    """
    from prompts.skip_detect import SKIP_DETECT_PROMPT

    preview_lines = []
    for i, row in enumerate(preview_data):
        cells = [str(v)[:30] if v is not None else "" for v in row[:15]]
        preview_lines.append(f"Row{i}: {', '.join(cells)}")
    preview_str = "\n".join(preview_lines)

    return SKIP_DETECT_PROMPT.format(
        file_path=file_path,
        sheet_name=sheet_name,
        sheet_index=sheet_index,
        max_row=max_row,
        max_col=max_col,
        preview_rows=preview_rows,
        preview_str=preview_str,
    )
