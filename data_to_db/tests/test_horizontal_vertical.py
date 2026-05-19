"""端到端测试: strategy_horizontal_vertical

测试内容：
  1. 对真实 Excel 文件 16-9_县(市)农村经济主要指标(一).xlsx 运行策略，
     验证能解析出 left/right 两个子表，且数据行数大于 0。

  2. 纯单元测试（不依赖真实文件）：
     - find_dash_left_cols
     - _build_columns
     - _extract_block_rows
     - border_info pre_classify_by_border 对混合类型的识别

用法：
  cd data_to_db
  python -m pytest tests/test_horizontal_vertical.py -v
  或
  python -m unittest tests/test_horizontal_vertical.py
"""

import os
import sys
import unittest

# 加载路径
_HERE = os.path.dirname(__file__)
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# 目标 Excel 文件路径
_EXCEL_PATH = os.path.join(
    _ROOT, "..", "data", "横向+纵向",
    "16-9_县(市)农村经济主要指标(一).xlsx",
)
_EXCEL_PATH = os.path.normpath(_EXCEL_PATH)
_EXCEL_EXISTS = os.path.isfile(_EXCEL_PATH)


# ──────────────────────────────────────────────────────────────────────────────
# 单元测试：无需真实文件
# ──────────────────────────────────────────────────────────────────────────────

class TestFindDashLeftCols(unittest.TestCase):

    def test_basic(self):
        from services.border_info import find_dash_left_cols
        cols = [
            {"left_style": "dashDot", "left_ratio": 0.05, "left_dash": True},
            {"left_style": "thin",    "left_ratio": 0.90, "left_dash": False},
            {"left_style": "dashDot", "left_ratio": 0.02, "left_dash": True},
        ]
        result = find_dash_left_cols(cols, min_ratio=0.01)
        self.assertEqual(result, [0, 2])

    def test_below_ratio(self):
        from services.border_info import find_dash_left_cols
        cols = [
            {"left_style": "dashDot", "left_ratio": 0.005, "left_dash": True},
        ]
        result = find_dash_left_cols(cols, min_ratio=0.01)
        self.assertEqual(result, [])

    def test_empty(self):
        from services.border_info import find_dash_left_cols
        self.assertEqual(find_dash_left_cols([], min_ratio=0.01), [])


class TestPreClassifyBorder(unittest.TestCase):
    """验证 _classify_by_border_info 对混合类型的识别（纯数据驱动，无文件 I/O）。"""

    def _make_rows_info(self, header_rows, total_rows):
        """构造 rows_info：前 header_rows 行有 bottom_solid。"""
        rows = []
        for i in range(total_rows):
            if i < header_rows:
                rows.append({
                    "bottom_solid": True, "bottom_ratio": 0.9,
                    "bottom_solid_ratio": 0.9, "bottom_dash": False,
                })
            else:
                rows.append({
                    "bottom_solid": False, "bottom_ratio": 0.0,
                    "bottom_solid_ratio": 0.0, "bottom_dash": False,
                })
        return rows

    def _make_cols_info(self, ncols, dash_col):
        """构造 cols_info：dash_col 列有 left_dash（-1 表示无）。"""
        cols = []
        for i in range(ncols):
            if i == dash_col:
                cols.append({
                    "left_style": "dashDot", "left_ratio": 0.05,
                    "left_dash": True,
                    "right_style": None, "right_ratio": 0.0, "right_dash": False,
                })
            else:
                cols.append({
                    "left_style": "thin", "left_ratio": 0.8,
                    "left_dash": False,
                    "right_style": "thin", "right_ratio": 0.8, "right_dash": False,
                })
        return cols

    def test_detects_horizontal_vertical(self):
        from services.border_info import _classify_by_border_info
        rows_info = self._make_rows_info(3, 50)
        cols_info = self._make_cols_info(16, 8)
        result = _classify_by_border_info(rows_info, cols_info)
        self.assertIsNotNone(result)
        self.assertEqual(result["strategy"], "strategy_horizontal_vertical")
        self.assertEqual(result["params"]["split_col"], 8)

    def test_no_dash_left_no_hv(self):
        from services.border_info import _classify_by_border_info
        rows_info = self._make_rows_info(3, 50)
        cols_info = self._make_cols_info(16, -1)  # 无 dash_left 列
        result = _classify_by_border_info(rows_info, cols_info)
        # 不应触发 horizontal_vertical（可能触发其他或 None）
        if result:
            self.assertNotEqual(result["strategy"], "strategy_horizontal_vertical")


