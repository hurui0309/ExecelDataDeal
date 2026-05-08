"""测试 services/mysql_writer.py 的纯函数（不连数据库）。"""

import unittest

import _bootstrap  # noqa: F401


class TestSanitizeColumnName(unittest.TestCase):
    def test_normal(self):
        from services.mysql_writer import sanitize_column_name
        self.assertEqual(sanitize_column_name("year"), "year")

    def test_chinese_kept(self):
        from services.mysql_writer import sanitize_column_name
        # 中文字符应保留（CJK 区段在 [\w] 通过 \u4e00-\u9fff 兜底）
        self.assertEqual(sanitize_column_name("年份"), "年份")

    def test_special_chars_replaced(self):
        from services.mysql_writer import sanitize_column_name
        self.assertEqual(sanitize_column_name("a/b c-d"), "a_b_c_d")
        self.assertEqual(sanitize_column_name("(总计)"), "总计")
        self.assertEqual(sanitize_column_name("a__b"), "a_b")  # 多重下划线压缩

    def test_empty_inputs(self):
        from services.mysql_writer import sanitize_column_name
        self.assertEqual(sanitize_column_name(None), "col_unknown")
        self.assertEqual(sanitize_column_name(""), "col_empty")
        self.assertEqual(sanitize_column_name("  "), "col_empty")
        self.assertEqual(sanitize_column_name("---"), "col_empty")

    def test_truncate_to_64(self):
        from services.mysql_writer import sanitize_column_name, MYSQL_IDENT_MAX
        long = "x" * 100
        out = sanitize_column_name(long)
        self.assertLessEqual(len(out), MYSQL_IDENT_MAX)


class TestMakeUniqueColumns(unittest.TestCase):
    def test_no_dup(self):
        from services.mysql_writer import make_unique_columns
        self.assertEqual(make_unique_columns(["a", "b", "c"]), ["a", "b", "c"])

    def test_dup(self):
        from services.mysql_writer import make_unique_columns
        self.assertEqual(make_unique_columns(["a", "a", "a"]), ["a", "a_1", "a_2"])

    def test_dup_long_name_kept_under_64(self):
        from services.mysql_writer import make_unique_columns, MYSQL_IDENT_MAX
        long = "x" * 64
        out = make_unique_columns([long, long, long])
        self.assertEqual(out[0], long)
        for n in out:
            self.assertLessEqual(len(n), MYSQL_IDENT_MAX,
                                 f"重复列名拼后缀后超过 64: {n!r} (len={len(n)})")
        # 后缀正确
        self.assertTrue(out[1].endswith("_1"))
        self.assertTrue(out[2].endswith("_2"))


class TestTruncateTableComment(unittest.TestCase):
    def test_short_passes(self):
        from services.mysql_writer import _truncate_table_comment
        self.assertEqual(_truncate_table_comment("hello"), "hello")

    def test_truncate_at_byte_boundary(self):
        from services.mysql_writer import _truncate_table_comment
        # 中文 utf-8 占 3 字节；limit=10 字节 ≈ 3 个汉字
        text = "中" * 100
        out = _truncate_table_comment(text, limit=10)
        self.assertLessEqual(len(out.encode("utf-8")), 10)
        # 不应解码失败
        self.assertTrue(all(c == "中" for c in out))


class TestNormalizeSourceFile(unittest.TestCase):
    def test_short_kept_with_slash(self):
        from services.mysql_writer import _normalize_source_file
        self.assertEqual(_normalize_source_file("a\\b\\c.xlsx"), "a/b/c.xlsx")

    def test_long_truncated(self):
        from services.mysql_writer import _normalize_source_file
        long = "/".join(["dir"] * 50) + "/file.xlsx"
        out = _normalize_source_file(long)
        self.assertTrue(out.endswith("/file.xlsx"))
        # 截断后字符数不应过长（应当只剩最后 2~3 段）
        self.assertLess(len(out), len(long))


if __name__ == "__main__":
    unittest.main()
