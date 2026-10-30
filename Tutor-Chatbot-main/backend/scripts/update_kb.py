"""增量更新常驻知识库（文件级「删 + 插」，不是切片级 upsert）。

用法（在 backend/ 目录下，用 venv 的 python）：
    python scripts/update_kb.py                       # 按源文件差异增量更新
    python scripts/update_kb.py --dry-run             # 只打印将要改哪些文档，不动库
    python scripts/update_kb.py --full                # 强制全量重建（等同 build_kb.py）
    python scripts/update_kb.py --query "死锁的必要条件"

切片模式不用手动指定：父子模式是 opt-in，但库内 metadata 存了建库时的
chunk_params_hash，脚本会拿它与「父子 / 单层」两种参数各算一次哈希比对，
自动对齐成建库时的模式（仅当库内参数未混杂时）。忘记加 --parent-child
不会再退化成全量重建（2026-10-26 实测修复）。显式指定 > 环境变量 > 自动推断。

为什么不是切片级 upsert（详见 RAG_HANDBOOK §8）：
    切片的 start_index / parent_start 是**全文位置偏移**。在第 3 段改一句话，
    后面所有切片的偏移量全部平移，按切片 upsert 时 Chroma 视为全新 ID 直接插入，
    旧切片没有任何机制会被删掉 → 库里同时存在改前改后的同一段话，
    检索命中哪一份是随机的（「孤儿切片」）。
    按 source 整篇「先删后插」则天然规避：source 是稳定主键，整篇删净，且幂等。

状态存库内，不外挂 docstore：
    切片 metadata 里带 doc_hash（源文件内容哈希）与 chunk_params_hash（切片参数快照），
    比对「磁盘 vs 库」即可得出变更集。knowledge/ 目录遍历即得完整清单，
    不需要 LlamaIndex 的 docstore 或 LangChain 的 RecordManager。
    （这两者是为网页/数据库这类无法廉价列举的输入源设计的，本项目用不上。）

已知限制（实测，见手册 §8）：
    1. 更新后进程不会立刻看到新内容（已打开过该库的进程持有旧内存快照）。
       **调一次热重载即可，不必重启**：`POST /admin/reload-kb`（XB-19）。
       不调也不报错——不 drop_collection，服务用旧数据继续正常响应。
       注意：`collection.count()` 看不出差别（它读 SQLite 元数据，永远返回
       最新值），判据只能是真实检索。
    2. 切片参数变更会自动回退为全量重建（--no-fallback 可改成报错退出）。
    3. 换 embedding 模型必须手动跑 build_kb.py（向量空间整体变了）。
"""

import argparse
import json
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv

load_dotenv()

from agent.rag.indexer import (  # noqa: E402
    build_chunks,
    build_knowledge_index,
    chunk_params_hash,
    chunk_params_signature,
    load_markdown_documents,
    load_knowledge_index,
    parent_child_enabled,
)

import build_kb  # 复用 drop_collection / write_baseline_meta / 默认路径  # noqa: E402

COLLECTION_NAME = build_kb.COLLECTION_NAME
DEFAULT_KNOWLEDGE_DIR = build_kb.DEFAULT_KNOWLEDGE_DIR
DEFAULT_PERSIST_DIR = build_kb.DEFAULT_PERSIST_DIR


# ---------------- 纯函数部分（可单测，不碰真实向量库）----------------


def scan_disk(knowledge_dir: str) -> dict[str, str]:
    """磁盘侧状态：source -> doc_hash。

    复用 load_markdown_documents，保证 source 的规范化规则（相对路径、
    正斜杠）与库里存的完全一致——两边口径不一致会让每篇都被判成「已变更」。
    """
    out: dict[str, str] = {}
    for doc in load_markdown_documents(knowledge_dir):
        meta = doc.metadata or {}
        source = str(meta.get("source") or "")
        if source:
            out[source] = str(meta.get("doc_hash") or "")
    return out


def read_library(metadatas: list[dict]) -> tuple[dict[str, str], set[str]]:
    """库侧状态：source -> doc_hash，以及出现过的切片参数哈希集合。

    只吃 metadatas 列表（不碰向量、不需要 store 实例），这样纯函数可直接单测。
    同一 source 下所有切片的 doc_hash 相同，先到先得即可。
    """
    lib: dict[str, str] = {}
    params: set[str] = set()
    for meta in metadatas:
        meta = meta or {}
        source = str(meta.get("source") or "")
        if not source:
            continue
        lib.setdefault(source, str(meta.get("doc_hash") or ""))
        params_hash = meta.get("chunk_params_hash")
        if params_hash:
            params.add(str(params_hash))
    return lib, params


def plan_update(disk: dict[str, str], lib: dict[str, str]) -> tuple[list[str], list[str]]:
    """算出变更集：返回 (需要重插的 source, 需要删除的 source)。

    - 新增与变更都归到「重插」：先删后插，因此不需要 deterministic ID，
      也不必区分这两种情况。
    - 源文件已不在磁盘上 → 删除（这是 LangChain Indexing API 的 incremental
      模式做不到的，它只有 full 模式处理删除）。
    """
    to_add = sorted(s for s in disk if s not in lib or lib[s] != disk[s])
    to_del = sorted(s for s in lib if s not in disk)
    return to_add, to_del


