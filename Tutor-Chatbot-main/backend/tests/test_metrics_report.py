"""检索链路指标报告卡的单测（eval/metrics_report.py + eval/report_api.py）。

重点锁的是三条**容易写错且写错后果严重**的语义：
1. 「没跑」(None) 与「跑出来是 0」必须区分——把 None 判成达标是最危险的写法；
2. 达标率的分母不含「未运行」与「不设阈值」；
3. CSV 的列顺序/列数必须与 JSON 的 rows 完全对齐（否则前端表格和导出文件会长得不一样）。
"""

from __future__ import annotations

import csv
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval import metrics_report as M  # noqa: E402
from eval import report_api as A  # noqa: E402


def _stub_report(**overrides) -> dict:
    """最小可用的 e2e 报告（L3 故意缺失，用来验证「未运行」）。"""
    base = {
        "timestamp": "2026-10-28T08:00:25",
        "index_fingerprint": "sha256:test",
        "cases_file": "rag_eval_cases.json",
        "layers": {
            "L1": {
                "granularity": "parent",
                "inject_k": 3,
                "top1": 68,
                "top3": 70,
                "total": 71,
                "recall_at_k": 0.7827,
                "precision_at_k": 0.5587,
                "noise_rate": 0.3052,
                "mrr": 0.9648,
                "ndcg_at_k": 0.8651,
                "annotated_cases": 71,
                "fallback_cases": 0,
                "ndcg_clamped_cases": 0,
                "avg_secs": 0.9611,
                "p95_secs": 1.0667,
                "degraded_cases": 0,
            },
            "L2": {
                "ndcg_rerank_on": 0.8651,
                "ndcg_rerank_off": 0.8161,
                "uplift": 0.049,
                "rerank_p95_ms": 1035.0,
                "top1_on": 68,
                "top1_off": 63,
            },
            "L3": None,
            "L4": None,
        },
        "gate": {"passed": True, "failures": []},
    }
    base.update(overrides)
    return base


class JudgeTest(unittest.TestCase):
    """达标判定：None / 0 / 阈值方向 / 严格大于，四种语义都不能混。"""

    def test_none_is_unknown_not_fail(self):
        """没跑的层必须是「未运行」，绝不能判成未达标或达标。"""
        spec = M.SPECS_BY_KEY["hallucination_rate"]
        self.assertEqual(M.judge(None, spec), M.STATUS_UNKNOWN)

    def test_zero_is_judged_not_unknown(self):
        """跑出来是 0 就要真判，不能因为 0 是 falsy 就当缺失。"""
        spec = M.SPECS_BY_KEY["degraded_rate"]
        self.assertEqual(M.judge(0.0, spec), M.STATUS_PASS)
        self.assertEqual(M.judge(0.5, spec), M.STATUS_FAIL)

    def test_higher_and_lower_directions(self):
        self.assertEqual(M.judge(0.95, M.SPECS_BY_KEY["top1_hit_rate"]), M.STATUS_PASS)
        self.assertEqual(M.judge(0.80, M.SPECS_BY_KEY["top1_hit_rate"]), M.STATUS_FAIL)
        self.assertEqual(M.judge(1.0, M.SPECS_BY_KEY["p95_secs"]), M.STATUS_PASS)
        self.assertEqual(M.judge(5.0, M.SPECS_BY_KEY["p95_secs"]), M.STATUS_FAIL)

    def test_strict_greater_excludes_zero(self):
        """重排提升度是 > 0：提升度恰好为 0 属于「没帮上忙」，必须判失败。"""
        spec = M.SPECS_BY_KEY["rerank_uplift"]
        self.assertEqual(spec.comparator, ">")
        self.assertEqual(M.judge(0.0, spec), M.STATUS_FAIL)
        self.assertEqual(M.judge(0.0001, spec), M.STATUS_PASS)

    def test_no_target_is_info(self):
        spec = M.SPECS_BY_KEY["top1_hit_rate"]
        spec = M.MetricSpec(**{**spec.__dict__, "target": None})
        self.assertEqual(M.judge(0.1, spec), M.STATUS_INFO)


