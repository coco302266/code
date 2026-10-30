"""把重排的"选择"摊开看：最差用例里，金标准片在池中的名次和重排给的分数。

回答一个问题：重排挑错（L_rank≈5pp）到底是
  (a) 金标准片在池里但被打低分挤出 top-3，还是
  (b) 根本没进池。
(a) 就改打分/融合，(b) 就改召回，不用猜。

用法（backend/ 下）：
    RAG_PARENT_CHILD=1 ./venv/Scripts/python.exe scripts/diag_rerank_choice.py [--worst 20]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

import smoke_rag as S  # noqa: E402
from agent.rag import retriever as R  # noqa: E402
from agent.rag.indexer import load_knowledge_index  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CASES = str(BACKEND_DIR / "rag_eval_cases.json")
PERSIST_DIR = str(BACKEND_DIR / "chroma_kb")
COLLECTION = "cs_basics"


def _key(doc):
    m = doc.metadata or {}
    try:
        s = int(m.get("start_index"))
    except (TypeError, ValueError):
        s = -1
    return (str(m.get("source") or ""), s)


def gold_map(case):
    out = {}
    for it in case.get("relevant_chunks") or []:
        if not it or len(it) < 2:
            continue
        g = 1.0
        if len(it) > 2:
            try:
                g = float(it[2])
            except (TypeError, ValueError):
                g = 1.0
        out[(it[0], int(it[1]))] = g
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default=DEFAULT_CASES)
    ap.add_argument("--worst", type=int, default=20)
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)

    raw = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    cases = []
    for it in raw.get("cases", []):
        q = (it.get("query") or "").strip()
        if not q or it.get("kind") in ("cascade", "oos"):
            continue
        gm = gold_map(it)
        if not gm:
            continue
        cases.append({"id": it["id"], "query": q, "kind": it.get("kind"),
                      "topic": it.get("topic", "") or "",
                      "expected": set(it.get("expected") or []), "gold": gm})

    store = load_knowledge_index(COLLECTION, PERSIST_DIR)
    R.get_knowledge_context(store, "预热", k=1)

    rows = []
    for c in cases:
        trace: dict = {}
        t0 = time.perf_counter()
        docs = R.retrieve_knowledge_docs(store, c["query"], topic=c["topic"], trace=trace)
        secs = time.perf_counter() - t0

        # 重跑一遍候选池，拿到"池里每一条"的金标准增益，才知道金标准片在不在池里
        sq = (f"{c['topic']} {c['query']}".strip()
              if R._should_augment_with_topic(c["query"], c["topic"]) else c["query"])
        route = R.doc_router.route_documents(store, sq, {}, tokenize=R._tokenize,
                                             bm25_get=lambda q: R.bm25_scored_docs(store, q))
        scope = frozenset(route.sources) if route.applied else None
        dense = R._scoped_similarity_search(store, sq, R.CONFIG.coarse_k, scope)
        if scope and not dense:
            dense = store.similarity_search(sq, k=R.CONFIG.coarse_k)
        sparse = R._bm25_search(store, sq, R.CONFIG.coarse_k, allowed_sources=scope)
        if sparse:
            dense = R._rrf_fuse([dense, sparse], limit=R.CONFIG.coarse_k)
        pool = R.aggregate_parent_docs(dense) if R.CONFIG.parent_child else dense

        pool_gains = [c["gold"].get(_key(d), 0.0) for d in pool]
        inj_gains = [c["gold"].get(_key(d), 0.0) for d in docs]
        total = sum(c["gold"].values())
        pool_ceil = sum(sorted(pool_gains, reverse=True)[:3])
        rows.append({
            "id": c["id"], "kind": c["kind"], "query": c["query"],
            "total": round(total, 2),
            "pool_size": len(pool),
            "pool_gains": [round(g, 2) for g in pool_gains],
            "pool_ceiling": round(pool_ceil, 2),
            "injected_gains": [round(g, 2) for g in inj_gains],
            "injected_gain": round(sum(inj_gains), 2),
            "recall": round(sum(inj_gains) / total, 4) if total else 0.0,
            "rank_loss": round((pool_ceil - sum(inj_gains)) / total, 4) if total else 0.0,
            "pool_loss": round((sum(sorted(c["gold"].values(), reverse=True)[:3]) - pool_ceil) / total, 4)
            if total else 0.0,
            "secs": round(secs, 3),
        })

    print("n=%d" % len(rows))
    print("平均 池损失(没召回)=%.4f  排序损失(挑错)=%.4f  实测recall=%.4f" % (
        statistics.mean(r["pool_loss"] for r in rows),
        statistics.mean(r["rank_loss"] for r in rows),
        statistics.mean(r["recall"] for r in rows),
    ))
    kinds = {}
    for r in rows:
        kinds.setdefault(r["kind"], []).append(r)
    for k, v in sorted(kinds.items()):
        print("  %-12s n=%2d 池损失=%.3f 排序损失=%.3f 池大小=%.1f"
              % (k, len(v),
                 statistics.mean(x["pool_loss"] for x in v),
                 statistics.mean(x["rank_loss"] for x in v),
                 statistics.mean(x["pool_size"] for x in v)))

    print("\n--- 排序损失最大的 %d 条 ---" % args.worst)
    for r in sorted(rows, key=lambda x: -x["rank_loss"])[: args.worst]:
        print("%-8s %-10s 池%s=%s 注入=%s 池上限%.1f 实得%.1f | %s"
              % (r["id"], r["kind"], r["pool_size"], r["pool_gains"],
                 r["injected_gains"], r["pool_ceiling"], r["injected_gain"], r["query"][:30]))

    if args.out:
        Path(args.out).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print("\n明细: %s" % args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
