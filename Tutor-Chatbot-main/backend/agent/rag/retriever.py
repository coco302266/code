"""检索层：粗召回（向量 + BM25）→ RRF 融合 → 交叉编码器重排 → 格式化。

改造前只有一步：`similarity_search(query, k)` 直接取前 k 条注入提示词。
实测（D5）的问题不是"召不回来"，而是"排不对"——top3 命中 6/7、top1 只 4/7。
双塔向量擅长捞回相关文档，不擅长把最相关的排最前，所以补了重排这一环。

  链路可用环境变量逐段关掉，便于 A/B 对比与回退：
    RAG_RERANK=0        关重排（回到纯向量 + 混合）
    RAG_HYBRID=0        关 BM25 混合召回
    RAG_COARSE_K=6      粗召回条数
    RAG_FINAL_K=4       最终注入条数
    RAG_RERANK_BATCH=1  重排批大小（本机 CPU 下 1 最快，见下方注释）
    RAG_RERANK_ONLINE=1 允许重排模型联网下载（默认只读本地缓存）

所有参数集中在 RetrievalConfig 里，扩库时只改这一处。
"""

import logging
import os
import re
import time
from dataclasses import dataclass
from functools import lru_cache

# 模型下载环境变量必须早于 fastembed / huggingface_hub 的导入（原因见 _env.py）
from . import _env  # noqa: F401  isort: skip

from langchain_chroma import Chroma
from langchain_core.documents import Document

# 文档路由（L1）。它反向依赖本模块的 `_tokenize` / `bm25_scored_docs`，
# 所以那边用的是函数内 import —— 模块级双向 import 会成环。
from . import doc_router

logger = logging.getLogger(__name__)


# ---------------- 配置 ----------------


def _opt_float(env_name: str, default: float) -> float | None:
    """解析「可关闭的浮点阈值」环境变量。

    off / none → None（该档过滤整体关闭）；未设置 → default；
    非法数字 → 告警并回退 default，不让一个手滑的环境变量把整条链路打挂。
    """
    raw = os.getenv(env_name, "").strip()
    if not raw:
        return default
    if raw.lower() in ("off", "none"):
        return None
    try:
        return float(raw)
    except ValueError:
        logger.warning("%s=%r 不是数字，回退默认 %s", env_name, raw, default)
        return default


