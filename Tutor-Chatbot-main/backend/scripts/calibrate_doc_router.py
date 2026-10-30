"""标定文档路由（L1）：量路由召回、域外覆盖率分布，并做完整链路 A/B。

分两段，先便宜后昂贵：

    ① route  只跑路由（一次全库 BM25 打分 + 一次宽向量召回），秒级。
             回答：top-N 篇能不能框住金标准文档？库内题与库外题的
             「查询词覆盖率」分布能不能分开（决定闸门要不要开）？

    ② full   路由开/关各跑一遍完整检索链路（含重排），分钟级——重排按条计费。

用法：
    ./venv/Scripts/python.exe scripts/calibrate_doc_router.py           # 只跑 ①
    ./venv/Scripts/python.exe scripts/calibrate_doc_router.py --full    # ①② 都跑

为什么闸门要单独标定：题库里 oos 只有 4 条，用它定一条会**静默丢掉整轮资料**
的阈值是不够的。这里额外带一组域外探针（其他学科/语言/日常问题），扩大负例面。
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 与线上运行配置一致（README/手册的默认跑法就是父子模式）。
os.environ.setdefault("RAG_PARENT_CHILD", "1")

from agent.rag import doc_router  # noqa: E402
from agent.rag.indexer import load_knowledge_index  # noqa: E402

CASES_FILE = Path(__file__).resolve().parents[1] / "rag_eval_cases.json"
SKIP_KINDS = {"cascade"}

# 额外的域外探针：题库只覆盖 cs 四门课，负例太少定不了阈值。
# 按「离题库多远」分三档，越往后越远——闸门只要挡住后两档就够，
# 挡不住「同属计算机但没文档」的那档也是可接受的（floor 还会再拦一次）。
OOS_PROBES = [
    # 同属计算机，但库里确实没有语言/前端/工程化文档
    "Python 里列表和元组有什么区别",
    "Java 的垃圾回收是怎么工作的",
    "React 的 useEffect 依赖数组怎么写",
    "git 怎么回退到上一个版本",
    "MySQL 的 explain 怎么看执行计划",
    "Redis 的持久化有哪几种方式",
    "Docker 镜像分层是怎么实现的",
    "Kubernetes 的 pod 和容器什么关系",
    # 其他学科
    "牛顿第二定律的公式是什么",
    "微积分里导数和微分有什么区别",
    "光合作用的两个阶段分别产生什么",
    "中国的朝代顺序是怎样的",
    "概率论里条件概率怎么算",
    "经济学里的边际效用是什么意思",
    # 日常/闲聊
    "今天天气怎么样",
    "红烧肉怎么做才好吃",
    "推荐几部好看的科幻电影",
    "怎么提高睡眠质量",
    "明天去北京的高铁几点发车",
]


def load_cases() -> list[dict]:
    data = json.loads(CASES_FILE.read_text(encoding="utf-8"))
    return [
        c
        for c in data.get("cases", [])
        if c.get("kind") not in SKIP_KINDS and (c.get("query") or "").strip()
    ]


def gold_sources(case: dict, topic_to_source: dict[str, str]) -> set[str]:
    """这一例的金标准文档集合（优先切片级金标准，其次 topic→source 映射）。"""
    chunks = case.get("relevant_chunks") or []
    if chunks:
        return {c[0] for c in chunks if c}
    return {
        topic_to_source[t]
        for t in (case.get("expected") or [])
        if t in topic_to_source
    }


def build_topic_map(store) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for meta in store.get().get("metadatas") or []:
        meta = meta or {}
        if meta.get("topic") and meta.get("source"):
            mapping.setdefault(str(meta["topic"]), str(meta["source"]))
    return mapping


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(p / 100 * (len(ordered) - 1))))
    return ordered[idx]


def _describe(label: str, values: list[float]) -> None:
    if not values:
        print(f"  {label}: 无数据")
        return
    print(
        f"  {label}（n={len(values)}）min={min(values):.3f} "
        f"p05={_pct(values, 5):.3f} 中位={statistics.median(values):.3f} "
        f"p95={_pct(values, 95):.3f} max={max(values):.3f}"
    )


def section_route(store, cases) -> dict:
    print("=" * 78)
    print("① 路由召回与域外覆盖率")
    print("=" * 78)
    print(f"RAG_ROUTER_TOP_DOCS={doc_router.top_docs()} "
          f"RAG_ROUTER_WIDE_K={doc_router.wide_k()}")

    topic_map = build_topic_map(store)
    limit = doc_router.top_docs()
    ks = sorted({1, 2, 3, limit})
    recall = {k: [0, 0] for k in ks}
    inside_scores: list[float] = []
    inside_cover: list[tuple[str, float]] = []
    outside_cover: list[tuple[str, float, str]] = []
    misses: list[tuple[str, str, list[str]]] = []

    for case in cases:
        query = (case.get("query") or "").strip()
        topic = (case.get("topic") or "").strip()
        kind = case.get("kind")
        from agent.rag.retriever import _should_augment_with_topic

        sq = (
            f"{topic} {query}".strip()
            if _should_augment_with_topic(query, topic)
            else query
        )
        # need_coverage 始终算：这一段就是为闸门取数，不能省
        route = doc_router.route_documents(store, sq, force_coverage=True)
        if not route.sources:
            print(f"  [!] 路由无结果 kind={kind} q={query!r} reason={route.reason}")
            continue

        if kind == "oos":
            outside_cover.append((query, max(route.coverage or [0.0]), route.sources[0]))
            continue

        gold = gold_sources(case, topic_map)
        if not gold:
            continue
        inside_scores.append(route.scores[0])
        inside_cover.append((query, max(route.coverage or [0.0])))
        for k in ks:
            if gold & set(route.sources[:k]):
                recall[k][0] += 1
            recall[k][1] += 1
        if not (gold & set(route.sources[:limit])):
            misses.append((query, sorted(gold)[0], route.sources))

    for probe in OOS_PROBES:
        route = doc_router.route_documents(store, probe, force_coverage=True)
        if route.sources:
            outside_cover.append(
                (probe, max(route.coverage or [0.0]), route.sources[0])
            )

    print("\n-- 路由召回（金标准文档进入 top-k）--")
    for k in ks:
        hit, total = recall[k]
        if total:
            print(f"  top{k}: {hit}/{total} = {hit / total:.1%}")

    in_vals = [c for _, c in inside_cover]
    out_vals = [c for _, c, _ in outside_cover]

    print("\n-- 库内题（金标准文档被路由到时的聚合分与覆盖率）--")
    _describe("聚合分 top1", inside_scores)
    print("\n-- 查询词覆盖率：库内 vs 库外（闸门判据）--")
    _describe("库内覆盖率", in_vals)
    _describe("库外覆盖率", out_vals)

    if in_vals and out_vals:
        out_max = max(out_vals)
        kill = [c for c in in_vals if c < out_max]
        print(
            f"\n  若取库外最高 {out_max:.3f} 作闸门 → 误杀库内 "
            f"{len(kill)}/{len(in_vals)} 例"
        )
        print("  逐条库外覆盖率（高到低）：")
        for q, c, src in sorted(outside_cover, key=lambda x: -x[1]):
            flag = "  ← 与库内重叠" if c >= min(in_vals) else ""
            print(f"    {c:.3f}  {q}（最近：{src}）{flag}")

    section_gate_sweep(inside_cover, outside_cover)

    if misses:
        print(f"\n-- 路由未覆盖金标准文档的 {len(misses)} 例（收窄的代价）--")
        for query, gold, got in misses:
            print(f"  {query}\n    金标准={gold}  路由={got}")

    return {"recall": recall, "inside_cover": inside_cover}


def section_gate_sweep(
    inside_cover: list[tuple[str, float]],
    outside_cover: list[tuple[str, float, str]],
) -> None:
    """扫一遍阈值，把「挡住多少库外题」与「误杀多少库内题」摆在一起看。

    单看某一条分布没有意义——闸门的代价永远是双向的，必须同时看到两边。
    """
    if not inside_cover or not outside_cover:
        return
    print("\n-- 闸门阈值扫描（覆盖率下限）--")
    print(f"  {'阈值':>6} {'挡掉库外':>10} {'误杀库内':>10}   说明")
    for thr in (0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50):
        killed_out = [q for q, c, _ in outside_cover if c < thr]
        killed_in = [(q, c) for q, c in inside_cover if c < thr]
        print(
            f"  {thr:>6.2f} {len(killed_out):>4}/{len(outside_cover):<5} "
            f"{len(killed_in):>4}/{len(inside_cover):<5}"
        )
        for q, c in killed_in:
            print(f"          误杀 {c:.3f}  {q}")

    # 推荐值：在「不误杀库内题」的前提下,取能挡掉最多库外题的那一档
    best = None
    for thr in [i / 100 for i in range(20, 60, 1)]:
        killed_in = [1 for _, c in inside_cover if c < thr]
        killed_out = [1 for _, c, _ in outside_cover if c < thr]
        if not killed_in and killed_out:
            score = len(killed_out)
            if best is None or score > best[0]:
                best = (score, thr)
    if best:
        print(
            f"\n  零误杀前提下最激进的阈值：{best[1]:.2f}"
            f"（挡掉 {best[0]}/{len(outside_cover)} 条库外探针）"
        )
    else:
        print("\n  没有任何阈值能在不误杀库内题的前提下挡掉库外题。")


def section_full(store, cases) -> None:
    """完整链路 A/B：路由关 vs 开。"""
    from agent.rag import retriever

    print("\n" + "=" * 78)
    print("② 完整链路 A/B（top1 命中 / 注入条数 / 库外误注入）")
    print("=" * 78)

    topic_map = build_topic_map(store)

    def run(tag: str, router_on: bool) -> dict:
        old = os.environ.get("RAG_DOC_ROUTER")
        os.environ["RAG_DOC_ROUTER"] = "1" if router_on else "0"
        hits = total = injected = empty = oos_injected = 0
        per_case = []
        try:
            for case in cases:
                query = (case.get("query") or "").strip()
                topic = (case.get("topic") or "").strip()
                trace: dict = {}
                docs = retriever.retrieve_knowledge_docs(
                    store, query, topic=topic, trace=trace
                )
                injected += len(docs)
                if not docs:
                    empty += 1
                if case.get("kind") == "oos":
                    if docs:
                        oos_injected += 1
                    continue
                gold = gold_sources(case, topic_map)
                if not gold:
                    continue
                total += 1
                top = (docs[0].metadata or {}).get("source") if docs else None
                hit = top in gold
                hits += 1 if hit else 0
                per_case.append(
                    {"id": case.get("id"), "hit": hit, "n": len(docs),
                     "top": top, "gold": sorted(gold)[0]}
                )
        finally:
            if old is None:
                os.environ.pop("RAG_DOC_ROUTER", None)
            else:
                os.environ["RAG_DOC_ROUTER"] = old

        print(
            f"\n[{tag}] top1 {hits}/{total} = {hits / max(total, 1):.1%}；"
            f"总注入 {injected} 条（均 {injected / max(total, 1):.2f}）；"
            f"零注入 {empty} 例；库外误注入 {oos_injected} 例"
        )
        return {"hits": hits, "total": total, "cases": per_case}

    off = run("路由关（现状）", False)
    on = run("路由开", True)

    regress = [
        (a, b) for a, b in zip(off["cases"], on["cases"])
        if a["id"] == b["id"] and a["hit"] and not b["hit"]
    ]
    improve = [
        (a, b) for a, b in zip(off["cases"], on["cases"])
        if a["id"] == b["id"] and b["hit"] and not a["hit"]
    ]
    print("\n-- 逐例差异 --")
    print(f"  路由后变差 {len(regress)} 例，变好 {len(improve)} 例")
    for a, _ in regress:
        print(f"    - {a['id']}: 金标准={a['gold']} 原top={a['top']}")
    for _, b in improve:
        print(f"    + {b['id']}: 金标准={b['gold']} 新top={b['top']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="标定文档路由（L1）")
    parser.add_argument("--full", action="store_true", help="额外跑完整链路 A/B（分钟级）")
    parser.add_argument(
        "--persist-dir", default=os.getenv("CHROMA_DIR", "chroma_kb"),
        help="知识库持久化目录",
    )
    args = parser.parse_args()

    store = load_knowledge_index(persist_directory=args.persist_dir)
    if store is None:
        raise SystemExit(f"找不到知识库：{args.persist_dir}")
    print(f"知识库 {args.persist_dir}：{store._collection.count()} 片")

    cases = load_cases()
    print(f"用例 {len(cases)} 条（已剔除 cascade），另加 {len(OOS_PROBES)} 条域外探针")

    section_route(store, cases)
    if args.full:
        section_full(store, cases)


if __name__ == "__main__":
    main()
