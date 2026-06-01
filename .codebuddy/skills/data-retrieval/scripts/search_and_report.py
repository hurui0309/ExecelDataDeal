"""数据检索报告生成脚本

用法:
    python search_and_report.py <查询文本>
    python search_and_report.py "帮我查1985年各地区社会商品零售总额"

输出:
    data_retrieval/search_results/YYYYMMDD_批次号_摘要.md
"""

from __future__ import annotations

import json
import os
import sys
import re
import time
import logging
from datetime import datetime
from pathlib import Path

# 设置路径
_SKILL_DIR = Path(__file__).resolve().parent.parent
_PROJ_ROOT = _SKILL_DIR.parent.parent.parent
_DATA_RETRIEVAL = _PROJ_ROOT / "data_retrieval"
_SEARCH_RESULTS = _DATA_RETRIEVAL / "search_results"

sys.path.insert(0, str(_DATA_RETRIEVAL))
sys.path.insert(0, str(_PROJ_ROOT / "data_to_db"))

from phase3_search import Phase3Search, load_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("search_report")


def get_next_batch_number(results_dir: Path, date_prefix: str) -> int:
    """获取当日下一个批号"""
    results_dir.mkdir(parents=True, exist_ok=True)
    existing = list(results_dir.glob(f"{date_prefix}_*.md"))
    max_batch = 0
    for f in existing:
        m = re.match(rf"{date_prefix}_(\d+)_", f.name)
        if m:
            max_batch = max(max_batch, int(m.group(1)))
    return max_batch + 1


def sanitize_filename(text: str, max_len: int = 30) -> str:
    """从查询文本生成安全的文件名片段"""
    # 提取中文和英文关键词
    cleaned = re.sub(r'[^\w\u4e00-\u9fff]', '', text)
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len]
    return cleaned or "search"


def format_result_md(results: list, query: str) -> str:
    """格式化检索结果为 Markdown"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if not results:
        return f"""# 数据检索报告

> 查询时间: {now}
> 查询内容: {query}

---

## 检索结果

