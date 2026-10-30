"""对比文档路由的几种打分信号，为 L1 选型取数。

摘要向量（doc_router 初版）实测 route@3 只有 84.5%，硬过滤必掉分。这里换
「用切片级证据按文档聚合」——向量检索和 BM25 本来就在全库跑过，把命中结果
按 source 聚一聚就能得到文档级排序，几乎零额外成本，而且用的是全文语义
而不是有损摘要。

候选信号：
  vec-max  文档内最高向量相似度
  vec-sum  文档内相似度之和（偏好命中条数多的文档）
  vec-rrf  文档内按名次倒数求和（RRF 思路，抗单条高分噪音）
  bm25-max 文档内最高 BM25 分
  bm25-sum 文档内 BM25 分之和
  mix      vec-rrf 与 bm25-sum 各自归一后相加

判据：金标准文档落在 top-3 / top-5 的比例（route recall），以及 top1。

用法：
    ./venv/Scripts/python.exe scripts/diag_router_signals.py
"""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("RAG_PARENT_CHILD", "1")

from agent.rag import retriever  # noqa: E402
from agent.rag.indexer import load_knowledge_index  # noqa: E402
from agent.rag.retriever import _bm25_search, _should_augment_with_topic  # noqa: E402

CASES_FILE = Path(__file__).resolve().parents[1] / "rag_eval_cases.json"


def load_cases() -> list[dict]:
    data = json.loads(CASES_FILE.read_text(encoding="utf-8"))
    return [
        c
        for c in data.get("cases", [])
        if c.get("kind") not in {"cascade"} and (c.get("query") or "").strip()
    ]


def main() -> None:
    store = load_knowledge_index(persist_directory="chroma_kb")
    if store is None:
        raise SystemExit("找不到知识库 chroma_kb")

    raw = store.get()
    topic_to_source: dict[str, str] = {}
    for meta in raw.get("metadatas") or []:
        meta = meta or {}
        if meta.get("topic") and meta.get("source"):
            topic_to_source.setdefault(str(meta["topic"]), str(meta["source"]))

    cases = load_cases()
    print(f"用例 {len(cases)} 条；知识库 {store._collection.count()} 片")

    # 每个信号的 route recall：k -> [hit, total]
    kk = (1, 3, 5, 8)
    stats: dict[str, dict[int, list[int]]] = {}

    def bump(signal: str, k: int, hit: bool) -> None:
        stats.setdefault(signal, {i: [0, 0] for i in kk})
        stats[signal][k][0] += 1 if hit else 0
        stats[signal][k][1] += 1

    for case in cases:
        if case.get("kind") == "oos":
            continue
        chunks = case.get("relevant_chunks") or []
        gold = {c[0] for c in chunks if c} or {
            topic_to_source[t] for t in (case.get("expected") or []) if t in topic_to_source
        }
        if not gold:
            continue

        query = case["query"].strip()
        topic = (case.get("topic") or "").strip()
        sq = (
            f"{topic} {query}".strip()
            if _should_augment_with_topic(query, topic)
            else query
        )

        # 一次宽召回，所有信号复用同一批候选
        wide = store.similarity_search_with_score(sq, k=40)
        sparse = _bm25_search(store, sq, 40)

        vec_scores = [(doc, dist) for doc, dist in wide]  # l2 距离，越小越近
        bm25_map = {
            (d.metadata or {}).get("source", ""): i for i, d in enumerate(sparse)
        }
        bm25_scores = _bm25_raw(store, sq, sparse)

        # --- 向量类 ---
        by_doc_max = defaultdict(float)
        by_doc_sum = defaultdict(float)
        by_doc_rrf = defaultdict(float)
        for rank, (doc, dist) in enumerate(vec_scores):
            src = (doc.metadata or {}).get("source", "")
            sim = 1.0 / (1.0 + float(dist))  # l2 -> (0,1] 的单调相似度
            by_doc_max[src] = max(by_doc_max[src], sim)
            by_doc_sum[src] += sim
            by_doc_rrf[src] += 1.0 / (60 + rank + 1)

        # --- BM25 类 ---
        by_doc_bm25_sum = defaultdict(float)
        by_doc_bm25_max = defaultdict(float)
        for doc, score in zip(sparse, bm25_scores):
            src = (doc.metadata or {}).get("source", "")
            by_doc_bm25_sum[src] += score
            by_doc_bm25_max[src] = max(by_doc_bm25_max[src], score)

        signals = {
            "vec-max": by_doc_max,
            "vec-sum": by_doc_sum,
            "vec-rrf": by_doc_rrf,
            "bm25-max": by_doc_bm25_max,
            "bm25-sum": by_doc_bm25_sum,
        }
        # 归一化混合：两个信号各自除以本查询最大值，避免量纲差异
        def norm(d: dict) -> dict:
            m = max(d.values()) if d else 0
            return {k: v / m for k, v in d.items()} if m > 0 else {}

        mix = defaultdict(float)
        for k_, v in norm(by_doc_rrf).items():
            mix[k_] += v
        for k_, v in norm(by_doc_bm25_sum).items():
            mix[k_] += v
        signals["mix"] = mix

        for name, table in signals.items():
            if not table:
                for k in kk:
                    bump(name, k, False)
                continue
            ranked = [s for s, _ in sorted(table.items(), key=lambda x: -x[1])]
            for k in kk:
                bump(name, k, bool(gold & set(ranked[:k])))

    print("\n-- route recall（金标准文档进入 top-k）--")
    header = "signal".ljust(10) + "".join(f"top{k}".rjust(8) for k in kk)
    print(header)
    winners = []
    for name, table in stats.items():
        row = name.ljust(10)
        for k in kk:
            hit, total = table[k]
            row += f"{hit}/{total}".rjust(8)
        print(row)
        winners.append((table[3][0], name))

    print("\n按 top3 排序：")
    for hit, name in sorted(winners, reverse=True):
        print(f"  {name}: {hit}/{stats[name][3][1]}")


def _bm25_raw(store, query: str, docs) -> list[float]:
    """取这批文档对应的 BM25 原始分（_bm25_search 只回文档不回分数）。"""
    bm25, corpus_docs = retriever._get_bm25_store(store)
    if bm25 is None:
        return [0.0] * len(docs)
    tokens = retriever._tokenize(query)
    scores = bm25.get_scores(tokens)
    index_of = {id(d): i for i, d in enumerate(corpus_docs)}
    return [float(scores[index_of[id(d)]]) if id(d) in index_of else 0.0 for d in docs]


if __name__ == "__main__":
    main()