class FormatTest(unittest.TestCase):
    def test_none_renders_as_dash(self):
        """None 显示成「—」而不是 0.00%——显示 0 会被当成真实测到的 0。"""
        self.assertEqual(M.format_value(None, "%"), "—")
        self.assertEqual(M.format_value(None, "s"), "—")

    def test_unit_formats(self):
        self.assertEqual(M.format_value(0.7827, "%"), "78.27%")
        self.assertEqual(M.format_value(1.0667, "s"), "1.067s")
        self.assertEqual(M.format_value(1035.0, "ms"), "1035ms")
        self.assertEqual(M.format_value(5.0, "条"), "+5")
        self.assertEqual(M.format_value(0.8651, ""), "0.8651")

    def test_target_without_value_shows_dash(self):
        spec = M.MetricSpec(**{**M.SPECS_BY_KEY["top1_hit_rate"].__dict__, "target": None})
        self.assertEqual(M.format_target(spec), "—")

    def test_target_shows_comparator(self):
        self.assertEqual(M.format_target(M.SPECS_BY_KEY["top1_hit_rate"]), ">= 90.00%")
        self.assertEqual(M.format_target(M.SPECS_BY_KEY["p95_secs"]), "<= 3.000s")


class BuildCardTest(unittest.TestCase):
    def test_rows_cover_all_specs(self):
        rows = M.build_rows(_stub_report())
        self.assertEqual(len(rows), len(M.METRIC_SPECS))

    def test_missing_layer_is_unknown(self):
        rows = {r["key"]: r for r in M.build_rows(_stub_report())}
        self.assertEqual(rows["hallucination_rate"]["status"], M.STATUS_UNKNOWN)
        self.assertIsNone(rows["hallucination_rate"]["value"])
        self.assertEqual(rows["hallucination_rate"]["value_display"], "—")

    def test_ratios_computed_from_counts(self):
        rows = {r["key"]: r for r in M.build_rows(_stub_report())}
        self.assertAlmostEqual(rows["top1_hit_rate"]["value"], 68 / 71, places=4)
        self.assertAlmostEqual(rows["top3_hit_rate"]["value"], 70 / 71, places=4)
        self.assertAlmostEqual(rows["degraded_rate"]["value"], 0.0, places=4)
        self.assertAlmostEqual(rows["rerank_top1_gain"]["value"], 5.0, places=4)

    def test_zero_denominator_is_not_zero(self):
        """计分题数为 0 时比率必须是 None，不能返回 0.0 假装达标。"""
        report = _stub_report()
        report["layers"]["L1"]["total"] = 0
        rows = {r["key"]: r for r in M.build_rows(report)}
        self.assertIsNone(rows["top1_hit_rate"]["value"])
        self.assertEqual(rows["top1_hit_rate"]["status"], M.STATUS_UNKNOWN)

    def test_summary_excludes_unknown_from_pass_rate(self):
        card = M.build_card(_stub_report())
        s = card["summary"]
        self.assertEqual(s["total"], len(M.METRIC_SPECS))
        # L3 / L4 未跑 ⇒ 来源为这两层的指标全部「未运行」。
        # 按来源算而不是写死数字：以后往这两层加指标时不用改测试。
        expected_unknown = sum(
            1 for spec in M.METRIC_SPECS if spec.source.startswith(("L3", "L4"))
        )
        self.assertEqual(s["unknown"], expected_unknown)
        # 达标率分母 = 已判定项，不含未运行
        self.assertEqual(s["judged"], s["passed"] + s["failed"])
        self.assertAlmostEqual(s["pass_rate"], s["passed"] / s["judged"], places=4)

    def test_known_failures_are_flagged(self):
        """当前真实基线里 recall / precision 就是未达标，报告必须如实标出来。"""
        card = M.build_card(_stub_report())
        rows = {r["key"]: r for r in card["rows"]}
        self.assertEqual(rows["recall_at_k"]["status"], M.STATUS_FAIL)
        self.assertEqual(rows["precision_at_k"]["status"], M.STATUS_FAIL)
        self.assertEqual(rows["p95_secs"]["status"], M.STATUS_PASS)

    def test_context_carries_caps_and_granularity(self):
        card = M.build_card(_stub_report())
        self.assertEqual(card["context"]["granularity"], "parent")
        self.assertEqual(card["context"]["inject_k"], 3)
        caps = card["context"]["caps"]
        # 物理上限必须真的算出来：recall 上限应 < 1（受注入窗口限制）
        if caps:
            self.assertLessEqual(caps["recall_cap_mean"], 1.0)
            self.assertGreater(caps["recall_cap_mean"], 0.0)


