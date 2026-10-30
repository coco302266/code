import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Awaitable, Callable

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph, END
from langchain_ollama import ChatOllama
from pydantic import BaseModel, Field, StrictBool, ValidationError

from .state import TutorState

from .prompts import EXTRACT_TOPIC_PROMPT, ASSESS_UNDERSTANDING_PROMPT

# 本项目此前一行日志都没有（无 basicConfig）。用 WARNING 记异常是刻意的：
# 即使不配 logging，Python 的 logging.lastResort 也会把 WARNING+ 打到 stderr，
# uvicorn 控制台直接可见；DEBUG/INFO 需要显式开 LOG_LEVEL 才看得到。
logger = logging.getLogger(__name__)

# 按文件位置加载，不依赖当前工作目录（理由见 db/session.py 的同款注释）
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

DEFAULT_MODELS = {
    "ollama": "llama3.2",
    "groq": "openai/gpt-oss-120b",
    "gemini": "gemini-2.5-flash-lite",
    "openai": "deepseek-flash",
}


def get_model(provider: str) -> str | None:
    configured_provider = os.getenv("LLM_PROVIDER")
    configured_model = os.getenv("LLM_MODEL")
    return (
        os.getenv(f"{provider.upper()}_MODEL")
        or (configured_model if configured_provider == provider else None)
        or DEFAULT_MODELS.get(provider)
    )


def get_llm(provider: str | None = None):
    if provider is None:
        provider = os.getenv("LLM_PROVIDER", "groq")

    model = get_model(provider)

    if provider == "ollama":
        return ChatOllama(model=model, temperature=0.4)
    if provider == "groq":
        from langchain_groq import ChatGroq

        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise ValueError(
                "GROQ_API_KEY is not configured. Add it to backend/.env "
                "and restart the backend."
            )
        return ChatGroq(model=model, temperature=0.4, groq_api_key=api_key)
    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        if not os.getenv("GOOGLE_API_KEY"):
            raise ValueError(
                "GOOGLE_API_KEY is not configured. Add it to backend/.env "
                "and restart the backend."
            )
        return ChatGoogleGenerativeAI(model=model, temperature=0.4)

    if provider == "openai":
        # Generic OpenAI-compatible endpoint (DeepSeek, Zhipu GLM, SiliconFlow,
        # DashScope...). Configure via LLM_API_KEY / LLM_BASE_URL / LLM_MODEL.
        # 注意：DeepSeek 已退役 deepseek-chat / deepseek-v4-flash，
        # 现在写 deepseek-flash；改厂商只需改 .env 三行，不用动这里。
        from langchain_openai import ChatOpenAI

        api_key = os.getenv("LLM_API_KEY")
        if not api_key:
            raise ValueError(
                "LLM_API_KEY is not configured. Add it to backend/.env "
                "and restart the backend."
            )
        return ChatOpenAI(
            model=model,
            temperature=0.4,
            api_key=api_key,
            base_url=os.getenv("LLM_BASE_URL") or None,
        )

    raise ValueError(f"Unknown provider: {provider}")


class AssessmentResult(BaseModel):
    resolved: StrictBool
    hint_level: int = Field(ge=0, le=3)
    misconception: str = Field(default="", max_length=500)


# 抽 topic 是「输出一两个词」的分类任务，而 deepseek-flash 是推理模型：实测它
# 每次会先产出 23~29 个 reasoning token 再给答案（见 scripts/probe_thinking_off.py）。
# 关掉思考后该节点 mean 1012→656ms（−35%）、p95 1509→878ms（−42%），判定准确率
# 96.3%→97.5% 不降反升（唯一差异那一条还是关掉后更准）。
# 只在抽取节点关——理解评估要判断学生是否真的懂，让它想是值得的。
TOPIC_NO_THINK = os.getenv("RAG_TOPIC_NO_THINK", "1").strip() != "0"

