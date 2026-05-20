"""Service: name_translate — 调用大模型翻译中文表名/列名为英文"""
from __future__ import annotations

import logging
import re

from services.llm_client import LLMClient, parse_json_response

logger = logging.getLogger("datadeal")


PROMPT_TEMPLATE = """你是一个数据仓库名称翻译专家。请将以下中文表名/列名翻译为简洁的英文缩写，用于 MySQL ODS 层建表。

规则：
1. 表名以 ods_ 开头，必须根据文件路径和Sheet名翻译出有意义的英文表名，不要输出 ods_xxx 这种占位符
2. 使用小写+下划线风格（snake_case）
3. 常见领域术语优先使用缩写：
   - 粮食→grain, 农业→agri, 产量→output, 播种面积→sow_area
   - 省份→province, 城市→city, 县→county, 地区→region
   - 统计年鉴→yearbook, 对照表→ref_map, 价格指数→price_idx
   - 农村→rural, 城镇→urban, 消费→consumption
4. 保留文件路径中的年份范围（如 1990_2021）
5. 保留年鉴章节编号（如 10_1）
6. 长度不超过 60 字符
7. {column_count_rule}
8. **关键**：列名中的数字/年份必须原样保留，不得修改或编造！
   - "1978年" → "1978" 或 "year_1978"，绝不能翻译成其他年份
   - "1984年产量" → "output_1984"，年份1984必须保持不变
   - "15-1" → "15_1"，数字原样保留
9. 列名翻译必须与中文列名提示严格一一对应，不要遗漏或增减
{hint_instruction}
文件路径：{file_path}
Sheet名：{sheet_name}
{preview_section}
请输出 JSON：
{{
  "table_name": "ods_有意义的英文表名",
  "column_names": ["col1_en", "col2_en", ...],
  "column_descriptions": {{"col1_en": "中文列名1", "col2_en": "中文列名2"}},
  "table_description": "一句话描述该表的业务含义",
  "reasoning": "简要说明翻译思路"
}}

要求：
- column_descriptions：每个英文列名对应其原始中文列名含义，用于字段 COMMENT
- table_description：一句话概括该表存储什么数据（如"各地区粮食产量统计表"），用于表 COMMENT"""


