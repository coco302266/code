"""父子切片与父聚合检索的单测（PROPOSAL_RAG_SCALE §4.4，opt-in）。

覆盖四件事：
1. 分节器按 `##` 切小节、前言有正文才保留、无 `##` 时整篇一节；
2. 建库侧 child 内嵌 parent_id/parent_text/parent_start，且不产出纯标题碎块；
3. 检索侧按 parent_id 聚合去重、返回 parent 文本、无 parent_id 时原样透传；
4. **默认路径零变化**：RAG_PARENT_CHILD 未设置时 `parent_child_enabled()` 为假、
   单层切片器仍是 500/80——这条守卫防的是「opt-in 功能悄悄改掉默认行为」。

不碰网络、不建真库、不烧额度。
"""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.documents import Document

from agent.rag.indexer import (
    build_parent_child_documents,
    get_markdown_splitter,
    parent_child_enabled,
    split_markdown_sections,
)
from agent.rag.retriever import (
    CONFIG,
    RetrievalConfig,
    aggregate_parent_docs,
    retrieve_knowledge_docs,
)

def _doc(content, metadata):
    """Document 是 pydantic 模型，metadata 只能走关键字参数。"""
    return Document(page_content=content, metadata=metadata)


SAMPLE = """# 死锁

## 一句话结论

死锁是多个进程互相等待对方持有的资源。

## 标准答案

### 四个必要条件

互斥、占有且等待、不可抢占、循环等待。

## 面试常问

怎么预防死锁？
"""


class SectionSplitTests(unittest.TestCase):
    """分节器：`##` 为界，前言按有无正文决定去留。"""

    def test_splits_by_h2(self):
        sections = split_markdown_sections(SAMPLE)
        self.assertEqual(
            [title for title, _, _ in sections],
            ["一句话结论", "标准答案", "面试常问"],
        )
        for _, text, _ in sections:
            self.assertTrue(text.startswith("## "), "小节应以 ## 开头：%r" % text[:20])

    def test_offsets_are_increasing_and_locatable(self):
        sections = split_markdown_sections(SAMPLE)
        starts = [start for _, _, start in sections]
        self.assertEqual(starts, sorted(starts))
        for _, text, start in sections:
            self.assertEqual(SAMPLE[start:start + len(text)], text)

    def test_preamble_with_body_kept(self):
        text = "# 死锁\n\n这是前言正文。\n\n## 标准答案\n\n正文。\n"
        sections = split_markdown_sections(text)
        self.assertEqual([t for t, _, _ in sections], ["死锁", "标准答案"])
        self.assertIn("前言正文", sections[0][1])

    def test_heading_only_preamble_dropped(self):
        # SAMPLE 的首行只有 `# 死锁`，不应单独成一个「只含标题」的 parent
        titles = [t for t, _, _ in split_markdown_sections(SAMPLE)]
        self.assertNotIn("死锁", titles)

    def test_no_h2_falls_back_to_one_section(self):
        sections = split_markdown_sections("# 纯标题\n\n只有正文，没有小节。\n")
        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0][0], "纯标题")


class ParentChildBuildTests(unittest.TestCase):
    """建库侧：child 内嵌父信息，且不产出纯标题碎块。"""

    def _kids(self):
        docs = [
            Document(
                page_content=SAMPLE,
                metadata={"source": "cs/os/deadlock.md", "topic": "死锁", "category": "操作系统"},
            )
        ]
        return build_parent_child_documents(docs)

    def test_children_grouped_by_parent_id(self):
        kids = self._kids()
        self.assertTrue(kids)
        parent_ids = {k.metadata["parent_id"] for k in kids}
        self.assertEqual(len(parent_ids), 3)

    def test_child_carries_parent_text_and_title(self):
        for kid in self._kids():
            meta = kid.metadata
            self.assertTrue(meta["parent_text"].startswith("## "))
            self.assertTrue(meta["parent_title"])
            self.assertEqual(meta["parent_id"], "%s#%d" % ("cs/os/deadlock.md", int(meta["parent_id"].rsplit("#", 1)[1])))

    def test_start_index_is_absolute(self):
        for kid in self._kids():
            self.assertGreaterEqual(kid.metadata["start_index"], kid.metadata["parent_start"])

    def test_no_heading_only_child(self):
        # `## 标准答案` 后紧跟 `### 四个必要条件`，分隔符会让标题行单独成块；
        # 合并不做的话这里就会出现「只有标题没有正文」的检索噪音。
        for kid in self._kids():
            lines = [ln for ln in kid.page_content.splitlines() if ln.strip()]
            self.assertTrue(lines)
            self.assertFalse(
                all(ln.lstrip().startswith("#") for ln in lines),
                "出现纯标题片：%r" % kid.page_content,
            )


