"""Service: mysql_writer — 建表 + 批量写入 + 自动扩宽超长字段"""

from __future__ import annotations
import os
import re
import logging

import pymysql

from services.excel_utils import rename_id_col

logger = logging.getLogger("datadeal")

# MySQL 标识符上限
MYSQL_IDENT_MAX = 64
# 表注释上限（保守取 1024，避免不同 MySQL 版本差异）
MYSQL_TABLE_COMMENT_MAX = 1024
# 默认 VARCHAR 长度（写入超长会自动扩宽）
DEFAULT_VARCHAR_LEN = 512
# MySQL 行大小限制（不含 BLOB/TEXT），utf8mb4 下 VARCHAR(N) 占 4*N 字节
MYSQL_MAX_ROW_SIZE = 65535
# utf8mb4 每字符最大字节数
CHARSET_BYTES_PER_CHAR = 4
# 大宽表列数阈值（超过此值直接 SKIP，MySQL 行格式限制）
WIDE_TABLE_COLUMN_LIMIT = 200


def sanitize_column_name(name: str) -> str:
    """将列名转为合法的 MySQL 字段名（≤ 64 字符）。"""
    if name is None:
        return "col_unknown"
    s = str(name).strip()
    if not s:
        return "col_empty"
    # 预处理：将 ％/% 替换为语义等价的 pct，避免被正则替换为 _ 后丢失语义
    s = re.sub(r"[％%]", "pct", s)
    s = re.sub(r"[^\w\u4e00-\u9fff]", "_", s)
    s = re.sub(r"_+", "_", s)
    s = s.strip("_")
    if not s:
        return "col_empty"
    # 纯数字列名加 y_ 前缀（如 1978 → y_1978），避免 SQL 查询混淆
    if s.isdigit():
        s = "y_" + s
    if len(s) > MYSQL_IDENT_MAX:
        s = s[:MYSQL_IDENT_MAX]
    return s


def make_unique_columns(columns: list[str]) -> list[str]:
    """确保列名唯一，重复的加 _N 后缀。

    带后缀拼接后仍保证不超过 MYSQL_IDENT_MAX 字符（64），避免触发
    MySQL "Identifier name is too long" 错误。
    """
    seen: dict[str, int] = {}
    result: list[str] = []
    for col in columns:
        if col not in seen:
            seen[col] = 0
            result.append(col)
            continue
        seen[col] += 1
        suffix = f"_{seen[col]}"
        # 预留后缀长度
        base_max = MYSQL_IDENT_MAX - len(suffix)
        base = col[:base_max] if len(col) > base_max else col
        new_name = f"{base}{suffix}"
        result.append(new_name)
    return result


def _truncate_table_comment(text: str, limit: int = MYSQL_TABLE_COMMENT_MAX) -> str:
    """按字节长度截断表注释，保证 utf8mb4 字符不被截半。"""
    if not text:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    truncated = encoded[:limit]
    # 避免 utf-8 截半导致解码错误
    return truncated.decode("utf-8", errors="ignore")


def _normalize_source_file(source_file: str) -> str:
    """归一化 source_file 路径：超过 200 字符时只保留最后两级目录 + 文件名，分隔符统一为 '/'."""
    if not source_file:
        return ""
    if len(source_file) <= 200:
        return source_file.replace("\\", "/")
    parts = source_file.replace("\\", "/").rsplit("/", 3)[-3:]
    return "/".join(parts) if len(parts) > 1 else source_file


