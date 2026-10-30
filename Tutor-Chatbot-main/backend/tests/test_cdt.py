"""M2 认知诊断（CDT）：知识图谱 / IRT 估计 / 认知风格。

这批单测守的是几类**不会报错但会静默变错**的性质：
- 图谱拓扑序写反了不是崩溃，是推荐顺序全错；
- IRT 更新写成简单加分也不是崩溃，是所有人的能力值都会刷到 1.0；
- 组卷把答案发给学生同样不报错，只是诊断数据从此作废。
这些一旦退化肉眼很难发现，只能靠断言钉住。
"""

from __future__ import annotations

import unittest

from agent.knowledge_graph import (
    learning_path,
    mastery_of,
    prerequisites,
    recommend_next,
    topics,
)
from agent.personalize import build_style_brief
from agent.pretest import (
    MIN_STYLE_SAMPLES,
    _ITEM_BANK,
    build_pretest,
    get_item,
    public_item,
    score_attempt,
    update_ability,
)


class ItemBankTests(unittest.TestCase):
    def test_every_item_has_valid_answer_index(self):
        """answer 越界会让批改永远判错，而且看起来像"学生答得差"。"""
        for item in _ITEM_BANK:
            with self.subTest(item=item["id"]):
                self.assertIsInstance(item["answer"], int)
                self.assertGreaterEqual(item["answer"], 0)
                self.assertLess(item["answer"], len(item["options"]))

    def test_options_are_unique(self):
        """重复选项意味着有两个正确答案，正确率不可解释。"""
        for item in _ITEM_BANK:
            with self.subTest(item=item["id"]):
                self.assertEqual(
                    len(set(item["options"])), len(item["options"])
                )

    def test_public_item_never_leaks_answer(self):
        item = _ITEM_BANK[0]
        exposed = public_item(item)
        self.assertNotIn("answer", exposed)
        self.assertNotIn("explanation", exposed)
        self.assertEqual(exposed["id"], item["id"])

    def test_requested_count_is_delivered(self):
        """要 8 题就得给 8 题。可选知识点被先修挡掉时不补足，
        组卷会悄悄缩水到 4 题，诊断精度下降却没人发现。"""
        self.assertEqual(build_pretest(limit=8)["count"], 8)
        self.assertEqual(build_pretest(limit=10)["count"], 10)

    def test_blocked_prerequisites_do_not_shrink_the_paper(self):
        """先修挡掉部分知识点时，要补足其它够得着的知识点，而不是少给题。"""
        mastery = {"进程与线程": 0.2, "死锁": 0.9}
        paper = build_pretest(mastery, limit=8)
        self.assertEqual(paper["count"], 8)
        # 先修「进程与线程」已测且只有 0.2，其上层知识点不该被拉进来考试
        self.assertNotIn("进程调度算法", paper["topics"])
        self.assertNotIn("死锁", paper["topics"])
        # 但也不能因此缩水：可以把没测过的基础链路放进同一张卷子里
        self.assertIn("进程与线程", paper["topics"])
        self.assertIn("TCP/IP 分层模型与 ARP / DNS", paper["topics"])

    def test_explicit_topics_are_respected(self):
        paper = build_pretest(topics=["死锁"], limit=2)
        self.assertEqual(set(paper["topics"]), {"死锁"})
        self.assertEqual([i["id"] for i in paper["items"]],
                         ["OS-DEAD-1", "OS-DEAD-2"])

    def test_build_pretest_does_not_leak(self):
        paper = build_pretest(limit=8)
        for item in paper["items"]:
            self.assertNotIn("answer", item)
            self.assertNotIn("explanation", item)


