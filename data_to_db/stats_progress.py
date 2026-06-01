# -*- coding: utf-8 -*-
"""
数据解析进度统计脚本
统计各分类文件的解析进度（基于 ods_parse_log 记录）
"""

import os
import sys
import pymysql
import yaml
from collections import defaultdict

# ============ 配置 ============
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(PROJECT_DIR, '待清洗数据')
CONFIG_PATH = os.path.join(SCRIPT_DIR, 'config.yaml')

EXCEL_EXTENSIONS = {'.xlsx', '.xls'}

# ============ 分类列表（用户指定） ============
CATEGORIES = [
    "全国各省份价格指数-农村居民消费价格分类指数（上年=100）-粮食类农村居民消费价格指数（上年=100）（1997-2019年）",
    "全国各省份价格指数-居民消费价格分类指数（上年=100)（-2015）-粮食类居民消费价格指数（上年=100）（1994-2015年）",
    "全国各省份价格指数-农村商品零售价格指数（上年=100）-粮食类农村商品零售价格指数（上年=100）（2003-2016年）",
    "全国各省份人民生活-城镇居民主要食品消费量-城镇居民人均粮食消费量（2015-2021年）",
    "全国各省份农业-主要农作物播种面积-粮食作物播种面积（1990-2020年）",
    "全国各省份农业-农村居民家庭平均每人出售主要农产品-农村居民人均粮食出售量（2000-2012年）",
    "全国各省份农业-主要农作物产品产量-夏收粮食产量（1990-2019年）",
    "全国各省份人民生活-全体居民主要食品消费量-居民人均粮食消费量（2015-2021年）",
    "全国各省份农业-主要农作物单位面积产量-粮食单位面积产量（1990-2021年）",
    "全国各省份农业-主要农作物播种面积-秋收粮食播种面积（1990-2019年）",
    "全国各省份农业-主要农作物产品产量-粮食产量（1990-2021年）",
    "全国各省份价格指数-居民消费价格分类指数（上年=100)（2016-）-粮食类居民消费价格指数（上年=100）（1994-2015年）",
    "全国各省份农业-平均每一农业劳动力生产的主要农产品-劳均粮食产量（2001-2012年）",
    "全国各省份农业-按人口平均的主要农产品产量-人均粮食产量（1996-2021年）",
    "全国各省份价格指数-商品零售价格分类指数（上年=100）-粮食类商品零售价格指数（上年=100）（1990-2021年）",
    "全国各省份农业-主要农作物单位面积产量-夏收粮食单位面积产量（1990-2019年）",
    "全国各省份农业-主要农作物播种面积-夏收粮食播种面积（1990-2019年）",
    "地级市-粮食产量（1990-2018年）",
    "县域粮食总产量（2000-2020年）",
    "全国各城市-主要农产品产量-粮食产量（1999-2020年）",
    "中国地区粮食播种、粮食产量、灾害等数据（1990-2023年）",
    "地级市-粮食产量、农作物播种面积等农业相关数据（2013-2022年）",
    "地级市-粮食安全数据（2000-2024年）",
    "中国统计年鉴（1949-2025年）EXCEL版本",
    "中国农村统计年鉴-Excel版（1985-2024年）",
    "全国经营主体数据（更新至2025年12月）",
]


def norm_paren(s: str) -> str:
    """统一把中文括号替换为英文括号，便于匹配"""
    return s.replace('（', '(').replace('）', ')')


def load_db_config():
    """从 config.yaml 加载数据库配置"""
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        cfg = yaml.safe_load(f)
    return cfg['database']


def scan_category_files(category_name: str):
    """
    扫描指定分类目录下所有 Excel 文件
    返回: (files_dict, actual_dir_name_or_None)
      files_dict: { 相对文件路径: 文件大小(bytes) }
    """
    cat_dir = os.path.join(DATA_DIR, category_name)
    actual_dir_name = category_name

    if not os.path.isdir(cat_dir):
        # 尝试近似匹配（括号归一化后对比）
        norm_cat = norm_paren(category_name)
        found = None
        for name in os.listdir(DATA_DIR):
            full = os.path.join(DATA_DIR, name)
            if os.path.isdir(full) and norm_paren(name) == norm_cat:
                found = name
                break
        if found:
            cat_dir = os.path.join(DATA_DIR, found)
            actual_dir_name = found
        else:
            return {}, None  # 目录不存在

    files = {}
    for root, dirs, filenames in os.walk(cat_dir):
        for fname in filenames:
            ext = os.path.splitext(fname)[1].lower()
            if ext in EXCEL_EXTENSIONS:
                full = os.path.join(root, fname)
                rel = os.path.relpath(full, DATA_DIR)
                size = os.path.getsize(full)
                files[rel] = size
    return files, actual_dir_name


