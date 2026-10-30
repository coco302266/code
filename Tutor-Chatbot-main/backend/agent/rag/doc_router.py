"""文档路由（PROPOSAL_RAG_SCALE §4.3 的 L1）：先选文档，再选切片。

## 为什么需要这一层

库到 20 篇 / 248 片之后，一次查询要让 20 篇文档的切片同台竞争。向量粗召回
只取 6 条，于是**正确文档的切片必须先在 248 片里挤进前 6**——这是在赌召回，
而不是在选文档。实测两处由此受伤：

1. 重排被干扰项压过：问「快排的时间复杂度」时正确切片 `dsa/sorting.md` 排到
   第 3，输给了完全不相关的 `os/scheduling.md`。召回是对的，排序是错的，
   因为进候选池的 6 条里塞了别的主题的切片。
2. 相关性下限被迫在一条窄缝里工作：库内事实题 -1.5~-1.8，库外题 -2.3~-3.8，
   阈值只能落在 -2.0，余量只有 0.25。

这一层先把「这个问题属于哪几篇文档」判掉，再把检索范围收窄到那几篇。

```
学生问题 ──①──> 文档级打分（切片证据按 source 聚合）
                   │ 取 top-N 篇
                   ▼
             ②──> 主库切片检索 + filter={"source": {"$in": [N 篇]}}
                   ▼
             ③──> 现有链路（BM25 → RRF → 重排 → 两级 floor）
```

## 为什么不用「每篇一条摘要向量」

§4.3 原本的设计是给每篇文档生成一条摘要（一级标题 + 二级标题 + 首段），
单独做一个小集合检索。**实测否决**（`scripts/calibrate_doc_router.py` 与
`scripts/diag_router_signals.py`，71 条带金标准题库）：

| 信号 | route@1 | route@3 | route@5 |
|---|---|---|---|
| 摘要向量 | — | **60/71（84.5%）** | 60/71 |
| vec-max（切片最高相似度按文档取 max） | 56/71 | 65/71 | 70/71 |
| vec-rrf | 54/71 | 64/71 | 68/71 |
| **bm25-sum（切片 BM25 分按文档求和）** | **66/71** | **71/71** | 71/71 |
| mix（bm25-sum + vec-rrf 各归一后相加） | 63/71 | **71/71** | 71/71 |

摘要向量差在两个地方：一是摘要被模板化的小节名污染（「一句话结论 / 面试常问」
几乎每篇都有，等于给所有文档加了一份共同噪音），二是有损——一篇 9 片文档压成
180 字后，具体术语大量丢失。而 bge-small-zh 只有 512 维，本来就不擅长区分
「同属一个学科、术语高度重叠」的短文档。

**换成聚合就解决了**：向量检索和 BM25 本来就在全库跑，把命中结果按 `source`
聚一下就是文档级排序，用的是全文语义而不是有损摘要，且不需要新建索引、
不需要额外 embedding、不会与主库不一致。BM25 在这个中文题库上特别强，
因为面试题与文档共享大量字面术语。

## 最终选型（71 条带金标准题库实测）

信号 = `bm25_agg`（每篇取最高 3 片求和）归一后 + `0.3 ×` 向量 RRF 归一后。

| 配置 | route@1 | route@3 |
|---|---|---|
| 摘要向量（原 §4.3 设计） | — | 60/71（84.5%） |
| 纯 BM25 全量求和 | 66/71 | 69/71 |
| 纯 BM25（每篇 top-3 片） | 66/70 | 69/70 |
| **bm25-top3 + 0.3×vec-rrf（当前）** | **67/71** | **71/71** |

两个关键修正都来自数据，不是调参：

1. **不能全量求和**：切片数 9~27 不等，长文档命中片多、求和后系统性占优，
   会把真正的目标文档挤出 top3（`http-https.md` 27 片尤其明显）。
   改成「每篇只取最高 N 片」等价于对文档长度做归一。
2. **向量不能与 BM25 平权**：等权时向量会把 BM25 已排对的文档挤下去
   （route@3 从 71/71 掉到 69/71）。向量的定位是「字面零命中时的兜底」，
   权重 0.3 是实测最优。

## 收窄为什么不会掉分

交叉编码器的分数是**逐对独立**打出来的（query 与 passage 两两配对），
候选池里多了谁少了谁都不会改变某个切片自己的分数。所以：

- 正确切片本来就在全库 top-6 → 收窄后它只可能更靠前，不会更靠后；
- 正确切片没进全库 top-6，但它所属文档被路由到 → 它被捞回来，是净增益；
- 正确切片所属文档没被路由到 → 才真正掉分，而这正是 route@3 = 71/71 兜住的。

实测印证：完整链路 A/B（71 题）top1 68/71 两边相同、注入总数 204 条相同、
逐例零变化，只有库外误注入从 2 例降到 1 例。**当前库规模下这是质量中性、
结构更稳的一层**；它的收益随库变大才显现（候选池恒为 6 条，
不再随库增长而稀释）。

## 域外闸门为什么不默认开

`RAG_ROUTER_GATE` 默认 off，这不是保守，是实测结论。用 IDF 加权的查询词
覆盖率做闸门，在 71 条库内题 + 23 条域外探针上扫阈值：

| 阈值 | 挡掉库外 | 误杀库内 |
|---|---|---|
| 0.25 | 17/23 | 0/71 |
| 0.30 | 18/23 | 1/71 |
| 0.40 | 20/23 | 1/71 |
| 0.50 | 20/23 | 4/71 |

挡不住的那几条暴露了根本问题：**「Python 里列表和元组有什么区别」的
`python`(df=2) / `列表`(df=1) / `元组`(df=1) 在库里都出现**
——被文档顺带提到过，所以任何「词是否出现在知识库」的判据都会把它判成库内题
（覆盖率 0.397）。而带 topic 的口语追问「死锁 + 能举个实际例子吗」只有 0.299
（「能举/例子」在技术库里 df=0，占了 IDF 分母）。两者只差 0.098，
没有余量支撑一条会**静默丢掉整轮资料**的判定。

结论：要根治这类误注入，得补知识库覆盖或换排序判别力更强的重排模型，
不是在路由层加阈值。闸门开关保留给「库覆盖明确、负例充足」之后再用，
届时先跑 `scripts/calibrate_doc_router.py` 复核。

## 三个开关

| 环境变量 | 默认 | 作用 |
|---|---|---|
| `RAG_DOC_ROUTER` | `1` | 总开关；关掉 = 完全回到全库竞争 |
| `RAG_ROUTER_TOP_DOCS` | 3 | 路由保留几篇文档 |
| `RAG_ROUTER_BM25_TOPK` | 3 | 每篇参与聚合的最高分切片数（消除长度偏置） |
| `RAG_ROUTER_VEC_WEIGHT` | 0.3 | 向量那一路的权重（0 = 纯 BM25 路由） |
| `RAG_ROUTER_WIDE_K` | 30 | 向量宽召回条数（只给 RRF 提供名次） |
| `RAG_ROUTER_GATE` | `off` | 域外拒答下限（查询词覆盖率），见 `gate_threshold()` |
"""

