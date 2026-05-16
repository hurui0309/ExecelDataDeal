"""
fix_prepare.py — 优化测试准备脚本

功能：
  1. 清空 to_fix_data/ 目录
  2. 从 sample_files/ 复制指定文件到 to_fix_data/
  3. 清理 ods_parse_log 中 to_fix_data 相关记录

用法：
  # 从 review 报告自动提取问题文件并准备
  python fix_prepare.py --from-report ../review_reports/review_XXXXXXXX_XXXXXX.md

  # 手动指定文件名（支持模糊匹配）
  python fix_prepare.py --files "粮食播种" "统计年鉴"

  # 清理 parse_log + DROP 旧表
  python fix_prepare.py --from-report ../review_reports/review_XXXXXXXX_XXXXXX.md --drop-tables

  # 仅清理 parse_log，不复制文件
  python fix_prepare.py --clean-log-only --files "粮食播种"
"""

import sys
import os
import re
import shutil
import argparse
import glob

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", "..", ".."))
DATA_TO_DB_DIR = os.path.join(PROJECT_ROOT, "data_to_db")
TO_FIX_DIR = os.path.join(PROJECT_ROOT, "to_fix_data")
SAMPLE_DIR = os.path.join(PROJECT_ROOT, "sample_files")

if DATA_TO_DB_DIR not in sys.path:
    sys.path.insert(0, DATA_TO_DB_DIR)

import yaml
import pymysql


def load_config():
    config_path = os.path.join(DATA_TO_DB_DIR, "config.yaml")
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def clear_to_fix_data():
    """清空 to_fix_data 目录"""
    if not os.path.exists(TO_FIX_DIR):
        os.makedirs(TO_FIX_DIR, exist_ok=True)
        print(f"[OK] 创建目录: {TO_FIX_DIR}")
        return

    count = 0
    for item in os.listdir(TO_FIX_DIR):
        item_path = os.path.join(TO_FIX_DIR, item)
        if os.path.isfile(item_path):
            os.remove(item_path)
            count += 1
        elif os.path.isdir(item_path):
            shutil.rmtree(item_path)
            count += 1
    print(f"[OK] 清空 to_fix_data/: 删除 {count} 个项目")


def find_in_sample_files(filenames):
    """在 sample_files/ 下查找匹配的文件，支持模糊匹配"""
    if not os.path.exists(SAMPLE_DIR):
        print(f"[ERROR] sample_files 目录不存在: {SAMPLE_DIR}")
        return []

    available = os.listdir(SAMPLE_DIR)
    results = []

    for target in filenames:
        # 精确匹配
        exact = os.path.join(SAMPLE_DIR, target)
        if os.path.exists(exact):
            results.append(exact)
            continue

        # 模糊匹配：target 中的关键词
        matches = []
        for f in available:
            if f.endswith((".xlsx", ".xls")) and target in f:
                matches.append(os.path.join(SAMPLE_DIR, f))

        if len(matches) == 1:
            results.append(matches[0])
        elif len(matches) > 1:
            # 多个匹配，选择最短的（最精确的）
            matches.sort(key=lambda x: len(os.path.basename(x)))
            results.append(matches[0])
            print(f"  [多匹配] '{target}' 匹配到 {len(matches)} 个文件，选择: {os.path.basename(matches[0])}")
        else:
            print(f"  [未找到] '{target}' 在 sample_files/ 中无匹配")

    return results


def extract_filenames_from_report(report_path):
    """从 review 报告中提取问题记录的源文件名"""
    with open(report_path, "r", encoding="utf-8") as f:
        content = f.read()

    filenames = set()

    # 匹配 "源文件: xxx.xlsx" 格式
    pattern = r'\*\*源文件\*\*:\s*(.+?)(?:\n|$)'
    for match in re.finditer(pattern, content):
        fname = match.group(1).strip()
        if fname:
            filenames.add(fname)

    # 匹配问题表格中的文件名
    pattern2 = r'\|[^|]*\|([^|]+\.xlsx?)\|'
    for match in re.finditer(pattern2, content):
        fname = match.group(1).strip()
        if fname:
            filenames.add(fname)

    return list(filenames)


