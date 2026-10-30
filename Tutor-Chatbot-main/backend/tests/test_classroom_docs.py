"""XB-23 方案 B：课堂资料（原件 + 派生索引 + 登记 + 第三路检索）的测试。

守的是「不报错但错」的那几类问题——这条线的主旋律就是静默失败：

1. **跨文档去重**：同一课堂两份 PDF 的第 1 页第 1 行不能被当成重复项丢掉；
2. **权限**：非教师删/传都要在建库、删片之前被挡；
3. **降级**：课堂库不存在或检索抛异常，/chat 必须拿到空列表而不是 500；
4. **泄漏**：课堂库检索只查本课堂 collection（物理边界），来源标签带 doc_id；
5. **注入口径**：三路顺序固定（上传 → 教材 → 课堂），sources 三种 origin 齐全；
6. **副作用顺序**：索引失败要留原件、登记失败要清切片与原件（不留孤儿）。
"""

from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import HTTPException, UploadFile
from langchain_core.documents import Document

import app as app_module
from agent.rag import classroom_store
from agent.rag.retriever import summarize_sources
from db.models import Base


@contextmanager
def _fake_db():
    yield MagicMock()


def _unwrapped(handler):
    return getattr(handler, "__wrapped__", handler)


class _FakeStore:
    """假 vectorstore：只实现检索与计数，记录调用参数供断言。"""

    def __init__(self, docs=None, chunks=7, distance=0.5):
        self.docs = list(docs or [])
        self.chunks = chunks
        # 向量距离（越小越相关）。默认 0.5，落在门限内；测门限时传大值。
        self.distance = distance
        self.search_calls = []

    def similarity_search(self, query, k=5, filter=None):
        self.search_calls.append({"query": query, "k": k, "filter": filter})
        return list(self.docs)

    def similarity_search_with_score(self, query, k=5, filter=None):
        self.search_calls.append({"query": query, "k": k, "filter": filter})
        return [(doc, self.distance) for doc in self.docs]


def _pdf(name="notes.pdf", body=b"%PDF-1.4\nhello"):
    return UploadFile(filename=name, file=BytesIO(body))


# ---------------------------------------------------------------- 命名与路径


class ClassroomStoreNamingTests(unittest.TestCase):
    def test_collection_name_is_per_classroom(self):
        self.assertEqual(
            classroom_store.classroom_collection_name(7), "classroom_7_docs"
        )

    def test_safe_filename_strips_traversal_and_keeps_pdf_suffix(self):
        # 上传文件名是不可信输入：`../` 能写到目录外，`\` 在 Windows 上换路径。
        self.assertEqual(
            classroom_store.safe_filename("../../etc/passwd.pdf"), "passwd.pdf"
        )
        self.assertNotIn("/", classroom_store.safe_filename("a/b\\c.pdf"))
        self.assertTrue(classroom_store.safe_filename("no_ext").endswith(".pdf"))
        self.assertEqual(classroom_store.safe_filename(""), "document.pdf")

    def test_source_label_is_prefixed(self):
        label = classroom_store.source_label(3, "abc123", "讲义.pdf")
        self.assertTrue(label.startswith("classroom:3/abc123_"))
        self.assertTrue(label.endswith(".pdf"))


# ---------------------------------------------------------------- 派生索引调用


class ClassroomIndexingTests(unittest.TestCase):
    def test_index_passes_persist_dir_and_metadata(self):
        captured = {}

        def fake_build(path, name, persist_directory=None, metadata_extra=None):
            captured.update(
                path=path,
                name=name,
                persist=persist_directory,
                extra=metadata_extra,
            )
            return _FakeStore()

        with patch.object(classroom_store, "build_index", fake_build):
            classroom_store.index_classroom_document(
                "/tmp/x.pdf", 5, "doc1", "讲义.pdf"
            )

        self.assertEqual(captured["name"], "classroom_5_docs")
        self.assertEqual(captured["persist"], str(classroom_store.CHROMA_DIR))
        extra = captured["extra"]
        self.assertEqual(extra["doc_id"], "doc1")
        self.assertEqual(extra["classroom_id"], 5)
        self.assertTrue(extra["source"].startswith("classroom:5/doc1_"))