def run(file_path: str, sheet_name: str, preview_data: list, column_hints: list = None,
        llm_client: LLMClient = None, max_retries: int = 2, preview_rows: int = 5) -> dict:
    """
    调用大模型翻译表名和列名。

    参数:
        file_path: 文件路径
        sheet_name: Sheet 名称
        preview_data: 预览数据
        column_hints: 可选的列名提示（中文列名）
        llm_client: LLMClient 实例
        max_retries: 最大重试次数
        preview_rows: 传给 LLM 的预览行数（默认5）

    返回:
        {"table_name": str, "column_names": list[str], "reasoning": str}
    """
    # 格式化预览数据
    if column_hints:
        # 有 column_hints 时：只发标题行预览（最多2行），明确告知只翻译中文列名提示
        hint_str = ', '.join(str(c) for c in column_hints)
        column_count_rule = f"column_names 必须恰好 {len(column_hints)} 个元素，与中文列名一一对应"
        hint_instruction = (
            f"10. **重要**：必须严格翻译'中文列名提示'中给出的 {len(column_hints)} 个列名，"
            "不要从数据预览中推断或猜测列名！数据预览仅供参考表名上下文，数据行中的值不是列名。"
        )
        # 只发送前2行预览（标题行/表头行），避免数据行干扰
        preview_lines = []
        for i, row in enumerate(preview_data[:2]):
            cells = [str(v)[:30] if v is not None else "" for v in row[:30]]
            preview_lines.append(f"Row{i}: {', '.join(cells)}")
        preview_str = "\n".join(preview_lines)
        preview_section = (
            f"前{min(len(preview_data), 2)}行预览（仅供参考上下文）：\n{preview_str}\n\n"
            f"中文列名提示（共{len(column_hints)}列，必须翻译这些）：{hint_str}"
        )
    else:
        # 无 column_hints：发送完整预览数据
        column_count_rule = "column_names 数量与数据预览列数对应"
        hint_instruction = ""
        preview_lines = []
        for i, row in enumerate(preview_data[:preview_rows]):
            cells = [str(v)[:30] if v is not None else "" for v in row[:30]]
            preview_lines.append(f"Row{i}: {', '.join(cells)}")
        preview_str = "\n".join(preview_lines)
        preview_section = f"前{min(len(preview_data), preview_rows)}行数据预览：\n{preview_str}"

    prompt = PROMPT_TEMPLATE.format(
        file_path=file_path,
        sheet_name=sheet_name,
        column_count_rule=column_count_rule,
        hint_instruction=hint_instruction,
        preview_section=preview_section,
    )

    last_error = ""
    content = ""
    for attempt in range(max_retries):
        try:
            content = llm_client.chat_with_retry("standard", [{"role": "user", "content": prompt}])
            result = parse_json_response(content)

            if result is not None and result.get("table_name") and result.get("column_names"):
                # MySQL 标识符最长 64 字符，LLM 可能不遵守 ≤60 限制
                tn = result["table_name"]
                if len(tn) > 64:
                    result["table_name"] = tn[:61] + "_end"

                # 校验：确保中文列名中的数字/年份在英文列名中被保留
                if column_hints:
                    before_numeric = list(result["column_names"])
                    result["column_names"] = _fix_numeric_mismatches(
                        column_hints, result["column_names"]
                    )
                    diff_numeric = [
                        (i, before_numeric[i], result["column_names"][i])
                        for i in range(len(before_numeric))
                        if before_numeric[i] != result["column_names"][i]
                    ]
                    if diff_numeric:
                        logger.info(
                            f"    [translate] _fix_numeric_mismatches 修正 {len(diff_numeric)} 列: "
                            f"{diff_numeric[:5]}{'...' if len(diff_numeric) > 5 else ''}"
                        )

                    before_semantic = list(result["column_names"])
                    # 二次校验：对完全偏离中文列名的翻译结果兜底
                    result["column_names"] = _fix_semantic_mismatches(
                        column_hints, result["column_names"]
                    )
                    diff_semantic = [
                        (i, before_semantic[i], result["column_names"][i])
                        for i in range(len(before_semantic))
                        if before_semantic[i] != result["column_names"][i]
                    ]
                    if diff_semantic:
                        logger.info(
                            f"    [translate] _fix_semantic_mismatches 修正 {len(diff_semantic)} 列: "
                            f"{diff_semantic[:5]}{'...' if len(diff_semantic) > 5 else ''}"
                        )

                    # 三次校验：对 LLM 翻译退化为 col_N 模式的列名，用 sanitize 兜底
                    result["column_names"] = _fix_col_n_fallback(
                        column_hints, result["column_names"]
                    )

                # 确保字段存在
                result.setdefault("column_descriptions", {})
                result.setdefault("table_description", "")
                return result

            last_error = f"返回内容不完整: table_name={result.get('table_name')}, column_names={result.get('column_names')}" if result else f"JSON 解析失败: {content[:200]}"

        except Exception as e:
            last_error = f"API 调用异常: {e}"

    # 所有重试失败，返回兜底
    return {
        "table_name": "ods_unknown",
        "column_names": [],
        "column_descriptions": {},
        "table_description": "",
        "reasoning": f"翻译失败({max_retries}次重试): {last_error}",
        "raw_response": content,
    }


