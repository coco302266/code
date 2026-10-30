"""eval/metrics.py 的单元测试（切片级检索指标）。

这些用例刻意用「手搓的小例子」而不是真实知识库：指标口径是会反复改的纯函数，
改一次要能立刻验一次，不该为了验一个 nDCG 去建库、跑重排（PROPOSAL §5 同样立场）。
"""

from __future__ import annotations

import itertools
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_core.documents import Document  # noqa: E402

from eval.metrics import (  # noqa: E402
    GAIN_EXACT,
    GAIN_NONE,
    GAIN_TOPIC,
    ChunkRel,
    aggregate,
    chunk_gains,
    chunk_rel,
    dcg,
    idcg,
    ideal_gains,
    p95,
)


def doc(source: str, start: int, topic: str) -> Document:
    return Document(
        page_content=f"{topic} 的正文 {start}",
        metadata={"source": source, "start_index": start, "topic": topic},
    )


class TestDcg(unittest.TestCase):
    def test_dcg_single_first_position_is_one(self):
        self.assertAlmostEqual(dcg([1.0]), 1.0)

    def test_dcg_discounts_later_positions(self):
        # 第 1 位 1.0、第 2 位 1.0：DCG = 1 + 1/log2(3) ≈ 1.6309
        self.assertAlmostEqual(dcg([1.0, 1.0]), 1 + 1 / math.log2(3), places=4)

    def test_idcg_sorted_desc(self):
        self.assertAlmostEqual(idcg([0.5, 1.0, 0.0], 3), dcg([1.0, 0.5, 0.0]))

    def test_idcg_empty_is_zero(self):
        self.assertEqual(idcg([], 4), 0.0)

    def test_ideal_gains_annotated_uses_gold(self):
        self.assertEqual(ideal_gains([0.5, 1.0], 2, True), [1.0, 0.5])

    def test_ideal_gains_unannotated_is_all_hit(self):
        # 无金标准时理想 = k 个位置全命中
        self.assertEqual(ideal_gains([], 4, False), [1.0, 1.0, 1.0, 1.0])

    def test_ideal_gains_k_zero(self):
        self.assertEqual(ideal_gains([1.0], 0, True), [])


class TestChunkGains(unittest.TestCase):
    def test_exact_gold_gets_full_gain(self):
        docs = [doc("a.md", 0, "死锁")]
        gains = chunk_gains(docs, [["a.md", 0]], {"死锁"})
        self.assertEqual(gains, [GAIN_EXACT])

    def test_topic_match_without_annotation_gets_half(self):
        # 命中期望 topic 但不在金标准切片里：算"部分相关"
        docs = [doc("a.md", 999, "死锁")]
        gains = chunk_gains(docs, [["a.md", 0]], {"死锁"})
        self.assertEqual(gains, [GAIN_TOPIC])

    def test_irrelevant_gets_zero(self):
        docs = [doc("b.md", 0, "TCP 与 UDP")]
        gains = chunk_gains(docs, [["a.md", 0]], {"死锁"})
        self.assertEqual(gains, [GAIN_NONE])

    def test_unannotated_falls_back_to_topic_level(self):
        docs = [doc("a.md", 7, "死锁"), doc("b.md", 0, "TCP 与 UDP")]
        gains = chunk_gains(docs, None, {"死锁"})
        self.assertEqual(gains, [GAIN_EXACT, GAIN_NONE])

    def test_gain_tuple_accepts_list_or_tuple(self):
        docs = [doc("a.md", 0, "死锁")]
        self.assertEqual(chunk_gains(docs, [("a.md", 0)], {"死锁"}), [GAIN_EXACT])

    def test_topic_match_is_case_and_space_insensitive(self):
        # 评测集写 "tcp 与 udp"、库内 topic 是 "TCP 与 UDP"。
        # 旧实现 `topic in expected_topics` 精确匹配 → 这批题永远拿不到 0.5 档，
        # 而 top1/top3 用的是归一化子串判定，同一份报告里两套结论自相矛盾。
        docs = [doc("b.md", 999, "TCP 与 UDP")]
        self.assertEqual(chunk_gains(docs, [["a.md", 0]], {"tcp 与 udp"}), [GAIN_TOPIC])

    def test_expected_short_name_matches_longer_topic(self):
        # 子串口径与 smoke_rag.top_hit 对齐：期望写 "DNS" 能命中
        # "TCP/IP 分层模型与 ARP / DNS"。宽匹配是刻意的——两处判定必须同源，
        # 否则"命中却没分"会变成读报告时的假信号。
        docs = [doc("b.md", 0, "TCP/IP 分层模型与 ARP / DNS")]
        self.assertEqual(chunk_gains(docs, [["a.md", 0]], {"DNS"}), [GAIN_TOPIC])


