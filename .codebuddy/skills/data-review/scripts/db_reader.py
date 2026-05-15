"""
db_reader.py — 落库结果审查辅助脚本

提供以下子命令：
  summary        读取 parse_log 并输出紧凑汇总（推荐首次使用）
  full_review    全量审查：一次扫描所有 SUCCESS 记录（早期测试阶段首选）
  auto_compare   自动对比 DB 表与 Excel 原始数据（单表深度审查）
  batch_compare  批量快速对比多张表
  table_info     读取指定表的结构 + 样本数据
  excel_preview  读取 Excel 文件预览数据
  list_tables    列出所有业务表（排除 ods_parse_log）

用法：
  python db_reader.py summary
  python db_reader.py full_review
  python db_reader.py auto_compare <table_name>
  python db_reader.py batch_compare [--strategy STRATEGY] [--limit 10]
  python db_reader.py table_info <table_name>
  python db_reader.py excel_preview <file_path> <sheet_index> [--rows 20]
  python db_reader.py list_tables
"""

import sys
import os
import json
import argparse
import re

# 将 data_to_db 加入 sys.path 以复用项目模块
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", "..", ".."))
DATA_TO_DB_DIR = os.path.join(PROJECT_ROOT, "data_to_db")
if DATA_TO_DB_DIR not in sys.path:
    sys.path.insert(0, DATA_TO_DB_DIR)

import yaml
import pymysql


def load_config() -> dict:
    """读取项目 config.yaml"""
    config_path = os.path.join(DATA_TO_DB_DIR, "config.yaml")
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_connection(db_config: dict):
    """创建 MySQL 连接"""
    return pymysql.connect(**db_config)


def serialize_row(row: dict) -> dict:
    """序列化单行数据（处理 bytes/datetime/numpy）"""
    for key, val in row.items():
        if isinstance(val, bytes):
            row[key] = val.decode("utf-8", errors="replace")
        elif hasattr(val, "isoformat"):
            row[key] = val.isoformat()
    return row


# 系统追加列（非原始 Excel 数据列，不参与列数对比和 null 检测）
SYSTEM_COLUMNS = {"data_source", "col_empty", "_id"}


def resolve_excel_path(source_path: str) -> str:
    """解析 Excel 文件路径：尝试绝对路径，不存在则在 sample_files/ 下查找文件名"""
    if os.path.exists(source_path):
        return source_path

    # 尝试在 sample_files 目录下查找
    basename = os.path.basename(source_path)
    sample_dir = os.path.join(PROJECT_ROOT, "sample_files")
    candidate = os.path.join(sample_dir, basename)
    if os.path.exists(candidate):
        return candidate

    # 尝试模糊匹配（文件名可能略有不同）
    if os.path.isdir(sample_dir):
        for f in os.listdir(sample_dir):
            if f.endswith((".xlsx", ".xls")) and basename[:20] in f:
                return os.path.join(sample_dir, f)

    return source_path  # 返回原始路径，后续会报错


# ─── 子命令: summary ─────────────────────────────────────────────

