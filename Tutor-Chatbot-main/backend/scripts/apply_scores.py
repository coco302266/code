"""把裁判初评结果回填到各 eval_scores.csv（**一次性数据工具，不进 CI**）。

回填规则：
- 按 (run_id, index) 匹配行；comment 列写入 `[AI初评]` 前缀 + 一句理由，
  保证任何人打开表都知道这些分的来源与复核方式。
- 同步更新 reports/{combined,v1,group,debate}/eval_scores.csv 四份。
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]  # backend/（脚本已移入 scripts/）
JUDGE = BACKEND / "reports" / "judge_scores.json"

DIM_KEYS = ["accuracy", "relevance", "completeness", "personalization"]
DIM_LABELS = ["准确性", "相关性", "完整性", "个性化"]

if not JUDGE.exists():
    raise SystemExit("先跑 judge_scores.py")

scores = {
    (r["run_id"], r["index"]): r
    for r in json.loads(JUDGE.read_text(encoding="utf-8"))
    if not r.get("error")
}
print(f"初评结果 {len(scores)} 行")

for scope in ["combined", "v1", "group", "debate"]:
    path = BACKEND / "reports" / scope / "eval_scores.csv"
    if not path.exists():
        continue
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = list(csv.reader(fh))
    header, rows = reader[0], reader[1:]
    col = {name: i for i, name in enumerate(header)}
    hit = miss = 0
    for row in rows:
        key = (int(row[col["run_id"]]), int(row[col["index"]]))
        judge = scores.get(key)
        if judge is None:
            miss += 1
            continue
        hit += 1
        comment = (judge.get("comment") or "").replace("\n", " ")
        row[col["comment"]] = f"[AI初评·待人工复核] {comment}"
        for key_name, label in zip(DIM_KEYS, DIM_LABELS):
            row[col[label]] = str(judge.get(key_name, ""))
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)
    print(f"{scope}: 命中 {hit} 行，未命中 {miss} 行 → {path}")
