#!/usr/bin/env python
"""理解评估节点（A）的质量尺子（2026-10-28，第 1 步）。

为什么需要它
------------
A 是前置段里最长的那一极（~2.08s），想给它关思考提速，必须先能回答
"关掉之后判断准不准"。项目里此前没有任何 A 的质量判据：现有评测集只
覆盖检索（top1 / nDCG），不覆盖 resolved / hint_level。

这就是那把尺子：拿 30 条人工标注的样本，让 A 在两档配置下各判一遍，
跟金标准比，同时记耗时。以后任何动到 A 的改动（关思考 / 改 prompt /
换模型）都先过这一关——**只允许在准确率不降的前提下拿延迟收益**。

怎么做到不改生产代码就能切档
----------------------------
`assess_understanding_node` 直接调 `state["llm"].ainvoke(...)`，所以只要在
外面把 state 里的 llm 换成 `llm.bind(extra_body={"thinking": {"type": "disabled"}})`
的句柄即可，生产代码一行不动。

指标口径
--------
- resolved_acc  ：二分类准确率（主指标，错了就会误导讲解走向）
- mis_acc      ："是否给出具体误解"的检出一致率（金标准说有 / 没有）
- hint_up_acc  ："该不该加深讲解"的方向一致率
                 （代码允许下调 1 级当作进步信号，所以只看"升不升"，
                   不苛求具体数字——prompt 规则本身就带模糊性）
- strict_acc   ：三条同时对（最严，作为回归门禁建议用它）
- 耗时         ：mean / p50 / p95

用法
----
    # 两档对照，各跑 2 轮（自动正反序消顺序偏差）
    python eval/assess_quality.py --modes think,nothink --rounds 2

    # 只跑一档看基线
    python eval/assess_quality.py --modes think --rounds 1
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402

from agent.graph import assess_understanding_node, get_llm  # noqa: E402

GOLD_PATH = BACKEND_DIR / "eval" / "data" / "assess_gold.json"
REPORT_DIR = BACKEND_DIR / "eval" / "reports"

MODES = ("think", "nothink")


def load_gold(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("items", [])
    if not items:
        raise SystemExit("标注集为空：%s" % path)
    return items


def build_state(item: dict, llm) -> dict:
    messages = []
    for h in item.get("history", []):
        text = (h.get("content") or "").strip()
        if not text:
            continue
        if h.get("role") == "assistant":
            messages.append(AIMessage(content=text))
        else:
            messages.append(HumanMessage(content=text))
    messages.append(HumanMessage(content=item["utterance"]))

    return {
        "messages": messages,
        "topic": item["topic"],
        "hint_level": item["hint_level_in"],
        "misconception": "",
        "resolved": False,
        "llm": llm,
        "topic_changed": False,
    }


def score_one(item: dict, patch: dict) -> dict:
    gold = item["gold"]
    hint_in = item["hint_level_in"]
    pred_hint = patch.get("hint_level", hint_in)

    pred_resolved = bool(patch.get("resolved", False))
    pred_mis = bool((patch.get("misconception") or "").strip())
    pred_up = pred_hint > hint_in

    ok_resolved = pred_resolved == gold["resolved"]
    ok_mis = pred_mis == gold["misconception"]
    ok_up = pred_up == gold["hint_up"]

    return {
        "ok_resolved": ok_resolved,
        "ok_mis": ok_mis,
        "ok_up": ok_up,
        "ok_strict": ok_resolved and ok_mis and ok_up,
        "pred": {
            "resolved": pred_resolved,
            "misconception": patch.get("misconception", ""),
            "hint_level": pred_hint,
        },
    }


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = q * (len(ordered) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


async def run_mode(
    mode: str, items: list[dict], rounds: int, pause: float
) -> dict:
    base_llm = get_llm("openai")
    llm = (
        base_llm.bind(extra_body={"thinking": {"type": "disabled"}})
        if mode == "nothink"
        else base_llm
    )

    latencies: list[float] = []
    per_item: dict[str, list[dict]] = {it["id"]: [] for it in items}

    for r in range(rounds):
        seq = items if r % 2 == 0 else list(reversed(items))
        for item in seq:
            state = build_state(item, llm)
            t0 = time.perf_counter()
            patch = await assess_understanding_node(state)
            latencies.append((time.perf_counter() - t0) * 1000)
            per_item[item["id"]].append(score_one(item, patch or {}))
            if pause:
                await asyncio.sleep(pause)

    # 每条取多数票/首轮都可，这里用「最后一次」会让噪声进来，
    # 统一取「各轮都对才算对」的严格口径更能暴露抖动。
    def agg(key: str) -> float:
        hits = sum(
            1
            for it in items
            if per_item[it["id"]] and all(s[key] for s in per_item[it["id"]])
        )
        return hits / len(items) * 100

    return {
        "mode": mode,
        "n": len(latencies),
        "resolved_acc": agg("ok_resolved"),
        "mis_acc": agg("ok_mis"),
        "hint_up_acc": agg("ok_up"),
        "strict_acc": agg("ok_strict"),
        "lat_mean": statistics.mean(latencies),
        "lat_p50": _pct(latencies, 0.5),
        "lat_p95": _pct(latencies, 0.95),
        "per_item": per_item,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=list(MODES), help="只跑一个档位（每档一个进程）")
    ap.add_argument("--rounds", type=int, default=2, help="轮次，奇偶数自动反序")
    ap.add_argument("--pause", type=float, default=0.8, help="每次调用间隔秒")
    ap.add_argument("--gold", default=str(GOLD_PATH))
    ap.add_argument("--out", help="结果 JSON 输出路径（含逐条明细）")
    ap.add_argument("--compare", nargs=2, metavar=("A", "B"), help="对照两份报告")
    args = ap.parse_args()

    if args.compare:
        return _compare([Path(p) for p in args.compare])
    if not args.mode:
        raise SystemExit(
            "请指定 --mode（每档一个独立进程；两个档位放同一进程会复用 "
            "HTTP 连接池而报 Event loop is closed）"
        )

    items = load_gold(Path(args.gold))
    print("标注集：%s（%d 条）" % (Path(args.gold).name, len(items)))
    from collections import Counter

    for cat, n in Counter(i["category"] for i in items).most_common():
        print("  %-14s %2d 条" % (cat, n))
    print()

    res = asyncio.run(run_mode(args.mode, items, args.rounds, args.pause))
    print("=== 档位 %s ===" % res["mode"])
    print(
        "  resolved %.1f%%｜误解检出 %.1f%%｜hint方向 %.1f%%｜strict %.1f%%"
        % (
            res["resolved_acc"],
            res["mis_acc"],
            res["hint_up_acc"],
            res["strict_acc"],
        )
    )
    print(
        "  耗时 mean %.0fms / p50 %.0fms / p95 %.0fms（n=%d）"
        % (res["lat_mean"], res["lat_p50"], res["lat_p95"], res["n"])
    )

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        # 带上标注原文，--compare 才能打印逐条差异（否则只有 id 看不出问题）
        res["_items"] = items
        out.write_text(
            json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("\n报告：%s" % out)
    return 0


def _summary_line(r: dict) -> str:
    return "%-9s %8.1f%% %8.1f%% %8.1f%% %8.1f%% %8.0fms %8.0fms" % (
        r["mode"],
        r["resolved_acc"],
        r["mis_acc"],
        r["hint_up_acc"],
        r["strict_acc"],
        r["lat_mean"],
        r["lat_p95"],
    )


def _compare(paths: list[Path]) -> int:
    results = []
    for p in paths:
        if not p.exists():
            raise SystemExit("找不到报告：%s" % p)
        results.append(json.loads(p.read_text(encoding="utf-8")))

    print("=== 对照 ===")
    print(
        "%-9s %9s %9s %9s %9s %10s %10s"
        % ("档位", "resolved", "误解检出", "hint方向", "strict", "mean", "p95")
    )
    for r in results:
        print(_summary_line(r))

    if len(results) == 2:
        a, b = results
        d_acc = b["strict_acc"] - a["strict_acc"]
        d_lat = b["lat_mean"] - a["lat_mean"]
        print()
        print(
            "%s 相对 %s：strict %+.1fpp，mean %+.0fms（%.0f%%）"
            % (b["mode"], a["mode"], d_acc, d_lat, d_lat / a["lat_mean"] * 100)
        )
        if d_acc < 0:
            print("⚠ 准确率下降 —— 按纪律不可转默认")
        elif d_acc == 0 and d_lat < 0:
            print("✓ 准确率持平、延迟下降 —— 可以转默认")
        elif d_acc > 0 and d_lat < 0:
            print("✓ 准确率与延迟双改善 —— 转默认")

        # 逐条差异：只看「一档对、另一档错」的样本，这是判读的关键
        diffs = _diff_items(results)
        if diffs:
            print("\n=== 逐条差异（strict 口径）===")
            for d in diffs:
                print(
                    "  %-5s %-12s %s｜gold=%s｜%s"
                    % (
                        d["id"],
                        d["category"],
                        d["topic"],
                        _fmt_gold(d["gold"]),
                        d["verdict"],
                    )
                )
                print("        %s" % d["utterance"][:70])
                for m, pred in d["preds"].items():
                    print(
                        "        %-8s resolved=%-5s hint=%d mis=%r"
                        % (
                            m,
                            pred["resolved"],
                            pred["hint_level"],
                            (pred["misconception"] or "")[:40],
                        )
                    )
    return 0


def _fmt_gold(g: dict) -> str:
    return "resolved=%s mis=%s up=%s" % (
        g["resolved"],
        g["misconception"],
        g["hint_up"],
    )


def _diff_items(results: list[dict]) -> list[dict]:
    """挑出两档结果不一致的样本（各轮全对才算对）。"""
    if len(results) != 2:
        return []
    a, b = results
    a_items = a.get("per_item") or {}
    b_items = b.get("per_item") or {}
    if not a_items or not b_items:
        return []

    # 需要从 gold 里回读原文，per_item 里只有评分，故由调用方补 _items
    meta = a.get("_items") or b.get("_items") or []
    meta_by_id = {m["id"]: m for m in meta}

    out = []
    for iid in a_items:
        if iid not in b_items:
            continue
        a_ok = all(s["ok_strict"] for s in a_items[iid]) if a_items[iid] else False
        b_ok = all(s["ok_strict"] for s in b_items[iid]) if b_items[iid] else False
        if a_ok == b_ok:
            continue
        m = meta_by_id.get(iid, {})
        last_a = a_items[iid][-1]["pred"] if a_items[iid] else {}
        last_b = b_items[iid][-1]["pred"] if b_items[iid] else {}
        verdict = (
            "%s 对、%s 错" % (b["mode"], a["mode"])
            if b_ok
            else "%s 对、%s 错" % (a["mode"], b["mode"])
        )
        out.append(
            {
                "id": iid,
                "category": m.get("category", "?"),
                "topic": m.get("topic", "?"),
                "utterance": m.get("utterance", ""),
                "gold": m.get("gold", {}),
                "verdict": verdict,
                "preds": {a["mode"]: last_a, b["mode"]: last_b},
            }
        )
    return out


if __name__ == "__main__":
    raise SystemExit(main())
