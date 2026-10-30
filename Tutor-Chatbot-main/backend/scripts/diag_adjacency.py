"""相邻小节扩展的收益预估（不跑重排，只算池上限能不能涨）。

动机：金标准片在池外（L_pool≈8.2pp）是召回阶段丢的。盲目扩大粗召回实测无效
——池子变大后重排挑错同步变多，净收益≈0。所以要看**定向**扩展行不行：
同一篇文档里，与命中片相邻的 `##` 小节（parent_id 序号 ±1）是不是更容易是金标准。
八股文资料里「必要条件 / 预防 / 避免」这类相关小节本来就是挨着写的。

回答两个数：
  1. 池上限能从 0.8336 提到多少（+1 邻域 / +2 邻域）；
  2. 为这个上限要多付多少重排条数（重排按条计费，是唯一真成本）。

用法（backend/ 下）：
    RAG_PARENT_CHILD=1 ./venv/Scripts/python.exe scripts/diag_adjacency.py
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from agent.rag import doc_router, retriever as R  # noqa: E402
from agent.rag.indexer import load_knowledge_index  # noqa: E402
from langchain_core.documents import Document  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parent.parent


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
        if len(it) < 2:
            continue
        g = 1.0
        if len(it) > 2:
            try:
                g = float(it[2])
            except (TypeError, ValueError):
                g = 1.0
        out[(it[0], int(it[1]))] = g
    return out


def load_parent_index(store):
    """把库里所有 parent 拉出来建 (source, 序号) -> Document 的索引。"""
    raw = store.get(include=["documents", "metadatas"])
    idx: dict[tuple[str, int], Document] = {}
    for text, meta in zip(raw.get("documents") or [], raw.get("metadatas") or []):
        pid = (meta or {}).get("parent_id")
        if not pid or "#" not in str(pid):
            continue
        source, _, num = str(pid).rpartition("#")
        try:
            n = int(num)
        except ValueError:
            continue
        idx[(source, n)] = Document(
            page_content=(meta or {}).get("parent_text") or text,
            metadata={**meta, "start_index": (meta or {}).get("parent_start")},
        )
    return idx


def expand(pool, index, radius):
    """给池里的每条 parent 补上同文档 ±radius 内的相邻小节。"""
    seen = {_key(d) for d in pool}
    out = list(pool)
    for d in pool:
        pid = str((d.metadata or {}).get("parent_id") or "")
        if "#" not in pid:
            continue
        source, _, num = pid.rpartition("#")
        try:
            n = int(num)
        except ValueError:
            continue
        for delta in range(1, radius + 1):
            for m in (n - delta, n + delta):
                nb = index.get((source, m))
                if nb is not None and _key(nb) not in seen:
                    seen.add(_key(nb))
                    out.append(nb)
    return out


def main() -> int:
    cases = []
    raw = json.loads((BACKEND_DIR / "rag_eval_cases.json").read_text(encoding="utf-8"))
    for it in raw.get("cases", []):
        q = (it.get("query") or "").strip()
        if not q or it.get("kind") in ("cascade", "oos"):
            continue
        g = gold_map(it)
        if g:
            cases.append({"id": it["id"], "q": q, "kind": it.get("kind"),
                          "topic": it.get("topic", "") or "", "gold": g})

    store = load_knowledge_index("cs_basics", str(BACKEND_DIR / "chroma_kb"))
    index = load_parent_index(store)
    print("库内 parent 小节数：%d" % len(index))

    def ceiling(pool, gold, k=3):
        v = sorted((gold.get(_key(d), 0.0) for d in pool), reverse=True)
        return sum(v[:k])

    res = {"base": [], "r1": [], "r2": []}
    sizes = {"base": [], "r1": [], "r2": []}
    for c in cases:
        sq = (f"{c['topic']} {c['q']}".strip()
              if R._should_augment_with_topic(c["q"], c["topic"]) else c["q"])
        tr: dict = {}
        route = doc_router.route_documents(store, sq, tr, tokenize=R._tokenize,
                                           bm25_get=lambda x: R.bm25_scored_docs(store, x))
        scope = frozenset(route.sources) if route.applied else None
        dense = R._scoped_similarity_search(store, sq, R.CONFIG.coarse_k, scope)
        if scope and not dense:
            dense = store.similarity_search(sq, k=R.CONFIG.coarse_k)
        sp = R._bm25_search(store, sq, R.CONFIG.coarse_k, allowed_sources=scope)
        if sp:
            dense = R._rrf_fuse([dense, sp], limit=R.CONFIG.coarse_k)
        pool = R.aggregate_parent_docs(dense)
        tot = sum(c["gold"].values())
        for name, p in (("base", pool),
                        ("r1", expand(pool, index, 1)),
                        ("r2", expand(pool, index, 2))):
            res[name].append(ceiling(p, c["gold"]) / tot if tot else 0.0)
            sizes[name].append(len(p))

    for name in ("base", "r1", "r2"):
        print("%-5s 池上限=%.4f  平均池大小=%.2f" % (
            name, statistics.mean(res[name]), statistics.mean(sizes[name])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
