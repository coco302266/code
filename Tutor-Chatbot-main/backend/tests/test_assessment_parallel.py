"""O3：评估链路分步并行（run_assessment_with_parallel）的单测（2026-10-27）。

测的是**编排语义**，不打真实 LLM、不碰向量库。
最关键的两条：

1. **并行真的发生了**——耗时接近 max(两侧)，不是 sum(两侧)。这条不成立的话
   O3 就是白改（串行也能"看起来对"）。
2. **错误分类正确**——评估失败是 `AssessmentFailed`（调用方转 502），
   并行段失败原样抛出（转 500）。并行化很容易把两类错误混成一类，
   届时 detail 会误导排查方向。

另外锁一条等价性：分步合并出来的 state 必须与 `assessment_graph.ainvoke` 一致。
"""

import asyncio
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.messages import HumanMessage  # noqa: E402

from agent.graph import (  # noqa: E402
    AssessmentFailed,
    assessment_graph,
    run_assessment_with_parallel,
)


class _Response:
    def __init__(self, content: str):
        self.content = content


class _SequenceLlm:
    """按调用顺序返回预设内容：第一次给 topic，第二次给理解评估 JSON。"""

    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls = 0

    def bind(self, **_kwargs):
        # 抽 topic 节点会 bind(thinking=disabled)（XB-32）；这里返回 self
        # 以保持「按调用次序取响应」的语义不变。
        return self

    async def ainvoke(self, _messages):
        # 抽 topic 与理解评估共用同一个 llm，靠调用次序区分。
        content = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return _Response(content)


class _SlowLlm(_SequenceLlm):
    """每次调用都睡一会儿，用来观察并行是否真的重叠。"""

    def __init__(self, responses: list[str], delay: float):
        super().__init__(responses)
        self.delay = delay

    async def ainvoke(self, _messages):
        await asyncio.sleep(self.delay)
        return await super().ainvoke(_messages)


def make_state(llm, topic: str = "", messages: list | None = None) -> dict:
    return {
        "messages": messages or [HumanMessage(content="死锁怎么预防？")],
        "topic": topic,
        "hint_level": 0,
        "misconception": "",
        "resolved": False,
        "llm": llm,
        "topic_changed": False,
    }


class ParallelismTests(unittest.TestCase):
    def test_parallel_overlaps_instead_of_adding_up(self):
        """核心判据：总耗时接近 max(理解评估, 并行段)，不是两者之和。"""
        assess_delay = 0.30
        parallel_delay = 0.30
        llm = _SlowLlm(
            ["死锁", '{"resolved": false, "hint_level": 1, "misconception": ""}'],
            assess_delay,
        )

        async def slow_parallel(topic):
            await asyncio.sleep(parallel_delay)
            return ["doc-for-" + topic]

        async def scenario():
            started = time.perf_counter()
            state, result = await run_assessment_with_parallel(
                make_state(llm), slow_parallel
            )
            return time.perf_counter() - started, state, result

        elapsed, state, result = asyncio.run(scenario())

        # 抽 topic 单独一次（0.30s）+ 并行段（0.30s）≈ 0.60s；
        # 若退化成串行会是 ≈0.90s。取 0.80 作上界，留足调度抖动空间。
        self.assertLess(elapsed, 0.80, "并行未生效：耗时接近串行")
        self.assertGreaterEqual(elapsed, 0.55, "并行段被跳过了？")
        self.assertEqual(result, ["doc-for-死锁"])
        self.assertEqual(state["topic"], "死锁")

    def test_parallel_gets_topic_from_first_node(self):
        """并行段拿到的必须是「抽 topic 之后」的 topic，不是初始 topic。"""
        llm = _SequenceLlm(
            ["死锁", '{"resolved": false, "hint_level": 1, "misconception": ""}']
        )
        seen = {}

        async def capture(topic):
            seen["topic"] = topic
            return []

        state, _ = asyncio.run(
            run_assessment_with_parallel(make_state(llm, topic="旧话题"), capture)
        )
        self.assertEqual(seen["topic"], "死锁")
        self.assertEqual(state["topic"], "死锁")
        self.assertTrue(state["topic_changed"])

    def test_unknown_topic_passed_through_verbatim(self):
        """unknown 原样传给并行段（由调用方决定是否归一成空串），不在这里偷偷改。"""
        llm = _SequenceLlm(["unknown"])
        seen = {}

        async def capture(topic):
            seen["topic"] = topic
            return []

        state, _ = asyncio.run(run_assessment_with_parallel(make_state(llm), capture))
        # topic=unknown 时理解评估会短路（不调 LLM），所以 llm 只需一次返回。
        self.assertEqual(seen["topic"], "unknown")
        self.assertEqual(state["topic"], "unknown")

    def test_empty_topic_becomes_empty_string(self):
        """初始 topic 为空且抽不出来时，并行段收到 '' 而不是 None。"""
        llm = _SequenceLlm(["same"])
        seen = {}

        async def capture(topic):
            seen["topic"] = topic
            return []

        asyncio.run(run_assessment_with_parallel(make_state(llm, topic=""), capture))
        self.assertEqual(seen["topic"], "")


