"""提示词行为回归：把 smoke_chat.py 的四轮剧本固化成无额度单测。

背景（Prompt-6）：改完提示词必须回归。smoke_chat.py 是真实 E2E（起服务 +
烧额度），只能手动跑；run_tests.py 里此前只有零散节点级用例，没有
**剧本级回放**——提示词改动若让「确认语不归零」「棘轮被绕过」这类
跨两轮的行为退化，单测抓不住，只能等 smoke 手动跑才发现。

本文件用与 test_graph.py 相同的 mock 手法回放 smoke_chat 的内置剧本，
断言同一口径（topic 非空 / hint 棘轮递进 / 确认语归零），不碰网络与额度。
smoke_chat.py 保留——它验的是 SSE 与真实模型，与本文件互补。

剧本（与 scripts/smoke_chat.py DEFAULT_SCRIPT 逐字一致）：
    1. "什么是死锁？产生的必要条件有哪些？"
    2. "还是没太懂，能再讲清楚一点吗？"
    3. "那怎么预防死锁呢？举例说明一下"
    4. "明白了，谢谢！"
"""

import asyncio
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.graph import assessment_graph
from agent.prompts import (
    ASSESS_UNDERSTANDING_PROMPT,
    EXTRACT_TOPIC_PROMPT,
    PPT_OUTLINE_PROMPT,
    RAG_GROUNDING_RULES,
)
from agent.state import HINT_STRATEGIES

# 与 scripts/smoke_chat.py 保持一致；改剧本时两边同步改。
DEFAULT_SCRIPT = [
    "什么是死锁？产生的必要条件有哪些？",
    "还是没太懂，能再讲清楚一点吗？",
    "那怎么预防死锁呢？举例说明一下",
    "明白了，谢谢！",
]

# 每轮图的调用顺序是 extract_topic → assess_understanding，
# 所以 mock 响应按「extract, assess, extract, assess, ...」排列。
# 这是「合格模型」的剧本化响应：学生说没懂就抬深度，说懂了就归零。
# 注意轮 1 评估给 0 而非 1：首轮 topic 从空变成「死锁」是新知识点，
# 图会走「新 topic 重置学习态」（hint_level=0 从头讲，产品设计如此，
# 见 test_graph.test_new_topic_resets_learning_state），assess 给的 1
# 会被重置覆盖——所以首轮之后深度一定是 0，剧本按真实行为写。
MOCK_LLM_RESPONSES = [
    "死锁",                                                    # 轮1 抽取
    '{"resolved": false, "hint_level": 1, "misconception": ""}',  # 轮1 评估（被新 topic 重置覆盖）
    "same",                                                    # 轮2 追问沿用
    '{"resolved": false, "hint_level": 2, "misconception": "四个必要条件记忆不全"}',
    "same",                                                    # 轮3 追问沿用
    '{"resolved": false, "hint_level": 2, "misconception": ""}',
    "same",                                                    # 轮4 确认语
    '{"resolved": true, "hint_level": 0, "misconception": ""}',
]


class _Response:
    def __init__(self, content: str):
        self.content = content


class _SequenceLlm:
    """按队列依次吐响应；队列耗尽再被调用会抛 StopIteration——
    那是「图的调用次数变了」的信号，让它炸而不是静默给错答案。"""

    def __init__(self, responses):
        self._responses = iter(responses)

    def bind(self, **_kwargs):
        # 抽 topic 节点会 bind(thinking=disabled)（XB-32）；返回 self 以保持
        # 「按调用次序取响应」的剧本语义不变。
        return self

    async def ainvoke(self, _messages):
        return _Response(next(self._responses))


