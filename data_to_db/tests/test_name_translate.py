"""测试 services/name_translate.py 中不依赖 LLM 的纯函数。"""

import unittest

import _bootstrap  # noqa: F401


class TestFixNumericMismatches(unittest.TestCase):
    def test_year_preserved(self):
        from services.name_translate import _fix_numeric_mismatches
        # LLM 翻译时丢年份的常见 bug：1990 被丢掉
        cn = ["1990年GDP"]
        en = ["gdp"]
        out = _fix_numeric_mismatches(cn, en)
        self.assertIn("1990", out[0], f"1990 应被保留: {out!r}")

    def test_year_replaced(self):
        from services.name_translate import _fix_numeric_mismatches
        # LLM 编造了错误年份：2000 → 应被纠正为 1990
        cn = ["1990年GDP"]
        en = ["gdp_2000"]
        out = _fix_numeric_mismatches(cn, en)
        self.assertIn("1990", out[0])
        self.assertNotIn("2000", out[0])

    def test_no_numbers_kept(self):
        from services.name_translate import _fix_numeric_mismatches
        cn = ["地区"]
        en = ["region"]
        self.assertEqual(_fix_numeric_mismatches(cn, en), ["region"])

    def test_length_mismatch_returns_input(self):
        from services.name_translate import _fix_numeric_mismatches
        out = _fix_numeric_mismatches(["a", "b"], ["x"])
        self.assertEqual(out, ["x"])  # 直接返回 en，不修复


class TestFixSemanticMismatches(unittest.TestCase):
    def test_no_change_when_en_has_keyword(self):
        from services.name_translate import _fix_semantic_mismatches
        cn = ["指标"]
        en = ["indicator_name"]
        # en 已经包含 'indicator'，不应被改写
        out = _fix_semantic_mismatches(cn, en)
        self.assertEqual(out, ["indicator_name"])

    def test_skip_when_numbers_present(self):
        from services.name_translate import _fix_semantic_mismatches
        # 含数字的列由 _fix_numeric 负责，_fix_semantic 应跳过
        cn = ["1990年指标"]
        en = ["something_1990"]
        out = _fix_semantic_mismatches(cn, en)
        self.assertEqual(out, ["something_1990"])

    def test_length_mismatch_returns_input(self):
        from services.name_translate import _fix_semantic_mismatches
        out = _fix_semantic_mismatches(["a", "b"], ["x"])
        self.assertEqual(out, ["x"])


if __name__ == "__main__":
    unittest.main()
