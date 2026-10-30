"""O13 候选池 A/B：child 超额召回 + parent 池上限，逐档独立进程实测。

为什么必须**每档一个进程**：`smoke_rag.configure()` 改的是 retriever 的模块级
全局 CONFIG，同进程里连跑两档，第二档的 HTTP 连接池/重排模型状态会污染延迟
（踩过：`Event loop is closed`），延迟指标失真就没法拿来定取舍。

用法（backend/ 下）：
    RAG_PARENT_CHILD=1 ./venv/Scripts/python.exe scripts/ab_recall_pool.py --config B

档位（`--config`）：
    A  基线           overfetch=1  parent_pool=0   （等同改动前）
    B  温和           overfetch=2  parent_pool=6
    C  激进           overfetch=3  parent_pool=6
    D  激进不设闸     overfetch=3  parent_pool=0
    E  更激进         overfetch=4  parent_pool=8

只报四个数：Recall@3 / Precision@3 / 平均耗时 / p95。
其余（nDCG、MRR、top1/top3、池大小）一并输出，用来确认没有顾此失彼。
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
from eval.metrics import chunk_rel, p95  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CASES = str(BACKEND_DIR / "rag_eval_cases.json")
PERSIST_DIR = str(BACKEND_DIR / "chroma_kb")
COLLECTION = "cs_basics"

CONFIGS = {
    "A": (1, 0),
    "B": (2, 6),
    "C": (3, 6),
    "D": (3, 0),
    "E": (4, 8),
}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="A")
    ap.add_argument("--cases", default=DEFAULT_CASES)
    ap.add_argument("--parent-final-k", type=int, default=0, help="覆盖注入条数（默认跟环境）")
    args = ap.parse_args(argv)

    overfetch, parent_pool = CONFIGS[args.config]
    R.CONFIG.parent_child = True
    R.CONFIG.child_overfetch = overfetch
    R.CONFIG.parent_pool_k = parent_pool
    if args.parent_final_k:
        R.CONFIG.parent_final_k = args.parent_final_k
    inject_k = R.CONFIG.parent_final_k

    S.CASES = S.load_cases(args.cases)
    store = load_knowledge_index(COLLECTION, PERSIST_DIR)

    # 预热：重排模型首次加载不能算进单题耗时
    R.get_knowledge_context(store, "预热", k=1)

    rels = []
    secs = []
    pool_sizes = []
    injected = []
    top1 = top3 = total = 0
    t_start = time.perf_counter()
    for query, expected, kind, topic, relevant in S.CASES:
        if not relevant:
            continue
        trace: dict = {}
        t0 = time.perf_counter()
        docs = R.retrieve_knowledge_docs(store, query, topic=topic, trace=trace)
        secs.append(time.perf_counter() - t0)
        pool_sizes.append(trace.get("parents_k") or 0)
        injected.append(len(docs))

        topics = [str(d.metadata.get("topic") or "") for d in docs]
        if expected:
            total += 1
            top1 += 1 if S.top_hit(topics[:1], expected) else 0
            top3 += 1 if S.top_hit(topics, expected) else 0
        rels.append(chunk_rel(docs, relevant, expected, k=inject_k))
    wall = time.perf_counter() - t_start

    def mean(xs):
        return round(statistics.mean(xs), 4) if xs else 0.0

    out = {
        "config": args.config,
        "child_overfetch": overfetch,
        "parent_pool_k": parent_pool,
        "inject_k": inject_k,
        "cases": len(rels),
        "recall_at_k": mean([r.recall for r in rels]),
        "precision_at_k": mean([r.precision for r in rels]),
        # 噪声率：注入窗口里零增益条数占比（2026-10-28 新增指标）
        "noise_rate": mean([r.noise_rate for r in rels]),
        "mrr": mean([r.mrr for r in rels]),
        "ndcg_at_k": mean([r.ndcg for r in rels]),
        "top1": top1,
        "top3": top3,
        "total": total,
        "mean_parent_pool": round(statistics.mean(pool_sizes), 2),
        "mean_injected": round(statistics.mean(injected), 3),
        "rerank_text": R.CONFIG.rerank_text,
        "per_doc_floor": R.CONFIG.per_doc_floor,
        "relevance_floor": R.CONFIG.relevance_floor,
        "avg_secs": mean(secs),
        "p95_secs": round(p95(secs), 4),
        "wall_secs": round(wall, 1),
    }
    print("RESULT " + json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