# 不是所有 OpenAI 兼容厂商都认这个字段。认不出的第一次会抛错，记下来后不再
# 重复送，避免每轮都先浪费一次失败请求。
_NO_THINK_UNSUPPORTED: set[str] = set()


def _topic_llm(llm):
    """抽取节点专用的 LLM 句柄：关掉推理模型的思考。

    返回原对象（不 bind）有两种情况：开关关掉、或该模型已被判定为不支持。
    """
    if not TOPIC_NO_THINK:
        return llm
    key = "%s/%s" % (type(llm).__name__, getattr(llm, "model_name", "") or "")
    if key in _NO_THINK_UNSUPPORTED:
        return llm
    return llm.bind(extra_body={"thinking": {"type": "disabled"}})


def _mark_no_think_unsupported(llm) -> None:
    key = "%s/%s" % (type(llm).__name__, getattr(llm, "model_name", "") or "")
    _NO_THINK_UNSUPPORTED.add(key)


def parse_assessment_result(raw: str) -> AssessmentResult:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].strip().lower() in {"```", "```json"}:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    return AssessmentResult.model_validate(json.loads(cleaned))


async def extract_topic_node(state: TutorState) -> dict:
    user_messages = [m for m in state["messages"] if isinstance(m, HumanMessage)]
    latest_message = user_messages[-1].content

    # 追问（"那怎么预防呢？""为什么？"）脱离上下文根本无法判断知识点。
    # 只把最后一句话喂给模型是原来的 bug：一旦客户端没回传 topic
    # （例如历史改成从数据库加载），所有追问都会被判成 unknown。
    recent = state["messages"][-6:]
    conversation = "\n".join(
        f"{'User' if isinstance(m, HumanMessage) else 'Tutor'}: {m.content[:300]}"
        for m in recent
    ) or "（无）"

    current_topic = state["topic"] or "unknown"
    prompt = EXTRACT_TOPIC_PROMPT.format(
        current_topic=current_topic,
        conversation=conversation,
        latest_message=latest_message,
    )

    caller = _topic_llm(state["llm"])
    try:
        response = await caller.ainvoke([HumanMessage(content=prompt)])
    except Exception as exc:
        if caller is state["llm"]:
            raise
        # 降级必须可见：厂商不认 thinking 字段时静默回退，下次就查不到为什么慢了。
        logger.warning(
            "抽取节点带 thinking=disabled 调用失败，降级为默认参数重试 "
            "model=%s exc=%s: %s",
            getattr(state["llm"], "model_name", "?"),
            type(exc).__name__,
            exc,
        )
        _mark_no_think_unsupported(state["llm"])
        response = await state["llm"].ainvoke([HumanMessage(content=prompt)])

    result = response.content.strip()
    if result.lower() == "same":
        return {}

    # 模型偶尔会带个句号或引号返回（"死锁。"），不清掉就会被当成换了话题，
    # 白白把 hint_level 打回 0。
    topic = result.lower().strip().strip("。.！!？?；;，,、\"' ")
    if topic in ["unknown", "none", "no topic", "not mentioned"]:
        if current_topic != "unknown":
            return {}
        return {
            "topic": "unknown",
            "hint_level": 0,
            "misconception": "",
            "resolved": False,
        }

    if topic == current_topic.lower():
        return {}

    return {
        "topic": topic,
        "hint_level": 0,
        "misconception": "",
        "resolved": False,
        "topic_changed": True,
    }


