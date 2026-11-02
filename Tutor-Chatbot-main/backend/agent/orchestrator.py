"""M4 多智能体编排入口（框架：langgraph）。

形态演进：
- M1：一张「角色解析」图——输入 agent_role，输出该角色的系统提示词。
- M4：升级为「编排计划」图——输入场景，输出**本轮由哪些角色依次发言**
      的完整计划（角色序列 + 衔接指令 + 是否降级 + 降级原因）。
      LLM 的实际调用与流式由 `app.py` 按计划执行，编排本身仍然零 LLM。

为什么主管（supervisor）用规则而不是模型：
- 多 Agent 本身已经把单轮 LLM 调用从 1 次变成 N 次，再叠一层主管决策
  会直接撞限流（本仓已出现过 429 打断整轮开发），且模型决策不可复现；
- 「为什么这轮是 3 个 Agent」必须能事后回答，规则决策天然自带解释。
这里的 langgraph 节点就是后续升级为真 LLM 主管的挂载点——届时替换
`plan_agents_node` 的实现即可，/chat 的调用方式不变。

降级契约（重要）：任何异常输入都收敛成「单 Agent + degraded=True + reason」，
绝不因为编排层的问题让整次 /chat 失败。
"""

from __future__ import annotations

import os
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from agent.scenarios import (
    DEFAULT_SCENARIO,
    MAX_AGENTS_PER_TURN,
    QUIZ_STEP,
    ROLE_LABELS,
    SCENARIOS,
    ScenarioSpec,
    get_scenario,
)


class AgentState(TypedDict):
    """多智能体调度在单轮 /chat 内的状态载体。"""

    scenario: str
    agent_role: str
    topic: str
    hint_level: int
    strategy: str
    misconception_note: str
    resolved: bool
    # 2026-11-01：课堂是否配备主讲助教 / 本轮是否由学生点名出题助手。
    # 用 total=False 的 TypedDict 语义上允许缺省，取值时统一 get(..., 默认值)。
    use_tutor: bool
    quiz_only: bool
    # 编排产物
    steps: list[dict]
    roles: list[str]
    scenario_label: str
    degraded: bool
    reason: str