def _stub_report_full() -> dict:
    """四层都跑过的报告（用于验证生成层 / 链路稳定性指标的取值与判定）。"""
    layers = _stub_report()["layers"]
    layers["L3"] = {
        "aggregate": {
            "cases": 20,
            "contradiction_rate": 0.05,
            "factual_judged": 20,
            "hallucination_rate": 0.02,
            "hallucination_judged": 20,
            "material_utilization": 0.6667,
            "utilization_judged": 20,
            "answer_relevancy": 0.9,
            "relevancy_judged": 20,
            "answer_correctness": 0.75,
            "correctness_judged": 20,
            "empty_source_declaration": None,
            "empty_source_cases": 0,
        },
        "per_case": [],
    }
    # L4 是两段结构：cascade（级联，零成本）与 e2e（真实 /chat）。
    # 端到端指标只在 e2e 段里——按顶层取会静默拿到 None 显示成「未运行」。
    layers["L4"] = {
        "cascade": {"cases": 8, "top1_fallback_rate": 0.625},
        "e2e": {
            "cases_count": 10,
            "success": 10,
            "success_rate": 1.0,
            "e2e_p95_secs": 6.5,
            "avg_sources": 3.0,
            "material_utilization": 0.7,
            "utilization_judged": 8,
        },
    }
    return _stub_report(layers=layers)