def run(table_name: str, columns: list[str], rows: list[list],
        db_config: dict, batch_size: int = 500,
        source_file: str = "", sheet_name: str = "",
        source_rel_path: str = "", column_comments: dict | None = None,
        table_description: str = "") -> dict:
    """
    建表 + 批量写入。

    参数:
        table_name: 目标表名
        columns: 列名列表（英文）
        rows: 数据行列表
        db_config: 数据库配置 {host, port, user, password, database, charset}
        batch_size: 批量写入行数
        source_file: 源文件路径（写入元数据字段）
        sheet_name: Sheet名称（写入元数据字段）
        source_rel_path: 相对于数据根目录的路径（用于表 COMMENT）
        column_comments: 英文列名→中文列名映射（用于字段 COMMENT）
        table_description: 表描述（写入 _table_description 字段和表 COMMENT）

    返回:
        {"success": bool, "table_name": str, "rows_written": int, "error": str|None}
    """
    column_comments = column_comments or {}

    # 清洗 + 唯一化 + 'id' 改名
    clean_columns = [sanitize_column_name(c) for c in columns]
    clean_columns = make_unique_columns(clean_columns)
    clean_columns = [rename_id_col(c) for c in clean_columns]

    # 大宽表检测：列数超过阈值直接返回 SKIP，避免建表失败
    if len(clean_columns) > WIDE_TABLE_COLUMN_LIMIT:
        reason = f"大宽表({len(clean_columns)}列)，超过阈值({WIDE_TABLE_COLUMN_LIMIT}列)，MySQL 不支持"
        logger.warning(f"跳过建表 table={table_name}: {reason}")
        return {
            "success": False,
            "table_name": table_name,
            "rows_written": 0,
            "error": reason,
            "skip": True,
        }

    # 重新映射 column_comments：原始列名 → 清洗后列名
    remapped_comments = {
        clean_col: column_comments[orig_col]
        for orig_col, clean_col in zip(columns, clean_columns)
        if orig_col in column_comments
    }
    column_comments = remapped_comments

    # 截断表名至 MySQL 标识符 64 字符限制
    if len(table_name) > MYSQL_IDENT_MAX:
        table_name = table_name[:MYSQL_IDENT_MAX]

    rel_source_file = _normalize_source_file(source_file)

    # 数据列 + 元数据列（_created_at 由 DEFAULT CURRENT_TIMESTAMP 填充，不在 INSERT 中显式赋值）
    data_meta_columns = ["_source_file", "_sheet_name", "_row_number", "_table_description"]
    insert_columns = clean_columns + data_meta_columns

    enriched_rows: list[list] = []
    for i, row in enumerate(rows):
        enriched = list(row)
        # 截断或补齐到数据列长度
        if len(enriched) < len(clean_columns):
            enriched += [None] * (len(clean_columns) - len(enriched))
        else:
            enriched = enriched[:len(clean_columns)]
        # 追加元数据（不含 _created_at）
        enriched.extend([rel_source_file, sheet_name, i + 1, table_description])
        enriched_rows.append(enriched)

    try:
        conn = pymysql.connect(**db_config)
        cursor = conn.cursor()
    except Exception as e:
        logger.error(f"MySQL 连接失败: {e}", exc_info=True)
        return {"success": False, "table_name": table_name, "rows_written": 0, "error": str(e)}

    try:
        # 重建表（同名表先 DROP，让 worker 决定是否走到这里）
        cursor.execute(f"DROP TABLE IF EXISTS `{table_name}`")
        conn.commit()

        # 判断是否需要用 TEXT 替代 VARCHAR：
        # MySQL 行大小限制 65535 字节（不含 BLOB/TEXT），utf8mb4 下 VARCHAR(N) 占 4*N 字节
        # 如果数据列 × VARCHAR(512) × 4 超出行限制，则全部改用 TEXT
        estimated_row_bytes = len(clean_columns) * DEFAULT_VARCHAR_LEN * CHARSET_BYTES_PER_CHAR
        use_text = estimated_row_bytes > MYSQL_MAX_ROW_SIZE
        if use_text:
            logger.info(
                f"数据列 {len(clean_columns)} × VARCHAR({DEFAULT_VARCHAR_LEN}) × {CHARSET_BYTES_PER_CHAR}B = "
                f"{estimated_row_bytes}B > {MYSQL_MAX_ROW_SIZE}B 行限制，改用 TEXT 类型"
            )

        col_defs = ["`id` INT AUTO_INCREMENT PRIMARY KEY"]
        col_type = "TEXT" if use_text else f"VARCHAR({DEFAULT_VARCHAR_LEN})"
        for col in clean_columns:
            comment = column_comments.get(col, "")
            if comment:
                safe_comment = comment.replace("'", "\\'")
                col_defs.append(f"`{col}` {col_type} COMMENT '{safe_comment}'")
            else:
                col_defs.append(f"`{col}` {col_type}")

        col_defs.append("`_source_file` VARCHAR(1024) COMMENT '源文件路径'")
        col_defs.append("`_sheet_name` VARCHAR(256) COMMENT 'Sheet名'")
        col_defs.append("`_row_number` INT COMMENT '原始行号'")
        col_defs.append("`_created_at` DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '入仓时间'")
        col_defs.append("`_table_description` TEXT COMMENT '表描述'")

        # 表级 COMMENT：表描述 + 来源路径
        comment_parts: list[str] = []
        if table_description:
            comment_parts.append(f"[LLM]{table_description}")
        if source_rel_path:
            comment_parts.append(f"来源: {source_rel_path}")
        table_comment = _truncate_table_comment("; ".join(comment_parts)).replace("'", "\\'")

        if table_comment:
            create_sql = (
                f"CREATE TABLE `{table_name}` ({', '.join(col_defs)}) "
                f"DEFAULT CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COMMENT='{table_comment}'"
            )
        else:
            create_sql = f"CREATE TABLE `{table_name}` ({', '.join(col_defs)}) DEFAULT CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC"
        cursor.execute(create_sql)
        conn.commit()

        rows_written = _batch_insert_with_auto_widen(
            cursor, table_name, insert_columns, enriched_rows, conn, batch_size,
            data_col_type=col_type
        )

        return {
            "success": True,
            "table_name": table_name,
            "rows_written": rows_written,
            "error": None,
        }
    except Exception as e:
        logger.error(f"建表/写入失败 table={table_name}: {e}", exc_info=True)
        try:
            conn.rollback()
        except Exception:
            pass
        return {
            "success": False,
            "table_name": table_name,
            "rows_written": 0,
            "error": str(e),
        }
    finally:
        try:
            cursor.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass


def _batch_insert_with_auto_widen(cursor, table_name, columns, rows, conn, batch_size=500, data_col_type="VARCHAR"):
    """批量写入，自动扩宽超长字段（TEXT 类型列跳过扩宽）。"""
    if not rows:
        return 0

    # TEXT 类型无需预检查长度和扩宽
    if data_col_type != "TEXT":
        # 预检查每列最大长度
        col_max_len = {col: 0 for col in columns}
        for row in rows:
            for col_idx, col in enumerate(columns):
                val = row[col_idx] if col_idx < len(row) else None
                if val is None:
                    continue
                length = len(str(val))
                if length > col_max_len[col]:
                    col_max_len[col] = length

        # 预扩宽（只对数据列，元数据列已在 CREATE TABLE 时定好类型）
        for col, max_len in col_max_len.items():
            if max_len > DEFAULT_VARCHAR_LEN and not col.startswith("_"):
                new_len = ((max_len // DEFAULT_VARCHAR_LEN) + 1) * DEFAULT_VARCHAR_LEN
                try:
                    _widen_column(cursor, table_name, col, new_len)
                    conn.commit()
                except Exception as e:
                    logger.warning(f"预扩宽字段失败 {table_name}.{col} → VARCHAR({new_len}): {e}")
                    try:
                        conn.rollback()
                    except Exception:
                        pass

    col_str = ", ".join([f"`{c}`" for c in columns])
    placeholders = ", ".join(["%s"] * len(columns))
    sql = f"INSERT INTO `{table_name}` ({col_str}) VALUES ({placeholders})"

    success = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        fixed_batch = []
        for row in batch:
            fixed = list(row)
            if len(fixed) < len(columns):
                fixed += [None] * (len(columns) - len(fixed))
            else:
                fixed = fixed[:len(columns)]
            fixed_batch.append(fixed)

        try:
            cursor.executemany(sql, fixed_batch)
            conn.commit()
            success += len(fixed_batch)
        except pymysql.err.DataTooLong:
            conn.rollback()
            if data_col_type == "TEXT":
                # TEXT 仍超长，尝试升级为 MEDIUMTEXT 后逐行重试
                success += _insert_one_by_one_upgrade_text(cursor, table_name, columns, fixed_batch, conn, sql)
                continue
            success += _insert_one_by_one_with_widen(cursor, table_name, columns, fixed_batch, conn, sql)
        except Exception as e:
            logger.error(
                f"批量写入失败 table={table_name} batch_start={i} batch_size={len(fixed_batch)}: {e}",
                exc_info=True,
            )
            try:
                conn.rollback()
            except Exception:
                pass

    return success


def _insert_one_by_one_upgrade_text(cursor, table_name, columns, batch, conn, sql) -> int:
    """逐行写入，遇到 DataTooLong 时将超长 TEXT 列升级为 MEDIUMTEXT。"""
    written = 0
    for row in batch:
        while True:
            try:
                cursor.execute(sql, row)
                conn.commit()
                written += 1
                break
            except pymysql.err.DataTooLong:
                conn.rollback()
                # 找到超长列，升级为 MEDIUMTEXT
                upgraded = False
                for col_idx, col in enumerate(columns):
                    if col.startswith("_"):
                        continue
                    val = row[col_idx] if col_idx < len(row) else None
                    if val is not None and len(str(val)) > 65535:
                        try:
                            upgrade_sql = f"ALTER TABLE `{table_name}` MODIFY COLUMN `{col}` MEDIUMTEXT"
                            cursor.execute(upgrade_sql)
                            conn.commit()
                            upgraded = True
                        except Exception as e:
                            logger.warning(f"升级 {table_name}.{col} 为 MEDIUMTEXT 失败: {e}")
                            conn.rollback()
                if not upgraded:
                    logger.error(f"无法定位超长字段或升级失败，跳过 table={table_name} row={row[:5]}…")
                    break
            except Exception as e:
                logger.error(f"单行写入失败 table={table_name}: {e}", exc_info=False)
                conn.rollback()
                break
    return written


def _insert_one_by_one_with_widen(cursor, table_name, columns, batch, conn, sql) -> int:
    """逐行写入，遇到 DataTooLong 时按需扩宽并重试。"""
    written = 0
    for row in batch:
        retry = 0
        while True:
            try:
                cursor.execute(sql, row)
                conn.commit()
                written += 1
                break
            except pymysql.err.DataTooLong:
                conn.rollback()
                retry += 1
                if retry > 3:
                    logger.error(
                        f"逐行写入仍超长（已重试 {retry} 次），跳过 table={table_name} row={row[:5]}…"
                    )
                    break
                widened = False
                for col_idx, col in enumerate(columns):
                    if col.startswith("_"):
                        continue
                    val = row[col_idx] if col_idx < len(row) else None
                    if val is not None and len(str(val)) > DEFAULT_VARCHAR_LEN:
                        new_len = ((len(str(val)) // DEFAULT_VARCHAR_LEN) + 1) * DEFAULT_VARCHAR_LEN
                        try:
                            _widen_column(cursor, table_name, col, new_len)
                            conn.commit()
                            widened = True
                        except Exception as e:
                            logger.warning(f"扩宽 {table_name}.{col} 失败: {e}")
                            conn.rollback()
                if not widened:
                    logger.error(f"无法定位超长字段，跳过 table={table_name} row={row[:5]}…")
                    break
            except Exception as e:
                logger.error(f"单行写入失败 table={table_name}: {e}", exc_info=False)
                conn.rollback()
                break
    return written


def _widen_column(cursor, table_name, col_name, new_length):
    """扩宽列到 VARCHAR(new_length)，若因行大小超限失败则降级为 TEXT，再失败降级为 MEDIUMTEXT。"""
    try:
        sql = f"ALTER TABLE `{table_name}` MODIFY COLUMN `{col_name}` VARCHAR({new_length})"
        cursor.execute(sql)
        return
    except Exception as e:
        logger.warning(f"扩宽为 VARCHAR({new_length}) 失败 {table_name}.{col_name}: {e}，尝试降级为 TEXT")
        try:
            cursor.connection.rollback()
        except Exception:
            pass
    # 降级为 TEXT
    try:
        sql = f"ALTER TABLE `{table_name}` MODIFY COLUMN `{col_name}` TEXT"
        cursor.execute(sql)
        return
    except Exception as e:
        logger.warning(f"降级为 TEXT 失败 {table_name}.{col_name}: {e}，尝试降级为 MEDIUMTEXT")
        try:
            cursor.connection.rollback()
        except Exception:
            pass
    # 降级为 MEDIUMTEXT
    sql = f"ALTER TABLE `{table_name}` MODIFY COLUMN `{col_name}` MEDIUMTEXT"
    cursor.execute(sql)