# ---------------------------------------------------------------- 检索


class ClassroomRetrievalTests(unittest.TestCase):
    def setUp(self):
        classroom_store._STORE_CACHE.clear()

    def tearDown(self):
        classroom_store._STORE_CACHE.clear()

    def test_missing_collection_returns_empty_without_error(self):
        with patch.object(classroom_store, "collection_exists", return_value=False):
            self.assertEqual(
                classroom_store.retrieve_classroom_docs(9, "死锁怎么预防"), []
            )

    def test_dedupe_keeps_same_page_of_different_documents(self):
        """★ 本文件最要紧的一条：两份 PDF 的第 1 页第 1 行是两个不同的事实。

        去重键不带 doc_id 时，第二份会被当重复项整段丢掉——检索结果看起来
        正常（有资料、不报错），只是少了一份讲义，几乎不可能被发现。
        """
        docs = [
            Document(page_content="A", metadata={"doc_id": "a", "page_number": 1, "line_start": 1}),
            Document(page_content="B", metadata={"doc_id": "b", "page_number": 1, "line_start": 1}),
            Document(page_content="A", metadata={"doc_id": "a", "page_number": 1, "line_start": 1}),
        ]
        with patch.object(classroom_store, "collection_exists", return_value=True), patch.object(
            classroom_store, "Chroma", return_value=_FakeStore(docs)
        ), patch.object(classroom_store, "_embeddings", return_value=object()):
            out = classroom_store.retrieve_classroom_docs(1, "讲义")

        self.assertEqual(len(out), 2)
        self.assertEqual({d.metadata["doc_id"] for d in out}, {"a", "b"})

    def test_retrieval_exceptions_degrade_to_empty(self):
        """课堂库坏了不能 500：它是增强项。"""
        store = Mock()
        store.similarity_search_with_score.side_effect = RuntimeError("boom")
        with patch.object(classroom_store, "collection_exists", return_value=True), patch.object(
            classroom_store, "Chroma", return_value=store
        ), patch.object(classroom_store, "_embeddings", return_value=object()):
            self.assertEqual(classroom_store.retrieve_classroom_docs(1, "q"), [])

    def test_distance_gate_blocks_offtopic_query(self):
        """★ 离题问题不该硬塞课堂资料（2026-10-26 实测缺陷）。

        原先纯 top-k 无过滤：学生问「今天天气怎么样？」也会注入 3 片课文，
        污染上下文、还可能诱导模型拿课文内容硬答。
        """
        docs = [Document(page_content="TCP 三次握手", metadata={"doc_id": "a", "page_number": 2})]
        trace = {}
        with patch.object(classroom_store, "collection_exists", return_value=True), patch.object(
            classroom_store, "Chroma", return_value=_FakeStore(docs, distance=1.45)
        ), patch.object(classroom_store, "_embeddings", return_value=object()), patch.object(
            classroom_store, "_max_distance", return_value=1.25
        ):
            out = classroom_store.retrieve_classroom_docs(1, "今天天气怎么样？", trace=trace)

        self.assertEqual(out, [])
        self.assertEqual(trace["classroom"], "filtered_by_distance")
        self.assertEqual(trace["classroom_top_distance"], 1.45)

    def test_distance_gate_keeps_relevant_query(self):
        docs = [Document(page_content="TCP 三次握手", metadata={"doc_id": "a", "page_number": 2})]
        trace = {}
        with patch.object(classroom_store, "collection_exists", return_value=True), patch.object(
            classroom_store, "Chroma", return_value=_FakeStore(docs, distance=0.72)
        ), patch.object(classroom_store, "_embeddings", return_value=object()), patch.object(
            classroom_store, "_max_distance", return_value=1.25
        ):
            out = classroom_store.retrieve_classroom_docs(1, "TCP 三次握手", trace=trace)

        self.assertEqual(len(out), 1)
        self.assertEqual(trace["classroom"], "injected:1")

    def test_distance_gate_can_be_disabled(self):
        docs = [Document(page_content="无关内容", metadata={"doc_id": "a", "page_number": 2})]
        with patch.dict("os.environ", {"CLASSROOM_MAX_DISTANCE": "off"}), patch.object(
            classroom_store, "collection_exists", return_value=True
        ), patch.object(
            classroom_store, "Chroma", return_value=_FakeStore(docs, distance=9.9)
        ), patch.object(classroom_store, "_embeddings", return_value=object()):
            out = classroom_store.retrieve_classroom_docs(1, "随便问问")

        self.assertEqual(len(out), 1)

    def test_document_metrics_counts_only_this_doc(self):
        """★ 计量必须按 doc_id：整库计数会把上一份文件的片算进这一份。

        实测后果：3 页空白 PDF 上传后报 188 片（整库总数），教师以为成功。
        """
        store = Mock()
        store._collection.get.return_value = {
            "ids": ["1", "2", "3"],
            "metadatas": [
                {"section": "front_matter", "doc_id": "d1"},
                {"section": "body", "doc_id": "d1"},
                {"section": "body", "doc_id": "d1"},
            ],
        }
        total, body = classroom_store.document_metrics(store, "d1")
        self.assertEqual((total, body), (3, 2))
        store._collection.get.assert_called_once()
        self.assertEqual(
            store._collection.get.call_args.kwargs["where"], {"doc_id": "d1"}
        )

    def test_document_metrics_flags_scanned_pdf_without_body(self):
        """只有 front_matter 一片 = 抽不出正文（扫描件），body 必须是 0。"""
        store = Mock()
        store._collection.get.return_value = {
            "ids": ["1"],
            "metadatas": [{"section": "front_matter", "doc_id": "d1"}],
        }
        self.assertEqual(classroom_store.document_metrics(store, "d1"), (1, 0))

    def test_purge_removes_dir_and_collection(self):
        """清理要连目录一起删，并清掉句柄缓存（悬空句柄会抛 NotFoundError）。"""
        tmp = Path(tempfile.mkdtemp(prefix="classroom_purge_"))
        docs_dir = tmp / "docs"
        chroma_dir = tmp / "chroma"
        (docs_dir / "7").mkdir(parents=True)
        (docs_dir / "7" / "x.pdf").write_bytes(b"%PDF-1.4\n")
        chroma_dir.mkdir(parents=True)
        try:
            import chromadb

            client = chromadb.PersistentClient(path=str(chroma_dir))
            client.get_or_create_collection("classroom_7_docs")
            classroom_store._STORE_CACHE[7] = object()
            with patch.object(classroom_store, "DOCS_DIR", docs_dir), patch.object(
                classroom_store, "CHROMA_DIR", chroma_dir
            ):
                removed = classroom_store.purge_classroom(7)

            self.assertTrue(removed["collection"])
            self.assertTrue(removed["files_dir"])
            self.assertFalse((docs_dir / "7").exists())
            self.assertNotIn(7, classroom_store._STORE_CACHE)
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- 上传入库


class ClassroomUploadTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        app_module.sessions.clear()

    async def _call(self, persist="classroom", filename="notes.pdf", content=b"%PDF-1.4\nx"):
        return await _unwrapped(app_module.upload_teacher_document)(
            request=Mock(),
            file=_pdf(filename, content),
            teacher_id=7,
            classroom_id=3,
            persist=persist,
        )

    def _patches(self, **overrides):
        """把上传依赖全换成假实现，返回 ExitStack 里用的 patch 列表。"""
        values = {
            "find_classroom_document_by_hash": Mock(return_value=None),
            "classroom_document_usage": Mock(return_value=(0, 0)),
            "add_classroom_document": Mock(return_value=object()),
            "save_original": Mock(return_value=Path("/tmp/fake_stored.pdf")),
            "index_classroom_document": Mock(return_value=_FakeStore(chunks=9)),
            "document_metrics": Mock(return_value=(9, 8)),
        }
        values.update(overrides)
        return values

    async def test_classroom_persist_writes_original_index_and_row(self):
        vals = self._patches()
        with patch.object(app_module, "_require_db"), patch.object(
            app_module, "get_db", _fake_db
        ), patch.object(app_module, "_require_teacher"), patch.object(
            app_module, "find_classroom_document_by_hash", vals["find_classroom_document_by_hash"]
        ), patch.object(
            app_module, "classroom_document_usage", vals["classroom_document_usage"]
        ), patch.object(
            app_module, "add_classroom_document", vals["add_classroom_document"]
        ), patch.object(
            classroom_store, "save_original", vals["save_original"]
        ), patch.object(
            classroom_store, "index_classroom_document", vals["index_classroom_document"]
        ), patch.object(
            classroom_store, "document_metrics", vals["document_metrics"]
        ):
            out = await self._call()

        self.assertEqual(out["persist"], "classroom")
        self.assertEqual(out["chunk_count"], 9)
        self.assertEqual(out["classroom_id"], 3)
        vals["save_original"].assert_called_once()
        vals["index_classroom_document"].assert_called_once()
        vals["add_classroom_document"].assert_called_once()
        # 课堂资料不进会话内存库（那是备课素材的路径）
        self.assertEqual(app_module.sessions, {})

    async def test_scanned_pdf_without_text_is_rejected_and_rolled_back(self):
        """★ 抽不出正文的 PDF 必须当场拒绝，不能静默入库。

        实测（2026-10-26）：3 页空白 PDF 返回 200 并报「188 片」，实际正文切片 0
        （188 是整库总数）。教师以为传成功，学生问什么都命不中——静默失败。
        """
        vals = self._patches(document_metrics=Mock(return_value=(1, 0)))
        rollback = {
            "chunks": Mock(return_value=1),
            "remove": Mock(return_value=True),
            "purge": Mock(),
        }
        with patch.object(app_module, "_require_db"), patch.object(
            app_module, "get_db", _fake_db
        ), patch.object(app_module, "_require_teacher"), patch.object(
            app_module, "find_classroom_document_by_hash", vals["find_classroom_document_by_hash"]
        ), patch.object(
            app_module, "classroom_document_usage", vals["classroom_document_usage"]
        ), patch.object(
            classroom_store, "save_original", vals["save_original"]
        ), patch.object(
            classroom_store, "index_classroom_document", vals["index_classroom_document"]
        ), patch.object(
            classroom_store, "document_metrics", vals["document_metrics"]
        ), patch.object(
            classroom_store, "delete_document_chunks", rollback["chunks"]
        ), patch.object(
            classroom_store, "remove_original", rollback["remove"]
        ), patch.object(
            classroom_store, "purge_classroom", rollback["purge"]
        ):
            with self.assertRaises(HTTPException) as ctx:
                await self._call()

        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn("no extractable text", ctx.exception.detail)
        # 三步回滚都必须发生，否则留孤儿切片/孤儿文件
        rollback["chunks"].assert_called_once()
        rollback["remove"].assert_called_once()
        rollback["purge"].assert_called_once()
        # 也绝不能留下登记行
        vals["add_classroom_document"].assert_not_called()

    async def test_duplicate_content_returns_409_without_side_effects(self):
        existing = Mock(id="old1", filename="旧讲义.pdf")
        vals = self._patches(
            find_classroom_document_by_hash=Mock(return_value=existing)
        )
        with patch.object(app_module, "_require_db"), patch.object(
            app_module, "get_db", _fake_db
        ), patch.object(app_module, "_require_teacher"), patch.object(
            app_module, "find_classroom_document_by_hash", vals["find_classroom_document_by_hash"]
        ), patch.object(
            classroom_store, "save_original", vals["save_original"]
        ), patch.object(
            classroom_store, "index_classroom_document", vals["index_classroom_document"]
        ):
            with self.assertRaises(HTTPException) as ctx:
                await self._call()

        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.detail["doc_id"], "old1")
        vals["save_original"].assert_not_called()
        vals["index_classroom_document"].assert_not_called()

    async def test_quota_exceeded_returns_413_before_copy(self):
        vals = self._patches(
            classroom_document_usage=Mock(return_value=(99, 0))
        )
        with patch.object(app_module, "_require_db"), patch.object(
            app_module, "get_db", _fake_db
        ), patch.object(app_module, "_require_teacher"), patch.object(
            app_module, "find_classroom_document_by_hash", vals["find_classroom_document_by_hash"]
        ), patch.object(
            app_module, "classroom_document_usage", vals["classroom_document_usage"]
        ), patch.object(
            classroom_store, "save_original", vals["save_original"]
        ):
            with self.assertRaises(HTTPException) as ctx:
                await self._call()

        self.assertEqual(ctx.exception.status_code, 413)
        vals["save_original"].assert_not_called()

    async def test_invalid_persist_value_is_rejected_early(self):
        with patch.object(app_module, "_require_db"), patch.object(
            app_module, "get_db", _fake_db
        ), patch.object(app_module, "_require_teacher") as require:
            with self.assertRaises(HTTPException) as ctx:
                await self._call(persist="forever")

        self.assertEqual(ctx.exception.status_code, 400)
        require.assert_not_called()

    async def test_default_persist_is_still_session_and_passes_no_persist_dir(self):
        """存量调用方不传 persist 时，必须逐字保持方案 A 的行为。"""
        calls = []

        def fake_build(path, name, **kwargs):
            calls.append((name, kwargs))
            return _FakeStore()

        with patch.object(app_module, "_require_db"), patch.object(
            app_module, "get_db", _fake_db
        ), patch.object(app_module, "_require_teacher"), patch.object(
            app_module, "build_index", fake_build
        ), patch.object(
            app_module, "add_classroom_document"
        ) as add_row:
            out = await self._call(persist="session")

        self.assertEqual(out["persist"], "session")
        self.assertEqual(calls[0][1], {})  # 没有 persist_directory
        add_row.assert_not_called()
        self.assertTrue(
            app_module.sessions[out["session_id"]].audience == "teacher"
        )

    async def test_index_failure_removes_original_and_returns_422(self):
        vals = self._patches(
            index_classroom_document=Mock(side_effect=RuntimeError("bad pdf"))
        )
        removed = []
        with patch.object(app_module, "_require_db"), patch.object(
            app_module, "get_db", _fake_db
        ), patch.object(app_module, "_require_teacher"), patch.object(
            app_module, "find_classroom_document_by_hash", vals["find_classroom_document_by_hash"]
        ), patch.object(
            app_module, "classroom_document_usage", vals["classroom_document_usage"]
        ), patch.object(
            classroom_store, "save_original", vals["save_original"]
        ), patch.object(
            classroom_store, "index_classroom_document", vals["index_classroom_document"]
        ), patch.object(
            classroom_store,
            "remove_original",
            side_effect=lambda p: removed.append(str(p)) or True,
        ):
            with self.assertRaises(HTTPException) as ctx:
                await self._call()

        self.assertEqual(ctx.exception.status_code, 422)
        self.assertEqual(len(removed), 1)

    async def test_db_registration_failure_cleans_chunks_and_original(self):
        vals = self._patches(
            add_classroom_document=Mock(side_effect=RuntimeError("db down"))
        )
        cleaned = {"chunks": 0, "files": 0}
        with patch.object(app_module, "_require_db"), patch.object(
            app_module, "get_db", _fake_db
        ), patch.object(app_module, "_require_teacher"), patch.object(
            app_module, "find_classroom_document_by_hash", vals["find_classroom_document_by_hash"]
        ), patch.object(
            app_module, "classroom_document_usage", vals["classroom_document_usage"]
        ), patch.object(
            app_module, "add_classroom_document", vals["add_classroom_document"]
        ), patch.object(
            classroom_store, "save_original", vals["save_original"]
        ), patch.object(
            classroom_store, "index_classroom_document", vals["index_classroom_document"]
        ), patch.object(
            classroom_store, "document_metrics", vals["document_metrics"]
        ), patch.object(
            classroom_store,
            "delete_document_chunks",
            side_effect=lambda c, d: cleaned.__setitem__("chunks", cleaned["chunks"] + 1) or 3,
        ), patch.object(
            classroom_store,
            "remove_original",
            side_effect=lambda p: cleaned.__setitem__("files", cleaned["files"] + 1) or True,
        ):
            with self.assertRaises(HTTPException) as ctx:
                await self._call()

        self.assertEqual(ctx.exception.status_code, 500)
        self.assertEqual(cleaned, {"chunks": 1, "files": 1})


