"""eval/slo_check.py 的单元测试（降级显式化）。

这组测试守的是一条容易被糊弄过去的规矩：**降级请求的耗时不许混进 p95**。
降级走的是快路径，混进来会让"性能达标"变成一堆失败请求堆出来的假象。
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.slo_check import (  # noqa: E402
    DEGRADED_COMPONENTS,
    check,
    degraded_of,
    describe,
    load_records,
    secs_of,
    summarize,
)


def row(secs: float, degraded=()) -> dict:
    return {"secs": secs, "degraded_components": list(degraded)}


class TestDegradedOf(unittest.TestCase):
    def test_prefers_component_list(self):
        self.assertEqual(degraded_of(row(1.0, ["rerank_error"])), ["rerank_error"])

    def test_falls_back_to_legacy_string(self):
        self.assertEqual(
            degraded_of({"secs": 1.0, "degraded": "no_store,rerank_unavailable"}),
            ["no_store", "rerank_unavailable"],
        )

    def test_empty_means_clean(self):
        self.assertEqual(degraded_of(row(1.0)), [])
        self.assertEqual(degraded_of({"secs": 1.0, "degraded": ""}), [])

    def test_list_valued_legacy_field(self):
        """smoke 早期版本把 degraded 写成列表，也得认。"""
        self.assertEqual(degraded_of({"secs": 1.0, "degraded": ["bm25_unavailable"]}), ["bm25_unavailable"])


class TestSecsOf(unittest.TestCase):
    def test_reads_secs(self):
        self.assertEqual(secs_of({"secs": 1.5}), 1.5)

    def test_missing_is_zero(self):
        self.assertEqual(secs_of({}), 0.0)

    def test_garbage_is_zero(self):
        self.assertEqual(secs_of({"secs": "n/a"}), 0.0)


class TestSummarize(unittest.TestCase):
    def test_clean_only(self):
        stat = summarize([row(1.0), row(2.0), row(3.0)])
        self.assertEqual(stat["cases"], 3)
        self.assertEqual(stat["degraded_cases"], 0)
        self.assertEqual(stat["degraded_rate"], 0.0)
        self.assertEqual(stat["p95_clean"], 3.0)
        self.assertEqual(stat["by_component"], {})

    def test_degraded_excluded_from_clean_p95(self):
        """核心用例：降级走快路径，混进来会把 p95 拉好看。

        样本要够大才有意义——p95 是最近秩，样本小的时候会退化成最大值，
        两组算出来一样，看不出差别（见 metrics.p95 的说明）。
        """
        clean = [row(float(i)) for i in range(1, 11)]          # 1..10 秒
        degraded = [row(0.1, ["rerank_unavailable"]) for _ in range(10)]
        stat = summarize(clean + degraded)
        self.assertEqual(stat["clean_cases"], 10)
        self.assertEqual(stat["degraded_cases"], 10)
        # 无降级子集 p95 = 10.0；把 10 条 0.1s 的降级混进来后全体 p95 掉到 9.0
        self.assertEqual(stat["p95_clean"], 10.0)
        self.assertLess(stat["p95_all"], stat["p95_clean"])
        self.assertLess(stat["mean_all"], stat["mean_clean"])

    def test_component_counts(self):
        records = [
            row(1.0, ["rerank_error"]),
            row(1.0, ["rerank_error", "bm25_unavailable"]),
        ]
        stat = summarize(records)
        self.assertEqual(stat["by_component"]["rerank_error"], 2)
        self.assertEqual(stat["by_component"]["bm25_unavailable"], 1)

    def test_empty_records(self):
        stat = summarize([])
        self.assertEqual(stat["cases"], 0)
        self.assertEqual(stat["degraded_rate"], 0.0)

    def test_all_degraded_has_no_clean_p95(self):
        stat = summarize([row(0.1, ["no_store"])])
        self.assertEqual(stat["clean_cases"], 0)
        self.assertEqual(stat["p95_clean"], 0.0)


class TestCheck(unittest.TestCase):
    def test_within_budget(self):
        ok, violations = check([row(1.0), row(2.0)], p95_budget=3.0, degraded_budget=0.0)
        self.assertTrue(ok)
        self.assertEqual(violations, [])

    def test_p95_over_budget(self):
        ok, violations = check([row(9.0)], p95_budget=3.0)
        self.assertFalse(ok)
        self.assertTrue(any("p95" in v for v in violations))

    def test_degraded_rate_over_budget(self):
        ok, violations = check([row(1.0), row(1.0, ["rerank_error"])], degraded_budget=0.0)
        self.assertFalse(ok)
        self.assertTrue(any("降级率" in v for v in violations))

    def test_all_degraded_fails_not_passes(self):
        """全降级时不能因为"p95 很低"就判通过。"""
        ok, violations = check([row(0.1, ["rerank_unavailable"])], p95_budget=3.0)
        self.assertFalse(ok)
        self.assertTrue(any("无从计算" in v for v in violations))

    def test_empty_input_is_failure(self):
        ok, violations = check([])
        self.assertFalse(ok)
        self.assertTrue(violations)

    def test_no_budget_means_report_only(self):
        ok, _ = check([row(99.0, ["no_store"])])
        self.assertTrue(ok)


class TestLoadRecords(unittest.TestCase):
    def test_accepts_cases_wrapper(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "baseline.json"
            path.write_text(json.dumps({"cases": [row(1.0)]}), encoding="utf-8")
            self.assertEqual(len(load_records(path)), 1)

    def test_accepts_bare_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rows.json"
            path.write_text(json.dumps([row(1.0), row(2.0)]), encoding="utf-8")
            self.assertEqual(len(load_records(path)), 2)

    def test_missing_file_exits(self):
        with self.assertRaises(SystemExit):
            load_records(Path(tempfile.gettempdir()) / "__nope_slo__.json")

    def test_bad_json_exits(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text("{oops", encoding="utf-8")
            with self.assertRaises(SystemExit):
                load_records(path)


class TestDescribe(unittest.TestCase):
    def test_flags_unknown_component(self):
        """组件名改了没同步 DEGRADED_COMPONENTS，报告里要能看出来。"""
        out = describe([row(1.0, ["some_new_thing"])], p95_budget=3.0)
        self.assertIn("未知组件", out)

    def test_known_component_not_flagged(self):
        out = describe([row(1.0, ["rerank_error"])])
        self.assertNotIn("未知组件", out)

    def test_component_names_match_retriever(self):
        """retriever 里若新增降级点，这里会先红——提醒同步名单。"""
        self.assertEqual(
            set(DEGRADED_COMPONENTS),
            {
                "no_store",
                "bm25_unavailable",
                "rerank_unavailable",
                "rerank_error",
                "rerank_count_mismatch",
            },
        )


if __name__ == "__main__":
    unittest.main()
