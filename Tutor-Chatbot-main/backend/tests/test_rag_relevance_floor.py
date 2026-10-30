"""检索相关性下限（RAG_RELEVANCE_FLOOR）的单测（D11 修复，2026-10-24）。

只测确定性分支：伪造 encoder 打分，验证 floor 的过滤/全过滤/关闭三种行为。
真实 rerank 分数分布见 B_WORKLOG XB-10 实测（相关 ~0.3，无关 -2.8 / -6.0）。
"""

import importlib
import pathlib
import sys
import unittest
from unittest.mock import patch

from langchain_core.documents import Document

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

# retriever 用包内相对导入（from . import _env），必须按包导入而不是单文件加载
R = importlib.import_module("agent.rag.retriever")


def _docs(n: int) -> list[Document]:
    return [
        Document(page_content=f"片段{i}", metadata={"source": "a.md", "start_index": i})
        for i in range(n)
    ]


class _FakeEncoder:
    """按文档序号给固定 logits：片段0=0.5（相关）… 片段5=-3.0（无关）。"""

    SCORES = [0.5, 0.2, -0.4, -1.2, -2.0, -3.0]

    def rerank(self, query, texts, batch_size=1):
        return list(self.SCORES[: len(texts)])


class RelevanceFloorTests(unittest.TestCase):
    def setUp(self):
        self._original_floor = R.CONFIG.relevance_floor
        self._original_per_doc = R.CONFIG.per_doc_floor
        R.CONFIG.enable_rerank = True
        R.CONFIG.rerank_batch = 1

    def tearDown(self):
        R.CONFIG.relevance_floor = self._original_floor
        R.CONFIG.per_doc_floor = self._original_per_doc
        R.get_reranker.cache_clear()

    def _run(self, floor):
        R.CONFIG.relevance_floor = floor
        R.get_reranker.cache_clear()
        with patch.object(R, "get_reranker", return_value=_FakeEncoder()):
            trace: dict = {}
            docs = R._rerank_docs("测试问题", _docs(6), top_n=4, trace=trace)
        return docs, trace

    def test_top_score_above_floor_keeps_topk_untouched(self):
        # 整题判定口径：最高分达标 → top-4 原样保留（含低分尾巴），
        # 逐片裁剪会误伤 follow-up 场景（正确主题片分数也可能 < floor），不做
        docs, trace = self._run(-1.0)
        self.assertEqual(len(docs), 4)
        self.assertEqual(trace["relevance_top_score"], 0.5)
        self.assertNotIn("relevance_filtered", trace)
        self.assertEqual(trace["rerank"], "ok")

    def test_all_below_floor_returns_empty(self):
        # 最高分也低于下限 → 整题不相关 → 空列表（app 端据此走"显式无资料"分支）
        docs, trace = self._run(0.6)
        self.assertEqual(docs, [])
        self.assertEqual(trace["relevance_filtered"], 6)

    def test_floor_off_keeps_old_behavior(self):
        # floor=off（None）：旧行为，top-4 原样返回，不写 filtered 键
        docs, trace = self._run(None)
        self.assertEqual(len(docs), 4)
        self.assertNotIn("relevance_filtered", trace)

    def test_floor_still_applies_when_candidates_le_top_n(self):
        # 父子检索回归（2026-10-25）：RRF 后按 parent_id 聚合成 3 个 parent，
        # 正好等于 parent_final_k(=3)。旧实现在 len<=top_n 时直接跳过重排，
        # 没有分数 → relevance_floor 失效 → 库外题被硬塞资料。
        # 新契约：只要 floor 开着，候选再少也要打分。
        R.CONFIG.relevance_floor = 100.0  # 若生效则全过滤
        R.CONFIG.per_doc_floor = None
        R.get_reranker.cache_clear()
        with patch.object(R, "get_reranker", return_value=_FakeEncoder()):
            trace: dict = {}
            docs = R._rerank_docs("测试问题", _docs(3), top_n=4, trace=trace)
        self.assertEqual(docs, [])
        self.assertEqual(trace["rerank"], "ok")
        self.assertEqual(trace["relevance_filtered"], 3)

    def test_skip_only_when_all_floors_off(self):
        # 两级 floor 都关时，候选≤top_n 仍可跳过重排（纯延迟优化，无过滤需求）
        R.CONFIG.relevance_floor = None
        R.CONFIG.per_doc_floor = None
        R.get_reranker.cache_clear()
        trace: dict = {}
        docs = R._rerank_docs("测试问题", _docs(3), top_n=4, trace=trace)
        self.assertEqual(len(docs), 3)
        self.assertEqual(trace["rerank"], "skipped")


class _ScoredEncoder:
    """按调用方给的列表打分，用于构造「高分 top1 + 低分尾巴」这类分布。"""

    def __init__(self, scores):
        self.SCORES = list(scores)

    def rerank(self, query, texts, batch_size=1):
        return list(self.SCORES[: len(texts)])


