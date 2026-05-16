"""Orchestrator Agent — 编排者：扫描、分发、协调、汇总"""

import os
import re
import time
import logging
import traceback

import pymysql

from services.excel_preview import list_sheets, run as preview_run, read_first_cols
from services.llm_client import LLMClient
from services.border_info import pre_classify_by_border
from services.mysql_writer import sanitize_column_name, MYSQL_IDENT_MAX
from agents.classifier import run as classifier_run
from agents.worker import run as worker_run


logger = logging.getLogger("datadeal")


def _fmt_ms(ms: float) -> str:
    """格式化耗时"""
    if ms < 1000:
        return f"{ms:.0f}ms"
    return f"{ms / 1000:.2f}s"


def _safe_error_table_name(prefix: str, source_filename: str,
                            sheet_name: str = "", sheet_index: int | None = None) -> str:
    """构造唯一的 ERROR/SKIP/UNKNOWN 占位表名（≤ 64 字符）。

    格式: <PREFIX>_<source>_<sheet>_s<idx>
    所有非法字符 → 下划线；过长按尾部截断。
    """
    parts = [prefix]
    base = re.sub(r"[^\w]", "_", source_filename or "")
    base = re.sub(r"_+", "_", base).strip("_")
    if base:
        parts.append(base)
    if sheet_name:
        s = re.sub(r"[^\w]", "_", sheet_name)
        s = re.sub(r"_+", "_", s).strip("_")
        if s:
            parts.append(s)
    if sheet_index is not None:
        parts.append(f"s{sheet_index}")
    name = "_".join(parts)
    if len(name) > MYSQL_IDENT_MAX:
        name = name[:MYSQL_IDENT_MAX]
    return name


