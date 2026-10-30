"""一次性诊断：逐条相关性下限（相对现状的整题级 floor）该定在哪。

现状 floor 是**整题级**的——只看重排最高分，最高分过线就把 top_k 全放进来。
后果：问 TIME_WAIT 时 top1 拿 4.635，尾部 -5.173 的「SJF / RR 时间片」切片也被注入。

本脚本量化「逐条下限」在各阈值下的取舍：砍掉了多少、砍掉的是明显垃圾
（与 top1 不同文档）还是可能误杀（与 top1 同文档的弱相关邻居）、注入条数变成多少。

**诊断单元必须与生产注入单元一致**（2026-10-25 扩库后修）：父子库里向量存的是
child，而 `/chat` 注入的是聚合后的 parent。早期版本直接拿 child 打分、并用 child 的
`start_index` 对 parent 坐标的金标准——`hit_kept` 恒为 0，看起来像"怎么调阈值都没用"。
现在先按 `CONFIG.parent_child` 聚合再重排，注入窗口用 `injected_k()`（父子 3 / 扁平 4）。

不进 CI。用法：backend/ 下 ./venv/Scripts/python.exe scripts/diag_perdoc_floor.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(BACKEND_DIR / "scripts"))

import smoke_rag as S  # noqa: E402
from agent.rag import retriever  # noqa: E402
from agent.rag.indexer import load_knowledge_index  # noqa: E402
from agent.rag.retriever import CONFIG, get_reranker  # noqa: E402

# (标签, 模式, 值)：abs=绝对逐条下限，rel=相对 top1 的落差（自适应不同 query 的分数尺度）
THRESHOLDS: list[tuple[str, str, float]] = [
    ("现状", "abs", float("-inf")),
    ("abs -6.0", "abs", -6.0),
    ("abs -5.0", "abs", -5.0),
    ("abs -4.0", "abs", -4.0),
    ("rel top1-10", "rel", 10.0),
    ("rel top1-8", "rel", 8.0),
    ("rel top1-6", "rel", 6.0),
    ("rel top1-5", "rel", 5.0),
]


def src(d) -> str:
    return os.path.basename(str((d.metadata or {}).get("source", "?")))


def main() -> None:
    cases = S.load_cases()
    vs = load_knowledge_index("cs_basics", str(BACKEND_DIR / "chroma_kb"))
    enc = get_reranker()
    if vs is None or enc is None:
        raise SystemExit("库或重排模型未就绪")

    # 每题只跑一次检索+重排，阈值在分数上复算
    inject_k = S.injected_k()
    unit = "parent（父子检索聚合）" if CONFIG.parent_child else "切片（扁平）"
    per_case = []
    for query, _expected, _kind, topic, rel in cases:
        # 与生产同一拼接口径：只在消息自足时才拼 topic
        sq = (
            f"{topic} {query}".strip()
            if retriever._should_augment_with_topic(query, topic)
            else query
        )
        cands = vs.similarity_search(sq, k=CONFIG.coarse_k)
        sparse = retriever._bm25_search(vs, sq, CONFIG.coarse_k)
        if sparse:
            cands = retriever._rrf_fuse([cands, sparse], limit=CONFIG.coarse_k)
        # 与 /chat 同一条链路：父子模式先把 child 聚合成 parent 再重排，
        # 否则打分的单元和生产注入的单元不是同一个，阈值量出来也没法用
        if CONFIG.parent_child:
            cands = retriever.aggregate_parent_docs(cands)
        scores = list(enc.rerank(sq, [d.page_content for d in cands], batch_size=8))
        order = sorted(range(len(cands)), key=lambda i: scores[i], reverse=True)
        # 切片级金标准（见 eval/build_gold）：[source, start_index]（粒度跟随库形态）
        gold = set()
        for item in rel or []:
            if len(item) >= 2:
                gold.add((os.path.basename(str(item[0])), item[1]))
        per_case.append(
            (
                query,
                [(cands[i], scores[i]) for i in order[:inject_k]],
                gold,
            )
        )

    scored = [c for c in per_case if c[2]]
    total_gold = sum(len(c[2]) for c in scored)
    print(
        "题数 %d（有切片级金标准 %d 条，金标准共 %d 片），诊断单元=%s，注入窗口=%d\n"
        % (len(per_case), len(scored), total_gold, unit, inject_k)
    )
    print(
        "%-9s %9s %9s %11s %11s %11s %9s"
        % (
            "逐条下限",
            "平均注入",
            "Recall@%d" % inject_k,
            "Precision@%d" % inject_k,
            "砍掉-真垃圾",
            "砍掉-真误杀",
            "整题空",
        )
    )
    empty_queries: list[tuple[str, float]] = []
    for label, mode, value in THRESHOLDS:
        kept, hit_kept, cut_noise, cut_gold, empty = 0, 0, 0, 0, 0
        for _q, ranked, gold in per_case:
            if not ranked:
                continue
            # 整题级 floor 先跑一遍（现状行为）
            if CONFIG.relevance_floor is not None and ranked[0][1] < CONFIG.relevance_floor:
                empty += 1
                if label == THRESHOLDS[0][0]:
                    # 整题空只由 relevance_floor 决定，与逐条阈值无关 → 记一次即可。
                    # 有金标准却被整题挡掉的题，就是 relevance_floor 的**误杀候选**，必须点名。
                    empty_queries.append((_q, ranked[0][1]))
                continue
            cutoff = value if mode == "abs" else ranked[0][1] - value
            for d, sc in ranked:
                key = (src(d), (d.metadata or {}).get("start_index"))
                is_gold = key in gold
                if sc < cutoff:
                    if is_gold:
                        cut_gold += 1
                    else:
                        cut_noise += 1
                    continue
                kept += 1
                if is_gold:
                    hit_kept += 1
        print(
            "%-11s %9.2f %9.4f %11.4f %11d %11d %9d"
            % (
                label,
                kept / len(per_case),
                hit_kept / total_gold,
                hit_kept / kept if kept else 0.0,
                cut_noise,
                cut_gold,
                empty,
            )
        )

    if empty_queries:
        print(
            "\n整题空（最高分 < relevance_floor=%s）%d 条 —— 有金标准却被整题挡掉，是 floor 的误杀候选："
            % (CONFIG.relevance_floor, len(empty_queries))
        )
        for query, score in empty_queries:
            print("  %-34s top1=%.2f" % (query[:32], score))


if __name__ == "__main__":
    main()
