#!/usr/bin/env python
"""节点级配对 A/B：投机执行（O12）相对分步并行（O3）省多少（2026-10-28）。

为什么不用端到端当主证据
------------------------
端到端 `/chat` 的方差太大：同一组 6 条第二轮请求，前置段从 2667ms 到 6823ms，
抖动 ±1.5s，而投机执行的预期收益只有 ~0.66s（就是抽 topic 那一段）。
n=6 的分辨力根本不够，测出来的是噪声。

这里改成**单进程内配对对照**：同一条输入，交替跑两条代码路径，多轮取差值。
两次调用挨在一起，网络与服务端状态接近，配对能把大部分共同噪声消掉。

用法
----
    ./venv/Scripts/python.exe scripts/ab_speculative_node.py --rounds 3

    # 只测「换题」路径（未命中）应当持平，这是设计目标而非失败
    ./venv/Scripts/python.exe scripts/ab_speculative_node.py --only miss
"""
from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402

from agent.graph import (  # noqa: E402
    get_llm,
    run_assessment_speculative,
    run_assessment_with_parallel,
)
from agent.rag.indexer import load_knowledge_index  # noqa: E402
from agent.rag.retriever import retrieve_knowledge_docs  # noqa: E402

# 与 app.py 保持同一套常量（那里是 @lru_cache 的单例，
# 这里只是测量脚本，自己加载一份即可）
KNOWLEDGE_PERSIST_DIR = os.getenv(
    "KNOWLEDGE_PERSIST_DIR", str(BACKEND_DIR / "chroma_kb")
)
KNOWLEDGE_COLLECTION = os.getenv("KNOWLEDGE_COLLECTION", "cs_basics")

# (第一轮主问题, 第二轮追问)。追问刻意不含主题词——这正是投机要覆盖的场景。
PAIRS: list[tuple[str, str]] = [
    ("什么是死锁？", "那怎么预防呢？"),
    ("TCP 三次握手为什么是三次？", "为什么是这样？"),
    ("进程和线程有什么区别？", "能举个例子吗？"),
    ("B+ 树索引为什么快？", "那什么时候会失效？"),
    ("事务隔离级别有哪些？", "RR 和 RC 差在哪？"),
]

# 换题对照组：第二轮转向完全不同的知识点，投机必然未命中
MISS_PAIRS: list[tuple[str, str]] = [
    ("什么是死锁？", "那 TCP 和 UDP 有什么区别？"),
    ("进程和线程有什么区别？", "讲讲虚拟内存吧"),
    ("B+ 树索引为什么快？", "快排的时间复杂度是多少？"),
]

FIRST_ANSWER = "这是该知识点的标准讲解，包含定义、要点与一个例子。"


def _state(topic: str, q1: str, q2: str, llm) -> dict:
    return {
        "messages": [
            HumanMessage(content=q1),
            AIMessage(content=FIRST_ANSWER),
            HumanMessage(content=q2),
        ],
        "topic": topic,
        "hint_level": 1,
        "misconception": "",
        "resolved": False,
        "llm": llm,
        "topic_changed": False,
    }


def _make_builder(store, query: str):
    """返回 (builder, calls)。

    `calls` 记录了并行段实际被调用几次，用来**判定这条样本走的是命中还是未命中**
    —— 命中时投机产物被直接采用，只调 1 次；未命中时作废重跑，调 2 次。
    这样分类不依赖日志、也不用多跑一次 LLM。
    """
    calls: list[str] = []

    async def build(topic: str):
        calls.append(topic)
        docs = await asyncio.to_thread(
            retrieve_knowledge_docs, store, query, topic=topic
        )
        return [d for d in docs]

    return build, calls


async def run(rounds: int, pause: float, only: str) -> int:
    llm = get_llm("openai")
    store = load_knowledge_index(KNOWLEDGE_COLLECTION, KNOWLEDGE_PERSIST_DIR)
    # 预热一次，把 Chroma 加载 + BM25 语料构建的开销挪到计时之外
    await asyncio.to_thread(retrieve_knowledge_docs, store, "预热", topic="")

    pairs = PAIRS if only == "hit" else MISS_PAIRS if only == "miss" else PAIRS + MISS_PAIRS

    step_ms: list[float] = []
    spec_ms: list[float] = []
    diffs: list[float] = []
    hit_ms: list[tuple[float, float]] = []
    miss_ms: list[tuple[float, float]] = []

    for r in range(rounds):
        seq = pairs if r % 2 == 0 else list(reversed(pairs))
        for q1, q2 in seq:
            topic = q1.replace("什么", "").replace("？", "").strip("是 ")
            step_builder, _ = _make_builder(store, q2)
            spec_builder, calls = _make_builder(store, q2)

            t0 = time.perf_counter()
            await run_assessment_with_parallel(
                _state(topic, q1, q2, llm), step_builder
            )
            a = (time.perf_counter() - t0) * 1000

            t0 = time.perf_counter()
            await run_assessment_speculative(
                _state(topic, q1, q2, llm), spec_builder
            )
            b = (time.perf_counter() - t0) * 1000

            # 1 次 = 命中（沿用上一轮 topic）；2 次 = 未命中（作废后重跑）
            bucket = hit_ms if len(calls) == 1 else miss_ms
            bucket.append((a, b))
            step_ms.append(a)
            spec_ms.append(b)
            diffs.append(a - b)
            print(
                "  %-12s→%-16s 分步 %5.0fms  投机 %5.0fms  差 %+6.0fms  %s"
                % (
                    q1[:12], q2[:16], a, b, a - b,
                    "命中" if len(calls) == 1 else "未命中",
                )
            )
            if pause:
                await asyncio.sleep(pause)

    print()
    print("=== 汇总（n=%d）===" % len(diffs))
    print(
        "  分步 mean %.0fms 中位 %.0fms" % (statistics.mean(step_ms), statistics.median(step_ms))
    )
    print(
        "  投机 mean %.0fms 中位 %.0fms" % (statistics.mean(spec_ms), statistics.median(spec_ms))
    )
    print(
        "  配对差 mean %+.0fms  中位 %+.0fms  标准差 %.0fms"
        % (statistics.mean(diffs), statistics.median(diffs), statistics.pstdev(diffs))
    )
    saved = statistics.mean(diffs) / statistics.mean(step_ms) * 100
    print("  相对节省 %.1f%%" % saved)
    pos = sum(1 for d in diffs if d > 0)
    print("  投机更快的配对：%d/%d" % (pos, len(diffs)))

    for label, bucket in (("命中（追问沿用）", hit_ms), ("未命中（换题作废）", miss_ms)):
        if not bucket:
            continue
        s = [x[0] for x in bucket]
        p = [x[1] for x in bucket]
        d = [x[0] - x[1] for x in bucket]
        print("\n--- %s  n=%d ---" % (label, len(bucket)))
        print("  分步 mean %.0fms 中位 %.0fms" % (statistics.mean(s), statistics.median(s)))
        print("  投机 mean %.0fms 中位 %.0fms" % (statistics.mean(p), statistics.median(p)))
        print(
            "  配对差 mean %+.0fms  中位 %+.0fms  ｜投机更快 %d/%d"
            % (
                statistics.mean(d),
                statistics.median(d),
                sum(1 for x in d if x > 0),
                len(d),
            )
        )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--pause", type=float, default=0.8)
    ap.add_argument("--only", choices=("all", "hit", "miss"), default="all")
    args = ap.parse_args()
    return asyncio.run(run(args.rounds, args.pause, args.only))


if __name__ == "__main__":
    sys.exit(main())