from __future__ import annotations

import logging
import math
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


def _env_flag(name: str, default: str) -> bool:
    return os.getenv(name, default).strip() == "1"


def _env_float_or_none(name: str, default: float | None) -> float | None:
    """off/none/空 → None（关闭该项判定）；非法值回退默认并告警。"""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    if raw.lower() in ("off", "none"):
        return None
    try:
        return float(raw)
    except ValueError:
        logger.warning("%s=%r 不是数字，回退默认 %s", name, default, default)
        return default


def router_enabled() -> bool:
    """文档路由总开关。

    默认开：库已是 20 篇 / 248 片，过了 §4.3 的触发条件（>15 篇或 >150 片）。
    小库自动不生效——见 `_MIN_DOCS`，那种规模下收窄范围只增加失败面。
    """
    return _env_flag("RAG_DOC_ROUTER", "1")


def top_docs() -> int:
    return int(os.getenv("RAG_ROUTER_TOP_DOCS", "3"))


def wide_k() -> int:
    """路由用的向量宽召回条数。

    只给「vec-rrf」这一路提供名次，条数太少会让长文档天然吃亏。
    30 条 × 20 篇仍是每篇 1.5 条，够用且只要几毫秒。
    """
    return int(os.getenv("RAG_ROUTER_WIDE_K", "30"))


