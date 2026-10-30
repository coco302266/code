"""注入多样性限额 cap_per_source 的单测（2026-10-26）。

背景：父子 mode 下去重只按 parent_id，一篇文档的好几个 `##` 小节可以同时进
top3——实测 6 条库内题里 4 条出现「3 个注入位全来自同一篇」
（见 scripts/diag_floor_margin.py 的注入多样性统计）。

这里只测纯函数本身的确定性行为；开关是否转默认，要看 L1 A/B 的结果。
"""

import importlib
import pathlib
import sys
import unittest

from langchain_core.documents import Document

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

# retriever 用包内相对导入，必须按包导入而不是单文件加载
R = importlib.import_module("agent.rag.retriever")


def doc(source: str, name: str) -> Document:
    return Document(page_content=name, metadata={"source": source, "start_index": 0})


def sources(docs: list[Document]) -> list[str]:
    return [(d.metadata or {}).get("source", "") for d in docs]


class TestCapPerSource(unittest.TestCase):
    def test_cap_zero_passthrough(self):
        """0 = 关闭限额：顺序与内容都原样返回（不擅自改默认行为）。"""
        docs = [doc("a.md", "1"), doc("a.md", "2"), doc("b.md", "3")]
        self.assertEqual(sources(R.cap_per_source(docs, 0)), ["a.md", "a.md", "b.md"])

    def test_cap_one_takes_each_source_once(self):
        """限额 1：同一篇只留最靠前的一条，名额让给次优文档。"""
        docs = [doc("a.md", "1"), doc("a.md", "2"), doc("b.md", "3")]
        out = R.cap_per_source(docs, 1)
        self.assertEqual(sources(out), ["a.md", "b.md", "a.md"])
        self.assertEqual([d.page_content for d in out[:2]], ["1", "3"])

    def test_cap_two_allows_two_per_source(self):
        docs = [doc("a.md", "1"), doc("a.md", "2"), doc("a.md", "3"), doc("b.md", "4")]
        self.assertEqual(sources(R.cap_per_source(docs, 2)), ["a.md", "a.md", "b.md", "a.md"])

    def test_overflow_kept_at_tail_for_fallback(self):
        """候选只有一篇文档时，超额条目仍排在末尾兜底——宁可复用也不能少给资料。"""
        docs = [doc("a.md", "1"), doc("a.md", "2"), doc("a.md", "3")]
        out = R.cap_per_source(docs, 1)
        self.assertEqual(len(out), 3)
        self.assertEqual([d.page_content for d in out], ["1", "2", "3"])

    def test_top_k_slice_after_cap_gets_diverse_head(self):
        """调用方截 [:3] 后，前 3 位必须是互不相同的文档。"""
        docs = [
            doc("a.md", "1"), doc("a.md", "2"), doc("a.md", "3"),
            doc("b.md", "4"), doc("c.md", "5"),
        ]
        head = R.cap_per_source(docs, 1)[:3]
        self.assertEqual(sources(head), ["a.md", "b.md", "c.md"])

    def test_stable_order_within_cap(self):
        """同一顺序的输入必须得到完全相同的输出（trace 与断言依赖稳定性）。"""
        docs = [doc("b.md", "1"), doc("a.md", "2"), doc("b.md", "3")]
        first = [d.page_content for d in R.cap_per_source(docs, 1)]
        second = [d.page_content for d in R.cap_per_source(docs, 1)]
        self.assertEqual(first, second)
        self.assertEqual(first, ["1", "2", "3"])

    def test_empty_and_missing_source(self):
        self.assertEqual(R.cap_per_source([], 1), [])
        # metadata 里没有 source 的片（例如某些加载器的产物）按同一空值聚合
        one = Document(page_content="x", metadata={})
        two = Document(page_content="y", metadata={})
        self.assertEqual(len(R.cap_per_source([one, two], 1)), 2)
        self.assertEqual(sources(R.cap_per_source([one, two], 1)), ["", ""])

    def test_does_not_mutate_input(self):
        docs = [doc("a.md", "1"), doc("a.md", "2")]
        R.cap_per_source(docs, 1)
        self.assertEqual(len(docs), 2)
        self.assertEqual(sources(docs), ["a.md", "a.md"])


class TestEarlyQuotaSemantics(unittest.TestCase):
    """配额放在 RRF 之后、重排之前时的语义（U9）。

    关键差别是「有没有替补可让位」：融合保留 coarse_k*2 条候选时，超额条目会被
    `[:coarse_k]` 截掉，多样性才真的换来；候选不够时用超额条目兜满，不减注入条数。
    """

    def test_surplus_pool_lets_other_docs_in(self):
        """候选池里有别篇时就必须是「每篇轮流进」——末端限额救不回来的正是这种。

        位置的重要性：这里调换的是**进入重排的候选**，重排只在池内排序，
        池里没有别的文档，再怎么排也排不出来。
        """
        fused = [
            doc("a.md", "1"), doc("a.md", "2"), doc("a.md", "3"), doc("a.md", "4"),
            doc("b.md", "5"), doc("c.md", "6"),
        ]
        head = R.cap_per_source(fused, 1)[:3]
        self.assertEqual(sources(head), ["a.md", "b.md", "c.md"])

    def test_scarce_pool_still_fills_slots(self):
        """只有一篇答得上时不许掉注入条数：宁可复用同篇第二小节，也不能少给资料。"""
        fused = [doc("a.md", "1"), doc("a.md", "2"), doc("a.md", "3")]
        head = R.cap_per_source(fused, 1)[:3]
        self.assertEqual(sources(head), ["a.md", "a.md", "a.md"])
        self.assertEqual(len(head), 3)

    def test_rrf_then_quota_keeps_diverse_head(self):
        """端到端口径：RRF 融合（保留两倍候选）→ 配额 → 截回 coarse_k。

        两路都命中同一篇时，向量排名更靠前，若不配额则名额被它吃满；
        BM25 的次选文档正是替补来源，这正是「多留一倍候选」的理由。
        """
        vec = [doc("a.md", "v1"), doc("a.md", "v2"), doc("a.md", "v3")]
        sparse = [doc("b.md", "s1"), doc("c.md", "s2"), doc("a.md", "s3")]
        fused = R._rrf_fuse([vec, sparse], limit=6)
        head = R.cap_per_source(fused, 1)[:3]
        self.assertEqual(len(head), 3)
        self.assertEqual(len(set(sources(head))), 3)

    def test_quota_two_allows_second_section_of_same_doc(self):
        """配额 2 意味着「每篇最多给两个小节」兜住连续阅读的理解成本。"""
        fused = [doc("a.md", "1"), doc("a.md", "2"), doc("a.md", "3"), doc("b.md", "4")]
        head = R.cap_per_source(fused, 2)[:3]
        self.assertEqual(sources(head), ["a.md", "a.md", "b.md"])


if __name__ == "__main__":
    unittest.main()