class Orchestrator:
    """编排者：扫描文件 → 分发任务 → 协调 Agent → 汇总结果"""

    def __init__(self, config: dict):
        self.config = config
        self.scan_config = config["scan"]
        self.db_config = config["database"].copy()
        self.llm_client = LLMClient(config)
        self.table_name_counter: dict[str, int] = {}  # 全局表名去重
        self.stats = {
            "total_files": 0,
            "total_sheets": 0,
            "success": 0,
            "skip": 0,
            "error": 0,
            "unknown": 0,
        }

    def run(self):
        """主流程"""
        logger.info("=" * 60)
        logger.info("Excel 数据清洗入仓 — Agent 驱动")
        logger.info("=" * 60)

        files = self._scan_files()
        self.stats["total_files"] = len(files)
        logger.info(f"扫描到 {len(files)} 个文件")

        if not files:
            logger.info("没有需要处理的文件")
            return

        conn = self._get_connection()
        self._ensure_log_tables(conn)

        for i, file_path in enumerate(files):
            logger.info(f"\n[{i + 1}/{len(files)}] 处理: {os.path.basename(file_path)}")
            try:
                self._process_file(file_path, conn)
            except Exception as e:
                logger.error(
                    f"文件处理异常: {file_path}\n{type(e).__name__}: {e}\n{traceback.format_exc()}"
                )
                # 重连
                try:
                    conn.close()
                except Exception:
                    pass
                conn = self._get_connection()

        conn.close()
        self._print_summary()

    def _scan_files(self) -> list[str]:
        """扫描数据目录下的所有 Excel 文件"""
        data_dir = self.scan_config["data_dir"]
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

    def _process_file(self, file_path: str, conn):
        """处理单个文件"""
        file_size = os.path.getsize(file_path)
        is_xls = file_path.lower().endswith(".xls") and not file_path.lower().endswith(".xlsx")
        source_filename = os.path.basename(file_path)

        # 1. 轻量获取 sheet 列表（不读单元格内容）
        try:
            t0 = time.time()
            sheet_info = list_sheets(file_path)
            logger.info(f"    [耗时] 获取sheet列表: {_fmt_ms((time.time() - t0) * 1000)}")

            if "error" in sheet_info:
                self._log_parse_result(conn, {
                    "source_path": file_path,
                    "source_filename": source_filename,
                    "parse_strategy": "ERROR",
                    "agent": "Orchestrator",
                    "status": "ERROR",
                    "table_name": _safe_error_table_name("ERROR", source_filename),
                    "error_message": sheet_info["error"],
                    "file_size_bytes": file_size,
                    "is_xls": is_xls,
                })
                self.stats["error"] += 1
                return

            sheet_names = sheet_info.get("sheet_names", [])
        except Exception as e:
            self._log_parse_result(conn, {
                "source_path": file_path,
                "source_filename": source_filename,
                "parse_strategy": "ERROR",
                "agent": "Orchestrator",
                "status": "ERROR",
                "table_name": _safe_error_table_name("ERROR", source_filename),
                "error_message": f"文件打开失败: {e}",
                "file_size_bytes": file_size,
                "is_xls": is_xls,
            })
            self.stats["error"] += 1
            return

        self.stats["total_sheets"] += len(sheet_names)
        has_multiple_sheets = len(sheet_names) > 1

        # 2. 预加载所有 sheet 的前两列纵向数据（供分类器使用）
        first_col_cache: dict[int, dict] = {}
        first_col_rows = self.config["parse"].get("first_col_preview_rows", 500)
        first_col_n_cols = self.config["parse"].get("first_col_preview_cols", 2)
        for si in range(len(sheet_names)):
            try:
                first_col_cache[si] = read_first_cols(
                    file_path, si, n_cols=first_col_n_cols, max_rows=first_col_rows
                )
            except Exception as e:
                logger.warning(f"    sheet[{si}] 纵向预读失败(不影响后续): {e}")
                first_col_cache[si] = {"first_col_data": [], "raw_row_count": 0}

        # 3. 逐 Sheet 处理
        for sheet_index, sheet_name in enumerate(sheet_names):
            self._process_sheet(
                conn=conn,
                file_path=file_path,
                file_size=file_size,
                is_xls=is_xls,
                source_filename=source_filename,
                sheet_index=sheet_index,
                sheet_name=sheet_name,
                has_multiple_sheets=has_multiple_sheets,
                first_col_cache=first_col_cache,
            )

    def _process_sheet(self, conn, file_path: str, file_size: int, is_xls: bool,
                        source_filename: str, sheet_index: int, sheet_name: str,
                        has_multiple_sheets: bool, first_col_cache: dict):
        """处理单个 sheet。"""
        start_time = time.time()
        logger.info(f"  Sheet {sheet_index}: {sheet_name}")

        raw_row_count = first_col_cache.get(sheet_index, {}).get("raw_row_count", 0)

        # 跳过已成功处理的 sheet
        if self._is_already_parsed(conn, file_path, sheet_name):
            logger.info(f"    [OK] 跳过: 已成功解析过 (path={file_path}, sheet={sheet_name})")
            self.stats["skip"] += 1
            return

        sheet_preview: dict = {}

        # Step 1: 框线预分类
        border_preclassify_result = None
        try:
            t_border = time.time()
            border_preclassify_result = pre_classify_by_border(file_path, sheet_name)
            logger.info(f"    [耗时] 框线预分类: {_fmt_ms((time.time() - t_border) * 1000)}")
            if border_preclassify_result:
                logger.info(
                    f"    框线预分类命中: {border_preclassify_result['strategy']} "
                    f"({border_preclassify_result.get('border_detail', '')})"
                )
        except Exception as e:
            logger.warning(f"    框线预分类异常(不影响后续): {e}")

        # Step 2: Classifier 决策
        if border_preclassify_result:
            decision = border_preclassify_result
        else:
            try:
                t_preview = time.time()
                sheet_preview = preview_run(
                    file_path, sheet_index,
                    preview_rows=self.config["parse"]["preview_rows"],
                )
                logger.info(f"    [耗时] 预览数据: {_fmt_ms((time.time() - t_preview) * 1000)}")

                if "error" in sheet_preview:
                    self._log_parse_result(conn, {
                        "source_path": file_path,
                        "source_filename": source_filename,
                        "sheet_name": sheet_name,
                        "sheet_index": sheet_index,
                        "parse_strategy": "ERROR",
                        "agent": "Classifier",
                        "status": "ERROR",
                        "table_name": _safe_error_table_name(
                            "ERROR", source_filename, sheet_name, sheet_index
                        ),
                        "error_message": sheet_preview["error"],
                        "file_size_bytes": file_size,
                        "is_xls": is_xls,
                        "has_merged_cells": sheet_preview.get("merged_count", 0) > 0,
                        "merged_cells_count": sheet_preview.get("merged_count", 0),
                    }, raw_row_count=raw_row_count)
                    self.stats["error"] += 1
                    return

                # 大宽表检测：列数超过阈值直接 SKIP
                max_columns = self.config["parse"].get("max_columns", 300)
                sheet_col_count = sheet_preview.get("max_col", 0)
                if sheet_col_count > max_columns:
                    logger.info(
                        f"    大宽表检测: {sheet_col_count} 列 > {max_columns} 列阈值，标记 SKIP"
                    )
                    self._log_parse_result(conn, {
                        "source_path": file_path,
                        "source_filename": source_filename,
                        "sheet_name": sheet_name,
                        "sheet_index": sheet_index,
                        "parse_strategy": "SKIP",
                        "agent": "Classifier",
                        "status": "SKIP",
                        "table_name": _safe_error_table_name(
                            "SKIP", source_filename, sheet_name, sheet_index
                        ),
                        "error_message": f"大宽表({sheet_col_count}列)，超过阈值({max_columns}列)，MySQL 不支持",
                        "file_size_bytes": file_size,
                        "is_xls": is_xls,
                        "has_merged_cells": sheet_preview.get("merged_count", 0) > 0,
                        "merged_cells_count": sheet_preview.get("merged_count", 0),
                        "parse_time_ms": int((time.time() - start_time) * 1000),
                    }, raw_row_count=raw_row_count)
                    logger.info(f"    [OK] 跳过: 大宽表({sheet_col_count}列)")
                    self.stats["skip"] += 1
                    return

                t_classify = time.time()
                fc_info = first_col_cache.get(sheet_index, {})
                decision = classifier_run(
                    file_path=file_path,
                    sheet_index=sheet_index,
                    preview_info=sheet_preview,
                    llm_client=self.llm_client,
                    first_col_data=fc_info.get("first_col_data"),
                )
                logger.info(f"    [耗时] Classifier首次: {_fmt_ms((time.time() - t_classify) * 1000)}")

                # 置信度不足时扩展预览重试
                confidence_threshold = self.config["parse"].get("confidence_threshold", 0.8)
                extended_rows = self.config["parse"].get("preview_rows_extended", 50)
                if decision.get("confidence", 0) < confidence_threshold and decision.get("strategy") != "SKIP":
                    logger.info(
                        f"    置信度不足({decision.get('confidence', 0):.2f})，扩展预览至 {extended_rows} 行重试"
                    )
                    ext_preview = preview_run(file_path, sheet_index, preview_rows=extended_rows)
                    if "error" not in ext_preview:
                        sheet_preview = ext_preview
                        t_ext = time.time()
                        decision = classifier_run(
                            file_path=file_path,
                            sheet_index=sheet_index,
                            preview_info=ext_preview,
                            llm_client=self.llm_client,
                            first_col_data=fc_info.get("first_col_data"),
                        )
                        logger.info(f"    [耗时] Classifier扩展: {_fmt_ms((time.time() - t_ext) * 1000)}")

                    if (decision.get("confidence", 0) < confidence_threshold
                            and decision.get("strategy") != "SKIP"):
                        logger.info(
                            f"    扩展预览后仍置信度不足({decision.get('confidence', 0):.2f})，判为 UNKNOWN"
                        )
                        decision["strategy"] = "UNKNOWN"
                        decision["confidence"] = 0.0

            except Exception as e:
                self._log_parse_result(conn, {
                    "source_path": file_path,
                    "source_filename": source_filename,
                    "sheet_name": sheet_name,
                    "sheet_index": sheet_index,
                    "parse_strategy": "ERROR",
                    "agent": "Classifier",
                    "status": "ERROR",
                    "table_name": _safe_error_table_name(
                        "ERROR", source_filename, sheet_name, sheet_index
                    ),
                    "error_message": f"{type(e).__name__}: {e}\n{traceback.format_exc()}",
                    "file_size_bytes": file_size,
                    "is_xls": is_xls,
                }, raw_row_count=raw_row_count)
                logger.error(
                    f"    ✗ Classifier异常: {source_filename} / {sheet_name}\n"
                    f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
                )
                self.stats["error"] += 1
                return

        strategy = decision.get("strategy", "UNKNOWN")
        logger.info(f"    Classifier 决策: {strategy} (confidence={decision.get('confidence', 0)})")

        # Step 3: 根据决策分发
        if strategy == "SKIP":
            self._log_parse_result(conn, {
                "source_path": file_path,
                "source_filename": source_filename,
                "sheet_name": sheet_name,
                "sheet_index": sheet_index,
                "parse_strategy": "SKIP",
                "agent": "Classifier",
                "status": "SKIP",
                "table_name": _safe_error_table_name(
                    "SKIP", source_filename, sheet_name, sheet_index
                ),
                "error_message": decision.get("reasoning", "无效数据/纯说明备注页"),
                "file_size_bytes": file_size,
                "is_xls": is_xls,
                "parse_time_ms": int((time.time() - start_time) * 1000),
            }, raw_row_count=raw_row_count)
            logger.info(f"    [OK] 跳过: 无效数据/纯说明备注页")
            self.stats["skip"] += 1
            return

        if strategy == "UNKNOWN":
            self._log_parse_result(conn, {
                "source_path": file_path,
                "source_filename": source_filename,
                "sheet_name": sheet_name,
                "sheet_index": sheet_index,
                "parse_strategy": "UNKNOWN",
                "agent": "Classifier",
                "status": "UNKNOWN",
                "table_name": _safe_error_table_name(
                    "UNKNOWN", source_filename, sheet_name, sheet_index
                ),
                "error_message": "无法识别的格式，标记为 UNKNOWN",
                "file_size_bytes": file_size,
                "is_xls": is_xls,
                "parse_time_ms": int((time.time() - start_time) * 1000),
            }, raw_row_count=raw_row_count)
            self.stats["unknown"] += 1
            logger.info(f"    [OK] 标记 UNKNOWN: 无法识别的格式")
            return

        # Step 4: 在调用 Worker 前确定最终表名（sheet 后缀 + 全局去重）
        table_name_hint = decision.get("table_name_hint", "") or ""
        final_table_name = table_name_hint
        if final_table_name:
            if has_multiple_sheets and sheet_name:
                sheet_suffix = sanitize_column_name(sheet_name)
                final_table_name = f"{final_table_name}_{sheet_suffix}"
                if len(final_table_name) > MYSQL_IDENT_MAX:
                    final_table_name = final_table_name[:MYSQL_IDENT_MAX]
            final_table_name = self._ensure_unique_table_name(final_table_name)
        decision["table_name_hint"] = final_table_name

        # Step 5: Worker 执行（透传 sheet_preview 减少重复 I/O）
        t_worker = time.time()
        worker_result = worker_run(
            decision=decision,
            file_path=file_path,
            sheet_index=sheet_index,
            sheet_name=sheet_name,
            config=self.config,
            llm_client=self.llm_client,
            preview_info=sheet_preview if sheet_preview else None,
        )
        logger.info(f"    [耗时] Worker: {_fmt_ms((time.time() - t_worker) * 1000)}")

        elapsed = int((time.time() - start_time) * 1000)

        # Step 6: 记录日志
        subtable_results = worker_result.get("subtable_results")
        if subtable_results:
            for st_idx, st in enumerate(subtable_results):
                # 子表空表防护
                st_success = st.get("success", False)
                if st_success and st.get("rows_written", 0) == 0:
                    st_success = False
                    st["skip"] = True
                    logger.info(f"    [SKIP] 子表空表: {st.get('table_name')} 解析成功但 0 行数据")
                self._log_parse_result(conn, {
                    "source_path": file_path,
                    "source_filename": source_filename,
                    "sheet_name": sheet_name,
                    "sheet_index": sheet_index,
                    "subtable_index": st_idx + 1 if len(subtable_results) > 1 else 0,
                    "subtable_label": st.get("label"),
                    "parse_strategy": strategy,
                    "agent": "Worker",
                    "status": "SKIP" if st.get("skip") else ("SUCCESS" if st_success else "ERROR"),
                    "table_name": st["table_name"],
                    "actual_row_count": st.get("rows_written", 0),
                    "column_count": None,
                    "column_names": worker_result.get("column_names_json"),
                    "table_description": worker_result.get("table_description"),
                    "error_message": st.get("error"),
                    "file_size_bytes": file_size,
                    "is_xls": is_xls,
                    "has_merged_cells": sheet_preview.get("merged_count", 0) > 0,
                    "merged_cells_count": sheet_preview.get("merged_count", 0),
                    "parse_time_ms": elapsed,
                }, raw_row_count=raw_row_count)
        else:
            table_name = worker_result.get("table_name") or final_table_name or _safe_error_table_name(
                "SKIP" if worker_result.get("skip") else "ERROR", source_filename, sheet_name, sheet_index
            )
            is_skip = worker_result.get("skip", False)
            # 空表防护：success=True 但实际写入 0 行时，标记为 SKIP
            if not is_skip and worker_result.get("success") and worker_result.get("rows_written", 0) == 0:
                is_skip = True
                logger.info(f"    [SKIP] 空表: 解析成功但 0 行数据，自动标记 SKIP")
            self._log_parse_result(conn, {
                "source_path": file_path,
                "source_filename": source_filename,
                "sheet_name": sheet_name,
                "sheet_index": sheet_index,
                "parse_strategy": strategy,
                "agent": "Worker",
                "status": "SKIP" if is_skip else ("SUCCESS" if worker_result.get("success") else "ERROR"),
                "table_name": table_name,
                "actual_row_count": worker_result.get("rows_written", 0),
                "column_count": None,
                "column_names": worker_result.get("column_names_json"),
                "table_description": worker_result.get("table_description"),
                "error_message": worker_result.get("error"),
                "file_size_bytes": file_size,
                "is_xls": is_xls,
                "has_merged_cells": sheet_preview.get("merged_count", 0) > 0,
                "merged_cells_count": sheet_preview.get("merged_count", 0),
                "parse_time_ms": elapsed,
            }, raw_row_count=raw_row_count)

        if worker_result.get("skip"):
            self.stats["skip"] += 1
            logger.info(f"    [OK] 跳过: 大宽表 — {worker_result.get('error', '')}")
        elif worker_result.get("success"):
            self.stats["success"] += 1
            display_names = final_table_name
            if subtable_results and len(subtable_results) > 1:
                display_names = ", ".join(st["table_name"] for st in subtable_results)
            logger.info(
                f"    [OK] 写入 {worker_result.get('rows_written', 0)} 行 → {display_names} "
                f"(总耗时: {_fmt_ms(elapsed)})"
            )
        else:
            self.stats["error"] += 1
            logger.error(
                f"    ✗ 执行失败: {worker_result.get('error', '')} (总耗时: {_fmt_ms(elapsed)})\n"
                f"    策略={strategy}, 文件={source_filename}, Sheet={sheet_name}"
            )

    def _ensure_unique_table_name(self, table_name: str) -> str:
        """全局表名去重，追加序号后确保不超过 64 字符。

        约定: 第 1 次出现 → 原名；第 2 次 → 原名_1；第 3 次 → 原名_2 ...
        """
        if table_name in self.table_name_counter:
            self.table_name_counter[table_name] += 1
            suffix = f"_{self.table_name_counter[table_name]}"
            name = table_name[:MYSQL_IDENT_MAX - len(suffix)] + suffix
        else:
            self.table_name_counter[table_name] = 0
            name = table_name
        return name

    def _is_already_parsed(self, conn, source_path: str, sheet_name: str) -> bool:
        """检查 source_path + sheet_name 是否已有 SUCCESS 记录。

        只跳过 SUCCESS，允许 SKIP/ERROR/MANUAL_REVIEW 重试。
        """
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM `ods_parse_log` "
                "WHERE `source_path` = %s AND `sheet_name` = %s AND `status` = 'SUCCESS'",
                (source_path, sheet_name),
            )
            count = cursor.fetchone()[0]
            cursor.close()
            return count > 0
        except Exception:
            return False

    def _get_connection(self):
        return pymysql.connect(**self.db_config)

    def _migrate_parse_log_if_needed(self, conn, cursor):
        """兼容升级：旧表可能缺少 uk_source_sheet_sub 唯一键或 table_description 字段"""
        try:
            cursor.execute(
                "SELECT INDEX_NAME FROM INFORMATION_SCHEMA.STATISTICS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'ods_parse_log' "
                "AND INDEX_NAME = 'uk_source_sheet_sub'"
            )
            if not cursor.fetchone():
                cursor.execute(
                    "ALTER TABLE `ods_parse_log` ADD UNIQUE KEY `uk_source_sheet_sub` "
                    "(`source_path`(255), `sheet_name`(255), `subtable_index`)"
                )
                conn.commit()
                logger.info("ods_parse_log: 新增唯一键 uk_source_sheet_sub")
        except Exception as e:
            logger.warning(f"ods_parse_log 迁移检查跳过: {e}")
            try:
                conn.rollback()
            except Exception:
                pass

        try:
            cursor.execute(
                "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'ods_parse_log' "
                "AND COLUMN_NAME = 'table_description'"
            )
            if not cursor.fetchone():
                cursor.execute(
                    "ALTER TABLE `ods_parse_log` ADD COLUMN `table_description` TEXT DEFAULT NULL "
                    "COMMENT '表描述' AFTER `column_names`"
                )
                conn.commit()
                logger.info("ods_parse_log: 新增字段 table_description")
        except Exception as e:
            logger.warning(f"ods_parse_log table_description 迁移跳过: {e}")
            try:
                conn.rollback()
            except Exception:
                pass

    def _ensure_log_tables(self, conn):
        """确保日志表存在"""
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS `ods_parse_log` (
            `id`                 INT AUTO_INCREMENT PRIMARY KEY,
            `table_name`         VARCHAR(64)   NOT NULL COMMENT 'MySQL实际建表名',
            `source_path`        VARCHAR(1024) NOT NULL COMMENT '源文件路径',
            `source_filename`    VARCHAR(1024) NOT NULL COMMENT '源文件名',
            `sheet_name`         VARCHAR(1024) DEFAULT NULL COMMENT 'Sheet名称',
            `sheet_index`        INT           DEFAULT NULL COMMENT 'Sheet序号',
            `subtable_index`     INT           DEFAULT 0    COMMENT '子表序号',
            `subtable_label`     VARCHAR(128)  DEFAULT NULL COMMENT '子表标签',
            `parse_strategy`     VARCHAR(64)   NOT NULL COMMENT '解析策略',
            `agent`              VARCHAR(32)   DEFAULT NULL COMMENT '执行Agent',
            `status`             VARCHAR(16)   NOT NULL COMMENT '状态',
            `original_row_count` INT           DEFAULT NULL COMMENT '原始行数',
            `actual_row_count`   INT           DEFAULT NULL COMMENT '实际写入行数',
            `column_count`       INT           DEFAULT NULL COMMENT '列数',
            `column_names`       TEXT          DEFAULT NULL COMMENT '列名映射(JSON: 英文→中文)',
            `table_description`  TEXT          DEFAULT NULL COMMENT '表描述',
            `error_message`      TEXT          DEFAULT NULL COMMENT '错误信息',
            `file_size_bytes`    BIGINT        DEFAULT NULL COMMENT '文件大小',
            `is_xls`             TINYINT(1)    DEFAULT 0    COMMENT '是否xls',
            `has_merged_cells`   TINYINT(1)    DEFAULT NULL COMMENT '是否含合并单元格',
            `merged_cells_count` INT           DEFAULT NULL COMMENT '合并单元格数量',
            `parse_time_ms`      INT           DEFAULT NULL COMMENT '解析耗时',
            `created_at`         DATETIME      DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
            UNIQUE KEY `uk_table_name` (`table_name`),
            UNIQUE KEY `uk_source_sheet_sub` (`source_path`(255), `sheet_name`(255), `subtable_index`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Excel解析行为日志表'
        """)
        conn.commit()

        self._migrate_parse_log_if_needed(conn, cursor)
        cursor.close()

    def _log_parse_result(self, conn, result: dict, raw_row_count: int = 0):
        """写入 ods_parse_log。自动补 NOT NULL 字段的默认值。"""
        try:
            # 自动补 NOT NULL 字段，避免上游漏传
            result.setdefault("source_filename", os.path.basename(result.get("source_path", "")))
            result.setdefault("parse_strategy", "UNKNOWN")
            result.setdefault("status", "ERROR")
            if not result.get("table_name"):
                result["table_name"] = _safe_error_table_name(
                    result.get("status", "ERROR"),
                    result.get("source_filename", "unknown"),
                    result.get("sheet_name", ""),
                    result.get("sheet_index"),
                )
            # 自动补充 Excel 原始行数
            if result.get("original_row_count") is None and raw_row_count:
                result["original_row_count"] = raw_row_count

            cursor = conn.cursor()
            columns = [
                "table_name", "source_path", "source_filename", "sheet_name", "sheet_index",
                "subtable_index", "subtable_label", "parse_strategy", "agent", "status",
                "original_row_count", "actual_row_count", "column_count", "column_names",
                "table_description", "error_message", "file_size_bytes", "is_xls",
                "has_merged_cells", "merged_cells_count", "parse_time_ms",
            ]
            values = [result.get(c) for c in columns]
            col_str = ", ".join([f"`{c}`" for c in columns])
            placeholders = ", ".join(["%s"] * len(columns))
            _uk_cols = {"source_path", "sheet_name", "subtable_index"}
            sql = (
                f"INSERT INTO `ods_parse_log` ({col_str}) VALUES ({placeholders}) "
                f"ON DUPLICATE KEY UPDATE "
                + ", ".join([f"`{c}` = VALUES(`{c}`)" for c in columns if c not in _uk_cols])
            )
            cursor.execute(sql, values)
            conn.commit()
            cursor.close()
        except Exception as e:
            logger.error(f"写入日志失败: {e}", exc_info=True)
            try:
                conn.rollback()
            except Exception:
                pass

    def _print_summary(self):
        """打印汇总统计"""
        logger.info("\n" + "=" * 60)
        logger.info("处理汇总")
        logger.info("=" * 60)
        logger.info(f"  总文件数: {self.stats['total_files']}")
        logger.info(f"  总 Sheet 数: {self.stats['total_sheets']}")
        logger.info(f"  成功: {self.stats['success']}")
        logger.info(f"  跳过: {self.stats['skip']}")
        logger.info(f"  错误: {self.stats['error']}")
        logger.info(f"  未知格式: {self.stats['unknown']}")
        logger.info("=" * 60)
