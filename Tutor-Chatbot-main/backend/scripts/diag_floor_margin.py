"""相关性下限（relevance_floor）边缘盘点 + 注入多样性统计。

为什么要这个脚本
----------------
`relevance_floor=-2.0` 是整题级闸门：最高分不到 -2.0 就**一条都不注入**。
这条闸门的代价是「宁可零资料，也不塞错资料」，但它的**误伤面从未被量过**——
fl-5 只逐条复查过「死锁 性能开销」这一条（结论是库内本无，属正确过滤）。

2026-10-26 用 `diag_rag_gaps.py` 逐条看注入结果时，撞到一条真误伤：
「快排的时间复杂度是多少」**不带 topic** 时最高分 -2.132，被 -2.0 拒掉 → 注入 0 条；
带 topic 变成 -1.751，勉强过闸。**库内题 ±0.13 就把资料全丢**，这条典型体质必须摸清，
而不是等学生提问时才发现。

本脚本对**整份评测集**跑一遍，回答三个此前没有数字的问题：
  1. 有多少条**库内题**被 floor 拒成 0 注入（硬误伤）？
  2. 有多少条落在 floor 上方 1 分的灰区（随时可能因措辞翻车）？
  3. 注入的几条里，**同一篇文档占了几个位置**（多样性——3 个位置给同一篇文档的
     不同小节，等于花同样的 token 却只给了一个来源的信息）？

用法（backend/ 下）：
    RAG_PARENT_CHILD=1 ./venv/Scripts/python.exe scripts/diag_floor_margin.py
    RAG_PARENT_CHILD=1 ./venv/Scripts/python.exe scripts/diag_floor_margin.py -v  # 全量逐条

退出码：0 = 无硬误伤；1 = 存在库内题被 floor 拒成 0 注入（需要走 XB 流程处理）。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("RAG_PARENT_CHILD", "1")  # 必须在 import CONFIG 之前

from agent.rag.indexer import load_knowledge_index  # noqa: E402
from agent.rag.retriever import CONFIG, retrieve_knowledge_docs  # noqa: E402

CASES_PATH = "rag_eval_cases.json"
# 只把这几种当作「库里应该有答案」的题。cascade/follow-up 依赖上下文，oos 本就该被拒。
INSCOPE_KINDS = {"in-kb", "kb-interview"}


def load_cases() -> list[dict]:
    with open(CASES_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else data.get("cases", data)


def short(source: str) -> str:
    return source.replace("cs/", "") if source else "?"


def source_dup_stats(docs) -> tuple[int, int]:
    """返回 (注入条数, 出现次数最多的那篇文档占了几个位置)。"""
    counts: dict[str, int] = {}
    for d in docs:
        src = short((d.metadata or {}).get("source", ""))
        counts[src] = counts.get(src, 0) + 1
    return len(docs), (max(counts.values()) if counts else 0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", "--verbose", action="store_true", help="打印逐条明细")
    args = ap.parse_args()

    store = load_knowledge_index(persist_directory="chroma_kb")
    if store is None:
        print("!! chroma_kb 打不开")
        return 2

    print(f"config: parent_child={CONFIG.parent_child} coarse_k={CONFIG.coarse_k} "
          f"floor={CONFIG.relevance_floor} per_doc_floor={CONFIG.per_doc_floor}\n")

    cases = load_cases()
    hard_miss: list[dict] = []
    hard_miss: list[dict] = []
    gray: list[dict] = []
    dup_rows: list[tuple[str, int, int]] = []

    for c in cases:
        query, cid, kind = c.get("query", ""), c.get("id", "?"), c.get("kind", "")
        trace: dict = {}
        docs = retrieve_knowledge_docs(store, query, topic="", trace=trace)
        n, max_dup = source_dup_stats(docs)
        top = trace.get("relevance_top_score")
        row = {
            "id": cid, "kind": kind, "query": query,
            "n": n, "top": top, "max_dup": max_dup,
            "sources": [short((d.metadata or {}).get("source", "")) for d in docs],
        }
        if n and max_dup >= 2:
            dup_rows.append((cid, n, max_dup))
        if kind not in INSCOPE_KINDS:
            continue
        if not n:
            hard_miss.append(row)
        elif top is not None and top < CONFIG.relevance_floor + 1.0:
            gray.append(row)

        if args.verbose:
            print(f"[{kind}] {cid} {query}")
            print(f"  注入 {n} 条 top={top} 单篇最多 {max_dup} 个位置 {row['sources']}")

    inscope = [c for c in cases if c.get("kind") in INSCOPE_KINDS]
    print("=" * 66)
    print(f"库内题（{INSCOPE_KINDS}）共 {len(inscope)} 条")
    print(f"  被 floor 拒成 0 注入（硬误伤）：{len(hard_miss)} 条")
    for r in hard_miss:
        print(f"    MISS {r['id']} top={r['top']} :: {r['query']}")
    print(f"  勉强过闸（top 落在 floor 上方 1 分内的灰区）：{len(gray)} 条")
    for r in gray:
        print(f"    GRAY {r['id']} top={r['top']} 余量={round(r['top'] - CONFIG.relevance_floor, 3)} "
              f":: {r['query']}")
    print()
    print(f"注入多样性：{len(dup_rows)} 条出现「同一文档占 ≥2 个注入位」"
          f"（共 {len(cases)} 条），明细 = {dup_rows}")
    return 1 if hard_miss else 0


if __name__ == "__main__":
    sys.exit(main())
