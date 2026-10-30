"""按重排输入档位取 **L1 门禁口径** 的 p95（2026-10-27）。

为什么需要它
------------
`scripts/smoke_rag.py --only full` 只打印每题明细、不打印汇总表，`--uplift` 只给
nDCG 提升度——两个都不输出门禁真正判的那个数：`run_config()["p95_secs"]`。
而 `scripts/ab_rerank_text.py` 用的是 83 条全集 + 自己的计时口径，与 L1 门禁
（75 题 / 计分 71，只统计计分题）**不可直比**。

本脚本复用 `smoke_rag.run_config`（与 L1 完全同一套检索与计时代码），
只做一件事：在指定 `RAG_RERANK_TEXT` 档位下跑一次 `full`，把门禁四件套打出来。

**一次只跑一档、一个独立进程**：同进程连续跑多档会有顺序效应（模型热、CPU 争用），
耗时结论不可信。要对照就在 shell 里串行跑多次。

用法（backend/ 下）：
    RAG_PARENT_CHILD=1 ./venv/Scripts/python.exe scripts/l1_p95_by_mode.py --mode child
    RAG_PARENT_CHILD=1 RAG_RERANK_TEXT=parent ./venv/Scripts/python.exe scripts/l1_p95_by_mode.py
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("RAG_PARENT_CHILD", "1")  # 必须在 import CONFIG 之前

import smoke_rag as S  # noqa: E402

from agent.rag import retriever  # noqa: E402
from agent.rag.indexer import load_knowledge_index  # noqa: E402

COLLECTION = "cs_basics"
PERSIST_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "chroma_kb")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="", help="覆盖 RAG_RERANK_TEXT（parent/child/child_title）")
    ap.add_argument("--cases", default="", help="评测集路径（默认 rag_eval_cases.json）")
    args = ap.parse_args()

    if args.mode:
        retriever.CONFIG.rerank_text = args.mode.strip().lower()

    store = load_knowledge_index(COLLECTION, PERSIST_DIR)
    if store is None:
        print("知识库不存在：先跑 python build_kb.py")
        return 1
    S.CASES = S.load_cases(args.cases) if args.cases else S.load_cases()

    r = S.run_config(store, "full")
    m = r.get("metrics") or {}
    scored = r.get("total") or 0
    avg = r.get("avg_secs") or 0
    p95 = r.get("p95_secs") or 0

    print()
    print("=" * 62)
    print("RAG_RERANK_TEXT = %s" % retriever.CONFIG.rerank_text)
    print("  切片数        %s" % store._collection.count())
    print("  top1          %s/%s" % (r.get("top1"), scored))
    print("  top3          %s/%s" % (r.get("top3"), scored))
    print("  nDCG@4        %s" % m.get("ndcg_at_k"))
    print("  MRR           %s" % m.get("mrr"))
    print("  平均耗时      %.3fs" % avg)
    print("  p95           %.3fs" % p95)
    print("  降级条数      %s" % r.get("degraded_cases"))
    gate = 3.0
    print("  门禁(≤%.1fs)   %s" % (gate, "✓ 通过" if p95 <= gate else "✗ 超预算 %.3fs" % (p95 - gate)))
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