def bm25_top_chunks() -> int:
    """每篇文档参与聚合的 BM25 最高分切片数（消除文档长度偏置）。"""
    return int(os.getenv("RAG_ROUTER_BM25_TOPK", "3"))


def vec_weight() -> float:
    """向量那一路在文档聚合分里的权重（0 = 纯 BM25 路由）。默认 0.3。

    向量 RRF 是语义兜底：BM25 靠字面命中，「快速排序」与「快排」这种
    同义改写在字面上对不上，只能靠向量救。但两路各自按本查询最大值归一后相加，
    权重相等时向量会把 BM25 已经排对的文档挤下去。71 条题库实测 route@3：

        weight=0（纯 BM25）  69/70   ← 有一例 BM25 全零，路由直接放弃
        weight=0.3           71/71   ← 取这一档
        weight=0.5           70/71
        weight=1.0           69/71

    所以向量在这里的定位是「没有字面命中时的兜底 + 平票时的打破」，不是
    与 BM25 平权。调大这个值会明显伤害路由准确率。
    """
    return float(os.getenv("RAG_ROUTER_VEC_WEIGHT", "0.3"))


def gate_threshold() -> float | None:
    """域外拒答阈值：**查询词在文档正文里的覆盖率**；None = 不做域外判定。

    为什么不用聚合分数做闸门：`bm25-sum` 与 `vec-rrf` 都按本查询的最大值归一，
    得到的只是**相对**排序分，天然落在 0~1 且最高分恒为 1，没有绝对含义，
    拿它跟固定阈值比是错的。覆盖率是绝对量：查询里有几个实词真的出现在
    这篇文档里。

    默认 **off**。理由：题库里库外负例只有 4 条，不足以定一条会**静默丢掉
    整轮资料**的阈值。打开前请先跑
    `scripts/calibrate_doc_router.py --gate`，确认库内题的覆盖率分布与库外题
    不重叠，再取保守值。
    """
    return _env_float_or_none("RAG_ROUTER_GATE", None)


# 少于这么多篇文档时路由无意义：收窄到 3 篇等于几乎不收窄，
# 却凭空多出「正确文档没被路由到」的失败面。
_MIN_DOCS = 4


@dataclass
class DocRoute:
    """一次路由的结论。

    Attributes:
        sources: 命中的文档（按得分降序）；`applied=False` 时为空列表。
        scores: 与 sources 对齐的**相对**聚合分（已按本查询最大值归一）。
        coverage: 与 sources 对齐的查询词覆盖率（绝对量，0~1）。
        applied: 是否真的收窄了检索范围。False = 走全库，与旧行为完全一致。
        reason: ok / off / too_few_docs / no_signal / gate
        gate_score: 触发域外判定时的最高覆盖率，便于日志与 trace 复盘。
    """

    sources: list[str] = field(default_factory=list)
    scores: list[float] = field(default_factory=list)
    coverage: list[float] = field(default_factory=list)
    applied: bool = False
    reason: str = "off"
    gate_score: float | None = None