class ExtendedMetricsTest(unittest.TestCase):
    """2026-10-28 扩展的指标：上下文层噪声率 + 生成层四维 + 链路稳定性两项。

    锁的是四条容易写错的语义：
    1. 分组必须登记进 GROUP_ORDER（漏登记会静默掉到表格末尾，看不出来）；
    2. 忠实度是外幻觉率的**补集**，不是第二个独立裁判；
    3. L4 没跑时链路成功率是「未运行」，绝不能按 100% 显示（那是最危险的骗人方式）；
    4. 阈值出处 / 计算说明 / 改进建议三列不许留空。
    """

    def test_every_group_is_registered(self):
        for spec in M.METRIC_SPECS:
            self.assertIn(spec.group, M.GROUP_ORDER, f"{spec.key} 的分组未登记")

    def test_rows_sorted_by_group_order(self):
        rows = M.build_rows(_stub_report())
        order = {g: i for i, g in enumerate(M.GROUP_ORDER)}
        ranks = [order[r["group"]] for r in rows]
        self.assertEqual(ranks, sorted(ranks))
        self.assertEqual(len(rows), len(M.METRIC_SPECS))

    def test_every_spec_is_documented(self):
        for spec in M.METRIC_SPECS:
            for name in ("basis", "formula", "advice"):
                self.assertTrue(
                    getattr(spec, name).strip(), f"{spec.key}.{name} 为空"
                )

    def test_faithfulness_is_complement_of_hallucination(self):
        rows = {r["key"]: r for r in M.build_rows(_stub_report_full())}
        self.assertAlmostEqual(rows["faithfulness"]["value"], 0.98, places=4)
        self.assertEqual(rows["faithfulness"]["status"], M.STATUS_PASS)
        # 与外幻觉率严格互补：两项不可能同时达标/同时不达标之外的情况
        self.assertAlmostEqual(
            rows["faithfulness"]["value"] + rows["hallucination_rate"]["value"],
            1.0,
            places=4,
        )

    def test_faithfulness_unknown_when_l3_missing(self):
        rows = {r["key"]: r for r in M.build_rows(_stub_report())}
        self.assertIsNone(rows["faithfulness"]["value"])
        self.assertEqual(rows["faithfulness"]["status"], M.STATUS_UNKNOWN)

    def test_generation_metrics_take_l3_values(self):
        rows = {r["key"]: r for r in M.build_rows(_stub_report_full())}
        self.assertAlmostEqual(rows["answer_relevancy"]["value"], 0.9, places=4)
        self.assertEqual(rows["answer_relevancy"]["status"], M.STATUS_PASS)
        self.assertAlmostEqual(rows["answer_correctness"]["value"], 0.75, places=4)
        # 0.75 < 0.80 目标 ⇒ 必须如实判未达标
        self.assertEqual(rows["answer_correctness"]["status"], M.STATUS_FAIL)
        self.assertAlmostEqual(rows["material_utilization"]["value"], 0.6667, places=4)
        self.assertEqual(rows["material_utilization"]["status"], M.STATUS_PASS)
        self.assertAlmostEqual(rows["contradiction_rate"]["value"], 0.05, places=4)

    def test_l4_metrics_unknown_when_l4_missing(self):
        """L4 不进 CI、日常不跑：缺值必须是「未运行」，不是 0 也不是 100%。"""
        rows = {r["key"]: r for r in M.build_rows(_stub_report())}
        for key in ("link_success_rate", "e2e_p95_secs"):
            self.assertIsNone(rows[key]["value"])
            self.assertEqual(rows[key]["status"], M.STATUS_UNKNOWN)
            self.assertEqual(rows[key]["value_display"], "—")

    def test_l4_metrics_take_l4_values(self):
        rows = {r["key"]: r for r in M.build_rows(_stub_report_full())}
        self.assertAlmostEqual(rows["link_success_rate"]["value"], 1.0, places=4)
        self.assertEqual(rows["link_success_rate"]["status"], M.STATUS_PASS)
        self.assertAlmostEqual(rows["e2e_p95_secs"]["value"], 6.5, places=4)
        self.assertEqual(rows["e2e_p95_secs"]["status"], M.STATUS_PASS)

    def test_l4_metrics_read_the_e2e_segment_not_top_level(self):
        """L4 是 {cascade, e2e} 两段：指标在 e2e 段里，按顶层取会静默变「未运行」。

        2026-10-28 真踩过：报告明明跑了 L4，指标卡却说没跑，**且不报任何错**——
        这种"静默取空"比报错危险得多，所以单独锁一条。
        """
        report = _stub_report_full()
        # 顶层也放一个同名但不同的值：取错路径会得到它，测试立刻能看出来
        report["layers"]["L4"]["success_rate"] = 0.5
        rows = {r["key"]: r for r in M.build_rows(report)}
        self.assertAlmostEqual(rows["link_success_rate"]["value"], 1.0, places=4)

    def test_l4_present_but_e2e_missing_is_unknown(self):
        """跑了 cascade 没跑 e2e：端到端两项必须是未运行，不能拿 cascade 的数字顶。"""
        report = _stub_report_full()
        report["layers"]["L4"] = {"cascade": {"cases": 8}}
        rows = {r["key"]: r for r in M.build_rows(report)}
        for key in ("link_success_rate", "e2e_p95_secs"):
            self.assertIsNone(rows[key]["value"])
            self.assertEqual(rows[key]["status"], M.STATUS_UNKNOWN)

    def test_noise_rate_comes_from_l1(self):
        rows = {r["key"]: r for r in M.build_rows(_stub_report())}
        self.assertAlmostEqual(rows["noise_rate"]["value"], 0.3052, places=4)
        # 阈值 1/3（平均每轮最多浪费 1 个注入槽位），当前 0.3052 在其内 ⇒ 达标
        self.assertEqual(rows["noise_rate"]["status"], M.STATUS_PASS)
        self.assertEqual(rows["noise_rate"]["group"], "上下文层")
        self.assertAlmostEqual(rows["noise_rate"]["target"], 1 / 3, places=4)


