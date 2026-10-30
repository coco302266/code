"""文档路由（L1）单测。

只测确定性分支：注入伪 BM25 打分与伪向量库，验证路由的排序、收窄、闸门、
回退与降级行为。真实路由召回（71/71 route@3）见
`scripts/calibrate_doc_router.py` 的输出，不在单测里跑——那需要真库与真模型。
"""

import importlib
import pathlib
import re
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.documents import Document

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

R = importlib.import_module("agent.rag.retriever")
DR = importlib.import_module("agent.rag.doc_router")


def _tok(text: str) -> list[str]:
    """测试用分词：按空白与中文标点切，避免依赖 jieba 的分词细节。"""
    return [t for t in re.split(r"[\s，。？、,.?：:；;]+", text.lower()) if t]


class _FakeStore:
    """最小向量库替身：只实现 route 用到的 similarity_search 与 count。"""

    def __init__(self, docs: list[Document]):
        self.docs = docs
        self._collection = SimpleNamespace(name="fake_kb", count=lambda: len(docs))
        self.filters: list = []
        self.fail_filter = False
        self.fail_all = False

    def similarity_search(self, query, k=4, filter=None):
        self.filters.append(filter)
        if self.fail_all:
            raise RuntimeError("boom")
        pool = self.docs
        if filter is not None:
            if self.fail_filter:
                raise RuntimeError("filter unsupported")
            allowed = set(filter["source"]["$in"])
            pool = [d for d in pool if d.metadata["source"] in allowed]
        return pool[:k]


def _doc(source: str, chunk: int = 0, text: str = "") -> Document:
    return Document(
        page_content=text or f"{source}-片段{chunk}",
        metadata={"source": source, "start_index": chunk},
    )


def _corpus(sources: dict[str, int]) -> list[Document]:
    return [_doc(s, i) for s, n in sources.items() for i in range(n)]


