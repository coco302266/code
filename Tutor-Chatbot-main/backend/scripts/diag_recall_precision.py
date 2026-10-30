"""召回率/准确率归因诊断：损失到底出在候选池还是排序？

用法（backend/ 下，必须带 RAG_PARENT_CHILD=1 与生产一致）：
    RAG_PARENT_CHILD=1 ./venv/Scripts/python.exe scripts/diag_recall_precision.py

回答三个问题（三个数，缺一个就没法定优化方向）：

1. **实际值**：当前注入的 3 条拿到多少增益（Recall / Precision 的分子）。
2. **池上限（Oracle）**：候选池里若有一个「完美排序器」，最多能拿到多少。
   把候选按金标准降序取前 3 再求和 —— 这是**不改候选池**时 Recall 的物理天花板。
   实测值 ≈ 池上限 ⇒ 候选池不够大，加粗召回才有用；
   实测值 << 池上限 ⇒ 排序/截断有问题，加粗召回只是把垃圾也一起加进来。
3. **损失构成**：注入的 3 条里，有多少是 0 增益（纯噪声）？有多少在榜外？

候选池按 coarse_k 多档扫描（默认 6/10/16/24），一次跑完就能看出
「加召回有没有用」，不用每个档位各起一次进程（重排模型加载很慢）。

**不跑重排**：本脚本只关心候选池的召回上限，重排不进池。所以很快。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

import smoke_rag as S  # noqa: E402
from agent.rag import retriever as R  # noqa: E402
from agent.rag.indexer import load_knowledge_index  # noqa: E402
from eval.metrics import chunk_gains  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CASES = str(BACKEND_DIR / "rag_eval_cases.json")
PERSIST_DIR = str(BACKEND_DIR / "chroma_kb")
COLLECTION = "cs_basics"
SCAN_KS = (6, 10, 16, 24)


def _chunk_key(doc):
    """与 eval.metrics._chunk_key 同一把尺子（source, start_index）。"""
    meta = doc.metadata or {}
    source = str(meta.get("source") or "")
    try:
        start = int(meta.get("start_index"))
    except (TypeError, ValueError):
        start = -1
    return (source, start)


def _gold_map(case):
    gold = {}
    for item in case.get("relevant_chunks") or []:
        if not item or len(item) < 2:
            continue
        gain = 1.0
        if len(item) > 2:
            try:
                gain = float(item[2])
            except (TypeError, ValueError):
                gain = 1.0
        gold[(item[0], int(item[1]))] = gain
    return gold


def build_pool(store, query: str, topic: str, coarse_k: int) -> list:
    """复刻 retrieve_knowledge_docs 的候选池构建（路由 → 向量 → BM25 → RRF → 聚合）。

    刻意不调用 retrieve_knowledge_docs：那个函数写死了 coarse_k 与重排，
    这里要按档位扫。**链路必须与生产一致**，否则量出来的上限是假的。
    """
    use_topic = R._should_augment_with_topic(query, topic)
    search_query = f"{topic} {query}".strip() if use_topic else query

    trace: dict = {}
    route = R.doc_router.route_documents(
        store,
        search_query,
        trace,
        tokenize=R._tokenize,
        bm25_get=lambda q: R.bm25_scored_docs(store, q),
    )
    if route.reason == "gate":
        return []
    scope = frozenset(route.sources) if route.applied else None

    candidates = R._scoped_similarity_search(store, search_query, coarse_k, scope)
    if scope and not candidates:
        scope = None
        candidates = store.similarity_search(search_query, k=coarse_k)

    if R.CONFIG.enable_hybrid:
        sparse = R._bm25_search(store, search_query, coarse_k, allowed_sources=scope)
        if sparse:
            candidates = R._rrf_fuse([candidates, sparse], limit=coarse_k)
    if R.CONFIG.parent_child:
        candidates = R.aggregate_parent_docs(candidates)
    return candidates


def oracle_gain(pool: list, gold: dict, k: int) -> float:
    """候选池里按增益降序取前 k 的增益和 = Recall 分子在该池下的上限。"""
    vals = sorted((gold.get(_chunk_key(d), 0.0) for d in pool), reverse=True)
    return sum(vals[:k])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default=DEFAULT_CASES)
    ap.add_argument("--out", default="")
    ap.add_argument("--ks", default=",".join(str(x) for x in SCAN_KS))
    args = ap.parse_args(argv)
    ks = [int(x) for x in args.ks.split(",") if x.strip()]

    raw = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    cases = []
    for item in raw.get("cases", []):
        q = (item.get("query") or "").strip()
        if not q:
            continue
        kind = item.get("kind")
        if kind in ("cascade", "oos"):  # 与 smoke_rag.load_cases 同口径
            continue
        gold = _gold_map(item)
        if not gold:  # 无金标准 → 不计入 recall/precision
            continue
        cases.append(
            {
                "id": item.get("id"),
                "query": q,
                "kind": kind,
                "topic": item.get("topic", "") or "",
                "expected": set(item.get("expected") or []),
                "gold": gold,
                "total_gain": sum(gold.values()),
            }
        )

    store = load_knowledge_index(COLLECTION, PERSIST_DIR)

    inject_k = R.CONFIG.parent_final_k if R.CONFIG.parent_child else R.CONFIG.final_k
    rows = []
    for c in cases:
        trace: dict = {}
        docs = R.retrieve_knowledge_docs(store, c["query"], topic=c["topic"], trace=trace)
        gains = chunk_gains(docs, None, c["expected"])
        # 用金标准覆盖：chunk_gains 的 0.5 档只看 topic，这里要看切片身份
        gains = [c["gold"].get(_chunk_key(d), 0.0) for d in docs]
        injected_gain = sum(g for g in gains if g > 0)
        noise = sum(1 for g in gains if g <= 0)

        pools = {}
        for k in ks:
            pool = build_pool(store, c["query"], c["topic"], k)
            pools[k] = {
                "size": len(pool),
                "oracle": round(oracle_gain(pool, c["gold"], inject_k), 4),
            }
        rows.append(
            {
                "id": c["id"],
                "kind": c["kind"],
                "query": c["query"],
                "total_gain": round(c["total_gain"], 3),
                "injected": len(docs),
                "gains": [round(g, 2) for g in gains],
                "injected_gain": round(injected_gain, 3),
                "noise_slots": noise,
                "recall": round(injected_gain / c["total_gain"], 4) if c["total_gain"] else 0.0,
                "precision": round(sum(gains) / inject_k, 4) if inject_k else 0.0,
                "pools": pools,
                "fused_k": trace.get("fused_k"),
                "parents_k": trace.get("parents_k"),
            }
        )

    def mean(xs):
        return round(statistics.mean(xs), 4) if xs else 0.0

    summary = {
        "cases": len(rows),
        "inject_k": inject_k,
        "parent_child": R.CONFIG.parent_child,
        "actual_recall": mean([r["recall"] for r in rows]),
        "actual_precision": mean([r["precision"] for r in rows]),
        "mean_noise_slots": round(statistics.mean([r["noise_slots"] for r in rows]), 3),
        "mean_total_gain": round(statistics.mean([r["total_gain"] for r in rows]), 3),
        "mean_injected": round(statistics.mean([r["injected"] for r in rows]), 3),
        "oracle_by_coarse_k": {
            str(k): {
                "recall_ceiling": mean([r["pools"][k]["oracle"] / r["total_gain"]
                                        if r["total_gain"] else 0.0 for r in rows]),
                "mean_pool_size": round(statistics.mean([r["pools"][k]["size"] for r in rows]), 2),
            }
            for k in ks
        },
    }
    # 池子小于注入位 ⇒ 连填都填不满，Recall 直接被池大小卡死
    starved = [r for r in rows if r["pools"][ks[0]]["size"] < inject_k]
    summary["pool_starved_cases_at_k%d" % ks[0]] = len(starved)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("\n--- 最差的 15 条（按 recall 升序）---")
    for r in sorted(rows, key=lambda x: (x["recall"], x["precision"]))[:15]:
        print(
            "%-10s r=%.2f p=%.2f gold=%.1f 注入=%s 噪声=%d 池k%d=%d 上限=%.2f | %s"
            % (
                r["id"], r["recall"], r["precision"], r["total_gain"],
                r["gains"], r["noise_slots"], ks[0],
                r["pools"][ks[0]]["size"], r["pools"][ks[0]]["oracle"],
                r["query"][:34],
            )
        )

    if args.out:
        Path(args.out).write_text(
            json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print("\n明细已写入 %s" % args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
