"""A/B：把「抽 topic」（E）做快（P0，2026-10-27，XB-32）。

XB-31 的结论：E（~1.08s）是 A（理解评估）与 R（检索）的**共同前驱**，串行挡在
前面。盲缓存不可行（会污染落库 topic、跨轮累积），所以只能让 E 自己变快。

E 的输出只有一两个词，耗时几乎全在 **API 侧的 TTFT + prefill**。于是有三个
可以单独隔离的杠杆，本脚本各做一个变体（都只改一个变量，好归因）：

| 变体 | 相对现行改了什么 | 隔离的杠杆 |
|---|---|---|
| `base` | —— 现行 | 基线 |
| `slim` | 去掉 8 个示例、规则 5 条压到 4 条 | prompt **内容长度** |
| `slim_win3` | slim + 历史窗口 6→3 条、每条截断 300→120 字 | **历史体积**（prefill 大头） |
| `static_first` | 内容一字不动，只把静态段整体挪到动态段之前 | **前缀可缓存性**（命中厂商前缀缓存） |

**延迟不是唯一判据**：prompt 砍狠了可能把「沿用 / 换题」判错、或把知识点名
抽歪。所以每变体都同时报：
  1. 延迟 mean / 中位 / p95；
  2. **二分类准确率**（12 追问 + 15 换题，金标准标签写死在 `diag_topic_cache.py`）；
  3. **与 base 的决策一致率** + 抽出的知识点名归一化相等率。

**读数纪律（沿用 XB-30）**：只用**同一轮内**的横向比较下结论，两个轮次互为
顺序校验；跨轮绝对值不可比（机器负载 / 限流排队）。

用法（backend/ 下）：
    ./venv/Scripts/python.exe scripts/ab_extract_prompt.py
    ./venv/Scripts/python.exe scripts/ab_extract_prompt.py --rounds 3 --pause 0.5
"""
from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(BACKEND_DIR / "scripts"))

from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402

from agent.graph import get_llm  # noqa: E402
from diag_topic_cache import (  # noqa: E402
    FOLLOWUPS,
    HISTORY,
    SWITCHES,
    build_messages,
)

# ---------------------------------------------------------------- prompt 变体
# 静态段 = 每次请求都一样的部分；动态段 = 随对话变化的部分。
# 现行 base 的静态段只有开头一句（<64 token，够不上厂商前缀缓存的最小粒度），
# 而最大的 prefill 开销其实在「近期对话」上（6 条 × 300 字）。

BASE_TEMPLATE = """你是计算机八股（面试知识点）辅导助手。从用户消息中抽取他正在问的知识点名称。

当前知识点：{current_topic}

近期对话（用来判断追问指代的是哪个知识点）：
{conversation}

示例：
    - "讲讲进程和线程的区别" -> "进程与线程的区别"
    - "什么是死锁？" -> "死锁"
    - "TCP 三次握手到底在握什么" -> "TCP 三次握手"
    - "MySQL 索引为什么会失效" -> "MySQL 索引失效"
    - "你好呀" -> "unknown"
    - "我想学操作系统" -> "操作系统"
    - （当前知识点=死锁）"那怎么预防呢？" -> "same"
    - （当前知识点=unknown、近期对话在讲死锁）"那怎么预防呢？" -> "死锁"

规则：
- 只要消息涉及任何计算机 / 编程 / 面试知识点，就返回该知识点名称（用中文，简短）。
- 如果这条消息是对当前知识点的追问、回答、补充或确认，只返回 "same"。
- 但如果「当前知识点」是 unknown，就先从近期对话推断他在问什么，直接返回那个知识点名称。
  追问（"那怎么预防呢""为什么""举个例子"）脱离上下文没有意义，必须结合近期对话判断。
- 只有当最新消息和近期对话都纯属寒暄、完全没有任何技术内容时，才返回 "unknown"。
- 不要返回 "unknown" / "same" / 知识点名之外的任何文字，也不要加标点、引号或 markdown。

用户最新消息：{latest_message}"""

# 去掉示例、规则压缩：内容长度杠杆
SLIM_TEMPLATE = """你是计算机八股（面试知识点）辅导助手，判断用户现在问的是哪个知识点。

当前知识点：{current_topic}

近期对话（用来判断追问指代的是哪个知识点）：
{conversation}

规则：
- 若这条消息是对当前知识点的追问、回答、补充或确认，只返回 same。
- 否则返回该知识点名称（用中文、简短）。当前知识点是 unknown 时，先从近期对话推断他在问什么再返回。
- 只有最新消息和近期对话都纯属寒暄、完全没有任何技术内容时，才返回 unknown。
- 只返回 unknown / same / 知识点名，不要标点、引号、markdown 或任何其他文字。

用户最新消息：{latest_message}"""

