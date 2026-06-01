"""第三阶段：智能检索 — 自然语言 → 表+字段匹配

检索流程：
  1. 意图解析 (LLM)：从用户输入中提取 时间/地理/指标/主题 等意图要素
  2. 关键词匹配：在 ods_keyword_index 中查倒排索引
  3. 分类匹配：在 ods_category_index 中按分类过滤
  4. 语义描述检索：在 ods_sheet_metadata 的 table_description 中模糊匹配
  5. 字段级匹配：在 fields_json 中匹配字段语义描述
  6. 结果排序 & 输出

支持两种调用方式：
  - CLI: python phase3_search.py "帮我查1985年各地区社会商品零售总额"
  - API: 通过 search_api.py 的 Flask 接口
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

import pymysql

_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA_TO_DB = os.path.join(_PROJ_ROOT, "data_to_db")
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)
if _DATA_TO_DB not in sys.path:
    sys.path.insert(0, _DATA_TO_DB)

from services.llm_client import LLMClient, parse_json_response

logger = logging.getLogger("phase3_search")


# ────────────────────────── 意图解析 Prompt ──────────────────────────

INTENT_PARSE_PROMPT = """你是一个数据检索意图解析专家。用户想要从数据仓库中查询数据，请解析用户的自然语言诉求。

用户输入: "{query}"

请提取以下结构化信息：
1. 时间：用户关心的年份/时间段
2. 地理：用户关心的地理范围
3. 指标：用户想查询的核心指标/数据内容
4. 主题类别：属于什么领域
5. 关键词：用于检索的关键词列表

请严格以 JSON 格式输出（不要输出其他内容）：
{{
  "time_filter": {{"start": "1985", "end": "1985", "keyword": "1985年"}},
  "geo_filter": {{"level": "province", "keyword": "各地"}},
  "core_indicator": "社会商品零售总额",
  "topic_category": "经济/零售",
  "search_keywords": ["社会商品零售总额", "零售额", "1985", "各地区"],
  "search_description": "1985年各地区社会商品零售总额统计"
}}

注意：
- 如果某个维度无法提取，设为 null
- search_keywords 按重要性排序，最多 8 个
- search_description 是对用户诉求的一句话概括，用于语义检索"""


# ────────────────────────── 结果重排序 Prompt ──────────────────────────

RESCORE_PROMPT = """你是一个数据检索结果评估专家。用户想执行以下查询：

用户诉求: "{query}"

以下是检索到的候选数据表及其描述，请评估每个表与用户查询的相关性，给出 0-1 的相关性评分。

候选表:
{candidates}

