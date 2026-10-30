"""重排输入文本开关（RAG_RERANK_TEXT）的单测（2026-10-27）。

测的是**选文本**这一步，不加载重排模型（那是秒级且依赖本地缓存，不该进单测）。
判据是 O1 的 A/B 结论成立的前提：child 模式只改「喂给交叉编码器什么」，
不能顺带改注入文本、顺序或父子聚合的结果。
"""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("RAG_PARENT_CHILD", "1")

from langchain_core.documents import Document  # noqa: E402

from agent.rag import retriever as R  # noqa: E402


def child(source: str, parent_idx: int, text: str, parent_text: str) -> Document:
    """造一个 child 切片：正文是碎片，parent_text 是整节。"""
    return Document(
        page_content=text,
        metadata={
            "source": source,
            "parent_id": f"{source}#{parent_idx}",
            "parent_text": parent_text,
            "start_index": 0,
        },
    )


class RerankTextTests(unittest.TestCase):
    def setUp(self):
        self._saved = R.CONFIG.rerank_text

    def tearDown(self):
        R.CONFIG.rerank_text = self._saved

    def test_parent_mode_reads_full_section(self):
        R.CONFIG.rerank_text = "parent"
        doc = Document(page_content="整节全文", metadata={"child_text": "碎片"})
        self.assertEqual(R.rerank_input(doc), "整节全文")

    def test_child_mode_reads_child_text(self):
        R.CONFIG.rerank_text = "child"
        doc = Document(page_content="整节全文", metadata={"child_text": "碎片"})
        self.assertEqual(R.rerank_input(doc), "碎片")

    def test_child_mode_falls_back_without_child_text(self):
        """单层库（无 child_text）必须退回正文，不能返回空串让重排打分失真。"""
        R.CONFIG.rerank_text = "child"
        doc = Document(page_content="整节全文", metadata={})
        self.assertEqual(R.rerank_input(doc), "整节全文")

    def test_unknown_value_behaves_like_parent(self):
        """手滑写成 child2 / 空串之类，必须退化成 parent，不能静默走新分支。"""
        for bad in ("child2", "", "paren", "children"):
            R.CONFIG.rerank_text = bad.lower()
            doc = Document(page_content="整节全文", metadata={"child_text": "碎片"})
            self.assertEqual(R.rerank_input(doc), "整节全文", msg=bad)

    def test_case_insensitive_config_value(self):
        """环境变量大小写不敏感（from_env 已 lower），`Child` 与 `child` 等价。"""
        for raw in ("Child", "CHILD", "child"):
            R.CONFIG.rerank_text = raw.lower()
            doc = Document(page_content="整节全文", metadata={"child_text": "碎片"})
            self.assertEqual(R.rerank_input(doc), "碎片", msg=raw)

    def test_child_mode_ignores_empty_child_text(self):
        R.CONFIG.rerank_text = "child"
        doc = Document(page_content="整节全文", metadata={"child_text": ""})
        self.assertEqual(R.rerank_input(doc), "整节全文")

    def test_child_title_mode_prepends_heading(self):
        """第三档：把小节标题拼到命中片前面，补回 child 丢掉的主题信号。"""
        R.CONFIG.rerank_text = "child_title"
        doc = Document(
            page_content="整节全文",
            metadata={"child_text": "碎片", "parent_title": "B+ 树索引为什么快"},
        )
        self.assertEqual(R.rerank_input(doc), "B+ 树索引为什么快\n碎片")

    def test_child_title_does_not_duplicate_inline_heading(self):
        """合并过的 child 自带标题行时不重复拼，避免白白加长序列。"""
        R.CONFIG.rerank_text = "child_title"
        doc = Document(
            page_content="整节全文",
            metadata={"child_text": "## 预防\n整节……", "parent_title": "预防"},
        )
        self.assertEqual(R.rerank_input(doc), "## 预防\n整节……")

    def test_child_title_without_title_behaves_like_child(self):
        """单层库没有 parent_title 时退化成 child，不能拼出 "None\n碎片"。"""
        R.CONFIG.rerank_text = "child_title"
        doc = Document(page_content="整节全文", metadata={"child_text": "碎片"})
        self.assertEqual(R.rerank_input(doc), "碎片")

    def test_child_title_without_child_text_falls_back_to_section(self):
        R.CONFIG.rerank_text = "child_title"
        doc = Document(page_content="整节全文", metadata={"parent_title": "预防"})
        self.assertEqual(R.rerank_input(doc), "整节全文")


class AggregateCarriesChildTextTests(unittest.TestCase):
    def test_aggregate_keeps_first_child_text(self):
        """聚合后正文换成整节，但要把「命中那片」留下来给重排用。"""
        docs = [
            child("os/deadlock.md", 0, "预防：破坏请求与保持条件", "## 预防\n整节……"),
            child("os/deadlock.md", 0, "预防：资源有序分配", "## 预防\n整节……"),
        ]
        out = R.aggregate_parent_docs(docs)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].page_content, "## 预防\n整节……")
        self.assertEqual(out[0].metadata["child_text"], "预防：破坏请求与保持条件")

    def test_aggregate_without_parent_id_untouched(self):
        """单层库的片没有 parent_id，聚合必须原样透传（不加字段、不改正文）。"""
        doc = Document(page_content="扁平切片", metadata={"source": "a.md"})
        out = R.aggregate_parent_docs([doc])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].page_content, "扁平切片")

    def test_injection_text_is_always_parent(self):
        """不管重排读什么，注入给模型的必须是整节——这是 O1 的核心边界。"""
        for mode in ("parent", "child", "child_title"):
            R.CONFIG.rerank_text = mode
            out = R.aggregate_parent_docs(
                [child("a.md", 0, "碎片", "整节全文")]
            )
            self.assertEqual(out[0].page_content, "整节全文", msg=mode)


if __name__ == "__main__":
    unittest.main()