# 内容一字不动，只把静态段（角色 + 示例 + 规则）整体挪到动态段之前：
# 静态段每次字节相同 → 可命中厂商的自动前缀缓存。
STATIC_FIRST_TEMPLATE = """你是计算机八股（面试知识点）辅导助手。从用户消息中抽取他正在问的知识点名称。

示例：
    - "讲讲进程和线程的区别" -> "进程与线程的区别"
    - "什么是死锁？" -> "死锁"
    - "TCP 三次握手到底在握什么" -> "TCP 三次握手"
    - "MySQL 索引为什么会失效" -> "MySQL 索引失效"
    - "你好呀" -> "unknown"
    - "我想学操作系统" -> "操作系统"
    - （当前知识点=死锁）"那怎么预防呢？" -> "same"
    - （当前知识点=unknown、近期对话在讲死锁）"那怎么预防呢？" -> "死锁"

规则：
- 只要消息涉及任何计算机 / 编程 / 面试知识点，就返回该知识点名称（用中文，简短）。
- 如果这条消息是对当前知识点的追问、回答、补充或确认，只返回 "same"。
- 但如果「当前知识点」是 unknown，就先从近期对话推断他在问什么，直接返回那个知识点名称。
  追问（"那怎么预防呢""为什么""举个例子"）脱离上下文没有意义，必须结合近期对话判断。
- 只有当最新消息和近期对话都纯属寒暄、完全没有任何技术内容时，才返回 "unknown"。
- 不要返回 "unknown" / "same" / 知识点名之外的任何文字，也不要加标点、引号或 markdown。

当前知识点：{current_topic}

近期对话（用来判断追问指代的是哪个知识点）：
{conversation}

用户最新消息：{latest_message}"""

VARIANTS: dict[str, dict] = {
    "base": {"template": BASE_TEMPLATE, "history_n": 6, "char_cap": 300, "bind": {}},
    "slim": {"template": SLIM_TEMPLATE, "history_n": 6, "char_cap": 300, "bind": {}},
    "slim_win3": {"template": SLIM_TEMPLATE, "history_n": 3, "char_cap": 120, "bind": {}},
    "static_first": {"template": STATIC_FIRST_TEMPLATE, "history_n": 6, "char_cap": 300, "bind": {}},
    # 抽取是「输出一两个词」的分类任务，不需要推理。deepseek-flash 默认会先
    # 想 20~30 个 reasoning token（实测），关掉后延迟 −27%（见 §7.9）。
    "base_nothink": {
        "template": BASE_TEMPLATE,
        "history_n": 6,
        "char_cap": 300,
        "bind": {"extra_body": {"thinking": {"type": "disabled"}}},
    },
}

REUSE = "（沿用）"


def norm(text: str) -> str:
    return (text or "").replace(" ", "").lower()


def classify(raw: str, current_topic: str) -> str:
    """把模型原始返回归一化成判定结果，口径与 agent/graph.py 完全一致。

    返回 REUSE 表示「沿用上一轮 topic」，否则返回抽出的新 topic。
    """
    result = raw.strip()
    if result.lower() == "same":
        return REUSE

    topic = result.lower().strip().strip("。.！!？?；;，,、\"' ")
    if topic in ["unknown", "none", "no topic", "not mentioned"]:
        return REUSE if current_topic != "unknown" else "unknown"
    if topic == current_topic.lower():
        return REUSE
    return topic


async def run_one(cfg: dict, llm, case) -> dict:
    label, prev_topic, _h, latest, want_file = case
    messages = build_messages(HISTORY.get(prev_topic, []), latest)

    recent = messages[-cfg["history_n"]:]
    conversation = "\n".join(
        f"{'User' if isinstance(m, HumanMessage) else 'Tutor'}: {m.content[: cfg['char_cap']]}"
        for m in recent
    ) or "（无）"

    prompt = cfg["template"].format(
        current_topic=prev_topic,
        conversation=conversation,
        latest_message=latest,
    )

    caller = llm
    if cfg.get("bind"):
        caller = llm.bind(**cfg["bind"])

    t0 = time.perf_counter()
    response = await caller.ainvoke([HumanMessage(content=prompt)])
    ms = round((time.perf_counter() - t0) * 1000)

    raw = (response.content or "").strip()
    verdict = classify(raw, prev_topic)
    changed = verdict not in (REUSE, "unknown")

    if label == "followup":
        correct = verdict == REUSE
    else:
        correct = changed

    return {
        "label": label,
        "latest": latest,
        "prev_topic": prev_topic,
        "raw": raw,
        "verdict": verdict,
        "changed": changed,
        "correct": correct,
        "ms": ms,
        "want_file": want_file,
    }


