"""一次性诊断：fl-5「性能开销体现在哪？」两种 query 表达下的重排分数全景。

不进 CI。用法：在 backend/ 下 ./venv/Scripts/python.exe scripts/diag_floor_scores.py
输出：候选 6 条的逐条 (分数, topic, 来源文件, 片段前 40 字)。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from agent.rag import retriever  # noqa: E402
from agent.rag.retriever import CONFIG, get_reranker  # noqa: E402


def main() -> None:
    # 关掉 floor，只看分数，不让过滤吞掉信息
    CONFIG.relevance_floor = None

    from agent.rag.indexer import load_knowledge_index  # noqa: E402

    vs = load_knowledge_index("cs_basics", str(BACKEND_DIR / "chroma_kb"))
    if vs is None:
        raise SystemExit("知识库加载失败（chroma_kb 不存在？先跑 build_kb.py）")

    encoder = get_reranker()
    if encoder is None:
        raise SystemExit("重排模型未加载，无法诊断分数")

    queries = [
        ("L4 实况（上下文 topic=死锁）", "死锁 性能开销体现在哪？"),
        ("L1 口径（文档级 topic 名）", "HTTPS 与 TLS 握手 性能开销体现在哪？"),
        ("真实 /chat 抽的 topic（口语化）", "https和http的区别 性能开销体现在哪？"),
        ("无拼接原话", "性能开销体现在哪？"),
    ]

    for label, q in queries:
        candidates = vs.similarity_search(q, k=CONFIG.coarse_k)
        sparse = retriever._bm25_search(vs, q, CONFIG.coarse_k)
        if sparse:
            candidates = retriever._rrf_fuse([candidates, sparse], limit=CONFIG.coarse_k)
        scores = list(
            encoder.rerank(q, [d.page_content for d in candidates], batch_size=1)
        )
        order = sorted(range(len(candidates)), key=lambda i: scores[i], reverse=True)
        print(f"\n=== {label} ===\nquery: {q!r}")
        # 先看重排前的候选池：粗召回没捞到的东西，重排再准也救不回来
        pool = [
            f"{candidates[i].metadata.get('topic', '?')}"
            f"@{os.path.basename(str(candidates[i].metadata.get('source', '?')))}"
            f":{candidates[i].metadata.get('start_index', '?')}"
            for i in range(len(candidates))
        ]
        print("  候选池（重排前 %d 条）：%s" % (len(pool), pool))
        for i in order:
            d = candidates[i]
            meta = d.metadata or {}
            preview = (d.page_content or "").replace("\n", " ")[:40]
            print(
                f"  {scores[i]:7.3f}  [{meta.get('topic', '?')}] "
                f"{os.path.basename(str(meta.get('source', '?')))}@{meta.get('start_index', '?')}  {preview}"
            )


if __name__ == "__main__":
    main()
