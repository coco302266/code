#!/usr/bin/env python
"""端到端 A/B：投机执行（O12）在**追问轮**能省多少（2026-10-28）。

为什么必须按「对话对」测
------------------------
投机执行只在**有上一轮 topic 可猜**时才启动。第一轮没有 prev_topic，
代码会直接走 O3 的分步路径——所以拿单轮探针测，两组数字必然一样，
什么也测不出来。必须同一课堂连续发两轮，只统计**第二轮**。

测的是什么
----------
第二轮的关键路径：
    分步（O3）：E + max(A, R)
    投机（O12）：max(E, A, R)      ← 命中时

所以收益 ≈ E（抽 topic 那一段），未命中时两组应当**持平**（这是设计目标，
不是失败迹象——取消投机产物后立即用新 topic 重跑，没有多等）。

⚠ 读数纪律：前置段（到 sources 事件）才是 O3/O12 的直接证据。
   TTFT 还包含生成段，而这两项改动都不碰生成，别把那部分算进来。

开关是进程级环境变量，两组必须**各起一次后端**：

    # A 组（投机开）
    CHAT_SPECULATE_TOPIC=1 ./venv/Scripts/python.exe -m uvicorn app:app --port 8000
    ./venv/Scripts/python.exe scripts/ab_speculative.py --label 投机开

    # B 组（投机关，退回 O3）
    CHAT_SPECULATE_TOPIC=0 ./venv/Scripts/python.exe -m uvicorn app:app --port 8000
    ./venv/Scripts/python.exe scripts/ab_speculative.py --label 投机关

用法：./venv/Scripts/python.exe scripts/ab_speculative.py --label 投机开 --n 6
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(BACKEND_DIR / "scripts"))

from measure_ttft import DEFAULT_QUERIES, sse_stream  # noqa: E402

# 每个主问题配一个省略式追问（"那…呢？""为什么？"这类）。
# 追问本身不含主题词，正是投机执行要覆盖的场景。
FOLLOWUPS = [
    "那怎么预防呢？",
    "为什么是这样？",
    "能举个例子吗？",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="?")
    ap.add_argument("--n", type=int, default=6, help="对话对数量")
    ap.add_argument("--pause", type=float, default=7.0, help="轮间间隔秒（限流 10/min）")
    ap.add_argument("--provider", default="openai")
    args = ap.parse_args()

    pairs = []
    for i in range(args.n):
        pairs.append((DEFAULT_QUERIES[i % len(DEFAULT_QUERIES)], FOLLOWUPS[i % len(FOLLOWUPS)]))

    def run_pair(q1: str, q2: str) -> dict | None:
        first = sse_stream(q1, args.provider)
        time.sleep(args.pause)
        second = sse_stream(
            q2,
            args.provider,
            history=[
                {"role": "user", "content": q1},
                {"role": "assistant", "content": first["answer"]},
            ],
            topic=first["topic"],
        )
        return {
            "q1": q1,
            "q2": q2,
            "t1": first["topic"],
            "t2": second["topic"],
            "pre_ms": second["pre_ms"],
            "ttft_ms": second["ttft_ms"],
            "gen_ms": second["gen_ms"],
            "n_sources": second["n_sources"],
        }

    # 预热：重启后第一条要给 Chroma 加载 + BM25 语料构建买单
    try:
        warm = run_pair(*pairs[0])
        print("预热（不计入）：第二轮前置 %dms topic=%r\n" % (warm["pre_ms"], warm["t2"]))
    except Exception as exc:
        print("预热失败：%s: %s" % (type(exc).__name__, exc))

    rows = []
    for q1, q2 in pairs:
        try:
            row = run_pair(q1, q2)
        except Exception as exc:
            print("请求失败 %r: %s: %s" % (q2, type(exc).__name__, exc))
            continue
        rows.append(row)
        same = "沿用✓" if row["t1"] == row["t2"] else "换题✗ %r→%r" % (row["t1"], row["t2"])
        print(
            "  %-14s→%-12s 前置 %5dms  首字 %5dms  生成段 %5dms  资料 %s 条  %s"
            % (row["q1"][:14], row["q2"][:12], row["pre_ms"], row["ttft_ms"],
               row["gen_ms"], row["n_sources"], same)
        )

    if not rows:
        return 1
    pre = [r["pre_ms"] for r in rows]
    ttft = [r["ttft_ms"] for r in rows]
    gen = [r["gen_ms"] for r in rows]
    print(
        "\n【%s】n=%d  前置 mean=%dms 中位=%dms p95=%dms  ｜  "
        "首字 mean=%dms 中位=%dms  ｜  生成段 mean=%dms"
        % (
            args.label, len(rows),
            statistics.mean(pre), statistics.median(pre),
            sorted(pre)[max(0, int(len(pre) * 0.95) - 1)],
            statistics.mean(ttft), statistics.median(ttft),
            statistics.mean(gen),
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
