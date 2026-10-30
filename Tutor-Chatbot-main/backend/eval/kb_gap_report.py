"""知识库缺口聚合报表（XB-14，2026-10-24 用户决策的「后期聚合」落点）。

读 `logs/kb_gap_events.jsonl`（/chat 检索为空时写入的事件），按知识点
（归一化 topic）聚合：缺口次数、样例提问、首末日期，输出
控制台摘要 + JSON 报告（`eval/reports/kb_gap_{ts}.json`）。

用法（在 backend/ 下）：
    python -m eval.kb_gap_report                 # 聚合默认事件文件
    python -m eval.kb_gap_report --events PATH   # 指定事件文件
    python -m eval.kb_gap_report --out PATH      # 指定报告输出路径
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.rag.kb_gap import (  # noqa: E402
    aggregate_events,
    kb_gap_log_path,
    load_events,
)

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPORT_DIR = Path(__file__).resolve().parent / "reports"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="知识库缺口聚合报表（XB-14）")
    parser.add_argument("--events", default="", help="事件文件路径（缺省用 RAG_KB_GAP_LOG 或默认路径）")
    parser.add_argument("--out", default="", help="报告输出路径（缺省 eval/reports/kb_gap_{ts}.json）")
    args = parser.parse_args(argv)

    events_path = Path(args.events) if args.events else kb_gap_log_path()
    events = load_events(events_path)
    if not events:
        print("事件文件为空或不存在：%s" % events_path)
        print("说明：/chat 检索到知识库片段为空时会自动写入（见 app.py XB-14 注释）。")
        return 0

    report = {
        "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "events_file": str(events_path),
        **aggregate_events(events),
    }

    print("知识库缺口事件 %d 条，涉及 %d 个知识点：" % (report["total_events"], report["distinct_topics"]))
    for item in report["topics"]:
        print(
            "  %-24s %3d 次（%s ~ %s）样例：%s"
            % (
                item["topic"][:24],
                item["count"],
                item["first_date"] or "?",
                item["last_date"] or "?",
                " / ".join(q[:20] for q in item["sample_queries"]) or "（无）",
            )
        )

    out_path = Path(args.out) if args.out else REPORT_DIR / (
        "kb_gap_%s.json" % datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("报告已写入：%s" % out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
