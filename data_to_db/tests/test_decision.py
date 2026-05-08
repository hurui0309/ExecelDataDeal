"""测试 agents/decision.py — Classifier 输出 schema。"""

import unittest

import _bootstrap  # noqa: F401


class TestClassifierDecision(unittest.TestCase):
    def setUp(self):
        from strategies import BUILTIN_STRATEGIES
        self.valid = set(BUILTIN_STRATEGIES.keys())

    def test_valid_strategy_passes(self):
        from agents.decision import ClassifierDecision
        raw = {
            "strategy": "strategy_standard",
            "params": {"header_start": 0, "header_end": 0},
            "table_name_hint": "ods_test",
            "confidence": 0.95,
            "reasoning": "ok",
        }
        d = ClassifierDecision.from_raw_dict(raw, self.valid)
        self.assertEqual(d.strategy, "strategy_standard")
        self.assertEqual(d.params["header_start"], 0)
        self.assertEqual(d.confidence, 0.95)
        self.assertEqual(d.error, "")

    def test_skip_unknown_allowed(self):
        from agents.decision import ClassifierDecision
        for s in ("SKIP", "UNKNOWN"):
            d = ClassifierDecision.from_raw_dict({"strategy": s}, self.valid)
            self.assertEqual(d.strategy, s)

    def test_unknown_strategy_forced_to_UNKNOWN(self):
        from agents.decision import ClassifierDecision
        d = ClassifierDecision.from_raw_dict({"strategy": "made_up"}, self.valid)
        self.assertEqual(d.strategy, "UNKNOWN")
        self.assertIn("非法 strategy", d.error)

    def test_params_null_becomes_empty_dict(self):
        from agents.decision import ClassifierDecision
        d = ClassifierDecision.from_raw_dict({
            "strategy": "strategy_standard",
            "params": None,
        }, self.valid)
        self.assertEqual(d.params, {})

    def test_params_wrong_type_becomes_empty_dict(self):
        from agents.decision import ClassifierDecision
        d = ClassifierDecision.from_raw_dict({
            "strategy": "strategy_standard",
            "params": "auto",
        }, self.valid)
        self.assertEqual(d.params, {})

    def test_confidence_clamp(self):
        from agents.decision import ClassifierDecision
        d_high = ClassifierDecision.from_raw_dict({
            "strategy": "strategy_standard",
            "confidence": 1.5,
        }, self.valid)
        self.assertEqual(d_high.confidence, 1.0)
        d_low = ClassifierDecision.from_raw_dict({
            "strategy": "strategy_standard",
            "confidence": -0.5,
        }, self.valid)
        self.assertEqual(d_low.confidence, 0.0)
        d_str = ClassifierDecision.from_raw_dict({
            "strategy": "strategy_standard",
            "confidence": "high",
        }, self.valid)
        self.assertEqual(d_str.confidence, 0.0)

    def test_regions_invalid_becomes_none(self):
        from agents.decision import ClassifierDecision
        d = ClassifierDecision.from_raw_dict({
            "strategy": "strategy_horizontal_split",
            "regions": "left|right",  # 错误类型
        }, self.valid)
        self.assertIsNone(d.regions)

    def test_regions_valid_kept(self):
        from agents.decision import ClassifierDecision
        d = ClassifierDecision.from_raw_dict({
            "strategy": "strategy_horizontal_split",
            "regions": [{"col_start": 0, "col_end": 3}],
        }, self.valid)
        self.assertEqual(len(d.regions), 1)

    def test_to_dict_omits_none_regions_and_empty_error(self):
        from agents.decision import ClassifierDecision
        d = ClassifierDecision(strategy="strategy_standard")
        out = d.to_dict()
        self.assertNotIn("regions", out)
        self.assertNotIn("error", out)
        self.assertIn("strategy", out)

    def test_get_compatibility(self):
        """旧代码 decision.get(...) 写法仍可用。"""
        from agents.decision import ClassifierDecision
        d = ClassifierDecision(strategy="strategy_standard", confidence=0.7)
        self.assertEqual(d.get("strategy"), "strategy_standard")
        self.assertEqual(d.get("confidence"), 0.7)
        self.assertEqual(d.get("nonexistent", "default"), "default")

    def test_non_dict_input(self):
        from agents.decision import ClassifierDecision
        d = ClassifierDecision.from_raw_dict("oops", self.valid)
        self.assertEqual(d.strategy, "UNKNOWN")
        self.assertIn("非 dict", d.error)


if __name__ == "__main__":
    unittest.main()