def cmd_summary(args):
    """读取 parse_log 并输出紧凑汇总表"""
    config = load_config()
    db_config = config["database"]
    conn = get_connection(db_config)

    try:
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        conditions = []
        params = []
        if args.status:
            placeholders = ",".join(["%s"] * len(args.status))
            conditions.append(f"status IN ({placeholders})")
            params.extend(args.status)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        limit = args.limit or 200

        sql = f"SELECT * FROM `ods_parse_log` {where_clause} ORDER BY id"
        cursor.execute(sql, params)
        rows = cursor.fetchall()

        result = {
            "total": len(rows),
            "records": []
        }

        for r in rows:
            r = serialize_row(r)
            record = {
                "id": r["id"],
                "table_name": r["table_name"],
                "source_filename": r.get("source_filename", ""),
                "sheet_name": r.get("sheet_name", ""),
                "sheet_index": r.get("sheet_index", 0),
                "parse_strategy": r.get("parse_strategy", ""),
                "status": r.get("status", ""),
                "original_row_count": r.get("original_row_count", 0),
                "actual_row_count": r.get("actual_row_count", 0),
                "column_count": r.get("column_count", 0),
                "has_merged_cells": bool(r.get("has_merged_cells", 0)),
                "merged_cells_count": r.get("merged_cells_count", 0),
                "source_path": r.get("source_path", ""),
            }
            result["records"].append(record)

        # 统计信息
        strategy_counts = {}
        status_counts = {}
        for r in result["records"]:
            s = r["parse_strategy"]
            strategy_counts[s] = strategy_counts.get(s, 0) + 1
            st = r["status"]
            status_counts[st] = status_counts.get(st, 0) + 1

        result["statistics"] = {
            "by_strategy": strategy_counts,
            "by_status": status_counts,
        }

        # 异常标记
        anomalies = []
        for r in result["records"]:
            flags = []
            if r["actual_row_count"] == 0 and r["status"] == "SUCCESS":
                flags.append("空表(0行)")
            if r["has_merged_cells"] and r["merged_cells_count"] > 50:
                flags.append(f"大量合并单元格({r['merged_cells_count']})")
            if r["original_row_count"] > 0 and r["actual_row_count"] > 0:
                ratio = r["actual_row_count"] / r["original_row_count"]
                if ratio < 0.5:
                    flags.append(f"行数差异大(原始{r['original_row_count']}→实际{r['actual_row_count']}, {ratio:.0%})")
            if r["parse_strategy"] in ("strategy_paired_row_bilingual",):
                flags.append("特殊策略(paired_row)")
            if flags:
                anomalies.append({"table_name": r["table_name"], "flags": flags})

        result["anomalies"] = anomalies

        print(json.dumps(result, ensure_ascii=False, indent=2))

    finally:
        conn.close()


# ─── 子命令: parse_log ────────────────────────────────────────────

def cmd_parse_log(args):
    """读取 ods_parse_log 解析日志"""
    config = load_config()
    db_config = config["database"]
    conn = get_connection(db_config)

    try:
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        conditions = []
        params = []
        if args.status:
            placeholders = ",".join(["%s"] * len(args.status))
            conditions.append(f"status IN ({placeholders})")
            params.extend(args.status)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        limit = args.limit or 100

        sql = f"SELECT * FROM `ods_parse_log` {where_clause} ORDER BY id DESC LIMIT %s"
        params.append(limit)

        cursor.execute(sql, params)
        rows = cursor.fetchall()

        for row in rows:
            serialize_row(row)

        print(json.dumps(rows, ensure_ascii=False, indent=2))

    finally:
        conn.close()


# ─── 子命令: table_info ──────────────────────────────────────────

def cmd_table_info(args):
    """读取指定表的结构 + 样本数据"""
    config = load_config()
    db_config = config["database"]
    database = db_config["database"]
    conn = get_connection(db_config)

    try:
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        table_name = args.table_name

        # 1. 表结构
        cursor.execute(
            "SELECT COLUMN_NAME, COLUMN_TYPE, COLUMN_COMMENT, IS_NULLABLE, COLUMN_KEY "
            "FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s "
            "ORDER BY ORDINAL_POSITION",
            (database, table_name),
        )
        columns = cursor.fetchall()

        # 2. CREATE TABLE 语句
        cursor.execute(f"SHOW CREATE TABLE `{table_name}`")
        create_result = cursor.fetchone()
        create_sql = create_result.get("Create Table", "") if create_result else ""

        # 3. 总行数
        cursor.execute(f"SELECT COUNT(*) as cnt FROM `{table_name}`")
        row_count = cursor.fetchone()["cnt"]

        # 4. 前 N 行样本数据
        sample_rows = []
        if row_count > 0:
            cursor.execute(f"SELECT * FROM `{table_name}` LIMIT %s", (args.sample or 5,))
            sample_rows = [serialize_row(r) for r in cursor.fetchall()]

        # 5. 表注释
        cursor.execute(
            "SELECT TABLE_COMMENT FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s",
            (database, table_name),
        )
        table_comment_result = cursor.fetchone()
        table_comment = table_comment_result["TABLE_COMMENT"] if table_comment_result else ""

        # 6. 空值统计（检查是否有全 null 行）
        null_stats = {}
        if row_count > 0 and columns:
            data_cols = [c["COLUMN_NAME"] for c in columns if not c["COLUMN_NAME"].startswith("_")]
            if data_cols:
                # 统计每列的 null 比例
                col_exprs = ", ".join(
                    f"SUM(CASE WHEN `{c}` IS NULL THEN 1 ELSE 0 END)/COUNT(*) as `{c}_null_pct`"
                    for c in data_cols[:20]  # 限制列数避免 SQL 过长
                )
                cursor.execute(f"SELECT {col_exprs} FROM `{table_name}`")
                null_row = cursor.fetchone()
                if null_row:
                    for k, v in null_row.items():
                        col_name = k.replace("_null_pct", "")
                        null_stats[col_name] = round(float(v), 3) if v else 0

        result = {
            "table_name": table_name,
            "table_comment": table_comment,
            "row_count": row_count,
            "columns": columns,
            "null_pct": null_stats,
            "create_sql": create_sql,
            "sample_data": sample_rows,
        }

        print(json.dumps(result, ensure_ascii=False, indent=2))

    finally:
        conn.close()


