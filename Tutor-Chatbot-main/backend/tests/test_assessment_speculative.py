"""O12：投机执行（run_assessment_speculative）的单测（2026-10-28）。

背景：XB-31 实测否决了「盲缓存 topic」——换题轮上沿用旧 topic 会让 93% 的请求
上下文退化，且 topic 落库后跨轮累积、从回答上还看不出来。投机执行的延迟收益
与盲缓存相同，但 E 仍是唯一裁决者。

这个测试锁的是**投机执行最容易出错的三件事**：

1. **命中/未命中的资源账**——命中时并行段只跑一次（省下的就是收益）；
   未命中时跑两次（投机作废 + 确认后重跑），且第二次必须用**新 topic**。
2. **未命中时的异常必须被吞掉**——投机产物的结果本来就要丢，把它失败报上去
   会让一次正常的换题变成 502 误报。这是最隐蔽的一条。
3. **任何路径上都不留孤儿任务**——抽 topic 失败时，已经在跑的投机任务必须
   被取消，否则会泄漏连接并继续烧钱。

外加一条延迟账：命中时耗时 ≈ max(E, A, R)，而不是 E + max(A, R)。
"""

import asyncio
import json
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.messages import HumanMessage  # noqa: E402

from agent.graph import (  # noqa: E402
    AssessmentFailed,
    run_assessment_speculative,
)

ASSESS_REPLY = {
    "resolved": False,
    "hint_level": 2,
    "misconception": "把死锁理解成了死循环",
}


class _Response:
    def __init__(self, content: str):
        self.content = content


class _FakeLlm:
    """按 prompt 内容区分两个节点：评估节点 / 抽 topic 节点。"""

    model_name = "fake-model"

    def __init__(
        self,
        topic_reply: str = "same",
        assess_reply: dict | None = None,
        topic_delay: float = 0.0,
        assess_delay: float = 0.0,
        fail_topic: bool = False,
        fail_assess: bool = False,
    ):
        self.topic_reply = topic_reply
        self.assess_reply = assess_reply or dict(ASSESS_REPLY)
        self.topic_delay = topic_delay
        self.assess_delay = assess_delay
        self.fail_topic = fail_topic
        self.fail_assess = fail_assess
        self.prompts: list[str] = []

    def bind(self, **_kwargs):
        return self

    async def ainvoke(self, messages):
        text = messages[0].content if messages else ""
        self.prompts.append(text)
        if "你在评估一名正在准备面试的学生" in text:
            await asyncio.sleep(self.assess_delay)
            if self.fail_assess:
                raise RuntimeError("assess boom")
            return _Response(json.dumps(self.assess_reply, ensure_ascii=False))
        await asyncio.sleep(self.topic_delay)
        if self.fail_topic:
            raise RuntimeError("topic boom")
        return _Response(self.topic_reply)


def _state(llm, topic: str = "死锁", hint: int = 1, utterance: str = "明白了"):
    return {
        "messages": [HumanMessage(content=utterance)],
        "topic": topic,
        "hint_level": hint,
        "misconception": "",
        "resolved": False,
        "llm": llm,
        "topic_changed": False,
    }


def _make_builder(log: list, delay: float = 0.0, result: str = "DOCS", fail_first: bool = False):
    async def build(topic: str):
        log.append(topic)
        if fail_first and len(log) == 1:
            raise RuntimeError("retrieve boom (speculative)")
        await asyncio.sleep(delay)
        return result

    return build


class SpeculativeHitTest(unittest.TestCase):
    """命中（追问沿用上一轮 topic）时的行为。"""

    def test_hit_reuses_speculative_result_and_runs_once(self):
        llm = _FakeLlm(topic_reply="same")
        log: list[str] = []
        state, docs = asyncio.run(
            run_assessment_speculative(
                _state(llm), _make_builder(log, result="DOCS")
            )
        )
        # 并行段只跑一次 —— 这就是省下来的那部分
        self.assertEqual(log, ["死锁"])
        self.assertEqual(docs, "DOCS")
        # 评估产物照常合并进 state
        self.assertEqual(state["hint_level"], 2)
        self.assertEqual(state["misconception"], "把死锁理解成了死循环")

    def test_hit_with_no_topic_patch(self):
        """extract 返回 {}（same）也属命中。"""
        llm = _FakeLlm(topic_reply="same")
        log: list[str] = []
        asyncio.run(
            run_assessment_speculative(_state(llm), _make_builder(log))
        )
        self.assertEqual(len(log), 1)

    def test_first_turn_falls_back_to_stepwise(self):
        """首轮没有 prev_topic 可投机 —— 必须走分步并行，不能瞎猜。"""
        llm = _FakeLlm(topic_reply="死锁")
        log: list[str] = []
        state, docs = asyncio.run(
            run_assessment_speculative(_state(llm, topic=""), _make_builder(log))
        )
        self.assertEqual(log, ["死锁"])
        self.assertEqual(docs, "DOCS")
        # 分步路径会把抽到的 topic 写回 state
        self.assertEqual(state["topic"], "死锁")

    def test_unknown_topic_falls_back(self):
        llm = _FakeLlm(topic_reply="死锁")
        log: list[str] = []
        asyncio.run(
            run_assessment_speculative(
                _state(llm, topic="unknown"), _make_builder(log)
            )
        )
        self.assertEqual(log, ["死锁"])


