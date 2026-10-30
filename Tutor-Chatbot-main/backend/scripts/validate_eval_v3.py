"""校验 v3 评测集：紧致度、结构完整性、可加载性、可检出效应(MDE)。

用法：
    python scripts/validate_eval_v3.py --cases eval/cases_v3.json
    python scripts/validate_eval_v3.py --cases eval/cases_v3.json --run-sample 40  # 实测抽样
"""

from __future__ import annotations

import argparse
import json
import math
import statistics as st
import sys
from collections import Counter
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))


def gain_of(item) -> float:
    if len(item) > 2:
        try:
            return float(item[2])
        except (TypeError, ValueError):
            return 1.0
    return 1.0


def profile(cases: list[dict]) -> dict:
    per_case, gcount = [], Counter()
    for c in cases:
        gains = [gain_of(x) for x in (c.get("relevant_chunks") or [])]
        gcount.update(round(g, 1) for g in gains)
        per_case.append(
            (
                len(gains),
                sum(1 for g in gains if g >= 1.0),
                sum(1 for g in gains if 0 < g < 1.0),
                sum(1 for g in gains if g <= 0.0),
            )
        )
    n = len(per_case) or 1
    return {
        "cases": len(per_case),
        "gain_dist": dict(gcount),
        "mean_entries": round(sum(p[0] for p in per_case) / n, 2),
        "mean_gain1": round(sum(p[1] for p in per_case) / n, 2),
        "mean_gain05": round(sum(p[2] for p in per_case) / n, 2),
        "mean_gain0": round(sum(p[3] for p in per_case) / n, 2),
        "cases_without_positive": sum(1 for p in per_case if p[1] == 0),
        "cases_single_answer": sum(1 for p in per_case if p[1] == 1),
        "cases_all_positive": sum(1 for p in per_case if p[3] == 0 and p[2] == 0),
    }


def mde_from_paired(base_json: Path, alt_json: Path, ns: list[int]) -> dict:
    """用真实逐题差值估最小可检出效应。

    MDE ≈ (z_{1-α/2} + z_{power}) * σ_diff / sqrt(N)，α=0.05 双侧、power=0.8
    ⇒ 系数 2.80。σ_diff 取既有 A/B 两档逐题 nDCG 差值的标准差（最贴近真实噪声）。
    """
    def load(path: Path, key: str) -> dict:
        d = json.loads(path.read_text(encoding="utf-8"))
        return {c["query"]: c[key] for c in d["per_case"]}

    b = load(base_json, "actual_ndcg")
    a = load(alt_json, "actual_ndcg")
    keys = sorted(set(b) & set(a))
    diffs = [a[k] - b[k] for k in keys]
    sigma = st.pstdev(diffs) if len(diffs) > 1 else 0.0
    out = {"paired_cases": len(diffs), "sigma_diff": round(sigma, 4)}
    for n in ns:
        out[f"mde_n{n}_pp"] = round(2.80 * sigma / math.sqrt(n) * 100, 2)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default=str(BACKEND_DIR / "eval" / "cases_v3.json"))
    ap.add_argument("--old", default=str(BACKEND_DIR / "rag_eval_cases.json"))
    ap.add_argument("--run-sample", type=int, default=0, help="实测抽样题数（含重排，较慢）")
    args = ap.parse_args(argv)

    path = Path(args.cases)
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = [c for c in data.get("cases", []) if c.get("kind") not in (None, "cascade")]
    print("=== v3 结构 ===")
    print("题数:", len(cases), " gold_granularity:", data.get("gold_granularity"))
    print("kind 分布:", dict(Counter(c.get("kind") for c in cases)))

    # 结构完整性
    ids = [c.get("id") for c in cases]
    queries = [str(c.get("query") or "").strip() for c in cases]
    dup_ids = [k for k, v in Counter(ids).items() if v > 1]
    dup_q = [k for k, v in Counter(queries).items() if v > 1 and k]
    empty_q = sum(1 for q in queries if not q)
    empty_gold = sum(1 for c in cases if not c.get("relevant_chunks"))
    print("\n=== 完整性 ===")
    print("id 重复:", len(dup_ids), "| query 重复:", len(dup_q), "| 空 query:", empty_q,
          "| 空金标准:", empty_gold)

    pv3 = profile(cases)
    print("\n=== v3 紧致度 ===")
    for k, v in pv3.items():
        print(f"  {k}: {v}")

    old_path = Path(args.old)
    if old_path.exists():
        old = [
            c for c in json.loads(old_path.read_text(encoding="utf-8")).get("cases", [])
            if c.get("kind") != "cascade" and c.get("expected")
        ]
        po = profile(old)
        print("\n=== 旧集对照（71 题）===")
        for k in ("cases", "mean_entries", "mean_gain1", "mean_gain05", "mean_gain0",
                  "cases_without_positive", "cases_single_answer", "cases_all_positive"):
            print(f"  {k}: 旧={po[k]}  v3={pv3[k]}")

    # MDE
    pc_base = BACKEND_DIR / "eval" / "reports" / "ceiling_BASE_K3_pc.json"
    pc_alt = BACKEND_DIR / "eval" / "reports" / "ceiling_EMB_JINA_pc.json"
    if pc_base.exists() and pc_alt.exists():
        print("\n=== 可检出效应（MDE, nDCG, α=0.05/power=0.8）===")
        print(json.dumps(mde_from_paired(pc_base, pc_alt, [71, 300, len(cases)]),
                         ensure_ascii=False, indent=2))

    # 可加载性
    print("\n=== 可加载性（smoke_rag.load_cases）===")
    sys.path.insert(0, str(BACKEND_DIR / "scripts"))
    import smoke_rag as S  # noqa: E402

    loaded = S.load_cases(str(path))
    print("load_cases 成功，条目:", len(loaded), " 全为 5 元组:",
          all(len(t) == 5 for t in loaded))

    # 抽样实测
    if args.run_sample > 0:
        from agent.rag import retriever as R  # noqa: E402
        from agent.rag.indexer import load_knowledge_index  # noqa: E402
        from eval.metrics import aggregate, chunk_rel  # noqa: E402

        store = load_knowledge_index("cs_basics", "chroma_kb")
        sample = loaded[: args.run_sample]
        R.get_knowledge_context(store, "预热", k=1)
        rels = []
        for query, expected, kind, topic, relevant in sample:
            docs = R.retrieve_knowledge_docs(store, query, topic=topic or "", trace={})
            rels.append(chunk_rel(docs, relevant, expected, k=len(docs) or 1))
        agg = aggregate(rels)
        print("\n=== 抽样实测（%d 题，含重排）===" % len(sample))
        print(json.dumps(agg, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
