import asyncio
import logging
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.messages import AIMessage, HumanMessage
from pydantic import ValidationError

from agent.graph import (
    assessment_graph,
    assess_understanding_node,
    extract_topic_node,
    get_llm,
    get_model,
    parse_assessment_result,
)


class _Response:
    def __init__(self, content: str):
        self.content = content


class _FakeLlm:
    def __init__(self, content: str):
        self.content = content

    def bind(self, **_kwargs):
        # 抽 topic 节点会 bind(thinking=disabled)（XB-32）。这里返回 self，
        # 让 canned 响应照原顺序走——本文件测的是判定逻辑，不是参数传递
        # （参数与降级在 tests/test_topic_no_think.py 里单独锁）。
        return self

    async def ainvoke(self, _messages):
        return _Response(self.content)


class _SequenceLlm:
    def __init__(self, responses: list[str]):
        self.responses = iter(responses)

    def bind(self, **_kwargs):
        return self

    async def ainvoke(self, _messages):
        return _Response(next(self.responses))


class AssessmentResultTests(unittest.TestCase):
    def test_accepts_strict_valid_json(self):
        result = parse_assessment_result(
            '{"resolved": false, "hint_level": 2, "misconception": "base case"}'
        )

        self.assertFalse(result.resolved)
        self.assertEqual(result.hint_level, 2)

    def test_rejects_string_booleans_and_out_of_range_hints(self):
        with self.assertRaises(ValidationError):
            parse_assessment_result(
                '{"resolved": "false", "hint_level": 2, "misconception": ""}'
            )
        with self.assertRaises(ValidationError):
            parse_assessment_result(
                '{"resolved": false, "hint_level": 4, "misconception": ""}'
            )


class ProviderConfigurationTests(unittest.TestCase):
    def test_groq_uses_gpt_oss_120b_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(get_model("groq"), "openai/gpt-oss-120b")

    def test_groq_requires_an_api_key(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "GROQ_API_KEY"):
                get_llm("groq")


class TopicTrackingTests(unittest.TestCase):
    def test_unknown_follow_up_preserves_current_topic(self):
        state = {
            "messages": [
                HumanMessage(content="Explain recursion"),
                AIMessage(content="What is a base case?"),
                HumanMessage(content="I think it stops at zero"),
            ],
            "topic": "recursion",
            "hint_level": 1,
            "misconception": "",
            "resolved": False,
            "llm": _FakeLlm("unknown"),
        }

        update = asyncio.run(extract_topic_node(state))

        self.assertEqual(update, {})

    def test_new_topic_resets_learning_state(self):
        state = {
            "messages": [
                HumanMessage(content="Explain recursion"),
                AIMessage(content="What is a base case?"),
                HumanMessage(content="Let's switch to hash tables"),
            ],
            "topic": "recursion",
            "hint_level": 3,
            "misconception": "base case",
            "resolved": True,
            "llm": _FakeLlm("hash tables"),
        }

        result = asyncio.run(assessment_graph.ainvoke(state))

        self.assertEqual(result["topic"], "hash tables")
        self.assertEqual(result["hint_level"], 0)
        self.assertEqual(result["misconception"], "")
        self.assertFalse(result["resolved"])

    def test_resolved_problem_resets_hint_level(self):
        state = {
            "messages": [
                HumanMessage(content="Explain recursion"),
                AIMessage(content="What does the base case do?"),
                HumanMessage(content="It stops the recursive calls."),
            ],
            "topic": "recursion",
            "hint_level": 3,
            "misconception": "base case",
            "resolved": False,
            "llm": _FakeLlm(
                '{"resolved": true, "hint_level": 3, "misconception": ""}'
            ),
        }

        update = asyncio.run(assess_understanding_node(state))

        self.assertTrue(update["resolved"])
        self.assertEqual(update["hint_level"], 0)
        self.assertEqual(update["misconception"], "")

    def test_async_graph_uses_request_scoped_llm(self):
        state = {
            "messages": [
                HumanMessage(content="Explain recursion"),
                AIMessage(content="What is a base case?"),
                HumanMessage(content="It stops the recursive calls"),
            ],
            "topic": "recursion",
            "hint_level": 1,
            "misconception": "",
            "resolved": False,
            "llm": _SequenceLlm(
                [
                    "same",
                    '{"resolved": false, "hint_level": 2, "misconception": "termination condition"}',
                ]
            ),
        }

        result = asyncio.run(assessment_graph.ainvoke(state))

        self.assertEqual(result["topic"], "recursion")
        self.assertEqual(result["hint_level"], 2)
        self.assertEqual(result["misconception"], "termination condition")


class _ListHandler(logging.Handler):
    """收集 WARNING 及以上日志，避免依赖 3.10+ 的 assertNoLogs。"""

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


