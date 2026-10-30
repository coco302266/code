"""M4 多智能体编排测试（零 LLM、零 IO，全部纯函数）。

覆盖重点是**降级路径**而不是正常路径：正常路径（v1 单 Agent）与 M1
完全一致、不会出错；真正会静默出问题的是「多场景没生效但回答看起来正常」，
所以每一条降级分支都必须有断言钉住。
"""

from __future__ import annotations

import unittest

from agent.orchestrator import plan_agents
from agent.scenarios import (
    MAX_AGENTS_PER_TURN,
    SCENARIOS,
    get_scenario,
    list_scenarios,
    normalize_scenario,
)


class ScenarioTableTests(unittest.TestCase):
    def test_three_scenarios_defined(self):
        self.assertEqual(set(SCENARIOS), {"v1", "group", "debate"})

    def test_normalize_unknown_falls_back_to_v1(self):
        self.assertEqual(normalize_scenario("nope"), "v1")
        self.assertEqual(normalize_scenario(""), "v1")
        self.assertEqual(normalize_scenario(None), "v1")
        # 大小写与空格不该把合法场景打回 v1
        self.assertEqual(normalize_scenario(" Group "), "group")

    def test_no_scenario_exceeds_agent_cap(self):
        """单轮 Agent 数不能超过硬上限——否则直接撞限流。"""
        for key, spec in SCENARIOS.items():
            self.assertLessEqual(
                len(spec.steps),
                MAX_AGENTS_PER_TURN,
                f"场景 {key} 的 Agent 数超过上限 {MAX_AGENTS_PER_TURN}",
            )

    def test_first_step_has_no_handoff(self):
        """首位没有前文可接，handoff 必须为空，否则提示词会出现「接着说」却没有前文。"""
        for key, spec in SCENARIOS.items():
            self.assertEqual(spec.steps[0].handoff, "", f"场景 {key} 首位不该有 handoff")

    def test_later_steps_have_handoff(self):
        """后续位必须有衔接指令，否则就是并联各答各的，不是协作。"""
        for key, spec in SCENARIOS.items():
            for step in spec.steps[1:]:
                self.assertTrue(step.handoff.strip(), f"场景 {key} 的 {step.role} 缺 handoff")

    def test_list_scenarios_exposes_agents(self):
        items = list_scenarios()
        self.assertEqual(len(items), 3)
        by_key = {i["key"]: i for i in items}
        self.assertEqual(by_key["debate"]["agents"], ["tutor", "peer", "examiner"])


class PlanAgentsTests(unittest.TestCase):
    def test_v1_is_single_tutor_by_default(self):
        plan = plan_agents("v1")
        self.assertEqual(plan["roles"], ["tutor"])
        self.assertEqual(len(plan["steps"]), 1)
        self.assertFalse(plan["degraded"])
        self.assertEqual(plan["reason"], "")

    def test_v1_honors_explicit_role(self):
        """单步场景下客户端指定什么角色就是什么角色（M1 行为不变）。"""
        plan = plan_agents("v1", explicit_role="examiner")
        self.assertEqual(plan["roles"], ["examiner"])

    def test_unknown_role_falls_back_to_tutor(self):
        plan = plan_agents("v1", explicit_role="wizard")
        self.assertEqual(plan["roles"], ["tutor"])

    def test_group_uses_two_agents(self):
        plan = plan_agents("group")
        self.assertEqual(plan["roles"], ["tutor", "peer"])
        self.assertEqual(plan["steps"][1]["handoff"], get_scenario("group").steps[1].handoff)
        self.assertFalse(plan["degraded"])

    def test_debate_uses_three_agents(self):
        plan = plan_agents("debate")
        self.assertEqual(plan["roles"], ["tutor", "peer", "examiner"])
        self.assertEqual(plan["steps"][0]["stance"], "立论")
        self.assertEqual(plan["steps"][1]["stance"], "质疑")
        self.assertEqual(plan["steps"][2]["stance"], "追问")

    def test_multi_step_ignores_explicit_role(self):
        """多步场景由场景决定角色：客户端传 examiner 不该把小组讨论变成考试。"""
        plan = plan_agents("group", explicit_role="examiner")
        self.assertEqual(plan["roles"], ["tutor", "peer"])

    def test_unknown_scenario_degrades_to_v1_with_reason(self):
        plan = plan_agents("nope")
        self.assertEqual(plan["scenario"], "v1")
        self.assertTrue(plan["degraded"])
        self.assertIn("未知场景", plan["reason"])

    def test_empty_scenario_is_not_degradation(self):
        """没指定场景 = 默认 1v1，不该报降级，否则日志里全是假信号。"""
        for value in ("", None):
            plan = plan_agents(value)
            self.assertFalse(plan["degraded"], f"{value!r} 不该被判为降级")
            self.assertEqual(plan["roles"], ["tutor"])

    def test_disabled_collab_degrades_to_single_agent(self):
        plan = plan_agents("debate", enabled=False)
        self.assertEqual(len(plan["steps"]), 1)
        self.assertTrue(plan["degraded"])
        self.assertIn("关闭", plan["reason"])

    def test_disabled_collab_honors_explicit_role(self):
        plan = plan_agents("debate", enabled=False, explicit_role="teacher")
        self.assertEqual(plan["roles"], ["teacher"])

    def test_max_agents_truncates_and_marks_degraded(self):
        plan = plan_agents("debate", max_agents=2)
        self.assertEqual(plan["roles"], ["tutor", "peer"])
        self.assertTrue(plan["degraded"])
        self.assertIn("上限", plan["reason"])

    def test_zero_max_agents_keeps_only_first(self):
        plan = plan_agents("debate", max_agents=0)
        self.assertEqual(len(plan["steps"]), 1)
        self.assertTrue(plan["degraded"])

    def test_replaced_role_does_not_inherit_stance(self):
        """角色被替换后不能沿用别人的 stance，否则会出现「考官」顶着「立论」。"""
        plan = plan_agents("debate", enabled=False, explicit_role="examiner")
        self.assertEqual(plan["steps"][0]["role"], "examiner")
        self.assertEqual(plan["steps"][0]["stance"], "")

    def test_steps_have_index_and_total(self):
        plan = plan_agents("debate")
        self.assertEqual([s["index"] for s in plan["steps"]], [0, 1, 2])
        self.assertEqual(plan["scenario_label"], "辩论对抗")

    def test_roles_match_steps(self):
        for key in SCENARIOS:
            plan = plan_agents(key)
            self.assertEqual(plan["roles"], [s["role"] for s in plan["steps"]])


if __name__ == "__main__":
    unittest.main()
