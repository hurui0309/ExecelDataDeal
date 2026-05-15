"""
run_parse_helper.py — 运行解析辅助脚本

功能：
  1. clean-log   — 清理指定文件夹对应的 ods_parse_log 记录（可选 DROP 旧表）
  2. check-log   — 检查指定文件夹的 parse_log 记录情况
  3. summary     — 汇总指定文件夹的入库结果

用法：
  python run_parse_helper.py clean-log --folder "待清洗数据"
  python run_parse_helper.py clean-log --folder "to_fix_data" --drop-tables
  python run_parse_helper.py check-log --folder "sample_files"
  python run_parse_helper.py summary --folder "待清洗数据"
"""

import sys
import os
import argparse
import json

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", "..", ".."))
DATA_TO_DB_DIR = os.path.join(PROJECT_ROOT, "data_to_db")

if DATA_TO_DB_DIR not in sys.path:
    sys.path.insert(0, DATA_TO_DB_DIR)

import yaml
import pymysql


def load_config():
    config_path = os.path.join(DATA_TO_DB_DIR, "config.yaml")
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_connection(config=None):
    if config is None:
        config = load_config()
    return pymysql.connect(**config["database"])


def resolve_folder(folder_name):
    """解析文件夹路径，返回绝对路径和文件夹短名"""
    # 已经是绝对路径
    if os.path.isabs(folder_name):
        return folder_name, os.path.basename(folder_name.rstrip("/\\"))

    # 基于项目根目录解析
    abs_path = os.path.join(PROJECT_ROOT, folder_name)
    return abs_path, os.path.basename(folder_name.rstrip("/\\"))


def cmd_clean_log(args):
    """清理指定文件夹对应的 parse_log 记录"""
    config = load_config()
    folder_path, folder_name = resolve_folder(args.folder)
    database = config["database"]["database"]

    conn = get_connection(config)
    try:
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        # 查询匹配记录
        like_pattern = f"%{folder_name}%"
        cursor.execute(
            "SELECT id, source_path, source_filename, sheet_name, table_name, status "
            "FROM `ods_parse_log` WHERE `source_path` LIKE %s",
            (like_pattern,),
        )
        rows = cursor.fetchall()

        if not rows:
            print(f"[OK] parse_log 中无 '{folder_name}' 相关记录")
            return

        print(f"[待清理] 找到 {len(rows)} 条 '{folder_name}' 相关记录:")
        status_count = {}
        table_names = []
        for r in rows:
            status = r["status"]
            status_count[status] = status_count.get(status, 0) + 1
            if r["table_name"] and r["status"] == "SUCCESS":
                table_names.append(r["table_name"])

        for s, c in sorted(status_count.items()):
            print(f"  - {s}: {c} 条")

        # 删除记录
        cursor.execute(
            "DELETE FROM `ods_parse_log` WHERE `source_path` LIKE %s",
            (like_pattern,),
        )
        print(f"[OK] 删除 {cursor.rowcount} 条 parse_log 记录")
        conn.commit()

        # DROP 旧表
        if args.drop_tables and table_names:
            unique_tables = list(set(table_names))
            print(f"\n[DROP] 删除 {len(unique_tables)} 张旧表:")
            for tn in unique_tables:
                try:
                    cursor.execute(f"DROP TABLE IF EXISTS `{tn}`")
                    print(f"  - DROP {tn}")
                except Exception as e:
                    print(f"  - DROP 失败 {tn}: {e}")
            conn.commit()

    finally:
        conn.close()


def cmd_check_log(args):
    """检查指定文件夹的 parse_log 记录"""
    config = load_config()
    folder_path, folder_name = resolve_folder(args.folder)

    conn = get_connection(config)
    try:
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        like_pattern = f"%{folder_name}%"
        cursor.execute(
            "SELECT status, COUNT(*) as cnt FROM `ods_parse_log` "
            "WHERE `source_path` LIKE %s GROUP BY status",
            (like_pattern,),
        )
        rows = cursor.fetchall()

        if not rows:
            print(f"[OK] parse_log 中无 '{folder_name}' 相关记录，可以安全运行")
            return

        print(f"[记录] '{folder_name}' 在 parse_log 中的记录:")
        total = 0
        for r in rows:
            print(f"  - {r['status']}: {r['cnt']} 条")
            total += r["cnt"]
        print(f"  总计: {total} 条")

        if any(r["status"] == "SUCCESS" for r in rows):
            print(f"\n[注意] 有 SUCCESS 记录，运行 main.py 时这些 Sheet 会被跳过！")
            print(f"  建议先执行: python run_parse_helper.py clean-log --folder \"{folder_name}\"")

    finally:
        conn.close()