# ─── 子命令: excel_preview ────────────────────────────────────────

def cmd_excel_preview(args):
    """读取 Excel 文件预览数据"""
    from services.excel_preview import list_sheets, run as preview_run

    file_path = resolve_excel_path(args.file_path)
    sheet_index = args.sheet_index
    preview_rows = args.rows or 20

    if not os.path.exists(file_path):
        print(json.dumps({"error": f"文件不存在: {file_path}"}, ensure_ascii=False))
        return

    # 获取 sheet 列表
    sheet_info = list_sheets(file_path)
    if "error" in sheet_info:
        print(json.dumps(sheet_info, ensure_ascii=False))
        return

    sheet_names = sheet_info.get("sheet_names", [])

    # 预览指定 sheet
    preview = preview_run(file_path, sheet_index, preview_rows=preview_rows)
    if "error" in preview:
        print(json.dumps(preview, ensure_ascii=False))
        return

    # 序列化处理
    def serialize_val(v):
        if v is None:
            return None
        try:
            import numpy as np
            if isinstance(v, (np.integer,)):
                return int(v)
            if isinstance(v, (np.floating,)):
                return float(v)
            if isinstance(v, np.bool_):
                return bool(v)
        except ImportError:
            pass
        return v

    preview_data = []
    for row in preview.get("preview_data", []):
        preview_data.append([serialize_val(v) for v in row])

    result = {
        "file_path": preview.get("file_path", file_path),
        "sheet_names": sheet_names,
        "sheet_index": sheet_index,
        "sheet_name": preview.get("sheet_name", ""),
        "max_row": preview.get("max_row", 0),
        "max_col": preview.get("max_col", 0),
        "merged_count": preview.get("merged_count", 0),
        "preview_data": preview_data,
    }

    print(json.dumps(result, ensure_ascii=False, indent=2))


# ─── 子命令: list_tables ─────────────────────────────────────────

def cmd_list_tables(args):
    """列出所有业务表（排除 ods_parse_log）"""
    config = load_config()
    db_config = config["database"]
    database = db_config["database"]
    conn = get_connection(db_config)

    try:
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        cursor.execute(
            "SELECT TABLE_NAME, TABLE_COMMENT, TABLE_ROWS "
            "FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_SCHEMA = %s AND TABLE_NAME != 'ods_parse_log' "
            "ORDER BY TABLE_NAME",
            (database,),
        )
        tables = cursor.fetchall()
        print(json.dumps(tables, ensure_ascii=False, indent=2))
    finally:
        conn.close()


# ─── 子命令: auto_compare ────────────────────────────────────────

