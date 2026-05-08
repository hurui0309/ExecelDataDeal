"""测试 agents/orchestrator.py 的纯函数。"""

import unittest

import _bootstrap  # noqa: F401


class TestSafeErrorTableName(unittest.TestCase):
    def test_basic_format(self):
        from agents.orchestrator import _safe_error_table_name
        out = _safe_error_table_name("ERROR", "abc.xlsx", "Sheet1", 0)
        self.assertTrue(out.startswith("ERROR_"))
        self.assertIn("abc_xlsx", out)
        self.assertIn("Sheet1", out)
        self.assertTrue(out.endswith("_s0"))

    def test_special_chars_replaced(self):
        from agents.orchestrator import _safe_error_table_name
        out = _safe_error_table_name("SKIP", "中国/统计 年鉴.xls", "汇总(1)", 2)
        # 不应出现非法标识符字符
        for c in "/().- ":
            self.assertNotIn(c, out)
        self.assertTrue(out.endswith("_s2"))

    def test_truncated_to_64(self):
        from agents.orchestrator import _safe_error_table_name
        from services.mysql_writer import MYSQL_IDENT_MAX
        long = "a" * 200 + ".xlsx"
        out = _safe_error_table_name("ERROR", long, "verylongsheetname" * 5, 99)
        self.assertLessEqual(len(out), MYSQL_IDENT_MAX)

    def test_no_sheet_index(self):
        from agents.orchestrator import _safe_error_table_name
        out = _safe_error_table_name("UNKNOWN", "f.xlsx")
        self.assertNotIn("_s", out)


class TestEnsureUniqueTableName(unittest.TestCase):
    def test_first_call_keeps_name(self):
        from agents.orchestrator import Orchestrator
        # Orchestrator 只用 self.table_name_counter，不需要走完 __init__
        orc = Orchestrator.__new__(Orchestrator)
        orc.table_name_counter = {}
        self.assertEqual(orc._ensure_unique_table_name("ods_t1"), "ods_t1")
        self.assertEqual(orc._ensure_unique_table_name("ods_t1"), "ods_t1_1")
        self.assertEqual(orc._ensure_unique_table_name("ods_t1"), "ods_t1_2")

    def test_long_name_with_suffix_truncated(self):
        from agents.orchestrator import Orchestrator
        from services.mysql_writer import MYSQL_IDENT_MAX
        orc = Orchestrator.__new__(Orchestrator)
        orc.table_name_counter = {"x" * 64: 0}
        out = orc._ensure_unique_table_name("x" * 64)
        self.assertLessEqual(len(out), MYSQL_IDENT_MAX)
        self.assertTrue(out.endswith("_1"))


if __name__ == "__main__":
    unittest.main()