def _collab_enabled() -> bool:
    """多智能体协作总开关（AGENT_COLLAB=0 关闭）。

    关掉后行为与 M1 完全一致：单 Agent、单次 LLM 调用。
    给「额度不够 / 演示环境要压延迟」留一条不改代码的退路。
    """
    return os.getenv("AGENT_COLLAB", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def plan_agents(
    scenario: str | None,
    *,
    explicit_role: str = "tutor",
    enabled: bool | None = None,
    max_agents: int = MAX_AGENTS_PER_TURN,
    use_tutor: bool = True,
    quiz_only: bool = False,
) -> dict:
    """产出本轮编排计划（纯函数，零 LLM、零 IO，可直接单测）。

    scenario      课堂或请求指定的场景（v1 / group；未知回落 v1）
    explicit_role 客户端显式指定的角色，**只在单步场景（v1）下生效**。
                  多步场景由场景自己决定角色序列——否则「小组讨论」里
                  客户端传个 examiner 就把讨论变成考试了。
    enabled       总开关，None 表示读环境变量 AGENT_COLLAB
    max_agents    单轮 Agent 数量上限，超出即截断并标记降级
    use_tutor     这个课堂是否配备主讲助教（建房时的开关）。关掉后助教不发言，
                  本轮就是空计划（没有 AI 应答），除非学生 @ 了出题助手
    quiz_only     学生 @ 了出题助手：本轮跳过常规编排，只让出题助手出题

    返回：{scenario, scenario_label, steps, roles, degraded, reason}
    """
    if enabled is None:
        enabled = _collab_enabled()

    raw = (scenario or "").strip().lower()
    spec: ScenarioSpec = get_scenario(raw)
    degraded = False
    reason = ""

    # 学生点名出题助手：直接短路，不走场景编排。
    # 这不是降级（degraded 保持 False）——它是学生明确要求的路径，不该被记成异常。
    if quiz_only:
        return _quiz_plan(spec, reason="学生 @ 了出题助手")

    # 课堂关掉了主讲助教（2026-11-02 修正）：助教不发言，学生的问题也不该
    # 转交给别人代答。出题助手是「@ 才说话」的角色——早先这里把它当成兜底
    # 应答者，后果是关掉助教后发一句「花生在吗」也会收到三道题。现在返回
    # 空计划：本轮没有 AI 发言，要出题由学生自己 @ 它（上面已先短路）。
    if not use_tutor:
        return _silent_plan(spec, reason="课堂未配备主讲助教，本轮无 AI 发言")

    # 空串视为「没指定」，按 v1 处理，不算降级——否则每次不带场景的普通
    # 请求都会打一条「未知场景 ''」的日志，真正的降级信号会被噪声埋掉。
    if raw and raw not in SCENARIOS:
        # 未知场景：回落到 1v1，但要留下痕迹，便于事后查「为什么没走多 Agent」
        degraded = True
        reason = f"未知场景 {scenario!r}，回落 {DEFAULT_SCENARIO}"
    elif not enabled:
        degraded = True
        reason = "多智能体协作已关闭（AGENT_COLLAB=0），退回单 Agent"

    # 总开关关闭：无论什么场景都只走一个 Agent，角色听客户端的。
    if not enabled:
        role = _valid_role(explicit_role)
        return _plan(spec, [role], degraded=True, reason=reason or "协作关闭")

    # 单步场景（v1）：角色由客户端显式指定决定，保持 M1 行为不变。
    if len(spec.steps) == 1:
        role = _valid_role(explicit_role)
        return _plan(spec, [role], degraded=degraded, reason=reason)

    # 多步场景：用场景自带的角色序列，explicit_role 不参与（理由见 docstring）。
    roles = [step.role for step in spec.steps]

    if max_agents <= 0:
        return _plan(
            spec,
            roles[:1],
            degraded=True,
            reason=(reason + "；" if reason else "") + "max_agents<=0，只保留首位",
        )

    if len(roles) > max_agents:
        degraded = True
        reason = (
            (reason + "；" if reason else "")
            + f"角色数 {len(roles)} 超过上限 {max_agents}，已截断"
        )
        roles = roles[:max_agents]

    return _plan(spec, roles, degraded=degraded, reason=reason)


def _quiz_plan(spec: ScenarioSpec, *, reason: str) -> dict:
    """只有出题助手一个发言位的计划。

    走单独构造函数而不是复用 `_plan`：出题助手不在场景表的 steps 里，
    硬塞进去会拿不到 handoff（`_plan` 按位置匹配角色，对不上就清空指令），
    结果就是「出题助手」收到一份没有出题要求的空指令。
    """
    return {
        "scenario": spec.key,
        "scenario_label": spec.label,
        "steps": [
            {
                "role": QUIZ_STEP.role,
                "label": QUIZ_STEP.label,
                "stance": QUIZ_STEP.stance,
                "handoff": QUIZ_STEP.handoff,
                "index": 0,
            }
        ],
        "roles": [QUIZ_STEP.role],
        "degraded": False,
        "reason": reason,
    }


def _silent_plan(spec: ScenarioSpec, *, reason: str) -> dict:
    """没有任何发言位的计划：本轮不派 Agent（课堂关掉了主讲助教）。

    为什么用空 steps 而不是「塞一条系统提示」：steps 决定的是**谁来调 LLM**，
    系统提示属于事件层的事。混在一起会让 /evaluation 把「没派 Agent」
    记成「派了一个 Agent」。

    degraded 保持 False——这是课堂设置决定的正常形态，不是异常兜底。
    记成降级会让评估口径里的降级率虚高，也会误导排查。
    """
    return {
        "scenario": spec.key,
        "scenario_label": spec.label,
        "steps": [],
        "roles": [],
        "degraded": False,
        "reason": reason,
    }


def _valid_role(role: str | None) -> str:
    """角色名归一：未知一律回落到 tutor。"""
    key = (role or "").strip().lower()
    return key if key in ROLE_LABELS else "tutor"


def _plan(spec: ScenarioSpec, roles: list[str], *, degraded: bool, reason: str) -> dict:
    """按最终角色序列装配计划：从场景里取对应 step 的衔接指令。"""
    steps: list[dict] = []
    for index, role in enumerate(roles):
        # 角色序列可能被截断 / 被 explicit_role 替换，所以按位置取 step 之后
        # 还要核对角色是否对得上：对不上就不能沿用它的 stance / handoff，
        # 否则会出现「考官」顶着「立论」这种串味的副标题。
        step = spec.steps[index] if index < len(spec.steps) else None
        matched = step is not None and step.role == role
        steps.append(
            {
                "role": role,
                "label": ROLE_LABELS.get(role, role),
                "stance": step.stance if matched else "",
                "handoff": step.handoff if matched else "",
                "index": index,
            }
        )
    return {
        "scenario": spec.key,
        "scenario_label": spec.label,
        "steps": steps,
        "roles": roles,
        "degraded": degraded,
        "reason": reason,
    }


def _plan_agents_node(state: AgentState) -> dict:
    """langgraph 节点：把编排决策挂进调度链路（确定性，不调 LLM）。

    后续要升级成 LLM 主管，替换这一个节点的实现即可，
    /chat 侧的 `agent_graph.ainvoke(...)` 调用方式不用改。
    """
    return plan_agents(
        state.get("scenario"),
        explicit_role=state.get("agent_role", "tutor"),
        use_tutor=state.get("use_tutor", True),
        quiz_only=state.get("quiz_only", False),
    )


def build_agent_graph():
    workflow = StateGraph(AgentState)
    workflow.add_node("plan_agents", _plan_agents_node)
    workflow.set_entry_point("plan_agents")
    workflow.add_edge("plan_agents", END)
    return workflow.compile()


# 模块加载即编译一次，/chat 每次调用直接 ainvoke。
agent_graph = build_agent_graph()


def orchestrate(state: dict[str, Any]) -> dict:
    """同步编排入口（给测试 / 非流式调用用），语义与 ainvoke 一致。"""
    return plan_agents(
        state.get("scenario"),
        explicit_role=state.get("agent_role", "tutor"),
        use_tutor=state.get("use_tutor", True),
        quiz_only=state.get("quiz_only", False),
    )
