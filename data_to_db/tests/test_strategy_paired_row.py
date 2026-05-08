"""测试 strategy_paired_row_bilingual：成对行映射表。

合成一个最简数据：
  Row1: '对应表号' | '内地名词' | '香港名词'        ← 表头
  Row2: '1,8,12'   | '建筑业'   | '建造业'           ← 主行
  Row3: ''         | ''         | 'Construction'    ← 续行（只有英文翻译）
"""

import os
import shutil
import tempfile
import unittest

import openpyxl

import _bootstrap  # noqa: F401


class TestPairedRowBilingual(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix="datadeal_paired_")
        cls.path = os.path.join(cls.tmpdir, "paired.xlsx")

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Pairs"
        ws.append(["对应表号", "内地名词", "香港名词"])
        ws.append(["1,8,12", "建筑业", "建造业"])
        ws.append([None, None, "Construction"])
        ws.append(["2,3", "工业", "工業"])
        ws.append([None, None, "Industry"])
        wb.save(cls.path)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def test_runs_and_returns_subtables_or_columns(self):
        """该策略 run 应能返回 columns + rows（或 subtables）。"""
        from strategies import strategy_paired_row_bilingual as st
        # 该策略对 LLM 没有强依赖：传 None 应当通过非 LLM 路径
        try:
            result = st.run(self.path, "Pairs", "ods_pairs",
                            column_names=None, params=None, llm_client=None)
        except TypeError:
            # 兼容签名差异（不同策略的 params 名字不同）
            result = st.run(self.path, "Pairs", "ods_pairs", column_names=None, llm_client=None)

        # 必须返回 dict，且有 columns / rows 或 subtables
        self.assertIsInstance(result, dict)
        if "subtables" in result:
            self.assertGreaterEqual(len(result["subtables"]), 1)
            sub = result["subtables"][0]
            self.assertIn("columns", sub)
            self.assertIn("rows", sub)
        else:
            self.assertIn("columns", result)
            self.assertIn("rows", result)
            # 应识别出 5 行原始数据中的成对组合
            self.assertGreaterEqual(len(result["rows"]), 1)


if __name__ == "__main__":
    unittest.main()