@dataclass
class RetrievalConfig:
    """检索链路参数。默认值 = 66 片知识库下的实测最优组合。

    集中在一处，是为了扩库时不用去函数体里找散落的常量
    （设计依据见 docs/proposals/PROPOSALS.md）。
    """

    # 粗召回 6 条：66 片库下 6/12/24 条的 top1 都是 7/7，加大只涨延迟。
    # 扩库到 366 片后 top1 掉到 6/7，但 6/12/24 条**仍然完全一样**——
    # 掉分是重排排序被干扰项压过，不是召不回来。所以不要靠调大这个值救扩库。
    coarse_k: int = 6
    final_k: int = 4
    enable_rerank: bool = True
    enable_hybrid: bool = True
    # 重排按条计费（本机 CPU 实测 0.19~0.25s/条），批大小决定 padding 浪费。
    # 本机无 GPU，batch 越大越慢——实测（取三次最快，候选 6/12/18/30 条一致）：
    #   6 条:  batch=1 0.73s  batch=2 0.90s  batch=4 1.52s
    #   30 条: batch=1 5.78s  batch=4 7.70s  batch=16 8.87s
    # 所以默认取 1。上了 GPU 之后批处理才会转为优势，届时用环境变量调大重测。
    rerank_batch: int = 1
    rerank_model: str = "BAAI/bge-reranker-v2-m3-int8"
    # 默认只读本地缓存：内网/离线环境下联网重试会让首个请求卡十几秒，
    # 而模型已经随仓库放在 .model_cache 里了。换机器没带缓存时设 RAG_RERANK_ONLINE=1。
    rerank_online: bool = False
    # RRF 的平滑常数，取自原论文，60 是通行取值
    rrf_k: int = 60
    # 扩库后是否按库规模自动放大粗召回。默认关：实测 366 片下 6/12/24 条的
    # top1 完全相同，放大只涨延迟。开启仅用于验证性实验或 L1 文档路由上线后的对比。
    coarse_k_auto: bool = False
    # 候选池预算上限：无论库多大，送进重排的条数不超过这个值（重排是线性成本）
    coarse_k_cap: int = 16
    # 相关性下限（D11 修复，2026-10-24）：bge-reranker 是 logits，实测
    # 真相关片约 +0.3 以上、完全不相关 -2.8 / -6.0（B_WORKLOG XB-10 三轮实测）。
    # 低于下限的片**不注入**——库外题以前会被硬塞 4 条不相关资料，
    # 模型半数情况零声明就把它们当依据用了。取 -2.0 是给
    # 「真相关但模型打分偏低」留约 -2.0~+0.3 的灰区；全被过滤时返回空列表，
    # 由 app.add_rag_grounding 的空资料分支显式告知模型"本轮无资料"。
    # 扩库后（19 篇、父子粒度）reranker 对"X 的时间复杂度是多少"这类具体事实问法
    # 即便命中正确文档也只给 -1.4~-1.5，若守 -1.0 会把快排/哈希表这种库内题误杀；
    # 而最近的真负例（git 回退）约 -2.87，故取 -2.0 留 0.87 余量。
    # RAG_RELEVANCE_FLOOR=off 关闭（回退到永远返回 top-k 的旧行为）。
    # 只在重排真正跑过时生效（无重排就没有分数，无从过滤）。
    relevance_floor: float | None = -2.0
    # 逐条下限（2026-10-25）：整题口径只挡「整题都不相关」，挡不住高分之后的
    # 低分尾巴——实测问「TIME_WAIT 为什么要等那么久？」top1 拿 +4.635，
    # 尾部 -5.173 的「SJF 为什么最优 / RR 时间片设多少」照样被注入。
    # 阈值按**切片级金标准**量化选定（scripts/diag_perdoc_floor.py，75 题）：
    #   -4.0 → Recall@4 -3.3pp / Precision@4 +7.2pp（误杀 20 片金标准，不划算）
    #   -5.0 → Recall@4 -1.3pp / Precision@4 +6.2pp（误杀 8 片，性价比拐点）
    #   -6.0 → Recall@4 -0.5pp / Precision@4 +3.9pp（收益太小，救不了上面那例）
    #
    # **2026-10-28 改到 -8.0（O13，父子模式 71 题实测，scripts/ab_recall_pool.py）**：
    #   -5.0  →  Recall 0.7827 / Precision 0.5587 / nDCG 0.8651 / p95 1.075s
    #   -8.0  →  Recall 0.7909 / Precision 0.5634 / nDCG 0.8700 / p95 1.081s
    #   off   →  Recall 0.7909 / Precision 0.5634 / nDCG 0.8653 / p95 1.160s
    # 即 -8.0 已经拿到「关掉下限」的全部收益（Recall +0.82pp），却仍留有兜底，
    # 且**几乎不付延迟**（+0.006s）。代价是平均多注入 0.2 条（-5.0 时 2.76 条
    # → 2.96 条），多出来的约九成是零增益片——但 Precision 的分母恒为 inject_k(3)，
    # 注入零增益片并不拉低 Precision，**这个代价只体现在提示词长度上**。
    # 为什么 -5.0 当年是对的、现在不是：那个性价比是在 **inject_k=4 的扁平模式**
    # 下算的，Precision@4 的分子里"砍掉的垃圾"能直接进分母；父子模式 inject_k=3
    # 后，砍垃圾换来的 Precision 收益归零，只剩误杀的成本。
    # 与 relevance_floor 的关系：先整题判（最高分 < -2.0 全不注入），
    # 再逐条砍（-2.0 与 -5.0 之间的灰区保留，< -5.0 的砍掉）。
    # 注意 top1 恒被保留：整题口径已保证最高分 ≥ -2.0 > -5.0。
    # RAG_PER_DOC_FLOOR=off 关闭（回退到只做整题判定的旧行为）。
    per_doc_floor: float | None = -8.0
    # 父子检索（PROPOSAL_RAG_SCALE §4.4，2026-10-25 opt-in）：child(~200 字) 用于召回，
    # parent(完整 `##` 小节，约 600~900 字) 用于注入。RRF 后按 parent_id 聚合去重
    # 再重排 —— 既实现「小块检索、大块注入」，也顺带缩小了进入重排的候选池
    # （同节多个 child 合成一个 parent），契合「候选池预算与库规模无关」的总原则。
    # 默认**关**：扩库前保持扁平以保 66 片基线可比；扩到 19 篇后已通过
    # RAG_PARENT_CHILD=1 启用（运行配置），待全量评测稳定后考虑改默认开。
    # RAG_PARENT_CHILD=1 启用；库必须是 build_kb.py 父子模式建的。
    parent_child: bool = False
    # parent 更长约 3 倍，final_k 相应从 4 降到 3 控制注入量（§4.4 明确要求）。
    parent_final_k: int = 3
    # 同一篇文档最多占几个注入位（0 = 不限制，等同于 2026-10-26 之前的行为）。
    # 父子 mode 下去重只按 parent_id，同一篇文档的不同 `##` 小节可以占满全部 3 个
    # 位置——实测 6 条库内题里 4 条出现这种情况，见 scripts/diag_floor_margin.py。
    # 默认 0：先保行为不变，A/B 有结论后再决定是否转默认。
    max_per_source: int = 0
    # 重排读哪段文本：parent（聚合后的小节全文 600~900 字）/ child（命中的那片
    # ~200 字）/ child_title（片 + 所属小节标题，默认）。
    # 父子模式下聚合会把正文换成整节，而交叉编码器在本机是无 GPU 的 ONNX，
    # 成本随序列长度增长——实测（2026-10-27）重排占检索端到端 **97.7%**。
    #
    # **默认 parent_head（截断 rerank_head_chars=480）**，2026-10-29 从
    # child_title 切过来。四档在 395 题新集（391 计分）上的实测：
    #
    #   档位             Recall@3  nDCG@3  Prec@3  噪声率  排序损失   rerank p95
    #   child            0.8567   0.8523  0.4744  0.4135   6.16pp     0.74s
    #   child_title(旧)  0.8586   0.8550  0.4761  0.4118   5.97pp     1.20s
    #   parent_head 240  0.8210   0.8237  0.4569  0.4007   9.73pp       —
    #   parent_head 480  0.8736   0.8812  0.4872  0.3909   4.47pp     1.97s  ← 默认
    #   parent（全文）    0.8798   0.8882  0.4902  0.3870   3.85pp     3.54s  ✗ 超门禁
    #
    # parent_head/480 相对 child_title：nDCG +2.62pp、Prec +1.11pp、噪声 −2.09pp
    # 三项配对 bootstrap 均**显著**（95% CI 不跨 0，n=391），Recall +1.49pp 边缘不显著；
    # 代价是 p95 1203→1967ms（1.64×），仍在 2.0× 安全线（2406ms）内。
    #
    # 为什么不是 child_title：金标准按**小节**标注，而 child_title 只让交叉编码器
    # 看到命中的 ~200 字碎片。同一小节前半段讲「资源有序分配法」、后半段讲
    # 「银行家算法」时，query 问后者也看到前者——**输入粒度与标注粒度错配**，
    # 换任何 checkpoint 都救不回来（5 个候选极差仅 1.7pp，全部不显著）。
    # 为什么不是 parent 全文：质量再多 0.7pp，但 p95 3.54s = 2.94× 已破 2.77×
    # 硬门禁；且长序列下 logits 被压缩，多线程环境会出现 ±0.4pp 的排序抖动。
    # 为什么不是 240：截断太短会丢掉命中片本身，比 child_title 还差 3.13pp——
    # 这一档要的是「覆盖答案所在位置」，不是「取小节开头」。
    #
    # 注入给模型的**始终是 parent 整节**（rerank_input 只影响打分输入）。
    # 回退：RAG_RERANK_TEXT=child_title（p95 回落至 ~1.2s，质量掉 2.6pp nDCG）。
    rerank_text: str = "parent_head"
    # 同一篇文档在**进入重排的候选池**里最多占几条（0 = 不限制，等同原行为）。
    # 与 max_per_source 的区别：那个管最终注入（Late-stage，实测救不回来），
    # 这个管候选池（Early-stage）——配额只有放在这里才有替补可让位，见 U9。
    source_quota: int = 0
    # 父子模式下 child 层的**超额召回倍数**（O13，2026-10-28）。
    #
    # 为什么必须有：RRF 的截断发生在 **child 层**，而聚合去重发生在**parent 层**。
    # 同一小节的多个 child 会合并成一个 parent，于是「召回 6 个 child」实际只
    # 剩下 4.21 个 parent（71 题实测），却要注入 3 条——等于把池子里 71% 的东西
    # 都塞进提示词，准确率被池子本身锁死，再好的重排也无米下锅。
    #
    # 修法：child 层按倍数多召回，RRF 也在 child 层按放大后的条数截断，
    # 聚合得到**足够多且互不重复**的 parent，再用 parent_pool_k 把重排成本压回去。
    # 向量检索与 BM25 打分相对重排（秒级）可以忽略，多召回几乎不要钱。
    #
    # 1 = 关闭（等同 2026-10-28 之前的行为）。RAG_CHILD_OVERFETCH 调。
    child_overfetch: int = 1
    # 进入重排的 **parent 候选条数上限**（O13）。重排是按条计费的
    # （本机无 GPU 实测约 0.19s/条），这是父子模式下唯一真正的成本闸门。
    # 0 = 不限制（聚合出多少就重排多少）。RAG_PARENT_POOL_K 调。
    # 扁平模式下本参数无意义（聚合是透传），恒不生效。
    parent_pool_k: int = 0
    # `rerank_text=parent_head` 的截断长度（字符数）。0 = 不截断（等同 parent）。
    rerank_head_chars: int = 480
    # 重排名次与 RRF 名次的融合权重（0 = 纯重排，即 2026-10-28 之前的行为）。
    # 详见 `_rerank_docs` 里的注释：放大候选池后重排挑错会同步变多，靠这个先验压回去。
    # 实测 0.5 / 1.5 都**更差**（Recall 0.778 / 0.757 vs 纯重排 0.788），故默认 0。
    rerank_fusion_w: float = 0.0
    # 相邻小节扩展半径（O13）：把池里每条 parent 的同文档 ±radius 个 `##` 小节
    # 补进候选池。0 = 关闭（等同 2026-10-28 之前的行为）。详见
    # `expand_adjacent_parents` 的注释（每多送一条进重排换来的池上限，
    # 它是盲目扩召回的 1.27 倍）。只在父子模式下有意义。
    adjacent_radius: int = 0

    @classmethod
    def from_env(cls) -> "RetrievalConfig":
        """读环境变量构造配置。

        浮点型开关统一走 _opt_float：off/none/空 → None（关闭该档过滤），
        非法值 → 回退默认并告警，不让一个手滑的环境变量把整条链路打挂。
        """
        raw_floor = os.getenv("RAG_RELEVANCE_FLOOR", "-2.0").strip()
        return cls(
            coarse_k=int(os.getenv("RAG_COARSE_K", "6")),
            final_k=int(os.getenv("RAG_FINAL_K", "4")),
            enable_rerank=os.getenv("RAG_RERANK", "1").strip() != "0",
            enable_hybrid=os.getenv("RAG_HYBRID", "1").strip() != "0",
            rerank_batch=int(os.getenv("RAG_RERANK_BATCH", "1")),
            rerank_model=os.getenv("RAG_RERANK_MODEL", "BAAI/bge-reranker-v2-m3-int8"),
            rerank_online=os.getenv("RAG_RERANK_ONLINE", "0").strip() == "1",
            rrf_k=int(os.getenv("RAG_RRF_K", "60")),
            coarse_k_auto=os.getenv("RAG_COARSE_K_AUTO", "0").strip() == "1",
            coarse_k_cap=int(os.getenv("RAG_COARSE_K_CAP", "16")),
            relevance_floor=None if raw_floor.lower() in ("off", "none", "") else float(raw_floor),
            per_doc_floor=_opt_float("RAG_PER_DOC_FLOOR", -8.0),
            parent_child=os.getenv("RAG_PARENT_CHILD", "0").strip() == "1",
            parent_final_k=int(os.getenv("RAG_PARENT_FINAL_K", "3")),
            max_per_source=int(os.getenv("RAG_MAX_PER_SOURCE", "0")),
            source_quota=int(os.getenv("RAG_SOURCE_QUOTA", "0")),
            rerank_text=os.getenv("RAG_RERANK_TEXT", "parent_head").strip().lower(),
            child_overfetch=int(os.getenv("RAG_CHILD_OVERFETCH", "1")),
            parent_pool_k=int(os.getenv("RAG_PARENT_POOL_K", "0")),
            rerank_head_chars=int(os.getenv("RAG_RERANK_HEAD_CHARS", "480")),
            rerank_fusion_w=float(os.getenv("RAG_RERANK_FUSION_W", "0")),
            adjacent_radius=int(os.getenv("RAG_ADJACENT_RADIUS", "0")),
        )


