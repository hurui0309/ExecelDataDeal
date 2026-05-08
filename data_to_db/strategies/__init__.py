"""策略包初始化 — 策略注册与加载

对外 API：
- BUILTIN_STRATEGIES : dict[str, str]
    内置策略名 → 模块路径（显式映射，是"主名单"）
- BUILTIN_DESCRIPTIONS : dict[str, str]
    内置策略名 → 详细特征描述（动态从各 strategy_*.py 的模块级 DESCRIPTION 汇总）
- get_strategy(name) -> module
    根据策略名返回模块。先查 BUILTIN_STRATEGIES，找不到再尝试 import strategies.<name>，
    便于本地临时新增/试验策略。
- list_strategies() -> set[str]
    扫描包内所有 strategy_*.py，用于诊断（"找不到策略"时打印候选名）。

新增策略的姿势：
1. 在本包下新建 strategy_xxx.py，实现 run() 函数；
2. 在文件顶部导出 ``DESCRIPTION = "..."``，会自动出现在 BUILTIN_DESCRIPTIONS；
3. （可选）在 BUILTIN_STRATEGIES 中加显式映射；不加也能被 list_strategies() 找到。
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from types import ModuleType

logger = logging.getLogger("datadeal")


# 预置策略映射（显式名单：保证内置策略优先被命中）
BUILTIN_STRATEGIES: dict[str, str] = {
    "strategy_standard": "strategies.strategy_standard",
    "strategy_simple_header": "strategies.strategy_simple_header",
    "strategy_horizontal_split": "strategies.strategy_horizontal_split",
    "strategy_multi_header": "strategies.strategy_multi_header",
    "strategy_vertical_subtable": "strategies.strategy_vertical_subtable",
    "strategy_paired_row_bilingual": "strategies.strategy_paired_row_bilingual",
}


# 运行时策略缓存（线程安全要求不高，只是避免每次重复 import_module）
_strategy_cache: dict[str, ModuleType] = {}


def get_strategy(name: str) -> ModuleType:
    """获取策略模块。

    解析顺序：
    1. 命中 _strategy_cache 直接返回
    2. 命中 BUILTIN_STRATEGIES 显式映射
    3. 兜底：尝试 import strategies.<name>（允许临时落盘策略）

    模块必须实现 run() 函数，否则视为非法策略。

    Raises:
        ValueError: 策略未找到或缺少 run()。
    """
    if name in _strategy_cache:
        return _strategy_cache[name]

    module_path = BUILTIN_STRATEGIES.get(name) or f"strategies.{name}"
    try:
        module = importlib.import_module(module_path)
    except ImportError as e:
        available = sorted(set(BUILTIN_STRATEGIES.keys()) | list_strategies())
        raise ValueError(
            f"Unknown strategy: {name!r}; available: {available} ({e})"
        ) from e

    if not hasattr(module, "run"):
        raise ValueError(f"Strategy module {module_path} missing run() function")

    _strategy_cache[name] = module
    return module


def list_strategies() -> set[str]:
    """扫描包内所有 strategy_*.py 模块名（不含扩展名）。"""
    found: set[str] = set()
    for info in pkgutil.iter_modules(__path__):
        if info.name.startswith("strategy_"):
            found.add(info.name)
    return found


def _collect_descriptions() -> dict[str, str]:
    """从所有 BUILTIN_STRATEGIES 中的策略模块汇总 DESCRIPTION。

    若某个模块没有 DESCRIPTION 常量，回退用 module.__doc__ 的第一行；
    若都没有，使用 "(no description)" 占位。
    """
    out: dict[str, str] = {}
    for name in BUILTIN_STRATEGIES.keys():
        try:
            mod = get_strategy(name)
        except Exception as e:
            logger.warning(f"汇总 DESCRIPTION 时加载 {name} 失败: {e}")
            out[name] = "(load failed)"
            continue
        desc = getattr(mod, "DESCRIPTION", None)
        if not desc and mod.__doc__:
            # 取 docstring 第一行
            desc = mod.__doc__.strip().splitlines()[0].strip()
        out[name] = desc or "(no description)"
    return out


# 模块加载时一次性收集；之后所有调用方读到的都是同一份
BUILTIN_DESCRIPTIONS: dict[str, str] = _collect_descriptions()


__all__ = [
    "BUILTIN_STRATEGIES",
    "BUILTIN_DESCRIPTIONS",
    "get_strategy",
    "list_strategies",
]
