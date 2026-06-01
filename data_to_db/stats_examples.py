# -*- coding: utf-8 -*-
"""
为每种分类输出一个示例：原文件路径 + 落库表名
"""

import os
import sys
import pymysql
import yaml
from collections import defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(PROJECT_DIR, '待清洗数据')
CONFIG_PATH = os.path.join(SCRIPT_DIR, 'config.yaml')

EXCEL_EXTENSIONS = {'.xlsx', '.xls'}

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


def norm_paren(s):
    return s.replace('（', '(').replace('）', ')')


def load_db_config():
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        cfg = yaml.safe_load(f)
    return cfg['database']


def find_actual_dir(category_name):
    """查找分类对应的实际目录名"""
    cat_dir = os.path.join(DATA_DIR, category_name)
    if os.path.isdir(cat_dir):
        return category_name
    norm_cat = norm_paren(category_name)
    for name in os.listdir(DATA_DIR):
        full = os.path.join(DATA_DIR, name)
        if os.path.isdir(full) and norm_paren(name) == norm_cat:
            return name
    return None


def get_files_in_category(actual_dir_name):
    """获取分类下第一个 Excel 文件路径"""
    cat_dir = os.path.join(DATA_DIR, actual_dir_name)
    if not os.path.isdir(cat_dir):
        return None
    for root, dirs, filenames in os.walk(cat_dir):
        for fname in filenames:
            ext = os.path.splitext(fname)[1].lower()
            if ext in EXCEL_EXTENSIONS:
                return os.path.join(root, fname)
    return None


def query_example_from_log(db_cfg, actual_dir_name):
    """从 ods_parse_log 查询该分类的一个示例记录"""
    conn = pymysql.connect(
        host=db_cfg['host'], port=db_cfg['port'],
        user=db_cfg['user'], password=db_cfg['password'],
        database=db_cfg['database'], charset=db_cfg.get('charset', 'utf8mb4'),
    )
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT source_path, table_name, sheet_name, table_description "
            "FROM ods_parse_log WHERE status='SUCCESS' ORDER BY id"
        )
        rows = cur.fetchall()

        norm_dir = norm_paren(actual_dir_name)

        # 构建匹配条件：每种分类需要额外的排除规则避免跨分类误匹配
        is_zg_tjnj = '中国统计年鉴' in actual_dir_name and '农村' not in actual_dir_name  # 中国统计年鉴（不含农村）
        is_nc_tjnj = '农村统计年鉴' in actual_dir_name or '中国农村统计年鉴' in actual_dir_name  # 农村统计年鉴

        for sp, tn, sn, td in rows:
            sp_norm = norm_paren(sp.replace('\\', '/'))

            # 主匹配：完整目录名
            if norm_dir in sp_norm:
                return (sp, tn, sn, td)

            # 简称匹配 + 排除规则
            if is_zg_tjnj and '统计年鉴' in sp_norm and '农村统计年鉴' not in sp_norm:
                return (sp, tn, sn, td)
            if is_nc_tjnj and '农村统计年鉴' in sp_norm:
                return (sp, tn, sn, td)
        return None
    finally:
        conn.close()


def main():
    db_cfg = load_db_config()
    print("[INFO] 查询 ods_parse_log 示例记录...")

    print("\n" + "=" * 100)
    print(f"{'序号':<4} {'分类':<52} {'原文件路径':<55} {'落库表名':<35} {'Sheet/描述'}")
    print("=" * 100)

    for i, cat in enumerate(CATEGORIES, 1):
        actual_dir = find_actual_dir(cat)
        cat_short = cat if len(cat) <= 50 else cat[:47] + '...'

        if actual_dir is None:
            print(f"{i:<4} {cat_short:<52} {'[目录不存在]':<55} {'-':<35} -")
            continue

        # 获取第一个文件路径
        file_path = get_files_in_category(actual_dir)
        if file_path is None:
            print(f"{i:<4} {cat_short:<52} {'[无Excel文件]':<55} {'-':<35} -")
            continue

        # 缩短文件路径显示
        file_short = file_path
        if len(file_short) > 53:
            file_short = '...' + file_short[-50:]

        # 查询落库示例
        example = query_example_from_log(db_cfg, actual_dir)
        if example:
            sp, tn, sn, td = example
            desc = f"Sheet: {sn}" if sn else ""
            if td:
                desc += f" | {td}"
            if len(desc) > 40:
                desc = desc[:37] + '...'
            tn_short = tn if len(tn) <= 33 else tn[:30] + '...'
            print(f"{i:<4} {cat_short:<52} {file_short:<55} {tn_short:<35} {desc}")
        else:
            print(f"{i:<4} {cat_short:<52} {file_short:<55} {'[未找到SUCCESS记录]':<35} -")

    print("=" * 100)
    print("\n[完成]")


if __name__ == '__main__':
    main()
