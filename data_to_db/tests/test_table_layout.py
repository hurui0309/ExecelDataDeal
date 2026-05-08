"""测试 services/table_layout.py — 表格结构通用工具。"""

import unittest

import _bootstrap  # noqa: F401


class TestCleanCell(unittest.TestCase):
    def test_none_and_empty(self):
        from services.table_layout import clean_cell
        self.assertIsNone(clean_cell(None))
        self.assertIsNone(clean_cell(""))
        self.assertIsNone(clean_cell("   "))
        self.assertIsNone(clean_cell("nan"))
        self.assertIsNone(clean_cell("NaN"))

    def test_thousand_separators(self):
        from services.table_layout import clean_cell
        # 逗号
        self.assertEqual(clean_cell("1,234"), "1234")
        self.assertEqual(clean_cell("1,234.56"), "1234.56")
        # 空格
        self.assertEqual(clean_cell("1 234"), "1234")
        # 不像数字的逗号字符串保留原样（去除两端空格）
        self.assertEqual(clean_cell("a,b"), "a,b")

    def test_keep_pure_text(self):
        from services.table_layout import clean_cell
        self.assertEqual(clean_cell("北京"), "北京")
        self.assertEqual(clean_cell("hello world"), "hello world")


class TestNormalizeCellText(unittest.TestCase):
    def test_compress_inner_spaces(self):
        from services.table_layout import normalize_cell_text
        self.assertEqual(normalize_cell_text("  人口    (万人)  "), "人口 (万人)")
        self.assertIsNone(normalize_cell_text(None))
        self.assertIsNone(normalize_cell_text("   "))


class TestCountLeadingSpaces(unittest.TestCase):
    def test_count(self):
        from services.table_layout import count_leading_spaces
        self.assertEqual(count_leading_spaces("foo"), 0)
        self.assertEqual(count_leading_spaces("  foo"), 2)
        self.assertEqual(count_leading_spaces("    foo bar"), 4)
        self.assertEqual(count_leading_spaces(None), 0)


class TestCleanHeaderSpaces(unittest.TestCase):
    def test_remove_inter_digit_spaces(self):
        from services.table_layout import clean_header_spaces
        self.assertEqual(clean_header_spaces("1 9 8 6年"), "1986年")
        self.assertEqual(clean_header_spaces("2 0 6 7"), "2067")
        # 非数字间空格保留
        self.assertEqual(clean_header_spaces("hello world"), "hello world")


class TestTrimTrailingEmptyCols(unittest.TestCase):
    def test_trim(self):
        from services.table_layout import trim_trailing_empty_cols
        data = [
            ["a", "b", "c", None, ""],
            ["d", "e", None, None, ""],
        ]
        out = trim_trailing_empty_cols(data)
        self.assertEqual(out, [["a", "b", "c"], ["d", "e", None]])


class TestFootnotes(unittest.TestCase):
    def test_basic_patterns(self):
        from services.table_layout import is_footnote_row
        self.assertTrue(is_footnote_row(["注：本表数据来自XXX"]))
        self.assertTrue(is_footnote_row(["备注: hello"]))
        self.assertTrue(is_footnote_row(["Note: this is a note"]))
        self.assertTrue(is_footnote_row(["资料来源:国家统计局"]))
        self.assertTrue(is_footnote_row(["数据来源:XXX"]))
        self.assertTrue(is_footnote_row(["Source: XXX"]))
        self.assertTrue(is_footnote_row(["① 占总人口的比重"]))
        self.assertTrue(is_footnote_row(["* 含港澳"]))
        self.assertTrue(is_footnote_row(["★ 关键数据"]))

    def test_data_row_not_footnote(self):
        from services.table_layout import is_footnote_row
        self.assertFalse(is_footnote_row(["北京", 1234.5, 2000]))
        self.assertFalse(is_footnote_row(["全国", 1, 2, 3]))
        self.assertFalse(is_footnote_row([]))
        self.assertFalse(is_footnote_row([None, None]))

    def test_truncate_footnotes(self):
        from services.table_layout import truncate_footnotes
        data = [
            ["年份", "GDP"],
            [2020, 1000],
            [2021, 1100],
            [None, None],
            ["资料来源：国家统计局"],
            ["注：含港澳"],
        ]
        out = truncate_footnotes(data)
        self.assertEqual(out, [
            ["年份", "GDP"],
            [2020, 1000],
            [2021, 1100],
        ])


class TestCategoryTitle(unittest.TestCase):
    def test_chinese_seq(self):
        from services.table_layout import is_category_title_text
        self.assertTrue(is_category_title_text("一、农垦系统"))
        self.assertTrue(is_category_title_text("（一）国营农场"))
        self.assertTrue(is_category_title_text("(二) 集体农场"))

    def test_number_seq(self):
        from services.table_layout import is_category_title_text
        self.assertTrue(is_category_title_text("1. 国营农场"))
        # 纯数字（短）不算
        self.assertFalse(is_category_title_text("1."))

    def test_normal_text(self):
        from services.table_layout import is_category_title_text
        self.assertFalse(is_category_title_text("总计"))
        self.assertFalse(is_category_title_text("北京"))


