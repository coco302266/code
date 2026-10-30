#!/usr/bin/env python
"""导出「理解评估」的标注候选集（第 1 步：给 A 装尺子的原材料）。

为什么需要这个脚本
------------------
理解评估节点（A，~2.08s）是前置段里最长的那一极，想给它关思考提速，
必须先能回答"关掉之后判断准不准"。而项目里**没有任何 A 的质量判据**——
现有的评测集只覆盖检索（top1 / nDCG），不覆盖 resolved / hint_level。

所以要先攒一个金标准。金标准必须用**真实历史对话**，不能用凭空造的句子：
造出来的句子分布和线上不一致，量出来的准确率没有参考价值。

本脚本从 messages 表抽出「学生话语 + 前文 + 该轮 topic」三元组，
落到 eval/data/assess_candidates.json，供人工（我）逐条标注后转 gold。

用法
----
    python scripts/export_assess_candidates.py [--limit 30] [--ctx 4]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

OUT_PATH = (
    Path(__file__).resolve().parents[1] / "eval" / "data" / "assess_candidates.json"
)

# 这些是脚本批量跑出来的固定句式，没有真实学生的表达变化，
# 拿来标注会高估 A 的准确率（它们太"标准"了）。剔除。
SCRIPT_PATTERNS = (
    "讲讲",
    "什么是",
    "那怎么",
    "为什么",
    "举个例子",
    "再讲讲",
    "总结一下",
    "详细说说",
)


def _fetch_rows() -> list[dict]:
    import pymysql

    conn = pymysql.connect(
        host=os.getenv("MYSQL_HOST"),
        port=int(os.getenv("MYSQL_PORT") or 3306),
        user=os.getenv("MYSQL_USER"),
        password=os.getenv("MYSQL_PASSWORD"),
        database=os.getenv("MYSQL_DATABASE"),
        charset="utf8mb4",
    )
    try:
        with conn.cursor() as cur:
            # 只取带 topic 的课堂：没有 topic 的轮次 A 会直接短路返回，
            # 标了也测不到东西。
            cur.execute(
                "SELECT classroom_id, id, role, content, topic "
                "FROM messages "
                "WHERE classroom_id IN ("
                "  SELECT DISTINCT classroom_id FROM messages "
                "  WHERE role='assistant' AND topic IS NOT NULL AND topic<>''"
                ") "
                "ORDER BY classroom_id, id"
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def _looks_scripted(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return True
    # 短促的固定指令句（<=12 字且命中模板）判为脚本产物
    if len(t) <= 12 and any(t.startswith(p) for p in SCRIPT_PATTERNS):
        return True
    return False


def build_candidates(rows: list[dict], ctx_n: int) -> list[dict]:
    """把扁平消息流切成 (前文, 学生话语, topic) 三元组。"""
    by_class: dict[int, list[dict]] = {}
    for r in rows:
        by_class.setdefault(r["classroom_id"], []).append(r)

    out: list[dict] = []
    for cid in sorted(by_class):
        seq = by_class[cid]
        for i, msg in enumerate(seq):
            # 注意：messages.role 的取值是 'student' / 'assistant'，不是 'user'。
            if msg["role"] != "student":
                continue
            # 该轮 topic：取这条 user 之后最近的一条 assistant 的 topic
            topic = ""
            for later in seq[i + 1 :]:
                if later["role"] == "assistant" and later["topic"]:
                    topic = later["topic"]
                    break
            if not topic or topic == "unknown":
                continue

            prior = seq[max(0, i - ctx_n) : i]
            history = [
                {"role": m["role"], "content": (m["content"] or "")[:400]}
                for m in prior
            ]
            out.append(
                {
                    "id": "c%s_m%s" % (cid, msg["id"]),
                    "classroom_id": cid,
                    "topic": topic,
                    "history": history,
                    "utterance": (msg["content"] or "").strip(),
                    "scripted": _looks_scripted(msg["content"] or ""),
                }
            )
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=30, help="导出条数上限")
    ap.add_argument("--ctx", type=int, default=4, help="前文条数")
    ap.add_argument(
        "--include-scripted",
        action="store_true",
        help="保留固定句式（默认剔除，它们不反映真实表达）",
    )
    args = ap.parse_args()

    rows = _fetch_rows()
    if not rows:
        print("messages 表无数据（或查不到带 topic 的课堂）")
        return 1

    cands = build_candidates(rows, args.ctx)
    total = len(cands)
    if not args.include_scripted:
        cands = [c for c in cands if not c["scripted"]]
    print("扫描到候选 %d 条，剔除固定句式后 %d 条" % (total, len(cands)))

    cands = cands[: args.limit]
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps(cands, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("已写出 %d 条 → %s" % (len(cands), OUT_PATH))

    print("\n=== 供标注阅读 ===")
    for c in cands:
        print("\n--- %s  topic=%s" % (c["id"], c["topic"]))
        for h in c["history"]:
            tag = "User" if h["role"] == "user" else "Tutor"
            print("  %-5s: %s" % (tag, h["content"][:160]))
        print("  >>学生: %s" % c["utterance"][:200])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
