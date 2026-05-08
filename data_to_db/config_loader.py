"""配置文件加载模块"""

import os
import yaml


def load_config(path: str = "config.yaml") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    # 环境变量替换：${VAR_NAME} → os.environ["VAR_NAME"]
    def _resolve_env(value):
        if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
            env_key = value[2:-1]
            return os.environ.get(env_key, "")
        return value

    # 递归替换
    def _walk(d):
        for k, v in d.items():
            if isinstance(v, dict):
                _walk(v)
            elif isinstance(v, str):
                d[k] = _resolve_env(v)

    _walk(config)
    return config

    