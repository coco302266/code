from langchain_core.messages import BaseMessage

from typing import Annotated, Any, List, Literal, NotRequired, TypedDict

from langgraph.graph.message import add_messages
from pydantic import BaseModel, ConfigDict, Field


# 讲解深度四层：面向「计算机八股 / 面试知识点」学习场景。
# 与苏格拉底式的「启发到第几级」不同，这里每一层都允许给答案，
# 区别只在展开的程度——学生来这里是背八股的，捂着答案不给没有意义。
HINT_STRATEGIES = {
    0: "先用一句话给出结论，让学生知道这个知识点的标准答案是什么。不要展开原理，不要举例子。",
    1: "给出面试里的标准答案：条理清晰、分点列出、可以直接背诵那种。篇幅控制在 200 字以内，不要深入底层原理。",
    2: "在标准答案的基础上展开原理：讲清楚「为什么是这样」，涉及哪些底层机制、数据结构或设计取舍。允许适当加长。",
    3: "先讲透原理，再配一个具体例子 or 场景帮助理解，最后抛一个面试常见追问方向，让学生自查是否真的掌握。",
}

# This is the state schema for our tutor agent. It includes the conversation history (messages), the current topic being discussed, the hint level (which determines the strategy for the next hint), any specific misconception identified, and whether the student's confusion has been resolved. The messages field is annotated with add_messages to allow the graph to automatically append new messages to the history as we generate responses.
class TutorState(TypedDict):
    messages: Annotated[List[BaseMessage], add_messages]
    topic: str
    hint_level: int
    misconception: str
    resolved: bool
    llm: Any
    topic_changed: NotRequired[bool]
    # 越界信号（2026-10-31）：最新消息有明确诉求，但不属于计算机 / 编程领域。
    # 故意不复用 topic —— 它是「本轮该不该拒答」的判断，不是知识点归属，
    # 混进 topic 会被「沿用上一轮知识点」的容错分支吃掉（详见 graph.py）。
    # 只写 True，不写 False：字典合并时缺省即 False，没必要多一个键。
    off_topic: NotRequired[bool]


class HistoryMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(default="", max_length=4000)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(..., min_length=1, max_length=1000)
    topic: str = Field(default="", max_length=100)
    hint_level: int = Field(default=0, ge=0, le=3)
    misconception: str = Field(default="", max_length=500)
    resolved: bool = False
    history: List[HistoryMessage] = Field(default_factory=list, max_length=50)
    session_id: str = Field(default="", max_length=36)
    provider: Literal["ollama", "groq", "gemini", "openai"] = "openai"
    # T3：持久化定位。都留空时 /chat 退化为「不落库」的旧行为，
    # 保证现有前端（只传 session_id）不被破坏。
    classroom_id: int | None = None
    user_id: int | None = None
    # M1 聊天室：AI 以哪个角色 Agent 回应（tutor 主讲助教 / teacher 教师 /
    # peer 学伴 / examiner 考官）。默认 tutor，向后兼容旧前端。
    agent_role: Literal["tutor", "teacher", "peer", "examiner"] = "tutor"
    # M4 多场景：本轮跑哪种协作形态（v1 / group）。
    # None = 跟随课堂自身的 scenario；显式传值则覆盖（便于调试与演示）。
    # 故意用 str 而不是 Literal：非法值由编排层回落到 v1，不该让请求 422
    # ——模型/客户端输入一律宽容解析（Bug M6 同源取舍）。
    scenario: str | None = Field(default=None, max_length=16)
