import datetime as dt
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.resource_agent import (
    OutlineError,
    OutlineSection,
    PptOutline,
    build_outline_prompt,
    outline_to_dict,
    parse_outline,
)
from agent.resource_pptx import safe_filename


def outline_payload(**overrides):
    payload = {
        "title": "死锁",
        "objectives": ["理解四个必要条件"],
        "sections": [{"title": "定义", "points": ["一句话结论"]}],
        "key_points": ["口诀"],
        "examples": ["两个线程互等"],
        "questions": ["为什么互斥不能破坏"],
        "summary": ["破坏任一条件即可预防"],
    }
    payload.update(overrides)
    return payload


class ParseTests(unittest.TestCase):
    def test_parses_plain_json(self):
        outline = parse_outline('{"title": "死锁", "objectives": [], "sections": [],'
                                ' "key_points": [], "examples": [], "questions": [],'
                                ' "summary": []}')
        self.assertEqual(outline.title, "死锁")

    def test_parses_fenced_json(self):
        """模型经常自作主张包一层 ```json。"""
        raw = '```json\n{"title": "TCP", "objectives": [], "sections": [],' \
              ' "key_points": [], "examples": [], "questions": [], "summary": []}\n```'
        self.assertEqual(parse_outline(raw).title, "TCP")

    def test_rejects_garbage(self):
        """解析失败必须炸出来：半份大纲比报错更坑教师。"""
        with self.assertRaises(OutlineError):
            parse_outline("这不是 JSON")

    def test_title_is_required(self):
        """列表字段允许为空（由上层判断大纲质量），但标题不能缺。"""
        with self.assertRaises(Exception):
            parse_outline('{"objectives": []}')

    def test_shell_outline_is_parsed_but_flagged(self):
        """空壳大纲解析得出来，但 is_shell() 要认出来——API 据此报 502，
        而不是让教师拿着只有封面的 PPT 去上课。"""
        shell = parse_outline('{"title": "死锁"}')
        self.assertEqual(shell.objectives, [])
        self.assertTrue(shell.is_shell())
        self.assertFalse(PptOutline.model_validate(outline_payload()).is_shell())

    def test_section_titles_are_flat(self):
        """前端要的是字符串数组，不是对象数组。"""
        outline = PptOutline.model_validate(outline_payload())
        self.assertEqual(outline.section_titles(), ["定义"])

    def test_to_dict_has_both_section_forms(self):
        result = outline_to_dict(PptOutline.model_validate(outline_payload()))
        self.assertEqual(result["section_titles"], ["定义"])
        self.assertEqual(result["sections"][0]["points"], ["一句话结论"])

    def test_empty_section_points_are_allowed(self):
        section = OutlineSection(title="定义")
        self.assertEqual(section.points, [])


class PromptTests(unittest.TestCase):
    def test_topic_is_in_prompt(self):
        self.assertIn("死锁", build_outline_prompt("死锁"))

    def test_audience_is_optional(self):
        self.assertNotIn("本班学生整体水平", build_outline_prompt("死锁"))
        self.assertIn("beginner", build_outline_prompt("死锁", audience="beginner"))

    def test_missing_context_falls_back(self):
        """没检索到资料时要明确告诉模型，不能让它以为资料为空就是没有要求。"""
        self.assertIn("没有检索到相关资料", build_outline_prompt("死锁", context=""))


class FilenameTests(unittest.TestCase):
    WHEN = dt.datetime(2026, 10, 4, 22, 0, 0)

    def test_illegal_chars_are_replaced(self):
        """主题是自由输入的，「进程/线程」这种会写出奇怪的目录。"""
        name = safe_filename("进程/线程:区别?", self.WHEN)
        self.assertNotIn("/", name)
        self.assertNotIn(":", name)
        self.assertNotIn("?", name)
        self.assertTrue(name.endswith(".pptx"))

    def test_empty_topic_still_yields_a_name(self):
        self.assertTrue(safe_filename("", self.WHEN).endswith(".pptx"))

    def test_long_topic_is_trimmed(self):
        name = safe_filename("死" * 200, self.WHEN)
        self.assertLess(len(name), 80)


if __name__ == "__main__":
    unittest.main()
