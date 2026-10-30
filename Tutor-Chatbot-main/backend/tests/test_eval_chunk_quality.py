"""eval/chunk_quality.py 纯函数与粒度适配的单测（Wave 7 + 2026-10-25 扩库）。

只测确定性计算（余弦距离 / 独立性比值 / 句读密度 / 标题悬空 / 空正文 /
源文件定位 / 截断判定 / 综合型聚合 / 诊断单元装载）。IO 层（报告落盘）
不进单测，验收靠 `python -m eval.chunk_quality` 出报告。

**粒度这条线为什么要测**：父子库里线上注入的是聚合后的 parent，而向量库存的是
child。诊断对象若停在 child，量出来的是"检索用的碎块"而不是"注入用的整节"，
两份报告对不上号。`load_units` 是这条口径的唯一开关，必须锁住：
parent 模式聚合 + 重新 embed，child 模式原样透传。
"""

import importlib.util
import pathlib
import sys
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location(
    "chunk_quality",
    pathlib.Path(__file__).resolve().parent.parent / "eval" / "chunk_quality.py",
)
cq = importlib.util.module_from_spec(spec)
sys.modules["chunk_quality"] = cq
spec.loader.exec_module(cq)


class TestCosineDistance(unittest.TestCase):
    def test_identical_vectors_distance_zero(self):
        self.assertAlmostEqual(cq.cosine_distance([1.0, 0.0], [1.0, 0.0]), 0.0)

    def test_orthogonal_vectors_distance_one(self):
        self.assertAlmostEqual(cq.cosine_distance([1.0, 0.0], [0.0, 1.0]), 1.0)

    def test_opposite_vectors_distance_two(self):
        self.assertAlmostEqual(cq.cosine_distance([1.0, 0.0], [-1.0, 0.0]), 2.0)

    def test_zero_vector_returns_one(self):
        # 零向量无方向，按"最不相似"处理而不是除零崩溃
        self.assertAlmostEqual(cq.cosine_distance([0.0, 0.0], [1.0, 0.0]), 1.0)


class TestIndependenceRatio(unittest.TestCase):
    def test_larger_is_better(self):
        # 片内贴（0.1）、片间散（0.9）→ 比值 9；反过来 → 0.111
        self.assertAlmostEqual(cq.independence_ratio([0.1], [0.9]), 9.0)
        self.assertAlmostEqual(cq.independence_ratio([0.9], [0.1]), 0.1111, places=3)

    def test_empty_sides_return_none(self):
        self.assertIsNone(cq.independence_ratio([], [0.5]))
        self.assertIsNone(cq.independence_ratio([0.5], []))

    def test_zero_same_topic_distance_returns_none(self):
        # 同主题全距 0：分母趋零，比值无意义，不可判而不是无穷大
        self.assertIsNone(cq.independence_ratio([0.0, 0.0], [0.5]))


class TestSentenceDensity(unittest.TestCase):
    def test_counts_chinese_terminators_per_100_chars(self):
        # 4 个句读 / 8 字 → 50.0/百字
        text = "甲。乙；丙。丁。"
        self.assertAlmostEqual(cq.sentence_density(text), 50.0)

    def test_english_period_not_counted(self):
        # 口径只数中文句读：知识文档是中文八股，英文句点是代码片段
        self.assertAlmostEqual(cq.sentence_density("hello world. foo"), 0.0)

    def test_empty_text_zero(self):
        self.assertEqual(cq.sentence_density(""), 0.0)


class TestHeadingOrphan(unittest.TestCase):
    def test_trailing_heading_is_orphan(self):
        self.assertTrue(cq.heading_orphan("正文一段。\n\n### TLS 1.2 握手"))

    def test_heading_with_content_after_is_fine(self):
        self.assertFalse(cq.heading_orphan("### TLS 1.2 握手\n握手过程如下。"))

    def test_plain_text_not_orphan(self):
        self.assertFalse(cq.heading_orphan("普通正文。"))


class TestDetectGranularity(unittest.TestCase):
    def test_parent_when_any_parent_id(self):
        self.assertEqual(
            cq.detect_granularity([{"start_index": 0}, {"parent_id": "a.md#0"}]),
            cq.GRANULARITY_PARENT,
        )

    def test_child_when_no_parent_id(self):
        self.assertEqual(cq.detect_granularity([{"start_index": 0}]), cq.GRANULARITY_CHILD)

    def test_empty_is_child(self):
        # 空库若被判成 parent，聚合后 0 个单元，报告会以"诊断 0 片"静默通过
        self.assertEqual(cq.detect_granularity([]), cq.GRANULARITY_CHILD)