class ErrorRoutingTests(unittest.TestCase):
    def test_assess_failure_becomes_assessment_failed(self):
        """评估分支炸了 → AssessmentFailed（调用方转 502）。"""
        llm = _SequenceLlm(["死锁"])
        # 第二次调用（理解评估）抛错
        llm.ainvoke = _raising_after_first(["死锁"])  # type: ignore[method-assign]

        async def ok_parallel(topic):
            return ["doc"]

        with self.assertRaises(AssessmentFailed):
            asyncio.run(
                run_assessment_with_parallel(make_state(llm), ok_parallel)
            )

    def test_assess_failure_keeps_original_cause(self):
        """包装不能吃掉真实原因——__cause__ 必须是原始异常。"""
        llm = _SequenceLlm(["死锁"])
        llm.ainvoke = _raising_after_first(["死锁"])  # type: ignore[method-assign]

        async def ok_parallel(topic):
            return ["doc"]

        try:
            asyncio.run(run_assessment_with_parallel(make_state(llm), ok_parallel))
            self.fail("应当抛 AssessmentFailed")
        except AssessmentFailed as exc:
            self.assertIsInstance(exc.__cause__, RuntimeError)

    def test_parallel_failure_propagates_untouched(self):
        """并行段（检索）炸了 → 原样抛出，不能被当成模型不可用。"""
        llm = _SequenceLlm(
            ["死锁", '{"resolved": false, "hint_level": 1, "misconception": ""}']
        )

        async def boom(topic):
            raise ValueError("知识库不可用")

        with self.assertRaises(ValueError):
            asyncio.run(run_assessment_with_parallel(make_state(llm), boom))

    def test_assess_wins_when_both_fail(self):
        """两分支都失败时以评估为准（原串行链路里评估先跑，也是它先失败）。"""
        llm = _SequenceLlm(["死锁"])
        llm.ainvoke = _raising_after_first(["死锁"])  # type: ignore[method-assign]

        async def boom(topic):
            raise ValueError("知识库不可用")

        with self.assertRaises(AssessmentFailed):
            asyncio.run(run_assessment_with_parallel(make_state(llm), boom))


class EquivalenceWithGraphTests(unittest.TestCase):
    def test_split_result_matches_full_graph(self):
        """分步入口与 `assessment_graph.ainvoke` 必须给出同样的 state。

        两者并存是刻意的（图仍在被测试与编排层用），等价性靠这条单测锁住。
        """
        for responses, initial_topic in [
            (["死锁", '{"resolved": false, "hint_level": 2, "misconception": "死循环"}'], ""),
            (["same", '{"resolved": true, "hint_level": 0, "misconception": ""}'], "死锁"),
            (["unknown"], ""),
        ]:
            with self.subTest(responses=responses):
                graph_state = asyncio.run(
                    assessment_graph.ainvoke(
                        make_state(_SequenceLlm(responses), topic=initial_topic)
                    )
                )
                split_state, _ = asyncio.run(
                    run_assessment_with_parallel(
                        make_state(_SequenceLlm(responses), topic=initial_topic),
                        lambda topic: asyncio.sleep(0, result=[]),
                    )
                )
                for key in ("topic", "hint_level", "misconception", "resolved",
                            "topic_changed"):
                    self.assertEqual(
                        split_state.get(key), graph_state.get(key), msg=key
                    )


def _raising_after_first(responses):
    """第一次调用（抽 topic）正常返回，之后（理解评估）抛 RuntimeError。"""
    state = {"n": 0}

    async def ainvoke(_messages):
        state["n"] += 1
        if state["n"] == 1:
            return _Response(responses[0])
        raise RuntimeError("模型不可用")

    return ainvoke


if __name__ == "__main__":
    unittest.main()
