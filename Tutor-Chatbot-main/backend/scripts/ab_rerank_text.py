"""重排输入文本 A/B：parent 小节全文 vs child 命中切片（2026-10-27）。

背景
----
`scripts/profile_rag_pipeline.py` 实测：检索端到端 mean 2149ms，其中**重排 2098ms
（97.7%）**，最长单条 9.5s。同时发现库内题被 `relevance_floor` 误杀——
「索引失效有哪些情况？」关掉下限后能命中 `db/index-transaction.md`（文档是对的），
但分数只有 -4.665，被 -2.0 判成库外、零注入。

两个现象同源：父子模式聚合后，重排读的是**整节 parent（600~900 字）**。
长序列既慢（交叉编码器成本随长度增长），又稀释语义（一节讲五六件事，
查询只问其中一件，逐 token 交互后平均下来分数就低）。

本脚本验证：改成**用命中的那片 child（~200 字）打分，顺序与注入仍用 parent**，
是不是「更快且更准」。同一进程切 `CONFIG.rerank_text`，跑同一批 83 条用例，量：

  - top1 命中（金标准）——唯一不能退的底线
  - 注入相关率——注入的资料有多少落在金标准沾边的文档上
  - 零注入条数（分库内/库外）——库内零注入 = 误杀，越少越好
  - 重排耗时与检索端到端 p95
  - top_score 分布——看下限余量是不是变宽了

用法（backend/ 下）：
    RAG_PARENT_CHILD=1 ./venv/Scripts/python.exe scripts/ab_rerank_text.py
    RAG_PARENT_CHILD=1 ./venv/Scripts/python.exe scripts/ab_rerank_text.py --floor -2.0

退出码：0 = child 组 top1 不低于 parent 且无库内误杀新增；1 = 有退化。
"""
import argparse
import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("RAG_PARENT_CHILD", "1")  # 必须在 import CONFIG 之前

from agent.rag.indexer import load_knowledge_index  # noqa: E402
from agent.rag import retriever  # noqa: E402

CASES_PATH = "rag_eval_cases.json"
INSCOPE_KINDS = {"in-kb", "kb-interview"}


def load_cases() -> list[dict]:
    with open(CASES_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else data.get("cases", data)


def norm(source: str) -> str:
    return (source or "").removeprefix("cs/")


def gold_sources(case: dict) -> set[str]:
    chunks = case.get("relevant_chunks") or []
    if not chunks:
        return set()
    best = max(float(c[2]) for c in chunks)
    return {norm(str(c[0])) for c in chunks if float(c[2]) == best and float(c[2]) > 0}


def related_sources(case: dict) -> set[str]:
    chunks = case.get("relevant_chunks") or []
    return {norm(str(c[0])) for c in chunks if float(c[2]) > 0}


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round((p / 100) * (len(ordered) - 1))))
    return ordered[idx]


def run(store, cases, text_mode: str) -> dict:
    retriever.CONFIG.rerank_text = text_mode
    hits = total = 0
    related_hits = related_total = 0
    zero_inscope = zero_oos = 0
    elapsed: list[float] = []
    rerank_ms: list[float] = []
    top_scores: list[float] = []
    injected = 0
    zero_examples: list[str] = []

    for case in cases:
        query = (case.get("query") or "").strip()
        kind = case.get("kind", "")
        gold = gold_sources(case)
        related = related_sources(case)
        started = time.perf_counter()
        trace: dict = {}
        docs = retriever.retrieve_knowledge_docs(store, query, topic="", trace=trace)
        elapsed.append(time.perf_counter() - started)
        if trace.get("rerank_ms"):
            rerank_ms.append(float(trace["rerank_ms"]))
        if trace.get("relevance_top_score") is not None:
            top_scores.append(float(trace["relevance_top_score"]))

        sources = [norm(str((d.metadata or {}).get("source", ""))) for d in docs]
        injected += len(docs)
        if related:
            related_total += len(docs)
            related_hits += sum(1 for s in sources if s in related)

        if kind in INSCOPE_KINDS and gold:
            total += 1
            if sources and sources[0] in gold:
                hits += 1

        if not docs:
            if kind in INSCOPE_KINDS:
                zero_inscope += 1
                zero_examples.append(f"{query}（库内，top={trace.get('relevance_top_score')}）")
            else:
                zero_oos += 1

    return {
        "mode": text_mode,
        "hits": hits, "total": total,
        "related_hits": related_hits, "related_total": related_total,
        "zero_inscope": zero_inscope, "zero_oos": zero_oos,
        "zero_examples": zero_examples,
        "injected": injected,
        "p50": _pct(elapsed, 50), "p95": _pct(elapsed, 95),
        "mean": statistics.mean(elapsed) if elapsed else 0,
        "rr_p50": _pct(rerank_ms, 50), "rr_p95": _pct(rerank_ms, 95),
        "rr_mean": statistics.mean(rerank_ms) if rerank_ms else 0,
        "score_mean": statistics.mean(top_scores) if top_scores else 0,
        "score_min": min(top_scores) if top_scores else 0,
        "score_below_floor": sum(1 for s in top_scores if s < (retriever.CONFIG.relevance_floor or -99)),
    }