def cmd_auto_compare(args):
    """自动对比 DB 表与 Excel 原始数据，输出对比结果"""
    config = load_config()
    db_config = config["database"]
    database = db_config["database"]
    conn = get_connection(db_config)

    try:
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        table_name = args.table_name

        # 1. 从 parse_log 获取元信息
        cursor.execute(
            "SELECT * FROM `ods_parse_log` WHERE `table_name` = %s LIMIT 1",
            (table_name,),
        )
        log_row = cursor.fetchone()
        if not log_row:
            print(json.dumps({"error": f"parse_log 中未找到表 {table_name}"}, ensure_ascii=False))
            return
        log_row = serialize_row(log_row)

        # 2. 获取表结构
        cursor.execute(
            "SELECT COLUMN_NAME, COLUMN_TYPE, COLUMN_COMMENT "
            "FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s "
            "ORDER BY ORDINAL_POSITION",
            (database, table_name),
        )
        columns = cursor.fetchall()

        # 3. 总行数
        cursor.execute(f"SELECT COUNT(*) as cnt FROM `{table_name}`")
        db_row_count = cursor.fetchone()["cnt"]

        # 4. 样本数据
        sample_rows = []
        if db_row_count > 0:
            cursor.execute(f"SELECT * FROM `{table_name}` LIMIT %s", (args.sample or 3,))
            sample_rows = [serialize_row(r) for r in cursor.fetchall()]

        # 5. 空值统计
        null_stats = {}
        data_cols = [c["COLUMN_NAME"] for c in columns if not c["COLUMN_NAME"].startswith("_")]
        biz_cols = [c for c in data_cols if c not in SYSTEM_COLUMNS]
        if db_row_count > 0 and data_cols:
            col_exprs = ", ".join(
                f"SUM(CASE WHEN `{c}` IS NULL THEN 1 ELSE 0 END) as `{c}_nulls`"
                for c in data_cols[:20]
            )
            cursor.execute(f"SELECT {col_exprs} FROM `{table_name}`")
            null_row = cursor.fetchone()
            if null_row:
                for k, v in null_row.items():
                    col_name = k.replace("_nulls", "")
                    null_stats[col_name] = int(v) if v else 0

        # 6. 尝试读取 Excel
        source_path = resolve_excel_path(log_row.get("source_path", ""))
        sheet_index = log_row.get("sheet_index", 0)
        excel_data = None

        if os.path.exists(source_path):
            try:
                from services.excel_preview import list_sheets, run as preview_run

                sheet_info = list_sheets(source_path)
                sheet_names = sheet_info.get("sheet_names", []) if "error" not in sheet_info else []

                preview = preview_run(source_path, sheet_index, preview_rows=args.excel_rows or 20)
                if "error" not in preview:
                    def serialize_val(v):
                        if v is None:
                            return None
                        try:
                            import numpy as np
                            if isinstance(v, (np.integer,)):
                                return int(v)
                            if isinstance(v, (np.floating,)):
                                return float(v)
                            if isinstance(v, np.bool_):
                                return bool(v)
                        except ImportError:
                            pass
                        return v

                    preview_data = [[serialize_val(v) for v in row] for row in preview.get("preview_data", [])]

                    excel_data = {
                        "file_path": source_path,
                        "sheet_names": sheet_names,
                        "sheet_name": preview.get("sheet_name", ""),
                        "max_row": preview.get("max_row", 0),
                        "max_col": preview.get("max_col", 0),
                        "merged_count": preview.get("merged_count", 0),
                        "preview_data": preview_data,
                    }
            except Exception as e:
                excel_data = {"error": str(e)}
        else:
            excel_data = {"error": f"Excel 文件不存在: {source_path}"}

        # 7. 自动对比分析
        comparison = {
            "row_count_match": None,
            "column_count_match": None,
            "issues": [],
        }

        db_col_count = len(data_cols)
        biz_col_count = len(biz_cols)
        original_row_count = log_row.get("original_row_count", 0)
        actual_row_count = log_row.get("actual_row_count", 0)

        # 行数对比
        if excel_data and "error" not in excel_data:
            excel_data_rows = excel_data["max_row"]
            # DB 行数 vs Excel 数据行数（Excel 行数 - 表头行数约等于 DB 行数）
            comparison["excel_max_row"] = excel_data_rows
            comparison["excel_max_col"] = excel_data["max_col"]
            comparison["db_row_count"] = db_row_count
            comparison["db_data_col_count"] = db_col_count
            comparison["db_biz_col_count"] = biz_col_count

            # 列数对比（用业务列对比，排除系统追加列）
            if excel_data["max_col"] != biz_col_count:
                comparison["column_count_match"] = False
                comparison["issues"].append(
                    f"列数不一致: Excel {excel_data['max_col']}列 vs DB业务列 {biz_col_count}列 (差{biz_col_count - excel_data['max_col']})"
                )
            else:
                comparison["column_count_match"] = True

            # 检查合并单元格
            if excel_data["merged_count"] > 0:
                comparison["issues"].append(
                    f"Excel 含 {excel_data['merged_count']} 个合并单元格，需关注拆分是否正确"
                )

        # 检查空表
        if db_row_count == 0 and log_row.get("status") == "SUCCESS":
            comparison["issues"].append("空表: SUCCESS 状态但 0 行数据")

        # 检查高 null 比例列（排除系统追加列）
        high_null_cols = [k for k, v in null_stats.items()
                         if db_row_count > 0 and v / db_row_count > 0.5 and k not in SYSTEM_COLUMNS]
        if high_null_cols:
            comparison["issues"].append(
                f"高 null 率列 (>50%): {', '.join(high_null_cols)}"
            )

        # 检查 parse_log 中的 original_row_count 与 actual_row_count 差异
        if original_row_count and actual_row_count:
            diff_pct = abs(original_row_count - actual_row_count) / max(original_row_count, 1)
            if diff_pct > 0.3:
                comparison["issues"].append(
                    f"行数差异显著: 原始 {original_row_count} → 实际 {actual_row_count} ({diff_pct:.0%})"
                )

        # 检查特殊策略
        strategy = log_row.get("parse_strategy", "")
        if strategy in ("strategy_paired_row_bilingual",):
            comparison["issues"].append(f"使用特殊策略: {strategy}，需重点关注列结构")

        # 输出结果
        result = {
            "parse_log": {
                "table_name": table_name,
                "source_filename": log_row.get("source_filename", ""),
                "sheet_name": log_row.get("sheet_name", ""),
                "parse_strategy": strategy,
                "status": log_row.get("status", ""),
                "original_row_count": original_row_count,
                "actual_row_count": actual_row_count,
                "column_count": log_row.get("column_count", 0),
                "column_names": log_row.get("column_names", ""),
                "has_merged_cells": bool(log_row.get("has_merged_cells", 0)),
                "merged_cells_count": log_row.get("merged_cells_count", 0),
            },
            "db_info": {
                "row_count": db_row_count,
                "data_columns": data_cols,
                "null_stats": null_stats,
                "sample_data": sample_rows,
            },
            "excel_info": excel_data,
            "comparison": comparison,
        }

        print(json.dumps(result, ensure_ascii=False, indent=2))

    finally:
        conn.close()


