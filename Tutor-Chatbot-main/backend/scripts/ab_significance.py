"""配对 bootstrap 显著性检验：对比两份含 per_case 的 ceiling 报告。

用法：
    python scripts/ab_significance.py --base eval/reports/ceiling_V3_BASE_*.json \
        --alt eval/reports/ceiling_V3_RR_JINA_*.json

为什么用配对 bootstrap：
    同一批题上两档共用题目与金标准，逐题差值才是可比信号；
    只比均值会把「题目间差异」和「模型间差异」混在一起，小样本下尤其危险。
"""

from __future__ import annotations

import argparse
import json
import random
import statistics as st
from pathlib import Path

METRICS = [
    ("actual_recall", "Recall@3"),
    ("actual_ndcg", "nDCG@3"),
    ("actual_precision", "Precision@3"),
    ("noise_rate_actual", "噪声率(越低越好)"),
]


def load(path: Path) -> dict[str, dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("per_case") or []
    if not rows:
        raise SystemExit("报告没有 per_case，无法做配对检验：%s" % path)
    return {str(r["query"]): r for r in rows}


def bootstrap_ci(diffs: list[float], n: int = 20000, seed: int = 42) -> tuple[float, float, float]:
    rng = random.Random(seed)
    m = len(diffs)
    means = []
    for _ in range(n):
        s = 0.0
        for _ in range(m):
            s += diffs[rng.randrange(m)]
        means.append(s / m)
    means.sort()
    return (
        means[int(0.025 * n)],
        means[int(0.975 * n)],
        sum(1 for x in means if x > 0) / n,
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="基线报告（含 per_case）")
    ap.add_argument("--alt", required=True, help="对照报告（含 per_case）")
    ap.add_argument("--n", type=int, default=20000, help="bootstrap 重采样次数")
    args = ap.parse_args(argv)

    base = load(Path(args.base))
    alt = load(Path(args.alt))
    keys = sorted(set(base) & set(alt))
    if not keys:
        raise SystemExit("两份报告没有共同题目")

    print("配对题目数：", len(keys))
    print("基线：%s\n对照：%s\n" % (args.base, args.alt))
    print(f"{'指标':22}{'基线':>9}{'对照':>9}{'差值(对照-基线)':>18}{'95%CI':>22}{'胜负':>14}{'需样本':>9}  结论")
    print("-" * 118)

    verdicts = []
    for field, label in METRICS:
        if field not in (base[keys[0]] or {}):
            continue
        b = [float(base[k].get(field) or 0.0) for k in keys]
        a = [float(alt[k].get(field) or 0.0) for k in keys]
        diffs = [y - x for x, y in zip(b, a)]   # 对照 − 基线
        lo, hi, p_pos = bootstrap_ci(diffs, args.n)
        wins = sum(1 for d in diffs if d > 1e-9)      # 对照更好
        losses = sum(1 for d in diffs if d < -1e-9)
        ties = len(diffs) - wins - losses
        mean_d = st.mean(diffs)
        # 检出「当前观测到的效应」需要的样本量（power=0.8, α=.05）
        sigma = st.pstdev(diffs) if len(diffs) > 1 else 0.0
        n_need = (
            int((2.80 * sigma / abs(mean_d)) ** 2) if abs(mean_d) > 1e-9 else 0
        )
        # 噪声率越低越好，方向相反
        if "噪声" in label:
            better = "对照更优" if hi < 0 else ("基线更优" if lo > 0 else "不显著")
        else:
            better = "对照更优" if lo > 0 else ("基线更优" if hi < 0 else "不显著")
        verdicts.append((label, mean_d, lo, hi, better))
        print(
            f"{label:22}{st.mean(b):>9.4f}{st.mean(a):>9.4f}{mean_d:>+18.4f}"
            f"{'[%+.4f, %+.4f]' % (lo, hi):>22}"
            f"{'%d胜/%d负/%d平' % (wins, losses, ties):>14}{n_need:>9}"
            f"  {better}"
        )

    print("\n=== 判定（95% CI 是否跨 0）===")
    for label, d, lo, hi, better in verdicts:
        sig = "显著" if (lo > 0 or hi < 0) else "不显著"
        print(f"  {label}: {sig}  {better}  差值 {d:+.4f}  CI [{lo:+.4f}, {hi:+.4f}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