# 全局唯一实例。smoke_rag 通过替换它的字段来切配置（不要另建副本，
# 否则函数体读到的和测试改的不是同一个对象）。
CONFIG = RetrievalConfig.from_env()

# per-source 配额开着时，粗召回扩大到多少倍（`RAG_SOURCE_QUOTA` > 0 才生效）。
# 实测依据：配额开着但保持 k=6 时，路由收窄后的候选池经常只有 3 条且同源，
# 配额没有替补可让位 —— 83 条里多样性只从 67 修到 66。扩到 2 倍才看得到第二篇文档。
# 重排成本不受影响：选完会截回 `coarse_k`，只有向量检索与 BM25 打分多一点，
# 这俩相对重排（秒级）可以忽略。
SOURCE_QUOTA_RECALL_MULT = 2


def coarse_k_for(n_chunks: int) -> int:
    """按库规模给出粗召回条数。

    默认恒等返回 CONFIG.coarse_k（6）——这是实测结论，不是偷懒：
    模拟扩库到 366 片后，k 取 6 / 12 / 24 的 top1 完全相同，延迟却从 2.0s
    涨到 6.8s。掉分原因是重排把干扰排在了前面，不是正确切片没被召回。
    所以扩库后要缩小候选池（文档路由 / 父子检索），而不是加大预算。

    RAG_COARSE_K_AUTO=1 时按 sqrt 增长并封顶 coarse_k_cap，
    仅用于验证性实验，不要在默认部署里打开。

    Args:
        n_chunks: 当前 collection 的切片总数；取不到时传 0。
    """
    base = CONFIG.coarse_k
    if not CONFIG.coarse_k_auto or n_chunks <= 0:
        return base
    scaled = round(base * (n_chunks / 66) ** 0.5)
    return max(base, min(CONFIG.coarse_k_cap, scaled))

METADATA_QUERY = re.compile(
    r"\b(title|author|authors|wrote|paper called|abstract|affiliation)\b",
    re.IGNORECASE,
)


# ---------------- 重排 ----------------

@lru_cache(maxsize=1)
def get_reranker():
    """交叉编码器单例。加载失败返回 None，调用方自动退化为不重排。

    与知识库加载同样的取舍：重排是提升项，不是主链路必需项。
    模型文件缺失、onnxruntime 不兼容之类的故障，不应该让 /chat 500。
    """
    if not CONFIG.enable_rerank:
        return None
    try:
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        encoder = TextCrossEncoder(
            model_name=CONFIG.rerank_model,
            cache_dir=os.getenv("FASTEMBED_CACHE_PATH") or None,
            local_files_only=not CONFIG.rerank_online,
        )
        logger.info("重排模型已加载：%s", CONFIG.rerank_model)
        return encoder
    except Exception as exc:
        logger.warning(
            "重排模型加载失败，本次检索退化为纯向量排序 model=%s exc=%s: %s",
            CONFIG.rerank_model,
            type(exc).__name__,
            exc,
        )
        return None


def _mark_degraded(trace: dict | None, component: str) -> None:
    """往 trace 里记一条降级（PROPOSAL Wave 4：降级必须显式化）。

    `degraded` 是旧的逗号串，保持兼容；`degraded_components` 是列表，
    给 eval/slo_check.py 分类统计用——字符串聚合到报告层再拆，拆错一次
    降级就被吞掉一次。
    """
    if trace is None:
        return
    comps = trace.setdefault("degraded_components", [])
    if component not in comps:
        comps.append(component)
    trace["degraded"] = ",".join(comps)


def rerank_input(doc: Document) -> str:
    """重排实际读的文本（纯函数，只看 doc 与 CONFIG.rerank_text）。

    三档取值：
      - `parent`：聚合后的小节全文（600~900 字），最准也最慢；
      - `child`：命中的那片（~200 字），最快，但丢掉了「这段属于哪个主题」；
      - `child_title`：**片 + 所属小节标题**（`parent_title`）。标题是整节里
        信息密度最高的几十字，拼上它只需多付 ~10% 序列长度，就能把 child 丢掉的
        主题信号补回来。2026-10-27 为拿回 `child` 的排序损失而加。
      - `parent_head`：小节全文**截断到 `rerank_head_chars` 字**（默认 480，O13）。
        介于 child_title(~250 字, 0.19s/条) 与 parent(~750 字, 0.52s/条) 之间。
        加它的理由是一个真实的判别力缺口：金标准按**小节**标注，而 child_title
        只让交叉编码器看到小节里 200 字的碎片，于是「死锁的必要条件」和
        「死锁的预防」两节在它眼里几乎一样——L1 实测重排挑错损失 5.1pp。
        小节开头通常就是定义/结论，信息密度最高，截断前 N 字能拿到大部分判别力，
        成本只有全文的三分之一左右。

    无论哪一档，**注入给模型的始终是 parent 整节**——「小块打分、大块注入」
    两件事分开，互不干扰；父子的正文替换发生在聚合阶段，这里只决定喂给
    交叉编码器的是什么。

    拿不到 child_text（单层库、或未走聚合）时退回正文，行为等同 parent。
    """
    mode = (CONFIG.rerank_text or "").strip().lower()
    if mode in ("child", "child_title"):
        text = (doc.metadata or {}).get("child_text")
        if text:
            if mode == "child_title":
                title = (doc.metadata or {}).get("parent_title")
                if title and not _starts_with_heading(text, title):
                    # 合并过的 child 可能自带标题行（`## 预防`），避免重复拼一次
                    # 把它也算进序列长度——那正是本档想省下的开销。
                    return "%s\n%s" % (title, text)
            return text
    if mode == "parent_head":
        text = doc.page_content or ""
        limit = CONFIG.rerank_head_chars
        if limit > 0 and len(text) > limit:
            text = text[:limit]
        return text
    return doc.page_content


