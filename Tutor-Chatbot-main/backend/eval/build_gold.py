"""从知识库主题标签派生切片级金标准 `relevant_chunks`。

**派生口径（先说清边界，避免把派生值当人工标注读）**：
- 金标准 = 该题期望主题（`expected`，完整 topic 名）下的**全部切片**
  `[source, start_index]`，来源是知识文档的一级标题，不是逐题人工判断。
- 它能回答：「top-k 对期望主题切片全集的覆盖程度」——
    recall    = 命中的主题切片数 / 该主题切片总数（**分母已知**，这是相对
                topic 级代理值的全部增量；主题切片多于 k 时 recall 天然封顶在 k/N）
    precision = top-k 中落在期望主题内的比例
- 它**不能**回答「这片是否真含该题的答案」，逐题细粒度相关性仍需人工标注。
- 因此 `--compare` 的退化门禁仍以 top1/top3 逐题判定为准；本派生值只进指标报告。

**粒度必须与「检索返回粒度」一致**（2026-10-25 扩库后新增；这是个静默失效坑）：
`metrics._chunk_key` 的切片身份是 `(source, start_index)`。父子库（§4.4）里
库里存的是 child，而 `retrieve_knowledge_docs` 聚合后返回的 parent 把
`start_index` 换成了 `parent_start`——**同一字段两套坐标**。若金标准仍按
child 坐标派生，L1 的 Recall/Precision 会全量错配（正确命中也匹配不上），
且**不会报任何错**，指标静默归零。因此本工具按库的实际形态自动选粒度：

    库内 metadata 有 parent_id  → parent 粒度，坐标 (source, parent_start)，
                                  金标准 = 每个二级小节 `##`
    否则                        → flat 粒度，坐标 (source, start_index)，
                                  与改造前行为逐字一致

派生结果写 `gold_granularity` 与 `gold_index_fingerprint`：前者让读文件的人
一眼知道坐标系，后者让「金标准与当前库是否同源」可机器校验（库重建后指纹变，
金标准必须重刷）。

**gain 三档会被重置**（换粒度后必然如此）：`refine_gold.py` 标的 1 / 0.5 / 0
是按旧粒度的切片内容判的，粒度一变就对不上号，留着比删掉更危险（静默错配）。
派生只写 2 元组 `[source, start_index]`、把 `gold_source` 降回 `topic-derived`，
**派生完必须重跑 `python -m eval.refine_gold --apply`** 才恢复细粒度增益。

用法（在 backend/ 下）：
    python -m eval.build_gold --dry-run   # 只看统计，不写文件
    python -m eval.build_gold             # 写回 rag_eval_cases.json（幂等）
    python -m eval.build_gold --granularity flat   # 强制扁平坐标（须与库一致）

设计约束：
- 只增改 `relevant_chunks` / `gold_source` / `_note` / `gold_granularity` /
  `gold_index_fingerprint`，其余字段原样保留（含顺序与缩进风格）。
- expected 里的 topic 若在库中找不到对应切片，属于评测集与文档标题失配，
  必须逐条列出并按非 0 退出——静默跳过会产生"看似已标注实则空集"的用例。
- 显式 `--granularity` 与库实际形态不符时直接非 0 退出：允许静默跑下去，
  等于把"金标准与库不同源"包装成正常结果。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from agent.rag.indexer import load_knowledge_index  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parent.parent
CASES_FILE = BACKEND_DIR / "rag_eval_cases.json"
COLLECTION = "cs_basics"
PERSIST_DIR = str(BACKEND_DIR / "chroma_kb")

GOLD_SOURCE_TAG = "topic-derived"
REFINED_GOLD_SOURCE = "topic-derived+llm-refined"
GRANULARITY_AUTO = "auto"
GRANULARITY_FLAT = "flat"
GRANULARITY_PARENT = "parent"
# 与 flat 同义：库内切片本身（父子库里就是 child）。仅作别名常量，
# 让调用方能明确表达"按库内切片派生"，实际分支只有 parent 与"其余"。
GRANULARITY_CHILD = "child"
GRANULARITIES = (GRANULARITY_AUTO, GRANULARITY_FLAT, GRANULARITY_PARENT)


def norm(s: str) -> str:
    """与 smoke_rag.top_hit 同口径：去空格、转小写。

    评测集里存在 `tcp 与 udp`（小写带空格）而库内 topic 是 `TCP 与 UDP` 的历史写法，
    旧判定靠归一化才没失配——金标准派生必须用同一把尺子，否则会出现
    「top1/top3 判定命中、金标准却查无此题」的分裂。
    """
    return s.replace(" ", "").lower()


def detect_granularity(metas: list[dict]) -> str:
    """按库里 metadata 的实际形态判断粒度：有 parent_id 就是父子库。

    只看 metadata 不看环境变量：金标准的坐标系必须由**库**决定，
    而不是由跑评测时忘了 export 的那个变量决定。
    """
    for meta in metas:
        if (meta or {}).get("parent_id"):
            return GRANULARITY_PARENT
    return GRANULARITY_FLAT


def chunk_key(meta: dict, granularity: str) -> tuple[str, int]:
    """一条库内切片在该粒度下的金标准坐标。

    parent 粒度用 `parent_start`（与 `aggregate_parent_docs` 把
    `start_index` 换成父小节起点完全对应）；flat 粒度用 `start_index`。
    """
    meta = meta or {}
    source = str(meta.get("source") or "")
    if granularity == GRANULARITY_PARENT:
        return source, int(meta.get("parent_start") or 0)
    return source, int(meta.get("start_index") or 0)


def load_topic_chunks(store, granularity: str = GRANULARITY_FLAT) -> dict[str, list[tuple[str, int]]]:
    """按 topic 归组全部切片，返回 {归一化topic: [(source, start_index), ...]}。

    parent 粒度下按 `parent_id` 去重（同一小节的多个 child 只算一片）——
    不去重会把一个 4 节的小节集数成 13 片，Recall 的分母直接灌水。
    """
    raw = store.get()
    groups: dict[str, list[tuple[str, int]]] = {}
    seen: set[str] = set()
    for text, meta in zip(raw.get("documents") or [], raw.get("metadatas") or []):
        meta = meta or {}
        topic = str(meta.get("topic") or "")
        if not topic:
            continue
        if granularity == GRANULARITY_PARENT:
            parent_id = meta.get("parent_id")
            if not parent_id:
                # 混库（父子库掺了扁平片）：扁平片没有 parent 坐标，不参与 parent 金标准
                continue
            if parent_id in seen:
                continue
            seen.add(str(parent_id))
        groups.setdefault(norm(topic), []).append(chunk_key(meta, granularity))
    return groups


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="派生切片级金标准 relevant_chunks")
    parser.add_argument("--dry-run", action="store_true", help="只打印统计，不写文件")
    parser.add_argument("--cases", default=str(CASES_FILE), help="评测集 JSON 路径")
    parser.add_argument(
        "--granularity",
        default=GRANULARITY_AUTO,
        choices=GRANULARITIES,
        help="金标准粒度：auto（按库形态，默认）/ flat / parent",
    )
    args = parser.parse_args(argv)

    store = load_knowledge_index(COLLECTION, PERSIST_DIR)
    if store is None:
        raise SystemExit("知识库不存在：先跑 python build_kb.py")

    from eval.fingerprint import collection_fingerprint

    raw_meta = (store.get(include=["metadatas"]) or {}).get("metadatas") or []
    detected = detect_granularity(raw_meta)
    granularity = detected if args.granularity == GRANULARITY_AUTO else args.granularity
    if args.granularity != GRANULARITY_AUTO and args.granularity != detected:
        print("✗ 显式指定 --granularity=%s，但库里实际是 %s（由 metadata 是否有 parent_id 决定）。" % (args.granularity, detected))
        print("  两者不符时派生出的坐标与检索返回值对不上，L1 指标会静默归零——先重建库或改用正确粒度。")
        return 1

    topic_chunks = load_topic_chunks(store, granularity)
    chunk_total = sum(len(v) for v in topic_chunks.values())
    unit = "parent 数" if granularity == GRANULARITY_PARENT else "切片数"
    print("collection=%s %s=%d 主题数=%d 粒度=%s（自动探测=%s）"
          % (COLLECTION, unit, chunk_total, len(topic_chunks), granularity, detected))

    # 注入窗口：与 retriever 实际返回条数同源，不在这里硬编码 4。
    # 父子模式注入 parent_final_k（默认 3）条，而每题金标准有 4 个 parent——
    # Recall 的物理上限因此是 3/4，不写清楚会被当成"检索变差了"。
    from agent.rag.retriever import CONFIG as _RAG_CONFIG

    inject_k = (
        _RAG_CONFIG.parent_final_k
        if granularity == GRANULARITY_PARENT
        else _RAG_CONFIG.final_k
    )

    data = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    cases = data.get("cases", [])
    if not cases:
        raise SystemExit("评测集为空：%s" % args.cases)

    prev_granularity = data.get("gold_granularity")
    if prev_granularity and prev_granularity != granularity:
        print("! 金标准粒度变更：%s → %s（历史 L1/L3/L4 指标与之前不可直接比较）"
              % (prev_granularity, granularity))

    missing_topics: Counter[str] = Counter()
    gold_sizes: list[int] = []
    annotated = 0
    gain_reset = 0
    for item in cases:
        expected = {str(t) for t in (item.get("expected") or [])}
        if item.get("gold_source") == REFINED_GOLD_SOURCE:
            gain_reset += 1
        if not expected:
            # 库外题（oos）：没有期望主题，保持无标注
            item.pop("relevant_chunks", None)
            continue

        gold: list[list] = []
        for topic in sorted(expected):
            chunks = topic_chunks.get(norm(topic))
            if not chunks:
                missing_topics[topic] += 1
                continue
            gold.extend([source, start] for source, start in sorted(chunks))

        if gold:
            item["relevant_chunks"] = gold
            item["gold_source"] = GOLD_SOURCE_TAG
            # gain 三档按旧粒度标的，换粒度后必然错配——显式删掉，
            # 不给「看似还带着细粒度标注」的假象。
            item.pop("_note", None)
            annotated += 1
            gold_sizes.append(len(gold))
        else:
            item.pop("relevant_chunks", None)
            item.pop("gold_source", None)
            item.pop("_note", None)

    fingerprint = collection_fingerprint(store)

    print()
    print("用例 %d 条：派生标注 %d 条（库外/无期望 %d 条保持无标注）"
          % (len(cases), annotated, len(cases) - annotated))
    if gold_sizes:
        gold_sizes.sort()
        hist = Counter(gold_sizes)
        print("每题金标准%s数：min %d / 中位 %d / max %d"
              % ("parent " if granularity == GRANULARITY_PARENT else "切片",
                 gold_sizes[0], gold_sizes[len(gold_sizes) // 2], gold_sizes[-1]))
        print("  分布：%s" % "、".join("%d 条 × %d 题" % (n, c) for n, c in sorted(hist.items())))
        over_k = sum(1 for n in gold_sizes if n > inject_k)
        print("注入窗口 %d 条：金标准条数 > 窗口的用例 %d 条" % (inject_k, over_k))
        print(
            "  注意：这不等于 Recall 封顶——金标准增益里有 0 分片，而 Recall 的分母只计"
            "正增益，故 n>k 也可能接近 1.0。跨粒度/跨库对比一律看 nDCG。"
        )
    if gain_reset:
        print("! gain 三档已重置 %d 条（旧标注按 %s 粒度，与当前 %s 不可比）"
              % (gain_reset, prev_granularity or "旧", granularity))
        print("  派生完必须重跑：python -m eval.refine_gold --apply")

    if missing_topics:
        print()
        print("✗ 以下 expected topic 在库中无对应切片（评测集与文档标题失配）：")
        for topic, count in sorted(missing_topics.items()):
            print("  - %s（%d 条用例）" % (topic, count))
        print("先修评测集或文档标题，再落标注——静默跳过会产生空金标准。")
        return 1

    if args.dry_run:
        print()
        print("（--dry-run，未写文件）")
        return 0

    data["gold_granularity"] = granularity
    data["gold_index_fingerprint"] = fingerprint

    Path(args.cases).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print()
    print("已写回：%s" % args.cases)
    print("  粒度=%s  索引指纹=%s" % (granularity, fingerprint))
    return 0


if __name__ == "__main__":
    sys.exit(main())
