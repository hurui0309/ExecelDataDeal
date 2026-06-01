"""第二阶段：归纳汇总 — 基于 ods_sheet_metadata 构建索引

流程：
  1. 读取 ods_sheet_metadata 增量数据（支持断点续传）
  2. 构建分类索引 ods_category_index
  3. 构建关键词倒排索引 ods_keyword_index
  4. （可选）LLM 发现字段同义词映射 ods_field_synonym

增量策略：
  - 维护 ods_index_state 表记录 last_indexed_id
  - 每次运行只处理新增的 SUCCESS 记录
  - 支持定期重新全量索引（设置 incremental: false）
"""

from __future__ import annotations

import json
import os
import sys
import time
import logging
import threading
from collections import defaultdict, Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from logging.handlers import RotatingFileHandler

import pymysql

_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA_TO_DB = os.path.join(_PROJ_ROOT, "data_to_db")
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)
if _DATA_TO_DB not in sys.path:
    sys.path.insert(0, _DATA_TO_DB)

from services.llm_client import LLMClient, parse_json_response

logger = logging.getLogger("phase2_index")


def load_config(path: str) -> dict:
    import yaml
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def setup_logging(config: dict) -> None:
    log_cfg = config.get("logging", {}) or {}
    log_level = log_cfg.get("level", "INFO")
    log_file = log_cfg.get("file", "")
    handlers = [logging.StreamHandler(sys.stdout)]
    if log_file:
        log_file = datetime.now().strftime(log_file)
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                log_file, maxBytes=log_cfg.get("max_bytes", 10 * 1024 * 1024),
                backupCount=log_cfg.get("backup_count", 30), encoding="utf-8",
            )
        )
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S", handlers=handlers, force=True,
    )


# ────────────────────────── 同义词发现 Prompt ──────────────────────────

SYNONYM_DISCOVERY_PROMPT = """你是一个数据字段语义分析专家。以下是从多个数据表中提取的字段信息列表，每个字段包含字段名和语义描述。

请分析这些字段，找出表达"相同或相近含义"的字段组，返回同义词映射关系。

{fields_text}

判断规则：
1. 字段名不同但描述相同/相近含义 → 视为同义词
2. 如 "社会商品零售总额" ≈ "商品零售额" ≈ "零售总额"
3. 如 "粮食产量" ≈ "谷物产量" ≈ "粮食总产量"
4. 仅列出明确属于同义词关系的字段组，不确定的不列
5. 每个同义词组给出一个规范化名称（推荐使用的标准名称）

请严格以 JSON 格式输出（不要输出其他内容）：
{{
  "synonym_groups": [
    {{
      "canonical_name": "社会商品零售总额",
      "description": "社会商品零售总额相关统计指标",
      "synonyms": ["社会商品零售总额", "商品零售总额", "社会消费品零售总额", "零售总额"],
      "confidence": 0.95
    }}
  ]
}}"""


SYNONYM_SIMPLIFY_PROMPT = """你是一个数据字段语义分析专家。以下是从 ods_sheet_metadata 中提取的所有唯一字段名及其出现次数。

请分析这些字段名，找出语义相同或相近的字段组，建立同义词映射关系。

{fields_list}

要求：
1. 每个同义词组给出一个规范化名称（canonical_name，简洁明了）
2. 列出所有同义字段名（synonyms）
3. 给出置信度（0-1）
4. 不要硬凑，仅列出确实语义相同的字段

请严格以 JSON 格式输出：
{{
  "synonym_groups": [
    {{
      "canonical_name": "...",
      "synonyms": ["...", "..."],
      "confidence": 0.9
    }}
  ]
}}"""


# ────────────────────────── Phase2Indexer ──────────────────────────