def _starts_with_heading(text: str, title: str) -> bool:
    """文本首行是否已就是这个标题（忽略 Markdown 的 `#` 前缀与空白）。

    纯函数。切分器的 `###` 优先分隔会把 `## 小节标题` 单独成块，随后又被
    `_merge_small_children` 并进相邻的片，所以 child 首行常自带标题——
    拼接前必须先认出来，否则白付一份序列长度。
    """
    stripped = (text or "").lstrip()
    if not stripped:
        return False
    first = stripped.splitlines()[0]
    return first.lstrip("#").strip() == str(title).strip()


def _rerank_docs(
    query: str,
    docs: list[Document],
    top_n: int,
    trace: dict | None = None,
) -> list[Document]:
    """按交叉编码器打分重排，取前 top_n。

    fastembed 的 rerank() 返回与入参一一对应的分数（不是排好序的列表），
    bge-reranker 输出 logits，越大越相关——所以排序由这里自己做。

    trace 非空时会把「重排走了哪条分支 / 耗时」写进去，供调用方打日志。
    """
    # 候选不超过注入数时通常可以不跑重排（没有分数也就没有排序收益）——
    # **但只有在两级 floor 都关时才允许跳**。父子检索（RAG_PARENT_CHILD）
    # 下 RRF 后按 parent_id 聚合，常把 6 个 child 收成 3 个 parent，正好等于
    # parent_final_k(=3)；一旦据此跳过重排就没有分数，relevance_floor 无从判定，
    # 「如何申请护照？」这类库外题会被硬塞 3 段死锁/HTTPS 的 parent——
    # 正是 D11 修复要挡的「库外题硬塞资料」。所以 floor 开着就必须算分
    # （3 条 parent 的重排约 0.6s，换来库外题不硬塞）。
    floors_on = CONFIG.relevance_floor is not None or CONFIG.per_doc_floor is not None
    if len(docs) <= top_n and not floors_on:
        if trace is not None:
            trace["rerank"] = "skipped"
        return docs[:top_n]

    encoder = get_reranker()
    if encoder is None:
        if trace is not None:
            trace["rerank"] = "unavailable"
            _mark_degraded(trace, "rerank_unavailable")
        return docs[:top_n]

    started = time.perf_counter()
    try:
        scores = list(
            encoder.rerank(
                query,
                [rerank_input(d) for d in docs],
                batch_size=CONFIG.rerank_batch,
            )
        )
    except Exception as exc:
        logger.warning("重排失败，退回原顺序 exc=%s: %s", type(exc).__name__, exc)
        if trace is not None:
            trace["rerank"] = "error"
            _mark_degraded(trace, "rerank_error")
        return docs[:top_n]

    if trace is not None:
        trace["rerank"] = "ok"
        trace["rerank_ms"] = round((time.perf_counter() - started) * 1000)

    if len(scores) != len(docs):
        logger.warning(
            "重排返回分数数与文档数不一致（%d vs %d），退回原顺序",
            len(scores),
            len(docs),
        )
        if trace is not None:
            trace["rerank"] = "count_mismatch"
            _mark_degraded(trace, "rerank_count_mismatch")
        return docs[:top_n]

    order = sorted(range(len(docs)), key=lambda i: scores[i], reverse=True)
    logger.debug(
        "重排 %d -> %d，最高分 %.3f 最低分 %.3f",
        len(docs),
        top_n,
        scores[order[0]],
        scores[order[-1]],
    )

    # ---- 召回先验融合（O13，2026-10-28）----
    # 交叉编码器只看 250 字的 child_title，而 RRF 的名次是「两路召回一致同意」
    # 的信号。实测两者互补：候选池从 4.2 放大到 9.2 条后，池的召回上限涨 6.9pp，
    # 但重排挑错也涨了 6.7pp，净收益≈0 —— 单独靠重排吃不下更大的池。
    # 把 RRF 名次按权重并进来，等于给重排一个「别信自己太过」的先验。
    #
    # 两条分数都先做**组内归一**再相加：rerank 是 logits（-10~+10，绝对量纲无意义），
    # RRF 是 1/(60+rank)（0~0.016），不归一直接加权等于让量纲大的那一路独裁。
    # w=0 关闭（等同 2026-10-28 之前的行为）。RAG_RERANK_FUSION_W 调。
    w = CONFIG.rerank_fusion_w
    if w and len(docs) > 1:
        lo, hi = min(scores), max(scores)
        span = (hi - lo) or 1.0
        k = CONFIG.rrf_k
        # docs 的顺序就是 RRF 名次（候选池一路传下来没重排过）
        rrf = [1.0 / (k + i + 1) for i in range(len(docs))]
        r_lo, r_hi = min(rrf), max(rrf)
        r_span = (r_hi - r_lo) or 1.0
        blended = [
            (scores[i] - lo) / span + w * (rrf[i] - r_lo) / r_span for i in range(len(docs))
        ]
        order = sorted(range(len(docs)), key=lambda i: blended[i], reverse=True)
        if trace is not None:
            trace["rerank_fusion_w"] = w

    # 相关性下限（D11 修复）：**整题判定**，不是逐片裁剪。
    # 逐片裁剪实测会误伤 follow-up 场景（「它和死锁有关系吗？」拼上 topic 后，
    # 正确主题的片分数也会 < -1，滤掉后 71 题里退化 1 条）。
    # 整题口径：最高分都低于下限 → 这题整体不相关，全部不注入（返回空列表，
    # 调用方走「显式无资料」分支）；最高分达标 → 保留 top-k 原样，低分尾巴
    # 交给模型的 grounding 规则处理（L3 实测幻觉率 0.09 达标，不因小失大）。
    # trace 记录判定结果，排查日志与 slo_check 都能看到。
    floor = CONFIG.relevance_floor
    if floor is not None:
        top_score = scores[order[0]]
        if trace is not None:
            trace["relevance_floor"] = floor
            trace["relevance_top_score"] = round(float(top_score), 3)
        if top_score < floor:
            if trace is not None:
                trace["relevance_filtered"] = len(docs)
            logger.info(
                "重排最高分 %.3f 低于相关性下限 %.1f，本轮 %d 条全部不注入",
                top_score,
                floor,
                len(docs),
            )
            return []

    picked = order[:top_n]

    # 逐条下限：整题判定放行后，再砍掉尾部明显不相关的片。
    # 与上面的整题口径是两件事，别合并——整题口径保护「库外题不注入」，
    # 这里保护「库内题不被塞别的主题的片」。阈值选 -5.0 的依据见
    # CONFIG.per_doc_floor 的注释（切片级金标准量化），不是拍脑袋。
    # top1 恒保留：整题口径已保证最高分 ≥ relevance_floor(-1.0) > -5.0。
    per_doc = CONFIG.per_doc_floor
    if per_doc is not None:
        before = len(picked)
        picked = [i for i in picked if scores[i] >= per_doc]
        if trace is not None:
            trace["per_doc_floor"] = per_doc
            trace["per_doc_filtered"] = before - len(picked)
            trace["per_doc_scores"] = [round(float(scores[i]), 3) for i in order[:top_n]]
        if before != len(picked):
            logger.info(
                "逐条下限 %.1f 砍掉 %d 条（分数 %s）",
                per_doc,
                before - len(picked),
                [round(float(scores[i]), 3) for i in order[:top_n]],
            )

    return [docs[i] for i in picked]


# ---------------- 相邻小节扩展（O13） ----------------

_PARENT_INDEX_CACHE: dict[str, dict[tuple[str, int], Document]] = {}


