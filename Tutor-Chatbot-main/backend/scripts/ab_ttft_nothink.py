"""端到端 A/B：抽 topic 关掉思考，前置段（评估+检索）能降多少（XB-32）。

节点级 A/B（`ab_extract_prompt.py`）只能证明「抽取节点自己快了」，证明不了
「端到端前置段真的降了」——前置段是 `E + max(A, R)`，E 省下的时间要看它是否
真的在关键路径上。所以这里必须回到真实 /chat 的 SSE 上量。

**冷启动必须单独处理**：后端重启后第一条请求要给 Chroma 加载 + BM25 语料构建
（248 片）买单，实测第一条 6501ms、之后 2356/2547ms。所以本脚本先跑一条
**预热请求并丢弃**，只统计预热之后的样本。

开关是进程级环境变量（`RAG_TOPIC_NO_THINK`），所以两组必须**各起一次后端**：

    # A 组（开）
    RAG_TOPIC_NO_THINK=1 ./venv/Scripts/python.exe -m uvicorn app:app --port 8000
    ./venv/Scripts/python.exe scripts/ab_ttft_nothink.py --label 开

    # B 组（关）
    RAG_TOPIC_NO_THINK=0 ./venv/Scripts/python.exe -m uvicorn app:app --port 8000
    ./venv/Scripts/python.exe scripts/ab_ttft_nothink.py --label 关

用法：./venv/Scripts/python.exe scripts/ab_ttft_nothink.py --label 开 --n 8
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="?")
    ap.add_argument("--n", type=int, default=8)
    ap.add_argument("--provider", default="openai")
    args = ap.parse_args()

    queries = (DEFAULT_QUERIES * ((args.n // len(DEFAULT_QUERIES)) + 1))[: args.n]

    # 预热：重启后的第一条要给 Chroma 加载 + BM25 语料构建买单，不计入统计
    try:
        warm = sse_stream(queries[0], args.provider)
        print("预热（不计入）：%dms\n" % warm["pre_ms"])
    except Exception as exc:
        print("预热失败：%s: %s" % (type(exc).__name__, exc))

    rows = []
    for q in queries:
        try:
            rows.append(sse_stream(q, args.provider))
        except Exception as exc:
            print("请求失败 %r: %s: %s" % (q, type(exc).__name__, exc))

    for r in rows:
        print(
            "  %-22s 前置 %5dms  首字 %5dms  生成段 %5dms  资料 %s 条"
            % (r["query"][:22], r["pre_ms"], r["ttft_ms"], r["gen_ms"], r["n_sources"])
        )

    if not rows:
        return 1
    pre = [r["pre_ms"] for r in rows]
    ttft = [r["ttft_ms"] for r in rows]
    print(
        "\n【%s】n=%d  前置 mean=%dms  中位=%dms  p95=%dms  ｜  "
        "TTFT mean=%dms  中位=%dms"
        % (
            args.label,
            len(rows),
            statistics.mean(pre),
            statistics.median(pre),
            sorted(pre)[max(0, int(len(pre) * 0.95) - 1)],
            statistics.mean(ttft),
            statistics.median(ttft),
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
