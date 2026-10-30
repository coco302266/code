"""学生画像推导（T5）。

职责：把「一轮对话的结果」翻译成「画像上的一次更新」。

设计取舍：
1. **不用 LLM 再总结一遍**。每轮再调一次模型既费额度又不可复现，
   而评估节点（graph.assess_understanding）已经给出了 resolved /
   hint_level / misconception 三个信号，够用了。画像推导做成确定性规则，
   好处是同一个学生跑两遍结果一样，出问题能追到具体是哪条规则。
2. **画像挂在 (user_id, classroom_id) 上**。一个教室 + 一个学科，
   同一个人在不同课堂的掌握度不该互相污染。
3. **流水和结论都留**。learning_preference 是从 learning_behaviors
   统计出来的，只留结论就没法解释「为什么判他是 beginner」。

掌握度不是「答对/答错」的二值判断，而是一个会漂移的分数：
    resolved  -> +0.3  学生自己说懂了，往 1 靠
    hint_level 3 -> -0.2  讲到举例+追问还没懂，是真薄弱
    hint_level 2 -> -0.1  需要展开原理
    hint_level 0/1 -> 0   刚开口问，不给分也不扣分
新知识点从 0.5 起步，代表「还没测出来」，而不是「半懂」。
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Iterable

from sqlalchemy import select

from db.models import LearningBehavior, StudentProfile

DEFAULT_MASTERY = 0.5
RESOLVED_DELTA = 0.3
# 讲到第 3 层（举例+追问）还没懂，说明这个知识点是真薄弱
HINT_DELTA = {3: -0.2, 2: -0.1, 1: 0.0, 0: 0.0}
MASTERY_MIN = 0.0
MASTERY_MAX = 1.0
WEAK_POINT_LIMIT = 12
# 定 advanced 至少要见过两个知识点：只问了一个、答得再好也不足以
# 说明整体水平，样本太少时封顶 intermediate。
LEVEL_SAMPLE_MIN = 2

LEVEL_BEGINNER = "beginner"
LEVEL_INTERMEDIATE = "intermediate"
LEVEL_ADVANCED = "advanced"


def normalize_topic(topic: str) -> str:
    """知识点名归一。

    topic 是模型自由抽取的，同一知识点会被写成「TCP 三次握手」
    「tcp三次握手」「TCP 三次握手。」——不做归一的话画像里会裂成
    好几个键，掌握度各算各的，同一个学生问三遍还是 beginner。
    """
    return re.sub(r"\s+", "", (topic or "").strip()).lower()


def derive_level(mastery: dict[str, float]) -> str:
    """由掌握度均值定级。空画像算 beginner——还没数据时不该虚报水平。"""
    if not mastery:
        return LEVEL_BEGINNER
    average = sum(mastery.values()) / len(mastery)
    if average >= 0.75 and len(mastery) >= LEVEL_SAMPLE_MIN:
        return LEVEL_ADVANCED
    if average >= 0.4:
        return LEVEL_INTERMEDIATE
    return LEVEL_BEGINNER


def classify_action(
    prev_topic: str,
    topic: str,
    prev_hint_level: int,
    hint_level: int,
    resolved: bool,
) -> str:
    """把这一轮归到一种学习行为。

    - ask_question：换知识点了（或刚开口），学生在开新坑
    - request_explanation：同一个知识点，讲解深度被推高了，学生要展开
    - repeat_question：同一个知识点还在聊，学生继续追问
    - mastered：学生明确掌握了（计划里只列了前三种，这个是为了让
      「什么时候学会的」在流水里可查，否则画像涨分了却查不到依据）
    """
    if resolved:
        return "mastered"
    if not topic:
        return "ask_question"
    if topic != prev_topic:
        return "ask_question"
    if hint_level > prev_hint_level:
        return "request_explanation"
    return "repeat_question"


def next_mastery(current: float | None, hint_level: int, resolved: bool) -> float:
    base = DEFAULT_MASTERY if current is None else float(current)
    delta = RESOLVED_DELTA if resolved else HINT_DELTA.get(hint_level, 0.0)
    return round(min(MASTERY_MAX, max(MASTERY_MIN, base + delta)), 2)


def merge_weak_points(existing: Iterable[str], new: str) -> list[str]:
    """追加一条薄弱点，去重、保序、超上限丢最老的。"""
    points = [p for p in existing if p]
    if new and new not in points:
        points.append(new)
    return points[-WEAK_POINT_LIMIT:]


def summarize_preference(behaviors: Iterable[LearningBehavior], mastery: dict) -> str:
    """从行为流水统计学习偏好。

    阈值取 1/3：三种行为大致均分时是 0.33，超过它才算「明显偏向」。
    """
    counts = Counter(b.action for b in behaviors)
    total = sum(counts.values())
    if total == 0:
        return "暂无明显偏好，按标准讲法即可"

    average = (sum(mastery.values()) / len(mastery)) if mastery else 0.0
    parts: list[str] = []

    if counts["request_explanation"] / total >= 0.34:
        parts.append("需要把原理展开才听得懂，别只给结论")
    if counts["repeat_question"] / total >= 0.34:
        parts.append("同一知识点需要多轮反复才能消化")
    if counts["ask_question"] >= 5 and average < 0.5:
        parts.append("提问覆盖面广但多数知识点尚未掌握")
    if counts["mastered"] and not parts:
        parts.append("吸收快，可以直接给标准答案和追问")

    return "；".join(parts) if parts else "暂无明显偏好，按标准讲法即可"


def get_or_create_profile(db, user_id: int, classroom_id: int) -> StudentProfile | None:
    if db is None:
        return None
    profile = (
        db.execute(
            select(StudentProfile).where(
                StudentProfile.user_id == user_id,
                StudentProfile.classroom_id == classroom_id,
            )
        )
        .scalars()
        .first()
    )
    if profile is None:
        profile = StudentProfile(
            user_id=user_id,
            classroom_id=classroom_id,
            overall_level=LEVEL_BEGINNER,
            learning_preference="",
            knowledge_mastery={},
            weak_points=[],
        )
        db.add(profile)
        db.flush()
    return profile


def record_behavior(
    db,
    user_id: int,
    classroom_id: int,
    topic: str,
    action: str,
    hint_level: int,
) -> LearningBehavior | None:
    if db is None or not topic:
        return None
    behavior = LearningBehavior(
        user_id=user_id,
        classroom_id=classroom_id,
        topic=topic,
        action=action,
        hint_level=hint_level,
    )
    db.add(behavior)
    db.flush()
    return behavior


def apply_turn(
    db,
    user_id: int,
    classroom_id: int,
    topic: str,
    hint_level: int,
    misconception: str,
    resolved: bool,
    prev_topic: str = "",
    prev_hint_level: int = 0,
) -> StudentProfile | None:
    """一轮对话结束后更新画像。任何异常都不许影响已经发出的回答。"""
    if db is None or not topic or topic == "unknown":
        return None

    topic = normalize_topic(topic)
    prev_topic = normalize_topic(prev_topic)

    profile = get_or_create_profile(db, user_id, classroom_id)
    if profile is None:
        return None

    mastery = dict(profile.knowledge_mastery or {})
    mastery[topic] = next_mastery(mastery.get(topic), hint_level, resolved)
    profile.knowledge_mastery = mastery

    if misconception:
        profile.weak_points = merge_weak_points(profile.weak_points or [], misconception)

    action = classify_action(prev_topic, topic, prev_hint_level, hint_level, resolved)
    record_behavior(db, user_id, classroom_id, topic, action, hint_level)

    behaviors = (
        db.execute(
            select(LearningBehavior)
            .where(
                LearningBehavior.user_id == user_id,
                LearningBehavior.classroom_id == classroom_id,
            )
            .order_by(LearningBehavior.id.desc())
            .limit(50)
        )
        .scalars()
        .all()
    )

    profile.overall_level = derive_level(mastery)
    profile.learning_preference = summarize_preference(behaviors, mastery)
    db.flush()
    return profile


def profile_to_dict(profile: StudentProfile) -> dict:
    mastery = profile.knowledge_mastery or {}
    return {
        "user_id": profile.user_id,
        "classroom_id": profile.classroom_id,
        "overall_level": profile.overall_level,
        "learning_preference": profile.learning_preference,
        "knowledge_mastery": mastery,
        "weak_points": profile.weak_points or [],
        "mastery_average": (
            round(sum(mastery.values()) / len(mastery), 2) if mastery else 0.0
        ),
        "topic_count": len(mastery),
        "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
        # --- M2 认知诊断 ---
        # 三列都可能是 NULL（存量行没跑过预测试；ability_vector 列本身可空），
        # 统一在出口处降级成 {} / None，避免把 None 漏给前端造成
        # "profile.cognitive_style.dimensions" 这类前端崩溃。
        "ability_vector": profile.ability_vector or {},
        "cognitive_style": profile.cognitive_style,
        "pretested_at": (
            profile.pretested_at.isoformat() if profile.pretested_at else None
        ),
    }