class LatestReportTest(unittest.TestCase):
    """「最新报告」的选取：踩过一次坑，锁死。"""

    def test_newest_by_mtime_wins_regardless_of_name(self):
        """名字不含时间戳的文件不能因为字典序靠后就永久当选。

        真实事故：手工跑出来的 `e2e_metrics_fresh.json` 按名字排在
        `e2e_20261028_080820.json` 之后（'m' > '2'），于是刷新生成的新报告
        一直选不上，接口永远返回旧数据且无任何报错。
        """
        import tempfile
        import time as _time

        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            older = tmpdir / "e2e_metrics_fresh.json"   # 名字字典序靠后，但更旧
            newer = tmpdir / "e2e_20261028_080820.json"
            older.write_text("{}", encoding="utf-8")
            _time.sleep(0.01)
            newer.write_text("{}", encoding="utf-8")
            with mock.patch.object(M, "REPORTS_DIR", tmpdir):
                self.assertEqual(M.latest_report_path(), newer)

    def test_empty_dir_returns_none(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(M, "REPORTS_DIR", Path(tmp)):
                self.assertIsNone(M.latest_report_path())


class CsvTest(unittest.TestCase):
    def setUp(self):
        self.card = M.build_card(_stub_report())

    def test_columns_match_declaration(self):
        """CSV 列顺序必须与 COLUMNS 完全一致。"""
        rows = list(csv.reader(io.StringIO(M.rows_to_csv(self.card["rows"], bom=False))))
        header = rows[0]
        self.assertEqual(header, [M.COLUMN_LABELS[c] for c in M.COLUMNS])
        self.assertEqual(len(header), len(M.COLUMNS))
        for line in rows[1:]:
            self.assertEqual(len(line), len(M.COLUMNS))

    def test_row_count(self):
        rows = list(csv.reader(io.StringIO(M.rows_to_csv(self.card["rows"], bom=False))))
        self.assertEqual(len(rows) - 1, len(M.METRIC_SPECS))

    def test_bom_toggle(self):
        with_bom = M.rows_to_csv(self.card["rows"], bom=True)
        without = M.rows_to_csv(self.card["rows"], bom=False)
        self.assertTrue(with_bom.startswith("﻿"))
        self.assertFalse(without.startswith("﻿"))
        # BOM 只是前缀，去掉后内容应完全一致
        self.assertEqual(with_bom.lstrip("﻿"), without)

    @staticmethod
    def _col(header: list[str], name: str) -> int:
        """按列名取下标。

        不要写死数字下标：2026-10-28 插入「物理上限」列后，写死的 6 / 8
        全部错位，而 CSV 内容其实是对的——错的是测试。按列名取才测得到真问题。
        """
        return M.COLUMNS.index(name)

    def test_chinese_survives_roundtrip(self):
        """中文经 CSV 往返不丢字（防的是编码写错导致的乱码）。"""
        text = M.rows_to_csv(self.card["rows"], bom=False)
        rows = list(csv.reader(io.StringIO(text)))
        header = rows[0]
        self.assertIn("上下文召回率@K", [r[self._col(header, "name")] for r in rows])
        self.assertIn("注入噪声率", [r[self._col(header, "name")] for r in rows])
        self.assertIn("链路成功率", [r[self._col(header, "name")] for r in rows])
        self.assertIn("未运行", [r[self._col(header, "status")] for r in rows])

    def test_embedded_comma_and_quote_are_escaped(self):
        """建议文本里有逗号/引号，必须靠 csv 模块正确转义，不能手写拼接。"""
        row = dict(self.card["rows"][0])
        row["advice"] = '先调 A，再调 B；别"C"'
        text = M.rows_to_csv([row], bom=False)
        parsed = list(csv.reader(io.StringIO(text)))
        idx = self._col(parsed[0], "advice")
        self.assertEqual(parsed[1][idx], '先调 A，再调 B；别"C"')

    def test_ceiling_only_set_for_capped_metrics(self):
        """物理上限只给真有上限的两项；其余必须是「—」，不能填 1.0 误导。"""
        by_key = {r["key"]: r for r in self.card["rows"]}
        self.assertIsNotNone(by_key["recall_at_k"]["ceiling"])
        self.assertIsNotNone(by_key["precision_at_k"]["ceiling"])
        self.assertIsNone(by_key["top1_hit_rate"]["ceiling"])
        self.assertEqual(by_key["top1_hit_rate"]["ceiling_display"], "—")

    def test_ceiling_rate_is_value_over_ceiling(self):
        row = {r["key"]: r for r in self.card["rows"]}["recall_at_k"]
        self.assertAlmostEqual(
            row["ceiling_rate"], row["value"] / row["ceiling"], places=4
        )

    def test_write_csv_uses_utf8_sig(self):
        out = Path(__file__).resolve().parent / "_tmp_metrics.csv"
        try:
            M.write_csv(self.card["rows"], out, bom=True)
            raw = out.read_bytes()
            self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))  # UTF-8 BOM
            self.assertIn("召回率@K".encode("utf-8"), raw)
        finally:
            out.unlink(missing_ok=True)


# --------------------------------------------------------------------------
# 接口层
# --------------------------------------------------------------------------


