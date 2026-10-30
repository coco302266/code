"""教师备课资源生成（T7）。

职责：教师给一个主题 → 检索八股知识库 → 产出一份可直接讲课用的大纲。

设计取舍：
1. **结构化输出靠 Pydantic 校验，不靠提示词自觉**。跟 graph.py 的
   AssessmentResult 同一套路：模型返回 JSON，解析失败就是失败，
   不做「尽力提取」——半个大纲比报错更危险，教师会拿着缺页的讲义去上课。
2. **大纲内容与 PPT 渲染分开**（`resource_pptx.py`）。结构变了不用碰
   排版代码，排版改了不用碰提示词。
3. **知识库是主要依据**。教师备课最怕的就是模型凭印象讲错，所以检索
   片段是硬要求；资料没覆盖时才允许用模型自己的知识补，且提示里要求
   不许编造来源。
4. **受众水平只做可选参数，不自动从画像聚合**。自动聚合听起来聪明，
   但一个课堂里 beginner 和 advanced 并存时「平均」出来的水平对谁都不
   适用，宁可让教师自己指定。
"""

from __future__ import annotations

import json
from typing import List

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field, ValidationError

from .prompts import PPT_OUTLINE_PROMPT

# 章节数量上限：备课大纲不是教材，超过这个数教师也没时间在课上讲完
MAX_SECTIONS = 6
MAX_ITEMS = 8


class OutlineSection(BaseModel):
    """一个章节。

    带 points 而不是纯标题：纯标题列表渲染出来的 PPT 每一页都是空的，
    教师还得自己填要点，那就没帮上忙。
    """

    title: str = Field(min_length=1, max_length=80)
    points: List[str] = Field(default_factory=list, max_length=MAX_ITEMS)


class PptOutline(BaseModel):
    """T7.4 要求的大纲结构。"""

    title: str = Field(min_length=1, max_length=120)
    objectives: List[str] = Field(default_factory=list, max_length=MAX_ITEMS)
    sections: List[OutlineSection] = Field(default_factory=list, max_length=MAX_SECTIONS)
    key_points: List[str] = Field(default_factory=list, max_length=MAX_ITEMS)
    examples: List[str] = Field(default_factory=list, max_length=MAX_ITEMS)
    questions: List[str] = Field(default_factory=list, max_length=MAX_ITEMS)
    summary: List[str] = Field(default_factory=list, max_length=MAX_ITEMS)

    def section_titles(self) -> List[str]:
        """给前端用的扁平章节列表（计划里的 `sections: []` 那种形态）。"""
        return [s.title for s in self.sections]

    def is_shell(self) -> bool:
        """是不是空壳（只有标题，没有任何实质内容）。

        模型偶尔会返回一个结构合法但内容全空的大纲。这种东西渲染出来
        就是只有一张封面的 PPT，教师拿到手才发现白等了一轮——宁可在接口
        层直接报失败让他重试。
        """
        return not any(
            (
                self.objectives,
                self.sections,
                self.key_points,
                self.examples,
                self.questions,
                self.summary,
            )
        )


class OutlineError(Exception):
    """大纲生成失败：模型没返回合法 JSON，或结构与约定不符。"""


def parse_outline(raw: str) -> PptOutline:
    """解析模型返回。带 markdown 代码围栏也能吃。"""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].strip().lower() in {"```", "```json"}:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    try:
        return PptOutline.model_validate(json.loads(cleaned))
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise OutlineError(f"模型返回的内容不是合法大纲 JSON：{exc}") from exc


def build_outline_prompt(topic: str, context: str = "", audience: str = "") -> str:
    audience_line = (
        f"本班学生整体水平：{audience}。据此调整深度与术语门槛。\n"
        if audience
        else ""
    )
    return PPT_OUTLINE_PROMPT.format(
        topic=topic,
        audience_line=audience_line,
        context=context or "（知识库没有检索到相关资料，请用你自己的知识生成）",
    )


async def generate_outline(
    llm, topic: str, context: str = "", audience: str = ""
) -> PptOutline:
    """调一次模型生成大纲。失败抛 OutlineError，由调用方决定怎么报给前端。"""
    prompt = build_outline_prompt(topic, context, audience)
    response = await llm.ainvoke([HumanMessage(content=prompt)])
    try:
        return parse_outline(response.content)
    except ValidationError as exc:
        raise OutlineError(f"大纲结构不符合约定：{exc}") from exc


def outline_to_dict(outline: PptOutline) -> dict:
    return {
        "title": outline.title,
        "objectives": outline.objectives,
        "sections": [{"title": s.title, "points": s.points} for s in outline.sections],
        "section_titles": outline.section_titles(),
        "key_points": outline.key_points,
        "examples": outline.examples,
        "questions": outline.questions,
        "summary": outline.summary,
    }
