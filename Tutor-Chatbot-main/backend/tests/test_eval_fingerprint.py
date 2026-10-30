"""eval/fingerprint.py 的单元测试（索引版本指纹）。

指纹是「这份基线还能不能和当前库比」的唯一判据，判据本身错一次，
后面所有退化检测就都是假的。所以这里锁死三件事：
    1. 指纹只认切片身份 + 正文（顺序无关、装饰性 metadata 无关）
    2. 少一个字、换一个 source、换一个 start_index 都要变
    3. 缺指纹是警告不是失败（跨库实验不该被硬拦）
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_core.documents import Document  # noqa: E402

from eval.fingerprint import (  # noqa: E402
    check_fingerprint,
    collection_fingerprint,
    compute_fingerprint,
    load_baseline_meta,
    save_baseline_meta,
)


def _doc(source: str, start: int, text: str, **extra) -> Document:
    meta = {"source": source, "start_index": start}
    meta.update(extra)
    return Document(page_content=text, metadata=meta)


class _FakeStore:
    """最小 vectorstore 替身，只要实现 fingerprint 用到的 `.get()`。"""

    def __init__(self, docs):
        self._docs = docs

    def get(self):
        return {
            "documents": [d.page_content for d in self._docs],
            "metadatas": [dict(d.metadata) for d in self._docs],
        }


class ComputeFingerprintTest(unittest.TestCase):
    def test_empty_is_deterministic(self):
        self.assertEqual(compute_fingerprint([]), compute_fingerprint([]))
        self.assertTrue(compute_fingerprint([]).startswith("sha256:"))

    def test_order_irrelevant(self):
        docs = [
            _doc("a.md", 0, "第一段"),
            _doc("b.md", 10, "第二段"),
            _doc("a.md", 20, "第三段"),
        ]
        self.assertEqual(compute_fingerprint(docs), compute_fingerprint(list(reversed(docs))))

    def test_content_change_changes_fingerprint(self):
        before = [_doc("a.md", 0, "死锁的四个必要条件")]
        after = [_doc("a.md", 0, "死锁的四个必要条件。")]
        self.assertNotEqual(compute_fingerprint(before), compute_fingerprint(after))

    def test_source_change_changes_fingerprint(self):
        before = [_doc("a.md", 0, "同一段正文")]
        after = [_doc("b.md", 0, "同一段正文")]
        self.assertNotEqual(compute_fingerprint(before), compute_fingerprint(after))

    def test_start_index_change_changes_fingerprint(self):
        before = [_doc("a.md", 0, "同一段正文")]
        after = [_doc("a.md", 1, "同一段正文")]
        self.assertNotEqual(compute_fingerprint(before), compute_fingerprint(after))

    def test_decorative_metadata_ignored(self):
        """topic / category 变了不算换库：指纹只覆盖身份与正文。"""
        before = [_doc("a.md", 0, "正文", topic="死锁")]
        after = [_doc("a.md", 0, "正文", topic="死锁预防", category="os")]
        self.assertEqual(compute_fingerprint(before), compute_fingerprint(after))

    def test_missing_metadata_tolerated(self):
        """切分器万一没写 source/start_index，不能让整条构建链崩掉。"""
        bare = Document(page_content="正文", metadata={})
        self.assertTrue(compute_fingerprint([bare]).startswith("sha256:"))


class CollectionFingerprintTest(unittest.TestCase):
    def test_matches_compute_on_same_chunks(self):
        docs = [_doc("a.md", 0, "第一段"), _doc("a.md", 30, "第二段")]
        self.assertEqual(collection_fingerprint(_FakeStore(docs)), compute_fingerprint(docs))

    def test_empty_collection(self):
        self.assertEqual(
            collection_fingerprint(_FakeStore([])), compute_fingerprint([])
        )


class BaselineMetaTest(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "baseline_meta.json"
            save_baseline_meta(
                "sha256:abc", 66, "BAAI/bge-small-zh-v1.5", cases_count=75, path=path
            )
            meta = load_baseline_meta(path)
            self.assertEqual(meta["index_fingerprint"], "sha256:abc")
            self.assertEqual(meta["embed_model"], "BAAI/bge-small-zh-v1.5")
            self.assertEqual(meta["chunks_count"], 66)
            self.assertEqual(meta["eval_cases_count"], 75)
            self.assertTrue(meta["created_at"])

    def test_missing_file_returns_empty(self):
        self.assertEqual(load_baseline_meta(Path(tempfile.gettempdir()) / "__nope__.json"), {})

    def test_corrupt_file_returns_empty(self):
        """基线文件被写坏时，宁可当「指纹未知」也不能让 --compare 直接崩。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text("{ not json", encoding="utf-8")
            self.assertEqual(load_baseline_meta(path), {})

    def test_extra_merged(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.json"
            save_baseline_meta("sha256:x", 1, "m", extra={"note": "扩库后重存"}, path=path)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["note"], "扩库后重存")


class CheckFingerprintTest(unittest.TestCase):
    def test_equal(self):
        ok, msg = check_fingerprint("sha256:a", "sha256:a")
        self.assertTrue(ok)
        self.assertIn("一致", msg)

    def test_mismatch_blocks(self):
        ok, msg = check_fingerprint("sha256:a", "sha256:b")
        self.assertFalse(ok)
        self.assertIn("不一致", msg)

    def test_missing_baseline_warns_only(self):
        ok, _ = check_fingerprint("sha256:a", "")
        self.assertTrue(ok)

    def test_missing_current_warns_only(self):
        ok, _ = check_fingerprint("", "sha256:b")
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