def _fix_numeric_mismatches(cn_cols: list, en_cols: list) -> list:
    """
    校验中文列名中的数字/年份在英文列名中是否被保留。
    如果 LLM 翻译时编造了不同的数字，用中文列名中提取的数字替换。

    核心逻辑：
    - 对每个中文列名，提取其中的数字（如 "1978年" → ["1978"]）
    - 如果中文有4位数字（年份）但英文中没有该4位数字 → LLM 翻译完全错误，重新构造
    - 如果中文有其他数字但英文中缺失 → 替换或追加
    """
    if len(cn_cols) != len(en_cols):
        return en_cols

    fixed = list(en_cols)
    for i, (cn, en) in enumerate(zip(cn_cols, en_cols)):
        cn_all_numbers = re.findall(r'\d+', str(cn))
        if not cn_all_numbers:
            continue

        # 检查中文的4位数字（年份）是否在英文中
        cn_year_numbers = [n for n in cn_all_numbers if len(n) == 4]
        en_str = str(en)

        # 如果中文有年份但英文中没有该年份 → LLM 翻译完全错误，重新构造
        if cn_year_numbers:
            year_found = any(yr in en_str for yr in cn_year_numbers)
            if not year_found:
                # 重新构造：翻译文本部分 + 保留数字
                # 去掉中文列名中的数字，翻译文本部分
                cn_text = str(cn)
                for num in cn_all_numbers:
                    cn_text = cn_text.replace(num, '', 1)
                cn_text = cn_text.strip()
                # 用英文列名的文本前缀 + 中文数字
                # 找英文列名中第一个数字之前的部分作为前缀
                prefix_match = re.match(r'^([a-z_]+?)_?\d', en_str)
                if prefix_match:
                    prefix = prefix_match.group(1).rstrip('_')
                else:
                    prefix = _translate_text_part(cn_text)
                # 构造新列名
                new_parts = [prefix] if prefix else []
                for num in cn_all_numbers:
                    new_parts.append(num)
                en_str = '_'.join(new_parts)
                fixed[i] = en_str
                continue

        # 中文有数字但英文中不完整 → 修复
        en_numbers = re.findall(r'\d+', en_str)

        if not en_numbers:
            # 英文中完全没有数字 → 追加中文数字
            for num in cn_all_numbers:
                en_str = en_str.rstrip('_') + '_' + num
        elif len(en_numbers) >= len(cn_all_numbers):
            # 英文数字数量 >= 中文数字数量 → 按顺序替换（LLM 编造了数字）
            en_parts = re.split(r'(\d+)', en_str)
            cn_idx = 0
            new_parts = []
            for part in en_parts:
                if re.match(r'\d+$', part) and cn_idx < len(cn_all_numbers):
                    new_parts.append(cn_all_numbers[cn_idx])
                    cn_idx += 1
                else:
                    new_parts.append(part)
            en_str = ''.join(new_parts)
        else:
            # 英文数字数量 < 中文数字数量 → 追加缺失的数字
            for num in cn_all_numbers:
                if num not in en_str:
                    en_str = en_str.rstrip('_') + '_' + num

        fixed[i] = en_str

    return fixed


# 中文列名常见关键词 → 对应的英文翻译映射
_CN_KEYWORD_MAP = {
    '指标': 'indicator', '单位': 'unit', '项目': 'item', '地区': 'region',
    '类别': 'category', '名称': 'name', '年份': 'year', '产量': 'output',
    '面积': 'area', '数量': 'count', '合计': 'total', '总计': 'total',
    '序号': 'seq', '编号': 'code', '代码': 'code', '月份': 'month',
    '省份': 'province', '城市': 'city', '县': 'county',
    '产品': 'product', '价格': 'price', '成本': 'cost',
    '收入': 'income', '消费': 'consumption', '指数': 'index',
    '比例': 'ratio', '百分比': 'pct', '增长率': 'growth',
    '值': 'value', '数': 'count', '量': 'qty',
}