# ---------------------------------------------------------------- 删除接口


class ClassroomDeleteEndpointTests(unittest.TestCase):
    def test_non_teacher_cannot_delete(self):
        with patch.object(app_module, "_require_db"), patch.object(
            app_module, "get_db", _fake_db
        ), patch.object(
            app_module,
            "_require_teacher",
            side_effect=HTTPException(403, "not a teacher"),
        ), patch.object(
            classroom_store, "delete_document_chunks"
        ) as delete_chunks:
            with self.assertRaises(HTTPException) as ctx:
                app_module.api_delete_classroom_document(1, "d1", teacher_id=9)

        self.assertEqual(ctx.exception.status_code, 403)
        delete_chunks.assert_not_called()

    def test_unknown_document_returns_404(self):
        with patch.object(app_module, "_require_db"), patch.object(
            app_module, "get_db", _fake_db
        ), patch.object(app_module, "_require_teacher"), patch.object(
            app_module, "get_classroom_document", return_value=None
        ), patch.object(
            classroom_store, "delete_document_chunks"
        ) as delete_chunks:
            with self.assertRaises(HTTPException) as ctx:
                app_module.api_delete_classroom_document(1, "nope", teacher_id=9)

        self.assertEqual(ctx.exception.status_code, 404)
        delete_chunks.assert_not_called()

    def test_delete_removes_chunks_row_and_original(self):
        doc = Mock(stored_path="/tmp/abc.pdf")
        calls = []
        with patch.object(app_module, "_require_db"), patch.object(
            app_module, "get_db", _fake_db
        ), patch.object(app_module, "_require_teacher"), patch.object(
            app_module, "get_classroom_document", return_value=doc
        ), patch.object(
            classroom_store,
            "delete_document_chunks",
            side_effect=lambda c, d: calls.append("chunks") or 5,
        ), patch.object(
            app_module,
            "mark_classroom_document_deleted",
            side_effect=lambda db, d: calls.append("row") or True,
        ), patch.object(
            classroom_store,
            "remove_original",
            side_effect=lambda p: calls.append("file") or True,
        ), patch.object(
            app_module, "classroom_document_usage", return_value=(1, 100)
        ), patch.object(
            classroom_store, "purge_classroom"
        ) as purge:
            resp = app_module.api_delete_classroom_document(1, "d1", teacher_id=9)

        self.assertEqual(resp.status_code, 204)
        self.assertEqual(calls, ["chunks", "row", "file"])
        purge.assert_not_called()  # 还有别的资料，不能连库一起清

    def test_deleting_last_document_purges_empty_collection(self):
        """删到一份不剩要清空库：否则每届留一个 0 切片 collection。"""
        doc = Mock(stored_path="/tmp/abc.pdf")
        with patch.object(app_module, "_require_db"), patch.object(
            app_module, "get_db", _fake_db
        ), patch.object(app_module, "_require_teacher"), patch.object(
            app_module, "get_classroom_document", return_value=doc
        ), patch.object(
            classroom_store, "delete_document_chunks", return_value=5
        ), patch.object(
            app_module, "mark_classroom_document_deleted", return_value=True
        ), patch.object(
            classroom_store, "remove_original", return_value=True
        ), patch.object(
            app_module, "classroom_document_usage", return_value=(0, 0)
        ), patch.object(
            classroom_store, "purge_classroom", return_value={}
        ) as purge:
            app_module.api_delete_classroom_document(1, "d1", teacher_id=9)

        purge.assert_called_once_with(1)


