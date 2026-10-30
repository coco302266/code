"""一次性诊断：省略式追问（「它的作用是什么？」）的 topic 抽取与检索表现。

走真实 /chat（不是 L1 的离线口径），每轮记：评估节点抽出的 state.topic、
实际注入的 sources 数与主题、答案长度与开头 60 字、与期望 topic 的归一化匹配。
期望 topic 写死在 SEQS 里，判定用与 L1 同一把尺（去空格小写子串）。

不进 CI。用法：backend/ 下 ./venv/Scripts/python.exe scripts/diag_followup_topic.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(BACKEND_DIR / "scripts"))

import gen_eval_samples as G  # noqa: E402

# (期望 topic, [主题句, 省略式追问]) —— 追问刻意不带任何主题词
SEQS: list[tuple[str, list[str]]] = [
    ("死锁", ["讲讲死锁？", "它的作用是什么？"]),
    ("虚拟内存与页面置换", ["什么是虚拟内存？", "它有什么优点？"]),
    ("HTTPS 与 TLS 握手", ["HTTPS 和 HTTP 有什么不同？", "性能开销体现在哪？"]),
    ("进程调度算法", ["讲讲进程调度算法？", "举个实际的例子？"]),
    ("进程与线程", ["进程和线程有什么区别？", "切换开销主要在哪些地方？"]),
]

PAUSE_SECS = 7.0


def norm(text: str) -> str:
    """与 L1 top_hit / kb_gap 同一把归一化尺子，不自造第二套。"""
    return (text or "").replace(" ", "").lower()


def main() -> None:
    classroom_id = G.create_classroom("追问topic诊断")
    print("课堂 #%s（同一课堂内连续追问，评估节点才看得到历史）\n" % classroom_id)

    last_start = 0.0
    for expect, questions in SEQS:
        print("=" * 72)
        print("期望 topic：%s" % expect)
        for turn, q in enumerate(questions, 1):
            gap = PAUSE_SECS - (time.perf_counter() - last_start)
            if gap > 0:
                time.sleep(gap)
            last_start = time.perf_counter()

            label = "主题句" if turn == 1 else "追问句"
            s = G.post_chat(classroom_id, q, timeout=120.0)
            if s.get("status") == 429:
                print("  429 限流，退避 65s")
                time.sleep(65)
                s = G.post_chat(classroom_id, q, timeout=120.0)

            topic = str(s.get("topic") or "")
            hit = norm(expect) in norm(topic) or norm(topic) in norm(expect)
            print(
                "  [%s] %s\n"
                "      state.topic = %r   匹配期望 = %s\n"
                "      sources=%s  topics=%s  chars=%s  hint=%s\n"
                "      答案开头：%s"
                % (
                    label,
                    q,
                    topic,
                    "✓" if hit else "✗",
                    s.get("sources"),
                    s.get("source_topics"),
                    s.get("chars"),
                    s.get("hint_level"),
                    (s.get("text") or "").replace("\n", " ")[:60],
                )
            )
        print()


if __name__ == "__main__":
    main()
