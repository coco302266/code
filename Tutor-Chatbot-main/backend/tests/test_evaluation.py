"""M4-C 评估聚合层测试（零 LLM、零 IO，全部纯函数）。

覆盖重点不是「算出来对不对」（那是算术），而是**口径**：
- 空样本不能炸，且报告要能明确说「无数据」而不是输出一堆 0；
- 降级必须单独统计——它是静默失败，混在均值里看不出来；
- 分位数必须返回真实观测值，不能插值造数；
- 评分表必须**按发言**分行，且行序与 roles/message_ids 严格对齐。
最后一条最关键：错位一行，人工评分就全打在别人的发言上了。
"""

from __future__ import annotations

import unittest

from agent.evaluation import (
    SCORE_DIMENSIONS,
    percentile,
    render_report_md,
    render_scores_csv,
    score_sheet_rows,
    summarize_runs,
)


def _run(**kwargs) -> dict:
    base = {
        "id": 1,
        "scenario": "v1",
        "topic": "死锁",
        "roles": ["tutor"],
        "degraded": False,
        "reason": "",
        "duration_ms": 4000,
        "rag_hits": 2,
        "message_ids": [11],
        "created_at": "2026-10-22T10:00:00",
    }
    base.update(kwargs)
    return base


class PercentileTests(unittest.TestCase):
    def test_empty_returns_zero(self):
        self.assertEqual(percentile([], 95), 0.0)

    def test_returns_observed_value_not_interpolated(self):
        """小样本下插值会造出一个从未发生过的数，读者按它下结论就是错的。"""
        values = [100.0, 900.0]
        self.assertIn(percentile(values, 50), values)
        self.assertIn(percentile(values, 95), values)

    def test_p95_is_max_on_small_sample(self):
        values = [1.0, 2.0, 3.0]
        self.assertEqual(percentile(values, 95), 3.0)

    def test_single_value(self):
        self.assertEqual(percentile([7.0], 50), 7.0)


class SummarizeTests(unittest.TestCase):
    def test_empty_sample_is_safe(self):
        summary = summarize_runs([])
        self.assertEqual(summary["turns"], 0)
        self.assertEqual(summary["degraded_rate"], 0.0)
        self.assertEqual(summary["latency_ms"], {})

    def test_basic_counts(self):
        summary = summarize_runs(
            [
                _run(id=1, roles=["tutor"], duration_ms=4000, rag_hits=2),
                _run(id=2, scenario="group", roles=["tutor", "peer"], duration_ms=9000, rag_hits=4),
            ]
        )
        self.assertEqual(summary["turns"], 2)
        self.assertEqual(summary["agents_per_turn"], 1.5)
        self.assertEqual(summary["scenarios"], {"v1": 1, "group": 1})
        self.assertEqual(summary["latency_ms"]["mean"], 6500.0)
        self.assertEqual(summary["rag"]["coverage_rate"], 1.0)

    def test_role_counts_turns_not_speeches(self):
        """同一轮里同角色出现两次只算一轮——口径是「多少轮用到了它」。"""
        summary = summarize_runs([_run(roles=["tutor", "tutor"])])
        self.assertEqual(summary["roles"], {"tutor": 1})

    def test_degraded_is_counted_separately_with_reason(self):
        summary = summarize_runs(
            [
                _run(id=1, degraded=True, reason="协作关闭"),
                _run(id=2, degraded=True, reason="协作关闭"),
                _run(id=3, degraded=True, reason="未知场景 x"),
                _run(id=4),
            ]
        )
        self.assertEqual(summary["degraded_turns"], 3)
        self.assertEqual(summary["degraded_rate"], 0.75)
        self.assertEqual(summary["degrade_reasons"]["协作关闭"], 2)
        self.assertEqual(summary["degrade_reasons"]["未知场景 x"], 1)

    def test_rag_coverage_excludes_misses(self):
        summary = summarize_runs(
            [_run(rag_hits=3), _run(rag_hits=0), _run(rag_hits=1)]
        )
        self.assertEqual(summary["rag"]["turns_with_hits"], 2)
        self.assertAlmostEqual(summary["rag"]["coverage_rate"], 2 / 3, places=3)

    def test_missing_fields_do_not_crash(self):
        """老数据（补列前落的库）缺 rag_hits 不该让整份报告失败。"""
        summary = summarize_runs([{"scenario": "v1", "roles": ["tutor"]}])
        self.assertEqual(summary["turns"], 1)
        self.assertEqual(summary["rag"]["avg_hits"], 0.0)


