"""eval/refine_gold.py 纯函数的单测（P2 细粒度标注）。

只测确定性计算：增益校验 / 多数投票 / 裁判输出解析 / 输入组装。
LLM 调用与写回 IO 不进单测（验收靠 --limit 试跑 + 人工复核报告）。
"""

import importlib
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
rg = importlib.import_module("eval.refine_gold")


class SanitizeGainTests(unittest.TestCase):
    def test_accepts_three_tiers(self):
        self.assertEqual(rg.sanitize_gain(1), 1.0)
        self.assertEqual(rg.sanitize_gain("0.5"), 0.5)
        self.assertEqual(rg.sanitize_gain(0), 0.0)

    def test_rejects_out_of_tier_and_garbage(self):
        # 裁判偶尔输出 0.7 / 文字：按废票处理，不擅自就近取档
        self.assertIsNone(rg.sanitize_gain(0.7))
        self.assertIsNone(rg.sanitize_gain("高"))
        self.assertIsNone(rg.sanitize_gain(None))


class MajorityGainTests(unittest.TestCase):
    def test_majority_wins(self):
        self.assertEqual(rg.majority_gain([1.0, 1.0, 0.0]), (1.0, False))

    def test_tie_takes_conservative_lower(self):
        # 平票取低档：金标准宁严勿松，标高会让 nDCG 虚高
        self.assertEqual(rg.majority_gain([1.0, 0.5])[0], 0.5)
        self.assertEqual(rg.majority_gain([0.5, 0.0])[0], 0.0)

    def test_tie_is_low_confidence(self):
        _, low = rg.majority_gain([1.0, 0.5])
        self.assertTrue(low)

    def test_all_invalid_votes_become_zero_and_low(self):
        self.assertEqual(rg.majority_gain([None, None, None]), (0.0, True))

    def test_invalid_vote_marks_low_confidence(self):
        _, low = rg.majority_gain([1.0, 1.0, None])
        self.assertTrue(low)


class ParseGainResponseTests(unittest.TestCase):
    def test_parses_numbered_gains(self):
        text = '{"gains": [{"index": 1, "gain": 1}, {"index": 2, "gain": 0}]}'
        self.assertEqual(rg.parse_gain_response(text, 2), [1.0, 0.0])

    def test_missing_index_stays_none(self):
        # 裁判漏标第 2 片：该位废票，不擅自补档
        text = '{"gains": [{"index": 1, "gain": 1}]}'
        self.assertEqual(rg.parse_gain_response(text, 2), [1.0, None])

    def test_out_of_range_and_duplicate_ignored(self):
        text = (
            '{"gains": [{"index": 5, "gain": 1},'
            ' {"index": 1, "gain": 0}, {"index": 1, "gain": 1}]}'
        )
        # 越界忽略；重复编号取首次出现
        self.assertEqual(rg.parse_gain_response(text, 2), [0.0, None])

    def test_no_json_returns_all_none(self):
        self.assertEqual(rg.parse_gain_response("抱歉我不会", 2), [None, None])


class BuildJudgeInputTests(unittest.TestCase):
    def test_chunks_are_numbered_from_one_and_truncated(self):
        over = "甲" * (rg.CHUNK_TEXT_LIMIT + 100)
        prompt = rg.build_judge_input("什么是死锁？", [over, "乙"])
        self.assertIn("[1] ", prompt)
        self.assertIn("[2] 乙", prompt)
        self.assertNotIn(over, prompt)  # 超上限被截断
        self.assertIn("什么是死锁？", prompt)

    def test_default_limit_covers_a_full_parent(self):
        """上限必须能装下整个注入单元。

        parent 是整节（600~900 字）。旧值 200 会让裁判只看到小节开头——
        实测「银行家算法是做什么的」整题被判全 0，而 deadlock.md 的
        「标准答案」节第 900 字处明明写着银行家算法。上限调回 200 就是复现这个错。
        """
        self.assertGreaterEqual(rg.CHUNK_TEXT_LIMIT, 1000)

    def test_parent_sized_chunk_goes_in_whole(self):
        body = "## 标准答案\n" + "死锁是多个进程互相等待资源。" * 60  # ≈800 字
        self.assertLess(len(body), rg.CHUNK_TEXT_LIMIT + 1)
        self.assertIn(body, rg.build_judge_input("死锁？", [body]))

    def test_explicit_limit_overrides_default(self):
        # 试跑时可以用更小的 limit 省 token，但不能改变默认口径
        self.assertNotIn("甲" * 300, rg.build_judge_input("q", ["甲" * 300], limit=200))


class TruncatedChunksTests(unittest.TestCase):
    """截断数要能报出来：残篇判出的 gain 偏保守，读结论时得打折。"""

    def test_counts_only_over_limit(self):
        self.assertEqual(rg.truncated_chunks(["甲" * 50, "乙" * 300]), 0)
        self.assertEqual(rg.truncated_chunks(["甲" * (rg.CHUNK_TEXT_LIMIT + 1)]), 1)

    def test_empty_and_none_not_counted(self):
        self.assertEqual(rg.truncated_chunks(["", None]), 0)


if __name__ == "__main__":
    unittest.main()
