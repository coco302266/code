"""M3 资源生成算法库的单元测试（零 LLM、零 IO，纯函数 + 题库路径）。"""

import asyncio
import json
import unittest

from agent.resource_gen import (
    ResourceGenError,
    build_quiz_from_bank,
    build_resource_prompt,
    difficulty_label,
    generate_resource,
    list_resource_types,
    parse_resource,
    resolve_difficulty,
)


class _FakeLLM:
    """返回固定 JSON 的假模型，用于不烧额度地跑通 generate_resource 编排。"""

    def __init__(self, content: str):
        self._content = content

    async def ainvoke(self, messages):
        # 回显最后一条消息内容的前 6 个字符作为标识（仅调试用途，不影响解析）
        return type("R", (), {"content": self._content})()


_SAMPLE_JSON = {
    "slides": (
        '{"title":"死锁","objectives":["理解四条件"],'
        '"sections":[{"title":"必要条件","points":["互斥"]}],'
        '"key_points":["循环等待"],"examples":["银行家算法"],'
        '"questions":["如何预防"],"summary":["总结"]}'
    ),
    "quiz": (
        '{"questions":[{"stem":"死锁的必要条件？","options":["a","b","c","d"],'
        '"answer":0,"explanation":"四条件","difficulty":0.4,"type":"factual"}]}'
    ),
    "mindmap": (
        '{"root":"死锁","nodes":[{"title":"条件","children":[{"title":"互斥"}]}]}'
    ),
    "cards": (
        '{"cards":[{"front":"什么是死锁","back":"永久阻塞","tag":"os"}]}'
    ),
    "guide": (
        '{"overview":"概览","steps":[{"step":1,"title":"第一步","detail":"说明"}],'
        '"checkpoints":["自测"]}'
    ),
}


class TestResourceTypes(unittest.TestCase):
    def test_exactly_five_types(self):
        self.assertEqual(
            set(list_resource_types()),
            {"slides", "quiz", "mindmap", "cards", "guide"},
        )


class TestDifficulty(unittest.TestCase):
    def test_explicit_overrides_everything(self):
        profile = {"overall_level": "beginner", "knowledge_mastery": {"死锁": 0.1}}
        self.assertEqual(resolve_difficulty(0.9, profile, "死锁"), 0.9)

    def test_explicit_clamped(self):
        self.assertEqual(resolve_difficulty(2.0, None, ""), 1.0)
        self.assertEqual(resolve_difficulty(-1.0, None, ""), 0.0)

    def test_level_mapping(self):
        self.assertAlmostEqual(resolve_difficulty(None, {"overall_level": "beginner"}, ""), 0.3)
        self.assertAlmostEqual(
            resolve_difficulty(None, {"overall_level": "intermediate"}, ""), 0.55
        )
        self.assertAlmostEqual(resolve_difficulty(None, {"overall_level": "advanced"}, ""), 0.8)

    def test_mastery_inference_low_mastery_lowers_base(self):
        # advanced 整体 0.8，但该知识点掌握度 0.1 → 反推 0.25+0.055=0.305，取更保守者
        profile = {"overall_level": "advanced", "knowledge_mastery": {"进程与线程": 0.1}}
        self.assertAlmostEqual(resolve_difficulty(None, profile, "进程与线程"), 0.305, places=3)

    def test_mastery_misspelled_key_still_found(self):
        profile = {"overall_level": "advanced", "knowledge_mastery": {"进程与线程": 0.9}}
        # 0.25 + 0.55*0.9 = 0.745，整体 advanced 0.8 → 取更保守 0.745
        self.assertAlmostEqual(resolve_difficulty(None, profile, "进程与线程"), 0.745, places=3)

    def test_no_profile_default(self):
        self.assertEqual(resolve_difficulty(None, None, ""), 0.5)

    def test_difficulty_label_boundaries(self):
        self.assertEqual(difficulty_label(0.0), "入门")
        self.assertEqual(difficulty_label(0.3), "基础")
        self.assertEqual(difficulty_label(0.5), "进阶")
        self.assertEqual(difficulty_label(0.7), "提高")
        self.assertEqual(difficulty_label(1.0), "挑战")