def copy_to_fix_data(file_paths):
    """复制文件到 to_fix_data/"""
    copied = 0
    for src in file_paths:
        if not os.path.exists(src):
            print(f"  [跳过] 文件不存在: {src}")
            continue
        dst = os.path.join(TO_FIX_DIR, os.path.basename(src))
        shutil.copy2(src, dst)
        print(f"  [复制] {os.path.basename(src)}")
        copied += 1
    print(f"[OK] 复制 {copied} 个文件到 to_fix_data/")


def clean_parse_log(drop_tables=False):
    """清理 ods_parse_log 中 to_fix_data 相关记录"""
    config = load_config()
    conn = pymysql.connect(**config["database"])

    try:
        cursor = conn.cursor()

        # 查询要删除的记录
        cursor.execute(
            "SELECT table_name, source_filename, status FROM ods_parse_log "
            "WHERE source_path LIKE '%to_fix_data%'"
        )
        rows = cursor.fetchall()
        if not rows:
            print("[OK] parse_log 中无 to_fix_data 相关记录")
            return []

        print(f"[待清理] parse_log 中 {len(rows)} 条 to_fix_data 记录:")
        table_names = []
        for r in rows:
            print(f"  - {r[0]} ({r[1]}) [{r[2]}]")
            table_names.append(r[0])

        # 删除记录
        cursor.execute(
            "DELETE FROM ods_parse_log WHERE source_path LIKE '%to_fix_data%'"
        )
        print(f"[OK] 删除 {cursor.rowcount} 条 parse_log 记录")
        conn.commit()

        # DROP 旧表
        if drop_tables and table_names:
            database = config["database"]["database"]
            for tn in table_names:
                try:
                    cursor.execute(f"DROP TABLE IF EXISTS `{tn}`")
                    print(f"  [DROP] {tn}")
                except Exception as e:
                    print(f"  [DROP失败] {tn}: {e}")
            conn.commit()

        return table_names

    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="优化测试准备脚本")
    parser.add_argument("--from-report", type=str, default=None,
                        help="从 review 报告提取问题文件")
    parser.add_argument("--files", nargs="+", default=None,
                        help="手动指定文件名（支持模糊匹配）")
    parser.add_argument("--drop-tables", action="store_true",
                        help="同时 DROP 旧 DB 表")
    parser.add_argument("--clean-log-only", action="store_true",
                        help="仅清理 parse_log，不复制文件")
    parser.add_argument("--no-clean", action="store_true",
                        help="不清空 to_fix_data（追加模式）")

    args = parser.parse_args()

    if not args.from_report and not args.files:
        print("[ERROR] 请指定 --from-report 或 --files")
        return

    # Step 1: 清空 to_fix_data
    if not args.no_clean:
        clear_to_fix_data()

    # Step 2: 收集文件
    filenames = []
    if args.from_report:
        report_path = args.from_report
        if not os.path.isabs(report_path):
            report_path = os.path.join(PROJECT_ROOT, report_path)
        if not os.path.exists(report_path):
            print(f"[ERROR] 报告文件不存在: {report_path}")
            return
        filenames = extract_filenames_from_report(report_path)
        print(f"[报告] 从 {os.path.basename(report_path)} 提取到 {len(filenames)} 个文件名")

    if args.files:
        filenames.extend(args.files)

    # Step 3: 查找并复制文件
    if not args.clean_log_only:
        file_paths = find_in_sample_files(filenames)
        if file_paths:
            copy_to_fix_data(file_paths)
        else:
            print("[WARN] 未找到任何匹配文件")

    # Step 4: 清理 parse_log
    clean_parse_log(drop_tables=args.drop_tables)


if __name__ == "__main__":
    main()