def query_ods_parse_log(db_cfg):
    """
    查询 ods_parse_log 全部记录
    返回: {
        source_path_normalized: {
            'sheets_total': int,
            'sheets_success': int,
            'sheets_error': int,
            'sheets_skip': int,
        }
    }
    """
    conn = pymysql.connect(
        host=db_cfg['host'],
        port=db_cfg['port'],
        user=db_cfg['user'],
        password=db_cfg['password'],
        database=db_cfg['database'],
        charset=db_cfg.get('charset', 'utf8mb4'),
        cursorclass=pymysql.cursors.Cursor,
    )
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT source_path, source_filename, sheet_name, status "
            "FROM ods_parse_log"
        )
        rows = cur.fetchall()

        log_map = defaultdict(lambda: {
            'sheets_total': 0,
            'sheets_success': 0,
            'sheets_error': 0,
            'sheets_skip': 0,
        })

        for sp, sfn, sn, st in rows:
            # 统一路径分隔符，取文件名部分用于匹配
            norm_sp = sp.replace('\\', '/')
            entry = log_map[norm_sp]
            entry['sheets_total'] += 1
            if st == 'SUCCESS':
                entry['sheets_success'] += 1
            elif st == 'ERROR':
                entry['sheets_error'] += 1
            elif st == 'SKIP':
                entry['sheets_skip'] += 1

        return dict(log_map)
    finally:
        conn.close()


def match_file_to_log(file_rel_path, log_map, category_dir_name):
    """
    将待清洗数据中的文件路径与 ods_parse_log 中的 source_path 进行匹配。

    匹配策略：
    1. 直接包含匹配：log 的 source_path 包含 file_rel_path 的归一化版本
       （处理 ../待清洗数据/ 来源路径）
    2. 扁平化匹配：sample_files 扁平化命名 = {dir_name}_{original_filename}.xlsx
       检测 log_path 是否同时包含 dir_name 和 file_name（作为前缀）
    3. 精确文件名匹配（回退）
    """
    norm_file = file_rel_path.replace('\\', '/')
    file_name = os.path.basename(norm_file)
    norm_filename = norm_paren(file_name)
    norm_dirname = norm_paren(category_dir_name)

    for log_path, log_info in log_map.items():
        norm_log = log_path.replace('\\', '/')

        # 策略1: 直接包含（../待清洗数据/ 原始路径）
        if norm_file in norm_log:
            return log_info

        # 策略2: 扁平化 sample_files 命名匹配
        # flat pattern: {dir_name}_{file_name}
        # 检查 log 路径同时包含 dir_name 和 file_name
        log_norm_paren = norm_paren(norm_log)
        if norm_dirname in log_norm_paren and norm_filename in log_norm_paren:
            return log_info

        # 策略3: 文件名精确匹配（回退）
        log_fname = os.path.basename(norm_log)
        if norm_paren(log_fname) == norm_filename:
            return log_info

    return None


def is_file_parsed(log_info):
    """文件是否已完全解析（所有 sheet 均为 SUCCESS）"""
    if log_info is None:
        return False
    return log_info['sheets_success'] > 0 and log_info['sheets_error'] == 0


def is_file_partial(log_info):
    """文件是否部分解析（有 SUCCESS 但也有 ERROR/SKIP，或全部 SKIP）"""
    if log_info is None:
        return False
    return log_info['sheets_success'] > 0 and (
        log_info['sheets_error'] > 0 or log_info['sheets_skip'] > 0
    )