# ─── 子命令: full_review ─────────────────────────────────────────

def cmd_full_review(args):
    """全量审查：一次扫描所有 SUCCESS 记录，输出紧凑审查摘要"""
    config = load_config()
    db_config = config["database"]
    database = db_config["database"]
    conn = get_connection(db_config)

    try:
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        # 获取所有 SUCCESS 记录
        cursor.execute(
            "SELECT * FROM `ods_parse_log` WHERE status = 'SUCCESS' ORDER BY id"
        )
        log_rows = cursor.fetchall()

        results = []
        for log_row in log_rows:
            log_row = serialize_row(log_row)
            table_name = log_row["table_name"]

            try:
                # 1. DB 行数
                cursor.execute(f"SELECT COUNT(*) as cnt FROM `{table_name}`")
                db_row_count = cursor.fetchone()["cnt"]

                # 2. DB 列信息
                cursor.execute(
                    "SELECT COLUMN_NAME, COLUMN_TYPE, COLUMN_COMMENT "
                    "FROM INFORMATION_SCHEMA.COLUMNS "
                    "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s "
                    "ORDER BY ORDINAL_POSITION",
                    (database, table_name),
                )
                col_rows = cursor.fetchall()
                data_cols = [c["COLUMN_NAME"] for c in col_rows if not c["COLUMN_NAME"].startswith("_")]
                # 业务数据列（排除系统追加列，用于与 Excel 列数对比）
                biz_cols = [c for c in data_cols if c not in SYSTEM_COLUMNS]
                col_info = [
                    {"name": c["COLUMN_NAME"], "type": c["COLUMN_TYPE"], "comment": c["COLUMN_COMMENT"]}
                    for c in col_rows
                ]

                # 3. 空值统计（仅记录高 null 率列）
                high_null_cols = []
                if db_row_count > 0 and data_cols:
                    col_exprs = ", ".join(
                        f"SUM(CASE WHEN `{c}` IS NULL THEN 1 ELSE 0 END) as `{c}_nulls`"
                        for c in data_cols[:30]
                    )
                    cursor.execute(f"SELECT {col_exprs} FROM `{table_name}`")
                    null_row = cursor.fetchone()
                    if null_row:
                        for k, v in null_row.items():
                            col_name = k.replace("_nulls", "")
                            null_count = int(v) if v else 0
                            null_pct = null_count / db_row_count
                            if null_pct > 0.3:
                                high_null_cols.append({
                                    "col": col_name,
                                    "null_pct": round(null_pct, 2),
                                })

                # 4. 采样首行 + 尾行（用于值验证）
                sample_head = None
                sample_tail = None
                if db_row_count > 0:
                    cursor.execute(f"SELECT * FROM `{table_name}` LIMIT 1")
                    sample_head = serialize_row(cursor.fetchone())
                    if db_row_count > 2:
                        cursor.execute(f"SELECT * FROM `{table_name}` LIMIT 1 OFFSET {db_row_count - 1}")
                        sample_tail = serialize_row(cursor.fetchone())

                # 5. Excel 快速扫描
                source_path = resolve_excel_path(log_row.get("source_path", ""))
                excel_summary = None
                excel_head_rows = None
                if os.path.exists(source_path):
                    try:
                        from services.excel_preview import list_sheets, run as preview_run
                        preview = preview_run(source_path, log_row.get("sheet_index", 0), preview_rows=5)
                        if "error" not in preview:
                            def _sv(v):
                                if v is None:
                                    return None
                                try:
                                    import numpy as np
                                    if isinstance(v, (np.integer,)):
                                        return int(v)
                                    if isinstance(v, (np.floating,)):
                                        return float(v)
                                    if isinstance(v, np.bool_):
                                        return bool(v)
                                except ImportError:
                                    pass
                                return v
                            excel_head_rows = [[_sv(v) for v in row] for row in preview.get("preview_data", [])[:5]]
                            excel_summary = {
                                "max_row": preview.get("max_row", 0),
                                "max_col": preview.get("max_col", 0),
                                "merged_count": preview.get("merged_count", 0),
                            }
                    except Exception as e:
                        excel_summary = {"error": str(e)}

                # 6. 自动问题检测
                issues = []
                severity = "ok"  # ok / warn / error

                # 空表
                if db_row_count == 0:
                    issues.append("空表: SUCCESS 状态但 0 行数据")
                    severity = "error"

                # 列数不一致（用业务列对比，排除系统追加列）
                if excel_summary and "max_col" in excel_summary:
                    excel_col_count = excel_summary["max_col"]
                    biz_col_count = len(biz_cols)
                    if excel_col_count != biz_col_count:
                        diff = biz_col_count - excel_col_count
                        issues.append(f"列数不一致: Excel {excel_col_count}列 vs DB业务列 {biz_col_count}列 (差{diff})")
                        if severity == "ok":
                            severity = "warn"

                # 行数差异
                original_rc = log_row.get("original_row_count", 0)
                actual_rc = log_row.get("actual_row_count", 0)
                if original_rc and actual_rc:
                    ratio = actual_rc / original_rc
                    if ratio < 0.5:
                        issues.append(f"行数差异大: 原始{original_rc}→实际{actual_rc} ({ratio:.0%})")
                        if severity == "ok":
                            severity = "warn"

                # 高 null 率列（排除系统追加列 data_source/col_empty，它们 100% null 是正常的）
                if high_null_cols:
                    real_null_cols = [c for c in high_null_cols if c["col"] not in SYSTEM_COLUMNS and c["null_pct"] > 0.5]
                    if real_null_cols:
                        col_strs = [f"{c['col']}({c['null_pct']:.0%})" for c in real_null_cols]
                        issues.append(f"高null率列(>50%): {', '.join(col_strs)}")
                        if severity == "ok":
                            severity = "warn"

                # 大量合并单元格
                if log_row.get("has_merged_cells") and log_row.get("merged_cells_count", 0) > 50:
                    issues.append(f"大量合并单元格({log_row['merged_cells_count']})")
                    if severity == "ok":
                        severity = "warn"

                # 特殊策略
                strategy = log_row.get("parse_strategy", "")
                if strategy in ("strategy_paired_row_bilingual",):
                    issues.append(f"特殊策略: {strategy}")
                    if severity == "ok":
                        severity = "warn"

                # 合并单元格
                if excel_summary and excel_summary.get("merged_count", 0) > 0:
                    issues.append(f"Excel含{excel_summary['merged_count']}个合并单元格，需关注拆分结果")

                # 列名全为数字（年份列名等）
                numeric_cols = [c for c in data_cols if c.isdigit()]
                if numeric_cols and len(numeric_cols) > len(data_cols) * 0.3:
                    issues.append(f"纯数字列名({len(numeric_cols)}/{len(data_cols)}): {numeric_cols[:5]}...")
                    if severity == "ok":
                        severity = "warn"

                # Excel 文件不存在
                if excel_summary is None and not os.path.exists(source_path):
                    issues.append(f"Excel文件缺失: {source_path}")

                results.append({
                    "id": log_row.get("id"),
                    "table_name": table_name,
                    "source_filename": log_row.get("source_filename", ""),
                    "sheet_name": log_row.get("sheet_name", ""),
                    "parse_strategy": strategy,
                    "original_row_count": original_rc,
                    "actual_row_count": actual_rc,
                    "db_row_count": db_row_count,
                    "db_col_count": len(data_cols),
                    "col_info": col_info,
                    "high_null_cols": high_null_cols,
                    "sample_head": sample_head,
                    "sample_tail": sample_tail,
                    "excel_summary": excel_summary,
                    "excel_head_rows": excel_head_rows,
                    "column_names": log_row.get("column_names", ""),
                    "issues": issues,
                    "severity": severity,
                })

            except Exception as e:
                results.append({
                    "table_name": table_name,
                    "error": str(e),
                    "severity": "error",
                })

        # 统计汇总
        total = len(results)
        ok_count = sum(1 for r in results if r.get("severity") == "ok")
        warn_count = sum(1 for r in results if r.get("severity") == "warn")
        error_count = sum(1 for r in results if r.get("severity") == "error")

        output = {
            "total": total,
            "statistics": {
                "ok": ok_count,
                "warn": warn_count,
                "error": error_count,
            },
            "records": results,
        }

        print(json.dumps(output, ensure_ascii=False, indent=2))

    finally:
        conn.close()