❌ **未找到匹配的数据表。** 请尝试：
- 使用更通用的关键词
- 检查数据是否已入库解析
- 确认 `ods_sheet_metadata` 和索引表已构建
"""

    lines = [
        f"# 数据检索报告",
        f"",
        f"> 查询时间: {now}",
        f"> 查询内容: {query}",
        f"> 匹配结果: {len(results)} 个数据表",
        f"",
        f"---",
        f"",
        f"## 检索结果总览",
        f"",
        f"| # | 评分 | 文件名 | Sheet | 分类 |",
        f"|---|---|---|---|---|",
    ]

    for i, r in enumerate(results):
        score = f"{r.get('score', 0):.2f}"
        fname = r.get("file_name", "")[:40]
        sname = r.get("sheet_name", "")[:30]
        cat = r.get("table_category", "")[:25]
        lines.append(f"| {i + 1} | {score} | {fname} | {sname} | {cat} |")

    lines.extend([
        "",
        "---",
        "",
        "## 各表详情",
        "",
    ])

    for i, r in enumerate(results):
        score = r.get("score", 0)
        score_bar = "█" * int(score * 10) + "░" * (10 - int(score * 10))

        lines.append(f"### #{i + 1} `{r.get('file_name', '')}` / `{r.get('sheet_name', '')}`")
        lines.append(f"")
        lines.append(f"| 属性 | 值 |")
        lines.append(f"|---|---|")
        lines.append(f"| 匹配评分 | {score:.2f} {score_bar} |")
        lines.append(f"| 文件路径 | `{r.get('file_path', '')}` |")
        lines.append(f"| Sheet 序号 | {r.get('sheet_index', '')} |")
        lines.append(f"| 分类 | {r.get('table_category', '未知')} |")
        lines.append(f"| 匹配方式 | {r.get('match_reason', '')} |")
        lines.append(f"| 表描述 | {r.get('table_description', '无')[:200]} |")
        lines.append(f"")

        matched_fields = r.get("matched_fields", [])
        if matched_fields:
            lines.append(f"#### 匹配字段")
            lines.append(f"")
            lines.append(f"| 字段名 | 语义描述 | 数据类型 | 单位 |")
            lines.append(f"|---|---|---|---|")
            for f in matched_fields[:10]:
                name = f.get("name", "")
                desc = f.get("description", "")[:60]
                dtype = f.get("data_type", "")
                unit = f.get("unit", "") or "-"
                lines.append(f"| {name} | {desc} | {dtype} | {unit} |")
            lines.append(f"")

        all_fields = r.get("all_fields", [])
        if all_fields and len(all_fields) > len(matched_fields):
            lines.append(f"<details>")
            lines.append(f"<summary>📋 全部字段 ({len(all_fields)} 列)</summary>")
            lines.append(f"")
            lines.append(f"| # | 字段名 | 语义描述 | 类型 |")
            lines.append(f"|---|---|---|---|")
            for j, f in enumerate(all_fields):
                name = f.get("column_name", f.get("name", ""))
                desc = f.get("semantic_description", f.get("description", ""))[:60]
                dtype = f.get("data_type", "")
                lines.append(f"| {j + 1} | {name} | {desc} | {dtype} |")
            lines.append(f"")
            lines.append(f"</details>")
            lines.append(f"")

        lines.append(f"---")
        lines.append(f"")

    lines.append(f"*报告由数据检索系统自动生成*")
    return "\n".join(lines)


def main():
    query = " ".join(sys.argv[1:]).strip() if len(sys.argv) > 1 else ""
    if not query:
        query = input("请输入数据需求查询: ").strip()
        if not query:
            print("错误: 查询内容不能为空")
            sys.exit(1)

    logger.info(f"查询: {query}")

    # 初始化搜索引擎
    config_path = _DATA_RETRIEVAL / "phase2_config.yaml"
    config = load_config(str(config_path))
    engine = Phase3Search(db_config=config["database"], llm_config=config.get("llm"))

    # 执行检索
    t0 = time.time()
    search_results = engine.search(query, top_k=10)
    elapsed = time.time() - t0
    logger.info(f"检索完成: {len(search_results)} 条结果 (耗时 {elapsed:.1f}s)")

    # 格式化结果
    json_results = engine.format_results_json(search_results)

    # 生成文件名
    date_str = datetime.now().strftime("%Y%m%d")
    batch_no = get_next_batch_number(_SEARCH_RESULTS, date_str)
    safe_query = sanitize_filename(query)
    filename = f"{date_str}_{batch_no:03d}_{safe_query}.md"
    output_path = _SEARCH_RESULTS / filename

    # 写入 MD
    md_content = format_result_md(json_results, query)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(md_content, encoding="utf-8")

    # 同时写 JSON（供程序读取）
    json_path = _SEARCH_RESULTS / f"{date_str}_{batch_no:03d}.json"
    json_path.write_text(
        json.dumps({
            "query": query,
            "timestamp": datetime.now().isoformat(),
            "took_ms": int(elapsed * 1000),
            "total": len(json_results),
            "results": json_results,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 输出结论
    print(f"\n[OK] 检索完成: {len(json_results)} 条结果")
    print(f"[MD] Markdown 报告: {output_path}")
    print(f"[JSON] JSON 数据:   {json_path}")
    print(f"[TIME] 耗时:        {elapsed:.1f}s")

    # 简要预览
    if json_results:
        print(f"\n--- 匹配结果预览 ---")
        for i, r in enumerate(json_results[:5]):
            score = r.get("score", 0)
            bar = "#" * int(score * 10)
            print(f"  {i + 1}. [{bar:10s}] {r['file_name']} / {r['sheet_name']} (评分: {score:.2f})")


if __name__ == "__main__":
    main()
