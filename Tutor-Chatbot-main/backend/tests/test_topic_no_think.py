"""抽取节点「关掉推理模型思考」的行为测试（XB-32，2026-10-27）。

背景：`deepseek-flash` 是推理模型。抽 topic 只需要回一两个词，但实测它每次
先产出 23~29 个 **reasoning token**，这一段的耗时就是该节点 ~1.0s 的大头。
于是真正的优化不是砍 prompt（`ab_extract_prompt.py` 实测砍长度只省 7ms），
而是给抽取节点单独关掉思考（实测 mean −35%、p95 −42%）。

本文件锁四件事：
  1. 开关开着时，抽取走的是**带 thinking=disabled 的句柄**；
  2. 开关关掉时，抽取走**原样句柄**（逐字回退到 2026-10-27 之前的行为）；
  3. 厂商不认这个字段时**降级重试**，且降级**必须留痕**（WARNING）；
  4. 降级过一次的模型**不再重复送**该字段（否则每轮白费一次失败请求）。

判定口径（same / unknown / 新 topic）与抽取走哪条句柄无关，单独锁一条。
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import unittest  # noqa: E402

from agent import graph as graph_module  # noqa: E402
from agent.graph import extract_topic_node  # noqa: E402


class _Resp:
    def __init__(self, content: str):
        self.content = content


class _BoundLlm:
    """bind() 的产物：记录自己被调了几次。"""

    def __init__(self, parent: "_FakeLlm", kwargs: dict):
        self.parent = parent
        self.kwargs = kwargs

    async def ainvoke(self, _messages):
        self.parent.bound_calls += 1
        self.parent.bound_kwargs = self.kwargs
        if self.parent.fail_bound:
            raise RuntimeError("unknown request parameter: thinking")
        return _Resp(self.parent.response)


class _FakeLlm:
    model_name = "fake-model"

    def __init__(
        self,
        response: str = "same",
        fail_bound: bool = False,
        fail_plain: bool = False,
    ):
        self.response = response
        self.fail_bound = fail_bound
        self.fail_plain = fail_plain
        self.plain_calls = 0
        self.bound_calls = 0
        self.bound_kwargs: dict | None = None

    def bind(self, **kwargs):
        return _BoundLlm(self, kwargs)

    async def ainvoke(self, _messages):
        self.plain_calls += 1
        if self.fail_plain:
            raise RuntimeError("model unavailable")
        return _Resp(self.response)


def _state(llm, topic: str = "死锁", latest: str = "那怎么预防呢？") -> dict:
    from langchain_core.messages import AIMessage, HumanMessage

    return {
        "messages": [
            HumanMessage(content="什么是死锁？"),
            AIMessage(content="死锁是多个进程互相等待对方持有的资源。"),
            HumanMessage(content=latest),
        ],
        "topic": topic,
        "hint_level": 1,
        "misconception": "",
        "resolved": False,
        "llm": llm,
        "topic_changed": False,
    }


class NoThinkBindingTests(unittest.TestCase):
    def setUp(self):
        self._saved = set(graph_module._NO_THINK_UNSUPPORTED)
        graph_module._NO_THINK_UNSUPPORTED.clear()

    def tearDown(self):
        graph_module._NO_THINK_UNSUPPORTED.clear()
        graph_module._NO_THINK_UNSUPPORTED.update(self._saved)

    def test_enabled_binds_thinking_disabled(self):
        llm = _FakeLlm("same")
        with mock.patch.object(graph_module, "TOPIC_NO_THINK", True):
            patch = asyncio.run(extract_topic_node(_state(llm)))
        self.assertEqual(patch, {})
        self.assertEqual(llm.bound_calls, 1)
        self.assertEqual(llm.plain_calls, 0)
        self.assertEqual(
            llm.bound_kwargs, {"extra_body": {"thinking": {"type": "disabled"}}}
        )

    def test_disabled_uses_plain_llm(self):
        llm = _FakeLlm("same")
        with mock.patch.object(graph_module, "TOPIC_NO_THINK", False):
            patch = asyncio.run(extract_topic_node(_state(llm)))
        self.assertEqual(patch, {})
        self.assertEqual(llm.bound_calls, 0)
        self.assertEqual(llm.plain_calls, 1)

    def test_falls_back_when_param_rejected(self):
        """厂商不认 thinking 字段：必须降级重试，且不能让 /chat 挂掉。"""
        llm = _FakeLlm("死锁预防", fail_bound=True)
        with mock.patch.object(graph_module, "TOPIC_NO_THINK", True):
            with self.assertLogs(graph_module.logger, level=logging.WARNING) as logs:
                patch = asyncio.run(extract_topic_node(_state(llm)))
        self.assertEqual(patch["topic"], "死锁预防")
        self.assertTrue(patch["topic_changed"])
        self.assertEqual(llm.bound_calls, 1)
        self.assertEqual(llm.plain_calls, 1)  # 降级后确实用原句柄重试了
        self.assertIn("thinking", "\n".join(logs.output))

    def test_fallback_is_remembered(self):
        """同一个模型第二次直接用原句柄，不再白送一次失败请求。"""
        llm = _FakeLlm("新知识点", fail_bound=True)
        with mock.patch.object(graph_module, "TOPIC_NO_THINK", True):
            with self.assertLogs(graph_module.logger, level=logging.WARNING):
                asyncio.run(extract_topic_node(_state(llm)))
            before = llm.bound_calls
            asyncio.run(extract_topic_node(_state(llm)))
        self.assertEqual(before, 1)
        self.assertEqual(llm.bound_calls, 1, "第二次不该再 bind")
        self.assertEqual(llm.plain_calls, 2)

    def test_plain_failure_propagates(self):
        """开关关掉时原句柄失败，必须原样往上抛（调用方要转 502）。"""
        llm = _FakeLlm("same", fail_plain=True)
        with mock.patch.object(graph_module, "TOPIC_NO_THINK", False):
            with self.assertRaises(RuntimeError):
                asyncio.run(extract_topic_node(_state(llm)))
        self.assertEqual(llm.plain_calls, 1)
        self.assertEqual(llm.bound_calls, 0)


class VerdictTests(unittest.TestCase):
    """抽取的判定口径不受「关不关思考」影响（口径在 graph.py，与句柄无关）。"""

    def _run(self, raw: str, topic: str = "死锁") -> dict:
        llm = _FakeLlm(raw)
        with mock.patch.object(graph_module, "TOPIC_NO_THINK", True):
            return asyncio.run(extract_topic_node(_state(llm, topic=topic)))

    def test_same_returns_empty_patch(self):
        self.assertEqual(self._run("same"), {})

    def test_unknown_with_existing_topic_is_reuse(self):
        self.assertEqual(self._run("unknown"), {})

    def test_unknown_without_topic_resets_state(self):
        patch = self._run("unknown", topic="unknown")
        self.assertEqual(patch["topic"], "unknown")
        self.assertEqual(patch["hint_level"], 0)

    def test_new_topic_resets_hint_level(self):
        patch = self._run("虚拟内存")
        self.assertEqual(patch["topic"], "虚拟内存")
        self.assertEqual(patch["hint_level"], 0)
        self.assertTrue(patch["topic_changed"])


if __name__ == "__main__":
    unittest.main()