def describe(tag: str, r: dict) -> None:
    print(f"\n[{tag}] rerank_text={r['mode']}")
    print(f"  top1 命中      {r['hits']}/{r['total']} = {r['hits'] / max(r['total'], 1):.1%}")
    print(f"  注入相关率     {r['related_hits']}/{r['related_total']} = "
          f"{r['related_hits'] / max(r['related_total'], 1):.1%}（总注入 {r['injected']} 条）")
    print(f"  零注入         库内 {r['zero_inscope']} 条 / 库外 {r['zero_oos']} 条")
    print(f"  重排耗时       mean {r['rr_mean']:.0f}ms / p50 {r['rr_p50']:.0f}ms / p95 {r['rr_p95']:.0f}ms")
    print(f"  检索端到端     mean {r['mean']:.3f}s / p50 {r['p50']:.3f}s / p95 {r['p95']:.3f}s")
    print(f"  top 分数       mean {r['score_mean']:.2f} / min {r['score_min']:.2f} "
          f"/ 低于下限 {r['score_below_floor']} 条")
    for example in r["zero_examples"][:6]:
        print(f"    零注入样例：{example}")


MODE_LABEL = {
    "parent": "parent 小节全文（原始行为）",
    "child": "child 命中切片（~200 字）",
    "child_title": "child 切片 + 小节标题（第三档）",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--floor", type=float, default=None,
                    help="临时改 relevance_floor（不传 = 用当前配置）")
    ap.add_argument("--modes", nargs="+", default=None,
                    choices=["parent", "child", "child_title"],
                    help="要比对的重排输入档位（默认三档全跑，parent 作基准）")
    args = ap.parse_args()

    store = load_knowledge_index(persist_directory="chroma_kb")
    if store is None:
        print("知识库不存在（chroma_kb）")
        return 1
    cases = load_cases()
    if args.floor is not None:
        retriever.CONFIG.relevance_floor = args.floor
    modes = args.modes or ["parent", "child", "child_title"]
    print(f"用例 {len(cases)} 条｜floor={retriever.CONFIG.relevance_floor}｜"
          f"父子={retriever.CONFIG.parent_child}｜档位 {modes}")

    results = {}
    for mode in modes:
        r = run(store, cases, mode)
        results[mode] = r
        describe(MODE_LABEL.get(mode, mode), r)

    base = results.get("parent")
    if base is None:
        # 没跑 parent 就以第一档作基准，只对耗时做相对比较
        base = results[modes[0]]
        print(f"\n（未跑 parent，以 {modes[0]} 为基准）")

    print("\n差值（各档 − parent）：")
    print("  档位          top1    相关率      库内零注入   重排mean    端到端p95")
    for mode in modes:
        r = results[mode]
        rate = r["related_hits"] / max(r["related_total"], 1)
        base_rate = base["related_hits"] / max(base["related_total"], 1)
        print("  %-12s %+d      %+5.1fpp     %+d          %+7.2fs    %+7.3fs" % (
            mode,
            r["hits"] - base["hits"],
            (rate - base_rate) * 100,
            r["zero_inscope"] - base["zero_inscope"],
            (r["rr_mean"] - base["rr_mean"]) / 1000,
            r["p95"] - base["p95"],
        ))

    ok = True
    for mode in modes:
        r = results[mode]
        if r["hits"] < base["hits"] or r["zero_inscope"] > base["zero_inscope"]:
            ok = False
    print("\n结论：" + ("候选档位均不劣于 parent" if ok else "存在退化档位，需逐档看上面差值表决策"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
