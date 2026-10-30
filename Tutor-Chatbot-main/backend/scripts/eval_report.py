"""生成成果 #4《多智能体协作效果评估报告》（M4-C）。

用法：
    python eval_report.py                          # 全部课堂，最近 200 轮
    python eval_report.py --classroom-id 3         # 只看某个课堂
    python eval_report.py --scenario group         # 只看小组讨论
    python eval_report.py --limit 500 --out-dir reports

产出两个文件（默认写在 backend/ 下）：
- `eval_report.md`   客观指标 + 评分说明（给人读的结论框架）
- `eval_scores.csv`  人工评分表，四列留空（准确/相关/完整/个性化各 1–5 分）

**这个脚本不调 LLM、不发 HTTP**，只读 `agent_runs` 做聚合——出报告不该有额度成本，
也不该依赖服务在跑。它和 `/evaluation/*` 端点共用 `agent.evaluation` 的同一份口径，
所以「接口里看到的」和「报告里算的」必然一致。
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # backend/

from agent.evaluation import (  # noqa: E402
    render_report_md,
    render_scores_csv,
    run_to_dict,
    score_sheet_rows,
    summarize_runs,
)
from db.diagnostics import diagnose, empty_result_hint  # noqa: E402
from db.repository import get_messages_by_ids, list_agent_runs  # noqa: E402
from db.session import SessionLocal, db_available  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成多智能体协作效果评估报告")
    parser.add_argument("--classroom-id", type=int, default=None, help="只看某个课堂")
    parser.add_argument("--user-id", type=int, default=None, help="只看某个学生")
    parser.add_argument("--scenario", default=None, help="只看某个场景（v1/group/debate）")
    parser.add_argument("--limit", type=int, default=200, help="最多取多少轮（默认 200）")
    parser.add_argument(
        "--out-dir", default=".", help="产物输出目录（默认当前目录）"
    )
    return parser.parse_args()


def scope_text(args: argparse.Namespace) -> str:
    parts = [f"课堂 {args.classroom_id}" if args.classroom_id else "全部课堂"]
    if args.user_id:
        parts.append(f"学生 {args.user_id}")
    if args.scenario:
        parts.append(f"场景 {args.scenario}")
    parts.append(f"最近 {args.limit} 轮")
    return "｜".join(parts)


def main() -> int:
    args = parse_args()
    if not db_available():
        print("数据库不可用：请检查 backend/.env 的 MYSQL_* 配置并确认 MySQL 已启动。", file=sys.stderr)
        return 1

    limit = max(1, args.limit)
    with SessionLocal() as db:
        # 自检放在查询之前：表缺失时 list_agent_runs 会直接抛，而抛出去的
        # 异常里看不出「表不存在」还是「过滤条件不对」。先诊断，才能把
        # 「样本为 0」解释清楚（脚步生成的报告里会原样带上这段）。
        diagnosis = diagnose(db)
        runs = [
            run_to_dict(r)
            for r in reversed(
                list_agent_runs(
                    db,
                    classroom_id=args.classroom_id,
                    user_id=args.user_id,
                    scenario=args.scenario or None,
                    limit=limit,
                )
            )
        ]
        message_ids = [i for r in runs for i in (r.get("message_ids") or [])]
        messages = get_messages_by_ids(db, message_ids)

    rows = score_sheet_rows(runs, messages)
    summary = summarize_runs(runs)
    if not runs and diagnosis.get("total_runs"):
        # 与端点同一口径：库里有数据却没被选中，是过滤条件的问题。
        diagnosis["hint"] = empty_result_hint(diagnosis["total_runs"])
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    report = render_report_md(
        summary,
        rows,
        scope=scope_text(args),
        generated_at=generated_at,
        diagnosis=diagnosis,
    )

    os.makedirs(args.out_dir, exist_ok=True)
    report_path = os.path.join(args.out_dir, "eval_report.md")
    csv_path = os.path.join(args.out_dir, "eval_scores.csv")
    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write(report)
    with open(csv_path, "w", encoding="utf-8", newline="") as fh:
        fh.write(render_scores_csv(rows))

    print(f"样本轮数：{summary['turns']}｜待评分发言：{len(rows)}")
    print(f"报告：{os.path.abspath(report_path)}")
    print(f"评分表：{os.path.abspath(csv_path)}")
    # 留痕为空时不给结论、只给诊断：四种成因表现完全一样，凭 0 判断不了。
    if summary["turns"] == 0:
        print(
            "\n样本为 0，留痕链路自检："
            f"\n  数据库：{diagnosis.get('database')}"
            f"\n  agent_runs 表：{'存在' if (diagnosis.get('table') or {}).get('exists') else '缺失'}"
            f"\n  留痕总行数（不受过滤影响）：{diagnosis.get('total_runs')}"
        )
        if diagnosis.get("hint"):
            print(f"\n下一步：{diagnosis['hint']}")
    elif diagnosis.get("runs_without_classroom"):
        print(
            f"\n提示：有 {diagnosis['runs_without_classroom']} 条留痕没有 classroom_id，"
            "这些轮次的发言正文未落库，评分表无原文可评。"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