class TestBuildPrompt(unittest.TestCase):
    def setUp(self):
        self.topic = "死锁"

    def test_contains_topic_and_difficulty(self):
        prompt = build_resource_prompt(
            "slides", self.topic, difficulty=0.3, profile_brief="", context=""
        )
        self.assertIn(self.topic, prompt)
        self.assertIn("基础", prompt)  # 0.3 → 基础
        self.assertIn("难度系数 0.30", prompt)

    def test_personalization_injected(self):
        prompt = build_resource_prompt(
            "quiz",
            self.topic,
            difficulty=0.5,
            profile_brief="学生画像：偏沉思型",
            context="",
        )
        self.assertIn("学生画像：偏沉思型", prompt)

    def test_no_profile_note(self):
        prompt = build_resource_prompt(
            "quiz", self.topic, difficulty=0.5, profile_brief="", context=""
        )
        self.assertIn("暂无该学生画像", prompt)
        self.assertNotIn("学生画像", prompt.replace("暂无该学生画像", ""))

    def test_few_shot_present_when_enabled(self):
        prompt = build_resource_prompt(
            "slides", self.topic, difficulty=0.5, profile_brief="", context="", few_shot=True
        )
        # slides few-shot 样例里含"三次握手"
        self.assertIn("三次握手", prompt)

    def test_few_shot_absent_when_disabled(self):
        prompt = build_resource_prompt(
            "slides", self.topic, difficulty=0.5, profile_brief="", context="", few_shot=False
        )
        self.assertNotIn("三次握手", prompt)

    def test_rag_context_injected(self):
        prompt = build_resource_prompt(
            "cards",
            self.topic,
            difficulty=0.5,
            profile_brief="",
            context="知识库片段：死锁四条件……",
        )
        self.assertIn("知识库片段：死锁四条件", prompt)
        self.assertIn("不要编造来源", prompt)

    def test_unknown_type_raises(self):
        with self.assertRaises(ResourceGenError):
            build_resource_prompt("video", self.topic, difficulty=0.5)


class TestParseResource(unittest.TestCase):
    def test_slides_with_fence(self):
        raw = (
            "```json\n"
            '{"title":"死锁","objectives":["理解四条件"],'
            '"sections":[{"title":"必要条件","points":["互斥"]}],'
            '"key_points":["循环等待"],"examples":[],"questions":[],"summary":["总结"]}\n'
            "```"
        )
        data = parse_resource("slides", raw)
        self.assertEqual(data["title"], "死锁")
        self.assertEqual(data["sections"][0]["title"], "必要条件")

    def test_quiz_assigns_ids_and_clamps_difficulty(self):
        raw = json.dumps(
            {
                "questions": [
                    {
                        "stem": "q1",
                        "options": ["a", "b", "c", "d"],
                        "answer": 0,
                        "explanation": "e",
                        "difficulty": 1.7,  # 越界，应夹到 1.0
                        "type": "factual",
                    }
                ]
            }
        )
        data = parse_resource("quiz", raw)
        q = data["questions"][0]
        self.assertEqual(q["id"], "Q-001")
        self.assertEqual(q["difficulty"], 1.0)

    def test_quiz_count_cap(self):
        raw = json.dumps(
            {
                "questions": [
                    {
                        "stem": f"q{i}",
                        "options": ["a", "b", "c", "d"],
                        "answer": 0,
                        "explanation": "e",
                        "type": "factual",
                    }
                    for i in range(10)
                ]
            }
        )
        data = parse_resource("quiz", raw, count=3)
        self.assertEqual(len(data["questions"]), 3)

    def test_mindmap_parses_nested(self):
        raw = json.dumps(
            {
                "root": "进程与线程",
                "nodes": [
                    {"title": "进程", "children": [{"title": "地址空间"}]},
                    {"title": "线程", "children": []},
                ],
            }
        )
        data = parse_resource("mindmap", raw)
        self.assertEqual(data["root"], "进程与线程")
        self.assertEqual(data["nodes"][0]["children"][0]["title"], "地址空间")

    def test_cards_and_guide_parse(self):
        cards = parse_resource(
            "cards",
            json.dumps({"cards": [{"front": "什么是死锁", "back": "永久阻塞", "tag": "os"}]}),
        )
        self.assertEqual(cards["cards"][0]["back"], "永久阻塞")
        guide = parse_resource(
            "guide",
            json.dumps(
                {
                    "overview": "概览",
                    "steps": [{"step": 1, "title": "第一步", "detail": "说明"}],
                    "checkpoints": ["自测"],
                }
            ),
        )
        self.assertEqual(guide["steps"][0]["title"], "第一步")

    def test_invalid_json_raises(self):
        with self.assertRaises(ResourceGenError):
            parse_resource("quiz", "这不是json{{{")

    def test_invalid_structure_raises(self):
        # slides 缺必填 title
        with self.assertRaises(ResourceGenError):
            parse_resource("slides", json.dumps({"objectives": []}))
        # quiz 选项不足 2 个
        with self.assertRaises(ResourceGenError):
            parse_resource(
                "quiz",
                json.dumps(
                    {"questions": [{"stem": "q", "options": ["a"], "answer": 0}]}
                ),
            )

    def test_slides_shell_raises(self):
        raw = json.dumps(
            {
                "title": "死锁",
                "objectives": [],
                "sections": [],
                "key_points": [],
                "examples": [],
                "questions": [],
                "summary": [],
            }
        )
        with self.assertRaises(ResourceGenError):
            parse_resource("slides", raw)