class AssessmentLoggingTests(unittest.TestCase):
    """LLM-6a：解析失败必须留证据，且不能和「模型判 false」混淆。"""

    def setUp(self):
        self.handler = _ListHandler()
        self.graph_logger = logging.getLogger("agent.graph")
        self.graph_logger.addHandler(self.handler)

    def tearDown(self):
        self.graph_logger.removeHandler(self.handler)

    def _state(self, hint_level, llm_content, misconception=""):
        return {
            "messages": [
                HumanMessage(content="Explain recursion"),
                AIMessage(content="What is a base case?"),
                HumanMessage(content="I think it stops at zero"),
            ],
            "topic": "recursion",
            "hint_level": hint_level,
            "misconception": misconception,
            "resolved": False,
            "llm": _FakeLlm(llm_content),
        }

    def test_parse_failure_logs_warning_and_degrades(self):
        update = asyncio.run(
            assess_understanding_node(self._state(2, "not json at all"))
        )

        # 行为不变：沿用旧 hint_level 与旧 misconception
        self.assertFalse(update["resolved"])
        self.assertEqual(update["hint_level"], 2)
        self.assertEqual(update["misconception"], "")

        self.assertEqual(len(self.handler.messages), 1)
        self.assertIn("评估节点解析失败", self.handler.messages[0])
        self.assertIn("in_hint_level=2", self.handler.messages[0])

    def test_model_judged_false_logs_no_warning(self):
        # 正常判 false 不该产生 WARNING，否则日志本身就成了噪声，
        # 也失去「和解析失败区分开」的意义。
        update = asyncio.run(
            assess_understanding_node(
                self._state(
                    2, '{"resolved": false, "hint_level": 2, "misconception": "base case"}'
                )
            )
        )

        self.assertFalse(update["resolved"])
        self.assertEqual(self.handler.messages, [])


class HintLevelRatchetTests(unittest.TestCase):
    def _state(self, hint_level, model_hint, misconception=""):
        return {
            "messages": [
                HumanMessage(content="Explain recursion"),
                AIMessage(content="What is a base case?"),
                HumanMessage(content="I think it stops at zero"),
            ],
            "topic": "recursion",
            "hint_level": hint_level,
            "misconception": "",
            "resolved": False,
            "llm": _FakeLlm(
                '{"resolved": false, "hint_level": %d, "misconception": "%s"}'
                % (model_hint, misconception)
            ),
        }

    def test_steps_down_only_one_level(self):
        # 模型给 1 也不一步跳到 1：temperature 0.4 下模型会抖，
        # 每轮最多降 1 级，避免讲解深度来回跳。
        update = asyncio.run(assess_understanding_node(self._state(3, 1)))
        self.assertEqual(update["hint_level"], 2)

    def test_still_raises_when_model_asks_for_more_depth(self):
        update = asyncio.run(assess_understanding_node(self._state(1, 2)))
        self.assertEqual(update["hint_level"], 2)

    def test_stays_when_model_repeats_same_level(self):
        update = asyncio.run(assess_understanding_node(self._state(2, 2)))
        self.assertEqual(update["hint_level"], 2)


class SuspectedMissedResolutionTests(unittest.TestCase):
    def _state(self, hint_level, model_hint, misconception):
        return {
            "messages": [
                HumanMessage(content="Explain recursion"),
                AIMessage(content="What is a base case?"),
                HumanMessage(content="I think it stops at zero"),
            ],
            "topic": "recursion",
            "hint_level": hint_level,
            "misconception": "",
            "resolved": False,
            "llm": _FakeLlm(
                '{"resolved": false, "hint_level": %d, "misconception": "%s"}'
                % (model_hint, misconception)
            ),
        }

    def test_deepest_level_without_misconception_warns(self):
        handler = _ListHandler()
        graph_logger = logging.getLogger("agent.graph")
        graph_logger.addHandler(handler)
        try:
            update = asyncio.run(assess_understanding_node(self._state(3, 3, "")))
        finally:
            graph_logger.removeHandler(handler)

        self.assertEqual(update["hint_level"], 3)
        self.assertEqual(len(handler.messages), 1)
        self.assertIn("疑似 resolved 漏判", handler.messages[0])

    def test_deepest_level_with_misconception_stays_quiet(self):
        handler = _ListHandler()
        graph_logger = logging.getLogger("agent.graph")
        graph_logger.addHandler(handler)
        try:
            update = asyncio.run(
                assess_understanding_node(self._state(3, 3, "base case"))
            )
        finally:
            graph_logger.removeHandler(handler)

        self.assertEqual(update["hint_level"], 3)
        self.assertEqual(handler.messages, [])


class LLMResilienceTests(unittest.TestCase):
    """LLM-8：get_llm 必须给所有 provider 显式 timeout + 最多 1 次重试。

    此前 get_llm 不传这两个参数，模型侧卡住时 /chat 会一直挂着。
    这条用例锁死「参数确实传进了底层 LLM 构造」，改坏会当场变红。
    """

    def test_openai_gets_timeout_and_retry(self):
        with patch.dict(
            os.environ, {"LLM_API_KEY": "test-key", "LLM_BASE_URL": "http://x"}
        ):
            with patch("langchain_openai.ChatOpenAI") as mock_cls:
                get_llm("openai")
                _, kwargs = mock_cls.call_args
                self.assertEqual(kwargs["timeout"], 60)
                self.assertEqual(kwargs["max_retries"], 1)

    def test_groq_gets_timeout_and_retry(self):
        with patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}):
            with patch("langchain_groq.ChatGroq") as mock_cls:
                get_llm("groq")
                _, kwargs = mock_cls.call_args
                self.assertEqual(kwargs["timeout"], 60)
                self.assertEqual(kwargs["max_retries"], 1)


if __name__ == "__main__":
    unittest.main()