# ---------------------------------------------------------------- 注入与来源口径


class ComposeContextTests(unittest.TestCase):
    def test_order_is_upload_then_kb_then_classroom(self):
        pdf = [Document(page_content="PDF-BODY", metadata={"page_number": 1})]
        kb = [
            Document(
                page_content="KB-BODY",
                metadata={"topic": "死锁", "category": "os", "source": "os/deadlock.md"},
            )
        ]
        room = [
            Document(
                page_content="ROOM-BODY",
                metadata={
                    "doc_id": "d1",
                    "filename": "讲义.pdf",
                    "page_number": 2,
                    "line_start": 3,
                },
            )
        ]
        context, sources = app_module._compose_rag_context(pdf, kb, room)

        self.assertLess(context.index("PDF-BODY"), context.index("KB-BODY"))
        self.assertLess(context.index("KB-BODY"), context.index("ROOM-BODY"))
        self.assertEqual(
            [s["origin"] for s in sources], ["upload", "kb", "classroom"]
        )

    def test_empty_classroom_keeps_two_path_output(self):
        """没有课堂资料时，输出必须与改造前逐字一致（零回归）。"""
        pdf = [Document(page_content="P", metadata={"page_number": 1})]
        kb = [
            Document(
                page_content="K",
                metadata={"topic": "t", "category": "c", "source": "s.md"},
            )
        ]
        without, sources_without = app_module._compose_rag_context(pdf, kb, [])
        legacy = "\n\n---\n\n".join(
            x
            for x in (
                app_module.format_pdf_docs(pdf),
                app_module.format_knowledge_docs(kb),
            )
            if x
        )
        self.assertEqual(without, legacy)
        self.assertEqual([s["origin"] for s in sources_without], ["upload", "kb"])


class SummarizeClassroomSourcesTests(unittest.TestCase):
    def test_classroom_sources_carry_doc_identity(self):
        docs = [
            Document(
                page_content="x",
                metadata={
                    "doc_id": "d9",
                    "filename": "讲义.pdf",
                    "source": "classroom:3/d9_讲义.pdf",
                    "page_number": 4,
                    "section": "body",
                },
            )
        ]
        items = summarize_sources(docs, "classroom")
        self.assertEqual(items[0]["origin"], "classroom")
        self.assertEqual(items[0]["doc_id"], "d9")
        self.assertEqual(items[0]["filename"], "讲义.pdf")
        self.assertEqual(items[0]["page"], 4)


# ---------------------------------------------------------------- 迁移守卫


class ClassroomModelRegistrationTests(unittest.TestCase):
    def test_table_is_registered_for_create_all(self):
        """新表忘了在模型里注册 → create_all 静默不建表，端点报 Unknown column。"""
        self.assertIn("classroom_documents", Base.metadata.tables)


if __name__ == "__main__":
    unittest.main()