class PerDocFloorTests(unittest.TestCase):
    """逐条下限（RAG_PER_DOC_FLOOR）：整题放行后再砍尾部明显不相关的片。

    与整题 floor 是两层，别混：整题挡「库外题硬塞资料」，这里挡
    「库内题被塞别的主题的片」。真实案例见 B_WORKLOG（问 TIME_WAIT 时
    top1 +4.635，尾部 -5.173 的 SJF/RR 时间片被注入）。
    """

    def setUp(self):
        self._orig_per_doc = R.CONFIG.per_doc_floor
        self._orig_floor = R.CONFIG.relevance_floor
        R.CONFIG.enable_rerank = True
        R.CONFIG.rerank_batch = 1

    def tearDown(self):
        R.CONFIG.per_doc_floor = self._orig_per_doc
        R.CONFIG.relevance_floor = self._orig_floor
        R.get_reranker.cache_clear()

    def _run(self, per_doc, scores, top_n=4):
        R.CONFIG.relevance_floor = -1.0
        R.CONFIG.per_doc_floor = per_doc
        R.get_reranker.cache_clear()
        with patch.object(R, "get_reranker", return_value=_ScoredEncoder(scores)):
            trace: dict = {}
            docs = R._rerank_docs("测试问题", _docs(len(scores)), top_n=top_n, trace=trace)
        return docs, trace

    def test_tail_garbage_cut_while_head_kept(self):
        # 真实分布：top1 +4.635 / +1.694 / -3.223 / -5.173（进程调度片）
        # -5.0 只砍最后一条，前三条（含 -3.223 的灰区）保留
        docs, trace = self._run(-5.0, [4.635, 1.694, -3.223, -5.173, -6.0, -7.0])
        self.assertEqual(len(docs), 3)
        self.assertEqual(trace["per_doc_filtered"], 1)
        self.assertEqual(len(trace["per_doc_scores"]), 4)

    def test_top1_always_kept_even_if_others_are_garbage(self):
        # 整题口径已保证最高分 ≥ -1.0 > -5.0，逐条砍永远砍不到空列表
        docs, _ = self._run(-5.0, [-0.5, -6.0, -7.0, -8.0, -9.0, -10.0])
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0].metadata["start_index"], 0)

    def test_no_cut_when_all_above_threshold(self):
        docs, trace = self._run(-5.0, [2.0, 1.0, 0.0, -1.0, -2.0, -3.0])
        self.assertEqual(len(docs), 4)
        self.assertEqual(trace["per_doc_filtered"], 0)

    def test_off_restores_old_behavior(self):
        # per_doc_floor=off：只做整题判定，低分尾巴原样注入
        docs, trace = self._run(None, [4.635, 1.694, -3.223, -5.173, -6.0, -7.0])
        self.assertEqual(len(docs), 4)
        self.assertNotIn("per_doc_filtered", trace)

    def test_whole_case_filtered_still_wins(self):
        # 整题不过线时直接返回空，不走逐条逻辑（两层不是替代关系）
        R.CONFIG.relevance_floor = 5.0
        R.CONFIG.per_doc_floor = -5.0
        R.get_reranker.cache_clear()
        with patch.object(
            R, "get_reranker", return_value=_ScoredEncoder([4.0, 3.0, 2.0, 1.0, 0.0, -1.0])
        ):
            docs = R._rerank_docs("测试问题", _docs(6), top_n=4, trace=None)
        self.assertEqual(docs, [])


class PerDocFloorEnvParsingTests(unittest.TestCase):
    def test_env_off_disables_per_doc_floor(self):
        with patch.dict("os.environ", {"RAG_PER_DOC_FLOOR": "off"}):
            cfg = R.RetrievalConfig.from_env()
        self.assertIsNone(cfg.per_doc_floor)

    def test_env_value_parses_and_default_is_minus_eight(self):
        # 默认 -8.0：O13（2026-10-28）父子模式 71 题实测由 -5.0 放宽而来，
        # Recall +0.82pp / Precision +0.47pp / p95 几乎不变，详见 CONFIG 注释。
        with patch.dict("os.environ", {"RAG_PER_DOC_FLOOR": "-2.5"}):
            cfg = R.RetrievalConfig.from_env()
        self.assertEqual(cfg.per_doc_floor, -2.5)
        with patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("RAG_PER_DOC_FLOOR", None)
            cfg = R.RetrievalConfig.from_env()
        self.assertEqual(cfg.per_doc_floor, -8.0)

    def test_env_garbage_falls_back_to_default(self):
        # 环境变量手滑写成非数字：告警并回退默认，不能让整条链路挂掉
        with patch.dict("os.environ", {"RAG_PER_DOC_FLOOR": "abc"}):
            cfg = R.RetrievalConfig.from_env()
        self.assertEqual(cfg.per_doc_floor, -8.0)


class FloorEnvParsingTests(unittest.TestCase):
    def test_env_off_disables_floor(self):
        with patch.dict("os.environ", {"RAG_RELEVANCE_FLOOR": "off"}):
            cfg = R.RetrievalConfig.from_env()
        self.assertIsNone(cfg.relevance_floor)

    def test_env_value_parses_and_default_is_minus_one(self):
        with patch.dict("os.environ", {"RAG_RELEVANCE_FLOOR": "-0.5"}):
            cfg = R.RetrievalConfig.from_env()
        self.assertEqual(cfg.relevance_floor, -0.5)
        with patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("RAG_RELEVANCE_FLOOR", None)
            cfg = R.RetrievalConfig.from_env()
        self.assertEqual(cfg.relevance_floor, -2.0)


if __name__ == "__main__":
    unittest.main()