async def assess_understanding_node(state: TutorState) -> dict:
    # 换话题时不再整体短路。
    # 原来的写法一遇到 topic_changed 就 return 空 misconception、hint_level=0，
    # 结果学生带着错误理解来提问的「第一轮」（"死锁就是进程死循环吧？"）
    # 永远抓不到误解——而那正是学生画像最值钱的原材料。
    # 现在换话题只把讲解深度归零，误解照样记下来。
    topic_changed = state.get("topic_changed", False)

    if state["topic"] == "unknown":
        return {"hint_level": 0, "misconception": "", "resolved": False}

    # 不再要求「至少两轮」才评估：学生的第一句话里经常就带着误解
    # （"死锁不就是进程死循环卡住了嘛"），那是最该记进画像的信号，
    # 用消息条数把它挡掉等于每次都要等学生错第二轮才发现。

    history_text = "\n".join(
        f"{'User' if isinstance(m, HumanMessage) else 'Tutor'}: {m.content}"
        for m in state["messages"]
    )
    prompt = ASSESS_UNDERSTANDING_PROMPT.format(
        topic=state["topic"],
        history_text=history_text,
        hint_level=state["hint_level"],
    )

    response = await state["llm"].ainvoke([HumanMessage(content=prompt)])

    try:
        assessment = parse_assessment_result(response.content)
    except (json.JSONDecodeError, TypeError, ValidationError, ValueError) as exc:
        # LLM-6a：解析失败与「模型判 resolved=false」的返回完全同形，
        # 不记日志的话线上永远无法区分这两者，只能靠复现。
        logger.warning(
            "评估节点解析失败，按「未掌握」降级处理 topic=%r in_hint_level=%d "
            "exc=%s: %s raw=%r",
            state["topic"],
            state["hint_level"],
            type(exc).__name__,
            exc,
            response.content[:500],
        )
        return {
            "resolved": False,
            "hint_level": state["hint_level"],
            "misconception": state["misconception"],
        }

    # 「模型说了什么」和「代码解析成什么」分两层看。项目没配 logging，
    # 所以这条默认不可见；需要排查时在主进程里执行一次
    # logging.basicConfig(level=logging.DEBUG) 就能拿到原始返回，不用再写临时脚本。
    logger.debug(
        "评估节点解析成功 topic=%r resolved=%s model_hint=%d cur_hint=%d "
        "misconception=%r raw=%r",
        state["topic"],
        assessment.resolved,
        assessment.hint_level,
        state["hint_level"],
        assessment.misconception,
        response.content[:500],
    )

    if topic_changed:
        return {
            "resolved": False,
            "hint_level": 0,
            "misconception": assessment.misconception,
        }

    if assessment.resolved:
        return {
            "resolved": True,
            "hint_level": 0,
            "misconception": "",
        }

    model_hint = assessment.hint_level
    cur_hint = state["hint_level"]

    if model_hint >= cur_hint:
        # AssessmentResult 已经用 Field(ge=0, le=3) 挡过，这里是双保险：
        # HINT_STRATEGIES 只有 0-3 四层，越界会在 app.py 里 KeyError。
        next_hint = min(model_hint, 3)
    else:
        # 模型主动调低 = 学生有进步的信号。原来用 max() 把这个信号整段吞掉，
        # 后果是 resolved 漏判一次，学生就永久卡在第 3 层（只有换话题才归零）。
        # 这里允许下调，但每轮最多降 1 级：temperature 是 0.4，
        # 一步到位地跟会让讲解深度来回跳。
        next_hint = max(cur_hint - 1, model_hint, 0)
        logger.info(
            "讲解深度下调 topic=%r %d -> %d（模型给 %d）misconception=%r",
            state["topic"],
            cur_hint,
            next_hint,
            model_hint,
            assessment.misconception,
        )

    # 已经讲到最深一层、模型又说不出学生具体哪里不懂 —— 这是 resolved 漏判的
    # 典型特征。不改行为，只留证据。
    if next_hint >= 3 and not assessment.misconception:
        logger.warning(
            "已到最深讲解层且模型未给出具体误解 topic=%r resolved=false "
            "—— 疑似 resolved 漏判，需人工核看",
            state["topic"],
        )

    return {
        "resolved": False,
        "hint_level": next_hint,
        "misconception": assessment.misconception,
    }

