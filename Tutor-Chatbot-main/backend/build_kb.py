"""构建常驻八股知识库。

用法（在 backend/ 目录下，用 venv 的 python）：
    python build_kb.py                    # 构建并自检
    python build_kb.py --query "TCP 为什么三次握手"   # 只做检索自检

首次运行会下载中文 embedding 模型（约 100MB），之后走缓存。
知识文档在仓库根的 knowledge/ 目录，向量库落在 backend/chroma_kb/。

PROPOSAL_RAG_QUALITY_EVAL Wave 3：每次重建后写 eval/baseline_meta.json，
记录索引指纹 + embedding 模型 + 切片数 + 评测集题数。
没有它，smoke_rag.py --compare 可能在「库变了、基线没更新」的情况下
报"无退化"——那是假阴，比报错更危险。
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv

load_dotenv()

from agent.rag.indexer import (
    EMBED_MODEL,
    build_knowledge_index,
    load_knowledge_index,
    parent_child_enabled,
)
from eval.fingerprint import collection_fingerprint, save_baseline_meta

BACKEND_DIR = Path(__file__).resolve().parent
DEFAULT_KNOWLEDGE_DIR = BACKEND_DIR.parents[1] / "knowledge"
DEFAULT_PERSIST_DIR = BACKEND_DIR / "chroma_kb"
COLLECTION_NAME = "cs_basics"
CASES_FILE = BACKEND_DIR / "rag_eval_cases.json"


def count_eval_cases(path: Path = CASES_FILE) -> int:
    """数一下当前评测集有多少题，一起记进基线元信息。

    指纹相同但题目翻倍时（扩了题没扩库），指标口径其实也变了，
    只记指纹看不出来。读不到就记 0，不阻断构建。
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    return len(data.get("cases", []))


def write_baseline_meta(
    store, chunk_mode: str = "flat", meta_path: str | Path = ""
) -> None:
    """重建后刷新基线元信息（指纹 / embedding 模型 / 切片数 / 题数 / 切片模式）。

    chunk_mode 必须记：父子库与单层库的切片数、注入粒度都不同，
    只记指纹不足以让人看出「这份基线是用哪种切片策略跑出来的」。

    meta_path 给了就写到该路径（隔离验证 / 单测用），否则写生产默认位置。
    """
    fp = collection_fingerprint(store)
    chunks = store._collection.count()
    path = save_baseline_meta(
        fingerprint=fp,
        chunks_count=chunks,
        embed_model=EMBED_MODEL,
        cases_count=count_eval_cases(),
        extra={"collection": COLLECTION_NAME, "chunk_mode": chunk_mode},
        path=meta_path,
    )
    print()
    print("索引指纹：%s" % fp)
    print("  embedding 模型：%s" % EMBED_MODEL)
    print("  切片模式：%s" % chunk_mode)
    print("  已写入基线元信息：%s" % path)
    print("  （重建过库就要重存基线：python smoke_rag.py --save-base rag_baseline.json）")



def drop_collection(persist_dir: str) -> None:
    """删掉同名 collection，保证重建是幂等的。"""
    if not Path(persist_dir).exists():
        return
    try:
        import chromadb

        client = chromadb.PersistentClient(path=persist_dir)
        client.delete_collection(name=COLLECTION_NAME)
        print("已清理旧 collection：%s" % COLLECTION_NAME)
    except Exception as exc:
        print("清理旧 collection 跳过（%s：%s）" % (type(exc).__name__, exc))


def self_check(store, queries):
    print()
    print("=== 检索自检 ===")
    for query in queries:
        docs = store.similarity_search(query, k=2)
        print()
        print("问：%s" % query)
        for i, doc in enumerate(docs, 1):
            print(
                "  %d. [%s / %s] %s"
                % (
                    i,
                    doc.metadata.get("topic"),
                    doc.metadata.get("category"),
                    doc.page_content[:80].replace("\n", " "),
                )
            )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--knowledge-dir", default=str(DEFAULT_KNOWLEDGE_DIR))
    parser.add_argument("--persist-dir", default=str(DEFAULT_PERSIST_DIR))
    parser.add_argument("--query", action="append", default=[])
    parser.add_argument(
        "--parent-child",
        action="store_true",
        help="父子切片（PROPOSAL_RAG_SCALE §4.4，opt-in）：child 检索 + parent 注入",
    )
    parser.add_argument(
        "--meta-path",
        default="",
        help="基线元信息写到这里（默认 eval/baseline_meta.json；隔离验证时用临时路径，"
        "避免覆盖生产基线）",
    )
    args = parser.parse_args()

    queries = args.query or [
        "TCP 为什么是三次握手",
        "死锁的必要条件",
        "进程和线程的区别",
    ]

    if not Path(args.knowledge_dir).is_dir():
        print("知识目录不存在：%s" % args.knowledge_dir)
        return 1

    print("知识目录：%s" % args.knowledge_dir)
    print("向量库：%s" % args.persist_dir)

    # 持久化库是追加语义，重建前必须清掉旧 collection，
    # 否则每次构建都会翻倍（66 → 132 → 264...），检索里混进重复片段。
    drop_collection(args.persist_dir)

    use_parent_child = args.parent_child or parent_child_enabled()
    print("切片模式：%s" % ("父子（child 检索 + parent 注入）" if use_parent_child else "单层 500/80"))
    print("正在构建（首次要下载中文 embedding 模型，请耐心等待）...")

    store = build_knowledge_index(
        knowledge_dir=args.knowledge_dir,
        collection_name=COLLECTION_NAME,
        persist_directory=args.persist_dir,
        parent_child=use_parent_child,
    )

    count = store._collection.count()
    print()
    if use_parent_child:
        metas = (store.get(include=["metadatas"]) or {}).get("metadatas") or []
        parent_count = len(
            {str((m or {}).get("parent_id")) for m in metas if (m or {}).get("parent_id")}
        )
        print("构建完成，切片数：%d（child）／%d（parent 小节）" % (count, parent_count))
    else:
        print("构建完成，切片数：%d" % count)

    # 指纹必须在自检之前写：自检万一抛错，至少基线元信息已经落地
    write_baseline_meta(
        store,
        chunk_mode="parent-child" if use_parent_child else "flat",
        meta_path=args.meta_path,
    )

    self_check(store, queries)
    return 0


if __name__ == "__main__":
    sys.exit(main())