class ConfigSwitchTests(unittest.TestCase):
    """开关：默认关，环境变量可控。"""

    def test_default_is_disabled(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("RAG_PARENT_CHILD", None)
            self.assertFalse(parent_child_enabled())

    def test_env_enables(self):
        with mock.patch.dict(os.environ, {"RAG_PARENT_CHILD": "1"}):
            self.assertTrue(parent_child_enabled())
        with mock.patch.dict(os.environ, {"RAG_PARENT_CHILD": "0"}):
            self.assertFalse(parent_child_enabled())

    def test_from_env_reads_parent_child_and_final_k(self):
        with mock.patch.dict(
            os.environ, {"RAG_PARENT_CHILD": "1", "RAG_PARENT_FINAL_K": "2"}
        ):
            cfg = RetrievalConfig.from_env()
        self.assertTrue(cfg.parent_child)
        self.assertEqual(cfg.parent_final_k, 2)

    def test_flat_splitter_unchanged(self):
        # opt-in 的硬守卫：默认切片参数必须还是 500/80
        splitter = get_markdown_splitter()
        self.assertEqual(splitter._chunk_size, 500)
        self.assertEqual(splitter._chunk_overlap, 80)


class AggregateParentTests(unittest.TestCase):
    """父聚合：去重、保序、无 parent_id 透传。"""

    def test_dedupes_children_into_one_parent(self):
        docs = [
            _doc("c0", {"source": "a.md", "parent_id": "a.md#0", "parent_text": "## P0", "parent_start": 0, "start_index": 0}),
            _doc("c1", {"source": "a.md", "parent_id": "a.md#0", "parent_text": "## P0", "parent_start": 0, "start_index": 100}),
            _doc("c2", {"source": "a.md", "parent_id": "a.md#1", "parent_text": "## P1", "parent_start": 200, "start_index": 205}),
        ]
        out = aggregate_parent_docs(docs)
        self.assertEqual([d.page_content for d in out], ["## P0", "## P1"])
        self.assertEqual([d.metadata["chunk_kind"] for d in out], ["parent", "parent"])
        self.assertEqual([d.metadata["start_index"] for d in out], [0, 200])

    def test_passthrough_without_parent_id(self):
        flat = _doc("flat", {"source": "b.md", "start_index": 3})
        out = aggregate_parent_docs([flat])
        self.assertIs(out[0], flat)
        self.assertNotIn("chunk_kind", out[0].metadata)


class _FakeCollection:
    def __init__(self, count, name="fake"):
        self._count = count
        self.name = name

    def count(self):
        return self._count


class _FakeStore:
    """只实现 retrieve_knowledge_docs 用到的两个接口，避免建真库。"""

    def __init__(self, docs):
        self._docs = docs
        self._collection = _FakeCollection(len(docs))

    def similarity_search(self, query, k):
        return self._docs[:k]

    def get(self, include=None):
        return {"documents": [], "metadatas": []}


def _children():
    return [
        _doc("c0", {"source": "a.md", "parent_id": "a.md#0", "parent_text": "## P0", "parent_start": 0, "start_index": 0}),
        _doc("c1", {"source": "a.md", "parent_id": "a.md#0", "parent_text": "## P0", "parent_start": 0, "start_index": 100}),
        _doc("c2", {"source": "a.md", "parent_id": "a.md#1", "parent_text": "## P1", "parent_start": 200, "start_index": 205}),
    ]


class RetrieveIntegrationTests(unittest.TestCase):
    """检索链路：开父聚合返回 parent，关则保持原样。"""

    def setUp(self):
        self._saved = (
            CONFIG.parent_child,
            CONFIG.parent_final_k,
            CONFIG.final_k,
            CONFIG.enable_hybrid,
            CONFIG.enable_rerank,
        )
        # 只测召回→聚合→截断这一段：关掉 BM25 与重排，避免额外依赖
        CONFIG.enable_hybrid = False
        CONFIG.enable_rerank = False

    def tearDown(self):
        (
            CONFIG.parent_child,
            CONFIG.parent_final_k,
            CONFIG.final_k,
            CONFIG.enable_hybrid,
            CONFIG.enable_rerank,
        ) = self._saved

    def test_parent_mode_returns_parents(self):
        CONFIG.parent_child = True
        CONFIG.parent_final_k = 3
        trace = {}
        docs = retrieve_knowledge_docs(_FakeStore(_children()), "问题", trace=trace)
        self.assertEqual([d.page_content for d in docs], ["## P0", "## P1"])
        self.assertEqual([d.metadata.get("chunk_kind") for d in docs], ["parent", "parent"])
        self.assertTrue(trace["parent_child"])
        self.assertEqual(trace["parents_k"], 2)

    def test_default_mode_returns_children_unchanged(self):
        CONFIG.parent_child = False
        CONFIG.final_k = 4
        docs = retrieve_knowledge_docs(_FakeStore(_children()), "问题")
        self.assertEqual([d.page_content for d in docs], ["c0", "c1", "c2"])
        self.assertNotIn("chunk_kind", docs[0].metadata)


if __name__ == "__main__":
    unittest.main()
