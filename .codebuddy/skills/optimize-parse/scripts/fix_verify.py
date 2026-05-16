"""
fix_verify.py — 优化验证脚本

功能：
  1. 检查 to_fix_data 相关记录的入库状态
  2. 对每个新入库的表执行 auto_compare 自动对比
  3. 输出验证结果摘要

用法：
  python fix_verify.py
  python fix_verify.py --detail   # 输出详细对比结果
"""

import sys
import os
import json
import subprocess

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", "..", ".."))
DATA_TO_DB_DIR = os.path.join(PROJECT_ROOT, "data_to_db")
DB_READER = os.path.join(PROJECT_ROOT, ".codebuddy", "skills", "data-review", "scripts", "db_reader.py")

if DATA_TO_DB_DIR not in sys.path:
    sys.path.insert(0, DATA_TO_DB_DIR)

import yaml
import pymysql


def load_config():
    config_path = os.path.join(DATA_TO_DB_DIR, "config.yaml")
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_to_fix_records():
    """获取 ods_parse_log 中 to_fix_data 相关的记录"""
    config = load_config()
    conn = pymysql.connect(**config["database"])

    try:
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        cursor.execute(
            "SELECT id, table_name, source_filename, sheet_name, parse_strategy, "
            "status, original_row_count, actual_row_count, column_count "
            "FROM ods_parse_log WHERE source_path LIKE '%to_fix_data%' "
            "ORDER BY id"
        )
        rows = cursor.fetchall()

        # 序列化
        for r in rows:
            for k, v in r.items():
                if hasattr(v, "isoformat"):
                    r[k] = v.isoformat()

        return rows
    finally:
        conn.close()


def auto_compare_table(table_name):
    """对指定表执行 auto_compare"""
    result = subprocess.run(
        [sys.executable, DB_READER, "auto_compare", table_name],
        capture_output=True, cwd=PROJECT_ROOT
    )

    raw = result.stdout
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("gbk", errors="replace")

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"error": f"JSON parse failed: {text[:200]}"}


def main():
    import argparse
    parser = argparse.ArgumentParser(description="优化验证脚本")
    parser.add_argument("--detail", action="store_true", help="输出详细对比结果")
    args = parser.parse_args()

    records = get_to_fix_records()

    if not records:
        print("未找到 to_fix_data 相关的入库记录")
        return

    print(f"=== to_fix_data 入库验证 ===")
    print(f"记录总数: {len(records)}")
    print()

    # 统计
    by_status = {}
    for r in records:
        s = r["status"]
        by_status[s] = by_status.get(s, 0) + 1

    print("状态分布:")
    for s, c in sorted(by_status.items()):
        print(f"  {s}: {c}")
    print()

    # 逐条验证 SUCCESS 记录
    success_records = [r for r in records if r["status"] == "SUCCESS"]
    problem_records = [r for r in records if r["status"] != "SUCCESS"]

    if problem_records:
        print("非 SUCCESS 记录:")
        for r in problem_records:
            print(f"  {r['table_name']}: {r['status']} (文件: {r['source_filename']}, Sheet: {r['sheet_name']})")
        print()

    if not success_records:
        print("无 SUCCESS 记录需要验证")
        return

    print("SUCCESS 记录验证:")
    results = []

    for r in success_records:
        tn = r["table_name"]
        print(f"\n  验证: {tn}")
        print(f"    策略: {r['parse_strategy']}, 行数: {r['actual_row_count']}, 列数: {r['column_count']}")

        compare = auto_compare_table(tn)

        if "error" in compare:
            print(f"    [ERROR] auto_compare 失败: {compare['error']}")
            results.append({"table_name": tn, "status": "error", "issues": [compare["error"]]})
            continue

        issues = compare.get("comparison", {}).get("issues", [])
        severity = "ok"

        # 判断严重度
        if any("空表" in i for i in issues):
            severity = "error"
        elif any("列数不一致" in i for i in issues):
            severity = "warn"
        elif any("高null率" in i for i in issues):
            severity = "warn"
        elif issues:
            severity = "warn"

        icon = {"ok": "✅", "warn": "⚠️", "error": "❌"}.get(severity, "❓")
        print(f"    {icon} severity={severity}, issues={len(issues)}")
        for iss in issues:
            print(f"      - {iss}")

        if args.detail:
            # 输出详细对比
            db_info = compare.get("db_info", {})
            parse_log = compare.get("parse_log", {})
            excel_info = compare.get("excel_info", {})

            if db_info.get("sample_data"):
                print(f"    DB 首行样本:")
                for k, v in list(db_info["sample_data"][0].items())[:5]:
                    print(f"      {k}: {v}")

            if excel_info and "preview_data" in excel_info:
                print(f"    Excel 预览(前2行):")
                for row in excel_info["preview_data"][:2]:
                    cells = [str(v)[:20] if v is not None else "" for v in row[:8]]
                    print(f"      {', '.join(cells)}")

        results.append({
            "table_name": tn,
            "severity": severity,
            "issues": issues,
            "parse_strategy": r["parse_strategy"],
            "db_rows": r["actual_row_count"],
            "db_cols": r["column_count"],
        })

    # 汇总
    print("\n" + "=" * 60)
    print("验证汇总:")
    ok = sum(1 for r in results if r["severity"] == "ok")
    warn = sum(1 for r in results if r["severity"] == "warn")
    err = sum(1 for r in results if r["severity"] == "error")
    print(f"  ✅ OK: {ok}")
    print(f"  ⚠️ WARN: {warn}")
    print(f"  ❌ ERROR: {err}")

    if warn + err > 0:
        print("\n需关注的表:")
        for r in results:
            if r["severity"] != "ok":
                print(f"  {r['severity'].upper():5} | {r['table_name']} | {r['issues']}")


if __name__ == "__main__":
    main()