class ScoreSheetTests(unittest.TestCase):
    def test_one_row_per_speech_not_per_turn(self):
        runs = [
            _run(id=1, scenario="group", roles=["tutor", "peer"], message_ids=[11, 12])
        ]
        rows = score_sheet_rows(runs, {11: "tutor 的话", 12: "peer 的话"})
        self.assertEqual(len(rows), 2)
        self.assertEqual([r["role"] for r in rows], ["tutor", "peer"])

    def test_rows_align_roles_with_message_ids(self):
        """错位一行，人工评分就全打在别人的发言上了——必须钉死。"""
        runs = [_run(roles=["tutor", "peer", "examiner"], message_ids=[11, 12, 13])]
        rows = score_sheet_rows(runs, {11: "A", 12: "B", 13: "C"})
        self.assertEqual([r["excerpt"] for r in rows], ["A", "B", "C"])

    def test_missing_message_still_yields_row(self):
        """消息被清理时行仍要生成：少一行比错位一行容易发现。"""
        rows = score_sheet_rows([_run(roles=["tutor"], message_ids=[999])], {})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["excerpt"], "")

    def test_score_columns_present_and_empty(self):
        rows = score_sheet_rows([_run(roles=["tutor"])], {})
        for key, _, _ in SCORE_DIMENSIONS:
            self.assertIn(key, rows[0])
            self.assertEqual(rows[0][key], "")

    def test_four_dimensions_defined(self):
        self.assertEqual(
            [label for _, label, _ in SCORE_DIMENSIONS],
            ["准确性", "相关性", "完整性", "个性化"],
        )

    def test_excerpt_flattens_newlines(self):
        """不压平换行会把 Markdown 表格 / CSV 撑破。"""
        rows = score_sheet_rows([_run(message_ids=[1])], {1: "第一行\n第二行"})
        self.assertNotIn("\n", rows[0]["excerpt"])


class RenderTests(unittest.TestCase):
    def test_empty_report_says_no_data(self):
        text = render_report_md(summarize_runs([]), [])
        self.assertIn("样本为 0", text)

    def test_report_contains_key_metrics(self):
        summary = summarize_runs([_run(), _run(id=2, scenario="group", roles=["tutor", "peer"])])
        text = render_report_md(summary, score_sheet_rows([_run()], {}))
        for needle in ("客观指标", "延迟", "检索覆盖率", "降级明细", "人工评分"):
            self.assertIn(needle, text)

    def test_csv_has_bom_and_header(self):
        """无 BOM 的 UTF-8 在 Windows Excel 里是乱码，评分人一打开就想放弃。"""
        csv_text = render_scores_csv(score_sheet_rows([_run()], {}))
        self.assertTrue(csv_text.startswith("\ufeff"))
        self.assertIn("准确性", csv_text)
        self.assertIn("个性化", csv_text)

    def test_csv_escapes_pipes_in_excerpt(self):
        """摘录里出现 | 会破坏 Markdown 表格（CSV 侧由 csv 模块自己处理）。"""
        rows = score_sheet_rows([_run(message_ids=[1])], {1: "A|B"})
        text = render_report_md(summarize_runs([_run()]), rows)
        self.assertIn("A\\|B", text)


if __name__ == "__main__":
    unittest.main()