class _EnvGuard:
    """临时改环境变量，退出时精确还原（None 表示原本不存在）。"""

    def __init__(self, **env):
        self.env = env
        self.saved: dict[str, str | None] = {}

    def __enter__(self):
        import os

        for k, v in self.env.items():
            self.saved[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = str(v)
        return self

    def __exit__(self, *exc):
        import os

        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return False


class RouterBasicsTests(unittest.TestCase):
    def test_disabled_returns_off_without_touching_store(self):
        store = _FakeStore(_corpus({"a.md": 2, "b.md": 2, "c.md": 2, "d.md": 2}))
        with _EnvGuard(RAG_DOC_ROUTER="0"):
            route = DR.route_documents(store, "死锁", tokenize=_tok, bm25_get=_scorer({}))
        self.assertFalse(route.applied)
        self.assertEqual(route.reason, "off")
        self.assertEqual(store.filters, [])

    def test_too_few_documents_does_not_narrow(self):
        # 只有 3 篇：收窄到 3 篇等于没收窄，白白多一个失败面
        store = _FakeStore(_corpus({"a.md": 2, "b.md": 2, "c.md": 2}))
        route = DR.route_documents(store, "死锁", tokenize=_tok, bm25_get=_scorer({}))
        self.assertFalse(route.applied)
        self.assertEqual(route.reason, "too_few_docs")

    def test_no_signal_falls_back_to_whole_library(self):
        # 两路都没命中任何文档（全新术语）：不能判死，交给全库检索
        store = _FakeStore(_corpus({f"d{i}.md": 2 for i in range(5)}))
        with _EnvGuard(RAG_ROUTER_VEC_WEIGHT=0):
            route = DR.route_documents(
                store, "量子纠缠", tokenize=_tok, bm25_get=_scorer({})
            )
        self.assertFalse(route.applied)
        self.assertEqual(route.reason, "no_signal")

    def test_router_error_falls_back_instead_of_raising(self):
        # BM25 取值器炸了 → 整个路由失败，但不能把异常抛给检索主链路
        store = _FakeStore(_corpus({f"d{i}.md": 2 for i in range(5)}))

        def boom(query):
            raise RuntimeError("scorer down")

        route = DR.route_documents(store, "死锁", tokenize=_tok, bm25_get=boom)
        self.assertFalse(route.applied)
        self.assertEqual(route.reason, "error")

    def test_top_docs_limits_sources_and_keeps_order(self):
        store = _FakeStore(_corpus({f"d{i}.md": 3 for i in range(6)}))
        scorer = _scorer({"d0.md": 9.0, "d1.md": 7.0, "d2.md": 5.0, "d3.md": 3.0})
        with _EnvGuard(RAG_ROUTER_TOP_DOCS=2, RAG_ROUTER_VEC_WEIGHT=0):
            route = DR.route_documents(
                store, "死锁", tokenize=_tok, bm25_get=scorer
            )
        self.assertTrue(route.applied)
        self.assertEqual(route.sources, ["d0.md", "d1.md"])
        self.assertEqual(route.scores[0], 1.0)

    def test_trace_records_route(self):
        store = _FakeStore(_corpus({f"d{i}.md": 3 for i in range(6)}))
        trace: dict = {}
        DR.route_documents(
            store, "死锁", trace, tokenize=_tok, bm25_get=_scorer({"d2.md": 4.0})
        )
        self.assertEqual(trace["router"], "ok")
        self.assertTrue(trace["router_applied"])
        self.assertEqual(trace["router_docs"][0], "d2.md")
        self.assertIn("router_scores", trace)
        self.assertIn("router_ms", trace)


def _scorer(scores: dict[str, float], texts: dict[str, str] | None = None):
    """构造 bm25_get：按 source 给固定分，正文默认不含查询词。

    **正文不能把 query 拼进去**：覆盖率的词频统计用的就是这些正文，拼进去会让
    每个词都"出现在所有文档里"，IDF 权重全归零，覆盖率恒为空。
    """
    texts = texts or {}

    def _get(query: str):
        hits = []
        for source, score in scores.items():
            body = texts.get(source, f"{source} 正文")
            for i in range(3):
                hits.append((_doc(source, i, f"{body} 片段{i}"), score))
        return [h[0] for h in hits], [h[1] for h in hits]

    return _get


class Bm25AggregationTests(unittest.TestCase):
    def test_per_doc_topk_removes_length_bias(self):
        """长文档命中片多，不能靠"片数多"赢过"分高但片少"的文档。

        短文档 2 片各 5 分（真实相关），长文档 10 片各 1 分（弱相关）。
        全量求和两者都是 10 分打平；按篇取 top-2 后是 10 : 2，短文档胜出。
        """
        docs = [_doc("short.md", i, f"短{i}") for i in range(2)]
        docs += [_doc("long.md", i, f"长{i}") for i in range(10)]
        docs += [_doc("f1.md", 0, "填充一"), _doc("f2.md", 0, "填充二")]
        store = _FakeStore(docs)
        scores = {
            "short.md": 5.0, "long.md": 1.0, "f1.md": 0.1, "f2.md": 0.05,
        }

        def scorer(query):
            return docs, [scores[d.metadata["source"]] for d in docs]

        with _EnvGuard(RAG_ROUTER_BM25_TOPK=2, RAG_ROUTER_VEC_WEIGHT=0,
                       RAG_ROUTER_TOP_DOCS=1):
            route = DR.route_documents(
                store, "死锁", tokenize=_tok, bm25_get=scorer
            )
        self.assertEqual(route.sources, ["short.md"])


class GateTests(unittest.TestCase):
    def setUp(self):
        # a 含查询实词；b~e 只含「什么/区别/怎么」这类泛词
        self.docs = [
            _doc("a.md", 0, "死锁 的 预防 与 避免"),
            _doc("b.md", 0, "什么 区别 怎么"),
            _doc("c.md", 0, "什么 区别 怎么 有哪"),
            _doc("d.md", 0, "什么 区别 怎么 有哪 何时"),
            _doc("e.md", 0, "什么 区别 怎么 有哪 何时 为何"),
        ]
        self.store = _FakeStore(self.docs)
        # 覆盖全部 5 篇，否则路由会因「打分到的文档太少」直接放弃
        self.scorer = _scorer(
            {"a.md": 5.0, "b.md": 1.0, "c.md": 0.8, "d.md": 0.6, "e.md": 0.4},
            texts={
                "a.md": "死锁 的 预防 与 避免",
                "b.md": "什么 区别 怎么",
                "c.md": "什么 区别 怎么 有哪",
                "d.md": "什么 区别 怎么 有哪 何时",
                "e.md": "什么 区别 怎么 有哪 何时 为何",
            },
        )

    def _run(self, gate, query):
        with _EnvGuard(RAG_ROUTER_GATE=gate, RAG_ROUTER_VEC_WEIGHT=0,
                       RAG_ROUTER_TOP_DOCS=3):
            return DR.route_documents(
                self.store, query, tokenize=_tok, bm25_get=self.scorer
            )

    def test_gate_off_by_default_never_rejects(self):
        route = self._run(None, "红烧肉 怎么做")
        self.assertNotEqual(route.reason, "gate")
        self.assertTrue(route.applied)

    def test_gate_rejects_when_no_document_covers_query_terms(self):
        # 查询词在库内一篇都找不到 → IDF 加权覆盖率 0 → 判库外
        route = self._run(0.5, "红烧肉 怎么做")
        self.assertEqual(route.reason, "gate")
        self.assertFalse(route.applied)
        self.assertEqual(route.sources, [])
        self.assertIsNotNone(route.gate_score)

    def test_gate_passes_when_a_document_covers_query_terms(self):
        route = self._run(0.5, "死锁 预防")
        self.assertEqual(route.reason, "ok")
        self.assertTrue(route.applied)
        self.assertEqual(route.sources[0], "a.md")

    def test_common_words_do_not_inflate_coverage(self):
        """「什么/区别」全库都有，IDF 权重趋零，不该把覆盖率抬到 0.5 附近。

        这是闸门能不能用的关键：未加权时「死锁 什么」里 b.md 只命中泛词也能
        拿到 0.50 的覆盖率，与真实命中的文档混在一起；加权后应接近 0。
        """
        route = DR.route_documents(
            self.store, "死锁 什么", tokenize=_tok, bm25_get=self.scorer,
            force_coverage=True,
        )
        cover = dict(zip(route.sources, route.coverage))
        # a.md 命中高 IDF 的「死锁」，b.md 只命中泛词「什么」
        self.assertGreater(cover.get("a.md", 0.0), 0.8)
        self.assertLess(cover.get("b.md", 1.0), 0.15)

    def test_all_common_words_makes_gate_abstain(self):
        """查询全由泛词组成时覆盖率无从计算，闸门必须弃权而不是判库外。

        「没有证据」不等于「有反证」——判死会让短追问（「有什么区别？」）
        整轮静默零资料。
        """
        route = self._run(0.9, "什么 区别")
        self.assertNotEqual(route.reason, "gate")
        self.assertTrue(route.applied)

    def test_rare_term_drives_coverage(self):
        route = DR.route_documents(
            self.store, "死锁 预防", tokenize=_tok, bm25_get=self.scorer,
            force_coverage=True,
        )
        cover = dict(zip(route.sources, route.coverage))
        self.assertEqual(cover.get("a.md"), 1.0)


class ScopedRetrievalTests(unittest.TestCase):
    def test_filter_is_passed_to_vector_search(self):
        store = _FakeStore(_corpus({f"d{i}.md": 2 for i in range(5)}))
        R._scoped_similarity_search(store, "x", 3, frozenset({"d1.md", "d2.md"}))
        self.assertEqual(store.filters[-1], {"source": {"$in": ["d1.md", "d2.md"]}})

    def test_no_sources_means_plain_search(self):
        store = _FakeStore(_corpus({f"d{i}.md": 2 for i in range(5)}))
        R._scoped_similarity_search(store, "x", 3, None)
        self.assertEqual(store.filters[-1], None)

    def test_filter_failure_returns_empty_so_caller_can_fallback(self):
        store = _FakeStore(_corpus({f"d{i}.md": 2 for i in range(5)}))
        store.fail_filter = True
        self.assertEqual(
            R._scoped_similarity_search(store, "x", 3, frozenset({"d1.md"})), []
        )

    def test_bm25_respects_allowed_sources(self):
        """BM25 必须同步收窄：否则它会把手路由刚排除的文档塞回候选池。"""
        docs = [_doc(s, i) for s in ("a.md", "b.md") for i in range(2)]
        bm25 = _FakeBm25(docs)
        corpus = docs
        with patch.object(R, "_get_bm25_store", return_value=(bm25, corpus)):
            scoped = R._bm25_search(
                _FakeStore(docs), "死锁", 10, allowed_sources=frozenset({"b.md"})
            )
        self.assertTrue(scoped)
        self.assertTrue(all(d.metadata["source"] == "b.md" for d in scoped))


class _FakeBm25:
    def __init__(self, docs):
        self._docs = docs

    def get_scores(self, tokens):
        # 每篇都给 1.0，确保"能在全库搜到"的前提下验证收窄是否生效
        return [1.0 for _ in self._docs]


class RetrieverIntegrationTests(unittest.TestCase):
    """验证 retrieve_knowledge_docs 里路由的三条支路。"""

    def setUp(self):
        self.docs = [_doc(s, i, f"{s} 死锁 正文{i}") for s in
                     ("a.md", "b.md", "c.md", "d.md", "e.md") for i in range(3)]
        self.store = _FakeStore(self.docs)
        self.store.similarity_search = self._search
        self._patch = [
            patch.object(R, "_get_bm25_store", return_value=(_FakeBm25(self.docs), self.docs)),
            patch.object(R, "_count_chunks", return_value=len(self.docs)),
            patch.object(R, "get_reranker", return_value=None),
        ]
        for p in self._patch:
            p.start()
        self._saved_floors = (R.CONFIG.relevance_floor, R.CONFIG.per_doc_floor)
        R.CONFIG.relevance_floor = None
        R.CONFIG.per_doc_floor = None
        R.CONFIG.parent_child = False

    def tearDown(self):
        for p in self._patch:
            p.stop()
        R.CONFIG.relevance_floor, R.CONFIG.per_doc_floor = self._saved_floors

    def _search(self, query, k=4, filter=None):
        self.last_filter = filter
        pool = self.docs
        if filter is not None:
            allowed = set(filter["source"]["$in"])
            pool = [d for d in pool if d.metadata["source"] in allowed]
        return pool[:k]

    def test_retrieval_narrows_to_routed_sources(self):
        with _EnvGuard(RAG_DOC_ROUTER="1", RAG_ROUTER_TOP_DOCS=2,
                       RAG_ROUTER_VEC_WEIGHT=0):
            trace: dict = {}
            docs = R.retrieve_knowledge_docs(self.store, "死锁", trace=trace)
        self.assertTrue(trace["router_applied"])
        self.assertEqual(len(trace["router_docs"]), 2)
        self.assertTrue(docs)
        allowed = set(trace["router_docs"])
        self.assertTrue(all(d.metadata["source"] in allowed for d in docs))

    def test_empty_filtered_result_falls_back_to_whole_library(self):
        """路由到的文档没有切片（索引重建一半等）→ 退回全库，不能零资料。"""
        with _EnvGuard(RAG_DOC_ROUTER="1", RAG_ROUTER_VEC_WEIGHT=0):
            with patch.object(R, "_scoped_similarity_search", return_value=[]):
                trace: dict = {}
                docs = R.retrieve_knowledge_docs(self.store, "死锁", trace=trace)
        self.assertTrue(docs, "回退后应有结果")
        self.assertFalse(trace["router_applied"])
        self.assertIn("router_fallback", trace["degraded_components"])

    def test_router_disabled_keeps_old_behaviour(self):
        with _EnvGuard(RAG_DOC_ROUTER="0"):
            trace: dict = {}
            R.retrieve_knowledge_docs(self.store, "死锁", trace=trace)
        self.assertEqual(trace["router"], "off")
        self.assertFalse(trace["router_applied"])
        self.assertIsNone(self.last_filter)

    def test_gate_rejection_returns_no_docs(self):
        with _EnvGuard(RAG_DOC_ROUTER="1", RAG_ROUTER_GATE="0.99",
                       RAG_ROUTER_VEC_WEIGHT=0):
            trace: dict = {}
            docs = R.retrieve_knowledge_docs(self.store, "红烧肉怎么做", trace=trace)
        self.assertEqual(docs, [])
        self.assertEqual(trace["router"], "gate")
        self.assertEqual(trace["final_k"], 0)


if __name__ == "__main__":
    unittest.main()