def main():
    print("=" * 90)
    print("  数据解析进度统计")
    print("=" * 90)

    # 1. 加载数据库配置
    try:
        db_cfg = load_db_config()
        print(f"\n[数据库] {db_cfg['host']}:{db_cfg['port']}/{db_cfg['database']}")
    except Exception as e:
        print(f"\n[错误] 无法加载数据库配置: {e}")
        sys.exit(1)

    # 2. 查询 ods_parse_log
    print("[INFO] 正在查询 ods_parse_log ...")
    log_map = query_ods_parse_log(db_cfg)
    print(f"[INFO] ods_parse_log 共 {len(log_map)} 条唯一文件记录")

    # 3. 按分类统计
    grand_total_files = 0
    grand_total_sheets = 0
    grand_parsed_files = 0
    grand_partial_files = 0
    grand_success_sheets = 0

    results = []

    for cat in CATEGORIES:
        files, actual_dir_name = scan_category_files(cat)

        if actual_dir_name is None:
            # 目录不存在
            results.append({
                'category': cat,
                'dir_exists': False,
                'file_count': 0,
                'parsed': 0,
                'partial': 0,
                'total_sheets': 0,
                'success_sheets': 0,
            })
            continue

        total_files = len(files)
        parsed_count = 0
        partial_count = 0
        total_sheets = 0
        success_sheets = 0

        for rel_path in files:
            log_info = match_file_to_log(rel_path, log_map, actual_dir_name)
            if log_info:
                if is_file_parsed(log_info):
                    parsed_count += 1
                elif is_file_partial(log_info):
                    partial_count += 1
                total_sheets += log_info['sheets_total']
                success_sheets += log_info['sheets_success']

        results.append({
            'category': cat,
            'dir_exists': True,
            'file_count': total_files,
            'parsed': parsed_count,
            'partial': partial_count,
            'total_sheets': total_sheets,
            'success_sheets': success_sheets,
        })

        grand_total_files += total_files
        grand_parsed_files += parsed_count
        grand_partial_files += partial_count
        grand_total_sheets += total_sheets
        grand_success_sheets += success_sheets

    # 4. 输出结果
    print("\n" + "=" * 90)
    print("  各类别统计明细")
    print("=" * 90)
    print(f"{'序号':<4} {'分类名称':<50} {'文件总数':>6} {'已解析':>6} {'部分解析':>6} {'未解析':>6} {'进度':>7}")
    print("-" * 90)

    for i, r in enumerate(results, 1):
        cat_short = r['category']
        if len(cat_short) > 48:
            cat_short = cat_short[:45] + '...'

        if not r['dir_exists']:
            print(f"{i:<4} {cat_short:<50} {'N/A':>6} {'N/A':>6} {'N/A':>6} {'N/A':>6} {'目录不存在':>7}")
            continue

        unparsed = r['file_count'] - r['parsed'] - r['partial']
        if r['file_count'] > 0:
            pct = (r['parsed'] + r['partial']) / r['file_count'] * 100
            progress_str = f"{pct:.1f}%"
        else:
            progress_str = "N/A"

        print(f"{i:<4} {cat_short:<50} {r['file_count']:>6} {r['parsed']:>6} "
              f"{r['partial']:>6} {unparsed:>6} {progress_str:>7}")

    # 5. 总体汇总
    print("\n" + "=" * 90)
    print("  总体汇总")
    print("=" * 90)

    grand_unparsed = grand_total_files - grand_parsed_files - grand_partial_files
    overall_pct = (grand_parsed_files + grand_partial_files) / grand_total_files * 100 if grand_total_files > 0 else 0

    print(f"  总分类数:           {len([r for r in results if r['dir_exists']])} 个")
    missing = [r['category'] for r in results if not r['dir_exists']]
    if missing:
        print(f"  缺失目录:           {len(missing)} 个 → {', '.join(missing)}")

    print(f"  总文件数:           {grand_total_files}")
    print(f"  已完全解析文件:     {grand_parsed_files}  ({grand_parsed_files / grand_total_files * 100:.1f}%)" if grand_total_files > 0 else "")
    print(f"  部分解析文件:       {grand_partial_files}  ({grand_partial_files / grand_total_files * 100:.1f}%)" if grand_total_files > 0 else "")
    print(f"  未解析文件:         {grand_unparsed}  ({grand_unparsed / grand_total_files * 100:.1f}%)" if grand_total_files > 0 else "")
    print(f"  总体进度(文件维度): {overall_pct:.1f}% ({grand_parsed_files + grand_partial_files}/{grand_total_files})")

    if grand_total_sheets > 0:
        print(f"\n  --- Sheet 维度 ---")
        print(f"  总 Sheet 数:        {grand_total_sheets}")
        print(f"  SUCCESS Sheet:      {grand_success_sheets}  ({grand_success_sheets / grand_total_sheets * 100:.1f}%)")

    # 6. 按分类的解析进度
    print("\n" + "=" * 90)
    print("  各类别解析进度详情（文件级）")
    print("=" * 90)

    for i, r in enumerate(results, 1):
        if not r['dir_exists']:
            print(f"  {i}. [{r['category']}]\n     → 目录不存在，无法统计")
            continue

        total = r['file_count']
        p = r['parsed']
        pp = r['partial']
        up = total - p - pp

        cat_name = r['category']
        if len(cat_name) > 65:
            cat_name = cat_name[:62] + '...'

        print(f"  {i}. {cat_name}")
        print(f"     文件总数: {total} | 已解析: {p} | 部分解析: {pp} | 未解析: {up} | "
              f"进度: {(p + pp) / total * 100:.1f}%" if total > 0 else f"     文件总数: 0")

    print("\n" + "=" * 90)
    print("  统计完成")
    print("=" * 90)


if __name__ == '__main__':
    main()