请严格以 JSON 格式输出（不要输出其他内容）：
{{
  "rankings": [
    {{"index": 0, "score": 0.95, "reason": "..."}},
    {{"index": 1, "score": 0.60, "reason": "..."}}
  ]
}}"""


# ────────────────────────── 数据结构 ──────────────────────────

@dataclass
class SearchResult:
    table_name: str = ""
    file_path: str = ""
    file_name: str = ""
    sheet_name: str = ""
    sheet_index: int = 0
    table_description: str = ""
    table_category: str = ""
    matched_fields: list = field(default_factory=list)
    all_fields: list = field(default_factory=list)
    match_score: float = 0.0
    match_reason: str = ""
    metadata_id: int = 0


# ────────────────────────── Phase3Search ──────────────────────────

class Phase3Search:
    """第三阶段：智能检索引擎"""

    def __init__(self, db_config: dict, llm_config: dict = None):
        self.db_config = db_config
        self.llm_client = LLMClient({"llm": llm_config}) if llm_config else None
        self.max_results = 10
        self.min_score = 0.3

    def _get_connection(self):
        return pymysql.connect(**self.db_config)

    # ──────────────────── 主入口 ────────────────────

    def search(self, query: str, top_k: int = 10) -> list[SearchResult]:
        """
        智能检索主入口

        参数:
            query: 用户自然语言查询
            top_k: 返回结果数

        返回:
            SearchResult 列表（按相关性降序）
        """
        t_start = time.time()
        self.max_results = top_k

        # Step 1: 意图解析
        intent = self._parse_intent(query)
        logger.info(f"意图解析: {intent}")

        # Step 2-4: 多路召回
        candidates: dict[int, SearchResult] = {}
        conn = self._get_connection()
        try:
            self._keyword_recall(conn, intent, candidates)
            self._category_recall(conn, intent, candidates)
            self._description_recall(conn, intent, candidates)
        finally:
            conn.close()

        if not candidates:
            logger.info(f"未找到匹配结果 (耗时 {time.time() - t_start:.2f}s)")
            return []

        # Step 5: 字段级匹配
        conn = self._get_connection()
        try:
            self._field_match(conn, list(candidates.values()), intent)
        finally:
            conn.close()

        # Step 6: 排序
        results = sorted(candidates.values(), key=lambda r: -r.match_score)

        # Step 7: LLM 重排序（可选，当候选较多时启用）
        if self.llm_client and len(results) > 3:
            results = self._llm_rescore(query, results[:20])

        # 截断
        results = [r for r in results if r.match_score >= self.min_score][:self.max_results]

        elapsed = time.time() - t_start
        logger.info(f"检索完成: {len(results)} 条结果 (耗时 {elapsed:.2f}s)")
        return results

    # ──────────────────── Step 1: 意图解析 ────────────────────

    def _parse_intent(self, query: str) -> dict:
        """LLM 解析用户意图"""
        if not self.llm_client:
            return self._simple_parse(query)

        prompt = INTENT_PARSE_PROMPT.format(query=query)
        try:
            content = self.llm_client.chat_with_retry(
                "standard", [{"role": "user", "content": prompt}],
                max_tokens=2048,
            )
            result = parse_json_response(content)
            if result:
                return result
        except Exception as e:
            logger.warning(f"意图解析LLM失败: {e}")

        return self._simple_parse(query)

    def _simple_parse(self, query: str) -> dict:
        """无 LLM 时的简单关键词提取"""
        # 提取年份
        years = re.findall(r'(\d{4})年?', query)
        tf = {}
        if years:
            tf["start"] = years[0]
            tf["end"] = years[-1] if len(years) > 1 else years[0]

        # 简单分词作为关键词
        keywords = [w.strip() for w in re.split(r'[，。,\s、]+', query) if len(w.strip()) >= 2]
        # 去除纯数字
        keywords = [k for k in keywords if not k.isdigit()]

        return {
            "time_filter": tf if tf else None,
            "geo_filter": None,
            "core_indicator": keywords[0] if keywords else "",
            "topic_category": "",
            "search_keywords": keywords[:8],
            "search_description": query,
        }

    # ──────────────────── Step 2: 关键词召回 ────────────────────

    def _keyword_recall(self, conn, intent: dict, candidates: dict):
        """通过关键词倒排索引召回"""
        keywords = intent.get("search_keywords", [])
        if not keywords:
            return

        cursor = conn.cursor()
        sheet_scores = defaultdict(float)

        for kw in keywords:
            kw_norm = kw.strip().lower()
            cursor.execute(
                "SELECT `sheet_ids` FROM `ods_keyword_index` WHERE `keyword_norm` = %s",
                (kw_norm,),
            )
            row = cursor.fetchone()
            if not row:
                continue

            try:
                ids = json.loads(row[0]) if isinstance(row[0], str) else row[0]
                boost = 1.5 if kw_norm == intent.get("core_indicator", "").strip().lower() else 1.0
                for sid in ids:
                    sheet_scores[sid] += 1.0 * boost
            except (json.JSONDecodeError, TypeError):
                continue

        cursor.close()

        if sheet_scores:
            self._load_candidates(conn, sheet_scores, candidates, "keyword", max_fetch=200)

    # ──────────────────── Step 3: 分类召回 ────────────────────

    def _category_recall(self, conn, intent: dict, candidates: dict):
        """通过分类索引召回"""
        topic = intent.get("topic_category", "")
        if not topic:
            return

        categories = [c.strip() for c in topic.replace("，", ",").split(",") if c.strip()]
        cursor = conn.cursor()
        sheet_scores = defaultdict(float)

        for cat in categories:
            cursor.execute(
                "SELECT `category_path`, `sheet_ids` FROM `ods_category_index` "
                "WHERE `category_name` LIKE %s OR `category_path` LIKE %s",
                (f"%{cat}%", f"%{cat}%"),
            )
            for row in cursor.fetchall():
                try:
                    ids = json.loads(row[1]) if isinstance(row[1], str) else row[1]
                    score = 1.5 if cat in row[0] else 1.0
                    for sid in ids:
                        sheet_scores[sid] += score
                except (json.JSONDecodeError, TypeError):
                    continue

        cursor.close()

        if sheet_scores:
            self._load_candidates(conn, sheet_scores, candidates, "category")

    # ──────────────────── Step 4: 描述语义召回 ────────────────────

    def _description_recall(self, conn, intent: dict, candidates: dict):
        """通过 table_description LIKE 模糊匹配召回"""
        desc = intent.get("search_description", "") or intent.get("core_indicator", "")
        if not desc:
            return

        cursor = conn.cursor()
        sheet_scores = defaultdict(float)

        # 提取核心词用于模糊匹配
        core_words = [w for w in re.split(r'[，。,、\s]+', desc) if len(w) >= 2 and not w.isdigit()]
        for word in core_words[:4]:
            cursor.execute(
                "SELECT `id`, `table_description` FROM `ods_sheet_metadata` "
                "WHERE `status` = 'SUCCESS' AND `table_description` LIKE %s LIMIT 100",
                (f"%{word}%",),
            )
            for row in cursor.fetchall():
                sheet_scores[row[0]] += 0.3

        cursor.close()

        if sheet_scores:
            self._load_candidates(conn, sheet_scores, candidates, "description")

    # ──────────────────── Step 5: 字段匹配 ────────────────────

    def _field_match(self, conn, results: list[SearchResult], intent: dict):
        """为每个候选结果匹配最相关字段"""
        core_indicator = intent.get("core_indicator", "")
        keywords = intent.get("search_keywords", [])

        if not core_indicator and not keywords:
            return

        cursor = conn.cursor()
        for r in results:
            cursor.execute(
                "SELECT `fields_json` FROM `ods_sheet_metadata` WHERE `id` = %s",
                (r.metadata_id,),
            )
            row = cursor.fetchone()
            if not row or not row[0]:
                continue

            try:
                fields = json.loads(row[0]) if isinstance(row[0], str) else row[0]
            except (json.JSONDecodeError, TypeError):
                fields = []

            r.all_fields = fields

            # 字段评分
            field_scores = []
            for f in (fields or []):
                score = 0.0
                name = f.get("column_name", "")
                desc = f.get("semantic_description", "")
                combined = f"{name} {desc}".lower()

                # 精确匹配核心指标
                if core_indicator and core_indicator.lower() in combined:
                    score += 3.0
                # 关键词匹配
                for kw in keywords:
                    if kw.lower() in combined:
                        score += 1.0
                # 同义词匹配（查 ods_field_synonym）
                if score > 0:
                    field_scores.append((f, score))

            field_scores.sort(key=lambda x: -x[1])
            r.matched_fields = [fs[0] for fs in field_scores[:5]]

            # 字段匹配分数加权到总体分
            if field_scores:
                r.match_score += field_scores[0][1] * 0.5

        cursor.close()

    # ──────────────────── 辅助: 加载候选 ────────────────────

    def _load_candidates(self, conn, sheet_scores: dict, candidates: dict,
                         source: str, max_fetch: int = 100):
        """根据 sheet id 列表加载完整元数据"""
        ids_to_load = [sid for sid in sheet_scores if sid not in candidates][:max_fetch]
        if not ids_to_load:
            return

        placeholders = ",".join(["%s"] * len(ids_to_load))
        cursor = conn.cursor()
        cursor.execute(
            f"""SELECT `id`, `file_path`, `file_name`, `sheet_name`, `sheet_index`,
                       `table_description`, `table_category`
                FROM `ods_sheet_metadata`
                WHERE `id` IN ({placeholders}) AND `status` = 'SUCCESS'""",
            ids_to_load,
        )
        for row in cursor.fetchall():
            sid = row[0]
            r = SearchResult(
                metadata_id=sid,
                file_path=row[1] or "",
                file_name=row[2] or "",
                sheet_name=row[3] or "",
                sheet_index=row[4] or 0,
                table_description=row[5] or "",
                table_category=row[6] or "",
                match_score=sheet_scores.get(sid, 0.0),
                match_reason=source,
            )
            names = [c.table_name for c in candidates.values()]
            if r.file_name not in names:
                candidates[sid] = r
        cursor.close()

    # ──────────────────── Step 6: LLM 重排序 ────────────────────

    def _llm_rescore(self, query: str, results: list[SearchResult]) -> list[SearchResult]:
        """使用 LLM 重排序（当候选较多时）"""
        cand_text = ""
        for i, r in enumerate(results):
            desc = r.table_description[:150] if r.table_description else ""
            cand_text += f"  [{i}] 分类={r.table_category} | {desc}\n"

        prompt = RESCORE_PROMPT.format(query=query, candidates=cand_text)
        try:
            content = self.llm_client.chat_with_retry(
                "standard", [{"role": "user", "content": prompt}],
                max_tokens=2048,
            )
            ranking = parse_json_response(content)
            if ranking:
                rankings = ranking.get("rankings", [])
                score_map = {}
                for rk in rankings:
                    idx = rk.get("index", -1)
                    score = rk.get("score", 0.5)
                    reason = rk.get("reason", "")
                    if 0 <= idx < len(results):
                        score_map[idx] = (score, reason)

                # 更新评分
                for idx, (score, reason) in score_map.items():
                    results[idx].match_score = score
                    results[idx].match_reason += f" | LLM: {reason}"

                results.sort(key=lambda r: -r.match_score)
        except Exception as e:
            logger.warning(f"LLM重排序失败: {e}")

        return results

    # ──────────────────── 格式化输出 ────────────────────

    def format_results(self, results: list[SearchResult]) -> str:
        """格式化检索结果为可读文本"""
        if not results:
            return "未找到匹配的数据表。"

        lines = []
        for i, r in enumerate(results):
            fields_str = ""
            if r.matched_fields:
                field_names = [f.get("column_name", "") for f in r.matched_fields[:5]]
                field_descs = [f.get("semantic_description", "")[:30] for f in r.matched_fields[:5]]
                pairs = [f"{n}({d})" for n, d in zip(field_names, field_descs) if n]
                fields_str = ", ".join(pairs) if pairs else ""

            lines.append(
                f"\n#{i + 1} [评分: {r.match_score:.2f}] {r.file_name} / {r.sheet_name}"
                f"\n    分类: {r.table_category or '未知'}"
                f"\n    描述: {r.table_description[:120] if r.table_description else '无'}"
                f"\n    路径: {r.file_path}"
                f"\n    匹配字段: {fields_str or '无'}"
                f"\n    匹配方式: {r.match_reason}"
            )
        return "\n".join(lines)

    def format_results_json(self, results: list[SearchResult]) -> list[dict]:
        """格式化检索结果为 JSON"""
        return [
            {
                "rank": i + 1,
                "score": r.match_score,
                "file_name": r.file_name,
                "sheet_name": r.sheet_name,
                "file_path": r.file_path,
                "sheet_index": r.sheet_index,
                "table_category": r.table_category,
                "table_description": r.table_description,
                "matched_fields": [
                    {"name": f.get("column_name", ""),
                     "description": f.get("semantic_description", ""),
                     "data_type": f.get("data_type", ""),
                     "unit": f.get("unit", "")}
                    for f in r.matched_fields[:5]
                ],
                "all_fields": r.all_fields,
                "match_reason": r.match_reason,
            }
            for i, r in enumerate(results)
        ]


# ────────────────────────── 命令行接口 ──────────────────────────

def load_config(path: str) -> dict:
    import yaml
    if not os.path.isabs(path):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), path)
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def main():
    import logging as _log
    _log.basicConfig(level=_log.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    config_path = "phase2_config.yaml"
    if len(sys.argv) > 2 and sys.argv[1] == "--config":
        config_path = sys.argv[2]

    config = load_config(config_path)

    search_engine = Phase3Search(
        db_config=config["database"],
        llm_config=config.get("llm"),
    )

    query = " ".join([a for a in sys.argv[1:] if not a.startswith("--")])
    if not query:
        query = input("请输入检索诉求: ")

    print(f"\n检索: {query}\n{'=' * 70}")
    results = search_engine.search(query, top_k=10)
    print(search_engine.format_results(results))

    # 同时输出 JSON
    import json as _json
    json_path = "search_result.json"
    with open(json_path, "w", encoding="utf-8") as f:
        _json.dump(search_engine.format_results_json(results), f, ensure_ascii=False, indent=2)
    print(f"\nJSON 结果已保存到: {json_path}")


if __name__ == "__main__":
    main()