def cmd_summary(args):
    """汇总指定文件夹的入库结果"""
    config = load_config()
    folder_path, folder_name = resolve_folder(args.folder)
    database = config["database"]["database"]

    conn = get_connection(config)
    try:
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        like_pattern = f"%{folder_name}%"
        cursor.execute(
            "SELECT * FROM `ods_parse_log` WHERE `source_path` LIKE %s ORDER BY id",
            (like_pattern,),
        )
        rows = cursor.fetchall()

        if not rows:
            print(f"[空] '{folder_name}' 无入库记录")
            return

        # 统计
        status_count = {}
        for r in rows:
            status = r["status"]
            status_count[status] = status_count.get(status, 0) + 1

        print(f"\n{'='*60}")
        print(f"  入库汇总: {folder_name}")
        print(f"{'='*60}")
        print(f"  总记录数: {len(rows)}")
        for s, c in sorted(status_count.items()):
            icon = {"SUCCESS": "[OK]", "ERROR": "[ERR]", "SKIP": "[SKIP]"}.get(s, "[?]")
            print(f"  {icon} {s}: {c}")

        # 列出 ERROR 和 SKIP 的详情
        error_rows = [r for r in rows if r["status"] == "ERROR"]
        skip_rows = [r for r in rows if r["status"] == "SKIP"]

        if error_rows:
            print(f"\n--- [ERR] ERROR ({len(error_rows)} 条) ---")
            for r in error_rows[:20]:
                print(f"  {r.get('source_filename', '?')} / {r.get('sheet_name', '?')}")
                if r.get("error_message"):
                    print(f"    错误: {r['error_message'][:100]}")
            if len(error_rows) > 20:
                print(f"  ... 还有 {len(error_rows) - 20} 条")

        if skip_rows:
            print(f"\n--- [SKIP] SKIP ({len(skip_rows)} 条) ---")
            for r in skip_rows[:10]:
                print(f"  {r.get('source_filename', '?')} / {r.get('sheet_name', '?')}")
            if len(skip_rows) > 10:
                print(f"  ... 还有 {len(skip_rows) - 10} 条")

        # SUCCESS 表的行数统计
        success_rows = [r for r in rows if r["status"] == "SUCCESS"]
        if success_rows:
            print(f"\n--- [OK] SUCCESS ({len(success_rows)} 条) ---")
            total_db_rows = 0
            for r in success_rows:
                actual_rc = r.get("actual_row_count", 0) or 0
                total_db_rows += actual_rc
            print(f"  入库总行数: {total_db_rows}")

        print(f"\n{'='*60}")

    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="运行解析辅助脚本")
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # clean-log
    p_clean = subparsers.add_parser("clean-log", help="清理指定文件夹的 parse_log 记录")
    p_clean.add_argument("--folder", required=True, help="目标文件夹名或路径")
    p_clean.add_argument("--drop-tables", action="store_true", help="同时 DROP 旧 DB 表")

    # check-log
    p_check = subparsers.add_parser("check-log", help="检查指定文件夹的 parse_log 记录")
    p_check.add_argument("--folder", required=True, help="目标文件夹名或路径")

    # summary
    p_summary = subparsers.add_parser("summary", help="汇总指定文件夹的入库结果")
    p_summary.add_argument("--folder", required=True, help="目标文件夹名或路径")

    args = parser.parse_args()

    commands = {
        "clean-log": cmd_clean_log,
        "check-log": cmd_check_log,
        "summary": cmd_summary,
    }

    if args.command in commands:
        commands[args.command](args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