class ApiTest(unittest.TestCase):
    """用一个只挂本报告路由的独立 app 测接口，不拉起真实的 Chroma / DB。"""

    @classmethod
    def setUpClass(cls):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        A.configure_metrics_api(lambda: None)  # 无知识库：指纹校验标为"无法校验"
        app = FastAPI()
        app.include_router(A.router)
        cls.client = TestClient(app)

    def setUp(self):
        import tempfile

        self.tmp = Path(tempfile.mkdtemp())
        self.report_path = self.tmp / "e2e_stub.json"
        self.report_path.write_text(
            json.dumps(_stub_report(), ensure_ascii=False), encoding="utf-8"
        )
        self._patch = mock.patch.object(
            A, "latest_report_path", lambda: self.report_path
        )
        self._patch.start()
        A._current_job_id = None
        A._jobs.clear()

    def tearDown(self):
        self._patch.stop()
        A._current_job_id = None
        A._jobs.clear()

    # ---- GET /evaluation/metrics ----

    def test_metrics_ok(self):
        r = self.client.get("/evaluation/metrics")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["available"])
        self.assertEqual(len(body["rows"]), len(M.METRIC_SPECS))
        self.assertEqual(body["columns"], M.COLUMNS)
        self.assertEqual(body["column_labels"], M.COLUMN_LABELS)
        self.assertIsNotNone(body["summary"]["pass_rate"])

    def test_metrics_without_report_returns_404(self):
        with mock.patch.object(A, "latest_report_path", lambda: None):
            r = self.client.get("/evaluation/metrics")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["detail"]["code"], "NO_REPORT")

    def test_path_traversal_rejected(self):
        r = self.client.get("/evaluation/metrics", params={"report": "../../.env"})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["detail"]["code"], "BAD_REPORT_NAME")

    def test_unknown_report_404(self):
        r = self.client.get("/evaluation/metrics", params={"report": "e2e_nope.json"})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["detail"]["code"], "REPORT_NOT_FOUND")

    # ---- GET /evaluation/metrics.csv ----

    def test_csv_headers_and_type(self):
        r = self.client.get("/evaluation/metrics.csv")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/csv", r.headers["content-type"])
        self.assertIn("attachment;", r.headers["content-disposition"])
        self.assertIn("Cache-Control", r.headers)

    def test_csv_bom_default_on(self):
        body = self.client.get("/evaluation/metrics.csv").content
        self.assertTrue(body.startswith(b"\xef\xbb\xbf"))
        self.assertIn("召回率@K".encode("utf-8"), body)

    def test_csv_bom_off(self):
        body = self.client.get("/evaluation/metrics.csv", params={"bom": 0}).content
        self.assertFalse(body.startswith(b"\xef\xbb\xbf"))

    def test_csv_matches_json_rows(self):
        """导出的行数与列数必须和 JSON 的 rows 一致——防止两套口径分叉。"""
        rows = self.client.get("/evaluation/metrics").json()["rows"]
        body = self.client.get("/evaluation/metrics.csv", params={"bom": 0}).content.decode("utf-8")
        parsed = list(csv.reader(io.StringIO(body)))
        self.assertEqual(len(parsed) - 1, len(rows))
        self.assertEqual(parsed[0], [M.COLUMN_LABELS[c] for c in M.COLUMNS])

    # ---- POST /evaluation/metrics/refresh ----

    def test_refresh_accepted(self):
        with mock.patch.object(A.threading, "Thread") as fake:
            r = self.client.post("/evaluation/metrics/refresh", json={"layers": "L1,L2"})
        self.assertEqual(r.status_code, 202)
        job_id = r.json()["job_id"]
        self.assertIn(job_id, A._jobs)
        self.assertEqual(A._jobs[job_id]["status"], "running")
        A._current_job_id = None  # 假线程不会真正复位

    def test_refresh_bad_layer_422(self):
        r = self.client.post("/evaluation/metrics/refresh", json={"layers": "L9"})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["detail"]["code"], "BAD_LAYERS")

    def test_refresh_empty_layer_422(self):
        r = self.client.post("/evaluation/metrics/refresh", json={"layers": " , "})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["detail"]["code"], "EMPTY_LAYERS")

    def test_refresh_single_flight_409(self):
        """并发评测会让延迟指标失真，所以必须拒绝而不是排队。"""
        A._current_job_id = "running123"
        A._jobs["running123"] = {"status": "running", "started_at": "x"}
        r = self.client.post("/evaluation/metrics/refresh", json={"layers": "L1,L2"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["detail"]["code"], "JOB_RUNNING")
        A._current_job_id = None

    # ---- GET /evaluation/metrics/job/{id} ----

    def test_job_not_found(self):
        r = self.client.get("/evaluation/metrics/job/nope")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["detail"]["code"], "JOB_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
