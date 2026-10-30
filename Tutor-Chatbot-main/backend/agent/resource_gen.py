"""M3：个性化资源生成算法库。

把「教师/系统根据学生画像，生成 5 类个性化学习资源」做成一个可复用、
可单测的算法库，而不是又一个散落在接口里的提示词。

设计取舍（这五条决定了它配得上叫"算法库"而不是"又一个接口"）：

1. **5 类资源共用同一套流程**：检索依据 → 注入画像 → 按难度调整 →
   few-shot 定格式 → 调模型 → 解析校验。每类只换「指令 + JSON 结构 +
   难度策略」，主流程一处维护。新增一类资源 = 往 TYPE_SPECS 加一段。
2. **纯函数可测**：`build_resource_prompt` / `parse_resource` /
   `resolve_difficulty` 都不碰 LLM，给定相同输入产出相同输出，可以写单测
   钉住「few-shot 有没有注入」「难度指令对不对」「画像段有没有进提示词」
   这三条关键逻辑——这正是当初 resource_agent 没有、只能靠真实调模型
   才能验证的盲区。
3. **难度控制是算法不是参数**：`resolve_difficulty` 综合
   「请求显式指定 > 学生整体水平 > 该知识点掌握度」三级回退，把学生的真实
   水平翻译成给模型的难度指令；quiz 还会把难度翻译成 IRT 题目难度参数
   （见 `build_quiz_from_bank`）。
4. **few-shot 是质量兜底**：模型偶尔不守 JSON 格式，给 1 个同类型样例能
   显著稳定输出；样例是写死的，不烧额度。
5. **个性化只注入画像片段，不替模型做主**：复用 `personalize.build_profile_brief`，
   让资源针对薄弱点和认知风格，但资源类型/数量仍由请求决定。
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field, ValidationError

from .personalize import build_profile_brief

# --- 资源类型 ------------------------------------------------------------
# 5 类个性化学习资源。顺序即对外暴露的稳定枚举，不要随意重排。
RESOURCE_TYPES: tuple[str, ...] = ("slides", "quiz", "mindmap", "cards", "guide")


def list_resource_types() -> list[str]:
    """返回受支持的资源类型列表（接口/前端据此校验）。"""
    return list(RESOURCE_TYPES)


# --- 难度控制算法 --------------------------------------------------------
def difficulty_label(d: float) -> str:
    """0-1 难度系数 → 中文档位（给模型的指令用语，不是给学生看的标签）。"""
    if d < 0.2:
        return "入门"
    if d < 0.4:
        return "基础"
    if d < 0.6:
        return "进阶"
    if d < 0.8:
        return "提高"
    return "挑战"


def resolve_difficulty(
    requested: float | None, profile: dict | None, topic: str = ""
) -> float:
    """把"难度"这层意图翻译成 0-1 的难度目标。

    三级回退：
    1. 请求显式给了就用显式的（教师/系统明确指定优先级最高）。
    2. 否则用学生整体水平映射：beginner→0.3, intermediate→0.55,
       advanced→0.8——弱的学生要更基础（更低）的资源。
    3. 否则用这个知识点的掌握度反推：掌握度越低越需要基础资源
       （0.25 + 0.55×mastery），并和整体水平取更保守（更低）的一个，
       避免"整体 advanced 但单点没学过"被推到过难。
    没画像就回默认 0.5（中等）。结果一律夹到 [0.2, 0.85]，极端值会让
    资源要么全是铺垫、要么全是边界情况，都没法直接用。
    """
    if requested is not None:
        try:
            return max(0.0, min(1.0, float(requested)))
        except (TypeError, ValueError):
            pass

    if profile:
        level = profile.get("overall_level")
        base = {
            "advanced": 0.8,
            "intermediate": 0.55,
            "beginner": 0.3,
        }.get(level, 0.5)

        # 若该知识点有明确掌握度，叠加修正：掌握得差→更基础。
        norm = "".join((topic or "").split()).lower()
        mastery = (profile.get("knowledge_mastery") or {}) or {}
        score = mastery.get(norm)
        if score is None:  # 容错：画像里键的归一规则偶有出入
            for key, value in mastery.items():
                if "".join(str(key).split()).lower() == norm:
                    score = value
                    break
        if score is not None:
            try:
                inferred = 0.25 + 0.55 * float(score)
                base = min(base, inferred)
            except (TypeError, ValueError):
                pass
        return max(0.2, min(0.85, base))

    return 0.5


def _difficulty_instruction(d: float) -> str:
    """把难度系数翻译成给模型的通用指令段落。"""
    return (
        f"目标难度：{difficulty_label(d)}（难度系数 {d:.2f}，0=入门、1=挑战）。"
        "据此调整内容的深浅与术语门槛：难度低时先讲清基础概念和结论、少用抽象推演；"
        "难度高时多给边界情况、方案对比和面试追问，不必反复铺垫基础。"
    )


# --- 资源结构（Pydantic，解析失败即失败，不做"尽力提取"）-----------------
class _SlideSection(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    points: list[str] = Field(default_factory=list, max_length=8)


class SlideResource(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    objectives: list[str] = Field(default_factory=list, max_length=8)
    sections: list[_SlideSection] = Field(default_factory=list, max_length=6)
    key_points: list[str] = Field(default_factory=list, max_length=8)
    examples: list[str] = Field(default_factory=list, max_length=8)
    questions: list[str] = Field(default_factory=list, max_length=8)
    summary: list[str] = Field(default_factory=list, max_length=8)

    def is_shell(self) -> bool:
        """是不是只有标题的空壳——和 resource_agent.PptOutline 同语义。"""
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


class QuizQuestion(BaseModel):
    id: str = ""
    stem: str = Field(min_length=1)
    options: list[str] = Field(min_length=2, max_length=4)
    answer: int = Field(ge=0, le=3)
    explanation: str = ""
    # 不加 ge/le：模型偶尔会返回越界难度，交给 parse_resource 统一夹到 [0,1]
    difficulty: float = 0.5
    type: str = "factual"


class QuizResource(BaseModel):
    questions: list[QuizQuestion] = Field(default_factory=list, max_length=30)


class _MindMapNode(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    children: list["_MindMapNode"] = Field(default_factory=list, max_length=8)


class MindMapResource(BaseModel):
    root: str = Field(min_length=1, max_length=120)
    nodes: list[_MindMapNode] = Field(default_factory=list, max_length=10)


class CardItem(BaseModel):
    front: str = Field(min_length=1)
    back: str = Field(min_length=1)
    tag: str = ""


class CardResource(BaseModel):
    cards: list[CardItem] = Field(default_factory=list, max_length=30)


class GuideStep(BaseModel):
    step: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=80)
    detail: str = ""


class GuideResource(BaseModel):
    overview: str = ""
    steps: list[GuideStep] = Field(default_factory=list, max_length=12)
    checkpoints: list[str] = Field(default_factory=list, max_length=10)


# 自引用模型需要显式 rebuild（from __future__ annotations 下字符串前向引用）
_MindMapNode.model_rebuild()

_MODELS: dict[str, type[BaseModel]] = {
    "slides": SlideResource,
    "quiz": QuizResource,
    "mindmap": MindMapResource,
    "cards": CardResource,
    "guide": GuideResource,
}


class ResourceGenError(Exception):
    """资源生成/解析失败：模型没返回合法 JSON，或结构与约定不符。"""


# --- 每类资源的指令 / JSON 结构 / few-shot 样例 -------------------------
_TYPE_SPECS: dict[str, dict[str, str]] = {
    "slides": {
        "instruction": (
            "请为教师准备一份关于「{topic}」的课堂讲义大纲（课件）。"
            "覆盖该主题的核心概念、原理、面试常考要点与易错点，"
            "按「是什么 → 为什么 → 怎么用 → 面试怎么考」组织章节。"
        ),
        "schema": (
            '{\n'
            '  "title": "本讲标题",\n'
            '  "objectives": ["教学目标1", "教学目标2"],\n'
            '  "sections": [{"title": "章节标题", "points": ["要点1", "要点2"]}],\n'
            '  "key_points": ["面试常考要点1"],\n'
            '  "examples": ["具体例子或场景1"],\n'
            '  "questions": ["课堂追问1"],\n'
            '  "summary": ["一句话总结1"]\n'
            "}"
        ),
        "few_shot": (
            '{"title":"TCP 三次握手","objectives":["理解为什么需要三次而不是两次"],'
            '"sections":[{"title":"为什么是三次","points":["确认双方收发能力正常",'
            '"两次握手无法防止已失效连接请求"]}],"key_points":["SYN/SYN-ACK/ACK"],'
            '"examples":["WireShark 抓包示意"],"questions":["为什么不是两次？"],'
            '"summary":["三次握手本质是相互确认"]}'
        ),
    },
    "quiz": {
        "instruction": (
            "请为关于「{topic}」的知识点生成 {count} 道练习题（测验），用于考察学生掌握程度。"
            "题目要同时覆盖事实记忆（factual）与概念辨析（conceptual）两类。"
            "每题给出题干、4 个选项、正确答案下标（answer，从 0 开始）、解析（explanation）"
            "和难度系数（difficulty，0-1，越高越难）。"
        ),
        "schema": (
            '{\n'
            '  "questions": [\n'
            '    {"stem": "题干", "options": ["A","B","C","D"], "answer": 0,\n'
            '     "explanation": "解析", "difficulty": 0.4, "type": "factual"}\n'
            "  ]\n"
            "}"
        ),
        "few_shot": (
            '{"questions":[{"stem":"关于 TCP 三次握手，下列说法正确的是？",'
            '"options":["第三次握手可携带数据","两次握手足够建立连接",'
            '"SYN 报文不消耗序号","四次握手才可靠"],"answer":0,'
            '"explanation":"第三次握手允许携带数据以提升效率","difficulty":0.45,'
            '"type":"conceptual"}]}'
        ),
    },
    "mindmap": {
        "instruction": (
            "请为「{topic}」构建一份思维导图（层级结构）。先给出根主题，"
            "再列出 3-6 个一级分支，每个一级分支可含 1-3 个子节点，"
            "呈现概念之间的从属与关联。"
        ),
        "schema": (
            '{\n'
            '  "root": "主题",\n'
            '  "nodes": [{"title": "一级分支", "children": [{"title": "子节点"}]}]\n'
            "}"
        ),
        "few_shot": (
            '{"root":"进程与线程","nodes":[{"title":"进程",'
            '"children":[{"title":"地址空间"},{"title":"上下文切换"}]},'
            '{"title":"线程","children":[{"title":"共享资源"},{"title":"私有栈"}]}]}'
        ),
    },
    "cards": {
        "instruction": (
            "请为「{topic}」生成 {count} 张知识卡片（速记卡），用于学生快速回顾。"
            "每张卡片 front 是一个问题或概念名，back 是简洁答案；tag 标注所属小类。"
        ),
        "schema": (
            '{\n'
            '  "cards": [{"front": "正面（问题/概念）", "back": "背面（答案）", '
            '"tag": "小类"}]\n'
            "}"
        ),
        "few_shot": (
            '{"cards":[{"front":"什么是死锁？","back":"多个进程因互相等待对方持有的资源'
            '而永久阻塞","tag":"操作系统"},{"front":"死锁四个必要条件","back":'
            '"互斥、占有且等待、不可抢占、循环等待","tag":"操作系统"}]}'
        ),
    },
    "guide": {
        "instruction": (
            "请为「{topic}」生成一份循序渐进的学习路径（讲稿式），帮助学生从零掌握该主题。"
            "给出总览、有序的步骤（每步含标题与要点说明）、以及学完后的自测检查点。"
        ),
        "schema": (
            '{\n'
            '  "overview": "总览",\n'
            '  "steps": [{"step": 1, "title": "步骤标题", "detail": "说明"}],\n'
            '  "checkpoints": ["自测点1"]\n'
            "}"
        ),
        "few_shot": (
            '{"overview":"从进程线程基础到并发编程","steps":[{"step":1,'
            '"title":"理解进程与线程区别","detail":"地址空间、调度、通信开销"},'
            '{"step":2,"title":"掌握同步原语","detail":"互斥锁、条件变量、信号量"}],'
            '"checkpoints":["能说清线程切换为何比进程轻","能写出生产者消费者"]}'
        ),
    },
}


# --- 提示词组装 ----------------------------------------------------------
def build_resource_prompt(
    resource_type: str,
    topic: str,
    *,
    difficulty: float,
    profile_brief: str = "",
    context: str = "",
    few_shot: bool = True,
    audience: str = "",
    count: int = 5,
) -> str:
    """组装某一类资源的生成提示词。

    纯函数：相同输入产出相同输出。测试靠它验证 few-shot / 难度指令 / 画像段
    这三条关键注入是否正确。
    """
    if resource_type not in RESOURCE_TYPES:
        raise ResourceGenError(f"未知资源类型：{resource_type}（可选：{RESOURCE_TYPES}）")
    spec = _TYPE_SPECS[resource_type]

    parts: list[str] = [
        spec["instruction"].format(topic=topic, count=count),
        _difficulty_instruction(difficulty),
    ]
    if audience:
        parts.append(
            f"本班学生整体水平：{audience}。据此进一步调整深度与术语门槛。"
        )
    if context:
        parts.append(
            "知识库资料（有则优先依据，没有就用你自己的知识，但不要编造来源）：\n"
            + context
        )
    if profile_brief:
        parts.append(
            "学生画像（个性化依据，据此针对薄弱点和认知风格调整内容，"
            "不要原样复述给学生）：\n" + profile_brief
        )
    else:
        parts.append("暂无该学生画像，按通用讲法即可。")
    parts.append("只返回如下结构的 JSON：\n" + spec["schema"])
    if few_shot and spec.get("few_shot"):
        parts.append(
            "参考样例（仅示范格式，内容必须针对本次主题重新生成）：\n" + spec["few_shot"]
        )
    parts.append("规则：全部用中文；只返回 JSON 对象，不要任何其他文字。")
    return "\n\n".join(parts)


# --- 解析（纯函数）------------------------------------------------------
def _strip_fence(raw: str) -> str:
    """去掉模型偶尔包上的 ```json 代码围栏。"""
    cleaned = (raw or "").strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].strip().lower() in {"```", "```json"}:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    return cleaned


def parse_resource(resource_type: str, raw: str, *, count: int | None = None) -> dict:
    """解析模型返回为结构化 dict。带代码围栏也能吃；结构非法即抛 ResourceGenError。"""
    if resource_type not in RESOURCE_TYPES:
        raise ResourceGenError(f"未知资源类型：{resource_type}")
    cleaned = _strip_fence(raw)
    model_cls = _MODELS[resource_type]
    try:
        obj = model_cls.model_validate(json.loads(cleaned))
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ResourceGenError(
            f"模型返回的内容不是合法 JSON（{resource_type}）：{exc}"
        ) from exc
    except ValidationError as exc:
        raise ResourceGenError(f"资源结构不符合约定（{resource_type}）：{exc}") from exc

    data = obj.model_dump()

    if resource_type == "slides" and obj.is_shell():
        raise ResourceGenError("生成的内容为空壳（只有标题没有实质内容），请重试。")

    if resource_type == "quiz":
        questions = data.get("questions", [])
        if count:
            questions = questions[:count]
        for index, question in enumerate(questions):
            try:
                question["difficulty"] = max(
                    0.0, min(1.0, float(question.get("difficulty", 0.5)))
                )
            except (TypeError, ValueError):
                question["difficulty"] = 0.5
            if not question.get("id"):
                question["id"] = f"Q-{index + 1:03d}"
        data["questions"] = questions

    return data


# --- 零-LLM 题库组卷（quiz 难度控制的确定性实现）-------------------------
def build_quiz_from_bank(
    topics: list[str] | None, difficulty: float, count: int = 5
) -> dict | None:
    """从 M2 标定题库按难度带挑题，零 LLM、可复现。

    只在 topics 命中题库知识点时才生效；否则返回 None 让调用方退回 LLM 生成。
    难度带以目标难度为中心优先挑选，不足再放宽——这就是把 `resolve_difficulty`
    算出的难度目标落地成"具体出哪些题"的算法环节。
    """
    from .pretest import _ITEM_BANK, normalize_topic

    if not topics:
        return None
    wanted = {normalize_topic(t) for t in topics}
    candidates = [
        it for it in _ITEM_BANK if normalize_topic(it.get("topic", "")) in wanted
    ]
    if not candidates:
        return None

    candidates.sort(key=lambda it: abs(float(it.get("difficulty", 0.5)) - difficulty))
    chosen = candidates[: max(1, count)]
    questions = [
        {
            "id": it["id"],
            "stem": it["stem"],
            "options": list(it["options"]),
            "answer": int(it["answer"]),
            "explanation": it.get("explanation", ""),
            "difficulty": float(it["difficulty"]),
            "type": it.get("type", "factual"),
        }
        for it in chosen
    ]
    return {"questions": questions, "from_bank": True}


# --- 编排（调 LLM，可单测时整体 mock）-----------------------------------
async def generate_resource(
    llm,
    resource_type: str,
    topic: str,
    *,
    profile: dict | None = None,
    difficulty: float | None = None,
    audience: str = "",
    context: str = "",
    few_shot: bool = True,
    count: int = 5,
) -> dict:
    """端到端生成一类资源：算难度 → 取画像段 → 组提示词 → 调模型 → 解析。"""
    resolved = resolve_difficulty(difficulty, profile, topic)
    brief = build_profile_brief(profile, topic) if profile else ""
    prompt = build_resource_prompt(
        resource_type,
        topic,
        difficulty=resolved,
        profile_brief=brief,
        context=context,
        few_shot=few_shot,
        audience=audience,
        count=count,
    )
    response = await llm.ainvoke([HumanMessage(content=prompt)])
    return parse_resource(resource_type, response.content, count=count)
