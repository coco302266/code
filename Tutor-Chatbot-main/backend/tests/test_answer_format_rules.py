"""回答排版规范：禁止模型输出 HTML 标签（XB-54，2026-10-31）。

背景：聊天区是**纯文本渲染**（`whitespace-pre-wrap`，见前端 TutorChat.tsx 与
components/chat.tsx），不解析任何标签。而排版规范 `ANSWER_FORMAT_RULES` 里
曾经把「规范的 HTML 表格」列为**允许的**表格形式之一，于是模型照做——
学生看到的是一串裸露的 `<thead><tr><td>`，完全没法读（截图为证）。

本文件锁三件事：
  1. HTML 表格**不再**是允许的表格形式（防止有人把那个选项加回去）；
  2. 规范里**明确禁止**标签，且点名了常见的那几个标签名；
  3. 规范**必须到达四个角色**——它挂在 `build_role_system` 最外层，
     一旦有人把它挪进某个角色模板里，这里会立刻红。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import unittest  # noqa: E402

from agent.prompts import ANSWER_FORMAT_RULES  # noqa: E402
from agent.roles import build_role_system  # noqa: E402


class HtmlTableNotAllowedTest(unittest.TestCase):
    """HTML 只能是被禁止的对象，不能是可选的表格形式。"""

    def test_html_table_not_listed_as_allowed(self) -> None:
        # 「规范的 HTML 表格」和「用以下两种之一」是出事那版的原文，
        # 只要它们回来就说明有人把 HTML 又当成可选项了。
        self.assertNotIn("规范的 HTML 表格", ANSWER_FORMAT_RULES)
        self.assertNotIn("用以下两种之一", ANSWER_FORMAT_RULES)

    def test_tags_explicitly_forbidden(self) -> None:
        self.assertIn("禁止输出任何 HTML", ANSWER_FORMAT_RULES)

    def test_common_tag_names_named(self) -> None:
        # 点名比泛泛说「不要标签」有效得多：模型照抄标签名才知道哪些算标签。
        for tag in ("table", "thead", "tbody", "tr", "th", "td", "br"):
            with self.subTest(tag=tag):
                self.assertIn(tag, ANSWER_FORMAT_RULES)

    def test_plain_text_table_is_the_only_form(self) -> None:
        self.assertIn("纯文本", ANSWER_FORMAT_RULES)
        self.assertIn("空格对齐", ANSWER_FORMAT_RULES)

    def test_explains_why(self) -> None:
        # 说清「为什么不许」比只说「不许」更能压住模型。
        # 缺了这句，模型会以为换个写法（比如 XML 标签）就可以。
        self.assertIn("不会被渲染", ANSWER_FORMAT_RULES)


class RulesReachEveryRoleTest(unittest.TestCase):
    """排版规范挂在最外层，四个角色都得带上。"""

    def test_all_roles_carry_the_rule(self) -> None:
        for role in ("tutor", "teacher", "peer", "examiner"):
            with self.subTest(role=role):
                self.assertIn("禁止输出任何 HTML", build_role_system(role))

    def test_unknown_role_falls_back_to_tutor(self) -> None:
        # 未知角色回落到 tutor，不能因此就把排版规范弄丢。
        self.assertIn("禁止输出任何 HTML", build_role_system("nonsense"))


if __name__ == "__main__":
    unittest.main()