class Phase2Indexer:
    """第二阶段：索引构建器"""

    def __init__(self, config: dict):
        self.config = config
        self.db_config = config["database"].copy()
        self.index_config = config["index"]
        self.llm_client = LLMClient(config)
        self._stats_lock = threading.Lock()
        self.stats = {
            "new_metadata": 0,
            "categories_updated": 0,
            "keywords_updated": 0,
            "synonyms_discovered": 0,
        }

    def _get_connection(self):
        return pymysql.connect(**self.db_config)

    # ──────────────────── 主入口 ────────────────────

    def run(self):
        logger.info("=" * 60)
        logger.info("第二阶段：归纳汇总 — 构建索引")
        logger.info("=" * 60)

        conn = self._get_connection()
        try:
            self._ensure_index_tables(conn)
            last_id = self._get_last_indexed_id(conn)
            logger.info(f"上次索引位置: id={last_id}")

            # Step 1: 读取增量元数据
            records = self._fetch_new_metadata(conn, last_id)
            self.stats["new_metadata"] = len(records)
            logger.info(f"增量元数据: {len(records)} 条 SUCCESS 记录")

            if not records:
                logger.info("没有新的元数据需要索引")
                self._print_summary()
                return

            # Step 2: 构建分类索引
            self._build_category_index(conn, records)

            # Step 3: 构建关键词倒排索引
            self._build_keyword_index(conn, records)

            # Step 4: 更新索引状态
            max_id = max(r[0] for r in records)
            self._update_index_state(conn, max_id)

            conn.commit()
            logger.info(f"索引状态已更新: last_indexed_id = {max_id}")

            # Step 5: 同义词发现（可选，较耗时）
            if self.index_config.get("discover_synonyms", True):
                self._discover_field_synonyms(conn)

        except Exception as e:
            logger.error(f"第二阶段异常: {e}", exc_info=True)
            conn.rollback()
        finally:
            conn.close()
            self._print_summary()

    # ──────────────────── 数据库表 ────────────────────

    def _ensure_index_tables(self, conn):
        cursor = conn.cursor()

        # 索引状态表（记录断点）
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS `ods_index_state` (
            `id`            INT AUTO_INCREMENT PRIMARY KEY,
            `index_name`    VARCHAR(64) NOT NULL UNIQUE COMMENT '索引名称',
            `last_indexed_id` BIGINT NOT NULL DEFAULT 0 COMMENT 'ods_sheet_metadata 最后索引的 id',
            `record_count`  INT DEFAULT 0 COMMENT '已索引记录数',
            `updated_at`    DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='索引构建状态表'
        """)

        # 分类索引表
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS `ods_category_index` (
            `id`                BIGINT AUTO_INCREMENT PRIMARY KEY,
            `category_path`     VARCHAR(512) NOT NULL COMMENT '分类层级路径(如: 农业/作物种植/粮食)',
            `category_name`     VARCHAR(128) NOT NULL COMMENT '末级分类名称',
            `category_level`    INT DEFAULT 1 COMMENT '分类层级深度',
            `parent_category`   VARCHAR(256) DEFAULT NULL COMMENT '父级分类路径',
            `sheet_count`       INT DEFAULT 0 COMMENT '该分类下的 Sheet 数量',
            `sheet_ids`         MEDIUMTEXT COMMENT 'ods_sheet_metadata.id 列表(JSON数组)',
            `total_rows`        BIGINT DEFAULT 0 COMMENT '总数据行数',
            `time_range_start`  VARCHAR(64) DEFAULT NULL COMMENT '时间范围(最早)',
            `time_range_end`    VARCHAR(64) DEFAULT NULL COMMENT '时间范围(最晚)',
            `sample_description` TEXT COMMENT '该分类代表性描述(取一个表的描述)',
            `created_at`        DATETIME DEFAULT CURRENT_TIMESTAMP,
            `updated_at`        DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY `uk_category` (`category_path`(255)),
            INDEX `idx_parent` (`parent_category`(128))
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='分类索引表'
        """)

        # 关键词倒排索引表
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS `ods_keyword_index` (
            `id`            BIGINT AUTO_INCREMENT PRIMARY KEY,
            `keyword`       VARCHAR(128) NOT NULL COMMENT '关键词',
            `keyword_norm`  VARCHAR(128) NOT NULL COMMENT '规范化关键词(小写去空格)',
            `sheet_ids`     MEDIUMTEXT COMMENT '包含该关键词的 sheet id 列表(JSON数组)',
            `doc_freq`      INT DEFAULT 0 COMMENT '文档频率(多少Sheet包含该词)',
            `created_at`    DATETIME DEFAULT CURRENT_TIMESTAMP,
            `updated_at`    DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY `uk_keyword` (`keyword_norm`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='关键词倒排索引表'
        """)

        # 字段同义词映射表
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS `ods_field_synonym` (
            `id`                BIGINT AUTO_INCREMENT PRIMARY KEY,
            `canonical_name`    VARCHAR(256) NOT NULL COMMENT '规范化字段名',
            `synonyms`          TEXT COMMENT '同义字段名列表(JSON数组)',
            `description`       TEXT COMMENT '字段含义描述',
            `confidence`        FLOAT DEFAULT 0.5 COMMENT '同义词置信度(0-1)',
            `doc_count`         INT DEFAULT 0 COMMENT '涉及文档数',
            `source`            VARCHAR(32) DEFAULT 'llm' COMMENT '来源: llm/rule',
            `created_at`        DATETIME DEFAULT CURRENT_TIMESTAMP,
            `updated_at`        DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY `uk_canonical` (`canonical_name`(128))
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='字段同义词映射表'
        """)

        conn.commit()
        cursor.close()
        logger.info("索引表已就绪 (ods_index_state, ods_category_index, ods_keyword_index, ods_field_synonym)")

    # ──────────────────── 断点管理 ────────────────────

    def _get_last_indexed_id(self, conn) -> int:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT `last_indexed_id` FROM `ods_index_state` WHERE `index_name` = 'main'"
        )
        row = cursor.fetchone()
        cursor.close()
        return row[0] if row else 0

    def _update_index_state(self, conn, last_id: int):
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO `ods_index_state` (`index_name`, `last_indexed_id`, `record_count`)
               VALUES ('main', %s, %s)
               ON DUPLICATE KEY UPDATE
                   `last_indexed_id` = VALUES(`last_indexed_id`),
                   `record_count` = `record_count` + VALUES(`record_count`)""",
            (last_id, self.stats["new_metadata"]),
        )
        cursor.close()

    # ──────────────────── 数据读取 ────────────────────

    def _fetch_new_metadata(self, conn, last_id: int) -> list:
        """读取 ods_sheet_metadata 中 id > last_id 的 SUCCESS 记录"""
        cursor = conn.cursor()
        cursor.execute(
            """SELECT `id`, `file_path`, `file_name`, `sheet_name`, `sheet_index`,
                      `table_description`, `table_category`, `table_keywords`,
                      `time_range_start`, `time_range_end`, `time_granularity`,
                      `geo_coverage`, `measure_type`, `fields_json`,
                      `max_row`, `max_col`
               FROM `ods_sheet_metadata`
               WHERE `status` = 'SUCCESS' AND `id` > %s
               ORDER BY `id`""",
            (last_id,),
        )
        rows = cursor.fetchall()
        cursor.close()
        return rows

    # ──────────────────── 分类索引构建 ────────────────────

    def _build_category_index(self, conn, records):
        """按 table_category 汇总构建分类树"""
        logger.info("构建分类索引...")
        cat_sheets = defaultdict(list)
        cat_meta = defaultdict(lambda: {"time_starts": [], "time_ends": [], "descriptions": [], "total_rows": 0})

        for rec in records:
            rid, _, _, _, _, desc, cat_str, _, ts, te, _, _, _, _, mrow, _ = rec
            if not cat_str:
                continue
            categories = [c.strip() for c in cat_str.replace("，", ",").split(",") if c.strip()]
            desc = desc or ""
            mrow = mrow or 0

            for cat in categories:
                cat_sheets[cat].append(rid)
                meta = cat_meta[cat]
                meta["descriptions"].append(desc)
                meta["total_rows"] += mrow
                if ts:
                    meta["time_starts"].append(ts)
                if te:
                    meta["time_ends"].append(te)

        cursor = conn.cursor()
        updated = 0

        for cat_name, sheet_ids in cat_sheets.items():
            meta = cat_meta[cat_name]
            ts_list = sorted(meta["time_starts"]) if meta["time_starts"] else []
            te_list = sorted(meta["time_ends"]) if meta["time_ends"] else []
            sample_desc = meta["descriptions"][0] if meta["descriptions"] else ""
            if len(sample_desc) > 500:
                sample_desc = sample_desc[:500]

            # 构建分类层级（按 / 分割构造多级路径）
            levels = cat_name.split("/")
            for lv in range(len(levels)):
                path = "/".join(levels[:lv + 1])
                parent = "/".join(levels[:lv]) if lv > 0 else None
                name = levels[lv]

                cursor.execute(
                    """SELECT `id`, `sheet_ids`, `sheet_count`, `total_rows`,
                              `time_range_start`, `time_range_end`
                       FROM `ods_category_index` WHERE `category_path` = %s""",
                    (path,),
                )
                existing = cursor.fetchone()

                if existing:
                    old_ids = json.loads(existing[1]) if existing[1] else []
                    merged = list(set(old_ids + sheet_ids))
                    cursor.execute(
                        """UPDATE `ods_category_index` SET
                           `sheet_count` = %s, `sheet_ids` = %s,
                           `total_rows` = `total_rows` + %s,
                           `time_range_start` = LEAST(IFNULL(`time_range_start`, '9999'), %s),
                           `time_range_end` = GREATEST(IFNULL(`time_range_end`, '0000'), %s),
                           `updated_at` = CURRENT_TIMESTAMP
                           WHERE `id` = %s""",
                        (len(merged), json.dumps(merged, ensure_ascii=False),
                         meta["total_rows"],
                         ts_list[0] if ts_list else None,
                         te_list[-1] if te_list else None,
                         existing[0]),
                    )
                else:
                    cursor.execute(
                        """INSERT INTO `ods_category_index`
                           (`category_path`, `category_name`, `category_level`,
                            `parent_category`, `sheet_count`, `sheet_ids`,
                            `total_rows`, `time_range_start`, `time_range_end`,
                            `sample_description`)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                           ON DUPLICATE KEY UPDATE
                           `sheet_count` = `sheet_count` + VALUES(`sheet_count`),
                           `total_rows` = `total_rows` + VALUES(`total_rows`),
                           `sheet_ids` = VALUES(`sheet_ids`)""",
                        (path, name, lv + 1, parent,
                         len(sheet_ids), json.dumps(sheet_ids, ensure_ascii=False),
                         meta["total_rows"],
                         ts_list[0] if ts_list else None,
                         te_list[-1] if te_list else None,
                         sample_desc),
                    )
                updated += 1

        conn.commit()
        cursor.close()
        self.stats["categories_updated"] = updated
        logger.info(f"  分类索引: {len(cat_sheets)} 个分类, {updated} 条更新")

    # ──────────────────── 关键词倒排索引 ────────────────────

    def _build_keyword_index(self, conn, records):
        """基于 table_keywords 构建倒排索引"""
        logger.info("构建关键词倒排索引...")
        kw_sheets = defaultdict(list)

        for rec in records:
            rid = rec[0]
            kw_str = rec[7]  # table_keywords
            if not kw_str:
                continue
            try:
                keywords = json.loads(kw_str) if isinstance(kw_str, str) else kw_str
                if isinstance(keywords, list):
                    for kw in keywords:
                        kw_norm = str(kw).strip().lower()
                        if kw_norm:
                            kw_sheets[kw_norm].append(rid)
            except (json.JSONDecodeError, TypeError):
                pass

        cursor = conn.cursor()
        updated = 0

        for kw_norm, sheet_ids in kw_sheets.items():
            original_kw = max(set(sheet_ids), key=lambda x: len(str(x))) if False else kw_norm
            # 取第一个sheet_id对应的关键词原文（从 records 查找）
            for rec in records:
                if rec[0] == sheet_ids[0]:
                    kw_str = rec[7]
                    try:
                        kws = json.loads(kw_str) if isinstance(kw_str, str) else kw_str
                        for k in (kws or []):
                            if str(k).strip().lower() == kw_norm:
                                original_kw = str(k).strip()
                                break
                    except Exception:
                        pass
                    break

            cursor.execute(
                """SELECT `id`, `sheet_ids`, `doc_freq` FROM `ods_keyword_index`
                   WHERE `keyword_norm` = %s""",
                (kw_norm,),
            )
            existing = cursor.fetchone()

            if existing:
                old_ids = json.loads(existing[1]) if existing[1] else []
                merged = list(set(old_ids + sheet_ids))
                cursor.execute(
                    """UPDATE `ods_keyword_index` SET
                       `sheet_ids` = %s, `doc_freq` = %s,
                       `updated_at` = CURRENT_TIMESTAMP
                       WHERE `id` = %s""",
                    (json.dumps(merged, ensure_ascii=False), len(merged), existing[0]),
                )
            else:
                cursor.execute(
                    """INSERT INTO `ods_keyword_index`
                       (`keyword`, `keyword_norm`, `sheet_ids`, `doc_freq`)
                       VALUES (%s, %s, %s, %s)""",
                    (original_kw, kw_norm,
                     json.dumps(sheet_ids, ensure_ascii=False), len(sheet_ids)),
                )
            updated += 1

        conn.commit()
        cursor.close()
        self.stats["keywords_updated"] = updated
        logger.info(f"  关键词索引: {len(kw_sheets)} 个唯一关键词, {updated} 条更新")

    # ──────────────────── 字段同义词发现 ────────────────────

    def _discover_field_synonyms(self, conn):
        """使用 LLM 发现字段同义词映射"""
        logger.info("发现字段同义词...")

        # 收集所有字段名及其出现次数
        cursor = conn.cursor()
        cursor.execute(
            "SELECT `fields_json` FROM `ods_sheet_metadata` WHERE `status` = 'SUCCESS' AND `fields_json` IS NOT NULL"
        )
        rows = cursor.fetchall()
        cursor.close()

        field_counter = Counter()
        for row in rows:
            try:
                fields = json.loads(row[0]) if isinstance(row[0], str) else row[0]
                for f in (fields or []):
                    name = f.get("column_name", "")
                    if name:
                        field_counter[name] += 1
            except (json.JSONDecodeError, TypeError):
                continue

        logger.info(f"  收集到 {len(field_counter)} 个唯一字段名")

        if not field_counter:
            return

        # 分段发送给 LLM（避免单次请求过大）
        batch_size = self.index_config.get("synonym_batch_size", 50)
        sorted_fields = sorted(field_counter.items(), key=lambda x: -x[1])
        all_groups = []

        for offset in range(0, len(sorted_fields), batch_size):
            batch = sorted_fields[offset:offset + batch_size]
            fields_text = "\n".join(
                f"- {name} (出现{count}次)" for name, count in batch
            )

            prompt = SYNONYM_SIMPLIFY_PROMPT.format(fields_list=fields_text)

            try:
                t0 = time.time()
                content = self.llm_client.chat_with_retry(
                    "standard", [{"role": "user", "content": prompt}],
                    max_tokens=4096,
                )
                elapsed = time.time() - t0
                result = parse_json_response(content)
                if result:
                    groups = result.get("synonym_groups", [])
                    all_groups.extend(groups)
                    logger.info(
                        f"    批次 {offset // batch_size + 1}: "
                        f"发现 {len(groups)} 组同义词 (耗时 {elapsed:.1f}s)"
                    )
            except Exception as e:
                logger.warning(f"    同义词发现异常 (offset={offset}): {e}")

        # 写入 ods_field_synonym 表
        if all_groups:
            cursor = conn.cursor()
            for g in all_groups:
                canonical = g.get("canonical_name", "")
                synonyms = g.get("synonyms", [])
                description = g.get("description", "")
                confidence = g.get("confidence", 0.5)

                if not canonical or not synonyms:
                    continue

                # 计算涉及的文档数
                doc_count = sum(field_counter.get(s, 0) for s in synonyms)

                cursor.execute(
                    """INSERT INTO `ods_field_synonym`
                       (`canonical_name`, `synonyms`, `description`, `confidence`, `doc_count`, `source`)
                       VALUES (%s, %s, %s, %s, %s, 'llm')
                       ON DUPLICATE KEY UPDATE
                       `synonyms` = VALUES(`synonyms`),
                       `description` = VALUES(`description`),
                       `confidence` = VALUES(`confidence`),
                       `doc_count` = VALUES(`doc_count`),
                       `updated_at` = CURRENT_TIMESTAMP""",
                    (canonical, json.dumps(synonyms, ensure_ascii=False),
                     description, confidence, doc_count),
                )
            conn.commit()
            cursor.close()
            self.stats["synonyms_discovered"] = len(all_groups)
            logger.info(f"  同义词映射: {len(all_groups)} 组已写入")

    # ──────────────────── 汇总 ────────────────────

    def _print_summary(self):
        s = self.stats
        logger.info("\n" + "=" * 60)
        logger.info("第二阶段 索引构建汇总")
        logger.info("=" * 60)
        logger.info(f"  新增元数据:       {s['new_metadata']} 条")
        logger.info(f"  分类更新:         {s['categories_updated']} 条")
        logger.info(f"  关键词更新:       {s['keywords_updated']} 条")
        logger.info(f"  同义词发现:       {s['synonyms_discovered']} 组")
        logger.info("=" * 60)


def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    config_path = sys.argv[1] if len(sys.argv) > 1 else "phase2_config.yaml"
    config = load_config(config_path)
    setup_logging(config)
    Phase2Indexer(config).run()


if __name__ == "__main__":
    main()
