"""测试 strategies/__init__.py 的注册表与 get_strategy。"""

import unittest

import _bootstrap  # noqa: F401


class TestStrategiesInit(unittest.TestCase):
    def test_builtin_keys_consistent(self):
        from strategies import BUILTIN_STRATEGIES, BUILTIN_DESCRIPTIONS
        self.assertEqual(set(BUILTIN_STRATEGIES.keys()), set(BUILTIN_DESCRIPTIONS.keys()),
                         "BUILTIN_STRATEGIES 与 BUILTIN_DESCRIPTIONS 必须一一对应")

    def test_get_strategy_returns_module_with_run(self):
        from strategies import get_strategy
        for name in [
            "strategy_standard",
            "strategy_simple_header",
            "strategy_multi_header",
            "strategy_horizontal_split",
            "strategy_vertical_subtable",
            "strategy_paired_row_bilingual",
        ]:
            mod = get_strategy(name)
            self.assertTrue(hasattr(mod, "run"), f"{name} 必须实现 run()")

    def test_get_strategy_caches(self):
        from strategies import get_strategy
        a = get_strategy("strategy_standard")
        b = get_strategy("strategy_standard")
        self.assertIs(a, b, "重复调用应命中缓存")

    def test_get_strategy_unknown_raises(self):
        from strategies import get_strategy
        with self.assertRaises(ValueError) as ctx:
            get_strategy("not_exist_xxx")
        # 错误信息应包含可用策略列表
        self.assertIn("available", str(ctx.exception))

    def test_list_strategies_covers_builtin(self):
        from strategies import BUILTIN_STRATEGIES, list_strategies
        scanned = list_strategies()
        for name in BUILTIN_STRATEGIES.keys():
            self.assertIn(name, scanned)


if __name__ == "__main__":
    unittest.main()
