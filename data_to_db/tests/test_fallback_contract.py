"""测试策略 fallback 契约：返回值含 action=fallback 时由 worker 接管切换。"""

import os
import shutil
import tempfile
import unittest

import openpyxl

import _bootstrap  # noqa: F401


class TestSimpleHeaderFallback(unittest.TestCase):
    """data_start >= 2 时 simple_header 应返回 fallback 契约。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="datadeal_fb_")
        self.path = os.path.join(self.tmpdir, "two_row_header.xlsx")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "S1"
        # 第 1+2 两行表头（多行表头），第 3 行起是数据
        ws.append(["指标", "1990", "2000"])
        ws.append([None, "万人", "万人"])
        ws.append(["人口", 1100.5, 1200.7])
        ws.append(["GDP", 5000.2, 6000.1])
        ws.append(["农业", 1000.3, 1100.4])
        wb.save(self.path)
        from services.excel_reader import clear_cache
        clear_cache()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_returns_fallback_to_multi_header(self):
        from strategies.strategy_simple_header import run
        result = run(self.path, "S1", "tmp", column_names=None, params={})
        # 期望返回 fallback 契约（不是 columns/rows）
        self.assertEqual(result.get("action"), "fallback")
        self.assertEqual(result.get("to"), "strategy_multi_header")


class TestVerticalSubtableFallbackToMultiHeader(unittest.TestCase):
    """无子表分隔的简单数据，vertical_subtable 应 fallback 到 multi_header。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="datadeal_fb2_")
        self.path = os.path.join(self.tmpdir, "no_subtable.xlsx")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "S1"
        ws.append(["年份", "GDP", "人口"])
        ws.append([2020, 1000.5, 1400.2])
        ws.append([2021, 1100.7, 1410.1])
        ws.append([2022, 1200.3, 1420.5])
        wb.save(self.path)
        from services.excel_reader import clear_cache
        clear_cache()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_returns_fallback(self):
        from strategies.strategy_vertical_subtable import run
        # 显式 subtable_regions=[] 触发"共享表头"分支
        result = run(self.path, "S1", "tmp",
                     column_names=None, params={"subtable_regions": []})
        self.assertEqual(result.get("action"), "fallback")
        self.assertEqual(result.get("to"), "strategy_multi_header")


if __name__ == "__main__":
    unittest.main()