class KnowledgeGraphTests(unittest.TestCase):
    def test_topological_order_holds(self):
        """任何知识点的先修都必须排在它前面，否则推荐顺序是错的。"""
        order = list(topics())
        for topic in order:
            for parent in prerequisites(topic):
                self.assertLess(
                    order.index(parent),
                    order.index(topic),
                    f"{parent} 必须排在 {topic} 之前",
                )

    def test_transitive_prerequisite_chain(self):
        self.assertEqual(
            learning_path("HTTPS 与 TLS 握手"),
            [
                "TCP/IP 分层模型与 ARP / DNS",
                "TCP 与 UDP",
                "TCP 三次握手与四次挥手",
                "HTTPS 与 TLS 握手",
            ],
        )

    def test_direct_prerequisites_only(self):
        self.assertEqual(
            prerequisites("TCP 三次握手与四次挥手", transitive=False),
            ["TCP 与 UDP"],
        )

    def test_mastery_lookup_is_case_and_space_insensitive(self):
        mastery = {"TCP/IP 分层模型与 ARP / DNS": 0.8}
        self.assertEqual(mastery_of(mastery, "tcp/ip分层模型与arp/dns"), 0.8)
        self.assertIsNone(mastery_of({}, "死锁"))

    def test_weak_prerequisite_blocks_dependent_topic(self):
        """基础没到阈值时，不该推荐上层知识点——推了也学不会。"""
        mastery = {"进程与线程": 0.2}
        recommended = {r["topic"] for r in recommend_next(mastery, limit=8)}
        self.assertNotIn("死锁", recommended)
        self.assertIn("进程与线程", recommended)

    def test_cold_start_recommends_foundations(self):
        recommended = {r["topic"] for r in recommend_next({}, limit=8)}
        self.assertIn("进程与线程", recommended)
        self.assertIn("TCP/IP 分层模型与 ARP / DNS", recommended)


class IrtTests(unittest.TestCase):
    def test_expected_correct_barely_moves(self):
        """θ=0.8 的人答对 d=0.3 的题是预料之中，几乎不该涨分。"""
        theta, _ = update_ability(0.8, 0.3, True)
        self.assertLess(theta - 0.8, 0.05)

    def test_unexpected_error_costs_more_than_expected_success_gains(self):
        """答错已知题目的惩罚必须远大于答对同一题的奖励。"""
        gain = update_ability(0.8, 0.3, True)[0] - 0.8
        loss = 0.8 - update_ability(0.8, 0.3, False)[0]
        self.assertGreater(loss, gain * 2)

    def test_unexpected_success_jumps(self):
        """θ=0.2 的人答对 d=0.8 的难题是强信号，必须明显上调。"""
        theta, _ = update_ability(0.2, 0.8, True)
        self.assertGreater(theta - 0.2, 0.2)

    def test_ability_stays_inside_bounds(self):
        theta = 0.05
        for _ in range(20):
            theta, _ = update_ability(theta, 0.99, False)
        self.assertGreaterEqual(theta, 0.0)
        self.assertLessEqual(theta, 1.0)

    def test_all_correct_beats_all_wrong_on_every_topic(self):
        paper = build_pretest(limit=8)["items"]
        answers_right, answers_wrong = [], []
        for item in paper:
            answer = get_item(item["id"])["answer"]
            answers_right.append({"item_id": item["id"], "choice": answer})
            answers_wrong.append(
                {"item_id": item["id"], "choice": (answer + 1) % len(item["options"])}
            )

        right = score_attempt(answers_right)["mastery"]
        wrong = score_attempt(answers_wrong)["mastery"]
        self.assertTrue(right)
        for key, value in right.items():
            self.assertGreater(value, wrong[key])

    def test_mastery_keys_are_normalized(self):
        """键必须归一，否则 personalize 按归一键查，画像等于没写进去。"""
        paper = build_pretest(limit=8)["items"]
        answers = [
            {"item_id": i["id"], "choice": get_item(i["id"])["answer"]} for i in paper
        ]
        mastery = score_attempt(answers)["mastery"]
        for key in mastery:
            self.assertEqual(key, "".join(key.split()).lower())

    def test_invalid_item_ids_are_reported_not_silently_dropped(self):
        result = score_attempt(
            [{"item_id": "NO-SUCH-ID", "choice": 0}]
        )
        self.assertEqual(result["graded"], 0)
        self.assertEqual(result["invalid_items"], ["NO-SUCH-ID"])
        self.assertEqual(result["accuracy"], 0.0)

    def test_non_integer_choice_counts_as_wrong_not_error(self):
        item = _ITEM_BANK[0]
        result = score_attempt([{"item_id": item["id"], "choice": None}])
        self.assertEqual(result["graded"], 1)
        self.assertEqual(result["correct"], 0)


