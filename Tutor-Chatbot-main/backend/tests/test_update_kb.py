"""增量更新（scripts/update_kb.py）的单测。

只覆盖**纯函数**：scan_disk / read_library / plan_update / needs_full_rebuild，
以及 indexer 新增的 file_hash / chunk_params_hash / build_chunks 的字段写入。
真正写向量库的部分由 scripts/verify_incremental.py 做端到端等价性验证，
那里要跑 embedding，不适合进单测。
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent.rag.indexer import (
    build_chunks,
    chunk_params_hash,
    chunk_params_signature,
    file_hash,
    load_markdown_documents,
)
from langchain_core.documents import Document
from scripts.update_kb import (
    infer_chunk_mode,
    needs_full_rebuild,
    plan_update,
    read_library,
    scan_disk,
)


class TestFileHash(unittest.TestCase):
    def test_same_bytes_same_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a.md"
            b = Path(tmp) / "b.md"
            a.write_text("同一份内容", encoding="utf-8")
            b.write_text("同一份内容", encoding="utf-8")
            self.assertEqual(file_hash(a), file_hash(b))

    def test_content_change_changes_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a.md"
            a.write_text("改前", encoding="utf-8")
            before = file_hash(a)
            a.write_text("改后", encoding="utf-8")
            self.assertNotEqual(before, file_hash(a))

    def test_hash_is_short(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a.md"
            a.write_text("x", encoding="utf-8")
            self.assertEqual(len(file_hash(a)), 16)


class TestChunkParamsHash(unittest.TestCase):
    def test_mode_changes_hash(self):
        self.assertNotEqual(chunk_params_hash(True), chunk_params_hash(False))

    def test_signature_follows_env(self):
        env = {
            "RAG_CHILD_CHUNK_SIZE": "200",
            "RAG_CHILD_CHUNK_OVERLAP": "40",
            "RAG_CHILD_MIN_CHARS": "40",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            base = chunk_params_hash(True)
            self.assertEqual(chunk_params_signature(True)["child_size"], 200)

        env["RAG_CHILD_CHUNK_SIZE"] = "300"
        with mock.patch.dict(os.environ, env, clear=False):
            self.assertNotEqual(base, chunk_params_hash(True))
            self.assertEqual(chunk_params_signature(True)["child_size"], 300)

    def test_overlap_change_is_detected(self):
        """改 overlap 也要能被发现，否则增量会漏掉需要重建的情况。"""
        with mock.patch.dict(os.environ, {"RAG_CHILD_CHUNK_OVERLAP": "40"}, clear=False):
            before = chunk_params_hash(True)
        with mock.patch.dict(os.environ, {"RAG_CHILD_CHUNK_OVERLAP": "60"}, clear=False):
            self.assertNotEqual(before, chunk_params_hash(True))


class TestBuildChunksWritesParamsHash(unittest.TestCase):
    def test_every_chunk_has_params_hash(self):
        docs = [
            Document(
                page_content="# 标题\n\n## 小节甲\n\n这是小节甲的正文内容，足够长一点。",
                metadata={"source": "a.md"},
            )
        ]
        with mock.patch.dict(os.environ, {"RAG_PARENT_CHILD": "1"}, clear=False):
            chunks = build_chunks(docs)
        self.assertTrue(chunks)
        expected = chunk_params_hash(True)
        for chunk in chunks:
            self.assertEqual(chunk.metadata.get("chunk_params_hash"), expected)

    def test_flat_mode_also_tagged(self):
        docs = [Document(page_content="# 标题\n\n正文内容。", metadata={"source": "a.md"})]
        with mock.patch.dict(os.environ, {"RAG_PARENT_CHILD": "0"}, clear=False):
            chunks = build_chunks(docs)
        for chunk in chunks:
            self.assertEqual(chunk.metadata.get("chunk_params_hash"), chunk_params_hash(False))


class TestScanDisk(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kbscan_")
        root = Path(self.tmp) / "cs" / "os"
        root.mkdir(parents=True)
        (root / "a.md").write_text("# 甲\n\n## 一节\n\n内容甲。\n", encoding="utf-8")
        (root / "b.md").write_text("# 乙\n\n## 一节\n\n内容乙。\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_source_uses_forward_slash(self):
        disk = scan_disk(self.tmp)
        self.assertEqual(sorted(disk), ["cs/os/a.md", "cs/os/b.md"])

    def test_hash_matches_file(self):
        disk = scan_disk(self.tmp)
        self.assertEqual(disk["cs/os/a.md"], file_hash(Path(self.tmp) / "cs" / "os" / "a.md"))

    def test_hash_changes_after_edit(self):
        before = scan_disk(self.tmp)["cs/os/a.md"]
        (Path(self.tmp) / "cs" / "os" / "a.md").write_text("# 甲\n\n改了。\n", encoding="utf-8")
        self.assertNotEqual(before, scan_disk(self.tmp)["cs/os/a.md"])

    def test_document_metadata_carries_doc_hash(self):
        """doc_hash 由 load_markdown_documents 写入，scan_disk 依赖它。"""
        docs = load_markdown_documents(self.tmp)
        for doc in docs:
            self.assertTrue(doc.metadata.get("doc_hash"))


class TestReadLibrary(unittest.TestCase):
    def test_groups_by_source(self):
        metas = [
            {"source": "a.md", "doc_hash": "h1", "chunk_params_hash": "p1"},
            {"source": "a.md", "doc_hash": "h1", "chunk_params_hash": "p1"},
            {"source": "b.md", "doc_hash": "h2", "chunk_params_hash": "p1"},
        ]
        lib, params = read_library(metas)
        self.assertEqual(lib, {"a.md": "h1", "b.md": "h2"})
        self.assertEqual(params, {"p1"})

    def test_skips_chunks_without_source(self):
        lib, params = read_library([{"topic": "x"}, None, {}])
        self.assertEqual(lib, {})
        self.assertEqual(params, set())

    def test_missing_doc_hash_becomes_empty_string(self):
        """旧库没有 doc_hash：记为 ""，由 needs_full_rebuild 判定为需重建。"""
        lib, _ = read_library([{"source": "a.md"}])
        self.assertEqual(lib["a.md"], "")

    def test_empty_library(self):
        self.assertEqual(read_library([]), ({}, set()))

    def test_detects_mixed_params(self):
        """库里混着两种切片参数 → 必须判定为需全量重建。"""
        _, params = read_library(
            [
                {"source": "a.md", "chunk_params_hash": "p1"},
                {"source": "b.md", "chunk_params_hash": "p2"},
            ]
        )
        self.assertEqual(params, {"p1", "p2"})
        self.assertTrue(needs_full_rebuild(params, "p1", lib_size=2))


class TestPlanUpdate(unittest.TestCase):
    def test_new_file_goes_to_add(self):
        to_add, to_del = plan_update({"a.md": "h1", "b.md": "h2"}, {"a.md": "h1"})
        self.assertEqual(to_add, ["b.md"])
        self.assertEqual(to_del, [])

    def test_changed_file_goes_to_add(self):
        to_add, to_del = plan_update({"a.md": "h1-new"}, {"a.md": "h1-old"})
        self.assertEqual(to_add, ["a.md"])
        self.assertEqual(to_del, [])

    def test_removed_file_goes_to_del(self):
        to_add, to_del = plan_update({}, {"a.md": "h1"})
        self.assertEqual(to_add, [])
        self.assertEqual(to_del, ["a.md"])

    def test_no_change(self):
        self.assertEqual(plan_update({"a.md": "h1"}, {"a.md": "h1"}), ([], []))

    def test_add_and_del_together(self):
        to_add, to_del = plan_update({"b.md": "h2"}, {"a.md": "h1"})
        self.assertEqual(to_add, ["b.md"])
        self.assertEqual(to_del, ["a.md"])

    def test_empty_library_builds_everything(self):
        to_add, to_del = plan_update({"a.md": "h1", "b.md": "h2"}, {})
        self.assertEqual(to_add, ["a.md", "b.md"])
        self.assertEqual(to_del, [])

    def test_results_are_sorted(self):
        """排序保证多次运行的变更集顺序稳定，日志与测试才好比对。"""
        to_add, _ = plan_update({s: "h" for s in ("c.md", "a.md", "b.md")}, {})
        self.assertEqual(to_add, ["a.md", "b.md", "c.md"])


class TestNeedsFullRebuild(unittest.TestCase):
    def test_empty_library_does_not_need_rebuild(self):
        """空库直接走增量即可（等价于首次全量插入），不必先重建再增量。"""
        self.assertFalse(needs_full_rebuild({"p1"}, "p1", lib_size=0))

    def test_matching_params_is_fine(self):
        self.assertFalse(needs_full_rebuild({"p1"}, "p1", lib_size=10))

    def test_legacy_library_without_params(self):
        self.assertTrue(needs_full_rebuild(set(), "p1", lib_size=10))

    def test_params_changed(self):
        self.assertTrue(needs_full_rebuild({"p-old"}, "p-new", lib_size=10))

    def test_legacy_empty_library_is_not_forced(self):
        """库里一篇都没有时不该触发重建，否则首次构建会白跑一遍。"""
        self.assertFalse(needs_full_rebuild(set(), "p1", lib_size=0))


class TestInferChunkMode(unittest.TestCase):
    """切片模式自动对齐（2026-10-26 实测修复）。

    父子模式是 opt-in，忘记加 --parent-child 时脚本会按 flat 算参数，
    与库内父子参数不符 → 每篇新增都退化成全量重建。这里保证能从库内
    参数快照反推出建库模式。
    """

    def test_parent_child_library_is_inferred(self):
        """库按父子建的、本次没指定模式 → 反推为父子。"""
        self.assertTrue(infer_chunk_mode({chunk_params_hash(True)}, False, False))

    def test_flat_library_is_inferred(self):
        self.assertFalse(infer_chunk_mode({chunk_params_hash(False)}, False, False))

    def test_explicit_switch_wins(self):
        """显式 --parent-child 优先：即使是 flat 库也不该被改写成 flat。"""
        self.assertIsNone(infer_chunk_mode({chunk_params_hash(False)}, True, False))

    def test_env_enabled_wins(self):
        """环境变量已开启时同样不覆盖（显式优先于推断）。"""
        self.assertIsNone(infer_chunk_mode({chunk_params_hash(False)}, False, True))

    def test_unknown_params_gives_up(self):
        """库内参数对不上任何一种模式（比如参数刚升级）→ 不猜，交给全量回退。"""
        self.assertIsNone(infer_chunk_mode({"p-unknown"}, False, False))

    def test_mixed_params_gives_up(self):
        """库内混参数时不对齐：反正要全量重建，猜哪种都没意义。"""
        mixed = {chunk_params_hash(True), chunk_params_hash(False)}
        self.assertIsNone(infer_chunk_mode(mixed, False, False))

    def test_inference_prevents_false_full_rebuild(self):
        """端到端：推断出的参数必须让 needs_full_rebuild 判 False。

        这正是修复的验收点——父子库 + 没带开关，推断前会被判成全量重建。
        """
        lib_params = {chunk_params_hash(True)}
        current = chunk_params_hash(False)
        self.assertTrue(needs_full_rebuild(lib_params, current, lib_size=10))
        inferred = infer_chunk_mode(lib_params, False, False)
        self.assertFalse(
            needs_full_rebuild(lib_params, chunk_params_hash(inferred), lib_size=10)
        )


if __name__ == "__main__":
    unittest.main()
