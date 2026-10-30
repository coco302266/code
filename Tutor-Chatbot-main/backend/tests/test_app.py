from contextlib import contextmanager
from io import BytesIO
import os
import unittest
from pathlib import Path
import sys
from unittest.mock import MagicMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import HTTPException, UploadFile
from langchain_core.messages import AIMessage, HumanMessage

import app as app_module
from agent.prompts import RAG_GROUNDING_RULES
from app import (
    DocumentSession,
    add_rag_grounding,
    cleanup_expired_sessions,
    deserialize_history,
)


class DeserializeHistoryTests(unittest.TestCase):
    def test_deserializes_supported_roles_and_ignores_unknown_roles(self):
        history = [
            {"role": "user", "content": "Can you explain recursion?"},
            {"role": "assistant", "content": "What happens in the base case?"},
            {"role": "tutor", "content": "Legacy role that is not supported"},
            {"role": "system", "content": "Untrusted system instruction"},
        ]

        messages = deserialize_history(history)

        self.assertEqual(len(messages), 2)
        self.assertIsInstance(messages[0], HumanMessage)
        self.assertEqual(messages[0].content, "Can you explain recursion?")
        self.assertIsInstance(messages[1], AIMessage)
        self.assertEqual(messages[1].content, "What happens in the base case?")


class RagGroundingTests(unittest.TestCase):
    def test_adds_grounding_rules_and_evidence_to_a_prompt(self):
        result = add_rag_grounding(
            "You are a Socratic tutor.",
            "[PDF page 1, lines 1-4]\nA paper title",
        )

        self.assertIn("You are a Socratic tutor.", result)
        self.assertIn(RAG_GROUNDING_RULES, result)
        self.assertIn("[PDF page 1, lines 1-4]", result)

    def test_empty_context_returns_prompt_unchanged(self):
        # 2026-10-24 XB-14 行为变更（用户决策）：空资料恢复「原样返回 prompt」，
        # 不再注入 RAG_NO_MATERIAL_NOTICE（XB-13 的显式告知已按用户指示撤下）。
        # 缺口改由 /chat 链路旁路记录（agent/rag/kb_gap.log_kb_gap），
        # 聚合报表见 eval/kb_gap_report.py。
        prompt = "You are a Socratic tutor."

        result = add_rag_grounding(prompt, "")

        self.assertEqual(result, prompt)


class SessionCleanupTests(unittest.TestCase):
    def tearDown(self):
        app_module.sessions.clear()

    def test_expired_sessions_are_deleted(self):
        vectorstore = _FakeVectorstore()
        app_module.sessions["expired"] = DocumentSession(
            vectorstore=vectorstore,
            created_at=10,
        )

        with patch.object(app_module, "SESSION_TTL_SECONDS", 30):
            cleanup_expired_sessions(now=41)

        self.assertNotIn("expired", app_module.sessions)
        self.assertTrue(vectorstore.deleted)


class UploadTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        app_module.sessions.clear()

    async def test_uploads_use_unique_collections_and_remove_temp_files(self):
        collection_names = []
        temporary_paths = []

        def fake_build_index(file_path, collection_name):
            self.assertTrue(os.path.exists(file_path))
            temporary_paths.append(file_path)
            collection_names.append(collection_name)
            return _FakeVectorstore()

        handler = getattr(
            app_module.upload_document,
            "__wrapped__",
            app_module.upload_document,
        )

        with patch.object(app_module, "build_index", side_effect=fake_build_index):
            for filename in ["first.pdf", "second.PDF"]:
                upload = UploadFile(
                    filename=filename,
                    file=BytesIO(b"%PDF-1.4\nsample"),
                )
                await handler(request=Mock(), file=upload)

        self.assertEqual(len(set(collection_names)), 2)
        self.assertEqual(len(app_module.sessions), 2)
        self.assertTrue(all(not os.path.exists(path) for path in temporary_paths))