def needs_full_rebuild(lib_params: set[str], current_params_hash: str, lib_size: int) -> bool:
    """是否必须全量重建。

    三种情况：
      1. 库非空但完全没有 doc_hash（旧版本建的库）→ 无法判断变更，
         继续走增量会退化成「全部重插」，不如直接全量更可预期。
      2. **库里混着多种切片参数** → 库处于半更新状态（上一次更新只覆盖了
         部分文档）。此时增量只会改「内容变了」的文档，那批旧参数文档的
         内容没变、永远不会被重切，库会一直不一致。必须全量。
      3. 当前切片参数不在库里 → 参数变过。增量按文件内容哈希判断，
         参数变了但内容没变会被漏掉，必须全量。
    """
    if lib_size == 0:
        return False
    if not lib_params:
        return True
    if len(lib_params) > 1:
        return True
    return current_params_hash not in lib_params


def infer_chunk_mode(
    lib_params: set[str],
    explicit: bool,
    env_enabled: bool,
) -> bool | None:
    """按库内切片参数快照反推建库时用的是哪种模式（True=父子 / False=单层）。

    父子模式是 opt-in（RAG_PARENT_CHILD=1 或 --parent-child），但库可能是按父子建的。
    本次命令没带开关、环境变量也没开时，若按 flat 去算 current_params，
    needs_full_rebuild 会判「切片参数已变更」→ 每次都回退全量重建，增量形同虚设
    （2026-10-26 实测：新增一篇文档却报"需全量重建"）。
    库内 metadata 存了 chunk_params_hash，拿它与两种模式各算一次哈希比对即可反推。

    返回 None = 推不出来，调用方保持原值：
      - 用户已显式指定开关或开了环境变量（显式优先，绝不覆盖）；
      - 库是空的 / 旧版无参数 / 混杂多种参数（后者反正要全量，不必对齐）。
    """
    if explicit or env_enabled:
        return None
    if len(lib_params) != 1:
        return None
    for flag in (True, False):
        if chunk_params_hash(flag) in lib_params:
            return flag
    return None


# ---------------- 执行部分 ----------------


