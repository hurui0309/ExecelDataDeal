"""第一阶段：Excel 语义提取 — 主入口

流程：
  扫描目录 → 收集任务 → 并发(2线程)处理
  每任务:
    ├─ 断点检查（已处理则跳过）
    ├─ 快速跳过判断（关键词规则）
    ├─ 读取前 N 行预览
    ├─ LLM 说明页判断
    ├─ LLM 语义提取（宽表 >50 列仅提取表级语义）
    └─ 写入 ods_sheet_metadata 表
"""

from __future__ import annotations

import json
import os
import sys
import time
import logging
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from logging.handlers import RotatingFileHandler

import pymysql

# 将项目根目录和 data_to_db 加入 sys.path，以便复用其 services 模块和本包模块
_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA_TO_DB = os.path.join(_PROJ_ROOT, "data_to_db")
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)
if _DATA_TO_DB not in sys.path:
    sys.path.insert(0, _DATA_TO_DB)

import services.xlrd_patch  # noqa: F401,E402  必须最先 import UTF-16 容错补丁
from services.excel_preview import list_sheets, run as preview_run  # noqa: E402
from services.llm_client import LLMClient, parse_json_response  # noqa: E402

from prompts.semantic_extract import build_semantic_prompt, build_skip_prompt  # noqa: E402

logger = logging.getLogger("phase1_extract")


def load_config(path: str) -> dict:
    """加载 YAML 配置（支持环境变量替换）"""
    import yaml

    with open(path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    def _resolve_env(value):
        if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
            return os.environ.get(value[2:-1], "")
        return value

    def _walk(d):
        for k, v in d.items():
            if isinstance(v, dict):
                _walk(v)
            elif isinstance(v, str):
                d[k] = _resolve_env(v)

    _walk(config)
    return config


def setup_logging(config: dict) -> None:
    """配置日志"""
    log_cfg = config.get("logging", {}) or {}
    log_level = log_cfg.get("level", "INFO")
    log_file = log_cfg.get("file", "")

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        log_file = datetime.now().strftime(log_file)
        log_dir = os.path.dirname(log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                log_file,
                maxBytes=log_cfg.get("max_bytes", 10 * 1024 * 1024),
                backupCount=log_cfg.get("backup_count", 30),
                encoding="utf-8",
            )
        )

    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
        force=True,
    )


# ────────────────────── 宽表简化 Prompt ──────────────────────

_WIDE_TABLE_SEMANTIC_PROMPT = """你是一个数据表语义分析专家。以下是一个列数很多的宽表，请仅分析表的整体语义（不逐列分析字段）。

【文件路径】: {file_path}
【文件名】: {file_name}
【Sheet 名】: {sheet_name}
【Sheet 序号】: {sheet_index}
【总行数】: {max_row}
【总列数】: {max_col}
【合并单元格数】: {merged_count}
【文件大小】: {file_size}

【前 {preview_rows} 行预览数据（仅展示前30列）】:
{preview_str}

请直接分析这个表格的语义信息（注意：此表列数过多，只需概括整体内容，不需要逐列分析字段）：

请严格以 JSON 格式输出（不要输出其他内容）：
{{
  "table_description": "一段话描述表的业务内容和数据覆盖范围（50-200字）",
  "table_category": "领域分类，逗号分隔（如：农业,经济,统计）",
  "table_keywords": ["关键词1", "关键词2", ...],
  "time_range": {{"start": "起始年份", "end": "结束年份", "granularity": "year/month/day/unknown"}},
  "geo_coverage": "地理覆盖描述",
  "measure_type": "指标类型（金额/数量/比率/指数等）",
  "fields": []
}}"""


