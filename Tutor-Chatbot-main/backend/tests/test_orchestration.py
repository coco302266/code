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
    QUIZ_STEP,
    SCENARIOS,
    get_scenario,
    list_scenarios,
    normalize_scenario,
    quiz_requested,
)


class ScenarioTableTests(unittest.TestCase):
    def test_two_scenarios_defined(self):
        """2026-11-01：辩论场景下线，只剩 v1 与 group。"""
        self.assertEqual(set(SCENARIOS), {"v1", "group"})

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
        self.assertEqual(len(items), 2)
        by_key = {i["key"]: i for i in items}
        # 两个场景都只有主讲助教一个发言位；出题助手不进默认编排
        self.assertEqual(by_key["v1"]["agents"], ["tutor"])
        self.assertEqual(by_key["group"]["agents"], ["tutor"])

    def test_scenarios_do_not_include_quiz_agent(self):
        """出题助手是「@ 才说话」的角色，不该出现在任何场景的默认序列里。"""
        for key, spec in SCENARIOS.items():
            self.assertNotIn(
                QUIZ_STEP.role,
                [step.role for step in spec.steps],
                f"场景 {key} 不该默认带上出题助手",
            )

    def test_quiz_mention_detection(self):
        self.assertTrue(quiz_requested("@出题助手 来三道 TCP 的题"))
        self.assertTrue(quiz_requested("@出题"))
        self.assertTrue(quiz_requested("@QUIZ 来两道"))
        # 不带 @ 的叙述不算点名，否则「出题助手刚才说的」会被误判
        self.assertFalse(quiz_requested("出题助手刚才说的不对"))
        self.assertFalse(quiz_requested(None))
        self.assertFalse(quiz_requested(""))


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

    def test_group_is_single_tutor(self):
        """小组讨论的差别在人数，不在发言位：一轮仍只有主讲助教一个人说。"""
        plan = plan_agents("group")
        self.assertEqual(plan["roles"], ["tutor"])
        self.assertFalse(plan["degraded"])

    def test_removed_debate_falls_back_to_v1(self):
        """已下线的场景值必须回落并留痕，不能静默走老规则。"""
        plan = plan_agents("debate")
        self.assertEqual(plan["roles"], ["tutor"])
        self.assertTrue(plan["degraded"])
        self.assertIn("未知场景", plan["reason"])

    def test_group_honors_explicit_role(self):
        """两个场景现在都是单步，客户端指定角色即生效（M1 行为）。

        真正防串味的场景（多步）已经不存在，这条断言留作「若将来再加多步场景，
        必须改回由场景决定角色」的提醒。
        """
        plan = plan_agents("group", explicit_role="examiner")
        self.assertEqual(plan["roles"], ["examiner"])

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
        plan = plan_agents("group", enabled=False)
        self.assertEqual(len(plan["steps"]), 1)
        self.assertTrue(plan["degraded"])
        self.assertIn("关闭", plan["reason"])

    def test_disabled_collab_honors_explicit_role(self):
        plan = plan_agents("group", enabled=False, explicit_role="teacher")
        self.assertEqual(plan["roles"], ["teacher"])

    def test_zero_max_agents_keeps_only_first(self):
        """上限截断只对多步场景有意义。

        2026-11-01 起两个场景都是单步，plan_agents 在单步分支就 return 了，
        根本走不到上限判断 —— 所以这里**不该**标降级：硬把 degraded 置 True
        会让日志里出现「上限截断」这种从未发生的信号。
        """
        plan = plan_agents("group", max_agents=0)
        self.assertEqual(len(plan["steps"]), 1)
        self.assertFalse(plan["degraded"])

    def test_replaced_role_does_not_inherit_stance(self):
        """角色被替换后不能沿用别人的 stance，否则会出现「考官」顶着「主讲」。"""
        plan = plan_agents("group", enabled=False, explicit_role="examiner")
        self.assertEqual(plan["steps"][0]["role"], "examiner")
        self.assertEqual(plan["steps"][0]["stance"], "")

    def test_steps_have_index_and_label(self):
        plan = plan_agents("v1")
        self.assertEqual([s["index"] for s in plan["steps"]], [0])
        self.assertEqual(plan["scenario_label"], "1v1 辅导")

    # ---- 2026-11-01 新增：出题助手的两条路径 ----

    def test_quiz_only_plan(self):
        """学生 @ 出题助手：本轮只有出题助手，且不是降级。"""
        plan = plan_agents("v1", quiz_only=True)
        self.assertEqual(plan["roles"], ["peer"])
        self.assertFalse(plan["degraded"])
        self.assertEqual(plan["steps"][0]["stance"], QUIZ_STEP.stance)
        self.assertEqual(plan["steps"][0]["handoff"], QUIZ_STEP.handoff)
        self.assertEqual(plan["steps"][0]["label"], "出题助手")

    def test_quiz_only_wins_over_use_tutor(self):
        """@ 的优先级高于课堂开关：即使关掉主讲助教，@ 一次仍然是出题。"""
        plan = plan_agents("group", quiz_only=True, use_tutor=False)
        self.assertEqual(plan["roles"], ["peer"])

    def test_no_tutor_classroom_is_silent(self):
        """关掉主讲助教后，普通提问不该被出题助手代答——本轮没有 AI 发言。

        2026-11-02 回归：此前这里返回 peer，于是在「小组讨论」里关掉助教后
        发一句「花生在吗」，收到的是三道题。出题助手是「@ 才说话」的角色，
        助教不在了不代表它要自动接手。
        """
        plan = plan_agents("group", use_tutor=False)
        self.assertEqual(plan["steps"], [])
        self.assertEqual(plan["roles"], [])
        # 课堂设置决定的正常形态，不是异常兜底——记成降级会污染评估口径。
        self.assertFalse(plan["degraded"])

    def test_quiz_mention_still_works_without_tutor(self):
        """关掉助教后 @出题助手 仍然有效：那是学生主动点名，不是自动代答。"""
        plan = plan_agents("group", quiz_only=True, use_tutor=False)
        self.assertEqual(plan["roles"], ["peer"])
        self.assertEqual(plan["steps"][0]["label"], "出题助手")

    def test_tutor_enabled_keeps_normal_plan(self):
        plan = plan_agents("v1", use_tutor=True)
        self.assertEqual(plan["roles"], ["tutor"])

    def test_roles_match_steps(self):
        for key in SCENARIOS:
            plan = plan_agents(key)
            self.assertEqual(plan["roles"], [s["role"] for s in plan["steps"]])


if __name__ == "__main__":
    unittest.main()