def _get_parent_index(vectorstore: Chroma) -> dict[tuple[str, int], Document]:
    """库里所有 parent 小节按 (source, 序号) 建索引，供相邻扩展查邻居。

    与 `_BM25_CACHE` 同一套约定：按 collection 名缓存，热重载必须连它一起清
    （见 `clear_bm25_cache` 的注释——两份独立状态不同步是最难查的那类问题）。
    库只有 81 个小节，全量拉一次几毫秒，缓存后是纯内存查表。
    """
    name = getattr(getattr(vectorstore, "_collection", None), "name", "") or "default"
    if name in _PARENT_INDEX_CACHE:
        return _PARENT_INDEX_CACHE[name]

    index: dict[tuple[str, int], Document] = {}
    try:
        raw = vectorstore.get(include=["documents", "metadatas"])
        for text, meta in zip(raw.get("documents") or [], raw.get("metadatas") or []):
            pid = str((meta or {}).get("parent_id") or "")
            if "#" not in pid:
                continue
            source, _, num = pid.rpartition("#")
            try:
                n = int(num)
            except ValueError:
                continue
            if (source, n) in index:
                continue  # 同一小节的多个 child，留第一个即可
            # `child_text` 必须带上：`rerank_input` 在 child_title 档读的是它，
            # 拿不到就退回 doc.page_content（= 整节 750 字）。曾漏了这个字段，
            # 结果邻居被按「整节」打分、池里原有的按「片+标题」打分——
            # 同一批候选两种输入，分数不可比，且重排耗时翻倍（p95 3.48s 超门禁）。
            # 与 aggregate_parent_docs 的产出保持同构，才不会一半候选走另一档。
            index[(source, n)] = Document(
                page_content=(meta or {}).get("parent_text") or text or "",
                metadata={
                    **meta,
                    "start_index": (meta or {}).get("parent_start"),
                    "chunk_kind": "parent",
                    "child_text": text or "",
                },
            )
    except Exception as exc:
        logger.warning("parent 索引构建失败，跳过相邻扩展 exc=%s: %s", type(exc).__name__, exc)
        index = {}
    _PARENT_INDEX_CACHE[name] = index
    return index


def clear_parent_index_cache() -> int:
    """清空 parent 索引缓存，返回清掉的条目数（热重载知识库时必须调）。"""
    cleared = len(_PARENT_INDEX_CACHE)
    _PARENT_INDEX_CACHE.clear()
    return cleared


def expand_adjacent_parents(
    docs: list[Document],
    index: dict[tuple[str, int], Document],
    radius: int,
) -> list[Document]:
    """把池里每条 parent 的**同文档相邻小节**（序号 ±radius）补进候选池。

    为什么比"盲目多召回"划算（O13 实测，71 题）：
        基线池        4.21 条 → 池召回上限 0.8336
        相邻 ±1       7.37 条 → 池召回上限 0.8884   (+3.16 条换 +5.5pp)
        盲目扩召回3倍  9.24 条 → 池召回上限 0.9021   (+5.03 条换 +6.9pp)
    每多送一条进重排换来的上限，相邻扩展是盲目扩召回的 1.27 倍——
    八股资料里「必要条件 / 预防 / 避免」这类相关小节本来就是挨着写的，
    定向扩展的命中率天然高于把全库按相似度再往下捞一截。

    保持原顺序在前、邻居追加在后：重排会重新排序，但顺序稳定便于 trace 与单测。
    radius<=0 或索引为空时原样返回（等价于关闭）。纯函数，便于单测。
    """
    if radius <= 0 or not index or not docs:
        return list(docs)

    seen = {_key(d) for d in docs}
    out = list(docs)
    for doc in docs:
        pid = str((doc.metadata or {}).get("parent_id") or "")
        if "#" not in pid:
            continue
        source, _, num = pid.rpartition("#")
        try:
            base_n = int(num)
        except ValueError:
            continue
        for delta in range(1, radius + 1):
            for n in (base_n - delta, base_n + delta):
                neighbour = index.get((source, n))
                if neighbour is None:
                    continue
                key = _key(neighbour)
                if key in seen:
                    continue
                seen.add(key)
                out.append(neighbour)
    return out


def _key(doc: Document) -> tuple:
    """切片身份 (source, start_index)。

    抽出来是为了让「去重」和「金标准比对」用同一把尺子——
    eval/metrics.py 里那个是私有的，这里不跨模块引用它。
    """
    meta = doc.metadata or {}
    try:
        start = int(meta.get("start_index"))
    except (TypeError, ValueError):
        start = -1
    return (str(meta.get("source") or ""), start)


# ---------------- 混合召回（BM25） ----------------

_PUNCT_ONLY = re.compile(r"^[\s\W_]+$", re.UNICODE)


def _tokenize(text: str) -> list[str]:
    """中文必须分词，否则 BM25 会把整句话当成一个 token，等于没检索。

    rank_bm25 只按空白切词，对中文完全失效；jieba 是这里的最小依赖。

    **大小写必须归一（2026-10-25 修）**：文档正文写 `HTTPS`，而评估节点抽出的
    topic 常是小写口语（`https和http的区别`）。不归一的话这条查询在 BM25 侧
    一个 token 都匹配不上，等于这一路检索直接失效——实测「性能开销体现在哪？」
    这类省略式追问因此整题被相关性下限过滤（sources=0，模型只能靠自有知识答）。
    建库语料与查询两侧都走这个函数，归一后天然同尺。
    """
    import jieba

    return [t.lower() for t in jieba.lcut(text) if not _PUNCT_ONLY.match(t)]


# collection 名 -> (BM25Okapi, 文档列表)；用模块级 dict 而不是 lru_cache，
# 因为 Chroma 对象不可哈希。语料只在首次检索时建一次。
_BM25_CACHE: dict[str, tuple] = {}


def _get_bm25_store(vectorstore: Chroma):
    name = getattr(getattr(vectorstore, "_collection", None), "name", "") or "default"
    if name in _BM25_CACHE:
        return _BM25_CACHE[name]

    try:
        raw = vectorstore.get()
        docs = [
            Document(page_content=text or "", metadata=meta or {})
            for text, meta in zip(
                raw.get("documents") or [], raw.get("metadatas") or []
            )
        ]
        if not docs:
            _BM25_CACHE[name] = (None, [])
            return _BM25_CACHE[name]

        from rank_bm25 import BM25Okapi

        corpus = [_tokenize(d.page_content) for d in docs]
        _BM25_CACHE[name] = (BM25Okapi(corpus), docs)
        logger.info("BM25 语料已建：collection=%s 文档数=%d", name, len(docs))
    except Exception as exc:
        logger.warning(
            "BM25 语料构建失败，跳过混合召回 exc=%s: %s", type(exc).__name__, exc
        )
        _BM25_CACHE[name] = (None, [])

    return _BM25_CACHE[name]


def clear_bm25_cache() -> int:
    """清空 BM25 语料缓存，返回清掉的条目数（热重载知识库时必须调）。

    **为什么必须连它一起清**：`_BM25_CACHE` 存的是「某个 collection 名下的
    语料快照 + BM25 索引」，与向量库句柄是两份独立状态。热重载只换
    Chroma 句柄、不清这里的话，混合召回会拿**旧语料**的分词统计去匹配
    **新库**的切片——向量侧是新的、BM25 侧是旧的，融合结果不可信，
    而且不报错、不告警，是最难查的那类问题。
    """
    cleared = len(_BM25_CACHE)
    _BM25_CACHE.clear()
    # 文档路由本身不缓存任何状态：它的输入就是这里的 BM25 语料快照 + 每次现算的
    # 向量宽召回，清了 BM25 语料，路由自然跟着新库走。（初版设计里路由有自己
    # 的摘要向量缓存，需要在这里一并清；改成证据聚合后那个缓存不存在了，别再
    # 照着旧注释加回来。）
    if cleared:
        logger.info("BM25 语料缓存已清空：%d 个 collection", cleared)
    return cleared