class TestRowHasNumericData(unittest.TestCase):
    def test_numeric(self):
        from services.table_layout import row_has_numeric_data
        self.assertTrue(row_has_numeric_data(["北京", 1234, 5678]))
        self.assertTrue(row_has_numeric_data(["上海", "1,234.5"]))

    def test_no_numeric(self):
        from services.table_layout import row_has_numeric_data
        self.assertFalse(row_has_numeric_data(["指标", "单位", "类别"]))
        self.assertFalse(row_has_numeric_data(["全国", None, None]))


class TestFindDataStartRow(unittest.TestCase):
    def test_basic(self):
        from services.table_layout import find_data_start_row
        # row 0: 表头, row 1+: 数据。
        # 注意：纯 4 位数会被识别为年份排除，所以这里用带小数点的值。
        data = [
            ["年份", "GDP", "人口"],
            [2020, 1000.5, 1400.2],
            [2021, 1100.1, 1410.7],
            [2022, 1200.0, 1420.3],
        ]
        self.assertEqual(find_data_start_row(data), 1)

    def test_with_title(self):
        from services.table_layout import find_data_start_row
        data = [
            ["全国统计表"],
            ["年份", "GDP", "人口"],
            [2020, 1000.5, 1400.2],
            [2021, 1100.1, 1410.7],
        ]
        self.assertEqual(find_data_start_row(data), 2)

    def test_no_data(self):
        from services.table_layout import find_data_start_row
        # 全部是文字行
        data = [["年份", "类别"], ["指标", "单位"]]
        self.assertEqual(find_data_start_row(data), -1)


class TestDetectHeaderRange(unittest.TestCase):
    def test_single_header(self):
        from services.table_layout import detect_header_range
        data = [
            ["年份", "GDP"],
            [2020, 1000],
            [2021, 1100],
        ]
        h_start, h_end = detect_header_range(data)
        self.assertEqual((h_start, h_end), (0, 0))

    def test_with_title_above_header(self):
        from services.table_layout import detect_header_range
        data = [
            ["统计表标题", "统计表标题"],     # 全等值 → title row（应被剥离）
            ["年份", "GDP"],
            [2020, 1000.5],
            [2021, 1100.7],
        ]
        h_start, h_end = detect_header_range(data)
        self.assertEqual((h_start, h_end), (1, 1))


class TestAdjustHeaderEndIfDataRow(unittest.TestCase):
    def test_data_row_pulled_back(self):
        from services.table_layout import adjust_header_end_if_data_row
        data = [
            ["年份", "GDP", "人口"],          # 真表头
            [2020, 12345.6, 14000.2],          # 数据行（被框线误指）
        ]
        # header_end 错指到 1（数据行）→ 应回退到 0
        self.assertEqual(adjust_header_end_if_data_row(data, 1), 0)

    def test_real_header_kept(self):
        from services.table_layout import adjust_header_end_if_data_row
        data = [
            ["地区", "类别", "总计"],
        ]
        self.assertEqual(adjust_header_end_if_data_row(data, 0), 0)


class TestHasNumericColumns(unittest.TestCase):
    def test_numeric(self):
        from services.table_layout import has_numeric_columns
        self.assertTrue(has_numeric_columns(["地区", "1990", "2000", "2010"], 0, []))

    def test_normal(self):
        from services.table_layout import has_numeric_columns
        self.assertFalse(has_numeric_columns(["地区", "总人口", "GDP"], 0, []))


class TestFfillIndicatorColumn(unittest.TestCase):
    def test_simple_ffill(self):
        from services.table_layout import ffill_indicator_column
        data = [
            ["指标", "1990", "2000"],
            ["总人口", 1000, 1100],
            [None, 200, 220],   # 第一列为空，应被 ffill 为"总人口"
        ]
        ffill_indicator_column(data, data_start=1)
        self.assertEqual(data[2][0], "总人口")

    def test_no_ffill_when_empty_data(self):
        from services.table_layout import ffill_indicator_column
        data = [
            ["指标", "1990"],
            ["总人口", 1000],
            [None, None],   # 整行空，不该被 ffill
        ]
        ffill_indicator_column(data, data_start=1)
        self.assertIsNone(data[2][0])

    def test_with_hborder_grouping(self):
        from services.table_layout import ffill_indicator_column
        # row_has_hborder[i]=True 表示第 i 行底边有线 → 是当前组最后一行
        data = [
            ["指标", "1990"],
            ["A", 1],
            [None, 2],
            [None, 3],   # 同组：A
            ["B", 4],
            [None, 5],   # 同组：B
        ]
        row_has_hborder = [False, False, False, True, False, True]
        ffill_indicator_column(data, data_start=1, row_has_hborder=row_has_hborder)
        self.assertEqual(data[2][0], "A")
        self.assertEqual(data[3][0], "A")
        self.assertEqual(data[5][0], "B")


if __name__ == "__main__":
    unittest.main()