class TestBuildColumns(unittest.TestCase):

    def test_single_header_row(self):
        from strategies.strategy_horizontal_vertical import _build_columns
        header = [["年份", "地区", "总产值"]]
        cols = _build_columns(header, 0, 0)
        self.assertEqual(len(cols), 3)
        self.assertIn("年份", " ".join(cols))

    def test_multi_row_header(self):
        from strategies.strategy_horizontal_vertical import _build_columns
        header = [
            ["年份", "农业", "农业", "工业"],
            [None,   "总产值", "增速", "总产值"],
        ]
        cols = _build_columns(header, 0, 1)
        self.assertEqual(len(cols), 4)

    def test_unique_cols(self):
        from strategies.strategy_horizontal_vertical import _build_columns
        header = [["年份", "值", "值", "值"]]
        cols = _build_columns(header, 0, 0)
        self.assertEqual(len(cols), len(set(cols)))


class TestExtractBlockRows(unittest.TestCase):

    def test_basic(self):
        from strategies.strategy_horizontal_vertical import _extract_block_rows
        block = [
            ["年份", "A", "B", "年份", "C", "D"],  # 表头行（被跳过）
            ["2020",  10,  20, "2020",  30,  40],
            ["2021",  11,  21, "2021",  31,  41],
        ]
        left_out, right_out = [], []
        _extract_block_rows(block, 1, 3, 3, 3, left_out, right_out)
        self.assertEqual(len(left_out), 2)
        self.assertEqual(len(right_out), 2)
        self.assertEqual(left_out[0], ["2020", "10", "20"])
        self.assertEqual(right_out[0], ["2020", "30", "40"])

    def test_skips_empty_rows(self):
        from strategies.strategy_horizontal_vertical import _extract_block_rows
        block = [
            ["年份", "A", "B", "年份", "C", "D"],  # 表头
            [None, None, None, None, None, None],    # 空行（跳过）
            ["2020", 10, 20, "2020", 30, 40],
        ]
        left_out, right_out = [], []
        _extract_block_rows(block, 1, 3, 3, 3, left_out, right_out)
        self.assertEqual(len(left_out), 1)


# ──────────────────────────────────────────────────────────────────────────────
# 端到端测试：依赖真实 Excel 文件
# ──────────────────────────────────────────────────────────────────────────────

@unittest.skipUnless(_EXCEL_EXISTS, f"真实 Excel 不存在: {_EXCEL_PATH}")
class TestRealFile(unittest.TestCase):

    SHEET_NAME = "16-9"

    def _get_first_sheet(self):
        from services.excel_preview import list_sheets
        info = list_sheets(_EXCEL_PATH)
        names = info.get("sheet_names", [])
        return names[0] if names else self.SHEET_NAME

    def test_pre_classify_detects_hv(self):
        """框线预分类应识别出 strategy_horizontal_vertical。"""
        from services.border_info import (
            read_border_info, pre_classify_by_border, _classify_by_border_info,
        )
        sheet = self._get_first_sheet()
        bi = read_border_info(_EXCEL_PATH, sheet)
        if not bi:
            self.skipTest("border_info 读取失败")
        # 用数据驱动版（_classify_by_border_info）校验纯逻辑
        result = _classify_by_border_info(bi["rows"], bi["cols"])
        self.assertIsNotNone(result, "pre_classify 应返回结果")
        self.assertEqual(
            result["strategy"], "strategy_horizontal_vertical",
            f"期望 strategy_horizontal_vertical, 实际: {result}"
        )
        self.assertIn("split_col", result["params"])
        # 同时验证文件路径版结果一致
        result2 = pre_classify_by_border(_EXCEL_PATH, sheet)
        self.assertEqual(result["strategy"], result2["strategy"])

    def test_strategy_run(self):
        """策略 run() 应返回两个子表且数据行数 > 0。"""
        from strategies.strategy_horizontal_vertical import run
        sheet = self._get_first_sheet()
        result = run(
            file_path=_EXCEL_PATH,
            sheet_name=sheet,
            table_name="test_hv",
        )
        subtables = result.get("subtables", [])
        self.assertEqual(len(subtables), 2, f"应有 2 个子表，实际: {len(subtables)}")
        for st in subtables:
            self.assertGreater(len(st["rows"]), 0, f"子表 {st['label']} 数据行应>0")
            self.assertGreater(len(st["columns"]), 0, f"子表 {st['label']} 列应>0")

    def test_column_uniqueness(self):
        """左右子表的列名不应重复。"""
        from strategies.strategy_horizontal_vertical import run
        sheet = self._get_first_sheet()
        result = run(_EXCEL_PATH, sheet, "test_hv_unique")
        for st in result.get("subtables", []):
            cols = st["columns"]
            self.assertEqual(
                len(cols), len(set(cols)),
                f"子表 {st['label']} 存在重复列名: {cols}"
            )

    def test_row_count_reasonable(self):
        """左右子表行数应合理（均 > 100 行，< 10000 行）。"""
        from strategies.strategy_horizontal_vertical import run
        sheet = self._get_first_sheet()
        result = run(_EXCEL_PATH, sheet, "test_hv_count")
        for st in result.get("subtables", []):
            n = len(st["rows"])
            self.assertGreater(n, 100, f"子表 {st['label']} 行数过少: {n}")
            self.assertLess(n, 10000, f"子表 {st['label']} 行数异常: {n}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