def _doc_scores(
    store, query: str, tokenize, bm25_get, need_coverage: bool = False
) -> tuple[dict[str, float], dict[str, float]]:
    """按文档聚合切片证据，返回 (归一化聚合分, 查询词覆盖率)。

    bm25_get(query) -> (corpus_docs, scores) 由调用方注入：BM25 语料与分词器都
    长在 retriever 里，而 retriever 又要 import 本模块，模块级互相 import 会成环。
    依赖注入比函数内 import 更显式，也让这里能脱开 retriever 单独测。

    need_coverage 默认 False：覆盖率是纯字符串扫描（整库正文拼一遍），
    只给域外闸门用。闸门默认关着，不该为此每轮白扫 5 万字符。
    """
    sources_of = lambda d: str((d.metadata or {}).get("source") or "")

    corpus_docs, scores = bm25_get(query)

    # ---- ① BM25：只取每篇最高的 N 片求和 ----
    # 不能把一篇文档所有命中片的分全加起来：切片数差异极大（9 片 vs 27 片），
    # 长文档天然有更多命中片，求和后系统性占优。实测全量求和会让
    # `http-https.md`(27 片) 把真正的目标文档挤出 top3，route@3 从 71/71 掉到 69/71。
    # 「一篇文档有几处强命中」比「一篇文档有多少处弱命中」更接近相关性，
    # 所以按篇取 top-N 再求和，等价于对文档长度做了归一。
    per_doc_scores: dict[str, list[float]] = defaultdict(list)
    for doc, score in zip(corpus_docs, scores):
        if score > 0:
            src = sources_of(doc)
            if src:
                per_doc_scores[src].append(float(score))
    keep = bm25_top_chunks()
    bm25_agg: dict[str, float] = {
        src: sum(sorted(v, reverse=True)[:keep]) for src, v in per_doc_scores.items()
    }

    # ---- ② 向量：宽召回后按文档做 RRF ----
    # 权重为 0 时整段跳过：宽召回是这里唯一一次真正的向量查询，没必要白跑。
    weight = vec_weight()
    vec_agg: dict[str, float] = defaultdict(float)
    if weight > 0:
        try:
            wide_docs = store.similarity_search(query, k=wide_k())
        except Exception as exc:
            logger.warning(
                "路由的向量宽召回失败，本轮回退为纯 BM25 路由 exc=%s: %s",
                type(exc).__name__,
                exc,
            )
            wide_docs = []
        for rank, doc in enumerate(wide_docs):
            src = sources_of(doc)
            if src:
                vec_agg[src] += 1.0 / (60 + rank + 1)

    def _normalize(table: dict[str, float]) -> dict[str, float]:
        peak = max(table.values()) if table else 0.0
        return {k: v / peak for k, v in table.items()} if peak > 0 else {}

    bm25_norm, vec_norm = _normalize(bm25_agg), _normalize(vec_agg)
    combined: dict[str, float] = defaultdict(float)
    for k, v in bm25_norm.items():
        combined[k] += v
    for k, v in vec_norm.items():
        combined[k] += weight * v

    # ---- ③ 覆盖率（绝对量，只给闸门用）----
    # 必须是**IDF 加权**的命中率，不能直接用词数占比：中文问句里
    # 「什么 / 怎么 / 区别 / 有哪些」这类泛词几乎每篇文档都有，会把所有文档的
    # 覆盖率一起抬高——实测未加权时库内最低 0.50、而「Python 列表和元组」
    # 「git 回退」这类库外题也能拿到 0.60，两条分布完全重叠，任何阈值都要
    # 误杀 20/71 条库内题。
    # IDF 用文档频次现算（20 篇文档、每篇拼一次正文），不需要维护停用词表，
    # 也不用对文档正文分词——只对查询词做子串统计，几毫秒。
    coverage: dict[str, float] = {}
    if need_coverage:
        tokens = [t for t in tokenize(query) if len(t) >= 2]
        text_of: dict[str, str] = defaultdict(str)
        for doc in corpus_docs:
            text_of[sources_of(doc)] += doc.page_content.lower()
        if tokens and text_of:
            n_docs = len(text_of)
            # 出现在 n_docs 篇里的词 → idf ≈ log((n+1)/(n+0.5)) ≈ 0.02，基本不影响
            # 结果；只出现在 1 篇里的词 → idf ≈ 2.6，主导判定。
            weights = {
                t: math.log((n_docs + 1) / (1 + sum(1 for txt in text_of.values() if t in txt)))
                for t in tokens
            }
            total_weight = sum(weights.values())
            if total_weight > 0:
                for src, text in text_of.items():
                    got = sum(w for t, w in weights.items() if t in text)
                    coverage[src] = round(got / total_weight, 4)

    return dict(combined), coverage