def build_assessment_graph() -> StateGraph:
    workflow = StateGraph(TutorState)

    workflow.add_node("extract_topic", extract_topic_node)
    workflow.add_node("assess_understanding", assess_understanding_node)

    workflow.set_entry_point("extract_topic")
    workflow.add_edge("extract_topic", "assess_understanding")
    workflow.add_edge("assess_understanding", END)

    return workflow.compile()


assessment_graph = build_assessment_graph()


class AssessmentFailed(Exception):
    """「理解评估」分支失败。

    并行化之后评估与检索的异常会在同一处冒出来，若不分类，检索出错也会被
    当成「模型不可用」报 502（detail 会误导排查方向）。这类只包评估分支。
    真实原因在 `__cause__` 里，别只看这条消息。
    """


async def run_assessment_with_parallel(
    state: TutorState,
    build_parallel: Callable[[str], Awaitable[Any]],
) -> tuple[dict, Any]:
    """**分步**跑评估链路，让「理解评估」与一段外部工作并发（O3，2026-10-27）。

    原链路是 `assessment_graph.ainvoke` 一次调用，两个节点串行；而 `/chat` 的
    知识库检索排在评估**之后**且只依赖第一个节点的产出（topic）。于是关键路径上是
    `抽 topic → 理解评估 → 检索` 三段相加。

    本函数把它拆成：`抽 topic → gather(理解评估, build_parallel(topic))`。

    **收益是 min(理解评估, 并行段)，不是理解评估全程**——并行后耗时由较长的一侧决定，
    较长那侧省不掉。实测见 `scripts/measure_ttft.py` 的前后对比。

    `build_parallel` 收到的是**已确定的 topic**（"unknown" 会被归一成空串，
    与 app.py 拼查询串的口径一致）。它与 `assess_understanding_node` 之间
    **没有数据依赖**：后者只读 topic / topic_changed / hint_level / messages，
    不读任何检索产物。

    异常处理（**刻意区分来源**，否则并行会把两种失败混成一种）：
      - 评估分支失败 → 抛 `AssessmentFailed`（调用方照旧转 502）；
      - 并行分支失败 → 原样抛出（调用方照旧转 500）。
    两分支都失败时以评估为准——原串行链路里评估先跑，也是它先失败。
    任一分支失败时取消尚未结束的另一分支，避免留下孤儿任务。

    `assessment_graph` 保留不动——测试与编排层仍在用它，两者行为必须等价。
    """
    topic_patch = await extract_topic_node(state)
    after_topic = {**state, **(topic_patch or {})}

    topic = after_topic.get("topic") or ""
    assess_task = asyncio.create_task(assess_understanding_node(after_topic))
    parallel_task = asyncio.create_task(build_parallel(topic))
    try:
        assess_result, parallel_result = await asyncio.gather(
            assess_task, parallel_task, return_exceptions=True
        )
    except BaseException:
        # gather 自身被中断（取消 / KeyboardInterrupt）：两个分支都要收干净。
        for task in (assess_task, parallel_task):
            if not task.done():
                task.cancel()
        raise

    if isinstance(assess_result, BaseException):
        raise AssessmentFailed("assess_understanding 节点失败") from assess_result
    if isinstance(parallel_result, BaseException):
        raise parallel_result

    return {**after_topic, **(assess_result or {})}, parallel_result


