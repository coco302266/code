"""eval/grounding_eval.py 纯函数部分的单元测试（L3 生成层评测）。

裁判调用没法进单测（要花钱花时间），所以**所有判定逻辑都收敛成纯函数**：
投票、中位数、空资料声明匹配、JSON 提取、门禁判定。LLM 只负责吐 JSON，
解析之后的每一步都是确定性代码——裁判抖动只影响输入，不影响计算。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.grounding_eval import (  # noqa: E402
    empty_declared,
    extract_json,
    gold_reference,
    judge_score,
    judge_utilization,
    majority_verdict,
    median_ratio,
)
from eval.e2e_runner import evaluate_gate  # noqa: E402


class _FakeDoc:
    """最小 Document 替身：judge_utilization 只碰 metadata.get('topic') 与 page_content。"""

    def __init__(self, topic: str, content: str = "正文"):
        self.metadata = {"topic": topic}
        self.page_content = content


class _FakeJudge:
    """按脚本吐 JSON 的假裁判：每票一个预设回答，用完抛异常（模拟废票）。"""

    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.calls = 0

    def invoke(self, messages):  # noqa: D102 - 与 LangChain 同签名即可
        if self.calls >= len(self.replies):
            raise RuntimeError("no more scripted replies")
        payload = self.replies[self.calls]
        self.calls += 1

        class _Reply:
            content = payload

        return _Reply()


class TestJudgeUtilization(unittest.TestCase):
    """资料利用率（L3 维度 3 / L4 复用同一函数，XB-16）。

    口径要点：多票取并集、越界编号丢弃、空列表是合法票、判不出就是 None。
    """

    def _docs(self, n: int = 4) -> list:
        return [_FakeDoc("死锁", "正文 %d" % i) for i in range(n)]

    def test_union_across_votes(self):
        """两票分别判 [1] 与 [3] → 并集 2/4 = 0.5（对「用过」宽容）。"""
        judge = _FakeJudge(
            [
                '{"used_indices": [1], "evidence": "用了定义"}',
                '{"used_indices": [3], "evidence": "用了步骤"}',
            ]
        )
        got = judge_utilization(judge, self._docs(4), "死锁有四个必要条件", votes=2)
        self.assertEqual(got["material_utilization"], 0.5)
        self.assertEqual(got["used_indices"], [1, 3])
        self.assertEqual(got["utilization_votes"], 2)

    def test_out_of_range_index_dropped(self):
        """裁判报越界编号（> len(docs)）直接丢弃，不能污染分母分子。"""
        judge = _FakeJudge(['{"used_indices": [1, 9], "evidence": "x"}'])
        got = judge_utilization(judge, self._docs(4), "答案", votes=1)
        self.assertEqual(got["used_indices"], [1])
        self.assertEqual(got["material_utilization"], 0.25)

    def test_empty_list_is_valid_vote(self):
        """空列表是合法票（一条都没用），不是废票——否则利用率会被系统性高估。"""
        judge = _FakeJudge(['{"used_indices": [], "evidence": "全靠自有知识"}'])
        got = judge_utilization(judge, self._docs(4), "答案", votes=1)
        self.assertEqual(got["material_utilization"], 0.0)
        self.assertEqual(got["utilization_votes"], 1)

    def test_no_docs_is_none(self):
        """docs 为空 → None（不是 0）：库外题没有可判对象，填 0 会把「没判」伪装成「没用」。"""
        got = judge_utilization(_FakeJudge([]), [], "答案", votes=3)
        self.assertIsNone(got["material_utilization"])
        self.assertEqual(got["used_indices"], [])
        self.assertEqual(got["utilization_votes"], 0)

    def test_all_votes_failed_is_none(self):
        """三票全废票 → None，不许拿 0 顶替。"""
        judge = _FakeJudge(["无法解析", "也没有 JSON"])
        got = judge_utilization(judge, self._docs(4), "答案", votes=2)
        self.assertIsNone(got["material_utilization"])

    def test_empty_answer_is_none(self):
        """空答案不判（生产不会有，但 /chat 出错时会有，别把 None 当 0 报）。"""
        got = judge_utilization(_FakeJudge([]), self._docs(4), "", votes=1)
        self.assertIsNone(got["material_utilization"])


class TestGoldReference(unittest.TestCase):
    """答案正确率的参考答案组装（2026-10-28）。

    锁的是两条实测踩出来的坑：
    1. gain=0 的片**不能**当参考答案（那标的就是无关片）；
    2. 取不到正文时退回知识点标签，但要在报告里如实标注锚点退化
       ——只用标签时裁判一律判 1.0，那个数不能当真。
    """

    def test_only_positive_gain_chunks_are_used(self):
        gold = [["a.md", 0, 1.0], ["a.md", 100, 0.0], ["a.md", 200, 0.5]]
        texts = ["必要条件有四个", "无关小节", "银行家算法"]
        ref = gold_reference(gold, texts)
        self.assertIn("必要条件有四个", ref)
        self.assertIn("银行家算法", ref)
        self.assertNotIn("无关小节", ref)

    def test_missing_text_is_skipped(self):
        """金标准与库不同源时正文取空串：宁可少给参考，也不能拿空串占位。"""
        ref = gold_reference([["a.md", 0, 1.0]], [""])
        self.assertEqual(ref, "")

    def test_truncated_to_limit(self):
        ref = gold_reference([["a.md", 0, 1.0]], ["很长的正文" * 200], limit=50)
        self.assertLessEqual(len(ref), 50)

    def test_empty_gold_is_empty_reference(self):
        self.assertEqual(gold_reference([], ["正文"]), "")


class TestJudgeScore(unittest.TestCase):
    """分档型维度（答案相关性 / 答案正确率）的裁判聚合，2026-10-28 新增。

    与资料利用率的聚合规则**故意不同**，别照抄：
    - 利用率取并集（对"用过"宽容）；
    - 分档分取中位数（0.7 这种不落档的值直接作废票，不许进均值）。

    另一条必锁的语义：**0 分是合法值**。任何 `if score` 的判断都会把
    "判了 0 分（完全答错）"当成"没判"，而这恰恰是最不能丢的一票。
    """

    PROMPT = "{question}||{reference}||{answer}"

    def test_median_of_three_levels(self):
        judge = _FakeJudge(
            [
                '{"score": 1, "evidence": "紧扣问题"}',
                '{"score": 0.5, "evidence": "部分跑题"}',
                '{"score": 1, "evidence": "核心答到了"}',
            ]
        )
        got = judge_score(judge, self.PROMPT, "死锁是什么", "答案", votes=3)
        self.assertEqual(got["score"], 1.0)
        self.assertEqual(got["votes"], 3)

    def test_off_level_vote_is_dropped(self):
        """不落档的 0.7 作废票——三档制的意义就是不给这种细粒度值存活空间。"""
        judge = _FakeJudge(
            [
                '{"score": 0.7, "evidence": "裁判自己造了个档位"}',
                '{"score": 0.5, "evidence": "半对"}',
            ]
        )
        got = judge_score(judge, self.PROMPT, "问题", "答案", votes=2)
        self.assertEqual(got["score"], 0.5)
        self.assertEqual(got["votes"], 1)

    def test_zero_is_a_real_score_not_missing(self):
        """0 分必须被当成"判了 0 分"，不能退化成 None。"""
        judge = _FakeJudge(['{"score": 0, "evidence": "答非所问"}'])
        got = judge_score(judge, self.PROMPT, "问题", "答案", votes=1)
        self.assertEqual(got["score"], 0.0)
        self.assertIsNotNone(got["score"])

    def test_all_votes_failed_is_none(self):
        judge = _FakeJudge(["无 JSON", '{"score": "abc"}'])
        got = judge_score(judge, self.PROMPT, "问题", "答案", votes=2)
        self.assertIsNone(got["score"])
        self.assertEqual(got["votes"], 0)

    def test_empty_answer_is_none(self):
        """空答案不判（/chat 出错时会出现，别把 None 当 0 分报出去）。"""
        got = judge_score(_FakeJudge([]), self.PROMPT, "问题", "", votes=1)
        self.assertIsNone(got["score"])

    def test_reference_is_passed_into_prompt(self):
        """参考答案要点必须真的进 prompt——漏传会让裁判失去判定锚点。"""
        captured: list[str] = []

        class _Spy:
            def invoke(self, messages):
                captured.append(messages[0].content)

                class _Reply:
                    content = '{"score": 1, "evidence": "x"}'

                return _Reply()

        judge_score(_Spy(), self.PROMPT, "死锁是什么", "答案", votes=1, reference="死锁、银行家算法")
        self.assertIn("死锁、银行家算法", captured[0])
        self.assertIn("死锁是什么", captured[0])


class TestEmptyDeclared(unittest.TestCase):
    """空资料声明：关键词匹配（提案 §2.2 定死的口径），不许用 LLM。"""

    def test_declared(self):
        for text in (
            "这个问题暂无资料，我用自己的知识简单说说。",
            "知识库里没有找到相关资料。",
            "抱歉，当前没有相关资料覆盖 Redis 持久化。",
        ):
            self.assertTrue(empty_declared(text), text)

    def test_not_declared(self):
        self.assertFalse(empty_declared("死锁的四个必要条件是互斥、持有并等待……"))

    def test_empty_answer(self):
        self.assertFalse(empty_declared(""))


class TestExtractJson(unittest.TestCase):
    def test_plain_json(self):
        self.assertEqual(extract_json('{"verdict": "一致"}'), {"verdict": "一致"})

    def test_json_in_code_fence(self):
        text = '好的，结果如下：\n```json\n{"verdict": "矛盾"}\n```'
        self.assertEqual(extract_json(text), {"verdict": "矛盾"})

    def test_json_with_surrounding_text(self):
        text = '判定如下 {"verdict": "一致", "evidence": "见第2条"} 请查收'
        self.assertEqual(extract_json(text), {"verdict": "一致", "evidence": "见第2条"})

    def test_no_json_returns_none(self):
        self.assertIsNone(extract_json("无法解析"))


class TestMajorityVerdict(unittest.TestCase):
    def test_majority_wins(self):
        self.assertEqual(majority_verdict(["一致", "一致", "矛盾"]), "一致")

    def test_tie_takes_conservative(self):
        """2:2 平票时取保守侧（矛盾），不装作没看见。"""
        self.assertEqual(majority_verdict(["一致", "矛盾", "一致", "矛盾"]), "矛盾")

    def test_empty_is_unrelated(self):
        self.assertEqual(majority_verdict([]), "无关")

    def test_all_votes_failed_shows_in_order(self):
        self.assertEqual(majority_verdict(["矛盾"]), "矛盾")


class TestMedianRatio(unittest.TestCase):
    def test_median_of_three(self):
        self.assertEqual(median_ratio([0.0, 0.2, 0.5]), 0.2)

    def test_median_of_two_averages(self):
        self.assertEqual(median_ratio([0.0, 0.2]), 0.1)

    def test_empty_is_none(self):
        self.assertIsNone(median_ratio([]))


class TestEvaluateGate(unittest.TestCase):
    """门禁只判「可判的」：L2 提升度、L1 p95、L3 幻觉率。

    L1 的 recall/precision 不设硬门禁——topic 派生金标准的分母是主题切片全集
    （min 6 / 中位 10），recall 天然封顶在 k/N，拿提案写的 0.85 去卡会永久红，
    那是口径错配，不是检索缺陷。报告里照常给值，门禁不碰它。
    """

    def test_all_pass(self):
        report = {
            "layers": {
                "L1": {"p95_secs": 2.0},
                "L2": {"uplift": 0.03},
                # XB-14 后声明率不再硬门禁，这里给 1.0 只示意信息性上报
                "L3": {"hallucination_rate": 0.08, "empty_source_declaration": 1.0},
            }
        }
        ok, failures = evaluate_gate(report)
        self.assertTrue(ok)
        self.assertEqual(failures, [])

    def test_low_declaration_no_longer_fails(self):
        """XB-14（2026-10-24 用户决策）：空资料不再要求声明，声明率降为信息性指标。

        旧硬门禁（必须 = 1.0）已移除——声明率低于 1.0 不许再判失败，
        否则本轮产品决策会让 CI 永久红。
        """
        report = {"layers": {"L3": {"hallucination_rate": 0.08, "empty_source_declaration": 0.0}}}
        ok, failures = evaluate_gate(report)
        self.assertTrue(ok)
        self.assertEqual(failures, [])

    def test_uplift_nonpositive_fails(self):
        report = {"layers": {"L2": {"uplift": 0.0}}}
        ok, failures = evaluate_gate(report)
        self.assertFalse(ok)
        self.assertTrue(any("提升度" in f for f in failures))

    def test_hallucination_over_gate_fails(self):
        report = {"layers": {"L3": {"hallucination_rate": 0.2}}}
        ok, failures = evaluate_gate(report)
        self.assertFalse(ok)
        self.assertTrue(any("幻觉" in f for f in failures))

    def test_p95_over_gate_fails(self):
        report = {"layers": {"L1": {"p95_secs": 3.5}}}
        ok, failures = evaluate_gate(report)
        self.assertFalse(ok)
        self.assertTrue(any("p95" in f for f in failures))

    def test_missing_layer_is_not_a_pass(self):
        """层缺失（没跑）不算通过——空报告不该绿灯。"""
        ok, failures = evaluate_gate({"layers": {}})
        self.assertFalse(ok)
        self.assertTrue(failures)

    def test_unrun_layer_is_none_not_placeholder(self):
        """回归：只跑 L1/L2 时 L3 必须是 None。

        曾出过这样的 bug：没跑的层被填成 {"aggregate": None}（非 None 的占位
        dict），门禁误判"有数据"，取 aggregate=None 后在 .get 上崩溃。
        """
        report = {
            "layers": {
                "L1": {"p95_secs": 2.0},
                "L2": {"uplift": 0.03},
                "L3": None,
            }
        }
        ok, failures = evaluate_gate(report)
        self.assertTrue(ok)
        self.assertEqual(failures, [])

    def test_l3_aggregate_none_does_not_crash(self):
        """L3 占位形态（aggregate=None）也不许崩，只能报缺失。"""
        report = {
            "layers": {
                "L1": {"p95_secs": 2.0},
                "L2": {"uplift": 0.03},
                "L3": {"aggregate": None, "per_case": None},
            }
        }
        ok, failures = evaluate_gate(report)
        self.assertFalse(ok)
        self.assertTrue(any("缺失" in f for f in failures))

    def test_none_metric_is_not_a_pass(self):
        report = {"layers": {"L3": {"hallucination_rate": None}}}
        ok, failures = evaluate_gate(report)
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
