"""Excel 数据清洗入仓 — Agent 驱动 主入口"""

from __future__ import annotations
import os
import sys
import logging
from datetime import datetime
from logging.handlers import RotatingFileHandler

import services.xlrd_patch  # noqa: F401  必须最先 import，应用 xlrd UTF-16 容错 patch

from config_loader import load_config
from agents.orchestrator import Orchestrator


def setup_logging(config: dict) -> None:
    """根据 config.logging 配置日志：控制台 + 轮转文件"""
    log_cfg = config.get("logging", {}) or {}
    log_level = log_cfg.get("level", "INFO")
    log_file = log_cfg.get("file", "")
    max_bytes = log_cfg.get("max_bytes", 10 * 1024 * 1024)
    backup_count = log_cfg.get("backup_count", 30)

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        # 文件名支持 strftime 时间模板
        log_file = datetime.now().strftime(log_file)
        log_dir = os.path.dirname(log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        handlers.append(RotatingFileHandler(
            log_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        ))

    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
        force=True,
    )


def main() -> None:
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    config = load_config(config_path)

    # 命令行第二个参数覆盖 data_dir，方便测试
    # 用法: python main.py config.yaml ./test_data
    if len(sys.argv) > 2:
        config["scan"]["data_dir"] = sys.argv[2]

    setup_logging(config)

    Orchestrator(config).run()


if __name__ == "__main__":
    main()