class TestChunkRel(unittest.TestCase):
    def test_perfect_ranking(self):
        docs = [doc("a.md", 0, "死锁"), doc("a.md", 480, "死锁"), doc("b.md", 0, "TCP")]
        gold = [["a.md", 0], ["a.md", 480]]
        rel = chunk_rel(docs, gold, {"死锁"})
        self.assertTrue(rel.annotated)
        self.assertAlmostEqual(rel.recall, 1.0)
        self.assertAlmostEqual(rel.mrr, 1.0)
        self.assertAlmostEqual(rel.ndcg, 1.0)
        # precision = (1 + 1 + 0) / 3
        self.assertAlmostEqual(rel.precision, 2 / 3, places=4)

    def test_reversed_ranking_lowers_ndcg_but_keeps_recall(self):
        docs = [doc("b.md", 0, "TCP"), doc("a.md", 480, "死锁"), doc("a.md", 0, "死锁")]
        gold = [["a.md", 0], ["a.md", 480]]
        rel = chunk_rel(docs, gold, {"死锁"})
        self.assertAlmostEqual(rel.recall, 1.0)  # 两片都召回了
        self.assertAlmostEqual(rel.mrr, 0.5)     # 第一个相关在第 2 位
        self.assertLess(rel.ndcg, 1.0)

    def test_missing_gold_lowers_recall(self):
        docs = [doc("a.md", 0, "死锁")]
        gold = [["a.md", 0], ["a.md", 480]]
        rel = chunk_rel(docs, gold, {"死锁"})
        self.assertAlmostEqual(rel.recall, 0.5)

    def test_no_hit_is_all_zero(self):
        docs = [doc("b.md", 0, "TCP")]
        rel = chunk_rel(docs, [["a.md", 0]], {"死锁"})
        self.assertEqual((rel.recall, rel.precision, rel.mrr, rel.ndcg), (0.0, 0.0, 0.0, 0.0))

    def test_noise_rate_counts_zero_gain_slots(self):
        """噪声率看的是「零增益条数」，与 precision 的「增益占比」不是一回事。

        这两条用例的 precision 都是 0.5，但噪声率 0.67 vs 0.33——
        「1 条 1.0 + 1 条 0.5 + 1 条 0」里有一条是纯噪声，只有噪声率能看出来。
        """
        gold = [["a.md", 0, 1.0], ["a.md", 480, 0.5], ["b.md", 0, 0.0]]
        one_noise = chunk_rel(
            [doc("a.md", 0, "死锁"), doc("a.md", 480, "死锁"), doc("c.md", 0, "TCP")],
            gold,
            {"死锁"},
            k=3,
        )
        self.assertAlmostEqual(one_noise.precision, 0.5, places=4)
        self.assertAlmostEqual(one_noise.noise_rate, 1 / 3, places=4)

        two_noise = chunk_rel(
            [doc("a.md", 0, "死锁"), doc("c.md", 0, "TCP"), doc("c.md", 480, "TCP")],
            gold,
            {"死锁"},
            k=3,
        )
        self.assertAlmostEqual(two_noise.noise_rate, 2 / 3, places=4)

    def test_noise_rate_is_zero_when_unannotated(self):
        """未标注用例不算噪声：没有金标准时 gain=0 只是「没标」，会假性拉高。"""
        rel = chunk_rel([doc("b.md", 0, "TCP")], None, {"死锁"}, k=3)
        self.assertFalse(rel.annotated)
        self.assertEqual(rel.noise_rate, 0.0)

    def test_unannotated_recall_is_topic_proxy(self):
        # 无金标准时 recall 退化为"是否命中"（等价于旧的 top3 判定），
        # 并显式标记 annotated=False，避免把代理值当真指标读。
        docs = [doc("b.md", 0, "TCP"), doc("a.md", 0, "死锁")]
        rel = chunk_rel(docs, None, {"死锁"})
        self.assertFalse(rel.annotated)
        self.assertAlmostEqual(rel.recall, 1.0)
        self.assertAlmostEqual(rel.mrr, 0.5)

    def test_unannotated_ndcg_normalized_by_all_hit(self):
        # 无金标准时分母是「k 个位置全命中」= dcg([1, 1])，不是 dcg([1])。
        # 旧实现用 dcg([1]) 当分母，四条全中能算出 2.56 —— nDCG 越界即口径错。
        docs = [doc("b.md", 0, "TCP"), doc("a.md", 0, "死锁")]
        rel = chunk_rel(docs, None, {"死锁"})
        self.assertAlmostEqual(rel.ndcg, (1 / math.log2(3)) / dcg([1.0, 1.0]), places=4)

    def test_unannotated_all_hit_is_one(self):
        docs = [doc("a.md", 0, "死锁"), doc("a.md", 480, "死锁")]
        rel = chunk_rel(docs, None, {"死锁"}, k=2)
        self.assertAlmostEqual(rel.ndcg, 1.0, places=4)

    def test_ndcg_never_exceeds_one(self):
        """nDCG 越界就是口径错。这条是护栏，改增益分档时必须还绿。"""
        topics = {"死锁"}
        pool = [doc("a.md", 0, "死锁"), doc("a.md", 480, "死锁"), doc("b.md", 0, "TCP")]
        for size in (1, 2, 3):
            for combo in itertools.permutations(pool, size):
                for gold in (None, [["a.md", 0]], [["a.md", 0], ["a.md", 480]]):
                    for k in (0, 2, 4):
                        rel = chunk_rel(list(combo), gold, topics, k=k)
                        self.assertLessEqual(rel.ndcg, 1.0 + 1e-9)
                        self.assertGreaterEqual(rel.ndcg, 0.0)

    def test_clamped_is_reported_not_silent(self):
        """部分相关档把 DCG 顶过 IDCG 时要截断，但必须记下来。"""
        # 金标准只标 1 片，另外 3 片是 topic 命中（0.5），DCG 必然超过 IDCG=1.0
        docs = [
            doc("a.md", 0, "死锁"),
            doc("a.md", 480, "死锁"),
            doc("a.md", 960, "死锁"),
            doc("a.md", 1440, "死锁"),
        ]
        rel = chunk_rel(docs, [["a.md", 0]], {"死锁"}, k=4)
        self.assertTrue(rel.clamped)
        self.assertAlmostEqual(rel.ndcg, 1.0, places=4)

    def test_perfect_annotated_ranking_not_clamped(self):
        docs = [doc("a.md", 0, "死锁"), doc("a.md", 480, "死锁")]
        rel = chunk_rel(docs, [["a.md", 0], ["a.md", 480]], {"死锁"}, k=2)
        self.assertFalse(rel.clamped)
        self.assertAlmostEqual(rel.ndcg, 1.0, places=4)

    def test_returns_dataclass(self):
        self.assertIsInstance(chunk_rel([], None, set()), ChunkRel)


