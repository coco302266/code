"""`/chat` 的 unknown-topic 降级分支回归用例。

背景（2026-10-30 容器实测）：这条分支里曾用 `if doc_context:` 判断「学生
有没有上传过文档」，但 `doc_context` 只是 `_compose_rag_context()` 内部的
局部变量，`chat()` 作用域里根本不存在它。于是只要评估节点抽不出 topic
（返回 unknown），必定抛 NameError → 500。

受影响的正是「纯寒暄 / 与八股无关」的提问，比如"帮我写一首关于秋天的诗"：
常规冒烟问的都是八股题，走不到这条分支，所以缺陷一直没被发现，直到容器
里用真实请求打出来。

这里锁两件事：
  1. unknown + 无上传件 → 走寒暄降级，200，且发空 sources 数组；
  2. unknown + 有上传件 → 走文档降级，200，sources 里带 upload origin。

第 2 条是那个缺陷的反面约束：它要求判断依据必须是 `chat()` 里真实存在的
变量（`pdf_docs`），而不是某个只在别处可见的局部变量。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.documents import Document

import app as app_module


class _Chunk:
    """假 LLM 吐的 token 块，只要有 .content 就够。"""

    def __init__(self, text: str):
        self.content = text


class _FakeLlm:
    """假 LLM：`astream` 逐个吐 token，不碰网络。"""

    def __init__(self, tokens):
        self._tokens = list(tokens)

    async def astream(self, messages):
        for token in self._tokens:
            yield _Chunk(token)


def _make_doc() -> Document:
    """一条学生上传件切片（字段齐到 format_pdf_docs / summarize_sources 够用）。"""
    return Document(
        page_content="这是我的课堂笔记第 3 页。",
        metadata={
            "source": "note.pdf",
            "page_number": 3,
            "line_start": 1,
            "line_end": 12,
            "title": "note.pdf",
        },
    )


class UnknownTopicBranchTests(unittest.TestCase):
    """unknown-topic 分支的两条路径都要 200。

    隔离策略：`/chat` 拉起真实的 Chroma / MySQL / LLM 都不现实，这里只保留
    被测的那条分支本身，把评估、检索、落库、LLM 全部换成桩。
    """

    def _patch(self, name, **kwargs):
        patcher = mock.patch.object(app_module, name, **kwargs)
        patcher.start()
        self.addCleanup(patcher.stop)

    def setUp(self):
        # 限流 10/minute 与分支逻辑无关，测试里关掉免得互相干扰
        self._limiter = getattr(app_module.limiter, "enabled", True)
        app_module.limiter.enabled = False
        self.addCleanup(setattr, app_module.limiter, "enabled", self._limiter)

        # 走串行路径：并行路径会额外引投机执行，与本用例无关
        self._patch("CHAT_PARALLEL_ASSESS", new=False)
        # 三路检索全部返回空（上传件那条在用例 2 里单独换成有内容）
        self._patch("get_knowledge_store", new=lambda: None)
        self._patch("retrieve_pdf_docs", new=lambda *a, **k: [])
        self._patch("log_kb_gap", new=lambda *a, **k: True)
        self._patch("_persist_assistant", new=lambda *a, **k: None)
        self._patch("cleanup_expired_sessions", new=lambda: 0)
        self._patch("get_llm", new=lambda provider: _FakeLlm(["你好", "！"]))

        # 评估节点判不出知识点
        self._patch(
            "assessment_graph",
            new=mock.MagicMock(
                ainvoke=mock.AsyncMock(return_value={"topic": "unknown"})
            ),
        )

    def _post(self, message: str, session_id: str = ""):
        from fastapi.testclient import TestClient

        payload = {"message": message, "provider": "openai"}
        if session_id:
            payload["session_id"] = session_id
        with TestClient(app_module.app, client=("127.0.0.1", 12345)) as client:
            return client.post("/chat", json=payload)

    def _sse_types(self, resp) -> list[str]:
        types = []
        for line in resp.text.splitlines():
            if line.startswith("data: ") and line != "data: [DONE]":
                try:
                    import json

                    types.append(json.loads(line[6:]).get("type", "?"))
                except Exception:
                    continue
        return types

    def _sources_items(self, resp) -> list[dict]:
        import json

        for line in resp.text.splitlines():
            if line.startswith("data: "):
                try:
                    event = json.loads(line[6:])
                except Exception:
                    continue
                if event.get("type") == "sources":
                    return event.get("items", [])
        return []

    # ---- 用例 1：寒暄降级 ----

    def test_unknown_without_upload_returns_200(self):
        resp = self._post("帮我写一首关于秋天的诗")
        # 缺陷存在时这里拿到 500：chat() 里访问了不存在的 doc_context
        self.assertEqual(resp.status_code, 200, resp.text[:400])

    def test_unknown_without_upload_emits_empty_sources(self):
        """寒暄场景压根没走资料，要发空数组而不是不发。"""
        resp = self._post("帮我写一首关于秋天的诗")
        items = self._sources_items(resp)
        self.assertEqual(items, [])
        self.assertIn("sources", self._sse_types(resp))

    # ---- 用例 2：文档降级（正是缺陷的反面）----

    def test_unknown_with_upload_returns_200(self):
        # 造一个已上传过文档的会话：session 有 vectorstore 才会走 PDF 检索
        app_module.sessions["sess-1"] = SimpleNamespace(vectorstore=object())
        self.addCleanup(app_module.sessions.pop, "sess-1", None)

        self._patch("retrieve_pdf_docs", new=lambda *a, **k: [_make_doc()])

        resp = self._post("帮我写一首关于秋天的诗", session_id="sess-1")
        self.assertEqual(resp.status_code, 200, resp.text[:400])

    def test_unknown_with_upload_carries_upload_sources(self):
        app_module.sessions["sess-1"] = SimpleNamespace(vectorstore=object())
        self.addCleanup(app_module.sessions.pop, "sess-1", None)

        self._patch("retrieve_pdf_docs", new=lambda *a, **k: [_make_doc()])

        resp = self._post("帮我写一首关于秋天的诗", session_id="sess-1")
        items = self._sources_items(resp)
        self.assertTrue(items, "有上传件时应该上报来源")
        self.assertEqual(items[0].get("origin"), "upload")


if __name__ == "__main__":
    unittest.main()
