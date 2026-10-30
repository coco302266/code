"""评估「追问场景缓存 topic」是否可行（2026-10-27，XB-31）。

背景：/chat 的关键路径现在是 `抽 topic（E，~1.24s）→ gather(理解评估 A ~2.08s,
三路检索 R ~1.0s)`。E 排在 A 和 R 前面，两者都等它的产出，所以 E 是纯串行段。
于是有了一个诱人的想法：**追问时 topic 没变，跳过 E 直接沿用上一轮**。

本脚本不预设结论，只测三件事：

1. **收益上限**：追问句上 E 判「沿用」的比例有多高 —— 这就是缓存的命中率天花板。
2. **代价（状态污染）**：换题句上 E 判「换新 topic」的比例 —— 这些轮次一旦盲缓存就会被
   带偏，而且 **topic 会落库并被下一轮 `load_last_state` 读回去**，错误跨轮累积。
3. **误伤的实际伤害**：在换题句上，用「上一轮 topic」去检索，期望文档还命中吗？
   如果只是被稀释（原话自带主题词），风险可控；如果完全跑到别的文档，风险不可接受。

另外顺带测一条更便宜的路子（**O4**）：能不能不用 LLM，只用本地 embedding 的
`cos(embed(消息), embed(上一轮 topic))` 就把「沿用 / 换题」分开。给出两组分布与
最佳阈值下的准确率，判断这条路值不值得做。

不进 CI。用法（backend/ 下）：
    ./venv/Scripts/python.exe scripts/diag_topic_cache.py
    ./venv/Scripts/python.exe scripts/diag_topic_cache.py --no-llm     # 只跑本地向量分离度
    ./venv/Scripts/python.exe scripts/diag_topic_cache.py --no-retrieve  # 不跑检索复算
"""
from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402

from agent.graph import extract_topic_node, get_llm  # noqa: E402

# ---------------------------------------------------------------- 用例集
#
# (label, prev_topic, [历史消息], 最新消息, 期望命中的知识库文件)
#   label="followup" —— 这条消息是上一轮知识点的追问（省略式，自带主题词极少）
#   label="switch"   —— 这条消息换了知识点（含一组「句式像追问、实则换题」的陷阱）
Case = tuple[str, str, list[tuple[str, str]], str, str]

FOLLOWUPS: list[Case] = [
    ("followup", "死锁", [], "那怎么预防呢？", "deadlock.md"),
    ("followup", "死锁", [], "为什么会发生？", "deadlock.md"),
    ("followup", "死锁", [], "举个实际例子？", "deadlock.md"),
    ("followup", "死锁", [], "避免和检测有什么区别？", "deadlock.md"),
    ("followup", "TCP 三次握手", [], "为什么不是两次？", "tcp-handshake.md"),
    ("followup", "TCP 三次握手", [], "那挥手为什么要四次？", "tcp-handshake.md"),
    ("followup", "B+ 树索引", [], "什么时候会失效？", "index-transaction.md"),
    ("followup", "虚拟内存与页面置换", [], "它有什么好处？", "memory-management.md"),
    ("followup", "进程与线程", [], "切换开销主要在哪？", "process-thread.md"),
    ("followup", "HTTPS 与 TLS 握手", [], "性能开销体现在哪？", "http-https.md"),
    ("followup", "进程调度算法", [], "举个实际例子？", "scheduling.md"),
    ("followup", "哈希表", [], "冲突怎么解决？", "hash-table.md"),
]

SWITCHES: list[Case] = [
    ("switch", "死锁", [], "讲讲虚拟内存？", "memory-management.md"),
    ("switch", "死锁", [], "TCP 三次握手的过程是怎样的？", "tcp-handshake.md"),
    ("switch", "死锁", [], "进程间通信有哪些方式？", "ipc.md"),
    ("switch", "TCP 三次握手", [], "那死锁呢？", "deadlock.md"),          # 陷阱：句式是追问
    ("switch", "TCP 三次握手", [], "B+ 树索引为什么快？", "index-transaction.md"),
    ("switch", "TCP 三次握手", [], "TIME_WAIT 是什么？", "tcp-handshake.md"),  # 同文档换子节
    ("switch", "进程与线程", [], "HTTPS 和 HTTP 有什么区别？", "http-https.md"),
    ("switch", "进程与线程", [], "讲讲堆排序和堆的结构", "tree-heap.md"),
    ("switch", "进程与线程", [], "协程是什么？", "process-thread.md"),    # 同文档换子节
    ("switch", "虚拟内存与页面置换", [], "讲讲文件系统？", "filesystem.md"),
    ("switch", "哈希表", [], "死锁的四个必要条件是什么？", "deadlock.md"),
    ("switch", "快速排序", [], "讲讲 DNS 解析过程", "dns.md"),
    ("switch", "快速排序", [], "什么是回表？", "index-transaction.md"),
    ("switch", "进程调度算法", [], "什么是中断和系统调用？", "interrupt-syscall.md"),
    ("switch", "HTTPS 与 TLS 握手", [], "MySQL 索引为什么会失效？", "index-transaction.md"),
]