def _bm25_search(
    vectorstore: Chroma,
    query: str,
    k: int,
    allowed_sources: set[str] | frozenset[str] | None = None,
) -> list[Document]:
    """BM25 检索；allowed_sources 非空时只在指定文档内检索。

    **收窄范围必须同步传给 BM25**（文档路由要求）：向量侧加了
    `filter={"source": {"$in": [...]}}` 而 BM25 仍然全库扫的话，混合召回会把
    路由刚排除掉的文档的切片原样塞回候选池，路由等于白做——而且这条路很隐蔽，
    因为结果看起来"还能用"，只是干扰项又回来了。
    """
    bm25, docs = _get_bm25_store(vectorstore)
    if bm25 is None or not docs:
        return []

    tokens = _tokenize(query)
    if not tokens:
        return []

    try:
        scores = bm25.get_scores(tokens)
    except Exception as exc:
        logger.warning("BM25 打分失败 exc=%s: %s", type(exc).__name__, exc)
        return []

    indices = range(len(docs))
    if allowed_sources:
        indices = [
            i
            for i in indices
            if str((docs[i].metadata or {}).get("source") or "") in allowed_sources
        ]
    order = sorted(indices, key=lambda i: scores[i], reverse=True)[:k]
    # 分数为 0 表示一个词都没命中，塞进来只会引入噪音
    return [docs[i] for i in order if scores[i] > 0]


def bm25_scored_docs(
    vectorstore: Chroma, query: str
) -> tuple[list[Document], list[float]]:
    """整库 BM25 打分，返回 (语料文档, 与之一一对应的分数)。

    给文档路由用：路由要把「切片得分」按 `source` 聚合成「文档得分」，
    而 `_bm25_search` 只返回文档、丢了分数。语料是复用的同一份缓存，
    所以这里除了 `get_scores` 的 O(库大小) 计算外没有额外成本。
    """
    bm25, docs = _get_bm25_store(vectorstore)
    if bm25 is None or not docs:
        return [], []
    tokens = _tokenize(query)
    if not tokens:
        return [], []
    try:
        return docs, [float(s) for s in bm25.get_scores(tokens)]
    except Exception as exc:
        logger.warning("BM25 全库打分失败 exc=%s: %s", type(exc).__name__, exc)
        return [], []


# ---------------- 融合 ----------------

def _rrf_fuse(rank_lists: list[list[Document]], limit: int = 0) -> list[Document]:
    """Reciprocal Rank Fusion：只用名次、不用原始分数。

    向量距离和 BM25 分数量纲完全不同，直接加权是错的；RRF 按名次倒数求和，
    不需要调权重，是混合检索的通行做法。

    limit 必须给：两路各 coarse_k 条，合并后最多 2×coarse_k 条，而重排
    是按条计费的（约 0.19~0.25s/条），不截断会让重排的输入翻倍。
    """
    agg: dict[str, list] = {}
    k = CONFIG.rrf_k
    for docs in rank_lists:
        for rank, doc in enumerate(docs):
            key = doc.page_content
            if key in agg:
                agg[key][0] += 1.0 / (k + rank + 1)
            else:
                agg[key] = [1.0 / (k + rank + 1), doc]

    fused = [entry[1] for entry in sorted(agg.values(), key=lambda x: x[0], reverse=True)]
    return fused[:limit] if limit else fused



def _should_augment_with_topic(query: str, topic: str) -> bool:
    """判断这轮要不要把 topic 拼进检索语句。

    实测两种相反的效果（本机跑过）：
    - 省略式追问：`它有什么缺点？` 不带 topic 时召回全错（命中进程/调度/死锁），
      带上 `页面置换lru` 后 3/4 命中内存管理。这类追问本身就是残缺的，必须补。
    - 关键词充足的直问：`HTTPS 和 HTTP 有什么不同？` 再拼 `https与http的区别`，
      等于把同一段语义说两遍，向量被稀释，top1 从 http-https 掉到 tcp-vs-udp。

    所以判据是「这条消息自己够不够用」，而不是「有没有 topic」：
    topic 的核心词只要已经在消息里出现，说明消息自足，拼上去只有坏处。
    """
    if not topic:
        return False

    topic_tokens = [t for t in _tokenize(topic) if len(t) >= 2]
    if not topic_tokens:
        return False

    lowered = query.lower()
    return not any(t.lower() in lowered for t in topic_tokens)


# ---------------- 对外接口 ----------------

def cap_per_source(docs: list[Document], cap: int) -> list[Document]:
    """限制同一篇文档（`source`）最多占 cap 个注入位，保持原有顺序。

    父子模式下 `aggregate_parent_docs` 只按 `parent_id` 去重，而一篇文档底下有
    好几个 `##` 小节 → 实测 6 条库内题里 4 条出现「3 个注入位全来自同一篇」。
    代价是：花了 3 份 token，学生只看到 1 个资料来源的信息广度。

    **超额的那些不丢，排到末尾兜底**：候选池多样性不足时（比如全网只有这篇文档
    答得上），仍然要把名额填满——宁可复用同文档的第 2 小节，也不能少给资料。
    调用方截 `[:top_k]` 时自然先吃满 diverse 的部分。

    cap<=0 时原样返回（等价于关闭限额）。纯函数、无副作用，便于单测。
    """
    if cap <= 0:
        return list(docs)

    counts: dict[str, int] = {}
    kept: list[Document] = []
    overflow: list[Document] = []
    for doc in docs:
        source = (doc.metadata or {}).get("source", "")
        used = counts.get(source, 0)
        if used < cap:
            counts[source] = used + 1
            kept.append(doc)
        else:
            overflow.append(doc)
    return kept + overflow


def _dedupe(docs: list[Document], key_fields: tuple[str, ...]) -> list[Document]:
    """按 metadata 字段 + 正文去重，保持原有顺序。

    放在检索阶段而不是格式化阶段，是为了让「注入了几片」和「事件层上报几片」
    永远一致——两处各去重一次迟早会算岔。
    """
    seen = set()
    out = []
    for doc in docs:
        key = tuple(doc.metadata.get(f) for f in key_fields) + (doc.page_content,)
        if key not in seen:
            seen.add(key)
            out.append(doc)
    return out


def aggregate_parent_docs(docs: list[Document]) -> list[Document]:
    """把命中的 child 按 parent_id 聚合去重成 parent 文档（§4.4 父子检索）。

    child 建库时内嵌了 parent_text / parent_title / parent_start：
    - 同一 parent 下多个 child 命中只保留一个（信息重复，注入多份纯浪费 token）；
    - 返回文档正文换成小节全文，start_index 换成父小节起点，便于溯源；
    - 没有 parent_id 的片（单层库或两种库混用）原样保留，不做任何改写。

    保持首次出现顺序（此时已是 RRF 融合名次）。重排会重新排序，顺序不影响
    正确性，但稳定顺序便于 trace 与单测断言。
    """
    seen: set[str] = set()
    out: list[Document] = []
    for doc in docs:
        meta = doc.metadata or {}
        parent_id = meta.get("parent_id")
        if not parent_id:
            out.append(doc)
            continue
        if parent_id in seen:
            continue
        seen.add(parent_id)
        out.append(
            Document(
                page_content=meta.get("parent_text") or doc.page_content,
                metadata={
                    **meta,
                    "start_index": meta.get("parent_start", meta.get("start_index")),
                    "chunk_kind": "parent",
                    # 命中那一片 child 的正文（RRF 名次最高的那片）。留着是为了让
                    # 重排可以选「打短文本」——见 CONFIG.rerank_text。
                    # 只留首次命中的那片：同节多片聚合时，名次最前的就是最有代表性的一片。
                    "child_text": doc.page_content,
                },
            )
        )
    return out