# ─── 子命令: batch_compare ───────────────────────────────────────

def cmd_batch_compare(args):
    """批量自动对比多张表（输出紧凑格式）"""
    config = load_config()
    db_config = config["database"]
    conn = get_connection(db_config)

    try:
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        # 获取 parse_log 记录
        conditions = ["status = 'SUCCESS'"]
        params = []
        if args.strategy:
            conditions.append("parse_strategy = %s")
            params.append(args.strategy)

        where_clause = f"WHERE {' AND '.join(conditions)}"
        limit = args.limit or 10

        cursor.execute(
            f"SELECT table_name, source_path, source_filename, sheet_name, sheet_index, "
            f"parse_strategy, original_row_count, actual_row_count, column_count, "
            f"has_merged_cells, merged_cells_count, column_names "
            f"FROM `ods_parse_log` {where_clause} ORDER BY id LIMIT %s",
            params + [limit],
        )
        log_rows = cursor.fetchall()

        results = []

        for log_row in log_rows:
            log_row = serialize_row(log_row)
            table_name = log_row["table_name"]

            try:
                # 快速统计
                cursor.execute(f"SELECT COUNT(*) as cnt FROM `{table_name}`")
                db_row_count = cursor.fetchone()["cnt"]

                # 空值快速检查
                cursor.execute(
                    "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                    "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND NOT COLUMN_NAME LIKE '\\_%%' "
                    "ORDER BY ORDINAL_POSITION",
                    (db_config["database"], table_name),
                )
                data_cols = [r["COLUMN_NAME"] for r in cursor.fetchall()]
                biz_cols = [c for c in data_cols if c not in SYSTEM_COLUMNS]

                # 采样 1 行
                sample = None
                if db_row_count > 0:
                    cursor.execute(f"SELECT * FROM `{table_name}` LIMIT 1")
                    sample = serialize_row(cursor.fetchone())

                # 尝试读取 Excel 概要
                source_path = resolve_excel_path(log_row.get("source_path", ""))
                excel_summary = None
                if os.path.exists(source_path):
                    try:
                        from services.excel_preview import list_sheets, run as preview_run
                        preview = preview_run(source_path, log_row.get("sheet_index", 0), preview_rows=3)
                        if "error" not in preview:
                            excel_summary = {
                                "max_row": preview.get("max_row", 0),
                                "max_col": preview.get("max_col", 0),
                                "merged_count": preview.get("merged_count", 0),
                            }
                    except Exception:
                        pass

                # 快速问题检测
                issues = []
                if db_row_count == 0:
                    issues.append("空表")
                if log_row.get("has_merged_cells") and log_row.get("merged_cells_count", 0) > 50:
                    issues.append(f"大量合并单元格({log_row['merged_cells_count']})")
                if excel_summary and excel_summary.get("max_col") != len(biz_cols):
                    issues.append(f"列数不一致(Excel:{excel_summary['max_col']} vs DB业务列:{len(biz_cols)})")
                if log_row.get("parse_strategy") in ("strategy_paired_row_bilingual",):
                    issues.append("特殊策略(paired_row)")
                if log_row.get("original_row_count", 0) > 0 and log_row.get("actual_row_count", 0) > 0:
                    ratio = log_row["actual_row_count"] / log_row["original_row_count"]
                    if ratio < 0.5:
                        issues.append(f"行数差异大({ratio:.0%})")

                results.append({
                    "table_name": table_name,
                    "source_filename": log_row.get("source_filename", ""),
                    "sheet_name": log_row.get("sheet_name", ""),
                    "parse_strategy": log_row.get("parse_strategy", ""),
                    "db_row_count": db_row_count,
                    "db_col_count": len(data_cols),
                    "excel_summary": excel_summary,
                    "issues": issues,
                    "sample": sample,
                })

            except Exception as e:
                results.append({
                    "table_name": table_name,
                    "error": str(e),
                })

        print(json.dumps(results, ensure_ascii=False, indent=2))

    finally:
        conn.close()