def _fix_semantic_mismatches(cn_cols: list, en_cols: list) -> list:
    """
    二次校验：检查 LLM 翻译的英文列名是否与中文列名语义匹配。
    如果英文列名完全偏离中文列名（无法从中文推导），用关键词映射兜底。

    判断逻辑：如果中文列名中有可识别的关键词，但英文列名中没有对应的翻译，
    则认为翻译偏离，用关键词映射兜底。
    """
    if len(cn_cols) != len(en_cols):
        return en_cols

    fixed = list(en_cols)
    for i, (cn, en) in enumerate(zip(cn_cols, en_cols)):
        cn_str = str(cn).strip()
        en_str = str(en).strip()

        # 如果英文列名已经包含中文列名中数字 → 跳过（数字校验已修复）
        cn_numbers = re.findall(r'\d+', cn_str)
        if cn_numbers and any(num in en_str for num in cn_numbers):
            continue

        # 如果中文列名中包含可识别的关键词
        # 同时检查原始文本和去除下划线的文本（make_unique_columns 可能插入下划线）
        cn_no_underscore = cn_str.replace('_', '')
        expected_en_parts = []
        for kw, en_kw in _CN_KEYWORD_MAP.items():
            if kw in cn_str or kw in cn_no_underscore:
                expected_en_parts.append(en_kw)

        if not expected_en_parts:
            continue  # 中文列名没有可识别的关键词，跳过校验

        # 检查英文列名中是否包含对应的翻译
        en_lower = en_str.lower()
        found = any(part in en_lower for part in expected_en_parts)
        if found:
            continue  # 语义匹配，OK

        # 语义不匹配 → 翻译偏离，用关键词映射构造新列名
        # 但如果英文列名长度 >= 15 字符且有 >=3 个下划线分隔段（说明 LLM 做了详细翻译），保留
        if len(en_str) >= 15 and en_str.count('_') >= 2:
            continue
        new_parts = list(expected_en_parts)
        # 追加数字（如果有）
        for num in cn_numbers:
            if num not in en_str:
                new_parts.append(num)
        if new_parts:
            fixed[i] = '_'.join(new_parts)

    return fixed


def _fix_col_n_fallback(cn_cols: list, en_cols: list) -> list:
    """
    三次校验：对 LLM 翻译退化为 col_N / col_empty 模式的列名，用 sanitize 兜底。
    
    当 LLM 对中文列名翻译失败时，常返回 col_1, col_2 等占位符。
    此函数检测这些占位符，用 sanitize_column_name 翻译原始中文列名作为兜底。
    """
    from services.mysql_writer import sanitize_column_name

    if len(cn_cols) != len(en_cols):
        return en_cols

    fixed = list(en_cols)
    fixed_count = 0
    for i, (cn, en) in enumerate(zip(cn_cols, en_cols)):
        en_str = str(en).strip()
        # 检测 col_N 或 col_empty 模式
        if re.match(r'^col_\d+$', en_str) or en_str == 'col_empty':
            cn_str = str(cn).strip()
            if cn_str:
                sanitized = sanitize_column_name(cn_str)
                # 只有当 sanitize 结果比 col_N 更好时才替换
                if sanitized and not re.match(r'^col_\d+$', sanitized) and sanitized != 'col_empty':
                    fixed[i] = sanitized
                    fixed_count += 1

    if fixed_count:
        logger.info(
            f"    [translate] _fix_col_n_fallback 修正 {fixed_count} 列 col_N 占位符"
        )

    return fixed


def _translate_text_part(text: str) -> str:
    """简单的中文文本→英文映射（用于数字校验修复时的兜底）"""
    mappings = {
        '指标': 'indicator', '单位': 'unit', '项目': 'item', '地区': 'region',
        '类别': 'category', '名称': 'name', '年份': 'year', '产量': 'output',
        '面积': 'area', '数量': 'count', '金额': 'amount', '比例': 'ratio',
        '百分比': 'pct', '增长率': 'growth_rate', '合计': 'total',
        '总计': 'total', '小计': 'subtotal', '平均值': 'avg',
        '产品': 'product', '价格': 'price', '成本': 'cost',
        '收入': 'income', '支出': 'expenditure', '消费': 'consumption',
        '年': 'year', '月': 'month', '日': 'day',
    }
    text = text.strip()
    if text in mappings:
        return mappings[text]
    # 去除多余空格和标点
    cleaned = re.sub(r'[，。、；：\u201c\u201d\u2018\u2019\uff08\uff09\s]+', '', text)
    if cleaned in mappings:
        return mappings[cleaned]
    return 'col'


    