# 历史消息：让「追问」看起来确实接续上文（E 会读最近 6 条）
HISTORY: dict[str, list[tuple[str, str]]] = {
    "死锁": [
        ("user", "什么是死锁？"),
        ("assistant", "死锁是多个进程互相等待对方持有的资源，导致谁都无法继续执行。"),
    ],
    "TCP 三次握手": [
        ("user", "TCP 三次握手的过程是怎样的？"),
        ("assistant", "三次握手是 SYN、SYN+ACK、ACK 三个包，用来同步双方的初始序列号。"),
    ],
    "B+ 树索引": [
        ("user", "B+ 树索引为什么快？"),
        ("assistant", "B+ 树是多路平衡树，树高低，一个页能放很多键，磁盘 IO 次数少。"),
    ],
    "虚拟内存与页面置换": [
        ("user", "什么是虚拟内存？"),
        ("assistant", "虚拟内存让每个进程看到连续的地址空间，由 MMU 映射到物理页框。"),
    ],
    "进程与线程": [
        ("user", "进程和线程有什么区别？"),
        ("assistant", "进程是资源分配的基本单位，线程是 CPU 调度的基本单位。"),
    ],
    "HTTPS 与 TLS 握手": [
        ("user", "HTTPS 和 HTTP 有什么不同？"),
        ("assistant", "HTTPS 在 TCP 与 HTTP 之间加了 TLS 层，提供加密、完整性与身份认证。"),
    ],
    "进程调度算法": [
        ("user", "讲讲进程调度算法？"),
        ("assistant", "常见的有先来先服务、时间片轮转、优先级调度与多级反馈队列。"),
    ],
    "哈希表": [
        ("user", "哈希表是怎么工作的？"),
        ("assistant", "哈希表用哈希函数把键映射到桶，理想情况下查找是 O(1)。"),
    ],
    "快速排序": [
        ("user", "讲讲快速排序？"),
        ("assistant", "快选取一个基准，把数组分成两半再递归排序，平均复杂度 O(n log n)。"),
    ],
}

PAUSE_SECS = 2.5


def norm(text: str) -> str:
    return (text or "").replace(" ", "").lower()


def build_messages(history: list[tuple[str, str]], latest: str) -> list:
    msgs = []
    for role, content in history:
        msgs.append(
            HumanMessage(content=content)
            if role == "user"
            else AIMessage(content=content)
        )
    msgs.append(HumanMessage(content=latest))
    return msgs