# ─── 主入口 ──────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="落库结果审查辅助脚本")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # summary
    p_summary = subparsers.add_parser("summary", help="输出 parse_log 紧凑汇总")
    p_summary.add_argument("--status", nargs="+", default=["SUCCESS"],
                           help="筛选状态（默认 SUCCESS）")
    p_summary.add_argument("--limit", type=int, default=200,
                           help="返回记录数上限（默认 200）")

    # parse_log
    p_log = subparsers.add_parser("parse_log", help="读取 ods_parse_log 解析日志（完整JSON）")
    p_log.add_argument("--status", nargs="+", default=["SUCCESS"],
                       help="筛选状态（默认 SUCCESS），可传多个如 --status SUCCESS ERROR")
    p_log.add_argument("--limit", type=int, default=50,
                       help="返回记录数上限（默认 50）")

    # table_info
    p_table = subparsers.add_parser("table_info", help="读取指定表的结构+样本数据")
    p_table.add_argument("table_name", help="表名")
    p_table.add_argument("--sample", type=int, default=5, help="样本行数（默认 5）")

    # excel_preview
    p_excel = subparsers.add_parser("excel_preview", help="读取 Excel 文件预览数据")
    p_excel.add_argument("file_path", help="Excel 文件路径")
    p_excel.add_argument("sheet_index", type=int, help="Sheet 序号（0-based）")
    p_excel.add_argument("--rows", type=int, default=20, help="预览行数（默认 20）")

    # list_tables
    subparsers.add_parser("list_tables", help="列出所有业务表")

    # auto_compare
    p_compare = subparsers.add_parser("auto_compare", help="自动对比单张表的 DB 与 Excel 数据")
    p_compare.add_argument("table_name", help="表名")
    p_compare.add_argument("--excel-rows", type=int, default=20, help="Excel 预览行数（默认 20）")
    p_compare.add_argument("--sample", type=int, default=3, help="DB 样本行数（默认 3）")

    # full_review
    subparsers.add_parser("full_review", help="全量审查：一次扫描所有 SUCCESS 记录")

    # batch_compare
    p_batch = subparsers.add_parser("batch_compare", help="批量自动对比多张表（紧凑格式）")
    p_batch.add_argument("--strategy", type=str, default=None,
                         help="按策略筛选（如 strategy_standard）")
    p_batch.add_argument("--anomaly-only", action="store_true",
                         help="仅输出检测到问题的表")
    p_batch.add_argument("--limit", type=int, default=10,
                         help="最多处理表数（默认 10）")

    args = parser.parse_args()

    commands = {
        "summary": cmd_summary,
        "parse_log": cmd_parse_log,
        "table_info": cmd_table_info,
        "excel_preview": cmd_excel_preview,
        "list_tables": cmd_list_tables,
        "auto_compare": cmd_auto_compare,
        "full_review": cmd_full_review,
        "batch_compare": cmd_batch_compare,
    }

    cmd_func = commands.get(args.command)
    if cmd_func:
        cmd_func(args)


if __name__ == "__main__":
    main()
