import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.personalize import (
    apply_profile,
    build_profile_brief,
    has_signal,
    normalize_topic,
    preview_prompt,
    suggest_start_level,
)


def profile(
    level="beginner",
    mastery=None,
    weak_points=None,
    preference="暂无明显偏好，按标准讲法即可",
):
    return {
        "overall_level": level,
        "knowledge_mastery": mastery or {},
        "weak_points": weak_points or [],
        "learning_preference": preference,
    }


class ColdStartTests(unittest.TestCase):
    """冷启动必须完全退化成 T5 之前的提示词，否则新学生会被凭空贴标签。"""

    def test_no_profile_is_no_op(self):
        self.assertEqual(build_profile_brief(None), "")
        self.assertEqual(apply_profile("规则", None)[0], "规则")
        self.assertFalse(apply_profile("规则", None)[1])

    def test_empty_profile_is_not_a_signal(self):
        """get_or_create 造出来的空画像不算数据。"""
        self.assertFalse(has_signal(profile()))
        self.assertEqual(build_profile_brief(profile()), "")

    def test_only_default_preference_is_not_a_signal(self):
        self.assertFalse(has_signal(profile(preference="暂无明显偏好，按标准讲法即可")))

    def test_any_real_data_counts(self):
        self.assertTrue(has_signal(profile(mastery={"死锁": 0.2})))
        self.assertTrue(has_signal(profile(weak_points=["把死锁当死循环"])))
        self.assertTrue(has_signal(profile(preference="需要把原理展开才听得懂")))


class BriefTests(unittest.TestCase):
    def test_level_changes_wording(self):
        beginner = build_profile_brief(profile("beginner", {"死锁": 0.5}))
        advanced = build_profile_brief(profile("advanced", {"死锁": 0.5}))
        self.assertNotEqual(beginner, advanced)
        self.assertIn("不用铺垫基础概念", advanced)
        self.assertIn("先给结论", beginner)

    def test_weak_topic_gets_relearned_from_scratch(self):
        brief = build_profile_brief(profile("beginner", {"死锁": 0.1}), "死锁")
        self.assertIn("完全没学过来讲", brief)

    def test_well_known_topic_skips_basics(self):
        brief = build_profile_brief(profile("advanced", {"死锁": 0.9}), "死锁")
        self.assertIn("不必从头铺垫", brief)

    def test_mastery_lookup_is_normalized(self):
        """抽取出的 topic 带空格大小写，画像里的键是归一过的。"""
        brief = build_profile_brief(profile("advanced", {"tcp三次握手": 0.9}), "TCP 三次握手")
        self.assertIn("不必从头铺垫", brief)

    def test_unknown_topic_says_no_history(self):
        brief = build_profile_brief(profile("advanced", {"死锁": 0.9}), "虚拟内存")
        self.assertIn("没有历史记录", brief)

    def test_weak_points_are_capped(self):
        points = [f"误解{i}" for i in range(10)]
        brief = build_profile_brief(profile("beginner", {"死锁": 0.2}, points))
        self.assertIn("误解9", brief)
        self.assertNotIn("误解0", brief)

    def test_preference_is_included(self):
        brief = build_profile_brief(
            profile("beginner", {"死锁": 0.2}, preference="需要把原理展开才听得懂")
        )
        self.assertIn("需要把原理展开才听得懂", brief)

    def test_apply_keeps_original_rules(self):
        merged, ok = apply_profile("硬规则", profile("advanced", {"死锁": 0.9}), "死锁")
        self.assertTrue(ok)
        self.assertTrue(merged.startswith("硬规则"))
        self.assertIn("学生画像", merged)


class StartLevelTests(unittest.TestCase):
    def test_no_history_starts_at_conclusion(self):
        self.assertEqual(suggest_start_level(profile("advanced", {"死锁": 0.9}), "虚拟内存"), 0)

    def test_well_known_topic_skips_to_standard_answer(self):
        self.assertEqual(suggest_start_level(profile("advanced", {"死锁": 0.9}), "死锁"), 1)

    def test_weak_topic_stays_at_conclusion(self):
        self.assertEqual(suggest_start_level(profile("advanced", {"死锁": 0.2}), "死锁"), 0)

    def test_overall_level_alone_never_skips(self):
        """整体强不代表这个知识点强——空 mastery 的 advanced 也得从结论讲起。"""
        self.assertEqual(suggest_start_level(profile("advanced"), "死锁"), 0)

    def test_topic_matching_is_normalized(self):
        self.assertEqual(
            suggest_start_level(profile("advanced", {"死锁": 0.9}), " 死锁 "), 1
        )


class PreviewTests(unittest.TestCase):
    def test_preview_is_deterministic(self):
        p = profile("advanced", {"死锁": 0.9})
        self.assertEqual(preview_prompt(p, "死锁"), preview_prompt(p, "死锁"))

    def test_preview_reports_effective_level(self):
        p = profile("advanced", {"死锁": 0.9})
        out = preview_prompt(p, "死锁", 0, {0: "结论版", 1: "标准答案版"})
        self.assertEqual(out["start_level_suggested"], 1)
        self.assertEqual(out["effective_hint_level"], 1)
        self.assertEqual(out["strategy"], "标准答案版")
        self.assertTrue(out["personalized"])

    def test_preview_of_new_student_is_not_personalized(self):
        out = preview_prompt(None, "死锁", 0, {0: "结论版"})
        self.assertFalse(out["personalized"])
        self.assertNotIn("学生画像", out["profile_brief"])


class NormalizeTests(unittest.TestCase):
    def test_strips_spaces_and_case(self):
        self.assertEqual(normalize_topic(" TCP 三次握手 "), "tcp三次握手")


if __name__ == "__main__":
    unittest.main()
