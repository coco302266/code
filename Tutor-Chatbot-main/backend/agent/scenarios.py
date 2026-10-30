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

场景与角色序列：
- `v1`    1v1 辅导：主讲助教单人（与 M1 行为完全一致，零回归）。
- `group` 小组讨论：助教主讲 → 学伴补充 / 提不同角度。
- `debate` 辩论对抗：助教立论 → 学伴质疑 → 考官追问收尾。
"""

from __future__ import annotations

from dataclasses import dataclass


# 角色中文名：给前端展示与 SSE 的 agent 事件用，避免 frontend 再维护一份映射。
ROLE_LABELS: dict[str, str] = {
    "tutor": "主讲助教",
    "teacher": "主讲教师",
    "peer": "学伴",
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
        steps=(
            AgentStep(role="tutor", stance="主讲"),
            AgentStep(
                role="peer",
                stance="补充",
                handoff=(
                    "上面是主讲助教的讲解。请你以一起备考的学伴身份接着说："
                    "给出你自己对这个知识点的理解，或补充一个助教没提到的角度、"
                    "一句好记的口诀。不要重复助教已经讲过的内容，控制在 150 字以内。"
                ),
            ),
        ),
    ),
    "debate": ScenarioSpec(
        key="debate",
        label="辩论对抗",
        steps=(
            AgentStep(role="tutor", stance="立论"),
            AgentStep(
                role="peer",
                stance="质疑",
                handoff=(
                    "上面是主讲助教给的「标准说法」。现在请你扮演质疑者："
                    "指出这个说法在什么前提或边界条件下不成立，或者学生最容易在"
                    "这里产生的误解是什么。要有理有据、就事论事，不要为了反对而反对，"
                    "控制在 150 字以内。"
                ),
            ),
            AgentStep(
                role="examiner",
                stance="追问",
                handoff=(
                    "上面两位给出了不同角度的说法。请你以考官身份收尾："
                    "针对这个知识点出 1 道能分辨「真懂」还是「背了答案」的追问题，"
                    "并一句话说明判断标准。不要给答案，控制在 120 字以内。"
                ),
            ),
        ),
    ),
}


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