def summarize_sources(docs: list[Document], origin: str) -> list[dict]:
    """把检索结果压成可序列化的小字典，供 SSE 的 sources 事件用。

    只带「这轮用了哪些资料」的最小信息，**不带正文**——正文已经在 token 流里了，
    重复传一份只会白白放大响应体积。
    """
    items = []
    for doc in docs:
        meta = doc.metadata or {}
        if origin == "kb":
            items.append(
                {
                    "origin": "kb",
                    "source": meta.get("source"),
                    "topic": meta.get("topic"),
                    "category": meta.get("category"),
                }
            )
        elif origin == "classroom":
            # 课堂资料（XB-23）：除了页码，还要带 doc_id / 文件名——
            # 一个课堂可能传了十几份 PDF，「第 3 页」单独拿出来无法定位是哪一份。
            items.append(
                {
                    "origin": "classroom",
                    "doc_id": meta.get("doc_id"),
                    "filename": meta.get("filename"),
                    "source": meta.get("source"),
                    "page": meta.get("page_number"),
                    "section": meta.get("section"),
                }
            )
        else:
            items.append(
                {
                    "origin": "upload",
                    "page": meta.get("page_number"),
                    "section": meta.get("section"),
                }
            )
    return items


def format_knowledge_docs(docs) -> str:
    """把知识库切片格式化成可注入提示的中文上下文。

    去重不在这里做：retrieve_* 已经去过重了，格式化只管排版，
    这样「注入了几片」和「事件层上报几片」必然一致。
    """
    parts = []
    for doc in docs:
        meta = doc.metadata or {}
        header = (
            f"[知识点：{meta.get('topic', '未标注')}"
            f"｜分类：{meta.get('category', '未标注')}"
            f"｜来源：{meta.get('source', '未标注')}"
        )
        # 父子检索注入的是整节，标出小节名让模型知道这段在文档里的位置。
        # 单层切片没有 parent_title，输出与旧版逐字一致。
        section = meta.get("parent_title")
        if section:
            header += f"｜小节：{section}"
        parts.append(header + "]\n" + doc.page_content)

    return "\n\n---\n\n".join(parts)


def _count_chunks(vectorstore: Chroma) -> int:
    """取 collection 的切片总数；取不到就当 0（coarse_k_for 会据此退回固定值）。"""
    try:
        return int(vectorstore._collection.count())
    except Exception:
        return 0


def _scoped_similarity_search(
    vectorstore: Chroma,
    query: str,
    k: int,
    sources: frozenset[str] | None,
) -> list[Document]:
    """向量召回；sources 非空时限定在这几篇文档内。

    过滤条件写成 `{"source": {"$in": [...]}}` 而不是逐篇过滤：Chroma 的
    `where` 在服务端执行，一次查询就能拿到窄范围内的 top-k，不用把全库拉回来
    再筛（后者会让「缩小候选池」在成本上完全不成立）。
    """
    if not sources:
        return vectorstore.similarity_search(query, k=k)
    try:
        return vectorstore.similarity_search(
            query, k=k, filter={"source": {"$in": sorted(sources)}}
        )
    except Exception as exc:
        # 过滤语法/驱动不支持之类的故障不该让 /chat 500。返回空列表让调用方
        # 走既有的「路由回退全库」分支，行为与路由关闭时一致。
        logger.warning(
            "按文档过滤的向量检索失败，回退全库 exc=%s: %s", type(exc).__name__, exc
        )
        return []


def get_knowledge_context(
    vectorstore: Chroma | None,
    query: str,
    k: int | None = None,
    topic: str = "",
    trace: dict | None = None,
) -> str:
    """常驻八股知识库检索，返回可直接注入提示词的文本。

    需要结构化来源（给事件层用）时改调 retrieve_knowledge_docs，
    不要自己再从这段文本里正则反解——那等于把给模型看的文本当接口。
    """
    return format_knowledge_docs(
        retrieve_knowledge_docs(vectorstore, query, k=k, topic=topic, trace=trace)
    )


