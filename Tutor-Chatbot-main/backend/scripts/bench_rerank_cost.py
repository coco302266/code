"""重排模型成本基准：冷加载时间 / 进程内存(RSS) / 重排延迟分位 / 吞吐。

一次只测一个模型（单进程，保证内存干净），两个模型各跑一次后对比。
用法：
  FASTEMBED_CACHE_PATH=<abs>/.model_cache python scripts/bench_rerank_cost.py \
      --model BAAI/bge-reranker-v2-m3-int8 --tag BGE --out eval/reports/bench_BGE.json

测量口径：
  - load_ms      ：TextCrossEncoder 实例化耗时（含读 onnx 进内存）
  - rss_mb       ：进程工作集，load 前后各取一次，delta 即模型驻留
  - latency_ms   ：每次 rerank(query, ~4 docs) 的墙钟耗时，取 p50/p90/p95/p99/max
  - throughput   ：docs/秒（总文档数 / 总重排耗时）
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class _PMC(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.wintypes.DWORD),
        ("PageFaultCount", ctypes.wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def rss_mb() -> float:
    """当前进程工作集(MB)。ctypes 在部分 64 位 Python 上返回 0，改用 tasklist 兜底。"""
    try:
        import os
        import re
        import subprocess

        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {os.getpid()}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
        ).stdout
        m = re.search(r'"([\d,]+) K"', out)
        if m:
            return int(m.group(1).replace(",", "")) / 1000.0
    except Exception:
        pass
    c = _PMC()
    c.cb = ctypes.sizeof(c)
    ctypes.windll.kernel32.K32GetProcessMemoryInfo(
        ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(c), c.cb
    )
    return c.WorkingSetSize / 1e6


def pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    k = (len(s) - 1) * (p / 100.0)
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def load_workload(max_queries: int = 40, docs_per_query: int = 4):
    """取真实 query + 真实知识库切片文本，构造贴近线上的 (query, [docs]) 负载。"""
    import json as _json

    queries: list[str] = []
    try:
        cases = _json.load(open(ROOT / "rag_eval_cases.json", encoding="utf-8"))["cases"]
        queries = [c["query"] for c in cases if c.get("query")][:max_queries]
    except Exception:
        pass
    if not queries:
        queries = ["什么是死锁", "进程和线程的区别", "虚拟内存的作用", "TCP和UDP的区别"]

    docs: list[str] = []
    try:
        from agent.rag.indexer import load_knowledge_index

        store = load_knowledge_index("cs_basics", "chroma_kb")
        all_docs = store._collection.get(include=["documents"])["documents"]
        all_docs = [d for d in all_docs if d and 120 <= len(d) <= 400]
        docs = all_docs[: max(docs_per_query * 3, 60)]
    except Exception as e:  # 兜底：合成 ~250 字中文段落
        print(f"[warn] 知识库加载失败({e})，改用合成文档", file=sys.stderr)
        base = (
            "操作系统通过进程调度器为就绪进程分配处理器时间，常见策略包括先来先服务、"
            "短作业优先、时间片轮转与多级反馈队列。时间片轮转将处理器时间划分为固定长度"
            "的时间片，轮流分配给就绪队列中的每个进程，从而在分时系统中提供良好的响应性。"
        )
        docs = [base[:250]] * (docs_per_query * 3)

    if not docs:
        docs = ["死锁是指两个或多个进程互相等待对方释放资源而无法继续推进的状态。" * 3]

    pairs = []
    for i, q in enumerate(queries):
        grp = [docs[(i * docs_per_query + j) % len(docs)] for j in range(docs_per_query)]
        pairs.append((q, grp))
    return pairs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--tag", default="")
    ap.add_argument("--warmup", type=int, default=2)
    ap.add_argument("--repeat", type=int, default=1, help="整套负载重复次数（取全部样本）")
    ap.add_argument("--batch", type=int, default=1)
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)

    from fastembed.rerank.cross_encoder import TextCrossEncoder

    pairs = load_workload()
    n_docs = sum(len(d) for _, d in pairs)
    print(f"[{args.tag}] model={args.model} queries={len(pairs)} docs={n_docs}")

    rss0 = rss_mb()
    t0 = time.perf_counter()
    enc = TextCrossEncoder(model_name=args.model)
    load_ms = (time.perf_counter() - t0) * 1000.0
    rss1 = rss_mb()

    # warmup
    for q, docs in pairs[: args.warmup]:
        list(enc.rerank(q, docs, batch_size=args.batch))

    lat: list[float] = []
    for _ in range(args.repeat):
        for q, docs in pairs:
            t = time.perf_counter()
            list(enc.rerank(q, docs, batch_size=args.batch))
            lat.append((time.perf_counter() - t) * 1000.0)

    total_s = sum(lat) / 1000.0
    result = {
        "tag": args.tag,
        "model": args.model,
        "queries": len(pairs),
        "documents": n_docs,
        "docs_per_query": round(n_docs / max(len(pairs), 1), 2),
        "load_ms": round(load_ms, 1),
        "rss_before_mb": round(rss0, 1),
        "rss_after_mb": round(rss1, 1),
        "rss_delta_mb": round(rss1 - rss0, 1),
        "lat_mean_ms": round(statistics.mean(lat), 1),
        "lat_p50_ms": round(pct(lat, 50), 1),
        "lat_p90_ms": round(pct(lat, 90), 1),
        "lat_p95_ms": round(pct(lat, 95), 1),
        "lat_p99_ms": round(pct(lat, 99), 1),
        "lat_max_ms": round(max(lat), 1),
        "throughput_docs_per_s": round(n_docs * args.repeat / total_s, 2),
        "samples": len(lat),
    }
    print("RESULT " + json.dumps(result, ensure_ascii=False))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print("报告已写入", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