class TestBodyWithoutHeading(unittest.TestCase):
    def test_strips_single_heading_line(self):
        self.assertEqual(
            cq.body_without_heading("## 一句话结论\n\n死锁是多个进程互相等待。"),
            "死锁是多个进程互相等待。",
        )

    def test_strips_only_first_line(self):
        # 只去一行：正文里的 `### 小标题` 是内容的一部分，删掉会误判空正文
        text = "## 原理\n### 子标题\n正文内容。"
        self.assertEqual(cq.body_without_heading(text), "### 子标题\n正文内容。")

    def test_no_heading_returns_text(self):
        self.assertEqual(cq.body_without_heading("普通正文。"), "普通正文。")

    def test_empty_text(self):
        self.assertEqual(cq.body_without_heading(""), "")


class TestEmptyBody(unittest.TestCase):
    def test_heading_only_is_empty(self):
        # parent 版的"空头支票"：只有小节名，注入后等于什么都没给
        self.assertTrue(cq.empty_body("## 面试常问"))

    def test_heading_with_short_stub_is_empty(self):
        self.assertTrue(cq.empty_body("## 面试常问\n待补充"))

    def test_heading_with_body_is_fine(self):
        self.assertFalse(cq.empty_body("## 面试常问\n\n常见的问法是先问必要条件，再问怎么预防。"))


class TestLoadUnitsChild(unittest.TestCase):
    def _store(self):
        return _FakeStore(
            docs=["片一", "片二"],
            metas=[
                {"source": "a.md", "topic": "T", "start_index": 0},
                {"source": "a.md", "topic": "T", "start_index": 100},
            ],
            embs=[[1.0, 0.0], [0.0, 1.0]],
        )

    def test_passthrough_texts_and_keys(self):
        units = cq.load_units(self._store(), cq.GRANULARITY_CHILD)
        self.assertEqual(units["granularity"], cq.GRANULARITY_CHILD)
        self.assertEqual(units["texts"], ["片一", "片二"])
        self.assertEqual(units["keys"], [("a.md", 0), ("a.md", 100)])
        self.assertEqual(units["child_count"], 2)
        self.assertIsNone(units["parent_count"])

    def test_child_to_unit_is_identity(self):
        units = cq.load_units(self._store(), cq.GRANULARITY_CHILD)
        self.assertEqual(units["child_to_unit"][("a.md", 100)], ("a.md", 100))

    def test_vectors_kept_as_is(self):
        units = cq.load_units(self._store(), cq.GRANULARITY_CHILD)
        self.assertEqual([list(v) for v in units["vecs"]], [[1.0, 0.0], [0.0, 1.0]])


class TestLoadUnitsParent(unittest.TestCase):
    """parent 模式：聚合 + 重新 embed + child→parent 映射。"""

    def _store(self):
        p0 = {
            "source": "a.md",
            "topic": "T",
            "parent_id": "a.md#0",
            "parent_title": "## 结论",
            "parent_start": 6,
            "parent_text": "## 结论\n\n正文甲。",
        }
        p1 = {
            "source": "a.md",
            "topic": "T",
            "parent_id": "a.md#1",
            "parent_title": "## 原理",
            "parent_start": 300,
            "parent_text": "## 原理\n\n正文乙。",
        }
        return _FakeStore(
            docs=["child0", "child1", "child2"],
            metas=[
                {**p0, "start_index": 6},
                {**p0, "start_index": 100},
                {**p1, "start_index": 300},
            ],
            embs=[[1.0, 0.0], [0.9, 0.1], [0.0, 1.0]],
        )

    def test_collapses_children_into_parents(self):
        with mock.patch("agent.rag.indexer.get_embeddings") as fake:
            fake.return_value.embed_documents.return_value = [[1.0, 0.0], [0.0, 1.0]]
            units = cq.load_units(self._store(), cq.GRANULARITY_PARENT)
        self.assertEqual(units["granularity"], cq.GRANULARITY_PARENT)
        self.assertEqual(units["parent_count"], 2)
        self.assertEqual(units["child_count"], 3)
        self.assertEqual(units["keys"], [("a.md", 6), ("a.md", 300)])
        # 注入的是整节正文，不是 200 字碎片
        self.assertIn("正文甲。", units["texts"][0])

    def test_reembeds_parent_text_instead_of_averaging(self):
        # parent 向量必须来自重新 embed：child 向量均值会系统性抬高独立性
        with mock.patch("agent.rag.indexer.get_embeddings") as fake:
            fake.return_value.embed_documents.return_value = [[7.0, 8.0], [9.0, 10.0]]
            units = cq.load_units(self._store(), cq.GRANULARITY_PARENT)
            fed = fake.return_value.embed_documents.call_args[0][0]
        self.assertEqual([list(v) for v in units["vecs"]], [[7.0, 8.0], [9.0, 10.0]])
        self.assertEqual(len(fed), 2)  # 喂进去的是 2 个 parent 正文

    def test_child_to_unit_maps_to_parent_start(self):
        with mock.patch("agent.rag.indexer.get_embeddings") as fake:
            fake.return_value.embed_documents.return_value = [[1.0, 0.0], [0.0, 1.0]]
            units = cq.load_units(self._store(), cq.GRANULARITY_PARENT)
        # 两个 child 都收敛到同一个 parent 坐标，综合度不会把同一小节数成两片
        self.assertEqual(units["child_to_unit"][("a.md", 6)], ("a.md", 6))
        self.assertEqual(units["child_to_unit"][("a.md", 100)], ("a.md", 6))
        self.assertEqual(units["child_to_unit"][("a.md", 300)], ("a.md", 300))

    def test_flat_piece_in_mixed_store_maps_to_itself(self):
        store = self._store()
        store._metas.append({"source": "b.md", "topic": "T", "start_index": 0})
        store._docs.append("扁平片")
        store._embs.append([0.5, 0.5])
        with mock.patch("agent.rag.indexer.get_embeddings") as fake:
            fake.return_value.embed_documents.return_value = [[1.0, 0.0], [0.0, 1.0], [0.5, 0.5]]
            units = cq.load_units(store, cq.GRANULARITY_PARENT)
        self.assertEqual(units["child_to_unit"][("b.md", 0)], ("b.md", 0))