class CognitiveStyleTests(unittest.TestCase):
    def test_insufficient_samples_means_not_ready(self):
        result = score_attempt(
            [{"item_id": _ITEM_BANK[i]["id"], "choice": 0} for i in range(2)]
        )
        style = result["cognitive_style"]
        self.assertFalse(style["ready"])
        self.assertLess(result["graded"], MIN_STYLE_SAMPLES)

    def test_factual_right_conceptual_wrong_is_sensing(self):
        answers = []
        for item in _ITEM_BANK:
            # factual 全答对，conceptual 全答错 —— 擅长事实、不擅长抽象
            correct = item["type"] == "factual"
            choice = item["answer"] if correct else (item["answer"] + 1) % 4
            answers.append({"item_id": item["id"], "choice": choice})

        style = score_attempt(answers)["cognitive_style"]
        self.assertTrue(style["ready"])
        dimensions = {d["key"]: d for d in style["dimensions"]}
        self.assertEqual(dimensions["sensing_intuitive"]["pole"], "sensing")

    def test_deliberate_answers_look_reflective(self):
        answers = [
            {
                "item_id": item["id"],
                "choice": item["answer"],
                "elapsed_ms": 12000,
                "hint_requested": True,
            }
            for item in _ITEM_BANK
        ]
        style = score_attempt(answers)["cognitive_style"]
        dimensions = {d["key"]: d for d in style["dimensions"]}
        self.assertEqual(dimensions["reflective_active"]["pole"], "reflective")


class StyleInjectionTests(unittest.TestCase):
    def _profile(self, style):
        return {
            "user_id": 1,
            "classroom_id": 1,
            "overall_level": "beginner",
            "learning_preference": "",
            "knowledge_mastery": {"死锁": 0.6},
            "weak_points": [],
            "cognitive_style": style,
        }

    def _style(self, **overrides):
        base = {
            "ready": True,
            "confidence": 1.0,
            "dimensions": [
                {"key": "reflective_active", "pole": "reflective", "value": 0.9},
                {"key": "sensing_intuitive", "pole": "sensing", "value": 0.1},
            ],
        }
        base.update(overrides)
        return base

    def test_guidance_is_injected_when_confident(self):
        brief = build_style_brief(self._profile(self._style()))
        self.assertIn("认知风格", brief)
        self.assertIn("沉思型", brief)
        self.assertIn("感悟型", brief)

    def test_low_confidence_is_ignored(self):
        """置信度不够就当作没有结论——拿噪声指挥讲法比不指导更糟。"""
        brief = build_style_brief(self._profile(self._style(confidence=0.3)))
        self.assertEqual(brief, "")

    def test_balanced_dimension_yields_no_guidance(self):
        style = self._style(
            dimensions=[
                {"key": "reflective_active", "pole": "balanced", "value": 0.5},
                {"key": "sensing_intuitive", "pole": "balanced", "value": 0.5},
            ]
        )
        self.assertEqual(build_style_brief(self._profile(style)), "")

    def test_legacy_profile_without_style_still_works(self):
        profile = self._profile(None)
        self.assertEqual(build_style_brief(profile), "")
        profile_missing_key = dict(profile)
        del profile_missing_key["cognitive_style"]
        self.assertEqual(build_style_brief(profile_missing_key), "")


if __name__ == "__main__":
    unittest.main()