async def _cancel_all(*tasks: Any) -> None:
    """取消并回收任务，吞掉 CancelledError（取消是预料中的，不是故障）。"""
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def run_assessment_speculative(
    state: TutorState,
    build_parallel: Callable[[str], Awaitable[Any]],
) -> tuple[dict, Any]:
    """投机执行：不等抽 topic，先拿「上一轮 topic」并行跑评估与检索（O12，2026-10-28）。

    为什么不是「缓存 topic」
    ------------------------
    XB-31 实测过盲缓存（跳过抽取、直接沿用上一轮 topic），结论是否决：
    换题轮上用旧 topic 检索，93% 会退化（注入 4 条里 2 条是旧知识点），
    13% 期望文档完全丢失；更严重的是 topic 会落库并被下一轮
    `load_last_state` 读回，还喂给 `_update_profile` 记 mastery
    ⇒ **一次误判跨轮累积，且从回答文本上看不出来**。

    投机执行拿到的**延迟收益与盲缓存完全相同**（都是省掉 E 那一段），
    但正确性零损失：E 仍然是唯一裁决者，它说沿用就直接采用投机结果，
    它说换题就把投机产物作废重跑。

    关键路径变化
    ------------
        原：    E → gather(A, R)          = E + max(A, R)
        投机：  gather(E, A(prev), R(prev)) = max(E, A, R)

    命中（追问）时省掉 E 全程；未命中（换题）时耗时与原路径持平
    —— 因为 E 与 A、R 同时在跑，E 先结束就能立刻作废重跑，没有多等。

    代价与风险
    ----------
    1. 未命中时上一次评估调用会被中途取消。是否计费取决于厂商，
       换 provider 前要确认；不放心就把 `CHAT_SPECULATE_TOPIC=0`。
    2. `build_parallel` 会被调用**两次**（投机一次、确认后一次），
       因此它必须是**只读且幂等**的。当前 `_retrieve_all` 走
       `asyncio.to_thread` 只做检索，满足要求；将来若改成带写入的
       编排，必须先在这里加保护。
    3. Python 线程无法被真正取消：投机检索如果已经在 `to_thread`
       里跑起来，取消后线程仍会跑完（结果被丢弃）。这是 CPU 浪费，
       不是正确性问题——换题时会有一次无用的重排开销。

    只有上一轮 topic 已知时才投机；首轮或 unknown 走原分步路径。
    """
    prev_topic = (state.get("topic") or "").strip()
    if not prev_topic or prev_topic == "unknown":
        return await run_assessment_with_parallel(state, build_parallel)

    topic_task = asyncio.create_task(extract_topic_node(state))
    spec_assess = asyncio.create_task(assess_understanding_node(state))
    spec_retrieve = asyncio.create_task(build_parallel(prev_topic))

    try:
        topic_patch = await topic_task
    except BaseException:
        # 抽 topic 失败：投机产物建立在「可能是错的 topic」上，一并作废。
        await _cancel_all(spec_assess, spec_retrieve)
        raise

    after_topic = {**state, **(topic_patch or {})}
    new_topic = (after_topic.get("topic") or "").strip()
    reused = not topic_patch or new_topic == prev_topic

    if reused:
        assess_result, retrieve_result = await asyncio.gather(
            spec_assess, spec_retrieve, return_exceptions=True
        )
        if isinstance(assess_result, BaseException):
            await _cancel_all(spec_retrieve)
            raise AssessmentFailed("assess_understanding 节点失败") from assess_result
        if isinstance(retrieve_result, BaseException):
            raise retrieve_result
        logger.info("投机执行命中，沿用 topic=%r（省掉抽 topic 等待）", prev_topic)
        return {**after_topic, **(assess_result or {})}, retrieve_result

    # 换题：投机产物作废。
    # 这里**故意不检查 spec 分支的异常**——它的结果本来就要丢，
    # 把它失败报上去会让一次正常的换题变成 502 误报。
    await _cancel_all(spec_assess, spec_retrieve)
    logger.info(
        "投机执行未命中，topic %r → %r，作废重跑", prev_topic, new_topic
    )

    query_topic = "" if new_topic == "unknown" else new_topic
    assess_task = asyncio.create_task(assess_understanding_node(after_topic))
    parallel_task = asyncio.create_task(build_parallel(query_topic))
    assess_result, parallel_result = await asyncio.gather(
        assess_task, parallel_task, return_exceptions=True
    )

    if isinstance(assess_result, BaseException):
        await _cancel_all(parallel_task)
        raise AssessmentFailed("assess_understanding 节点失败") from assess_result
    if isinstance(parallel_result, BaseException):
        raise parallel_result

    return {**after_topic, **(assess_result or {})}, parallel_result
