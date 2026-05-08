"""测试 strategy_horizontal_split 的 region 级表头路径（R10）。"""

import os
import shutil
import tempfile
import unittest

import openpyxl

import _bootstrap  # noqa: F401


class TestHorizontalSplitRegionHeaders(unittest.TestCase):
    """region 内独立的 header_start/header_end/data_start 应被尊重。"""

    def setUp(self):
        from services.excel_reader import clear_cache
        clear_cache()
        self.tmpdir = tempfile.mkdtemp(prefix="datadeal_hs_")
        self.path = os.path.join(self.tmpdir, "two_panels.xlsx")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "S1"
        # 左侧表：col 0-2，表头在 row 0
        # 右侧表：col 4-6，表头在 row 0
        # row 7 起是数据，中间 col 3 全空当分隔
        ws.append(["指标A", "1990", "2000", None, "指标B", "1990", "2000"])
        ws.append(["人口", 1100.5, 1200.7, None, "GDP", 5000.5, 6000.7])
        ws.append(["农业", 1000.5, 1100.7, None, "工业", 7000.5, 8000.7])
        ws.append(["服务", 1500.5, 1700.7, None, "建筑", 3000.5, 3200.7])
        wb.save(self.path)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_runs_with_explicit_regions(self):
        """LLM-style explicit regions 应能正确切出两个子表。"""
        from strategies.strategy_horizontal_split import run
        params = {
            "regions": [
                {"col_start": 0, "col_end": 2, "label": "left",
                 "header_start": 0, "header_end": 0, "data_start": 1},
                {"col_start": 4, "col_end": 6, "label": "right",
                 "header_start": 0, "header_end": 0, "data_start": 1},
            ],
        }
        result = run(self.path, "S1", "tmp", column_names=None, params=params)
        self.assertIn("subtables", result)
        # 至少切出 2 个子表（label 为 left/right）
        labels = [s["label"] for s in result["subtables"]]
        self.assertIn("left", labels)
        self.assertIn("right", labels)
        # 每个子表应有 3 行数据
        for st in result["subtables"]:
            self.assertEqual(len(st["rows"]), 3)


if __name__ == "__main__":
    unittest.main()
