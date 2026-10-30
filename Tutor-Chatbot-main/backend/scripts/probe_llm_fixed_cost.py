"""把 E（抽 topic）的 ~1.0s 拆成「固定开销」与「边际 prefill 成本」（XB-32）。

A/B（`ab_extract_prompt.py`）的结果与假设相反：三个精简变体（去示例 / 缩历史 /
调前缀顺序）**全都比现行更慢 17%~40%**，且正反序两轮里每个变体的耗时都高度复现。
⇒ "prefill 长度是瓶颈" 这个假设大概率不成立。

于是不能直接下结论，得把耗时**分解**开：
  - `min`   ：极短 prompt + 1 token 输出  → 逼近 API 侧固定开销（RTT + 排队）
  - `base`  ：现行抽取 prompt            → 现状
  - `long`  ：现行 prompt + 约 3000 字填充 → 同base多出来的部分≈prefill 边际成本

若 min ≈ base ≪ long，结论就是：**E 的 1.0s 几乎全是固定开销，砍 prompt 省不出
时间来**，唯一出路是减少调用次数（投机执行 / 与评估合并）或换更快的服务/地域。
若 min ≪ base，说明还是长度主导，A/B 的反常需要另找解释。

同时把 `response_metadata` 里的 token 用量打出来：若 completion_tokens 很大，
说明模型在"想"（reasoning），那瓶颈就是输出侧——此时**限制思考**才是杠杆，
砍 prompt 完全没用。

用法（backend/ 下）：
    ./venv/Scripts/python.exe scripts/probe_llm_fixed_cost.py --reps 8
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from langchain_core.messages import HumanMessage  # noqa: E402

from agent.graph import get_llm  # noqa: E402
from agent.prompts import EXTRACT_TOPIC_PROMPT  # noqa: E402

MIN_PROMPT = "回一个字：好"

PADDING = (
    "以下内容只是填充，用来拉长 prompt，与任务无关，请忽略："
    + "计算机基础知识包括数据结构、操作系统、计算机网络与数据库四个部分。" * 100
)


def build_base_prompt() -> str:
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="openai")
    ap.add_argument("--reps", type=int, default=8)
    args = ap.parse_args()

    llm = get_llm(args.provider)
    probes = [
        ("min", MIN_PROMPT),
        ("base", build_base_prompt()),
        ("long", build_base_prompt() + "\n\n" + PADDING),
    ]

    print("每档跑 %d 次（min=极短 prompt，base=现行抽取 prompt，long=base+约3000字填充）\n" % args.reps)
    stats = {}
    for name, prompt in probes:
        lats = []
        meta = None
        for _ in range(args.reps):
            t0 = time.perf_counter()
            resp = llm.invoke([HumanMessage(content=prompt)])
            lats.append(round((time.perf_counter() - t0) * 1000))
            meta = getattr(resp, "response_metadata", None) or meta
            time.sleep(0.8)
        stats[name] = (statistics.mean(lats), statistics.median(lats), min(lats), max(lats))
        print(
            "  %-5s mean=%6.0fms  中位=%6.0fms  min=%5dms  max=%6dms  prompt约%d字"
            % (name, stats[name][0], stats[name][1], stats[name][2], stats[name][3], len(prompt))
        )
        if meta:
            usage = meta.get("token_usage") or meta.get("usage") or {}
            print(
                "         tokens: prompt=%s completion=%s total=%s  model=%s"
                % (
                    usage.get("prompt_tokens"),
                    usage.get("completion_tokens"),
                    usage.get("total_tokens"),
                    meta.get("model_name") or meta.get("model"),
                )
            )
            for key in ("reasoning_content", "reasoning"):
                if key in meta:
                    print("         %s: %r" % (key, str(meta[key])[:120]))

    extra_chars = len(PADDING)
    d_long = stats["long"][0] - stats["base"][0]
    print(
        "\n分解：固定开销≈%dms（min 档），base 相对 min 多 %dms，"
        "再加约 %d 字填充多 %dms\n"
        "      ⇒ 边际 prefill 约 %.3f ms/字（约 %.1f ms/千字），"
        "把现行 prompt 砍掉一半也只省约 %dms"
        % (
            stats["min"][0],
            stats["base"][0] - stats["min"][0],
            extra_chars,
            d_long,
            d_long / extra_chars if extra_chars else 0,
            d_long / extra_chars * 1000 if extra_chars else 0,
            (len(build_base_prompt()) / 2) * (d_long / extra_chars) if extra_chars else 0,
        )
    )

    # 端到端验证：把 min 档的开销当成「一次 API 往返」的下界，
    # 用它估 E 里有多少是能靠改 prompt 省下来的。
    reducible = stats["base"][0] - stats["min"][0]
    print(
        "E 的 %.0fms 里，理论最多可省 %dms（%.0f%%），其余 %dms 是固定往返开销。"
        % (stats["base"][0], reducible, reducible / stats["base"][0] * 100, stats["base"][0] - reducible)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
