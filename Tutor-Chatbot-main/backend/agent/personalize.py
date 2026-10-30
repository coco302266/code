"""把学生画像翻译成提示词片段（T6）。

职责：读画像 → 产出可以直接拼进 system prompt 的中文说明。

设计取舍：
1. **和 profiler.py 分开**。profiler 负责「写画像」，这里负责「用画像」。
   两个方向硬塞进一个模块，改提示词时会误伤推导规则。
2. **纯函数，不调 LLM**。画像 → 提示片段是字符串拼装，给定同一份画像
   必须产出同一段话。这样能写单测，也能加一个不花钱的预览接口。
3. **冷启动必须完全退化为 T5 之前的行为**。新学生没有画像数据时不注入
   任何段落——宁可这一轮不个性化，也不能凭空把人当 beginner 教。
4. **画像只管「怎么讲」，hint_level 管「讲多少」**。这两件事分开才不会
   打架：hint_level 是学生现场没听懂才往上涨的负反馈信号，画像不该
   覆盖它。唯一的例外见 suggest_start_level。

关于 suggest_start_level 的克制：
整体水平高 ≠ 这个知识点强。拿 overall_level 去抬起步深度，会让一个
advanced 学生第一次接触「MySQL 索引」时直接跳过结论层，而他对这个
知识点其实一无所知。所以只有「这个知识点历史上确实掌握过」
（mastery >= 0.75）才敢省掉一句话结论那一步。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from db.models import StudentProfile

# M2：认知风格要确有把握才注入。这是硬阈值而不是建议值——
# 拿 3 道题答出来的"沉思型"去指挥整个学期的讲法，是拿噪声当结论。
from agent.pretest import MIN_CONFIDENCE

# 冷启动阈值：画像里至少得有一条实质信号才注入个性化段落
NO_SIGNAL_PREFERENCE = "暂无明显偏好，按标准讲法即可"
# 掌握度 >= 这个值，说明这个知识点他此前确实掌握了，可以省掉结论层
MASTERY_WELL_KNOWN = 0.75
# 掌握度 < 这个值，当作完全没学过来讲
MASTERY_UNFAMILIAR = 0.4
WEAK_POINT_SAMPLE = 3


def normalize_topic(topic: str) -> str:
    """与 profiler 保持同一套归一规则，否则查不到 mastery 键。"""
    return "".join((topic or "").split()).lower()


def has_signal(profile: dict | None) -> bool:
    """画像里有没有实质数据。

    get_or_create_profile 会造一个全空的 beginner 画像，那种不能算数——
    把它当真人画像用，等于把每个新学生都先入为主地当成基础薄弱。
    """
    if not profile:
        return False
    if profile.get("knowledge_mastery"):
        return True
    if profile.get("weak_points"):
        return True
    preference = (profile.get("learning_preference") or "").strip()
    return bool(preference) and preference != NO_SIGNAL_PREFERENCE


def _level_guidance(level: str) -> str:
    """整体水平 → 讲解方式。改这三条等于改整个产品的教学口径。"""
    if level == "advanced":
        return (
            "- 不用铺垫基础概念，直接进入要点和易错点。\n"
            "- 多给面试里会被追问的边界情况和方案对比（为什么不用另一种做法）。\n"
            "- 篇幅紧凑，他已经知道的部分直接略过。\n"
            "- 追问要有难度，用来验证他是不是真的吃透了。"
        )
    if level == "intermediate":
        return (
            "- 可以直接使用标准术语，不必逐个解释。\n"
            "- 按面试标准答案的结构分点给出，控制篇幅。\n"
            "- 容易混淆的地方点出来即可，不用从头铺垫。"
        )
    return (
        "- 先给结论，再解释为什么；不要一上来就讲底层机制。\n"
        "- 出现专业术语（如临界区、虚拟内存、滑动窗口）时，"
        "顺手用一句话说明它是什么。\n"
        "- 一次只讲清一个要点，不要堆砌。\n"
        "- 结尾追问要简单明确，让他能用一两句话回答。"
    )


def _mastery_note(topic: str, score: float | None) -> str:
    if score is None:
        return (
            f"- 知识点「{topic}」他没有历史记录，按上面的整体水平讲，"
            "讲完记得确认他跟不跟得上。"
        )
    if score >= MASTERY_WELL_KNOWN:
        return (
            f"- 知识点「{topic}」他此前掌握得不错（{score}）。"
            "可以直接给要点和易错点，不必从头铺垫；如果他是来复习的，"
            "重点放在面试追问上。"
        )
    if score < MASTERY_UNFAMILIAR:
        return (
            f"- 知识点「{topic}」他此前一直掌握得不好（{score}）。"
            "这次当作完全没学过来讲，不要假设他记得以前讲过的内容。"
        )
    return f"- 知识点「{topic}」他此前的掌握度是 {score}，讲完确认一次即可。"


# 认知风格 → 讲法。每条都写成「怎么做」而不是「他是什么类型」：
# 告诉模型"他是沉思型"，模型往往回一句客套认定；告诉它"先给结构再给结论"，
# 它才会真的改变输出形态。
_STYLE_GUIDANCE: dict[tuple[str, str], str] = {
    ("reflective_active", "reflective"): (
        "- 他偏沉思型：先给出整体结构和要点清单，再展开细节，"
        "让他看清脉络而不是一上来就掉进细节里。\n"
        "- 讲完别立刻追问，留出消化时间；追问时给选项式提示而不是开放式追问。"
    ),
    ("reflective_active", "active"): (
        "- 他偏主动型：喜欢边做边学，多用具体例子和反例推进，"
        "长篇铺垫会让他失去耐心。\n"
        "- 每个要点讲完就用一个小问题确认，节奏可以快一点。"
    ),
    ("sensing_intuitive", "intuitive"): (
        "- 他偏直觉型：可以直接进入模型、原理之间的联系，"
        "不必把每个术语都拆开讲，抽象概括他接得住。"
    ),
    ("sensing_intuitive", "sensing"): (
        "- 他偏感悟型：先给具体事实、步骤或实例，再往上抽象，"
        "顺序反过来他会跟不上。\n"
        "- 讲解尽量绑定具体场景（如「调这个参数会发生什么」）而不是纯概念推演。"
    ),
}


def build_style_brief(profile: dict | None) -> str:
    """认知风格 → 提示片段。没到可信度阈值就返回空串。"""
    style = (profile or {}).get("cognitive_style")
    if not isinstance(style, dict) or not style.get("ready"):
        return ""
    if float(style.get("confidence", 0)) < MIN_CONFIDENCE:
        return ""

    lines: list[str] = []
    for dimension in style.get("dimensions") or []:
        key = dimension.get("key")
        pole = dimension.get("pole")
        guidance = _STYLE_GUIDANCE.get((key, pole), "")
        if not guidance:
            # balanced（阈值区间内）不给指导：没有倾向就不该有偏向，
            # 硬塞一条会把它变成随机噪声。
            continue
        lines.append(guidance)

    if not lines:
        return ""
    return "认知风格（按这个节奏讲，不要原样告诉他）：\n" + "\n".join(lines)


def build_profile_brief(profile: dict | None, topic: str = "") -> str:
    """画像 → 提示词片段。没有实质画像时返回空串（调用方就不拼这一段）。"""
    if not has_signal(profile):
        return ""

    level = (profile or {}).get("overall_level") or "beginner"
    lines = ["学生画像（据此调整讲法，不要原样复述给他听）：", _level_guidance(level)]

    mastery = (profile or {}).get("knowledge_mastery") or {}
    if topic:
        lines.append(_mastery_note(topic, mastery.get(normalize_topic(topic))))

    weak_points = [p for p in ((profile or {}).get("weak_points") or []) if p]
    if weak_points:
        # 只取最近几条：画像里可能攒了十几条，全塞进去会把提示词撑爆，
        # 而且早年的误解很可能早就被纠正了。
        sample = weak_points[-WEAK_POINT_SAMPLE:]
        lines.append(
            "他历史上出现过以下理解偏差，本轮内容若涉及就顺手澄清"
            "（自然带过，不要生硬插入）：\n"
            + "\n".join(f"  · {p}" for p in sample)
        )

    preference = ((profile or {}).get("learning_preference") or "").strip()
    if preference and preference != NO_SIGNAL_PREFERENCE:
        lines.append(f"学习偏好：{preference}")

    # 认知风格放在偏好的位置之后：偏好描述"他习惯怎么学"，
    # 风格描述"你现在该怎么写"，后者更贴近输出，放在更靠近正文的位置。
    style_brief = build_style_brief(profile)
    if style_brief:
        lines.append(style_brief)

    # 段与段之间空一行：这几段是给模型读的，糊成一坨容易被当成一条规则。
    return "\n\n".join(lines)


def suggest_start_level(profile: dict | None, topic: str) -> int:
    """换新知识点时的起步讲解深度。

    默认 0（一句话结论）；只有这个知识点历史掌握度 >= 0.75 时才抬到 1
    （直接给标准答案）。overall_level 不参与——见文件头的取舍说明。
    """
    if not topic or not profile:
        return 0
    score = (profile.get("knowledge_mastery") or {}).get(normalize_topic(topic))
    if score is None:
        return 0
    try:
        return 1 if float(score) >= MASTERY_WELL_KNOWN else 0
    except (TypeError, ValueError):
        return 0


def load_profile(db, user_id: int | None, classroom_id: int | None) -> dict | None:
    """取画像并转成 dict。查不到返回 None——那是冷启动，不是错误。"""
    if db is None or user_id is None or classroom_id is None:
        return None
    row = (
        db.execute(
            select(StudentProfile).where(
                StudentProfile.user_id == user_id,
                StudentProfile.classroom_id == classroom_id,
            )
        )
        .scalars()
        .first()
    )
    if row is None:
        return None
    mastery = row.knowledge_mastery or {}
    return {
        "user_id": row.user_id,
        "classroom_id": row.classroom_id,
        "overall_level": row.overall_level,
        "learning_preference": row.learning_preference,
        "knowledge_mastery": mastery,
        "weak_points": row.weak_points or [],
        # M2：认知风格。personalize 只消费不推导，这里带上它
        # build_profile_brief 才能在 /chat 里真正改变讲法。
        "cognitive_style": row.cognitive_style,
    }


def apply_profile(
    system_content: str, profile: dict | None, topic: str = ""
) -> tuple[str, bool]:
    """把画像段落接进 system prompt。返回 (新提示, 是否真的个性化了)。"""
    brief = build_profile_brief(profile, topic)
    if not brief:
        return system_content, False
    return f"{system_content}\n\n{brief}\n", True


def preview_prompt(
    profile: dict | None,
    topic: str = "",
    hint_level: int = 0,
    strategies: dict[int, str] | None = None,
) -> dict[str, Any]:
    """不调 LLM 的提示词预览。

    T6.4 要对比 beginner / advanced 的讲法差异，靠打两轮真实对话也能看，
    但那要花额度还受限流。这个接口能直接把「同一道题、两种画像」的提示
    摆在一起对着看，改提示词时迭代快得多。
    """
    strategies = strategies or {}
    start_level = suggest_start_level(profile, topic)
    effective_level = max(hint_level, start_level)
    brief = build_profile_brief(profile, topic)
    return {
        "topic": topic,
        "hint_level": hint_level,
        "start_level_suggested": start_level,
        "effective_hint_level": effective_level,
        "strategy": strategies.get(effective_level, ""),
        "personalized": bool(brief),
        "profile_brief": brief,
    }
