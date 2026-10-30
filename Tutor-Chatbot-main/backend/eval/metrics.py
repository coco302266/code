"""评测指标：切片级检索质量（纯函数，零 IO、零 LLM）。

对应 PROPOSAL_RAG_QUALITY_EVAL.md 的 E2：把 `smoke_rag.py` 的 topic 级
top1/top3 判定升级为切片级 Recall / Precision / MRR / nDCG。

**为什么必须处理「没有金标准标注」的情况**（本文件最重要的一条）：
Recall 的定义是 `召回到的相关片增益 / 所有相关片增益`，分母需要完整的相关切片清单。
`rag_eval_cases.json` 当前 79 条计分用例带派生金标准
（`python -m eval.build_gold` 产出：金标准 = 期望主题的切片全集，分母已知），
4 条 oos 库外题无标注。两档口径的处理：

- 有标注 → 算真实指标，`annotated=True`。注意 topic 派生金标准度量的是
  「top-k 对主题切片全集的覆盖」，不是逐题细粒度相关性（那个仍需人工标注）；
- 无标注 → 增益退化为 topic 级（命中期望知识点 = 1，否则 0），
  recall 退化为"是否命中"（等价于旧的 top3 判定），`annotated=False`；
- 汇总层必须分组报告两类，不得混算平均——把代理值当真指标报，
  比不报更糟（读报告的人会以为这是切片级精度）。

增益分档（与提案 §2.2 一致）：
    1.0  切片在 `relevant_chunks` 金标准里
    0.5  不在金标准，但它的 topic 属于期望知识点（算"部分相关"）
    0.0  其他

**nDCG 归一化口径**（踩过一次坑，写死在这里）：
nDCG 必须落在 [0,1]，越界就是口径错了。两条约束：

1. 无标注时金标准为空，IDCG 无从算起。此时理想排序定义为「k 个位置全部命中」
   （本档位下命中即 1.0），即 `dcg([1.0] * k)`——全命中 = 1.0，符合直觉，
   也保证有界。注意此时的 nDCG 是「命中充分度 × 排序」，不纯是排序质量。
2. 已标注时走标准 IDCG（金标准增益降序取前 k）。但 0.5 的"部分相关"档是我们
   自己加的，金标准不完整时，一堆 0.5 的折损和会超过只用 1.0 算出的 IDCG。
   这时**截断到 1.0，并把截断次数记进 ChunkRel.clamped 报出来**——
   静默截断等于把口径问题藏起来，后面没人会再发现。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

GAIN_EXACT = 1.0
GAIN_TOPIC = 0.5
GAIN_NONE = 0.0


def dcg(gains: list[float]) -> float:
    """折损累计增益。第 i 位（从 0 起）除以 log2(i+2)。"""
    return sum(g / math.log2(i + 2) for i, g in enumerate(gains))


def idcg(gold_gains: list[float], k: int) -> float:
    """理想 DCG：金标准增益降序取前 k 后的 DCG。"""
    if k <= 0 or not gold_gains:
        return 0.0
    return dcg(sorted(gold_gains, reverse=True)[:k])


def ideal_gains(gold_gains: list[float], k: int, annotated: bool) -> list[float]:
    """理想排序的增益序列（nDCG 的分母）。

    已标注：标准做法，金标准增益降序取前 k。
    未标注：没有金标准，理想只能是「k 个位置全部命中」——全命中即满分 1.0，
    且天然有界（旧实现用 dcg([1.0]) 当分母，四命中能算出 2.56，越界）。
    """
    if k <= 0:
        return []
    if not annotated:
        return [GAIN_EXACT] * k
    return sorted(gold_gains or [], reverse=True)[:k]


def p95(values: list[float]) -> float:
    """最近秩 p95（与 agent/evaluation.percentile 同口径）。

    不插值：样本小的时候插值会凭空造出一个从未发生过的数值。
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, int(round(0.95 * len(ordered))))
    return float(ordered[min(rank, len(ordered)) - 1])


def _chunk_key(doc) -> tuple:
    """切片身份：(source, start_index)。与 retriever 的去重键一致。

    **身份跟粒度走**：父子库（§4.4）聚合后返回的 parent 把 `start_index`
    换成了 `parent_start`，所以金标准也必须按 parent 派生（`build_gold` 自动判别）。
    两套坐标混用不会报错，只会让 Recall/Precision 静默归零——正确命中也匹配不上。
    """
    meta = doc.metadata or {}
    return (meta.get("source"), meta.get("start_index"))


def _norm_topic(value) -> str:
    """topic 归一化：去空格、转小写（与 smoke_rag.norm / build_gold.norm 同一把尺子）。"""
    return str(value or "").replace(" ", "").lower()


def _topic_match(doc, expected_topics: set[str]) -> bool:
    """该片 topic 是否属于期望知识点。

    **必须与 top1/top3 判定同口径（归一化 + 子串）**：旧实现是
    `topic in expected_topics` 精确匹配，而评测集里写的是 `tcp 与 udp`
    （小写带空格）、库内是 `TCP 与 UDP`，于是这批题**永远拿不到 0.5 档**
    ——top1/top3 判它命中，切片级指标却判它 0 分，同一份报告里自相矛盾。
    """
    topic = _norm_topic((doc.metadata or {}).get("topic"))
    return any(_norm_topic(k) in topic for k in expected_topics)


