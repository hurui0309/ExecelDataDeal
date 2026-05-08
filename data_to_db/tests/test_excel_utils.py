"""测试 services/excel_utils.py 的纯工具函数。"""

import unittest

import _bootstrap  # noqa: F401


class TestIsEmptyRow(unittest.TestCase):
    def test_all_none(self):
        from services.excel_utils import is_empty_row
        self.assertTrue(is_empty_row([None, None, None]))

    def test_blank_strings(self):
        from services.excel_utils import is_empty_row
        self.assertTrue(is_empty_row(["", "  ", None, "\u3000"]))  # \u3000 是全角空格
        # 我们的实现里 \u3000 不算空格（只 strip ASCII），允许它判为非空

    def test_has_value(self):
        from services.excel_utils import is_empty_row
        self.assertFalse(is_empty_row([None, "x", ""]))
        self.assertFalse(is_empty_row([0, None]))  # 数字 0 不是空


class TestIsXlsFile(unittest.TestCase):
    def test_xls(self):
        from services.excel_utils import is_xls_file
        self.assertTrue(is_xls_file("a.xls"))
        self.assertTrue(is_xls_file("path/to/B.XLS"))

    def test_xlsx_not_xls(self):
        from services.excel_utils import is_xls_file
        self.assertFalse(is_xls_file("a.xlsx"))
        self.assertFalse(is_xls_file("a.XLSX"))


class TestIsTitleRow(unittest.TestCase):
    def test_title(self):
        from services.excel_utils import is_title_row
        # 合并单元格展开后，多列同值
        self.assertTrue(is_title_row(["农村经济", "农村经济", "农村经济", None]))

    def test_not_title(self):
        from services.excel_utils import is_title_row
        self.assertFalse(is_title_row(["年份", "地区", "总计"]))
        self.assertFalse(is_title_row([None, "标题", None]))  # 只有一列
        self.assertFalse(is_title_row([None, None, None]))


class TestIsHeaderLikeRow(unittest.TestCase):
    def test_header_keyword(self):
        from services.excel_utils import is_header_like_row
        self.assertTrue(is_header_like_row(["指标", "单位", "数量"]))

    def test_year_pattern(self):
        from services.excel_utils import is_header_like_row
        self.assertTrue(is_header_like_row(["地区", "1990年", "2000年"]))

    def test_pure_data_row(self):
        from services.excel_utils import is_header_like_row
        self.assertFalse(is_header_like_row(["北京", "1234.5", "678.9", "2000"]))

    def test_empty_or_single(self):
        from services.excel_utils import is_header_like_row
        self.assertFalse(is_header_like_row([]))
        self.assertFalse(is_header_like_row(["仅一列"]))


class TestRenameIdCol(unittest.TestCase):
    def test_id_renamed(self):
        from services.excel_utils import rename_id_col
        self.assertEqual(rename_id_col("id"), "row_id")
        self.assertEqual(rename_id_col("ID"), "row_id")
        self.assertEqual(rename_id_col("Id"), "row_id")

    def test_other_kept(self):
        from services.excel_utils import rename_id_col
        self.assertEqual(rename_id_col("user_id"), "user_id")
        self.assertEqual(rename_id_col("name"), "name")


if __name__ == "__main__":
    unittest.main()