def summarize(name: str, rows: list[dict]) -> dict:
    lats = [r["ms"] for r in rows]
    return {
        "variant": name,
        "n": len(rows),
        "mean": round(statistics.mean(lats)),
        "median": round(statistics.median(lats)),
        "p95": round(sorted(lats)[max(0, int(len(lats) * 0.95) - 1)]),
        "acc": round(sum(1 for r in rows if r["correct"]) / len(rows) * 100, 1),
        "changed": sum(1 for r in rows if r["changed"]),
    }


async def main_async(args) -> int:
    cases = FOLLOWUPS + SWITCHES
    llm = get_llm(args.provider)
    names = (
        [n.strip() for n in args.variants.split(",") if n.strip()]
        if args.variants
        else list(VARIANTS)
    )
    unknown = [n for n in names if n not in VARIANTS]
    if unknown:
        raise SystemExit("未知变体：%s（可选：%s）" % (unknown, list(VARIANTS)))

    all_rows: dict[str, list[dict]] = {n: [] for n in names}
    per_round: list[dict[str, list[dict]]] = []

    for rnd in range(args.rounds):
        order = names if rnd % 2 == 0 else list(reversed(names))
        print("\n" + "=" * 78)
        print("第 %d/%d 轮  顺序：%s" % (rnd + 1, args.rounds, " → ".join(order)))
        print("=" * 78)

        round_rows: dict[str, list[dict]] = {}
        for name in order:
            cfg = VARIANTS[name]
            rows = []
            for case in cases:
                try:
                    rows.append(await run_one(cfg, llm, case))
                except Exception as exc:
                    msg = str(exc)
                    if "429" in msg or "rate" in msg.lower():
                        print("  429 限流，退避 60s")
                        await asyncio.sleep(60)
                        rows.append(await run_one(cfg, llm, case))
                    else:
                        raise
                await asyncio.sleep(args.pause)
            round_rows[name] = rows
            all_rows[name].extend(rows)
            s = summarize(name, rows)
            print(
                "  %-13s mean=%5dms  中位=%5dms  p95=%5dms  准确率=%5.1f%%  判换题=%2d/%d"
                % (s["variant"], s["mean"], s["median"], s["p95"], s["acc"], s["changed"], s["n"])
            )
        per_round.append(round_rows)

    print("\n" + "=" * 78)
    print("汇总（%d 轮 × %d 例）" % (args.rounds, len(cases)))
    print("=" * 78)
    base_s = summarize("base", all_rows["base"])
    print(
        "  %-13s mean=%5dms  中位=%5dms  p95=%5dms  准确率=%5.1f%%（基线）"
        % ("base", base_s["mean"], base_s["median"], base_s["p95"], base_s["acc"])
    )
    for name in names[1:]:
        s = summarize(name, all_rows[name])
        d_mean = s["mean"] - base_s["mean"]
        d_med = s["median"] - base_s["median"]
        print(
            "  %-13s mean=%5dms (%+5d,%+.0f%%)  中位=%5dms (%+5d)  p95=%5dms  "
            "准确率=%5.1f%% (%+.1fpp)"
            % (
                name,
                s["mean"],
                d_mean,
                d_mean / base_s["mean"] * 100,
                s["median"],
                d_med,
                s["p95"],
                s["acc"],
                s["acc"] - base_s["acc"],
            )
        )

    # 与 base 的逐例一致性
    print("\n与 base 的一致性（逐例对齐）：")
    base_by_msg = {(r["latest"], r["prev_topic"]): r for r in all_rows["base"]}
    for name in names[1:]:
        same_decision = 0
        same_topic = 0
        diffs: list[tuple[str, str, str]] = []
        for r in all_rows[name]:
            b = base_by_msg.get((r["latest"], r["prev_topic"]))
            if b is None:
                continue
            if b["changed"] == r["changed"]:
                same_decision += 1
            elif len(diffs) < 6:
                diffs.append((r["latest"], b["raw"], r["raw"]))
            if norm(b["verdict"]) == norm(r["verdict"]):
                same_topic += 1
        n = len(all_rows[name])
        print(
            "  %-13s 决策一致 %2d/%d = %5.1f%%   知识点名等价 %2d/%d = %5.1f%%"
            % (
                name,
                same_decision,
                n,
                same_decision / n * 100,
                same_topic,
                n,
                same_topic / n * 100,
            )
        )
        for msg, b_raw, r_raw in diffs:
            print("      · %-22s base=%-16r 本变体=%r" % (msg, b_raw, r_raw))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="openai")
    ap.add_argument("--rounds", type=int, default=2, help="轮次数（奇偶数自动反序）")
    ap.add_argument("--pause", type=float, default=1.0, help="每次调用间隔秒")
    ap.add_argument("--variants", default="", help="只跑指定变体，逗号分隔")
    args = ap.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