def retrieve_knowledge_docs(
    vectorstore: Chroma | None,
    query: str,
    k: int | None = None,
    topic: str = "",
    trace: dict | None = None,
) -> list[Document]:
    """常驻八股知识库检索，返回去重后的 Document 列表。没有库就返回空列表。

    Args:
        query: 学生原话。
        k: 最终注入条数，默认取 CONFIG.final_k。
        topic: 评估节点已抽出的知识点。仅当学生这条消息自身信息不足时
            （典型是「那怎么预防呢？」这类省略式追问）才拼进检索语句，
            消息已经自带主题词时不拼——理由见 _should_augment_with_topic。
        trace: 传一个可变 dict，本轮检索过程会写进去（排查扩库后的质量退化用）：
            query（实际检索语句）/ used_topic / coarse_k / bm25_k /
            fused_k / final_k / rerank（ok|skipped|unavailable|error|
            count_mismatch|off）/ rerank_ms / degraded /
            router（ok|off|gate|no_signal|too_few_docs|error）/ router_docs /
            router_scores / router_coverage / router_applied / router_ms /
            parents_k / source_quota（per-source 配额生效时的配额值）。
            不传则零开销，行为与之前完全一致。

    链路：文档路由（L1，收窄到几篇文档）→ 向量粗召回 → BM25 召回 → RRF 融合
    → 重排取 top_k。每一步都可单独关掉（见模块文档字符串与 doc_router）。
    """
    if trace is not None:
        trace.update(
            query=query,
            used_topic=False,
            coarse_k=0,
            bm25_k=0,
            fused_k=0,
            final_k=0,
            rerank="off",
            rerank_ms=0,
            degraded="",
            degraded_components=[],
            parent_child=CONFIG.parent_child,
            router="off",
            router_docs=[],
            router_applied=False,
            recall_k=0,
            parent_pool_k=CONFIG.parent_pool_k,
        )

    if vectorstore is None:
        _mark_degraded(trace, "no_store")
        return []

    # parent 更长（约 600~900 字），父子模式下注入条数默认降到 3（§4.4）；
    # 调用方显式传 k 时以调用方为准。
    default_k = CONFIG.parent_final_k if CONFIG.parent_child else CONFIG.final_k
    top_k = k or default_k
    # 只在消息自足时才拼 topic（理由见 _should_augment_with_topic）
    use_topic = _should_augment_with_topic(query, topic)
    search_query = f"{topic} {query}".strip() if use_topic else query

    # ---- 文档路由（L1）：先把检索范围收窄到几篇文档 ----
    # 放在粗召回之前，是为了让后面的向量 + BM25 都在**同一个窄范围**里竞争。
    # 代价是一次 BM25 全库打分（语料已缓存，O(库大小) 的纯计算）+ 一次宽向量召回，
    # 相对重排的 0.7~4s 可以忽略。
    route = doc_router.route_documents(
        vectorstore,
        search_query,
        trace,
        tokenize=_tokenize,
        bm25_get=lambda q: bm25_scored_docs(vectorstore, q),
    )
    if route.reason == "gate":
        # 域外拒答：整题都没有一篇文档的查询词覆盖率达到下限。
        # 只在显式配置 RAG_ROUTER_GATE 时才会走到这里（默认关）。
        if trace is not None:
            trace["final_k"] = 0
            trace["router_rejected"] = True
        return []

    scope_sources = frozenset(route.sources) if route.applied else None

    # 粗召回条数由库规模决定。默认恒为 6：扩库后加大没有实测收益，
    # 真正该做的是缩小候选池（见 coarse_k_for 与 docs/proposals/PROPOSALS.md）。
    coarse_k = coarse_k_for(_count_chunks(vectorstore))
    # 开了配额就必须同时**扩大召回**：路由收窄到 3 篇后，两路各 coarse_k 条实测
    # 经常全部落在最相似的同一篇上（`fused_k` 低到 3）。没有替补，配额无从让位，
    # 这正是「只挪配额不扩召回」无效的原因。多召回的这些不影响重排成本——
    # 选完就截回 coarse_k。
    recall_k = coarse_k * SOURCE_QUOTA_RECALL_MULT if CONFIG.source_quota else coarse_k
    # O13：父子模式下再乘 child_overfetch。截断发生在 child 层、去重发生在
    # parent 层，同节多片会塌成一个 parent，不超额召回就没有足够的不重复 parent。
    # 只在父子模式且倍数 >1 时生效，扁平模式行为完全不变。
    if CONFIG.parent_child and CONFIG.child_overfetch > 1:
        recall_k = coarse_k * CONFIG.child_overfetch
    candidates = _scoped_similarity_search(
        vectorstore, search_query, recall_k, scope_sources
    )

    if scope_sources and not candidates:
        # 路由命中的文档在库里没有可检索切片（切片被删、索引重建到一半等）。
        # 退回全库，与路由关闭时的行为完全一致——宁可多几条干扰，不能零资料。
        _mark_degraded(trace, "router_fallback")
        if trace is not None:
            trace["router_applied"] = False
        scope_sources = None
        # 回退全库时用同样的口径（配额开着就多召回），否则两条路径的候选预算不一致。
        candidates = vectorstore.similarity_search(search_query, k=recall_k)

    sparse: list[Document] = []
    fused = False
    if CONFIG.enable_hybrid:
        # 这里的 scope 必须跟向量侧一致，否则 BM25 会把路由刚排除的文档塞回来
        # （详见 _bm25_search 的注释）。
        sparse = _bm25_search(
            vectorstore, search_query, recall_k, allowed_sources=scope_sources
        )
        if sparse:
            fused = True
            # 开了 per-source 配额就先多留候选：配额要「让位」，前提是得有替补可选，
            # 替补来自 BM25 与向量的分歧处（第二篇文档往往只在一路里排得靠前）。
            # **重排的成本不变**——候选在下面会被截回 coarse_k，重排仍只打那么多条。
            # O13：父子模式下在 child 层按放大后的条数截断，让 parent 层聚合后
            # 仍有 coarse_k 量级的不重复候选（原来在 child 层就截到 coarse_k，
            # 聚合后只剩 4.21 条，重排无米下锅）。真正压成本的是下面的
            # parent_pool_k——截断放在 parent 层才不会误伤同节多片。
            fuse_limit = recall_k * 2 if CONFIG.source_quota else recall_k
            # 合并后截回：重排按条计费，不能让它吃掉双倍候选
            candidates = _rrf_fuse([candidates, sparse], limit=fuse_limit)
        elif _get_bm25_store(vectorstore)[0] is None:
            # 语料没建起来（jieba/rank_bm25 缺失或建库失败）：混合召回整条失效。
            # 「建起来了但这题一个词都没命中」不算降级，看 trace 的 bm25_k=0 即可。
            _mark_degraded(trace, "bm25_unavailable")

    if CONFIG.source_quota and fused:
        # ---- per-source 配额（U9，2026-10-26）：把候选池的多样性提前到重排之前 ----
        # 为什么必须在这里做：路由收窄到 3 篇后，向量 + BM25 各自 6 条几乎都落在
        # 最相似的那同一篇上，RRF 之后剩给别的文档的名额基本为零。此时再在注入末尾
        # 限额（`max_per_source`）已经没有替补——实测 83 条里只从 67 修到 59。
        # 放到这里，多出来那一倍候选就是替补来源；配合 cap_per_source 的
        # 「超额排末尾」语义，候选不足时（只有一、两篇答得上）仍然兜满 coarse_k。
        candidates = cap_per_source(candidates, CONFIG.source_quota)[:coarse_k]
        if trace is not None:
            trace["source_quota"] = CONFIG.source_quota

    # 父子检索：RRF 后先把 child 聚合成 parent 再重排。
    # 放在重排前（而不是重排后）有两个理由：
    #   1) 同一小节多个 child 合成一条，进入重排的候选池直接缩小——
    #      契合提案「候选池预算与库规模无关」的总原则，重排是按条计费的；
    #   2) 重排看到的是小节全文，排序判别力比看 200 字碎片更强。
    # 单层库（无 parent_id）此调用是原样透传，行为不变。
    if CONFIG.parent_child:
        candidates = aggregate_parent_docs(candidates)
        # O13：相邻小节扩展。必须在聚合**之后**（要看 parent_id 序号），
        # 也必须在截预算**之前**（扩出来的邻居同样是有竞争力的候选）。
        if CONFIG.adjacent_radius > 0:
            before = len(candidates)
            candidates = expand_adjacent_parents(
                candidates, _get_parent_index(vectorstore), CONFIG.adjacent_radius
            )
            if trace is not None:
                trace["adjacent_added"] = len(candidates) - before
        # O13：去重**之后**才截，这才是「重排预算」该待的位置。
        # 截在聚合前会把同一小节的多个 child 当成多个名额，白白浪费预算。
        if CONFIG.parent_pool_k:
            candidates = candidates[: CONFIG.parent_pool_k]
        if trace is not None:
            trace["parents_k"] = len(candidates)

    if CONFIG.enable_rerank:
        # 开了 per-source 限额就要多取一点：不然被限额踢掉的名额没有替补，
        # 只会让注入条数变少。重排的成本在「把候选全部打分」这一步，
        # 已经付过了，取多少是免费的。
        want = len(candidates) if CONFIG.max_per_source else top_k
        docs = _rerank_docs(search_query, candidates, want, trace)
    else:
        docs = candidates[:top_k]

    docs = _dedupe(docs, ("source", "start_index"))
    if CONFIG.max_per_source:
        docs = cap_per_source(docs, CONFIG.max_per_source)[:top_k]

    # 排查检索质量时唯一想知道的事就是「这轮到底注入了哪几片」。
    # 只有 DEBUG 级别才打，正常运行时零开销。
    logger.debug(
        "知识库检索 用topic=%s topic=%r 命中 %d 条：%s",
        use_topic,
        topic,
        len(docs),
        [os.path.basename(str(d.metadata.get("source", "?"))) for d in docs],
    )

    if trace is not None:
        trace.update(
            query=search_query,
            used_topic=use_topic,
            coarse_k=coarse_k,
            recall_k=recall_k,
            bm25_k=len(sparse),
            fused_k=len(candidates),
            final_k=len(docs),
            # route_documents 已经把细节写进去了，这里只补一个「最终有没有生效」：
            # 中途回退全库时它会是 False，与开头的 True 不同，排查时要看这个。
            router_applied=bool(scope_sources),
        )

    return docs


def get_relevant_context(
    vectorstore: Chroma,
    query: str,
    k: int = 5,
) -> str:
    """学生上传 PDF 的会话级检索，返回可直接注入提示词的文本。"""
    return format_pdf_docs(retrieve_pdf_docs(vectorstore, query, k=k))


def retrieve_pdf_docs(
    vectorstore: Chroma,
    query: str,
    k: int = 5,
) -> list[Document]:
    """学生上传 PDF 的会话级检索，返回去重后的 Document 列表。

    保持原有检索逻辑不变：这是学术 PDF 的通用链路（含 front_matter 特判），
    与知识库的重排/混合无关，改动只会引入回归风险。
    """
    if vectorstore is None:
        return []

    docs = vectorstore.similarity_search(query, k=k)

    if METADATA_QUERY.search(query):
        front_matter = vectorstore.similarity_search(
            query,
            k=1,
            filter={"section": "front_matter"},
        )
        docs = front_matter + docs

    return _dedupe(docs, ("page_number", "line_start"))


def format_pdf_docs(docs) -> str:
    """把 PDF 切片格式化成可注入提示的上下文。"""
    parts = []
    for doc in docs:
        parts.append(
            f"[PDF page {doc.metadata.get('page_number', '?')}, "
            f"lines {doc.metadata.get('line_start', '?')}-"
            f"{doc.metadata.get('line_end', '?')}]\n"
            f"{doc.page_content}"
        )

    return "\n\n---\n\n".join(parts)
