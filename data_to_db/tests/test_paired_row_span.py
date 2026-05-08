"""测试 strategy_paired_row_bilingual._detect_record_row_span。"""

import unittest

import _bootstrap  # noqa: F401


class TestDetectRecordRowSpan(unittest.TestCase):
    def test_two_row_pair(self):
        from strategies.strategy_paired_row_bilingual import _detect_record_row_span
        # 主行 3 列有值，续行只有第 3 列
        rows = []
        for _ in range(8):
            rows.append(["1", "建筑业", "建造业"])
            rows.append([None, None, "Construction"])
        span = _detect_record_row_span(rows, data_start_row=0, header_n_cols=3)
        self.assertEqual(span, 2)

    def test_three_row_group(self):
        from strategies.strategy_paired_row_bilingual import _detect_record_row_span
        rows = []
        for _ in range(8):
            rows.append(["1", "建筑业", "建造业"])
            rows.append([None, None, "Construction"])
            rows.append([None, None, "建造業"])
        span = _detect_record_row_span(rows, data_start_row=0, header_n_cols=3)
        self.assertEqual(span, 3)

    def test_fallback_to_two_when_unsure(self):
        from strategies.strategy_paired_row_bilingual import _detect_record_row_span
        # 完全乱序：每行都是主行，没有"短续行"
        rows = []
        for i in range(8):
            rows.append(["a", f"b{i}", "c"])
        span = _detect_record_row_span(rows, data_start_row=0, header_n_cols=3)
        self.assertEqual(span, 2)


if __name__ == "__main__":
    unittest.main()
