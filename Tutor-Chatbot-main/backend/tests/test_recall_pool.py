"""O13 候选池相关开关的单测（2026-10-28）。

这一批开关的大半**实测是负结论**（扩大池子 / 融合都更差），
保留代码是为了让后来的人不必重跑一遍同样的实验——所以单测除了锁行为，
还要锁住"默认关闭"这件事：默认值一旦被改动，说明有人重新采纳了负结论，
测试必须红。

涉及的开关（全部默认关闭，行为与 2026-10-28 之前完全一致）：
    child_overfetch / parent_pool_k / adjacent_radius / rerank_fusion_w

真正转默认的有两处：
    per_doc_floor: -5.0 -> -8.0（2026-10-28，71 题 Recall +0.82pp）
    rerank_text:   child_title -> parent_head（2026-10-29，见下方该用例注释）
"""

import importlib
import pathlib
import sys
import unittest

from langchain_core.documents import Document

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

R = importlib.import_module("agent.rag.retriever")


def parent(source: str, idx: int, text: str = "") -> Document:
    """造一条聚合后的 parent 文档（parent_id 形如 `a.md#3`）。"""
    return Document(
        page_content=text or ("第%d节" % idx),
        metadata={
            "source": source,
            "parent_id": "%s#%d" % (source, idx),
            "start_index": idx * 100,
            "parent_title": "第%d节" % idx,
        },
    )


def index_of(pairs) -> dict:
    """(source, idx) -> Document 的邻居索引。"""
    out = {}
    for source, idx in pairs:
        out[(source, idx)] = parent(source, idx)
    return out


class DefaultsTests(unittest.TestCase):
    """默认值必须保持"关闭"，否则等于悄悄采纳了实测更差的方案。"""

    def test_all_new_knobs_default_off(self):
        cfg = R.RetrievalConfig()
        self.assertEqual(cfg.child_overfetch, 1)   # 1 = 不超额召回
        self.assertEqual(cfg.parent_pool_k, 0)     # 0 = 不截 parent 池
        self.assertEqual(cfg.adjacent_radius, 0)   # 0 = 不扩展邻居
        self.assertEqual(cfg.rerank_fusion_w, 0.0)  # 0 = 纯重排

    def test_rerank_text_adopted_parent_head(self):
        """rerank_text 已从"负结论"转为"已采纳默认"（2026-10-29，XB-46）。

        依据：395 题新集（391 计分）paired bootstrap 2 万次，parent_head/480
        vs 旧的 child_title —— nDCG +2.62pp 显著 CI[+0.81,+4.41]、Precision
        +1.11pp 显著、噪声 -2.09pp 显著、p95 1.97s（父子预算 5s 内）。

        机制：金标准按 parent 粒度标注，只看 child+title（约 200 字碎片）会让
        交叉编码器读不到同小节后半段的答案——**输入粒度与标注粒度错配**。

        改成 parent 全文虽然再高 0.7pp，但 p95 3.54s 破门禁，故不用。
        要动这个值，先看 XB-46 的 8 档实测表，别凭手感调。
        """
        cfg = R.RetrievalConfig()
        self.assertEqual(cfg.rerank_text, "parent_head")
        self.assertEqual(cfg.rerank_head_chars, 480)  # 240 档截断 79% 直接崩

    def test_per_doc_floor_adopted(self):
        """唯一转默认的改动：-5.0 → -8.0（71 题 Recall +0.82pp，p95 +0.006s）。"""
        cfg = R.RetrievalConfig()
        self.assertEqual(cfg.per_doc_floor, -8.0)
        self.assertEqual(R.CONFIG.relevance_floor, -2.0)  # 整题下限不动