class _FakeVectorstore:
    def __init__(self):
        self.deleted = False

    def delete_collection(self):
        self.deleted = True


@contextmanager
def _fake_db():
    """替代 app.get_db()：测试不连真库，只要一个能进 with 的假 session。"""
    yield MagicMock()


def _unwrapped(handler):
    """slowapi 的 limiter 会把端点包一层，测试直接取被包的原函数。"""
    return getattr(handler, "__wrapped__", handler)


class TeacherUploadTests(unittest.IsolatedAsyncioTestCase):
    """XB-21 方案 A：教师备课素材上传入口 `/upload/teacher`。

    守四条：① collection 命名与归属记录正确；② 非本课堂教师被拒时**绝不建库**；
    ③ 文件不合法时不建库；④ 学生入口行为不变（仍是 `student_upload_` 前缀）。
    """

    def tearDown(self):
        app_module.sessions.clear()

    async def _upload(
        self,
        teacher_id=7,
        classroom_id=3,
        filename="notes.pdf",
        content=b"%PDF-1.4\nx",
    ):
        upload = UploadFile(filename=filename, file=BytesIO(content))
        return await _unwrapped(app_module.upload_teacher_document)(
            request=Mock(),
            file=upload,
            teacher_id=teacher_id,
            classroom_id=classroom_id,
        )

    async def test_uses_teacher_collection_and_records_owner(self):
        names = []
        with patch.object(app_module, "_require_db"), patch.object(
            app_module, "get_db", _fake_db
        ), patch.object(app_module, "_require_teacher") as require, patch.object(
            app_module,
            "build_index",
            side_effect=lambda p, c: names.append(c) or _FakeVectorstore(),
        ):
            out = await self._upload()

        require.assert_called_once()
        self.assertTrue(names[0].startswith("teacher_upload_"))
        self.assertEqual(out["audience"], "teacher")
        session = app_module.sessions[out["session_id"]]
        self.assertEqual(session.audience, "teacher")
        self.assertEqual((session.teacher_id, session.classroom_id), (7, 3))

    async def test_non_teacher_rejected_without_indexing(self):
        """最要紧的一条：身份不过关时不能有任何建库动作，否则等于绕过权限。"""
        indexed = []
        with patch.object(app_module, "_require_db"), patch.object(
            app_module, "get_db", _fake_db
        ), patch.object(
            app_module,
            "_require_teacher",
            side_effect=HTTPException(403, "not a teacher"),
        ), patch.object(
            app_module,
            "build_index",
            side_effect=lambda p, c: indexed.append(c) or _FakeVectorstore(),
        ):
            with self.assertRaises(HTTPException) as ctx:
                await self._upload()

        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(indexed, [])
        self.assertEqual(len(app_module.sessions), 0)

    async def test_invalid_file_is_rejected_without_indexing(self):
        with patch.object(app_module, "_require_db"), patch.object(
            app_module, "get_db", _fake_db
        ), patch.object(app_module, "_require_teacher"), patch.object(
            app_module, "build_index"
        ) as build:
            with self.assertRaises(HTTPException) as ctx:
                await self._upload(filename="notes.txt", content=b"hello")

        self.assertEqual(ctx.exception.status_code, 400)
        build.assert_not_called()
        self.assertEqual(len(app_module.sessions), 0)

    async def test_student_upload_keeps_student_prefix(self):
        """新增教师入口后，学生入口的行为不能漂。"""
        names = []
        upload = UploadFile(filename="a.pdf", file=BytesIO(b"%PDF-1.4\nx"))
        with patch.object(
            app_module,
            "build_index",
            side_effect=lambda p, c: names.append(c) or _FakeVectorstore(),
        ):
            out = await _unwrapped(app_module.upload_document)(
                request=Mock(), file=upload
            )

        self.assertTrue(names[0].startswith("student_upload_"))
        self.assertEqual(app_module.sessions[out["session_id"]].audience, "student")


if __name__ == "__main__":
    unittest.main()