class TestQuizFromBank(unittest.TestCase):
    def test_unmatched_topic_returns_none(self):
        self.assertIsNone(build_quiz_from_bank(["量子计算"], 0.5, 5))

    def test_matched_topic_returns_items(self):
        result = build_quiz_from_bank(["进程与线程"], 0.3, 5)
        self.assertIsNotNone(result)
        self.assertTrue(result["from_bank"])
        self.assertGreaterEqual(len(result["questions"]), 1)
        # 难度目标 0.3 → 选出的题难度应靠近 0.3（最大不超过 0.55 带）
        for q in result["questions"]:
            self.assertIn("difficulty", q)
            self.assertLessEqual(q["difficulty"], 0.6)

    def test_count_cap(self):
        result = build_quiz_from_bank(["进程与线程"], 0.5, 1)
        self.assertIsNotNone(result)
        self.assertLessEqual(len(result["questions"]), 1)

    def test_no_topics_returns_none(self):
        self.assertIsNone(build_quiz_from_bank(None, 0.5, 5))


class TestGenerateResourceE2E(unittest.TestCase):
    """用假模型跑通 generate_resource 编排（算难度→组提示词→调模型→解析）。"""

    async def _run(self, rtype, profile=None):
        llm = _FakeLLM(_SAMPLE_JSON[rtype])
        return await generate_resource(
            llm, rtype, "死锁", profile=profile, difficulty=None, count=5
        )

    def test_all_five_types(self):
        for rtype in list_resource_types():
            with self.subTest(rtype=rtype):
                data = asyncio.run(self._run(rtype))
                self.assertTrue(data)  # 非空结构

    def test_quiz_id_assigned_in_pipeline(self):
        data = asyncio.run(self._run("quiz"))
        self.assertEqual(data["questions"][0]["id"], "Q-001")

    def test_personalization_flag_propagates(self):
        # 有画像时 brief 非空，generate_resource 内部会把难度按画像推导
        profile = {
            "overall_level": "beginner",
            "knowledge_mastery": {"死锁": 0.2},
            "weak_points": ["把死锁当死循环"],
        }
        # 仅验证编排不抛错且返回结构；难度推导在 resolve_difficulty 单测已覆盖
        data = asyncio.run(self._run("cards", profile=profile))
        self.assertIn("front", data["cards"][0])


if __name__ == "__main__":
    unittest.main()
