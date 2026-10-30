"""检索质量体检：库内题能否命中 + 库外题会不会被误注入。

用法（backend/ 下）：
    RAG_PARENT_CHILD=1 ./venv/Scripts/python.exe scripts/diag_rag_gaps.py

判读：
  - 库内题 → 看 top1 是否落在期望文档；跑偏说明召回/排序有偏差
  - 库外题 → 注入条数应为 0；非 0 即为「误注入」，模型会拿无关资料当依据
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("RAG_PARENT_CHILD", "1")  # 必须在 import CONFIG 之前

from agent.rag.indexer import load_knowledge_index  # noqa: E402
from agent.rag.retriever import CONFIG, retrieve_knowledge_docs  # noqa: E402

# (查询, 期望命中的文档；None 表示库外题，期望 0 条注入, 评估节点会抽出的 topic)
# topic 用生产同一入口传进去：线上 /chat 会抽 topic，评测 e2e_runner 也是带 topic 跑的，
# 不带 topic 属于「抽不出知识点」的退化场景，两种都要看。
CASES = [
    ("快排的时间复杂度是多少", "dsa/sorting.md", "快速排序"),
    ("TIME_WAIT 为什么要等那么久", "network/tcp-handshake.md", "TCP 连接管理"),
    ("死锁怎么预防", "os/deadlock.md", "死锁"),
    ("B+ 树索引为什么快", "db/index-transaction.md", "B+ 树索引"),
    ("TCP 三次握手为什么是三次", "network/tcp-handshake.md", "TCP 三次握手"),
    ("进程和线程有什么区别", "os/process-thread.md", "进程与线程"),
    ("Python 里列表和元组有什么区别", None, ""),
    ("什么是变量", None, ""),
    ("React 的 useEffect 怎么用", None, ""),
    ("git 怎么回退到上一个版本", None, ""),
]


def short(source: str) -> str:
    return source.replace("cs/", "") if source else "?"


def main() -> int:
    store = load_knowledge_index(persist_directory="chroma_kb")
    if store is None:
        print("!! chroma_kb 打不开")
        return 2

    print(f"config: parent_child={CONFIG.parent_child} coarse_k={CONFIG.coarse_k} "
          f"final_k={CONFIG.final_k} floor={CONFIG.relevance_floor} "
          f"per_doc_floor={CONFIG.per_doc_floor}\n")

    inside_ok = inside_total = 0
    outside_bad = outside_total = 0

    for query, expect, topic in CASES:
        modes = [("无topic", "")]
        if topic:
            modes.append((f"带topic={topic}", topic))
        for label, tp in modes:
            trace: dict = {}
            docs = retrieve_knowledge_docs(store, query, topic=tp, trace=trace)
            srcs = [short((d.metadata or {}).get("source", "")) for d in docs]
            top = trace.get("relevance_top_score")
            scores = trace.get("per_doc_scores") or []
            kind = "库内" if expect else "库外"
            # 统计只算线上口径：有 topic 的看「带 topic」那次，库外题没有 topic
            primary = (tp != "") or (topic == "")

            if expect:
                hit = bool(srcs) and srcs[0] == expect
                verdict = "OK" if hit else f"MISS(期望 {expect})"
            else:
                hit = len(docs) == 0
                verdict = "误注入!" if not hit else "空(正确)"

            if primary:
                if expect:
                    inside_total += 1
                    inside_ok += hit
                else:
                    outside_total += 1
                    outside_bad += 0 if hit else 1

            print(f"[{kind}/{label}] {query}")
            print(f"  {verdict}")
            print(f"  注入 {len(docs)} 条: {srcs}")
            print(f"  top_score={top}  scores={[round(s, 2) for s in scores]} "
                  f"filtered(整题)={trace.get('relevance_filtered')} "
                  f"filtered(逐条)={trace.get('per_doc_filtered')} "
                  f"rerank={trace.get('rerank')} {trace.get('rerank_ms')}ms")
            print()

    print("=" * 60)
    print(f"库内 top1 命中: {inside_ok}/{inside_total}")
    print(f"库外误注入: {outside_bad}/{outside_total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
