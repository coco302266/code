"""把备课大纲渲染成 .pptx 文件（T7）。

只做排版，不做内容生成——内容是 `resource_agent.py` 的事。
分开的原因：改大纲结构时不必碰排版代码，调幻灯片样式时不必碰提示词。

排版刻意保守：一页一个章节、要点用项目符号、不搞图表动画。
教师拿到之后多半还要自己改，模板越花越难改。
"""

from __future__ import annotations

import datetime as dt
import os
import re
from pathlib import Path

from pptx import Presentation
from pptx.util import Pt

from .resource_agent import PptOutline

DEFAULT_OUTPUT_DIR = "generated"
# 一页最多放几条要点：再多字号就得缩到看不清
BULLET_LIMIT = 8
ILLEGAL_FILENAME = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def safe_filename(topic: str, when: dt.datetime | None = None) -> str:
    """把主题转成合法文件名，并带上时间戳避免同名覆盖。

    主题是教师自由输入的，「进程/线程」这种带斜杠的名字直接当文件名
    会在 Windows 上写出一个奇怪的目录。
    """
    when = when or dt.datetime.now()
    cleaned = ILLEGAL_FILENAME.sub("_", (topic or "").strip())[:40] or "outline"
    return f"{cleaned}_{when.strftime('%Y%m%d_%H%M%S')}.pptx"


def _bullets(slide, items: list[str]) -> None:
    """往内容占位符里写项目符号列表。"""
    if not items:
        return
    text_frame = slide.placeholders[1].text_frame
    text_frame.text = str(items[0])
    for item in items[1:BULLET_LIMIT]:
        paragraph = text_frame.add_paragraph()
        paragraph.text = str(item)
        paragraph.level = 0
    for paragraph in text_frame.paragraphs:
        for run in paragraph.runs:
            run.font.size = Pt(18)


def _content_slide(prs: Presentation, title: str, items: list[str]) -> None:
    """加一页「标题 + 要点」。要点为空就不加——空页比少一页更糟。"""
    if not items:
        return
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.placeholders[0].text = title
    _bullets(slide, items)


def build_pptx(
    outline: PptOutline,
    output_dir: str | None = None,
    subtitle: str = "",
) -> str:
    """渲染大纲，返回生成文件的绝对路径。"""
    target_dir = Path(
        output_dir
        or os.getenv("RESOURCE_OUTPUT_DIR")
        or Path(__file__).resolve().parent.parent / DEFAULT_OUTPUT_DIR
    )
    target_dir.mkdir(parents=True, exist_ok=True)

    prs = Presentation()

    cover = prs.slides.add_slide(prs.slide_layouts[0])
    cover.placeholders[0].text = outline.title
    cover.placeholders[1].text = subtitle or "AI Classroom 备课大纲"

    _content_slide(prs, "教学目标", outline.objectives)

    for index, section in enumerate(outline.sections, 1):
        _content_slide(prs, f"{index}. {section.title}", section.points)

    _content_slide(prs, "面试常考要点", outline.key_points)
    _content_slide(prs, "例子与场景", outline.examples)
    _content_slide(prs, "课堂提问", outline.questions)
    _content_slide(prs, "本讲总结", outline.summary)

    path = target_dir / safe_filename(outline.title)
    prs.save(str(path))
    return str(path)