def chunk_gains(
    docs: list,
    relevant_chunks: list | None,
    expected_topics: set[str] | None = None,
) -> list[float]:
    """给每条召回切片打增益分。

    Args:
        docs: retrieve_knowledge_docs 返回的 Document 列表。
        relevant_chunks: 金标准，元素为 [source, start_index]（list 或 tuple 都收）。
            为 None / 空 时退化为 topic 级判定。
        expected_topics: 期望知识点集合，用于 0.5 档与无标注回退。
    """
    expected_topics = expected_topics or set()
    gold: dict[tuple, float] = {}
    for item in relevant_chunks or []:
        if not item or len(item) < 2:
            continue
        gold[(item[0], item[1])] = GAIN_EXACT
        if len(item) > 2:  # 可选的第三位：自定义增益
            try:
                gold[(item[0], item[1])] = float(item[2])
            except (TypeError, ValueError):
                pass

    gains: list[float] = []
    for doc in docs:
        key = _chunk_key(doc)
        if gold:
            if key in gold:
                gains.append(gold[key])
            elif _topic_match(doc, expected_topics):
                gains.append(GAIN_TOPIC)
            else:
                gains.append(GAIN_NONE)
        else:
            gains.append(GAIN_EXACT if _topic_match(doc, expected_topics) else GAIN_NONE)
    return gains


@dataclass
class ChunkRel:
    """一条用例的切片级指标。"""

    gains: list[float] = field(default_factory=list)
    annotated: bool = False      # 是否有金标准标注（False 时 recall 是代理值）
    recall: float = 0.0
    precision: float = 0.0
    mrr: float = 0.0
    ndcg: float = 0.0
    clamped: bool = False        # nDCG 是否因部分相关档超界被截断到 1.0
    noise_rate: float = 0.0      # 注入窗口里零增益切片的占比（见 chunk_rel 口径）


def chunk_rel(
    docs: list,
    relevant_chunks: list | None = None,
    expected_topics: set[str] | None = None,
    k: int = 0,
) -> ChunkRel:
    """计算一条用例的切片级指标。

    Args:
        k: 截断位数（0 = 用召回条数）。precision 的分母就是它。
    """
    expected_topics = expected_topics or set()
    gains = chunk_gains(docs, relevant_chunks, expected_topics)
    k = k or len(gains)
    head = gains[:k]

    gold_gains: list[float] = []
    for item in relevant_chunks or []:
        if not item or len(item) < 2:
            continue
        value = GAIN_EXACT
        if len(item) > 2:
            try:
                value = float(item[2])
            except (TypeError, ValueError):
                value = GAIN_EXACT
        gold_gains.append(value)

    annotated = bool(gold_gains)
    if annotated:
        total = sum(gold_gains)
        recall = sum(g for g in head if g > 0) / total if total else 0.0
    else:
        # 无金标准：退化为"有没有命中"，与旧 top3 判定等价
        recall = 1.0 if any(g > 0 for g in head) else 0.0

    precision = sum(head) / k if k else 0.0

    mrr = 0.0
    for index, gain in enumerate(head):
        if gain > 0:
            mrr = 1.0 / (index + 1)
            break

    ideal = dcg(ideal_gains(gold_gains, k, annotated))
    ndcg = dcg(head) / ideal if ideal else 0.0
    clamped = ndcg > 1.0
    if clamped:
        ndcg = 1.0

    # 噪声率：注入窗口里**完全答不上题**的切片占比。
    # 与 precision 的区别：precision 是"增益占比"（0.5 档会拉高它），
    # 噪声率是"0 档条数占比"——precision 0.5 可能来自「3 条各 0.5」也可能来自
    # 「1 条 1.0 + 1 条 0.5 + 1 条 0」，后者的第三条是纯噪声，只有噪声率能看出来。
    # 未标注用例不算：没有金标准时 gain=0 可能只是"没标"，会假性拉高噪声。
    noise_rate = (
        round(sum(1 for g in head if g <= 0) / len(head), 4) if (annotated and head) else 0.0
    )

    return ChunkRel(
        gains=gains,
        annotated=annotated,
        recall=round(recall, 4),
        precision=round(precision, 4),
        mrr=round(mrr, 4),
        ndcg=round(ndcg, 4),
        clamped=clamped,
        noise_rate=noise_rate,
    )


def aggregate(rels: list[ChunkRel]) -> dict:
    """把多条用例的指标聚合成报告层需要的结构。

    已标注与未标注**分组统计**：混在一起平均等于把代理值伪装成真指标。
    """
    def mean(values: list[float]) -> float:
        return round(sum(values) / len(values), 4) if values else 0.0

    annotated = [r for r in rels if r.annotated]
    fallback = [r for r in rels if not r.annotated]
    return {
        "cases": len(rels),
        "annotated_cases": len(annotated),
        "fallback_cases": len(fallback),
        "recall_at_k": mean([r.recall for r in annotated]) if annotated else None,
        "precision_at_k": mean([r.precision for r in annotated]) if annotated else None,
        # 噪声率：与 recall/precision 同分母组（只算已标注用例），
        # 是对"上下文精确率"的补充口径——0 档条数占比，不看 0.5 档的贡献。
        "noise_rate": mean([r.noise_rate for r in annotated]) if annotated else None,
        "mrr": mean([r.mrr for r in rels]),
        "ndcg_at_k": mean([r.ndcg for r in rels]),
        # 未标注组的 ndcg/mrr 仍是有效的排序质量指标（只看排序不看召回是否完整），
        # 单独列出，读报告的人自己决定怎么用。
        "ndcg_at_k_fallback": mean([r.ndcg for r in fallback]) if fallback else None,
        # 被截断到 1.0 的用例数：非零说明金标准不完整 + 部分相关档在起作用，
        # 是"该补标注了"的信号，不是"指标很好"的信号。
        "ndcg_clamped_cases": sum(1 for r in rels if r.clamped),
    }
