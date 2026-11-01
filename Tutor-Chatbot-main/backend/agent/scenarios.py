"""M4 多场景策略表：场景 → 本轮由哪些角色 Agent 依次发言。

三条设计原则（决定了这个文件为什么长这样）：

1. **纯数据 + 纯函数，零 LLM、零 IO。**
   编排决策必须可复现、可单测。一旦让模型来选角色，「为什么这轮是
   3 个 Agent 而不是 1 个」就无从归因，出问题只能靠重放整条链路。

2. **主管用规则，不用模型。**
   多 Agent 本身已经把单轮 LLM 调用从 1 次变成 N 次，再加一层主管
   决策会直接撞限流（本仓已出现过 429 打断），且模型决策不可复现。
   规则决策的代价是「不够聪明」，换来的是可控成本 + 可解释编排——
   这是本阶段刻意选的取舍。`agent/orchestrator.py` 里留了 langgraph
   节点作为后续升级为真 LLM 主管的挂载点，届时只需替换那个节点。

3. **后续 Agent 看得到前面 Agent 说了什么。**
   这是「协作」与「并联各答各的」的分界线。每个 step 的 `handoff`
   只给「怎么接」的角色指令，前文的**实际内容**由 app.py 在拼装提示词
   时追加（只有运行时才知道前面说了什么，静态表写不出来）。

角色与场景（2026-11-01 调整）：
- 角色：主讲助教（tutor）、主讲教师（teacher）、出题助手（peer，内部键沿用 peer，
  避免历史的 messages.agent_role 变成脏数据）。
- `v1`    1v1 辅导：主讲助教单人（与 M1 行为完全一致，零回归）。成员上限 1 教师 + 1 学生。
- `group` 小组讨论：同样是主讲助教单人，区别在于是多人课堂（无人数上限）。
- 出题助手**不进默认编排**：默认每轮不说话，只在学生 `@出题助手` 时单独出题。
  这是刻意的取舍——多一个自动发言位就多一次完整 LLM 调用（实测 +1.5~3 秒、额度翻倍），
  而「出题」这个动作本身就是学生主动发起的，不需要猜。
"""

from __future__ import annotations

from dataclasses import dataclass


# 角色中文名：给前端展示与 SSE 的 agent 事件用，避免 frontend 再维护一份映射。
ROLE_LABELS: dict[str, str] = {
    "tutor": "主讲助教",
    "teacher": "主讲教师",
    "peer": "出题助手",
    "examiner": "考官",
}

# 单轮参与 Agent 数量硬上限。多 Agent 每多一个就多一次完整 LLM 调用
# （含 RAG 上下文重发），超过 3 个既烧额度又让学生等太久。
MAX_AGENTS_PER_TURN = 3

DEFAULT_SCENARIO = "v1"


@dataclass(frozen=True)
class AgentStep:
    """本轮编排里的一个发言位。

    role:    用哪个角色 Agent（tutor / teacher / peer / examiner）。
    handoff: **衔接指令**——告诉这个 Agent「你在接谁的什么话、该干什么」。
             第一位为空（没有前文可接）。
    stance:  在本场景里这个发言位的身份说明，给前端做气泡副标题用。
    """

    role: str
    handoff: str = ""
    stance: str = ""

    @property
    def label(self) -> str:
        return ROLE_LABELS.get(self.role, self.role)

    def to_dict(self) -> dict:
        return {
            "role": self.role,
            "label": self.label,
            "stance": self.stance,
            "handoff": self.handoff,
        }


@dataclass(frozen=True)
class ScenarioSpec:
    key: str
    label: str
    steps: tuple[AgentStep, ...]


SCENARIOS: dict[str, ScenarioSpec] = {
    "v1": ScenarioSpec(
        key="v1",
        label="1v1 辅导",
        steps=(AgentStep(role="tutor", stance="主讲"),),
    ),
    "group": ScenarioSpec(
        key="group",
        label="小组讨论",
        steps=(AgentStep(role="tutor", stance="主讲"),),
    ),
}

# 出题助手的一次发言位。它不在上面的场景表里，只有学生 @ 它时才被编进计划，
# 或者课堂关掉了主讲助教、由它独自撑起整个课堂时使用。
QUIZ_STEP = AgentStep(
    role="peer",
    stance="出题",
    handoff=(
        "学生点名要你出题。请针对当前知识点出 2 到 3 道由浅入深的题目，"
        "只出题、不给答案，每题后面用一句话点明它在考什么。"
        "如果学生指定了题量和题型，以学生的要求为准。"
    ),
)

# 学生怎么点名出题助手。逗号里是等价写法，不带 @ 的纯文本不算，
# 否则一句「出题助手刚才说的」也会被误判成请求。
QUIZ_MENTIONS: tuple[str, ...] = ("@出题助手", "@出题", "@出题人", "@quiz")


def quiz_requested(text: str | None) -> bool:
    """学生这句话是不是在叫出题助手。

    纯字符串判断，不调模型也不做分词：命中即认为要出题，误判的代价只是
    多出一道题，比漏判（学生 @ 了却没人应）体验好得多。
    """
    if not text:
        return False
    lowered = text.lower()
    return any(token.lower() in lowered for token in QUIZ_MENTIONS)


def normalize_scenario(scenario: str | None) -> str:
    """场景名归一：空 / 未知一律回落到 v1。

    客户端传来的值不可信（新场景随时可能加、前端可能传错），
    硬校验会让整次请求 422，宽容回落才对——与 Bug M6 同源的取舍。
    """
    key = (scenario or "").strip().lower()
    return key if key in SCENARIOS else DEFAULT_SCENARIO


def get_scenario(scenario: str | None) -> ScenarioSpec:
    return SCENARIOS[normalize_scenario(scenario)]


def list_scenarios() -> list[dict]:
    """给前端「建房时选场景」用：场景键 / 名称 / 角色序列。"""
    return [
        {
            "key": spec.key,
            "label": spec.label,
            "agents": [s.to_dict()["role"] for s in spec.steps],
            "steps": [s.to_dict() for s in spec.steps],
        }
        for spec in SCENARIOS.values()
    ]