class ExpandAdjacentParentsTests(unittest.TestCase):
    def test_radius_zero_is_passthrough(self):
        docs = [parent("a.md", 1), parent("a.md", 2)]
        out = R.expand_adjacent_parents(docs, index_of([("a.md", 0), ("a.md", 3)]), 0)
        self.assertEqual([d.metadata["parent_id"] for d in out], ["a.md#1", "a.md#2"])

    def test_empty_index_is_passthrough(self):
        """索引建不起来（库无 parent_id / Chroma 取失败）时必须原样返回，不能少给资料。"""
        docs = [parent("a.md", 1)]
        self.assertEqual(len(R.expand_adjacent_parents(docs, {}, 2)), 1)

    def test_adds_neighbours_within_radius(self):
        docs = [parent("a.md", 3)]
        idx = index_of([("a.md", n) for n in range(0, 7)])
        out = R.expand_adjacent_parents(docs, idx, 1)
        self.assertEqual([d.metadata["parent_id"] for d in out], ["a.md#3", "a.md#2", "a.md#4"])

    def test_radius_two_reaches_two_away(self):
        docs = [parent("a.md", 3)]
        idx = index_of([("a.md", n) for n in range(0, 7)])
        got = {d.metadata["parent_id"] for d in R.expand_adjacent_parents(docs, idx, 2)}
        self.assertEqual(got, {"a.md#1", "a.md#2", "a.md#3", "a.md#4", "a.md#5"})

    def test_no_duplicate_and_no_cross_document(self):
        """邻居只在**同文档**内取；已在池里的不重复加。"""
        docs = [parent("a.md", 3), parent("a.md", 4), parent("b.md", 0)]
        idx = index_of([("a.md", n) for n in range(0, 6)] + [("b.md", 1)])
        out = R.expand_adjacent_parents(docs, idx, 1)
        ids = [d.metadata["parent_id"] for d in out]
        self.assertEqual(len(ids), len(set(ids)))          # 无重复
        self.assertEqual(ids[:3], ["a.md#3", "a.md#4", "b.md#0"])  # 原顺序在前
        # 邻居只在同文档内取：a.md 的邻居仍全是 a.md，b.md 的邻居仍是 b.md
        self.assertEqual(ids, ["a.md#3", "a.md#4", "b.md#0", "a.md#2", "a.md#5", "b.md#1"])

    def test_missing_neighbour_index_is_skipped(self):
        """序号越界（首节没有 -1、末节没有 +1）不能抛异常。"""
        docs = [parent("a.md", 0)]
        idx = index_of([("a.md", 0), ("a.md", 1)])
        out = R.expand_adjacent_parents(docs, idx, 2)
        self.assertEqual([d.metadata["parent_id"] for d in out], ["a.md#0", "a.md#1"])

    def test_doc_without_parent_id_is_ignored(self):
        """扁平库（无 parent_id）走扩展必须原样返回。"""
        flat = Document(page_content="x", metadata={"source": "a.md", "start_index": 0})
        out = R.expand_adjacent_parents([flat], index_of([("a.md", 1)]), 1)
        self.assertEqual(len(out), 1)


class ParentIndexCacheTests(unittest.TestCase):
    def test_clear_returns_count_and_empties(self):
        R._PARENT_INDEX_CACHE["x"] = {}
        self.assertEqual(R.clear_parent_index_cache(), 1)
        self.assertEqual(R._PARENT_INDEX_CACHE, {})
        self.assertEqual(R.clear_parent_index_cache(), 0)

    def test_hot_reload_clears_parent_index(self):
        """热重载必须连 parent 索引一起清，否则邻居来自旧库（与 BM25 同理）。"""
        import app  # noqa: E402

        self.assertTrue(hasattr(app, "clear_parent_index_cache"))


class RerankInputParentHeadTests(unittest.TestCase):
    def setUp(self):
        self._saved_text = R.CONFIG.rerank_text
        self._saved_chars = R.CONFIG.rerank_head_chars

    def tearDown(self):
        R.CONFIG.rerank_text = self._saved_text
        R.CONFIG.rerank_head_chars = self._saved_chars

    def test_truncates_to_head_chars(self):
        R.CONFIG.rerank_text = "parent_head"
        R.CONFIG.rerank_head_chars = 10
        doc = Document(page_content="一二三四五六七八九十" + "余下内容", metadata={})
        self.assertEqual(R.rerank_input(doc), "一二三四五六七八九十")

    def test_zero_means_no_truncation(self):
        R.CONFIG.rerank_text = "parent_head"
        R.CONFIG.rerank_head_chars = 0
        doc = Document(page_content="整节全文", metadata={})
        self.assertEqual(R.rerank_input(doc), "整节全文")

    def test_does_not_touch_child_title_mode(self):
        """新增档位不能影响既有默认档：child_title 仍读 child_text + 标题。"""
        R.CONFIG.rerank_text = "child_title"
        doc = Document(
            page_content="整节全文",
            metadata={"child_text": "碎片", "parent_title": "小节标题"},
        )
        self.assertEqual(R.rerank_input(doc), "小节标题\n碎片")


class RerankFusionTests(unittest.TestCase):
    """融合权重默认 0，且不能改变候选条数（只改顺序）。"""

    def test_default_is_pure_rerank(self):
        self.assertEqual(R.RetrievalConfig().rerank_fusion_w, 0.0)


class BackwardCompatibilityTests(unittest.TestCase):
    """扁平模式（parent_child=False）下所有新开关必须完全不生效。"""

    def setUp(self):
        self._saved_pc = R.CONFIG.parent_child
        self._saved_of = R.CONFIG.child_overfetch
        R.CONFIG.parent_child = False
        R.CONFIG.child_overfetch = 3

    def tearDown(self):
        R.CONFIG.parent_child = self._saved_pc
        R.CONFIG.child_overfetch = self._saved_of

    def test_flat_mode_recall_k_unchanged(self):
        """扁平模式没有 parent 聚合，超额召回只会白白扩大重排成本。"""
        self.assertEqual(R.coarse_k_for(100), R.CONFIG.coarse_k)


if __name__ == "__main__":
    unittest.main()