class TestP95(unittest.TestCase):
    def test_nearest_rank_not_interpolation(self):
        # 10 个值 1..10，p95 取第 ceil(0.95*10)=10 个 → 10
        self.assertEqual(p95(list(range(1, 11))), 10.0)

    def test_single_value(self):
        self.assertEqual(p95([2.5]), 2.5)

    def test_empty_is_zero(self):
        self.assertEqual(p95([]), 0.0)

    def test_p95_degenerates_to_max_on_small_samples(self):
        # 样本 < 20 时 p95 会退化成最大值，这是诚实的（报告里同时给样本量）
        self.assertEqual(p95([3.0, 1.0, 2.0]), 3.0)


class TestAggregate(unittest.TestCase):
    def test_annotated_and_fallback_not_mixed(self):
        rels = [
            chunk_rel([doc("a.md", 0, "死锁")], [["a.md", 0]], {"死锁"}, k=1),
            chunk_rel([doc("a.md", 0, "死锁")], None, {"死锁"}, k=1),
        ]
        agg = aggregate(rels)
        self.assertEqual(agg["annotated_cases"], 1)
        self.assertEqual(agg["fallback_cases"], 1)
        # 真实 recall 只由已标注组贡献；代理值另放一个字段
        self.assertAlmostEqual(agg["recall_at_k"], 1.0)
        self.assertIsNotNone(agg["ndcg_at_k_fallback"])

    def test_no_annotated_cases_reports_none(self):
        """没有标注时不许报 0——0 会被读成「一片都没召回到」。"""
        agg = aggregate([chunk_rel([doc("a.md", 0, "死锁")], None, {"死锁"}, k=1)])
        self.assertIsNone(agg["recall_at_k"])
        self.assertIsNone(agg["precision_at_k"])
        # 噪声率同组：未标注用例的 gain=0 只是「没标」，混进来会假性拉高噪声
        self.assertIsNone(agg["noise_rate"])

    def test_noise_rate_averages_only_annotated(self):
        rels = [
            chunk_rel([doc("a.md", 0, "死锁"), doc("c.md", 0, "TCP")], [["a.md", 0]], {"死锁"}, k=2),
            chunk_rel([doc("a.md", 0, "死锁")], None, {"死锁"}, k=1),
        ]
        # 已标注那条：2 条里 1 条零增益 = 0.5；未标注那条不计
        self.assertAlmostEqual(aggregate(rels)["noise_rate"], 0.5, places=4)

    def test_clamped_count_surfaces_in_report(self):
        docs = [doc("a.md", s, "死锁") for s in (0, 480, 960, 1440)]
        agg = aggregate([chunk_rel(docs, [["a.md", 0]], {"死锁"}, k=4)])
        self.assertEqual(agg["ndcg_clamped_cases"], 1)


if __name__ == "__main__":
    unittest.main()
