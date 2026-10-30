"""per-source 配额放在 RRF 之前（Early-stage）的 A/B 标定（U9，2026-10-26）。

背景
----
2026-10-26 实测：83 条评测用例里 **67 条**出现「同一文档占 ≥2 个注入位」（大量 3/3）。
先做了末端限额（`RAG_MAX_PER_SOURCE`，作用在最终注入那一步），结果只把它从 67 修到
**59**——因为路由收窄到 3 篇之后，向量 + BM25 各自 6 条几乎都落在最相似的同一篇上，
候选池里压根没有别的文档可以递补。

本脚本验证「配额挪到 RRF 之后、重排之前」值不值：
在同一进程里把 `retriever.CONFIG.source_quota` 切成 0（现状）与给定值（默认 2），
跑同一批评测用例，对比四件事：

  - **top1 命中**：配额会不会把正确答案挤出去（这是唯一不能退的底线）
  - **注入多样性**：单篇文档最多占几个位置（3/3 是最糟形态）
  - **注入条数**：别把 3 条掉成 2 条（少给资料同样是退化）
  - **p95 耗时**：融合多留一倍候选不能推高延迟（重排前截回，理论上不变）

用法（backend/ 下）：
    RAG_PARENT_CHILD=1 ./venv/Scripts/python.exe scripts/ab_source_quota.py
    RAG_PARENT_CHILD=1 ./venv/Scripts/python.exe scripts/ab_source_quota.py --quota 1 3

退出码：0 = 配额组 top1 不低于现状；1 = 出现 top1 退化（按实际情况决定要不要启用）。
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
# oos 题本就不该注入，不参与 top1 统计（但参与多样性统计，它同样会同源堆叠）
INSCOPE_KINDS = {"in-kb", "kb-interview"}


def load_cases() -> list[dict]:
    with open(CASES_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else data.get("cases", data)


def norm(source: str) -> str:
    return (source or "").removeprefix("cs/")


def gold_sources(case: dict) -> set[str]:
    """金标准文档 = relevant_chunks 里增益最高的那一档（多篇并列就都算命中）。"""
    chunks = case.get("relevant_chunks") or []
    if not chunks:
        return set()
    best = max(float(c[2]) for c in chunks)
    return {norm(str(c[0])) for c in chunks if float(c[2]) == best and float(c[2]) > 0}


def related_sources(case: dict) -> set[str]:
    """所有沾边的文档（增益 > 0）。

    用来回答「配额塞进来的第二篇到底是帮忙还是噪音」：金标准只标了增益最高的一档，
    但同一道题常常横跨两篇（例：进程线程 ↔ 调度）。拿这一档比对，才不会因为
    「新资料不在 argmax 里」就把本来有用的东西误判成噪音。
    """
    chunks = case.get("relevant_chunks") or []
    return {norm(str(c[0])) for c in chunks if float(c[2]) > 0}


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round((p / 100) * (len(ordered) - 1))))
    return ordered[idx]


def run(store, cases, quota: int, max_per_source: int = 0) -> dict:
    retriever.CONFIG.source_quota = quota
    retriever.CONFIG.max_per_source = max_per_source
    per_case = []
    elapsed: list[float] = []
    hits = total = 0
    dup_cases = 0
    worst_dup = 0
    related_hits = related_total = 0

    for case in cases:
        query = (case.get("query") or "").strip()
        kind = case.get("kind", "")
        gold = gold_sources(case)
        related = related_sources(case)
        started = time.perf_counter()
        trace: dict = {}
        docs = retriever.retrieve_knowledge_docs(store, query, topic="", trace=trace)
        elapsed.append(time.perf_counter() - started)

        sources = [norm(str((d.metadata or {}).get("source", ""))) for d in docs]
        counts: dict[str, int] = {}
        for s in sources:
            counts[s] = counts.get(s, 0) + 1
        max_dup = max(counts.values()) if counts else 0
        worst_dup = max(worst_dup, max_dup)
        if sources and max_dup >= 2:
            dup_cases += 1

        if related:
            related_total += len(docs)
            related_hits += sum(1 for s in sources if s in related)

        hit = None
        if kind in INSCOPE_KINDS and gold:
            total += 1
            hit = bool(sources) and sources[0] in gold
            hits += 1 if hit else 0
        per_case.append({
            "id": case.get("id"), "kind": kind, "query": query,
            "n": len(docs), "sources": sources, "max_dup": max_dup,
            "hit": hit, "gold": sorted(gold),
        })

    return {
        "quota": quota, "max_per_source": max_per_source,
        "hits": hits, "total": total,
        "injected": sum(c["n"] for c in per_case),
        "related_rate": round(related_hits / max(related_total, 1), 4),
        "related_total": related_total,
        "dup_cases": dup_cases, "worst_dup": worst_dup,
        "distinct_avg": round(statistics.fmean(
            [len(set(c["sources"])) for c in per_case]), 3),
        "p95": round(_pct(elapsed, 95), 3),
        "mean": round(statistics.fmean(elapsed), 3),
        "cases": per_case,
    }


def describe(tag: str, r: dict) -> None:
    print(f"\n[{tag}] source_quota={r['quota']} max_per_source={r['max_per_source']}")
    print(f"  top1 命中 {r['hits']}/{r['total']} = {r['hits'] / max(r['total'], 1):.1%}")
    print(f"  总注入 {r['injected']} 条；平均不同来源 {r['distinct_avg']} 篇")
    print(f"  注入相关率 {r['related_rate']:.1%}（{r['related_total']} 条里落在增益>0 的文档上）")
    print(f"  同源堆叠用例 {r['dup_cases']} 条（单篇最多占 {r['worst_dup']} 位）")
    print(f"  耗时 p95 {r['p95']}s / 均值 {r['mean']}s")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--combos", nargs="+", default=["2:1", "2:2"],
        help="要试的组合，格式 配额:末端限额（例 2:1 = 候选池每篇 2 条 + 注入每篇 1 条）",
    )
    args = ap.parse_args()

    store = load_knowledge_index(persist_directory="chroma_kb")
    if store is None:
        print("!! chroma_kb 打不开")
        return 2

    cases = load_cases()
    print(f"config: parent_child={retriever.CONFIG.parent_child} "
          f"coarse_k={retriever.CONFIG.coarse_k} "
          f"final_k(parent)={retriever.CONFIG.parent_final_k} "
          f"floor={retriever.CONFIG.relevance_floor}")
    print(f"评测集 {len(cases)} 条，其中库内 {len([c for c in cases if c.get('kind') in INSCOPE_KINDS])} 条")

    base = run(store, cases, 0, 0)
    describe("现状（配额关）", base)

    results = [base]
    for combo in args.combos:
        try:
            quota_s, max_s = combo.split(":")
            r = run(store, cases, int(quota_s), int(max_s))
        except ValueError:
            print(f"!! 组合格式不对，跳过：{combo}（应形如 2:1）")
            continue
        describe(f"组合 {combo}", r)
        results.append(r)

    ok = True
    for r in results[1:]:
        regress = [
            (a, b) for a, b in zip(base["cases"], r["cases"])
            if a["hit"] and b["hit"] is False
        ]
        improve = [
            (a, b) for a, b in zip(base["cases"], r["cases"])
            if a["hit"] is False and b["hit"]
        ]
        missing = [
            (a, b) for a, b in zip(base["cases"], r["cases"])
            if b["n"] < a["n"]
        ]
        print(f"\n-- 配额 {r['quota']} vs 现状 --")
        print(f"  top1 变差 {len(regress)} 例 / 变好 {len(improve)} 例")
        for a, _ in regress[:10]:
            print(f"    - {a['id']} 金标准={a['gold']} 原top={a['sources'][:1]}")
        for _, b in improve[:10]:
            print(f"    + {b['id']} 金标准={b['gold']} 新top={b['sources'][:1]}")
        print(f"  注入条数变少 {len(missing)} 例（掉一则少给资料）")
        if regress:
            ok = False

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
