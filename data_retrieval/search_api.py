"""第三阶段：智能检索 API

基于 Flask 提供 HTTP 接口，供前端或外部系统调用。

启动方式:
    python search_api.py                          # 默认端口 5100
    python search_api.py --port 8080              # 指定端口
    python search_api.py --config phase2_config.yaml --port 5100

API 接口:
    POST /api/search
        Body: {"query": "帮我查1985年各地区社会商品零售总额", "top_k": 5}
        返回: {"results": [...], "total": 5, "took_ms": 1234}

    GET /api/categories
        返回所有分类索引

    GET /api/keywords?q=粮食
        关键词搜索提示

    GET /api/health
        健康检查
"""

from __future__ import annotations

import json
import os
import sys
import time
import argparse
import logging
from flask import Flask, request, jsonify

_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA_TO_DB = os.path.join(_PROJ_ROOT, "data_to_db")
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)
if _DATA_TO_DB not in sys.path:
    sys.path.insert(0, _DATA_TO_DB)

from phase3_search import Phase3Search, load_config

logger = logging.getLogger("search_api")

app = Flask(__name__)
search_engine: Phase3Search = None
config: dict = None


def init_search_engine(config_path: str):
    global search_engine, config
    config = load_config(config_path)
    search_engine = Phase3Search(
        db_config=config["database"],
        llm_config=config.get("llm"),
    )
    logger.info("搜索引擎已初始化")


# ────────────────────────── API 路由 ──────────────────────────

@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "timestamp": time.time()})


@app.route("/api/search", methods=["POST"])
def search():
    data = request.get_json(force=True, silent=True) or {}
    query = data.get("query", "").strip()
    if not query:
        return jsonify({"error": "query 参数不能为空"}), 400

    top_k = min(data.get("top_k", 10), 50)

    t0 = time.time()
    results = search_engine.search(query, top_k=top_k)
    took_ms = int((time.time() - t0) * 1000)

    return jsonify({
        "results": search_engine.format_results_json(results),
        "total": len(results),
        "took_ms": took_ms,
        "query": query,
    })


@app.route("/api/categories", methods=["GET"])
def categories():
    import pymysql
    conn = pymysql.connect(**config["database"])
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT `category_path`, `category_name`, `category_level`, "
            "`sheet_count` FROM `ods_category_index` ORDER BY `category_path`"
        )
        rows = cursor.fetchall()
        cats = [
            {"path": r[0], "name": r[1], "level": r[2], "count": r[3]}
            for r in rows
        ]
        return jsonify({"categories": cats, "total": len(cats)})
    finally:
        conn.close()


@app.route("/api/keywords", methods=["GET"])
def keywords():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"error": "q 参数不能为空"}), 400

    import pymysql
    conn = pymysql.connect(**config["database"])
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT `keyword`, `doc_freq` FROM `ods_keyword_index` "
            "WHERE `keyword_norm` LIKE %s ORDER BY `doc_freq` DESC LIMIT 20",
            (f"%{q.lower()}%",),
        )
        rows = cursor.fetchall()
        kws = [{"keyword": r[0], "doc_freq": r[1]} for r in rows]
        return jsonify({"keywords": kws, "total": len(kws)})
    finally:
        conn.close()


@app.route("/api/synonyms", methods=["GET"])
def synonyms():
    q = request.args.get("q", "").strip()
    import pymysql
    conn = pymysql.connect(**config["database"])
    try:
        cursor = conn.cursor()
        if q:
            cursor.execute(
                "SELECT `canonical_name`, `synonyms`, `confidence`, `doc_count` "
                "FROM `ods_field_synonym` WHERE `canonical_name` LIKE %s "
                "OR `synonyms` LIKE %s ORDER BY `doc_count` DESC LIMIT 20",
                (f"%{q}%", f"%{q}%"),
            )
        else:
            cursor.execute(
                "SELECT `canonical_name`, `synonyms`, `confidence`, `doc_count` "
                "FROM `ods_field_synonym` ORDER BY `doc_count` DESC LIMIT 50"
            )
        rows = cursor.fetchall()
        syns = []
        for r in rows:
            try:
                syn_list = json.loads(r[1]) if isinstance(r[1], str) else r[1]
            except Exception:
                syn_list = []
            syns.append({
                "canonical": r[0], "synonyms": syn_list,
                "confidence": r[2], "doc_count": r[3],
            })
        return jsonify({"synonyms": syns, "total": len(syns)})
    finally:
        conn.close()


# ────────────────────────── 入口 ──────────────────────────

def main():
    parser = argparse.ArgumentParser(description="数据检索系统 API 服务")
    parser.add_argument("--port", type=int, default=5100, help="服务端口 (默认 5100)")
    parser.add_argument("--config", type=str, default="phase2_config.yaml", help="配置文件路径")
    parser.add_argument("--debug", action="store_true", help="调试模式")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    init_search_engine(args.config)

    logger.info(f"API 服务启动: http://0.0.0.0:{args.port}")
    logger.info(f"API 文档:")
    logger.info(f"  POST /api/search       - 智能检索")
    logger.info(f"  GET  /api/categories   - 分类索引")
    logger.info(f"  GET  /api/keywords?q=  - 关键词提示")
    logger.info(f"  GET  /api/synonyms?q=  - 同义词查询")
    logger.info(f"  GET  /api/health       - 健康检查")

    app.run(host="0.0.0.0", port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
