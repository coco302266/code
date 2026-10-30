"""越界拒答（off_topic）的守护用例。

锁三件事：

1. 节点层：`extract_topic_node` 判出越界后，信号**必须**传出去，
   不能被「当前已有知识点就沿用」的容错吞掉；
2. 接口层：`/chat` 收到越界信号后返回固定文案，且**不调用 LLM**；
3. 一致性：所有越界场景措辞完全一致，不因问题类型不同而改变。

第 1 条是这次要补的那个洞（2026-10-31）：学生正讲着死锁、突然问
「给我写一首关于秋天的诗」，模型其实正确判出了越界，却因为
`current_topic="死锁"` 被 `return {}` 当成沿用旧话题，于是走正常链路
用模型自有知识把诗写了出来——前端截图里那首「秋日／风过疏林叶渐黄」，
回复下方还老实标注着「依据：模型自有知识（本轮未使用知识库资料）」。
"""

from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.messages import AIMessage, HumanMessage

import app as app_module
from agent.graph import extract_topic_node
from agent.prompts import OFF_TOPIC_REPLY


class _Resp:
    def __init__(self, content: str):
        self.content = content


class _Chunk:
    def __init__(self, text: str):
        self.content = text


class _FakeLlm:
    """节点层用 `response`，接口层用 `tokens`，各自记录调用次数。"""

    model_name = "fake-model"

    def __init__(self, response: str = "same", tokens=None):
        self.response = response
        self._tokens = list(tokens or [])
        self.calls = 0

    def bind(self, **kwargs):
        return self

    async def ainvoke(self, _messages):
        self.calls += 1
        return _Resp(self.response)

    async def astream(self, _messages):
        self.calls += 1
        for token in self._tokens:
            yield _Chunk(token)


# ---------------------------------------------------------------------------
# 节点层：越界信号能不能传出来
# ---------------------------------------------------------------------------


class ExtractTopicNodeTests(unittest.TestCase):
    def _run(
        self,
        response: str,
        topic: str = "死锁",
        latest: str = "给我写一首关于秋天的诗",
    ) -> dict:
        """跑一次抽取节点，返回它会往 state 上打的 patch。"""
        state = {
            "messages": [
                HumanMessage(content="什么是死锁？"),
                AIMessage(content="死锁是多个进程互相等待对方持有的资源。"),
                HumanMessage(content=latest),
            ],
            "topic": topic,
            "hint_level": 1,
            "misconception": "",
            "resolved": False,
            "llm": _FakeLlm(response),
            "topic_changed": False,
        }
        return asyncio.run(extract_topic_node(state))

    def test_off_topic_passes_through_with_existing_topic(self):
        """核心回归：已有 topic 时越界信号不能被容错吞掉。

        修复前这里返回 {}（沿用旧 topic），于是「讲着死锁突然要诗」会被当成
        死锁话题的延续，走正常链路用自有知识把诗写出来。
        """
        self.assertEqual(self._run("off_topic"), {"off_topic": True})

    def test_off_topic_passes_through_without_topic(self):
        self.assertEqual(self._run("off_topic", topic="unknown"), {"off_topic": True})

    def test_off_topic_keeps_teaching_state(self):
        """拒答不该清掉教学状态：下一轮回到正题还要接着讲。"""
        patch = self._run("off_topic")
        self.assertNotIn("topic", patch)
        self.assertNotIn("hint_level", patch)
        self.assertNotIn("misconception", patch)

    def test_trailing_punctuation_is_stripped(self):
        self.assertEqual(self._run("off_topic。"), {"off_topic": True})

    def test_existing_topic_still_absorbs_unknown(self):
        """容错本身要保留：追问被误判 unknown 时仍沿用旧 topic，不能误伤。"""
        self.assertEqual(self._run("unknown"), {})

    def test_unknown_without_topic_is_unknown(self):
        self.assertEqual(self._run("unknown", topic="unknown")["topic"], "unknown")

    def test_same_still_returns_empty(self):
        self.assertEqual(self._run("same"), {})