def delete_sources(store, sources: list[str]) -> int:
    """按 source 整篇删除，返回耗时毫秒。

    批量用 `$in` 一次完成，避免 N 次全表扫描——实测单次 delete(where=)
    在 940 片库上约 0.6s，是增量的主要成本，按篇循环会随规模线性放大。
    """
    if not sources:
        return 0
    started = time.perf_counter()
    if len(sources) == 1:
        store._collection.delete(where={"source": sources[0]})
    else:
        store._collection.delete(where={"source": {"$in": sources}})
    return int((time.perf_counter() - started) * 1000)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="增量更新常驻知识库（文件级删+插）",
    )
    parser.add_argument("--knowledge-dir", default=str(DEFAULT_KNOWLEDGE_DIR))
    parser.add_argument("--persist-dir", default=str(DEFAULT_PERSIST_DIR))
    parser.add_argument("--query", action="append", default=[])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印将要新增/变更/删除哪些文档，不改动向量库",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="强制全量重建（等同 build_kb.py，用于切片参数变更或换 embedding 模型）",
    )
    parser.add_argument(
        "--no-fallback",
        action="store_true",
        help="切片参数变更时不自动回退全量，改为报错退出（CI 里用于暴露意外变更）",
    )
    parser.add_argument(
        "--parent-child",
        action="store_true",
        help="父子切片（默认读 RAG_PARENT_CHILD 环境变量）",
    )
    parser.add_argument(
        "--meta-path",
        default="",
        help="基线元信息写到这里（默认 eval/baseline_meta.json；隔离验证时用临时路径）",
    )
    args = parser.parse_args()

    queries = args.query or []

    if not Path(args.knowledge_dir).is_dir():
        print("知识目录不存在：%s" % args.knowledge_dir)
        return 1

    use_parent_child = args.parent_child or parent_child_enabled()

    def _mode_label(flag: bool) -> str:
        return "父子（child 检索 + parent 注入）" if flag else "单层 500/80"

    print("知识目录：%s" % args.knowledge_dir)
    print("向量库  ：%s" % args.persist_dir)

    # ---- 强制全量分支 ----
    if args.full:
        print("切片模式：%s" % _mode_label(use_parent_child))
        print("（--full：执行全量重建）")
        build_kb.drop_collection(args.persist_dir)
        started = time.perf_counter()
        store = build_knowledge_index(
            knowledge_dir=args.knowledge_dir,
            collection_name=COLLECTION_NAME,
            persist_directory=args.persist_dir,
            parent_child=use_parent_child,
        )
        build_ms = int((time.perf_counter() - started) * 1000)
        print()
        print("全量重建完成，切片数：%d，耗时 %.2fs" % (store._collection.count(), build_ms / 1000))
        build_kb.write_baseline_meta(
            store,
            chunk_mode="parent-child" if use_parent_child else "flat",
            meta_path=args.meta_path,
        )
        if queries:
            build_kb.self_check(store, queries)
        return 0

    # ---- 增量分支 ----
    if not Path(args.persist_dir).exists():
        print("向量库不存在：%s" % args.persist_dir)
        print("首次构建请用：python build_kb.py")
        return 1

    store = load_knowledge_index(
        collection_name=COLLECTION_NAME, persist_directory=args.persist_dir
    )
    if store is None:
        print("向量库加载失败：%s" % args.persist_dir)
        return 1

    raw_metas = (store.get(include=["metadatas"]) or {}).get("metadatas") or []
    lib, lib_params = read_library(raw_metas)

    inferred = infer_chunk_mode(lib_params, args.parent_child, parent_child_enabled())
    if inferred is not None and inferred is not use_parent_child:
        use_parent_child = inferred
        print("※ 切片模式自动对齐为「%s」：按库内现有切片的参数快照反推，"
              "本次命令未指定模式（显式加 --parent-child 可覆盖）" % _mode_label(inferred))

    print("切片模式：%s" % _mode_label(use_parent_child))
    current_params = chunk_params_hash(use_parent_child)

    if needs_full_rebuild(lib_params, current_params, len(lib)):
        reason = (
            "库里没有 doc_hash（旧版本建的库，无法判断变更）"
            if not lib_params
            else "切片参数已变更：%s" % json.dumps(
                chunk_params_signature(use_parent_child), ensure_ascii=False, sort_keys=True
            )
        )
        print()
        print("※ 需要全量重建：%s" % reason)
        if args.dry_run:
            print("  （--dry-run：未改动向量库）")
            return 0
        if args.no_fallback:
            print("  （--no-fallback：已按配置报错退出，请手动跑 python build_kb.py）")
            return 2
        print("  （自动回退为全量重建；切片参数变更走增量会漏掉未改内容的文件）")
        build_kb.drop_collection(args.persist_dir)
        started = time.perf_counter()
        store = build_knowledge_index(
            knowledge_dir=args.knowledge_dir,
            collection_name=COLLECTION_NAME,
            persist_directory=args.persist_dir,
            parent_child=use_parent_child,
        )
        print()
        print(
            "全量重建完成，切片数：%d，耗时 %.2fs"
            % (store._collection.count(), time.perf_counter() - started)
        )
        build_kb.write_baseline_meta(
            store,
            chunk_mode="parent-child" if use_parent_child else "flat",
            meta_path=args.meta_path,
        )
        return 0

    disk = scan_disk(args.knowledge_dir)
    if not disk:
        print("知识目录里没有 .md 文件：%s" % args.knowledge_dir)
        return 1

    to_add, to_del = plan_update(disk, lib)

    print()
    print("=== 变更集 ===")
    print("  库内文档：%d 篇 / %d 切片" % (len(lib), len(raw_metas)))
    print("  磁盘文档：%d 篇" % len(disk))
    print("  新增+变更：%d 篇" % len(to_add))
    print("  已删除  ：%d 篇" % len(to_del))
    for source in to_add:
        tag = "新增" if source not in lib else "变更"
        print("    [%s] %s" % (tag, source))
    for source in to_del:
        print("    [删除] %s" % source)

    if args.dry_run:
        print()
        print("--dry-run：未改动向量库")
        return 0

    if not to_add and not to_del:
        print()
        print("无变更，向量库保持原样（基线元信息不刷新，避免掩盖后续改动）")
        return 0

    started = time.perf_counter()
    documents = [
        doc
        for doc in load_markdown_documents(args.knowledge_dir)
        if str((doc.metadata or {}).get("source") or "") in set(to_add)
    ]
    chunks = build_chunks(documents, use_parent_child)
    slice_ms = int((time.perf_counter() - started) * 1000)

    # 变更的先删后插，删除的只删
    del_ms = delete_sources(store, sorted(set(to_add) | set(to_del)))

    started = time.perf_counter()
    if chunks:
        store.add_documents(chunks)
    add_ms = int((time.perf_counter() - started) * 1000)

    total = slice_ms + del_ms + add_ms
    print()
    print(
        "增量完成：切片 %dms + 删除 %dms + 插入 %dms = %.2fs（新增/重插 %d 片）"
        % (slice_ms, del_ms, add_ms, total / 1000, len(chunks))
    )
    print("  更新后切片数：%d" % store._collection.count())

    build_kb.write_baseline_meta(
        store,
        chunk_mode="parent-child" if use_parent_child else "flat",
        meta_path=args.meta_path,
    )

    if queries:
        build_kb.self_check(store, queries)

    print()
    print("※ 生效方式：调一次热重载即可，不必重启服务")
    print("    curl -X POST http://127.0.0.1:8000/admin/reload-kb")
    print("  不调也不会报错——服务继续用旧数据正常响应，只是看不到新内容。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