class SpeculativeMissTest(unittest.TestCase):
    """未命中（换题）时的行为 —— 这里藏着最容易踩的坑。"""

    def test_miss_reruns_with_new_topic(self):
        llm = _FakeLlm(topic_reply="B+树索引")
        log: list[str] = []
        state, docs = asyncio.run(
            run_assessment_speculative(_state(llm), _make_builder(log))
        )
        # 先用旧 topic 投机一次，作废后再用新 topic 跑一次
        self.assertEqual(log, ["死锁", "b+树索引"])
        self.assertEqual(docs, "DOCS")
        self.assertEqual(state["topic"], "b+树索引")

    def test_miss_swallows_speculative_failure(self):
        """关键：投机段失败不该影响结果 —— 它的产物本来就要丢。

        如果这里往上抛，用户换一次话题就会看到 502，而真实评估其实还没开始跑。
        """
        llm = _FakeLlm(topic_reply="B+树索引")
        log: list[str] = []
        state, docs = asyncio.run(
            run_assessment_speculative(
                _state(llm), _make_builder(log, fail_first=True)
            )
        )
        self.assertEqual(log, ["死锁", "b+树索引"])
        self.assertEqual(docs, "DOCS")
        self.assertEqual(state["topic"], "b+树索引")

    def test_miss_when_assess_spec_fails(self):
        """投机评估失败同样要吞掉：换题后真实评估会重跑，旧结果无意义。"""
        llm = _FakeLlm(topic_reply="B+树索引", fail_assess=False)
        log: list[str] = []
        # 让第一次评估（投机那次）抛错
        original = llm.ainvoke
        calls = {"n": 0}

        async def flaky(messages):
            text = messages[0].content if messages else ""
            if "你在评估一名正在准备面试的学生" in text:
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("speculative assess boom")
            return await original(messages)

        llm.ainvoke = flaky
        state, docs = asyncio.run(
            run_assessment_speculative(_state(llm), _make_builder(log))
        )
        self.assertEqual(docs, "DOCS")
        self.assertEqual(state["topic"], "b+树索引")


class ErrorClassificationTest(unittest.TestCase):
    """命中路径上，错误仍必须分类正确 —— 投机不能把两类失败混成一类。"""

    def test_hit_assess_failure_raises_assessment_failed(self):
        llm = _FakeLlm(topic_reply="same", fail_assess=True)
        log: list[str] = []
        with self.assertRaises(AssessmentFailed):
            asyncio.run(
                run_assessment_speculative(_state(llm), _make_builder(log))
        )

    def test_hit_retrieve_failure_propagates_raw(self):
        llm = _FakeLlm(topic_reply="same")
        log: list[str] = []
        with self.assertRaises(RuntimeError):
            asyncio.run(
                run_assessment_speculative(
                    _state(llm), _make_builder(log, fail_first=True)
                )
            )

    def test_topic_failure_cancels_speculative_tasks(self):
        """抽 topic 失败时，投机任务必须收干净，不能留孤儿继续烧钱。"""
        llm = _FakeLlm(fail_topic=True)
        started = []
        cancelled = []

        async def build(topic: str):
            started.append(topic)
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                cancelled.append(topic)
                raise

        with self.assertRaises(RuntimeError):
            asyncio.run(run_assessment_speculative(_state(llm), build))

        self.assertEqual(started, ["死锁"])
        self.assertEqual(cancelled, ["死锁"])


class LatencyTest(unittest.TestCase):
    """延迟账：命中的收益必须真的兑现。"""

    def test_hit_is_faster_than_stepwise(self):
        d = 0.25
        llm_spec = _FakeLlm(
            topic_reply="same",
            topic_delay=d,
            assess_delay=d,
        )
        log: list[str] = []
        builder = _make_builder(log, delay=d)

        t0 = time.perf_counter()
        asyncio.run(run_assessment_speculative(_state(llm_spec), builder))
        spec_ms = (time.perf_counter() - t0) * 1000

        # 对照：O3 分步并行（先抽 topic，再 gather）
        from agent.graph import run_assessment_with_parallel

        llm_step = _FakeLlm(
            topic_reply="same", topic_delay=d, assess_delay=d
        )
        t0 = time.perf_counter()
        asyncio.run(run_assessment_with_parallel(_state(llm_step), builder))
        step_ms = (time.perf_counter() - t0) * 1000

        print(
            "\n  投机 %.0fms vs 分步 %.0fms（两侧各 %.0fms）"
            % (spec_ms, step_ms, d * 1000)
        )
        # 分步 ≈ E + max(A,R) ≈ 2d；投机 ≈ max(E,A,R) ≈ d（+ 少量调度开销）
        self.assertLess(spec_ms, step_ms * 0.75)


if __name__ == "__main__":
    unittest.main()