class SmokeScriptReplayTests(unittest.TestCase):
    """smoke_chat.py 四轮剧本的无额度回放。"""

    def _run_script(self, messages):
        history = []
        state = {
            "topic": "",
            "hint_level": 0,
            "misconception": "",
            "resolved": False,
            "llm": _SequenceLlm(MOCK_LLM_RESPONSES),
        }
        seen = []
        for message in messages:
            state["messages"] = history + [
                _human(message)
            ]
            result = asyncio.run(assessment_graph.ainvoke(state))
            state = {
                "topic": result.get("topic", ""),
                "hint_level": result.get("hint_level", 0),
                "misconception": result.get("misconception", ""),
                "resolved": result.get("resolved", False),
                "llm": state["llm"],
            }
            seen.append(dict(state))
            history = history + [
                _human(message),
                _ai("（mock 回答，不校验内容）"),
            ]
        return seen

    def test_first_turn_extracts_topic(self):
        seen = self._run_script(DEFAULT_SCRIPT)
        self.assertEqual(seen[0]["topic"], "死锁")

    def test_every_turn_has_non_empty_topic(self):
        # smoke_chat 的核心断言之一：topic 为空 = 知识点没抽出来，
        # 走的是「未识别知识点」降级路径。
        seen = self._run_script(DEFAULT_SCRIPT)
        for index, turn in enumerate(seen, 1):
            self.assertTrue(
                turn["topic"], "第 %d 轮 topic 为空，追问没沿用工知识点" % index
            )

    def test_hint_level_follows_the_learning_curve(self):
        # smoke_chat 用途第 3 条：学生说没听懂，hint_level 必须递进；
        # 说懂了必须归零。这里断言**整条剧本序列**——序列相等是最强
        # 回归断言，任何一环（抽取 / 评估 / 棘轮 / 归零）被改坏都会变。
        # 「下降每轮最多一级」的平滑约束是另一回事（XB-4），
        # 单测在 test_graph.HintLevelRatchetTests，不在这里重复。
        seen = self._run_script(DEFAULT_SCRIPT)
        levels = [turn["hint_level"] for turn in seen]
        self.assertEqual(
            levels,
            [0, 2, 2, 0],
            "深度序列不符合剧本：%s（新 topic 重置=0，没听懂抬到 2，懂了归零）" % levels,
        )

    def test_confirmation_resets_learning_state(self):
        # smoke_chat 末轮断言：说「明白了，谢谢」必须 resolved=true、
        # hint_level=0、misconception 清空——这三个一起归零才算话题结束。
        seen = self._run_script(DEFAULT_SCRIPT)
        final = seen[-1]
        self.assertTrue(final["resolved"], "确认语没触发 resolved")
        self.assertEqual(final["hint_level"], 0, "确认语后 hint_level 没归零")
        self.assertEqual(final["misconception"], "", "确认语后 misconception 没清空")


class PromptInventoryTests(unittest.TestCase):
    """提示词资产清单：四个模板 + 策略表必须都在且非空。

    防的是「重构时把某个 prompt 删了/清空了」——这类改动不报错，
    只是模型突然不会干某件事，线上很难定位。
    """

    def test_all_prompts_present_and_non_empty(self):
        for name, template in [
            ("EXTRACT_TOPIC_PROMPT", EXTRACT_TOPIC_PROMPT),
            ("ASSESS_UNDERSTANDING_PROMPT", ASSESS_UNDERSTANDING_PROMPT),
            ("PPT_OUTLINE_PROMPT", PPT_OUTLINE_PROMPT),
            ("RAG_GROUNDING_RULES", RAG_GROUNDING_RULES),
        ]:
            self.assertTrue(
                template and template.strip(), "%s 是空的" % name
            )

    def test_hint_strategies_cover_four_levels(self):
        # Prompt-2：四层讲法是产品语义，缺一层就是产品行为丢失。
        self.assertEqual(sorted(HINT_STRATEGIES), [0, 1, 2, 3])
        for level, text in HINT_STRATEGIES.items():
            self.assertTrue(
                text and text.strip(), "hint_level %d 的策略文案是空的" % level
            )

    def test_grounding_rules_live_in_prompts_module(self):
        # XB-18 归口的守卫：提示词只写 prompts.py / state.py 两处（Prompt-1）。
        self.assertIn("知识库资料使用要求", RAG_GROUNDING_RULES)


def _human(content):
    from langchain_core.messages import HumanMessage

    return HumanMessage(content=content)


def _ai(content):
    from langchain_core.messages import AIMessage

    return AIMessage(content=content)


if __name__ == "__main__":
    unittest.main()