def route_documents(store, query: str, trace: dict | None = None, *,
                    tokenize=None, bm25_get=None,
                    force_coverage: bool = False) -> DocRoute:
    """按问题给出一批候选文档；失败一律退回全库（不抛错、不阻断检索）。

    Args:
        store: 主知识库（Chroma）。
        query: 实际检索语句（已按需拼过 topic，由调用方决定）。
        trace: 传可变 dict 时写入 router / router_ms / router_docs /
            router_scores / router_coverage / router_applied / router_gate。
        tokenize: 分词函数，默认从 retriever 惰性取。
        bm25_get: `query -> (语料文档, 分数)`，默认从 retriever 惰性取。
        force_coverage: 闸门关着时也算覆盖率。只给标定脚本用——
            线上路径不该为一个没在用的阈值每轮多扫一遍全库正文。
    """
    if not router_enabled():
        _write_trace(trace, "off")
        return DocRoute(reason="off")

    if store is None:
        _write_trace(trace, "no_store")
        return DocRoute(reason="no_store")

    if tokenize is None or bm25_get is None:
        # 函数内 import：retriever 模块级 import 本模块，反过来只能延后。
        from . import retriever as _r

        tokenize = tokenize or _r._tokenize
        # 注意是包一层绑定 store：bm25_scored_docs 的签名是 (store, query)，
        # 而这里要的是 query -> (语料, 分数)。
        bm25_get = bm25_get or (lambda q: _r.bm25_scored_docs(store, q))

    limit = top_docs()
    started = time.perf_counter()
    threshold = gate_threshold()

    try:
        combined, coverage = _doc_scores(
            store, query, tokenize, bm25_get,
            need_coverage=force_coverage or threshold is not None,
        )
    except Exception as exc:
        logger.warning(
            "文档路由失败，本轮退回全库 exc=%s: %s", type(exc).__name__, exc
        )
        _write_trace(trace, "error")
        return DocRoute(reason="error")

    # 一篇都没打分：可能是全新术语（两路信号都不认），也可能是库为空。
    # 这里绝不判死——退回全库检索，与路由关闭时一致。
    if not combined:
        _write_trace(trace, "no_signal")
        return DocRoute(reason="no_signal")

    # 文档太少时收窄没有意义（收窄到 3 篇等于没收窄），只为「正确文档没被
    # 路由到」白添一个失败面。
    if len(combined) < _MIN_DOCS or limit >= len(combined):
        _write_trace(trace, "too_few_docs")
        return DocRoute(reason="too_few_docs")

    ranked = sorted(combined.items(), key=lambda x: -x[1])[:limit]

    sources = [s for s, _ in ranked]
    scores = [round(float(v), 4) for _, v in ranked]
    cover = [coverage.get(s, 0.0) for s in sources]
    elapsed = round((time.perf_counter() - started) * 1000)

    if trace is not None:
        trace.update(
            router="ok",
            router_ms=elapsed,
            router_docs=list(sources),
            router_scores=list(scores),
            router_coverage=list(cover),
            router_applied=True,
        )

    if threshold is not None:
        if trace is not None:
            trace["router_gate"] = threshold
        # coverage 为空 = 这个查询全是泛词（「有什么区别」这种），IDF 权重和为 0，
        # 覆盖率无从计算。这种情况**不能判库外**——没有证据不等于有反证，
        # 判死会让整轮静默零资料。交给重排的 relevance_floor 继续兜底。
        best_cover = max(cover) if coverage else None
        if best_cover is not None and best_cover < threshold:
            if trace is not None:
                trace["router"] = "gate"
                trace["router_applied"] = False
            logger.info(
                "文档路由查询词覆盖率 %.4f 低于域外下限 %.4f，本轮判定库外问题"
                "（最相近的 %d 篇：%s）",
                best_cover,
                threshold,
                len(sources),
                "、".join(sources),
            )
            return DocRoute(
                sources=[], scores=scores, coverage=cover,
                applied=False, reason="gate", gate_score=best_cover,
            )

    logger.debug(
        "文档路由 top%d=%s（分数 %s 覆盖 %s 耗时 %dms）",
        len(sources), sources, scores, cover, elapsed,
    )
    return DocRoute(
        sources=sources, scores=scores, coverage=cover, applied=True, reason="ok"
    )


def _write_trace(trace: dict | None, reason: str) -> None:
    if trace is None:
        return
    trace.setdefault("router", reason)
    trace.setdefault("router_ms", 0)
    trace.setdefault("router_docs", [])
    trace.setdefault("router_scores", [])
    trace.setdefault("router_coverage", [])
    trace.setdefault("router_applied", False)