# ---------------------------------------------------------------------------
# 接口层：拒答流
# ---------------------------------------------------------------------------


class ChatOffTopicTests(unittest.TestCase):
    def _patch(self, name, **kwargs):
        patcher = mock.patch.object(app_module, name, **kwargs)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _post(self, message: str, topic: str = "死锁", off_topic: bool = True):
        from fastapi.testclient import TestClient

        # 关键：topic 是**旧的知识点**（拒答不清状态），只有 off_topic 反映本轮越界
        assessment = {"topic": topic, "hint_level": 1, "misconception": "", "resolved": False}
        if off_topic:
            assessment["off_topic"] = True

        self._patch(
            "assessment_graph",
            new=mock.MagicMock(ainvoke=mock.AsyncMock(return_value=assessment)),
        )
        with TestClient(app_module.app, client=("127.0.0.1", 12345)) as client:
            return client.post(
                "/chat", json={"message": message, "provider": "openai"}
            )

    def setUp(self):
        self._limiter = getattr(app_module.limiter, "enabled", True)
        app_module.limiter.enabled = False
        self.addCleanup(setattr, app_module.limiter, "enabled", self._limiter)

        self._patch("CHAT_PARALLEL_ASSESS", new=False)
        self._patch("get_knowledge_store", new=lambda: None)
        self._patch("retrieve_pdf_docs", new=lambda *a, **k: [])
        self._patch("log_kb_gap", new=lambda *a, **k: True)
        self._patch("_persist_assistant", new=lambda *a, **k: None)
        self._patch("cleanup_expired_sessions", new=lambda: 0)
        # 一旦拒答流去调模型，tokens 里的内容就会出现在响应里，用例立刻红
        self.llm = _FakeLlm(tokens=["绝不该出现的内容"])
        self._patch("get_llm", new=lambda provider: self.llm)

    def _reply_text(self, resp) -> str:
        parts = []
        for line in resp.text.splitlines():
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            event = json.loads(line[6:])
            if event.get("type") == "token":
                parts.append(event["content"])
        return "".join(parts)

    # ---- 越界拒答 ----

    def test_off_topic_returns_200(self):
        resp = self._post("给我写一首关于秋天的诗")
        self.assertEqual(resp.status_code, 200, resp.text[:400])

    def test_off_topic_replies_with_fixed_text(self):
        resp = self._post("给我写一首关于秋天的诗")
        self.assertEqual(self._reply_text(resp), OFF_TOPIC_REPLY)

    def test_off_topic_does_not_call_llm(self):
        """越界不该去问模型：既省一次往返，也杜绝模型自作主张把诗写出来。"""
        self._post("给我写一首关于秋天的诗")
        self.assertEqual(self.llm.calls, 0)

    def test_off_topic_emits_empty_sources(self):
        resp = self._post("给我写一首关于秋天的诗")
        for line in resp.text.splitlines():
            if line.startswith("data: "):
                event = json.loads(line[6:])
                if event.get("type") == "sources":
                    self.assertEqual(event.get("items"), [])
                    return
        self.fail("没有发出 sources 事件")

    def test_reply_is_identical_across_off_topic_types(self):
        """措辞一致：不因问题类型不同而改变。"""
        texts = [
            self._reply_text(self._post(msg))
            for msg in (
                "给我写一首关于秋天的诗",
                "《静夜思》的作者是谁",
                "最近那个明星离婚是怎么回事",
                "感冒了该吃什么药",
            )
        ]
        self.assertEqual(len(set(texts)), 1, "不同越界类型返回了不同措辞")
        self.assertEqual(texts[0], OFF_TOPIC_REPLY)

    def test_normal_question_is_not_blocked(self):
        """八股题不受影响：没有 off_topic 信号就该走正常链路。"""
        resp = self._post("什么是死锁？", off_topic=False)
        self.assertEqual(resp.status_code, 200)
        self.assertNotEqual(self._reply_text(resp), OFF_TOPIC_REPLY)


if __name__ == "__main__":
    unittest.main()
