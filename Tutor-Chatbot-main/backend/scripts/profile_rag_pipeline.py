"""RAG 全链路分段剖析（2026-10-27 系统性调优用）。

目的：把「端到端慢 / 答案不准」拆到具体阶段上，避免凭印象调参。
量的是**检索链路内部**的每一段（embed / 路由 / 向量 / BM25 / 融合 / 重排 /
格式化），外加注入体积——成本与延迟两头都要有数。

用法：
    RAG_PARENT_CHILD=1 ./venv/Scripts/python.exe scripts/profile_rag_pipeline.py
    RAG_PARENT_CHILD=1 ./venv/Scripts/python.exe scripts/profile_rag_pipeline.py --repeat 3

输出：每段的 p50 / p95 均值，以及注入字符数与估算 token 数。
退出码恒为 0（这是量具，不做门禁；门禁脚本是 diag_floor_margin.py）。
"""

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.rag import retriever  # noqa: E402
from agent.rag.indexer import load_knowledge_index  # noqa: E402


# 覆盖各主题 + 不同问法长度的探针。故意混入口语追问与术语直问，
# 因为这两类的候选池规模与重排耗时差别很大。
PROBES = [
    ("B+ 树索引为什么快？", "数据库索引"),
    ("进程和线程有什么区别？", "进程与线程"),
    ("TCP 三次握手为什么是三次？", "TCP 三次握手"),
    ("死锁怎么预防？", "死锁"),
    ("快排的时间复杂度是多少？", ""),
    ("HTTPS 和 HTTP 有什么不同？", "HTTPS 与 HTTP"),
    ("TIME_WAIT 为什么要等那么久？", "TCP 连接管理"),
    ("那怎么预防呢？", "死锁"),
    ("它有什么缺点？", "页面置换 LRU"),
    ("为什么？", "进程调度"),
    ("索引失效有哪些情况？", "数据库索引"),
    ("虚拟内存是什么？", "内存管理"),
    ("冒泡排序稳定吗？", "排序算法"),
    ("事务的隔离级别有哪些？", "数据库事务"),
    ("拥塞控制和流量控制有什么区别？", "TCP 拥塞控制"),
    ("什么是死锁？", "死锁"),
    ("哈希表冲突怎么解决？", "哈希表"),
    ("线程同步有哪些方式？", "线程同步"),
    ("DNS 解析过程是怎样的？", "DNS"),
    ("SELECT * 为什么不推荐？", "SQL 查询"),
]


def pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round((len(ordered) - 1) * q)))
    return ordered[index]


def profile_once(store, query: str, topic: str) -> dict:
    """跑一次完整检索，把各段耗时拆出来。

    分段靠 trace（路由/重排已有埋点）+ 外层手动计时（向量/BM25 只能整段量，
    因为它们在 retrieve_knowledge_docs 内部，不逐段对外暴露）。
    """
    row: dict = {"query": query, "topic": topic}

    started = time.perf_counter()
    trace: dict = {}
    docs = retriever.retrieve_knowledge_docs(store, query, topic=topic, trace=trace)
    row["total_ms"] = round((time.perf_counter() - started) * 1000)

    row["router_ms"] = trace.get("router_ms", 0)
    row["rerank_ms"] = trace.get("rerank_ms", 0)
    row["rerank"] = trace.get("rerank", "?")
    row["coarse_k"] = trace.get("coarse_k", 0)
    row["bm25_k"] = trace.get("bm25_k", 0)
    row["fused_k"] = trace.get("fused_k", 0)
    row["parents_k"] = trace.get("parents_k", 0)
    row["final_k"] = trace.get("final_k", 0)
    row["router_docs"] = len(trace.get("router_docs") or [])
    row["router_applied"] = bool(trace.get("router_applied"))
    row["top_score"] = trace.get("relevance_top_score")

    text = retriever.format_knowledge_docs(docs)
    row["chars"] = len(text)
    # 中文按 1.5 token/字 粗估（中英混排会偏低，只用来看量级与横向比较）
    row["est_tokens"] = round(len(text) * 0.75)
    row["sources"] = [
        str(d.metadata.get("source", "")).replace("cs/", "") for d in docs
    ]
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=2, help="每个探针跑几轮（取最后一轮避免预热污染）")
    ap.add_argument("--json", default="", help="把明细写成 JSON 供后续对比")
    args = ap.parse_args()

    store = load_knowledge_index(persist_directory="chroma_kb")
    if store is None:
        print("知识库不存在（chroma_kb），先跑 build_kb.py")
        return 1

    try:
        n_chunks = store._collection.count()
    except Exception:
        n_chunks = 0
    print(f"库切片数 {n_chunks}｜父子={retriever.CONFIG.parent_child}｜"
          f"重排={retriever.CONFIG.enable_rerank}｜混合={retriever.CONFIG.enable_hybrid}")
    print(f"探针 {len(PROBES)} 条 × {args.repeat} 轮\n")

    rows: list[dict] = []
    for query, topic in PROBES:
        last = None
        for _ in range(args.repeat):
            last = profile_once(store, query, topic)
        rows.append(last)

    def stats(key: str) -> tuple[float, float, float]:
        vals = [float(r.get(key) or 0) for r in rows]
        return statistics.mean(vals), pct(vals, 0.5), pct(vals, 0.95)

    print("阶段耗时（ms）：mean / p50 / p95")
    print("-" * 58)
    for key, label in [
        ("total_ms", "检索端到端"),
        ("router_ms", "  文档路由"),
        ("rerank_ms", "  重排"),
    ]:
        mean, p50, p95 = stats(key)
        print(f"{label:<12} {mean:8.1f} {p50:8.1f} {p95:8.1f}")

    total_mean, _, _ = stats("total_ms")
    r_mean, _, _ = stats("rerank_ms")
    router_mean, _, _ = stats("router_ms")
    other = total_mean - r_mean - router_mean
    print(f"{'  其余(embed+召回+融合+格式化)':<12} {other:8.1f}")
    if total_mean:
        print(f"重排占端到端 {r_mean / total_mean:.1%}，路由占 {router_mean / total_mean:.1%}")

    print("\n注入体积：")
    chars = [r["chars"] for r in rows]
    toks = [r["est_tokens"] for r in rows]
    print(f"  字符 mean {statistics.mean(chars):.0f} / p95 {pct(chars, 0.95):.0f} / max {max(chars)}")
    print(f"  估算 token mean {statistics.mean(toks):.0f} / p95 {pct(toks, 0.95):.0f} / max {max(toks)}")

    print("\n候选池与注入条数：")
    for key in ("coarse_k", "bm25_k", "fused_k", "parents_k", "final_k"):
        vals = [int(r.get(key) or 0) for r in rows]
        print(f"  {key:<10} mean {statistics.mean(vals):.1f} min {min(vals)} max {max(vals)}")

    zero = [r for r in rows if r["final_k"] == 0]
    print(f"\n零注入探针 {len(zero)} 条" + ("" if not zero else "："))
    for r in zero:
        print(f"  {r['query']!r} topic={r['topic']!r} top={r['top_score']}")

    print("\n明细（前 8 条）：")
    for r in rows[:8]:
        print(f"  {r['query'][:22]:<24} {r['total_ms']:>6}ms "
              f"重排{r['rerank_ms']:>5}ms 注入{r['final_k']}条/{r['chars']}字 "
              f"候选{r['fused_k']} {r['sources']}")

    if args.json:
        Path(args.json).write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n明细已写入 {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
