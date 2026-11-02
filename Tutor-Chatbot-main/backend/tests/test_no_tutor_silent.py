"""关掉主讲助教后 `/chat` 必须没人应答（2026-11-02 回归）。

用户实测现象（小组讨论课堂，use_tutor=0）：学生发一句「花生在吗」，
回话的却是**出题助手**。根因在 `agent/orchestrator.plan_agents`——
`not use_tutor` 曾被当作「整轮交给出题助手」的兜底，于是任何一句普通提问
都会变成一次出题。

修法分两层，这里各锁一条：
  1. 编排层：`use_tutor=False` 且没有 @出题助手 → 空计划（见 test_orchestration）；
  2. 接口层：`/chat` 在前置短路就返回 `sources + notice + [DONE]`，
     不跑评估、不派 Agent、不吐 token —— 否则评估节点会先播一条
     「超出知识库范围」的固定拒答，学生照样觉得「有东西在回我」。

第 2 条为什么必须锁：只改编排层时，「花生在吗」这类抽不出 topic 的话
根本走不到编排层，会被 unknown-topic 分支截胡，看起来像没修好。
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as app_module


class _FakeLlm:
    """假 LLM：一旦被调用就吐一段可见文本，用来证明「这轮没走到模型」。"""

    def __init__(self, tokens=("不该出现",)):
        self._tokens = list(tokens)
        self.called = False

    async def astream(self, messages):
        self.called = True
        for token in self._tokens:
            yield type("Chunk", (), {"content": token})()


class NoTutorSilentTests(unittest.TestCase):
    """课堂关掉主讲助教后，普通提问应当是「零 AI 发言」。"""

    def _patch(self, name, new):
        patcher = mock.patch.object(app_module, name, new=new)
        patcher.start()
        self.addCleanup(patcher.stop)

    def setUp(self):
        # 限流与本用例无关，关掉免得连续请求互相干扰
        self._limiter = getattr(app_module.limiter, "enabled", True)
        app_module.limiter.enabled = False
        self.addCleanup(setattr, app_module.limiter, "enabled", self._limiter)

        self.llm = _FakeLlm()
        self._patch("get_llm", lambda provider=None: self.llm)
        self._patch("cleanup_expired_sessions", lambda: 0)
        # 课堂读库链路全部打桩：本用例只关心「读到的 use_tutor 是 False」之后怎么办
        self._patch("get_db", lambda: mock.MagicMock())
        self._patch("is_member", lambda db, classroom_id, user_id: True)
        self._patch("load_history", lambda db, classroom_id: [])
        self._patch("get_classroom_scenario", lambda db, classroom_id: "group")
        self._patch("get_classroom_use_tutor", lambda db, classroom_id: False)
        self._patch("load_last_state", lambda db, classroom_id, user_id: ("", 0))
        self._patch("save_message", lambda *a, **k: None)
        self._patch("load_profile", lambda *a, **k: None)
        self._patch("_persist_assistant", lambda *a, **k: None)
        self._patch("_record_agent_run", lambda *a, **k: None)
        # 评估节点：真跑起来就会「抽不出 topic」，正是要绕开的那条路
        self._patch(
            "assessment_graph",
            mock.MagicMock(ainvoke=mock.AsyncMock(return_value={"topic": "unknown"})),
        )

    def _post(self, message: str):
        from fastapi.testclient import TestClient

        payload = {
            "message": message,
            "classroom_id": 1,
            "user_id": 2,
            "provider": "openai",
        }
        with TestClient(app_module.app, client=("127.0.0.1", 12345)) as client:
            return client.post("/chat", json=payload)

    @staticmethod
    def _events(resp) -> list[dict]:
        events = []
        for line in resp.text.splitlines():
            if not line.startswith("data: "):
                continue
            payload = line[6:].strip()
            if payload == "[DONE]":
                continue
            try:
                events.append(json.loads(payload))
            except Exception:
                continue
        return events

    def test_plain_message_gets_notice_and_no_tokens(self):
        """普通提问：只有 sources + notice，绝不吐 token、绝不派 Agent。"""
        resp = self._post("花生在吗")
        self.assertEqual(resp.status_code, 200, resp.text[:400])
        events = self._events(resp)
        self.assertEqual([e["type"] for e in events], ["sources", "notice"])
        self.assertEqual(events[0]["items"], [])
        self.assertIn("没有 AI 回复", events[1]["content"])
        # 关键约束：模型一次都没被调用
        self.assertFalse(self.llm.called)
        # 也不该出现任何角色切换事件
        self.assertNotIn("agent", [e["type"] for e in events])

    # 注：「@出题助手 仍能出题」由 test_orchestration.test_quiz_mention_still_works_without_tutor
    # 锁编排层，接口层在容器里实测过（agent=peer）。不在这里重复：跑通那条要
    # 连带桩掉检索与评估，桩一多测试就只剩「我桩的东西能跑」，反而失真。


if __name__ == "__main__":
    unittest.main()
