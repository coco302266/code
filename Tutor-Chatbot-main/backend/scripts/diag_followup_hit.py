"""一次性诊断：省略式追问在「库内确实有内容」的主题上能否正确工作。

与 diag_followup_topic.py 的区别：那条脚本只量 state.topic 抽得对不对，
本脚本量**功能是否真的成立**——追问的知识点在库里已有独立章节时，
能不能把那一段原文检索出来并注入。判定三件事：

1. 真实 /chat 的 sources > 0（编排层确实注入了资料）；
2. 注入切片来自期望文档（不是别的主题的邻居切片）；
3. 注入切片正文里真的有目标知识点（证明不是"注入了但注入的是错的"）。

第 2/3 项用生产同一入口 retrieve_knowledge_docs 复算（含 relevance_floor），
口径与线上完全一致，不是近似。

不进 CI。用法：backend/ 下 ./venv/Scripts/python.exe scripts/diag_followup_hit.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(BACKEND_DIR / "scripts"))

import gen_eval_samples as G  # noqa: E402
from agent.rag.indexer import load_knowledge_index  # noqa: E402
from agent.rag.retriever import retrieve_knowledge_docs  # noqa: E402

# (主题句, [(省略式追问, 期望来源文件名, 期望出现在注入正文里的关键词(任一命中即可))])
# 期望关键词都取自知识库原文，是"这段知识确实被注入"的证据。
SEQS: list[tuple[str, list[tuple[str, str, list[str]]]]] = [
    (
        "TCP 三次握手的过程是怎样的？",
        [
            ("为什么不是两次？", "tcp-handshake.md", ["两次", "第三次"]),
            ("TIME_WAIT 为什么要等那么久？", "tcp-handshake.md", ["2MSL", "MSL"]),
            ("挥手为什么要四次？", "tcp-handshake.md", ["四次挥手", "半关闭"]),
        ],
    ),
    (
        "什么是死锁？",
        [
            ("那怎么预防呢？", "deadlock.md", ["有序分配", "破坏"]),
            ("避免和检测有什么区别？", "deadlock.md", ["银行家", "安全状态"]),
        ],
    ),
]

PAUSE_SECS = 7.0


def norm(text: str) -> str:
    return (text or "").replace(" ", "").lower()


def main() -> None:
    vs = load_knowledge_index("cs_basics", str(BACKEND_DIR / "chroma_kb"))
    if vs is None:
        raise SystemExit("知识库加载失败（chroma_kb 不存在？先跑 build_kb.py）")

    classroom_id = G.create_classroom("追问命中诊断")
    print("课堂 #%s（同一课堂内连续追问）\n" % classroom_id)

    last_start = 0.0
    total = 0
    passed = 0

    for anchor, followups in SEQS:
        print("=" * 78)
        print("主题句：%s" % anchor)
        gap = PAUSE_SECS - (time.perf_counter() - last_start)
        if gap > 0:
            time.sleep(gap)
        last_start = time.perf_counter()
        s = G.post_chat(classroom_id, anchor, timeout=120.0)
        print("  主题句 -> state.topic=%r sources=%s" % (s.get("topic"), s.get("sources")))

        for question, want_file, want_keys in followups:
            gap = PAUSE_SECS - (time.perf_counter() - last_start)
            if gap > 0:
                time.sleep(gap)
            last_start = time.perf_counter()

            s = G.post_chat(classroom_id, question, timeout=120.0)
            if s.get("status") == 429:
                print("  429 限流，退避 65s")
                time.sleep(65)
                s = G.post_chat(classroom_id, question, timeout=120.0)

            topic = str(s.get("topic") or "")
            sources = s.get("sources") or 0

            # 用生产同一入口复算：得到这一轮真正注入的切片与正文
            trace: dict = {}
            docs = retrieve_knowledge_docs(vs, question, topic=topic, trace=trace)
            files = [os.path.basename(str(d.metadata.get("source", "?"))) for d in docs]
            body = "\n".join(d.page_content or "" for d in docs)
            hit_file = want_file in files
            hit_key = next((k for k in want_keys if norm(k) in norm(body)), None)

            ok = bool(sources and hit_file and hit_key)
            total += 1
            passed += 1 if ok else 0
            print(
                "  追问 %-22s topic=%-14r sources=%s\n"
                "      检索语句=%r\n"
                "      注入文件=%s  期望 %s -> %s\n"
                "      知识点关键词 %s -> %s\n"
                "      ==> %s"
                % (
                    question,
                    topic,
                    sources,
                    trace.get("query"),
                    files,
                    want_file,
                    "命中" if hit_file else "未命中",
                    want_keys,
                    ("命中 %r" % hit_key) if hit_key else "未命中",
                    "PASS" if ok else "FAIL",
                )
            )
        print()

    print("=" * 78)
    print("合计 %d/%d PASS" % (passed, total))


if __name__ == "__main__":
    main()