class _FakeStore:
    """只实现 get() 的最小假库（load_units 只用这一个方法）。"""

    def __init__(self, docs, metas, embs):
        self._docs = docs
        self._metas = metas
        self._embs = embs

    def get(self, include=None):
        return {"documents": self._docs, "metadatas": self._metas, "embeddings": self._embs}


class TestLocateChunk(unittest.TestCase):
    SRC = "第一段完整结束。第二段从这里开始继续讲述。"

    def test_exact_start_index_trusted(self):
        start, trusted = cq.locate_chunk(TestLocateChunk.SRC, 8, "第二段从这里开始继续讲述。")
        self.assertTrue(trusted)
        self.assertEqual(start, 8)

    def test_drifted_offset_relocated_by_probe(self):
        # 切片器 strip 掉前导空白导致 start_index 漂移：按前缀回搜要能找回来
        src = "前缀噪声" + TestLocateChunk.SRC
        start, trusted = cq.locate_chunk(src, 0, "第二段从这里开始继续讲述。")
        self.assertTrue(trusted)
        self.assertEqual(start, src.find("第二段"))

    def test_not_found_returns_untrusted(self):
        start, trusted = cq.locate_chunk("完全无关的文本。", 0, "不存在的切片内容。")
        self.assertFalse(trusted)
        self.assertEqual(start, 0)


class TestEndCutKind(unittest.TestCase):
    SRC = "甲句结束。乙句未完"

    def test_eof(self):
        self.assertEqual(cq.end_cut_kind(TestEndCutKind.SRC, 6, len(TestEndCutKind.SRC) - 6), "eof")

    def test_terminator_next_is_ok(self):
        # 切到句号之前（句号本身留给下一片）也算完整收尾
        self.assertEqual(cq.end_cut_kind(TestEndCutKind.SRC, 0, 4), "ok")

    def test_mid_sentence(self):
        # 在"乙句未完"中间切断：下一个字符是正文
        self.assertEqual(cq.end_cut_kind(TestEndCutKind.SRC, 6, 2), "mid_sentence")


class TestGeneralistAggregation(unittest.TestCase):
    def test_three_distinct_topics_flagged(self):
        hits = {
            ("a.md", 0): {"死锁", "进程与线程", "TCP 与 UDP"},
            ("b.md", 0): {"死锁", "进程与线程"},
        }
        flagged = cq.generalist_from_hits(hits, 3)
        self.assertIn(("a.md", 0), flagged)
        self.assertNotIn(("b.md", 0), flagged)

    def test_same_topic_repeated_does_not_count(self):
        # 同一个主题的多条 query 命中不算综合——要的是"不同主题"的数量
        hits = {("a.md", 0): {"死锁"}}
        self.assertEqual(cq.generalist_from_hits(hits, 3), {})

    def test_sorted_by_topic_count_desc(self):
        hits = {
            ("a.md", 0): {"死锁", "进程与线程", "TCP 与 UDP", "虚拟内存与页面置换"},
            ("b.md", 0): {"死锁", "进程与线程", "TCP 与 UDP"},
        }
        flagged = cq.generalist_from_hits(hits, 3)
        self.assertEqual(list(flagged.keys()), [("a.md", 0), ("b.md", 0)])


if __name__ == "__main__":
    unittest.main()
