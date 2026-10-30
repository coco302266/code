"""为评测集生成「结构化难负例」标注（HN2 修正版：整节零增益）。

## 为什么不用「同文档相邻小节」

2026-10-28 实测发现：现有 gold 在 **parent 粒度上是全相关**的——
79/79 条 case 的金标准切片覆盖了其所在文档的全部 parent（覆盖率 1.0）。
所以「相邻小节」在标注里也是 gold，永远不可能成为负例。
（这同时解释了为什么 Recall@3 上限停在 0.9159 而不是 1.0：
金标准是 slice 级、4 条/题，不是 parent 级。）

## 可用的硬负例在哪

`relevant_chunks` 带三档 gain（1.0 / 0.5 / 0.0），分布 117 / 123 / 112。
**gain=0 就是标注阶段判定的"同文档里不相关"** —— 这是天然的、无需人工重标的
硬负例。本脚本按 **parent 粒度**聚合：若某个 parent 底下被标注的切片
**全部 gain=0**，该 parent 整体判为硬负例 parent。

判据故意保守（要求"整节都零增益"）：
只要节里有一条 gain>0 的切片，它就可能是答案的一部分，不能当负例——否则
会把"正确的节"算成误注入，指标反过来奖励错误行为。

## 字段约定（向后兼容）

    hard_negatives: ["cs/os/deadlock.md#3", ...]   # parent_id
    hard_neg_source: "zero-gain-parent"
    hard_neg_slices: [[source, start_index], ...]  # 支撑该判定的零增益切片

用法（backend/ 下）：

    RAG_PARENT_CHILD=1 venv/Scripts/python.exe scripts/build_rerank_hardneg.py \
        --in rag_eval_cases.json --out eval/cases_rerank_v2.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from agent.rag.indexer import load_knowledge_index  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parent.parent
PERSIST_DIR = str(BACKEND_DIR / "chroma_kb")
COLLECTION = "cs_basics"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default=str(BACKEND_DIR / "rag_eval_cases.json"))
    ap.add_argument("--out", default=str(BACKEND_DIR / "eval" / "cases_rerank_v2.json"))
    args = ap.parse_args(argv)

    data = json.loads(Path(args.inp).read_text(encoding="utf-8"))
    cases = data.get("cases") or []
    store = load_knowledge_index(COLLECTION, PERSIST_DIR)
    col = getattr(store, "_collection", None)
    if col is None:
        raise SystemExit("vectorstore 没有 _collection，无法读取元数据")
    metas = list(col.get(include=["metadatas"]).get("metadatas") or [])

    child_to_parent: dict[tuple[str, int], str] = {}
    parent_of_source: dict[str, dict[int, str]] = defaultdict(dict)
    topic_of_parent: dict[str, str] = {}
    parents_of_topic: dict[str, set[str]] = defaultdict(set)

    for m in metas:
        src = str(m.get("source") or "")
        pid = m.get("parent_id")
        if not src or not pid:
            continue
        try:
            idx = int(m.get("start_index"))
        except (TypeError, ValueError):
            idx = -1
        if idx >= 0:
            child_to_parent[(src, idx)] = pid
        try:
            order = int(str(pid).rsplit("#", 1)[1])
        except (IndexError, ValueError):
            continue
        parent_of_source[src][order] = pid
        topic = str(m.get("topic") or "")
        if topic:
            topic_of_parent[pid] = topic
            parents_of_topic[topic].add(pid)

    if not topic_of_parent:
        raise SystemExit("元数据里没有 parent_id/topic：当前库不是父子模式")

    hit = 0
    counts = []
    gain_hist: Counter = Counter()

    for c in cases:
        rel = c.get("relevant_chunks") or []
        parent_gains: dict[str, list[float]] = defaultdict(list)
        slices_of_parent: dict[str, list[list]] = defaultdict(list)

        for item in rel:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            try:
                src, idx = str(item[0]), int(item[1])
            except (TypeError, ValueError):
                continue
            gain = float(item[2]) if len(item) > 2 else 1.0
            pid = child_to_parent.get((src, idx))
            if not pid:
                continue
            parent_gains[pid].append(gain)
            slices_of_parent[pid].append([src, idx, gain])
            gain_hist[gain] += 1

        # 硬负例：整节所有被标注切片都是零增益
        negs = {
            pid
            for pid, gs in parent_gains.items()
            if gs and all(g <= 0 for g in gs)
        }

        # 兜底：本题没有零增益节 → 取同 topic、但本题未标为相关的节（跨文档）
        if not negs:
            topics = {topic_of_parent.get(pid, "") for pid in parent_gains}
            topics.discard("")
            for t in topics:
                for pid in parents_of_topic.get(t, set()):
                    if pid not in parent_gains:
                        negs.add(pid)
                        if len(negs) >= 3:
                            break
                if negs:
                    break

        if negs:
            c["hard_negatives"] = sorted(negs)
            c["hard_neg_source"] = (
                "zero-gain-parent" if any(
                    parent_gains.get(p) for p in negs
                ) else "same-topic-other-doc"
            )
            support = []
            for p in sorted(negs):
                support.extend(slices_of_parent.get(p, []))
            if support:
                c["hard_neg_slices"] = support[:8]
            else:
                c.pop("hard_neg_slices", None)
            hit += 1
            counts.append(len(negs))
        else:
            c.pop("hard_negatives", None)
            c.pop("hard_neg_source", None)
            c.pop("hard_neg_slices", None)
            counts.append(0)

    stats = {
        "cases_with_neg": hit,
        "cases_without_neg": len(cases) - hit,
        "mean_negs_per_case": round(sum(counts) / max(1, len(counts)), 2),
        "gain_hist": {str(k): v for k, v in sorted(gain_hist.items())},
        "parents_total": len(topic_of_parent),
        "sources_total": len(parent_of_source),
        "note": "hard_negatives 是 parent_id；判据=整节被标注切片全部 gain<=0",
    }

    out = dict(data)
    out["version"] = int(data.get("version", 1)) + 1
    out["note"] = (
        str(data.get("note") or "")
        + " ｜ rerank v2：新增 hard_negatives（整节零增益 parent；"
        "现有 gold 在 parent 粒度为全相关，相邻节不可用），见 scripts/build_rerank_hardneg.py"
    )
    out["cases"] = cases
    out["hard_neg_stats"] = stats

    p = Path(args.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("STAT " + json.dumps(stats, ensure_ascii=False))
    print(f"已写入 {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