def _build_wide_table_prompt(
    file_path: str, file_name: str, sheet_name: str, sheet_index: int,
    max_row: int, max_col: int, merged_count: int, file_size: int,
    preview_data: list, preview_rows: int = 20,
) -> str:
    """构建宽表（>50列）语义提取 Prompt：仅提取表级信息，忽略字段"""
    # 仅展示前 30 列
    truncated = [row[:30] for row in preview_data]
    preview_lines = []
    for i, row in enumerate(truncated):
        cells = [str(v)[:30] if v is not None else "" for v in row]
        preview_lines.append(f"Row{i}: {', '.join(cells)}")
    preview_str = "\n".join(preview_lines)

    return _WIDE_TABLE_SEMANTIC_PROMPT.format(
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


# ────────────────────── Phase1Extractor ──────────────────────

class Phase1Extractor:
    """第一阶段：语义提取器（支持并发）"""

    # 宽表阈值：超过此列数仅提取表级语义
    WIDE_TABLE_COL_THRESHOLD = 50

    def __init__(self, config: dict):
        self.config = config
        self.scan_config = config["scan"]
        self.extract_config = config["extract"]
        self.db_config = config["database"].copy()
        self.concurrency = config["extract"].get("concurrency", 2)

        # 每个线程独立创建 LLMClient，保证线程安全
        self._llm_client_factory = lambda: LLMClient(config)

        self._stats_lock = threading.Lock()
        self.stats = {
            "total_files": 0,
            "total_sheets": 0,
            "skipped_quick": 0,
            "skipped_llm": 0,
            "skipped_incremental": 0,
            "success": 0,
            "error": 0,
            "llm_time_ms_total": 0,
        }

    def _inc_stat(self, key: str, delta: int = 1):
        with self._stats_lock:
            self.stats[key] += delta

    def _add_llm_time(self, ms: int):
        with self._stats_lock:
            self.stats["llm_time_ms_total"] += ms

    # ────────────────────── 主流程 ──────────────────────

    def run(self, data_dir: str = ""):
        """主入口"""
        logger.info("=" * 60)
        logger.info(f"第一阶段：Excel 语义提取  (并发数: {self.concurrency})")
        logger.info("=" * 60)

        scan_dir = data_dir or self.scan_config["data_dir"]
        files = self._scan_files(scan_dir)
        self.stats["total_files"] = len(files)
        logger.info(f"扫描到 {len(files)} 个文件")

        if not files:
            logger.info("没有需要处理的文件")
            return

        # 初始化数据库
        conn = self._get_connection()
        self._ensure_metadata_table(conn)
        conn.close()

        # ── 收集所有待处理 Sheet 任务 ──
        all_tasks: list[dict] = []
        for file_path in files:
            tasks = self._collect_sheet_tasks(file_path)
            all_tasks.extend(tasks)
            self._inc_stat("total_sheets", len(tasks))

        logger.info(f"共 {len(all_tasks)} 个 Sheet 待处理")

        if not all_tasks:
            logger.info("所有 Sheet 均已处理或无需处理")
            self._print_summary()
            return

        # ── 并发处理 ──
        completed = 0
        with ThreadPoolExecutor(max_workers=self.concurrency) as executor:
            future_map = {
                executor.submit(self._process_one_task, task): task
                for task in all_tasks
            }
            for future in as_completed(future_map):
                completed += 1
                task = future_map[future]
                try:
                    future.result()
                except Exception as e:
                    logger.error(
                        f"  任务异常 [{task['file_name']}/{task['sheet_name']}]: {e}"
                    )
                    self._inc_stat("error")

        self._print_summary()

    def _collect_sheet_tasks(self, file_path: str) -> list[dict]:
        """收集单个文件的所有待处理 Sheet 任务。

        在此阶段做轻量预检（断点跳过、快速关键词跳过），
        减少无效任务进入并发队列。
        """
        file_size = os.path.getsize(file_path)
        file_name = os.path.basename(file_path)

        sheet_info = list_sheets(file_path)
        if "error" in sheet_info:
            logger.warning(f"  无法读取 {file_name}: {sheet_info['error']}")
            return []

        sheet_names = sheet_info.get("sheet_names", [])
        incremental = self.extract_config.get("incremental", True)
        enable_quick_skip = self.extract_config.get("enable_quick_skip", True)
        quick_skip_kw = self.extract_config.get("quick_skip_keywords", [])
        min_rows = self.extract_config.get("min_data_rows", 3)

        tasks = []
        conn = self._get_connection()
        try:
            for si, sname in enumerate(sheet_names):
                # 断点检查（检查 SUCCESS 和 SKIP 状态，避免重复处理）
                if incremental:
                    if self._is_already_processed(conn, file_path, si):
                        self._inc_stat("skipped_incremental")
                        continue

                # 快速跳过判断（仅关键词规则，无需读 Excel）
                if enable_quick_skip and any(kw in sname for kw in quick_skip_kw):
                    logger.info(f"  [{file_name}] Sheet[{si}] {sname} → 快速跳过")
                    self._save_metadata(
                        conn, file_path, file_name, sname, si,
                        status="SKIP",
                        skip_reason="快速跳过：Sheet名称匹配跳过关键词",
                        file_size=file_size,
                    )
                    self._inc_stat("skipped_quick")
                    continue

                tasks.append({
                    "file_path": file_path,
                    "file_name": file_name,
                    "file_size": file_size,
                    "sheet_name": sname,
                    "sheet_index": si,
                })
        finally:
            conn.close()

        return tasks

    def _process_one_task(self, task: dict):
        """处理单个 Sheet 任务（线程安全）。

        每个线程使用独立的 DB 连接和 LLM 客户端。
        """
        file_path = task["file_path"]
        file_name = task["file_name"]
        file_size = task["file_size"]
        sheet_name = task["sheet_name"]
        sheet_index = task["sheet_index"]

        t_start = time.time()
        llm_client = self._llm_client_factory()
        conn = self._get_connection()

        try:
            # ── Step 1: 读取预览 ──
            preview_rows = self.extract_config["preview_rows"]
            preview = preview_run(file_path, sheet_index, preview_rows=preview_rows)
            if "error" in preview:
                logger.warning(f"  [{file_name}] Sheet[{sheet_index}] {sheet_name} 预览失败")
                self._save_metadata(
                    conn, file_path, file_name, sheet_name, sheet_index,
                    status="ERROR", skip_reason=f"预览失败: {preview['error']}",
                    file_size=file_size,
                )
                self._inc_stat("error")
                return

            max_row = preview.get("max_row", 0)
            max_col = preview.get("max_col", 0)
            merged_count = preview.get("merged_count", 0)
            preview_data = preview.get("preview_data", [])

            # 行数太少直接跳过
            min_rows = self.extract_config.get("min_data_rows", 3)
            if max_row <= min_rows:
                logger.info(f"  [{file_name}] Sheet[{sheet_index}] {sheet_name} → 行数太少({max_row})，跳过")
                self._save_metadata(
                    conn, file_path, file_name, sheet_name, sheet_index,
                    status="SKIP", max_row=max_row, max_col=max_col,
                    skip_reason=f"行数太少({max_row} ≤ {min_rows})",
                    file_size=file_size,
                )
                self._inc_stat("skipped_quick")
                return

            # ── Step 2: LLM 说明页判断 ──
            is_skip, skip_reason = self._llm_skip_detect(
                llm_client, file_path, sheet_name, sheet_index,
                max_row, max_col, preview_data, preview_rows
            )
            if is_skip:
                logger.info(f"  [{file_name}] Sheet[{sheet_index}] {sheet_name} → LLM判断为说明页")
                self._save_metadata(
                    conn, file_path, file_name, sheet_name, sheet_index,
                    status="SKIP", max_row=max_row, max_col=max_col,
                    merged_count=merged_count, skip_reason=skip_reason,
                    file_size=file_size,
                )
                self._inc_stat("skipped_llm")
                return

            # ── Step 3: 语义提取（宽表/普通表分流，带重试）──
            is_wide = max_col > self.WIDE_TABLE_COL_THRESHOLD
            metadata = None
            for attempt in range(3):
                if is_wide:
                    metadata = self._llm_wide_table_extract(
                        llm_client, file_path, file_name, sheet_name, sheet_index,
                        max_row, max_col, merged_count, file_size,
                        preview_data, preview_rows
                    )
                else:
                    metadata = self._llm_semantic_extract(
                        llm_client, file_path, file_name, sheet_name, sheet_index,
                        max_row, max_col, merged_count, file_size,
                        preview_data, preview_rows
                    )
                if metadata is not None:
                    break
                if attempt < 2:
                    wait_s = (attempt + 1) * 3
                    logger.warning(
                        f"    [{file_name}] {sheet_name} 语义提取失败，"
                        f"{wait_s}s 后重试 ({attempt + 1}/2)..."
                    )
                    time.sleep(wait_s)

            if metadata is None:
                logger.warning(f"  [{file_name}] Sheet[{sheet_index}] {sheet_name} → 语义提取失败（3次）")
                self._save_metadata(
                    conn, file_path, file_name, sheet_name, sheet_index,
                    status="ERROR", max_row=max_row, max_col=max_col,
                    merged_count=merged_count,
                    skip_reason="LLM语义提取失败（重试3次）",
                    file_size=file_size,
                )
                self._inc_stat("error")
                return

            # ── Step 4: 写入元数据 ──
            elapsed = int((time.time() - t_start) * 1000)
            self._save_metadata(
                conn, file_path, file_name, sheet_name, sheet_index,
                status="SUCCESS",
                max_row=max_row, max_col=max_col, merged_count=merged_count,
                table_description=metadata.get("table_description", ""),
                table_category=metadata.get("table_category", ""),
                table_keywords=metadata.get("table_keywords", []),
                time_range_start=(metadata.get("time_range") or {}).get("start"),
                time_range_end=(metadata.get("time_range") or {}).get("end"),
                time_granularity=(metadata.get("time_range") or {}).get("granularity"),
                geo_coverage=metadata.get("geo_coverage", ""),
                measure_type=metadata.get("measure_type", ""),
                fields_json=metadata.get("fields", []),
                llm_model=llm_client.standard_cfg.get("model", ""),
                file_size=file_size,
            )
            self._inc_stat("success")
            tag = "宽表(仅表级)" if is_wide else ""
            logger.info(
                f"  [{file_name}] Sheet[{sheet_index}] {sheet_name} → 成功 "
                f"({len(metadata.get('fields', []))} 字段{', ' + tag if tag else ''}, "
                f"耗时 {elapsed / 1000:.1f}s)"
            )

        except Exception as e:
            logger.error(
                f"  [{file_name}] Sheet[{sheet_index}] {sheet_name} 异常: "
                f"{type(e).__name__}: {e}"
            )
            self._inc_stat("error")
        finally:
            conn.close()

    # ────────────────────── 扫描 ──────────────────────

    def _scan_files(self, data_dir: str) -> list[str]:
        extensions = set(ext.lower() for ext in self.scan_config["extensions"])
        skip_prefixes = tuple(self.scan_config.get("skip_prefixes", ["~$"]))
        skip_dirs = set(self.scan_config.get("skip_dirs", []))

        files: list[str] = []
        for root, dirs, filenames in os.walk(data_dir):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for fn in filenames:
                if fn.startswith(skip_prefixes):
                    continue
                if os.path.splitext(fn)[1].lower() in extensions:
                    files.append(os.path.join(root, fn))
        files.sort()
        return files

    # ────────────────────── 跳过判断 ──────────────────────

    def _llm_skip_detect(self, llm_client: LLMClient, file_path: str,
                         sheet_name: str, sheet_index: int,
                         max_row: int, max_col: int,
                         preview_data: list, preview_rows: int
                         ) -> tuple[bool, str]:
        prompt = build_skip_prompt(
            file_path, sheet_name, sheet_index,
            max_row, max_col, preview_data, preview_rows
        )
        try:
            t0 = time.time()
            result = llm_client.chat_json(
                "standard", [{"role": "user", "content": prompt}]
            )
            self._add_llm_time(int((time.time() - t0) * 1000))
            if result is None:
                return False, ""
            return result.get("is_skip", False), result.get("reason", "")
        except Exception as e:
            logger.warning(f"    [跳过判断] LLM异常: {e}")
            return False, ""

    # ────────────────────── 语义提取：普通表 ──────────────────────

    def _llm_semantic_extract(self, llm_client: LLMClient,
                               file_path: str, file_name: str,
                               sheet_name: str, sheet_index: int,
                               max_row: int, max_col: int, merged_count: int,
                               file_size: int, preview_data: list,
                               preview_rows: int) -> dict | None:
        prompt = build_semantic_prompt(
            file_path, file_name, sheet_name, sheet_index,
            max_row, max_col, merged_count, file_size,
            preview_data, preview_rows
        )
        return self._call_llm_and_parse(llm_client, prompt, is_wide=False)

    # ────────────────────── 语义提取：宽表（>50列）──────────────────

    def _llm_wide_table_extract(self, llm_client: LLMClient,
                                 file_path: str, file_name: str,
                                 sheet_name: str, sheet_index: int,
                                 max_row: int, max_col: int, merged_count: int,
                                 file_size: int, preview_data: list,
                                 preview_rows: int) -> dict | None:
        prompt = _build_wide_table_prompt(
            file_path, file_name, sheet_name, sheet_index,
            max_row, max_col, merged_count, file_size,
            preview_data, preview_rows
        )
        return self._call_llm_and_parse(llm_client, prompt, is_wide=True)

    def _call_llm_and_parse(self, llm_client: LLMClient, prompt: str,
                             is_wide: bool = False) -> dict | None:
        """通用 LLM 调用 + JSON 解析 + 校验"""
        try:
            t0 = time.time()
            max_tokens = (
                self.config["llm"].get("wide_table_max_tokens", 4096)
                if is_wide
                else self.config["llm"].get("extract_max_tokens", 16384)
            )
            content = llm_client.chat_with_retry(
                "standard", [{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
            )
            self._add_llm_time(int((time.time() - t0) * 1000))

            result = parse_json_response(content)
            if result is None:
                result = self._try_repair_json(content)
            if result is None:
                logger.warning(f"    [语义提取] JSON解析失败: {content[:200]}")
                return None

            if not result.get("table_description"):
                logger.warning("    [语义提取] 缺少 table_description")
                return None

            # 宽表跳过字段校验
            if not is_wide:
                fields = result.get("fields", [])
                valid_fields = [f for f in fields
                                if isinstance(f, dict) and "column_index" in f]
                result["fields"] = valid_fields
            else:
                result["fields"] = []

            return result

        except Exception as e:
            logger.error(f"    [语义提取] LLM异常: {type(e).__name__}: {e}")
            return None

    # ────────────────────── JSON 修复 ──────────────────────

    def _try_repair_json(self, content: str) -> dict | None:
        if not content:
            return None
        cleaned = content.strip()
        if cleaned.startswith("```"):
            parts = cleaned.split("\n", 1)
            cleaned = parts[1] if len(parts) > 1 else ""
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].strip()

        start = cleaned.find("{")
        if start < 0:
            return None
        json_str = cleaned[start:]

        for pad in ["", "}]", "]}]", "}]}]", "}]\n}", "]}"]:
            try:
                result = json.loads(json_str + pad)
                if result.get("table_description"):
                    logger.info(f"    [JSON修复] 补齐 '{pad}' 成功")
                    return result
            except json.JSONDecodeError:
                continue

        # 截断到最后一个完整 field 后补齐
        try:
            last_complete = -1
            brace_depth = 0
            in_string = False
            for i, ch in enumerate(json_str):
                if ch == '"' and (i == 0 or json_str[i - 1] != '\\'):
                    in_string = not in_string
                if in_string:
                    continue
                if ch == '{':
                    brace_depth += 1
                elif ch == '}':
                    brace_depth -= 1
                elif ch == ',' and brace_depth == 1:
                    last_complete = i
            if last_complete > 0:
                truncated = json_str[:last_complete] + "}]}"
                result = json.loads(truncated)
                if result.get("table_description"):
                    logger.info("    [JSON修复] 截断补齐成功")
                    return result
        except Exception:
            pass
        return None

    # ────────────────────── 数据库操作 ──────────────────────

    def _get_connection(self):
        return pymysql.connect(**self.db_config)

    def _is_already_processed(self, conn, file_path: str, sheet_index: int) -> bool:
        """检查是否已有有效元数据记录（SUCCESS 或 SKIP 均可跳过）。

        仅 ERROR 状态允许重试。
        """
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM `ods_sheet_metadata` "
                "WHERE `file_path` = %s AND `sheet_index` = %s "
                "AND `status` IN ('SUCCESS', 'SKIP')",
                (file_path, sheet_index),
            )
            count = cursor.fetchone()[0]
            cursor.close()
            return count > 0
        except pymysql.err.ProgrammingError:
            return False
        except Exception:
            return False

    def _ensure_metadata_table(self, conn):
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS `ods_sheet_metadata` (
            `id`                    BIGINT AUTO_INCREMENT PRIMARY KEY,
            `file_path`             VARCHAR(1024) NOT NULL COMMENT 'Excel文件完整路径',
            `sheet_index`           INT NOT NULL COMMENT 'Sheet序号(0-based)',
            `file_name`             VARCHAR(512) NOT NULL COMMENT '文件名',
            `sheet_name`            VARCHAR(512) NOT NULL COMMENT 'Sheet名称',
            `file_size_bytes`       BIGINT DEFAULT NULL COMMENT '文件大小(字节)',
            `max_row`               INT DEFAULT NULL COMMENT '总行数',
            `max_col`               INT DEFAULT NULL COMMENT '总列数',
            `merged_count`          INT DEFAULT NULL COMMENT '合并单元格数',
            `table_description`     TEXT COMMENT 'LLM生成的表内容描述',
            `table_category`        VARCHAR(256) DEFAULT NULL COMMENT '表分类标签',
            `table_keywords`        TEXT COMMENT '关键词列表(JSON数组)',
            `time_range_start`      VARCHAR(64) DEFAULT NULL COMMENT '时间范围起始',
            `time_range_end`        VARCHAR(64) DEFAULT NULL COMMENT '时间范围结束',
            `time_granularity`      VARCHAR(32) DEFAULT NULL COMMENT '时间粒度(year/month/day)',
            `geo_coverage`          VARCHAR(256) DEFAULT NULL COMMENT '地理覆盖描述',
            `measure_type`          VARCHAR(128) DEFAULT NULL COMMENT '指标类型',
            `fields_json`           MEDIUMTEXT COMMENT '字段信息JSON数组',
            `status`                VARCHAR(16) NOT NULL DEFAULT 'SUCCESS' COMMENT 'SUCCESS/SKIP/ERROR',
            `skip_reason`           TEXT DEFAULT NULL COMMENT '跳过/失败原因',
            `llm_model`             VARCHAR(64) DEFAULT NULL COMMENT '使用的LLM模型',
            `created_at`            DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
            `updated_at`            DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
            UNIQUE KEY `uk_file_sheet` (`file_path`(255), `sheet_index`),
            INDEX `idx_category` (`table_category`),
            INDEX `idx_status` (`status`),
            INDEX `idx_file_path` (`file_path`(255))
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Sheet语义元数据表'
        """)
        conn.commit()
        cursor.close()
        logger.info("ods_sheet_metadata 表已就绪")

    def _save_metadata(self, conn, file_path: str, file_name: str,
                       sheet_name: str, sheet_index: int,
                       status: str = "SUCCESS",
                       max_row: int = None, max_col: int = None,
                       merged_count: int = None,
                       table_description: str = None,
                       table_category: str = None,
                       table_keywords: list = None,
                       time_range_start: str = None,
                       time_range_end: str = None,
                       time_granularity: str = None,
                       geo_coverage: str = None,
                       measure_type: str = None,
                       fields_json: list = None,
                       skip_reason: str = None,
                       llm_model: str = None,
                       file_size: int = None):
        try:
            cursor = conn.cursor()
            keywords_str = json.dumps(table_keywords, ensure_ascii=False) if table_keywords else None
            fields_str = json.dumps(fields_json, ensure_ascii=False) if fields_json else None

            cursor.execute(
                """INSERT INTO `ods_sheet_metadata` (
                    `file_path`, `sheet_index`, `file_name`, `sheet_name`,
                    `file_size_bytes`, `max_row`, `max_col`, `merged_count`,
                    `table_description`, `table_category`, `table_keywords`,
                    `time_range_start`, `time_range_end`, `time_granularity`,
                    `geo_coverage`, `measure_type`, `fields_json`,
                    `status`, `skip_reason`, `llm_model`
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s
                ) ON DUPLICATE KEY UPDATE
                    `file_name` = VALUES(`file_name`),
                    `sheet_name` = VALUES(`sheet_name`),
                    `file_size_bytes` = VALUES(`file_size_bytes`),
                    `max_row` = VALUES(`max_row`),
                    `max_col` = VALUES(`max_col`),
                    `merged_count` = VALUES(`merged_count`),
                    `table_description` = VALUES(`table_description`),
                    `table_category` = VALUES(`table_category`),
                    `table_keywords` = VALUES(`table_keywords`),
                    `time_range_start` = VALUES(`time_range_start`),
                    `time_range_end` = VALUES(`time_range_end`),
                    `time_granularity` = VALUES(`time_granularity`),
                    `geo_coverage` = VALUES(`geo_coverage`),
                    `measure_type` = VALUES(`measure_type`),
                    `fields_json` = VALUES(`fields_json`),
                    `status` = VALUES(`status`),
                    `skip_reason` = VALUES(`skip_reason`),
                    `llm_model` = VALUES(`llm_model`),
                    `updated_at` = CURRENT_TIMESTAMP
                """,
                (
                    file_path, sheet_index, file_name, sheet_name,
                    file_size, max_row, max_col, merged_count,
                    table_description, table_category, keywords_str,
                    time_range_start, time_range_end, time_granularity,
                    geo_coverage, measure_type, fields_str,
                    status, skip_reason, llm_model,
                ),
            )
            conn.commit()
            cursor.close()
        except Exception as e:
            logger.error(f"    写入元数据失败: {e}", exc_info=True)
            try:
                conn.rollback()
            except Exception:
                pass

    # ────────────────────── 汇总 ──────────────────────

    def _print_summary(self):
        s = self.stats
        logger.info("\n" + "=" * 60)
        logger.info("第一阶段 处理汇总")
        logger.info("=" * 60)
        logger.info(f"  总文件数:         {s['total_files']}")
        logger.info(f"  总 Sheet 数:      {s['total_sheets']}")
        logger.info(f"  并发数:           {self.concurrency}")
        logger.info(f"  成功提取:         {s['success']}")
        logger.info(f"  快速跳过(规则):   {s['skipped_quick']}")
        logger.info(f"  LLM跳过(说明页):  {s['skipped_llm']}")
        logger.info(f"  增量跳过(已处理): {s['skipped_incremental']}")
        logger.info(f"  错误:             {s['error']}")
        logger.info(f"  LLM 总耗时:       {s['llm_time_ms_total'] / 1000:.1f}s")
        logger.info("=" * 60)


# ────────────────────── 入口 ──────────────────────

def main() -> None:
    """命令行入口

    用法:
        python phase1_extract.py                          # 使用默认配置和 data_dir
        python phase1_extract.py phase1_config.yaml        # 指定配置文件
        python phase1_extract.py phase1_config.yaml ./data # 指定配置+数据目录
    """
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    config_path = sys.argv[1] if len(sys.argv) > 1 else "phase1_config.yaml"
    config = load_config(config_path)

    data_dir = ""
    if len(sys.argv) > 2:
        data_dir = sys.argv[2]

    setup_logging(config)
    Phase1Extractor(config).run(data_dir)


if __name__ == "__main__":
    main()