# ---------------------------------------------------------------- 第 1 部分：本地向量分离度
def part1_separation(cases: list[Case]) -> None:
    """不用 LLM，只看 cos(embed(消息), embed(上一轮 topic)) 能不能分开两类。

    若分得开，说明「沿用判定」可以换成几十毫秒的本地向量比对（O4）；
    若重叠严重，这条路就不用做了，只能走投机执行。
    """
    print("=" * 78)
    print("第 1 部分：本地 embedding 判定「沿用 / 换题」的分离度（无 LLM 调用）")
    print("=" * 78)

    try:
        from agent.rag.indexer import get_embeddings
    except Exception as exc:
        print("  跳过：embedding 加载失败 %s: %s" % (type(exc).__name__, exc))
        return

    started = time.perf_counter()
    emb = get_embeddings()
    print("  embedding 加载 %.0fms\n" % ((time.perf_counter() - started) * 1000))

    rows = []
    t0 = time.perf_counter()
    for label, prev_topic, _hist, latest, want_file in cases:
        v_msg = emb.embed_query(latest)
        v_topic = emb.embed_query(prev_topic)
        sim = sum(a * b for a, b in zip(v_msg, v_topic)) / (
            (sum(a * a for a in v_msg) ** 0.5)
            * (sum(b * b for b in v_topic) ** 0.5)
            + 1e-12
        )
        rows.append((label, prev_topic, latest, sim, want_file))
    per_msg_ms = (time.perf_counter() - t0) * 1000 / (len(cases) * 2)

    fu = sorted(r[3] for r in rows if r[0] == "followup")
    sw = sorted(r[3] for r in rows if r[0] == "switch")

    def stat(name: str, xs: list[float]) -> None:
        print(
            "  %-10s n=%2d  min=%.3f  p25=%.3f  中位=%.3f  p75=%.3f  max=%.3f  mean=%.3f"
            % (
                name,
                len(xs),
                min(xs),
                xs[len(xs) // 4],
                statistics.median(xs),
                xs[(3 * len(xs)) // 4],
                max(xs),
                statistics.mean(xs),
            )
        )

    stat("追问句", fu)
    stat("换题句", sw)
    print("\n  单条 embed 耗时 ~%.0fms（两条共 ~%.0fms，对比 E 的 ~1240ms）" % (per_msg_ms, per_msg_ms * 2))

    # 扫阈值找最佳准确率：判 sim >= t 为「沿用」
    best = None
    for i in range(0, 1001):
        t = i / 1000.0
        tp = sum(1 for x in fu if x >= t)
        fn = len(fu) - tp
        fp = sum(1 for x in sw if x >= t)
        tn = len(sw) - fp
        acc = (tp + tn) / len(rows)
        if best is None or acc > best[1]:
            best = (t, acc, tp, fn, fp, tn)
    t, acc, tp, fn, fp, tn = best
    print(
        "\n  最佳阈值 t=%.3f：准确率 %.1f%%（%d/%d）\n"
        "    追问判沿用 ✓%d  ✗%d   换题判沿用（误伤）%d  ✓%d"
        % (t, acc * 100, tp + tn, len(rows), tp, fn, fp, tn)
    )
    print("  ⇒ 误伤 %d 条 = 换题句的 %.0f%%，每一条都会把 topic 带偏并落库。" % (fp, fp / len(sw) * 100))
    print()


# ---------------------------------------------------------------- 第 2 部分：LLM 判定的天花板
async def part2_llm(cases: list[Case], provider: str) -> list[dict]:
    """跑真实的 extract_topic_node，看它在两类用例上分别判什么。"""
    print("=" * 78)
    print("第 2 部分：真实 extract_topic_node 的判定（缓存命中率天花板）")
    print("=" * 78)

    llm = get_llm(provider)
    results = []
    last_start = 0.0
    for label, prev_topic, _h, latest, want_file in cases:
        gap = PAUSE_SECS - (time.perf_counter() - last_start)
        if gap > 0:
            await asyncio.sleep(gap)
        last_start = time.perf_counter()

        state = {
            "messages": build_messages(HISTORY.get(prev_topic, []), latest),
            "topic": prev_topic,
            "hint_level": 1,
            "misconception": "",
            "resolved": False,
            "llm": llm,
            "topic_changed": False,
        }
        t0 = time.perf_counter()
        patch = dict(await extract_topic_node(dict(state)))
        ms = round((time.perf_counter() - t0) * 1000)

        reused = not patch or patch.get("topic") == prev_topic
        new_topic = patch.get("topic", "（沿用 %s）" % prev_topic)
        results.append(
            {
                "label": label,
                "prev_topic": prev_topic,
                "latest": latest,
                "ms": ms,
                "reused": reused,
                "new_topic": new_topic,
                "want_file": want_file,
                "raw": patch,
            }
        )
        print(
            "  [%-8s] 上一轮=%-14s 消息=%-22s\n"
            "           判定=%-8s 结果=%-24s %dms"
            % (
                label,
                prev_topic,
                latest,
                "沿用" if reused else "换题",
                new_topic,
                ms,
            )
        )

    fu = [r for r in results if r["label"] == "followup"]
    sw = [r for r in results if r["label"] == "switch"]
    hit = sum(1 for r in fu if r["reused"])
    miss = sum(1 for r in sw if r["reused"])
    print(
        "\n  追问集：E 判沿用 %d/%d = %.0f%%（= 缓存命中率上限）\n"
        "  换题集：E 判换题 %d/%d = %.0f%%（= 盲缓存必然误伤的比例）\n"
        "  抽 topic 单次 mean %.0fms / 中位 %.0fms"
        % (
            hit,
            len(fu),
            hit / len(fu) * 100,
            len(sw) - miss,
            len(sw),
            (len(sw) - miss) / len(sw) * 100,
            statistics.mean([r["ms"] for r in results]),
            statistics.median([r["ms"] for r in results]),
        )
    )
    print()
    return results


# ---------------------------------------------------------------- 第 3 部分：误伤的实际伤害
def part3_retrieval(results: list[dict]) -> None:
    """对每条换题句，比较「上一轮 topic」（缓存方案）与「E 抽出的新 topic」（现状）
    检索出来的文档，判断误伤是否真的造成伤害。

    只跑换题集里 E 正确判成换题的那些 —— 缓存方案在这些轮上会被带偏，
    这正是要量化的对象。
    """
    print("=" * 78)
    print("第 3 部分：误伤的实际伤害（用上一轮 topic 检索 vs 用新 topic 检索）")
    print("=" * 78)

    from agent.rag.indexer import load_knowledge_index
    from agent.rag.retriever import retrieve_knowledge_docs

    vs = load_knowledge_index("cs_basics", str(BACKEND_DIR / "chroma_kb"))
    if vs is None:
        print("  跳过：chroma_kb 加载失败")
        return

    targets = [
        r
        for r in results
        if r["label"] == "switch" and not r["reused"]
    ]
    if not targets:
        print("  没有可分析的样本（E 在换题集上没判出换题？）")
        return

    print("  样本：E 判为换题的 %d 条\n" % len(targets))
    same = 0
    diluted = 0
    broken = 0
    for r in targets:
        trace_old: dict = {}
        docs_old = retrieve_knowledge_docs(
            vs, r["latest"], topic=r["prev_topic"], trace=trace_old
        )
        files_old = [
            os.path.basename(str(d.metadata.get("source", "?"))) for d in docs_old
        ]

        trace_new: dict = {}
        docs_new = retrieve_knowledge_docs(
            vs, r["latest"], topic=r["raw"].get("topic", ""), trace=trace_new
        )
        files_new = [
            os.path.basename(str(d.metadata.get("source", "?"))) for d in docs_new
        ]

        want = r["want_file"]
        ok_old = want in files_old
        ok_new = want in files_new
        if files_old == files_new:
            verdict, bucket = "完全一致（无害）", "same"
            same += 1
        elif ok_old:
            verdict, bucket = "期望文档仍命中（被稀释）", "diluted"
            diluted += 1
        else:
            verdict, bucket = "期望文档丢失（有害）", "broken"
            broken += 1
        print(
            "  %-24s 上一轮=%s\n"
            "      缓存(topic=%-14s) -> %s  期望%s\n"
            "      现状(topic=%-14s) -> %s  期望%s\n"
            "      ==> %s"
            % (
                r["latest"],
                r["prev_topic"],
                r["prev_topic"],
                files_old,
                "✓" if ok_old else "✗",
                str(r["raw"].get("topic", ""))[:14],
                files_new,
                "✓" if ok_new else "✗",
                verdict,
            )
        )

    print(
        "\n  合计 %d 条：无害 %d / 稀释 %d / 有害 %d（有害 = 学生换题后拿到的还是旧知识点原文）"
        % (len(targets), same, diluted, broken)
    )
    print()


async def main_async(args) -> int:
    cases = FOLLOWUPS + SWITCHES
    print("用例 %d 条（追问 %d / 换题 %d）\n" % (len(cases), len(FOLLOWUPS), len(SWITCHES)))

    if not args.no_embed:
        await asyncio.to_thread(part1_separation, cases)

    if args.no_llm:
        return 0

    results = await part2_llm(cases, args.provider)
    if not args.no_retrieve:
        await asyncio.to_thread(part3_retrieval, results)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="openai")
    ap.add_argument("--no-llm", action="store_true", help="不跑 LLM，只测本地向量分离度")
    ap.add_argument("--no-embed", action="store_true", help="跳过第 1 部分")
    ap.add_argument("--no-retrieve", action="store_true", help="跳过第 3 部分检索复算")
    args = ap.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
