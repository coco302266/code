"""把 Recall 的缺口拆成三段：路由丢的 / 粗召回丢的 / 重排挑错的。

Recall 缺口 = 绝对上限(0.9159) − 实测(0.7827)。三段各占多少决定了该改哪里：

    L_router  金标准片根本不在路由选出的文档里        → 该放宽 RAG_ROUTER_TOP_DOCS
    L_coarse  在路由范围内，但两路召回都没捞进来      → 该加大 coarse_k
    L_rank    进了候选池，但重排没把它挑进 top-3     → 该改重排/下限

用法（backend/ 下）：
    RAG_PARENT_CHILD=1 ./venv/Scripts/python.exe scripts/diag_pool_loss.py

不跑重排，很快。
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

from agent.rag import doc_router, retriever as R  # noqa: E402
from agent.rag.indexer import load_knowledge_index  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CASES = str(BACKEND_DIR / "rag_eval_cases.json")
PERSIST_DIR = str(BACKEND_DIR / "chroma_kb")
COLLECTION = "cs_basics"


def _key(doc):
    meta = doc.metadata or {}
    try:
        start = int(meta.get("start_index"))
    except (TypeError, ValueError):
        start = -1
    return (str(meta.get("source") or ""), start)


def gold_map(case):
    out = {}
    for item in case.get("relevant_chunks") or []:
        if not item or len(item) < 2:
            continue
        g = 1.0
        if len(item) > 2:
            try:
                g = float(item[2])
            except (TypeError, ValueError):
                g = 1.0
        out[(item[0], int(item[1]))] = g
    return out


def build_pool(store, query, topic, k, use_router=True, router_top=None):
    if router_top is not None:
        doc_router.top_docs = lambda: router_top  # type: ignore[assignment]
    sq = f"{topic} {query}".strip() if R._should_augment_with_topic(query, topic) else query
    scope = None
    if use_router:
        trace: dict = {}
        route = doc_router.route_documents(
            store, sq, trace, tokenize=R._tokenize,
            bm25_get=lambda q: R.bm25_scored_docs(store, q),
        )
        scope = frozenset(route.sources) if route.applied else None
    dense = R._scoped_similarity_search(store, sq, k, scope) if scope else store.similarity_search(sq, k=k)
    if scope and not dense:
        dense = store.similarity_search(sq, k=k)
    if R.CONFIG.enable_hybrid:
        sparse = R._bm25_search(store, sq, k, allowed_sources=scope)
        if sparse:
            dense = R._rrf_fuse([dense, sparse], limit=k)
    if R.CONFIG.parent_child:
        dense = R.aggregate_parent_docs(dense)
    return dense, scope


def ceiling(pool, gold, k=3):
    vals = sorted((gold.get(_key(d), 0.0) for d in pool), reverse=True)
    return sum(vals[:k])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default=DEFAULT_CASES)
    args = ap.parse_args(argv)

    raw = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    cases = []
    for item in raw.get("cases", []):
        q = (item.get("query") or "").strip()
        if not q or item.get("kind") in ("cascade", "oos"):
            continue
        gm = gold_map(item)
        if not gm:
            continue
        cases.append({"id": item["id"], "query": q, "kind": item.get("kind"),
                      "topic": item.get("topic", "") or "", "gold": gm})

    store = load_knowledge_index(COLLECTION, PERSIST_DIR)
    out = {}

    def run(label, k, use_router, router_top=None):
        vals = []
        for c in cases:
            pool, _ = build_pool(store, c["query"], c["topic"], k, use_router, router_top)
            total = sum(c["gold"].values())
            vals.append(ceiling(pool, c["gold"]) / total if total else 0.0)
        out[label] = round(statistics.mean(vals), 4)
        return out[label]

    abs_ceil = statistics.mean(
        sum(sorted(c["gold"].values(), reverse=True)[:3]) / sum(c["gold"].values())
        for c in cases
    )
    out["绝对上限"] = round(abs_ceil, 4)
    run("路由关_k6", 6, False)
    run("路由关_k16", 16, False)
    run("路由3篇_k6(现状)", 6, True, 3)
    run("路由3篇_k16", 16, True, 3)
    run("路由5篇_k6", 6, True, 5)
    run("路由5篇_k16", 16, True, 5)
    run("路由8篇_k16", 16, True, 8)

    base = out["路由3篇_k6(现状)"]
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print()
    print("缺口拆解（Recall，绝对值 pp）：")
    print("  L_router  路由丢的   = %.1f pp  (路由关_k16 %.4f − 路由3篇_k16 %.4f)"
          % ((out["路由关_k16"] - out["路由3篇_k16"]) * 100, out["路由关_k16"], out["路由3篇_k16"]))
    print("  L_coarse  粗召回丢的 = %.1f pp  (路由3篇_k16 %.4f − 路由3篇_k6 %.4f)"
          % ((out["路由3篇_k16"] - base) * 100, out["路由3篇_k16"], base))
    print("  L_rank    重排挑错的 = %.1f pp  (路由3篇_k6 %.4f − 实测 0.7827)"
          % ((base - 0.7827) * 100, base))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
