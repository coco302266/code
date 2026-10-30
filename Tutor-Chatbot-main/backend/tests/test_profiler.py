import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.profiler import (
    LEVEL_ADVANCED,
    LEVEL_BEGINNER,
    LEVEL_INTERMEDIATE,
    classify_action,
    derive_level,
    merge_weak_points,
    next_mastery,
    normalize_topic,
    summarize_preference,
)


class MasteryTests(unittest.TestCase):
    def test_new_topic_starts_at_neutral(self):
        """没测过的知识点从 0.5 起步，代表「还没测出来」而不是「半懂」。"""
        self.assertEqual(next_mastery(None, 0, False), 0.5)

    def test_resolved_pushes_mastery_up(self):
        self.assertEqual(next_mastery(0.5, 0, True), 0.8)

    def test_deep_hint_pushes_mastery_down(self):
        """讲到举例+追问还没懂，扣分最重。"""
        self.assertEqual(next_mastery(0.5, 3, False), 0.3)
        self.assertEqual(next_mastery(0.5, 2, False), 0.4)

    def test_mastery_stays_within_bounds(self):
        self.assertEqual(next_mastery(0.95, 0, True), 1.0)
        self.assertEqual(next_mastery(0.05, 3, False), 0.0)

    def test_first_question_does_not_penalize(self):
        """刚开口提问不给分也不扣分。"""
        self.assertEqual(next_mastery(0.5, 1, False), 0.5)


class LevelTests(unittest.TestCase):
    def test_empty_profile_is_beginner(self):
        self.assertEqual(derive_level({}), LEVEL_BEGINNER)

    def test_single_topic_cannot_be_advanced(self):
        """只问了一个知识点、答得再好也不足以说明整体水平。"""
        self.assertEqual(derive_level({"死锁": 0.9}), LEVEL_INTERMEDIATE)

    def test_two_strong_topics_are_advanced(self):
        self.assertEqual(
            derive_level({"死锁": 0.8, "tcp握手": 0.8}), LEVEL_ADVANCED
        )

    def test_weak_topics_are_beginner(self):
        self.assertEqual(derive_level({"死锁": 0.1, "tcp握手": 0.2}), LEVEL_BEGINNER)


class TopicNormalizationTests(unittest.TestCase):
    def test_same_topic_written_differently_merges(self):
        """模型自由抽取的 topic 不归一的话，画像里会裂成好几个键。"""
        self.assertEqual(
            normalize_topic("TCP 三次握手"),
            normalize_topic("tcp三次握手"),
        )

    def test_strips_punctuation_and_spacing(self):
        self.assertEqual(normalize_topic(" 进程与线程 "), "进程与线程")


class ActionTests(unittest.TestCase):
    def test_new_topic_is_ask_question(self):
        self.assertEqual(classify_action("死锁", "tcp握手", 0, 0, False), "ask_question")

    def test_first_message_is_ask_question(self):
        self.assertEqual(classify_action("", "死锁", 0, 0, False), "ask_question")

    def test_deeper_hint_is_request_explanation(self):
        self.assertEqual(
            classify_action("死锁", "死锁", 1, 2, False), "request_explanation"
        )

    def test_same_level_followup_is_repeat_question(self):
        self.assertEqual(
            classify_action("死锁", "死锁", 3, 3, False), "repeat_question"
        )

    def test_resolved_is_recorded_as_mastered(self):
        self.assertEqual(classify_action("死锁", "死锁", 1, 0, True), "mastered")


class WeakPointTests(unittest.TestCase):
    def test_duplicates_are_dropped(self):
        points = merge_weak_points(["以为死锁是死循环"], "以为死锁是死循环")
        self.assertEqual(points, ["以为死锁是死循环"])

    def test_empty_input_is_ignored(self):
        self.assertEqual(merge_weak_points([], ""), [])

    def test_oldest_is_dropped_when_over_limit(self):
        points = [f"薄弱点{i}" for i in range(12)]
        merged = merge_weak_points(points, "最新薄弱点")
        self.assertEqual(len(merged), 12)
        self.assertEqual(merged[-1], "最新薄弱点")
        self.assertNotIn("薄弱点0", merged)


class PreferenceTests(unittest.TestCase):
    class _Behavior:
        def __init__(self, action):
            self.action = action

    def test_no_behavior_has_no_preference(self):
        self.assertIn("暂无", summarize_preference([], {}))

    def test_mostly_explanation_requests(self):
        behaviors = [self._Behavior("request_explanation") for _ in range(4)]
        text = summarize_preference(behaviors, {"死锁": 0.3})
        self.assertIn("原理展开", text)

    def test_fast_learner(self):
        behaviors = [self._Behavior("mastered") for _ in range(3)]
        text = summarize_preference(behaviors, {"死锁": 0.8})
        self.assertIn("吸收快", text)


if __name__ == "__main__":
    unittest.main()
