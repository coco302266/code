"""探针：抽 topic（E）能不能关掉推理模型的"思考"（XB-32）。

`probe_llm_fixed_cost.py` 打出 token 用量后发现：

    completion_tokens=25  completion_tokens_details.reasoning_tokens=23

**输出的 25 个 token 里有 23 个是 reasoning** ⇒ `deepseek-flash` 是推理模型，
哪怕只让它回 "same" 一个词，它也要先想 20~30 个 token。这解释了为什么
`ab_extract_prompt.py` 里砍 prompt 长度完全没用：耗时根本不在 prompt 侧，
而在**输出侧的推理**。

抽 topic 是"输出一两个词"的分类任务，本来就不需要推理。所以真正的杠杆
不是砍 prompt，而是**关掉思考**。各家开关名不统一，这里一次试完几种
常见写法，用 `reasoning_tokens` 是否降到 0 来判定哪个真的生效
（不看报错猜，看实测 token）。

同时记录延迟与判定结果，确认关掉思考后判定不退化。

用法（backend/ 下）：
    ./venv/Scripts/python.exe scripts/probe_thinking_off.py --reps 5
"""
from __future__ import annotations

import argparse
import os
import statistics
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from langchain_core.messages import HumanMessage  # noqa: E402
from langchain_openai import ChatOpenAI  # noqa: E402

from agent.graph import get_model  # noqa: E402
from agent.prompts import EXTRACT_TOPIC_PROMPT  # noqa: E402

# 各家「关思考」的写法；用实测 reasoning_tokens 判定谁生效
CANDIDATES: list[tuple[str, dict]] = [
    ("baseline（不关）", {}),
    ("extra_body.thinking=disabled", {"extra_body": {"thinking": {"type": "disabled"}}}),
    ("extra_body.enable_thinking=False", {"extra_body": {"enable_thinking": False}}),
    (
        "extra_body.chat_template_kwargs",
        {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}},
    ),
]


def build_prompt() -> str:
    conversation = "\n".join(
        [
            "User: 什么是死锁？",
            "Tutor: 死锁是多个进程互相等待对方持有的资源，导致谁都无法继续执行。",
        ]
    )
    return EXTRACT_TOPIC_PROMPT.format(
        current_topic="死锁",
        conversation=conversation,
        latest_message="那怎么预防呢？",
    )


def reason_tokens(resp) -> int | None:
    md = getattr(resp, "response_metadata", None) or {}
    usage = md.get("token_usage") or md.get("usage") or {}
    details = usage.get("completion_tokens_details") or {}
    return details.get("reasoning_tokens")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=5)
    args = ap.parse_args()

    model = get_model("openai")
    prompt = build_prompt()
    print("模型=%s，每档跑 %d 次\n" % (model, args.reps))

    for label, kwargs in CANDIDATES:
        llm = ChatOpenAI(
            model=model,
            temperature=0.4,
            api_key=os.getenv("LLM_API_KEY"),
            base_url=os.getenv("LLM_BASE_URL") or None,
            **kwargs,
        )
        lats: list[float] = []
        rts: list[int] = []
        outs: list[str] = []
        err = ""
        for _ in range(args.reps):
            try:
                t0 = time.perf_counter()
                resp = llm.invoke([HumanMessage(content=prompt)])
                lats.append((time.perf_counter() - t0) * 1000)
                outs.append((resp.content or "").strip()[:20])
                rt = reason_tokens(resp)
                if rt is not None:
                    rts.append(rt)
            except Exception as exc:
                err = "%s: %s" % (type(exc).__name__, str(exc)[:160])
                break
            time.sleep(0.8)

        if err:
            print("  %-34s 失败 -> %s" % (label, err))
            continue
        print(
            "  %-34s mean=%5.0fms  中位=%5.0fms  reasoning_tokens=%s  输出=%s"
            % (
                label,
                statistics.mean(lats),
                statistics.median(lats),
                (round(statistics.mean(rts)) if rts else "-"),
                outs[:3],
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
