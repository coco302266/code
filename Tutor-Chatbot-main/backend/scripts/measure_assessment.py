"""评估链路耗时测量（2026-10-27 系统性调优用）。

/chat 在检索**之前**串行跑两次 LLM：extract_topic → assess_understanding。
这两次是首字延迟里最大的一块（实测前置段 7.7s，其中检索只占约 2s），
但一直没有人单独量过它们——所以要拆开：topic 抽取多少、理解评估多少。

用法（需要 .env 里配好 LLM_API_KEY / LLM_BASE_URL）：
    ./venv/Scripts/python.exe scripts/measure_assessment.py
    ./venv/Scripts/python.exe scripts/measure_assessment.py --provider openai --n 3
"""

import argparse
import asyncio
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402

from agent.graph import (  # noqa: E402
    assess_understanding_node,
    extract_topic_node,
    get_llm,
)

PROBES = [
    ("B+ 树索引为什么快？", ""),
    ("死锁怎么预防？", "死锁"),
    ("那它和进程调度有关系吗？", "死锁"),
]


async def measure(llm, message: str, topic: str) -> dict:
    state = {
        "messages": [
            HumanMessage(content="什么是死锁？"),
            AIMessage(content="死锁是多个进程互相等待对方持有的资源。"),
            HumanMessage(content=message),
        ],
        "topic": topic,
        "hint_level": 0,
        "misconception": "",
        "resolved": False,
        "llm": llm,
        "topic_changed": False,
    }
    started = time.perf_counter()
    t1 = dict(await extract_topic_node(dict(state)))
    t_topic = time.perf_counter() - started

    merged = dict(state)
    merged.update(t1)
    started = time.perf_counter()
    await assess_understanding_node(merged)
    t_assess = time.perf_counter() - started

    return {
        "message": message,
        "topic_ms": round(t_topic * 1000),
        "assess_ms": round(t_assess * 1000),
        "extracted": t1.get("topic", "（沿用）"),
    }


async def main_async(provider: str, n: int) -> int:
    llm = get_llm(provider)
    rows = []
    for _ in range(n):
        for message, topic in PROBES:
            try:
                rows.append(await measure(llm, message, topic))
            except Exception as exc:
                print(f"失败 {message!r}: {type(exc).__name__}: {exc}")
    for r in rows:
        print(f"{r['message'][:20]:<22} 抽topic {r['topic_ms']:>6}ms "
              f"评估 {r['assess_ms']:>6}ms 抽出={r['extracted']}")
    if len(rows) > 1:
        t = [r["topic_ms"] for r in rows]
        a = [r["assess_ms"] for r in rows]
        print(f"\n抽 topic mean {statistics.mean(t):.0f}ms｜评估 mean "
              f"{statistics.mean(a):.0f}ms｜合计 mean "
              f"{statistics.mean(t) + statistics.mean(a):.0f}ms")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="openai")
    ap.add_argument("--n", type=int, default=2)
    args = ap.parse_args()
    return asyncio.run(main_async(args.provider, args.n))


if __name__ == "__main__":
    sys.exit(main())
