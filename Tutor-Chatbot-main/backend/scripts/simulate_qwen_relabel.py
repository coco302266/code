"""把「标注差异」换算成「检索指标变化」——换裁判模型前的决策依据。

为什么要有这一步
----------------
`compare_annotators.py` 只能回答「两个裁判打的分像不像」（打分一致率、kappa），
但真正要决定的是：**换成 Qwen 重标之后，历史那一串结论（nDCG / Precision / Recall）
会变成多少、还能不能跟过去比**。一致率是间接证据，指标换算才是直接证据。

做法：
  1. 读 Qwen 的逐点标注（compare_annotators.py --dump-labels 导出）；
  2. 生成一份「假设当年是 Qwen 标的」的影子评测集 cases_v3_qwen.json；
  3. 对同一批问句只跑一次真实检索（含重排），拿到同一份召回结果；
  4. 同一份召回分别用「历史金标准」和「Qwen 影子金标准」算 chunk_rel / aggregate，
     逐题配对求差。

**只检索一次**是关键设计：两份金标准必须压在同一份召回结果上比，
否则会把「检索抖动」混进「标尺变化」，看到的差值就不可归因了。

用法：
    venv/Scripts/python.exe scripts/simulate_qwen_relabel.py
    venv/Scripts/python.exe scripts/simulate_qwen_relabel.py --k 3 --write-cases
"""

from __future__ import annotations

import argparse
import json
import statistics as st
import sys
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(BACKEND_DIR / "scripts"))
load_dotenv(BACKEND_DIR / ".env")

CASES = BACKEND_DIR / "eval" / "cases_v3.json"
LABELS = BACKEND_DIR / "eval" / "reports" / "annotator_labels_qwen_3votes.json"
REPORTS = BACKEND_DIR / "eval" / "reports"


def load_labels(path: Path) -> dict[str, dict[tuple[str, int], float]]:
    """{(source, parent_start): qwen_gain} 按 query 索引。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, dict[tuple[str, int], float]] = {}
    for row in data.get("labels", []):
        q = (row.get("query") or "").strip()
        val = row.get("a")
        if not q or val is None:
            continue
        out.setdefault(q, {})[(row["source"], int(row["parent_start"]))] = float(val)
    return out


def apply_gain(relevant: list, addon: dict[tuple[str, int], float]) -> tuple[list, int]:
    """把 Qwen 的逐点分档盖到原始 relevant_chunks 上，返回 (新列表, 被改动的点数)。"""
    changed = 0
    new_rows = []
    for row in relevant or []:
        if len(row) < 3:
            new_rows.append(list(row))
            continue
        key = (row[0], int(row[1]))
        old = float(row[2])
        new_rows.append([row[0], int(row[1]), addon.get(key, old)])
        if key in addon and addon[key] != old:
            changed += 1
    return new_rows, changed


def relabel_stats(labels: dict[str, dict[tuple[str, int], float]], cases: list[dict]) -> Counter:
    """统计 Qwen 相对历史金标准的改档方向。"""
    cnt: Counter = Counter()
    for c in cases:
        q = (c.get("query") or "").strip()
        addon = labels.get(q)
        if not addon:
            continue
        for row in c.get("relevant_chunks") or []:
            if len(row) < 3:
                continue
            key = (row[0], int(row[1]))
            if key not in addon:
                continue
            old, new = float(row[2]), addon[key]
            if old != new:
                cnt["%s->%s" % (fmt(old), fmt(new))] += 1
    return cnt


def fmt(v: float) -> str:
    return "%g" % v if v != int(v) else "%.1f" % v


def row(label: str, agg: dict) -> None:
    print(
        "  %-14s recall %-7s precision %-7s ndcg %-7s mrr %-7s 噪声率 %-7s"
        % (
            label,
            nz(agg.get("recall_at_k")),
            nz(agg.get("precision_at_k")),
            nz(agg.get("ndcg_at_k")),
            nz(agg.get("mrr")),
            nz(agg.get("noise_rate")),
        )
    )


def nz(v) -> str:
    return "—" if v is None else "%.4f" % v


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", default=str(LABELS))
    ap.add_argument("--cases", default=str(CASES))
    ap.add_argument("--k", type=int, default=3, help="截断位数（0=用召回条数）")
    ap.add_argument("--collection", default="cs_basics")
    ap.add_argument("--persist-dir", default="chroma_kb")
    ap.add_argument("--write-cases", action="store_true", help="顺带落盘影子评测集")
    args = ap.parse_args(argv)

    labels = load_labels(Path(args.labels))
    data = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    cases = [c for c in data.get("cases", []) if c.get("kind") not in (None, "cascade")]

    covered = [c for c in cases if (c.get("query") or "").strip() in labels]
    if not covered:
        raise SystemExit("没有任何受影响的问句，检查 --labels 导出是否为空")

    print("=== 影子重标注范围 ===")
    print("金标准总题数：%d    本次受 Qwen 标注覆盖：%d 题" % (len(cases), len(covered)))
    srcs = sorted({key[0] for table in labels.values() for key in table})
    print("覆盖文档：%s" % ", ".join(srcs))

    shifts = relabel_stats(labels, covered)
    total_points = sum(len(table) for table in labels.values()) or 1
    changed = sum(shifts.values())
    print("\n=== Qwen 相对历史金标准的改档方向（%d 个评分点中改了 %d 个 = %.1f%%）"
          % (total_points, changed, changed / total_points * 100))
    for k, v in shifts.most_common():
        print("  %-12s %3d 处（占评分点 %.1f%%）" % (k, v, v / total_points * 100))

    # —— 同一份召回，两套标尺
    from agent.rag import retriever as R
    from agent.rag.indexer import load_knowledge_index
    from eval.metrics import aggregate, chunk_rel

    print("\n读取索引并预热 ...")
    store = load_knowledge_index(args.collection, args.persist_dir)
    R.get_knowledge_context(store, "预热", k=1)

    pairs = []
    for c in covered:
        q = (c.get("query") or "").strip()
        expected = set(c.get("expected") or [])
        old_rows = c.get("relevant_chunks") or []
        new_rows, _ = apply_gain(old_rows, labels[q])

        docs = R.retrieve_knowledge_docs(store, q, topic=(c.get("expected") or [""])[0] or "", trace={})
        if not docs:
            continue
        kreal = args.k or len(docs)
        rel_old = chunk_rel(docs, old_rows, expected, k=kreal)
        rel_new = chunk_rel(docs, new_rows, expected, k=kreal)
        pairs.append((rel_old, rel_new))

    print("\n=== 同一份召回 × 两套金标准（K=%d，%d 题配对）===" % (args.k or 0, len(pairs)))
    agg_old = aggregate([p[0] for p in pairs])
    agg_new = aggregate([p[1] for p in pairs])
    row("历史金标准", agg_old)
    row("Qwen 重标后", agg_new)

    print("\n=== 逐题配对差值（Qwen − 历史）===")

    def delta(field: str):
        vals = [getattr(b, field) - getattr(a, field) for a, b in pairs]
        moved = sum(1 for v in vals if abs(v) > 1e-9)
        return vals, moved

    summary = {}
    for label, field in (("recall", "recall"), ("precision", "precision"),
                         ("ndcg", "ndcg"), ("mrr", "mrr")):
        vals, moved = delta(field)
        mean = sum(vals) / len(vals) if vals else 0.0
        sd = st.pstdev(vals) if len(vals) > 1 else 0.0
        summary[field] = {"mean_pp": round(mean * 100, 2), "sd_pp": round(sd * 100, 2),
                          "changed_cases": moved}
        print(
            "  %-10s 均值 %+6.2f pp   σ %5.2f pp   有变化的题数 %3d/%d  提升 %2d  下降 %2d"
            % (
                label, mean * 100, sd * 100, moved, len(vals),
                sum(1 for v in vals if v > 1e-9), sum(1 for v in vals if v < -1e-9),
            )
        )

    if args.write_cases:
        shadow = json.loads(Path(args.cases).read_text(encoding="utf-8"))
        touched = 0
        for c in shadow.get("cases", []):
            q = (c.get("query") or "").strip()
            addon = labels.get(q)
            if not addon:
                continue
            rows, changed_n = apply_gain(c.get("relevant_chunks") or [], addon)
            c["relevant_chunks"] = rows
            touched += changed_n
        shadow["note"] = ("【影子集·勿用于历史对比】仅 reflow 目的：%s 个小节/孩子的 gain 被 Qwen 重标点替换。"
                          "原集见 cases_v3.json" % touched)
        shadow["generated_by"] = "scripts/simulate_qwen_relabel.py"
        out = REPORTS / "cases_v3_qwen_shadow.json"
        REPORTS.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(shadow, ensure_ascii=False, indent=2), encoding="utf-8")
        print("\n影子评测集已写入：%s（被替换的评分点 %d 个）" % (out, touched))

    REPORTS.mkdir(parents=True, exist_ok=True)
    rep = REPORTS / ("relabel_impact_%s.json" % json.loads(Path(args.labels).read_text(encoding="utf-8")).get("generated_at", "x"))
    rep.write_text(
        json.dumps(
            {
                "k": args.k,
                "paired_cases": len(pairs),
                "points": total_points,
                "changed_points": changed,
                "shift_directions": dict(shifts),
                "old": agg_old,
                "qwen": agg_new,
                "delta": summary,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(" Impact 报告已写入：%s" % rep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
