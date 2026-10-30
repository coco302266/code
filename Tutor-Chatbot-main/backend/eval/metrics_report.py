"""检索链路评测指标报告卡：**把 e2e 报告翻译成人能判读的一张表**。

定位
----
`eval/e2e_runner.py` 产出的报告是**分层原始值**（L1/L2/L3/L4 各自的 dict），
机器好读、人不好读：想知道"现在到底达不达标"，得自己翻 thresholds、自己算
比率、自己回忆每个指标的口径。本模块只做这一层翻译，且**不重写任何指标计算**
——值全部从既有报告里取，口径与 `eval/metrics.py`、`smoke_rag`、`e2e_runner`
保持一致（同一套代码两个口径迟早分叉）。

三条容易踩错、因此写死在这里的纪律
----------------------------------
1. **未运行 ≠ 达标**。L3/L4 要花真金白银跑裁判模型，日常只跑 L1+L2。
   缺的层必须报 `unknown`，**绝不能填 0 或 False**——把"没跑"显示成"达标"，
   是这类报告最常见的骗人方式。汇总里 `unknown` 单独计数，不计入达标率分母。

2. **阈值必须给出处，不能拍脑袋**。本项目里两个反例：
   - `GATE_L1_P95 = 3.0` 有出处（统一延迟预算，见 e2e_runner 模块注释）；
   - 但 recall 若照抄提案的 0.85 会**永久红**——金标准派生自 topic，
     分子最多取到 top-3 的金标准增益，物理上限只有 ~0.92（见 `gold_caps()`）。
   所以每个阈值都在 `basis` 字段写明来源，并把物理上限一并报出来。

3. **降级率从耗时里剥出来单独报**。链路降级（重排没起来等）走的是快路径，
   耗时反而更短；只看 p95 会得到一个"因为大量失败所以很好看"的数字。
   理由见 `eval/slo_check.py` 模块 docstring。

用法（backend/ 下）：
    python -m eval.metrics_report                       # 读最新报告打印表格
    python -m eval.metrics_report --csv out.csv         # 导出 CSV
    python -m eval.metrics_report --json                # 给程序读
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPORTS_DIR = Path(__file__).resolve().parent / "reports"
DEFAULT_CASES = str(BACKEND_DIR / "rag_eval_cases.json")

# smoke_rag 在 backend/scripts/ 下，取金标准算物理上限时要用到它的 load_cases
_SCRIPTS_DIR = BACKEND_DIR / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# 与 eval/e2e_runner.py 同源的门禁常量。**不要在这里另写一份数字**——
# 两处阈值一旦分叉，"报告说达标、CI 说没达标"会让人不知道信谁。
GATE_L1_P95 = 3.0
GATE_L3_HALLUCINATION = 0.15

# 表格列（CSV 与之完全一致）
COLUMNS = [
    "group",
    "name",
    "unit",
    "value",
    "target",
    "ceiling",
    "direction",
    "status",
    "formula",
    "advice",
    "source",
]

COLUMN_LABELS = {
    "group": "分组",
    "name": "指标",
    "unit": "单位",
    "value": "当前值",
    "target": "目标阈值",
    "ceiling": "物理上限",
    "direction": "判定方向",
    "status": "是否达标",
    "formula": "计算说明",
    "advice": "改进建议",
    "source": "数据来源",
}

# 哪些指标有"数学上不可能超过"的天花板，以及它在 gold_caps() 里的字段名。
# 只有这两个：Recall@K 的分母是金标准全集而分子最多 K 条，
# Precision@K 的分子同样是那 K 条、且 0.5 档天生拉低均值。
# **其余指标不设上限**（首位命中率能到 100%、延迟没有下界），
# 给它们填 1.0 会让读者误以为"差一点就满分"。
CEILING_KEY_BY_METRIC = {
    "recall_at_k": "recall_cap_mean",
    "precision_at_k": "precision_cap_mean",
}

STATUS_PASS = "达标"
STATUS_FAIL = "未达标"
STATUS_UNKNOWN = "未运行"
STATUS_INFO = "不设阈值"


# --------------------------------------------------------------------------
# 指标定义
# --------------------------------------------------------------------------


@dataclass
class MetricSpec:
    """一个指标的定义。

    value 一律由 `get(report)` 从既有报告里取，**返回 None 表示该层没跑**
    （与"值是 0"严格区分）。
    """

    key: str
    name: str
    group: str
    source: str            # 数据来源层，给人看的
    unit: str              # "" | "%" | "s" | "ms" | "条"
    direction: str         # higher | lower
    comparator: str        # ">=" | "<=" | ">"
    target: float | None   # None = 信息性指标，不判达标
    basis: str             # 阈值出处（纪律 2）
    formula: str           # 计算说明
    advice: str            # 未达标时的改进方向
    get: "callable" = field(repr=False, default=lambda _r: None)


def _l1(key: str):
    def getter(report: dict):
        layer = (report.get("layers") or {}).get("L1")
        return None if not layer else layer.get(key)

    return getter


def _l2(key: str):
    def getter(report: dict):
        layer = (report.get("layers") or {}).get("L2")
        return None if not layer else layer.get(key)

    return getter


def _l3(key: str):
    def getter(report: dict):
        layer = (report.get("layers") or {}).get("L3")
        if not layer:
            return None
        agg = layer.get("aggregate") if isinstance(layer, dict) else layer
        return None if not agg else agg.get(key)

    return getter


def _ratio_l1(num_key: str, den_key: str = "total"):
    """L1 里 "命中数 / 计分题数" 这类比率。分母为 0 时返回 None（不是 0）。"""

    def getter(report: dict):
        layer = (report.get("layers") or {}).get("L1")
        if not layer:
            return None
        num, den = layer.get(num_key), layer.get(den_key)
        if num is None or not den:
            return None
        return num / den

    return getter


def _degraded_rate(report: dict):
    layer = (report.get("layers") or {}).get("L1")
    if not layer:
        return None
    total = layer.get("total")
    if not total:
        return None
    degraded = layer.get("degraded_cases")
    return None if degraded is None else degraded / total


def _l4(key: str):
    """L4 端到端层（真实 POST /chat）。整层没跑就是 None，不填占位。

    L4 报告是**两段结构**：`cascade`（级联，零 LLM 成本）与 `e2e`（真实 /chat）。
    本卡关心的成功率 / 端到端延迟都在 e2e 段里——**不能直接按顶层取**，
    否则会静默拿到 None 而显示成「未运行」（2026-10-28 踩过：报告明明跑了 L4，
    指标卡却说没跑，且没有任何报错）。
    """

    def getter(report: dict):
        layer = (report.get("layers") or {}).get("L4")
        if not layer:
            return None
        e2e = layer.get("e2e")
        if isinstance(e2e, dict) and key in e2e:
            return e2e.get(key)
        return layer.get(key)

    return getter


def _faithfulness(report: dict):
    """忠实度 = 1 − 外幻觉率。

    **这是派生值，不是第二个独立裁判**。口径差异必须说清楚：RAGAS 的
    faithfulness 是「回答中能被上下文支持的陈述占比」，本项目 L3 的
    hallucination_rate 是「既不在资料里也不属于通识的陈述占比」——
    两者在「通识陈述」这一块不同（RAGAS 会算成不忠实，本项目按通识豁免），
    所以本值**系统性高于** RAGAS 口径，跨项目比较时不可直接套用。
    好处是零额外成本：不新增任何裁判调用。
    """
    layer = (report.get("layers") or {}).get("L3")
    if not layer:
        return None
    agg = layer.get("aggregate") if isinstance(layer, dict) else layer
    if not agg:
        return None
    rate = agg.get("hallucination_rate")
    return None if rate is None else 1.0 - rate


def _rerank_top1_gain(report: dict):
    layer = (report.get("layers") or {}).get("L2")
    if not layer:
        return None
    on, off = layer.get("top1_on"), layer.get("top1_off")
    if on is None or off is None:
        return None
    return float(on - off)


METRIC_SPECS: list[MetricSpec] = [
    # ---------------- 链路稳定性 ----------------
    # 这一组是"看板第一行"：链路先得能跑通，后面所有质量指标才有意义。
    # 与降级率的区别见各自的 formula——成功率量的是"请求成不成功"，
    # 降级率量的是"成功了但走了快路径"（后者不报错，只看成功率会漏掉）。
    MetricSpec(
        key="link_success_rate",
        name="链路成功率",
        group="链路稳定性",
        source="L4 端到端层",
        unit="%",
        direction="higher",
        comparator=">=",
        target=0.99,
        basis="软目标：L4 走真实 POST /chat，429 限流已有一次退避重试，"
        "重试后仍失败才计失败，故取 99% 而非 100%（给网络抖动留一格）。"
        "**当前无实测序列**：L4 不进 CI、日常不跑，缺值显示「未运行」。",
        formula="HTTP 200 的用例数 / 全部请求用例数（429 退避 65s 重试一次后仍非 200 才算失败）。",
        advice="L4 要起后端才能跑：`python -m eval.e2e_runner --layers L4 --sample 10`。"
        "成功率掉下来先分清是限流还是真错：429 已被重试吸收，"
        "剩下的 5xx / SSE 中断要去后端日志看 traceback。"
        "注意本指标**不能替代降级率**——降级时请求照样返回 200，只看成功率会漏。",
        get=_l4("success_rate"),
    ),
    MetricSpec(
        key="e2e_p95_secs",
        name="端到端 p95 延迟",
        group="链路稳定性",
        source="L4 端到端层",
        unit="s",
        direction="lower",
        comparator="<=",
        target=12.0,
        basis="软目标，按首次实测校准（2026-10-28，12 条真实 /chat）："
        "实测 p95 **10.404s**，取 1.15 倍留余量 ⇒ 12.0s。"
        "L1 门禁 3.0s 只覆盖检索、**不含 LLM 生成与编排**，本项才是用户真正感知的延迟。"
        "样本仅 12 条，p95 稳定性有限，积累更多样本后需重标。",
        formula="一次 POST /chat 从发请求到 SSE 收完的耗时 p95（最近秩，不插值）。"
        "只在 status=200 的用例上统计——失败请求的耗时没有体感意义。",
        advice="端到端 10.4s 里检索只占 1.1s，**九成是生成与编排**——"
        "压这个数不要去动检索（动了也只省 10%）。若 L1 达标而本项不达标，"
        "问题在生成/编排（流式分段、评估节点并行度）而不在检索；"
        "若两者同步抬升，先查重排与知识库加载。",
        get=_l4("e2e_p95_secs"),
    ),
    # ---------------- 检索层 ----------------
    MetricSpec(
        key="top1_hit_rate",
        name="首位命中率",
        group="检索层",
        source="L1 检索层",
        unit="%",
        direction="higher",
        comparator=">=",
        target=0.90,
        basis="经验阈值：库内题占绝大多数，首位答错等于直接讲错知识点。",
        formula="top1 / total。首位召回结果的知识点 ∈ 期望知识点即命中。",
        advice="优先查查询改写与粗召回：① 省略式追问是否带上了历史 topic；"
        "② 混合召回的 BM25 与向量配比是否偏科；③ 金标准是否漏标了该知识点。",
        get=_ratio_l1("top1", "total"),
    ),
    MetricSpec(
        key="top3_hit_rate",
        name="检索命中率@K",
        group="检索层",
        source="L1 检索层",
        unit="%",
        direction="higher",
        comparator=">=",
        target=0.95,
        basis="经验阈值：注入窗口为 3，前三全不中意味着本轮回答无资料可用。",
        formula="top3 / total。前三任一结果的知识点 ∈ 期望知识点即命中。",
        advice="与首位命中率同源。若 top1 达标而 top3 不达标，检查注入窗口 "
        "inject_k 是否被调小；若两者同步下滑，先查知识库索引指纹是否变了。",
        get=_ratio_l1("top3", "total"),
    ),
    # ---------------- 上下文层 ----------------
    # 与检索层的区别：检索层问「有没有找到」，上下文层问「送进生成器的这 K 条
    # 质量如何」——所以精确率/召回率/噪声率都归在这里（对齐 RAGAS 的
    # context precision / context recall）。三者的口径差异见各自 formula。
    MetricSpec(
        key="recall_at_k",
        name="上下文召回率@K",
        group="上下文层",
        source="L1 检索层",
        unit="%",
        direction="higher",
        comparator=">=",
        target=0.85,
        basis="按物理上限折算：金标准增益 top-3 之和 / 金标准增益总和，"
        "全量 71 题均值上限约 0.92，0.85 ≈ 上限的 92%。"
        "直接抄提案的 0.85 而不看上限是错的——此处恰好 0.85 在上限内，"
        "但换题库后必须重算上限。",
        formula="命中的金标准切片增益之和 / 该用例全部金标准增益之和"
        "（gain 三档 1.0 精确 / 0.5 同知识点 / 0.0 无关，见 eval/metrics.py）。"
        "分母是切片全集，所以有上限。",
        advice="先看清缺口在哪一段（2026-10-28 实测拆解，71 题）："
        "召回阶段没捞进来 8.2pp、重排从池里挑错 5.1pp，两段大致相当。"
        "**别再试这几条，都已实测为负或零收益**："
        "① 盲目扩粗召回（池 4.2→9.2 条，池上限 +6.9pp 但重排挑错同步 +6.7pp，净 ≈0）；"
        "② 相邻小节扩展 ±1/±2（Recall 0.781 / 0.783，均低于不扩的 0.791，还贵 0.4~0.6s）；"
        "③ 重排改读更长的文本（parent_head 480 字 → Recall 掉到 0.731）；"
        "④ 重排名次与 RRF 名次融合（w=0.5/1.5 → 0.778/0.757，更差）。"
        "真正能动的只剩两个方向：**换更强的重排模型**（本机只有 "
        "bge-reranker-v2-m3-int8 缓存，换 fp16/更大模型需联网下载），"
        "或**补人工复核金标准**（当前为 AI 标注、平票取低档，上限本身可能偏低）。"
        "扩 inject_k 会同时拉低 precision（k=4 时 precision 上限仅 0.539），别单独看这一个数。",
        get=_l1("recall_at_k"),
    ),
    MetricSpec(
        key="precision_at_k",
        name="上下文精确率@K",
        group="上下文层",
        source="L1 检索层",
        unit="%",
        direction="higher",
        comparator=">=",
        target=0.60,
        basis="按物理上限折算：金标准增益 top-3 之和 / 3，全量 71 题均值上限 0.657，"
        "0.60 ≈ 上限的 91%。注意 0.5 档（同知识点非金标准片）会拉低分子，"
        "**上限本身就不是 1.0**，别拿 1.0 当目标。",
        formula="注入 K 条切片的增益之和 / K（K = inject_k，父子模式默认 3）。",
        advice="注意本指标与 recall **不是**跷跷板：K 固定时 "
        "precision = 命中的增益和 / K，而 recall = 同一分子 / 金标准增益和，"
        "二者比值恒为「金标准增益和 / K」（本评测集约 0.71）——"
        "所以任何提高 recall 的改动都会按比例带上 precision，不可能此消彼长。"
        "已实测的无效手段：收紧 relevance_floor / per_doc_floor 挡不住噪声"
        "（砍掉的几乎都是零增益片，而分母恒为 3，砍了也不涨分），"
        "放宽 per_doc_floor 到 -8.0 反而 +0.47pp（2026-10-28 已转默认）。"
        "剩下能动的同 recall：换更强的重排模型，或复核金标准标注。",
        get=_l1("precision_at_k"),
    ),
    MetricSpec(
        key="noise_rate",
        name="注入噪声率",
        group="上下文层",
        source="L1 检索层",
        unit="%",
        direction="lower",
        comparator="<=",
        target=round(1 / 3, 4),
        basis="业务含义（不是拍脑袋）：注入窗口 3 条，**平均每轮最多浪费 1 个槽位**"
        " ⇒ 1/3 ≈ 0.3333。首次实测（2026-10-28，71 题）0.3052（per_doc_floor=-8.0 档）。"
        "⚠ 它与召回率是**真实的旋钮关系**，已实测："
        "per_doc_floor -8.0 → 噪声 0.3052 / 召回 0.7909 / 平均注入 2.96 条；"
        "-5.0 → 噪声 0.2582 / 召回 0.7827 / 平均注入 2.76 条。"
        "即**收紧下限确实能砍噪声（-4.7pp），代价是召回 -0.82pp**——"
        "当前选了召回优先，故本项贴着阈值走，改档位前先看清这个取舍。",
        formula="注入窗口 K 条里增益为 0 的切片条数 / K，对已标注用例取均值。"
        "**未标注用例不计**（无金标准时 gain=0 只是「没标」，会把噪声算高）。",
        advice="与上下文精确率的区别：精确率看增益占比（0.5 档会拉高它），"
        "噪声率只看**完全答不上题**的条数——「1 条 1.0 + 1 条 0.5 + 1 条 0」和"
        "「3 条各 0.5」精确率相同（0.5），但前者有一条是纯噪声，只有噪声率能看出来。"
        "**唯一实测有效的旋钮是 per_doc_floor**（单条相关性下限），但它同时压召回："
        "-8.0 → -5.0 噪声 -4.7pp、召回 -0.82pp（71 题实测）。"
        "⚠ 别把它当成「免费降噪」：XB-35 曾把 -8.0 记为「白捡 +0.8pp」，"
        "那是**在没有噪声率这把尺子时得出的结论**——加上噪声率才看到代价是"
        "每轮多注入 0.2 条垃圾。要同时拿到低噪声 + 高召回，只能换更强的重排模型。",
        get=_l1("noise_rate"),
    ),
    # ---------------- 检索层（排序质量）----------------
    MetricSpec(
        key="mrr",
        name="MRR 平均倒数排名",
        group="检索层",
        source="L1 检索层",
        unit="",
        direction="higher",
        comparator=">=",
        target=0.90,
        basis="经验阈值：注入窗口只有 3 位，正确答案平均应落在第 1.1 位以内。",
        formula="对每条用例取 1/rank（rank = 首个增益 > 0 的位置），再对用例取均值。",
        advice="MRR 高而召回率低 ⇒ 排序没问题、是候选池没捞全，去调粗召回。"
        "MRR 本身低 ⇒ 重排模型或融合策略有问题。",
        get=_l1("mrr"),
    ),
    MetricSpec(
        key="ndcg_at_k",
        name="NDCG@K",
        group="检索层",
        source="L1 检索层",
        unit="",
        direction="higher",
        comparator=">=",
        target=0.80,
        basis="经验阈值：兼顾增益与位置，0.8 表示排序已接近理想序的八成。"
        "当前链路重排前有 0.816、重排后 0.865，阈值取在重排前之下留有余量。",
        formula="DCG(实际注入序列) / IDCG(金标准增益降序取前 K)。"
        "第 i 位折损 1/log2(i+2)。越界会截断到 1.0 并计入 ndcg_clamped_cases。",
        advice="先看 L2 提升度：若重排提升很小，换重排输入档位"
        "（RAG_RERANK_TEXT，当前默认 parent_head＝小节全文截断 480 字）收益最直接；"
        "若提升正常但绝对值低，问题在粗召回而不在重排。",
        get=_l1("ndcg_at_k"),
    ),
    # ---------------- 响应延迟 ----------------
    MetricSpec(
        key="p95_secs",
        name="检索 p95 延迟",
        group="响应延迟",
        source="L1 检索层",
        unit="s",
        direction="lower",
        comparator="<=",
        target=GATE_L1_P95,
        basis=f"CI 硬门禁 GATE_L1_P95 = {GATE_L1_P95}s（统一预算，不按切片粒度分档，"
        "见 eval/e2e_runner.py）。分档预算曾存在，因重排改打 parent_head "
        "（480 字，p95≈1.97s）消除了「全文打分」前提而撤销。",
        formula="单条用例「粗召回 + 融合 + 重排」端到端耗时的最近秩 p95（不插值）。"
        "不含 LLM 生成。",
        advice="⚠ 把 RAG_RERANK_TEXT 改成 parent（全文）会让 p95 回到约 3.5s 而判红，"
        "那是预期内的。真要压：① 重排候选数 coarse_k；② 重排输入文本长度"
        "（RAG_RERANK_HEAD_CHARS）；③ BM25 语料构建是否每次重建。"
        "改完用 scripts/l1_p95_by_mode.py 复核。",
        get=_l1("p95_secs"),
    ),
    MetricSpec(
        key="avg_secs",
        name="检索平均延迟",
        group="响应延迟",
        source="L1 检索层",
        unit="s",
        direction="lower",
        comparator="<=",
        target=1.5,
        basis="软目标：p95 卡尾、均值看体感，取当前实测 0.96s 的 1.5 倍留余量。",
        formula="全部计分用例检索耗时的算术平均。",
        advice="均值与 p95 同时抬升才说明真变慢；只有 p95 抬升通常是偶发慢请求，"
        "先查有没有降级（见降级率）。",
        get=_l1("avg_secs"),
    ),
    MetricSpec(
        key="degraded_rate",
        name="降级率",
        group="链路稳定性",
        source="L1 检索层",
        unit="%",
        direction="lower",
        comparator="<=",
        target=0.0,
        basis="零容忍：降级意味着重排/BM25/知识库某一环没起来，"
        "此时耗时更短但质量更差，不能用延迟指标掩盖。",
        formula="出现 degraded_components 的用例数 / 计分用例数"
        "（组件清单见 eval/slo_check.py）。",
        advice="按组件名定位：no_store=知识库没接上；bm25_unavailable=语料没建；"
        "rerank_unavailable=重排模型加载失败；rerank_error=调用抛异常。"
        "只要有降级，本报告的延迟数字就不可信，先修降级再谈优化。",
        get=_degraded_rate,
    ),
    # ---------------- 重排增益 ----------------
    MetricSpec(
        key="rerank_uplift",
        name="重排 NDCG 提升度",
        group="重排增益",
        source="L2 重排层",
        unit="",
        direction="higher",
        comparator=">",
        target=0.0,
        basis="CI 硬门禁：提升度 ≤ 0 即判失败（说明重排在帮倒忙）。"
        "严格大于 0，等于 0 不算过。",
        formula="NDCG@K(开重排) − NDCG@K(关重排，hybrid 档)。"
        "两次跑的是同一套检索代码，只切 enable_rerank。",
        advice="提升度 ≤ 0 时先别调参，确认重排模型真的加载了（看降级率）。"
        "其次换重排输入档位 RAG_RERANK_TEXT："
        "实测 parent_head（480 字）＞ parent 全文 ＞ child_title（片+标题）＞ child，"
        "parent_head 是唯一「质量显著优于 child_title 且 p95 仍过 3.0s 门禁」的档。",
        get=_l2("uplift"),
    ),
    MetricSpec(
        key="rerank_top1_gain",
        name="重排首位提升条数",
        group="重排增益",
        source="L2 重排层",
        unit="条",
        direction="higher",
        comparator=">",
        target=0.0,
        basis="与提升度同源的直观口径：重排把多少条用例从「首位答错」救成「首位答对」。",
        formula="top1_on − top1_off（开/关重排各自的首位命中用例数）。",
        advice="提升度为正但本项为 0 或负，说明重排改善了整体排序却伤了首位，"
        "检查重排阈值与融合权重。",
        get=_rerank_top1_gain,
    ),
    MetricSpec(
        key="rerank_p95_ms",
        name="重排 p95 耗时",
        group="重排增益",
        source="L2 重排层",
        unit="ms",
        direction="lower",
        comparator="<=",
        target=1500.0,
        basis="软目标：重排占检索耗时 97%（实测 1035ms / 总 p95 1067ms），"
        "取当前值 1.45 倍留余量。它决定了整条链路的延迟天花板。",
        formula="每条用例重排阶段耗时的最近秩 p95。",
        advice="重排是检索里最贵的一环。① 减少重排候选数（coarse_k）；"
        "② 缩短重排输入（当前 child_title ≈ 250 字，回退 parent 全文会涨 4 倍）；"
        "③ 换更小的交叉编码器。",
        get=_l2("rerank_p95_ms"),
    ),
    # ---------------- 生成层 ----------------
    MetricSpec(
        key="hallucination_rate",
        name="外幻觉率",
        group="生成层",
        source="L3 生成层",
        unit="%",
        direction="lower",
        comparator="<=",
        target=GATE_L3_HALLUCINATION,
        basis=f"CI 硬门禁 GATE_L3_HALLUCINATION = {GATE_L3_HALLUCINATION}"
        "（见 eval/e2e_runner.py）。",
        formula="裁判模型判为「回答含资料外内容」的用例数 / 参评用例数"
        "（temperature=0，多票多数）。",
        advice="L3 要跑裁判模型，成本高、日常不跑，缺值时本报告显示「未运行」。"
        "要出这个数：`python -m eval.e2e_runner --layers L3`（周跑）。"
        "超标时先查注入资料是否够（看召回率），再查提示词的引用约束。",
        get=_l3("hallucination_rate"),
    ),
    MetricSpec(
        key="faithfulness",
        name="忠实度",
        group="生成层",
        source="L3 生成层",
        unit="",
        direction="higher",
        comparator=">=",
        target=1.0 - GATE_L3_HALLUCINATION,
        basis=f"与 CI 硬门禁同源：忠实度 = 1 − 外幻觉率，"
        f"门禁 GATE_L3_HALLUCINATION = {GATE_L3_HALLUCINATION} ⇒ 忠实度 ≥ "
        f"{1.0 - GATE_L3_HALLUCINATION:.2f}。**不是第二个独立阈值**。",
        formula="1 − 外幻觉率（派生值，不新增裁判调用）。"
        "⚠ 口径与 RAGAS 的 faithfulness 不同：RAGAS 把「资料里没有的陈述」全算不忠实，"
        "本项目按资料 + **计算机通识**判定（教科书级常识不算幻觉），"
        "故本值系统性高于 RAGAS 口径，跨项目比较时不可直接套用。",
        advice="与外幻觉率严格互补，改进方向完全一致，**不要当成两个独立问题处理**："
        "① 先确认注入资料够不够（看上下文召回率）；② 再看提示词的引用约束有没有生效；"
        "③ 资料为空时 `add_rag_grounding` 会原样返回 prompt（生产行为，有测试锁定），"
        "此时模型只能凭通识作答，忠实度对这类用例不敏感。",
        get=_faithfulness,
    ),
    MetricSpec(
        key="contradiction_rate",
        name="事实矛盾率",
        group="生成层",
        source="L3 生成层",
        unit="%",
        direction="lower",
        comparator="<=",
        target=0.05,
        basis="经验阈值：与资料**直接冲突**是硬伤（比「没答到」严重），"
        "应接近于 0；取 5% 给裁判抖动留余量。当前无历史序列，待首次 L3 校准。",
        formula="裁判判为「矛盾」的用例数 / 有效判定用例数（三档：一致/矛盾/无关，多数投票）。",
        advice="矛盾 ≠ 幻觉：幻觉是「资料没写、模型自己编」，矛盾是「资料写了 A、回答说了非 A」。"
        "后者通常出自提示词的 grounding 约束没生效或资料被截断，"
        "优先查 `app.add_rag_grounding` 与注入条数，而不是调检索。",
        get=_l3("contradiction_rate"),
    ),
    MetricSpec(
        key="answer_relevancy",
        name="答案相关性",
        group="生成层",
        source="L3 生成层",
        unit="",
        direction="higher",
        comparator=">=",
        target=0.85,
        basis="经验阈值：答非所问与幻觉同属「讲了但没用」的硬伤，"
        "与忠实度取同一量级；三档判分（1 / 0.5 / 0）多票取中位数。"
        "⚠ **首次实测（2026-10-28，8 条 1 票）全部 1.0**：生成与裁判同源"
        "（都是 deepseek-flash，见 eval/judge_prompts 的异源说明），裁判明显偏松。"
        "该值在接入异源裁判或人工复核前**只能看趋势，不能当结论**。",
        formula="裁判按三档判「回答是否针对该问题作答」：1 紧扣 / 0.5 核心答到但明显跑题 / "
        "0 答非所问；多票取中位数后对用例取均值。",
        advice="相关性低而忠实度高 ⇒ 资料找对了、答案却没对准问题，"
        "查提示词是否把学生问题放在了显眼位置（追问场景尤其容易被 topic 覆盖）。"
        "相关性低且上下文召回率低 ⇒ 检索没找到，是检索问题不是生成问题。",
        get=_l3("answer_relevancy"),
    ),
    MetricSpec(
        key="answer_correctness",
        name="答案正确率",
        group="生成层",
        source="L3 生成层",
        unit="",
        direction="higher",
        comparator=">=",
        target=0.80,
        basis="经验阈值：完全答对是辅导场景的底线，三档判分下 0.8 ≈ 八成用例判 1 分。"
        "⚠ **首次实测（2026-10-28，8 条 1 票）全部 1.0**，与答案相关性同源的偏松问题："
        "生成与裁判同走 deepseek-flash，裁判对自己风格的答案天然宽容。"
        "参考答案优先用金标准切片正文（不是知识点标签），但锚点再准也挡不住同源偏松——"
        "**接入异源裁判（EVAL_JUDGE_MODEL）或人工复核前，本值只能看趋势**。",
        formula="裁判按三档判「内容对不对」：1 核心结论正确且覆盖要点 / "
        "0.5 部分正确或有实质遗漏 / 0 核心结论错误或未给出结论；"
        "多票取中位数后对用例取均值。只判对错，不判跑题（跑题由答案相关性判）。",
        advice="正确率低但相关性高 ⇒ 找对了方向但说错了，优先查资料本身对不对"
        "（看上下文精确率）与金标准标注。**要真正校准这个数，得先给题库补标准答案全文**——"
        "当前只用知识点标签做参考要点，判不出「漏了半个要点」这类细粒度错误。",
        get=_l3("answer_correctness"),
    ),
    MetricSpec(
        key="material_utilization",
        name="资料利用率",
        group="生成层",
        source="L3 生成层",
        unit="%",
        direction="higher",
        comparator=">=",
        target=0.60,
        basis="软目标：注入 3 条至少应真正用上 2 条（≈0.67），取 0.60 留余量。"
        "L3 与 L4 共用 `eval.grounding_eval.judge_utilization`（同一份口径），本卡只取 L3。",
        formula="回答实际引用的资料条数 / 注入条数，多票取**并集**"
        "（对「用过」宽容、对「没用」严格——本指标要抓的是「资料白注入」）。"
        "资料为空或一票都没解析出来记 None，不填 0。",
        advice="利用率低 ⇒ 检索召回来了但模型没用，这是**纯浪费**——"
        "既付了检索延迟又没提升回答。先查注入条数是否过多（噪声会挤掉真正有用的），"
        "再查提示词里资料块的排版是否被系统提示淹没。"
        "库外题（sources=0）没有可判对象，记 None 不进分母。",
        get=_l3("material_utilization"),
    ),
]

# 展示顺序（CSV 同序）。spec 的物理定义顺序与展示顺序解耦：
# 新增指标一律追加在末尾即可，由这里决定它在表格里落在哪一组。
GROUP_ORDER = (
    "链路稳定性",
    "检索层",
    "上下文层",
    "生成层",
    "响应延迟",
    "重排增益",
)

SPECS_BY_KEY = {s.key: s for s in METRIC_SPECS}


# --------------------------------------------------------------------------
# 取值 / 格式化 / 判定
# --------------------------------------------------------------------------


def format_value(value, unit: str) -> str:
    """把原始值格式化成人读的字符串。None 一律显示为「—」，不显示 0。"""
    if value is None:
        return "—"
    if unit == "%":
        return "%.2f%%" % (value * 100)
    if unit == "s":
        return "%.3fs" % value
    if unit == "ms":
        return "%.0fms" % value
    if unit == "条":
        return "%+d" % int(round(value))
    return "%.4f" % value


def format_target(spec: MetricSpec) -> str:
    """阈值显示。不设阈值的显示「—」而不是 0——0 会被当成"目标是 0"。"""
    if spec.target is None:
        return "—"
    return "%s %s" % (spec.comparator, format_value(spec.target, spec.unit))


def direction_label(spec: MetricSpec) -> str:
    return "越高越好" if spec.direction == "higher" else "越低越好"


def judge(value, spec: MetricSpec) -> str:
    """判定达标状态。

    **None 与 0 必须区分**：None 是"这层没跑"，0 是"跑出来是 0"。
    把前者判成「未达标」会让读者以为系统坏了，判成「达标」更糟。
    """
    if value is None:
        return STATUS_UNKNOWN
    if spec.target is None:
        return STATUS_INFO
    if spec.comparator == ">=":
        ok = value >= spec.target
    elif spec.comparator == "<=":
        ok = value <= spec.target
    elif spec.comparator == ">":
        ok = value > spec.target
    else:
        ok = value == spec.target
    return STATUS_PASS if ok else STATUS_FAIL


def build_rows(report: dict, caps: dict | None = None) -> list[dict]:
    """把报告翻译成表格行。纯函数。

    Args:
        caps: `gold_caps()` 算出的物理上限。传了就给有上限的指标补 ceiling 列——
            **只看当前值不看上限，读者无法判断 0.79 是好是坏**（2026-10-28：
            召回 0.79 看着离 0.85 差一截，实际是上限 0.916 的 86%，
            而真实缺口在"重排挑错"而不是"检索退化"，不看上限会找错方向）。
    """
    caps = caps or {}
    rows = []
    # 按 GROUP_ORDER 排序（组内保持定义顺序，故用稳定排序）。
    # 定义顺序与展示顺序解耦：新增指标追加在 METRIC_SPECS 末尾即可，
    # 不必为了"插到表格中间"去挪动已有 spec。
    order = {name: i for i, name in enumerate(GROUP_ORDER)}
    specs = sorted(
        METRIC_SPECS, key=lambda s: order.get(s.group, len(GROUP_ORDER))
    )
    for spec in specs:
        value = spec.get(report)
        cap_key = CEILING_KEY_BY_METRIC.get(spec.key)
        ceiling = caps.get(cap_key) if cap_key else None
        rows.append(
            {
                "key": spec.key,
                "group": spec.group,
                "name": spec.name,
                "unit": spec.unit,
                "value": value,
                "value_display": format_value(value, spec.unit),
                "target": spec.target,
                "target_display": format_target(spec),
                "target_basis": spec.basis,
                "ceiling": ceiling,
                "ceiling_display": "—" if ceiling is None else format_value(ceiling, spec.unit),
                "ceiling_rate": (
                    None if (ceiling is None or not value) else round(value / ceiling, 4)
                ),
                "direction": direction_label(spec),
                "status": judge(value, spec),
                "formula": spec.formula,
                "advice": spec.advice,
                "source": spec.source,
            }
        )
    return rows


def gold_caps(cases_file: str = "", k: int = 3) -> dict:
    """从评测集算指标的物理上限（不是人为目标）。

    Recall 的分母是金标准增益全集，而分子最多取到注入窗口 K 条，
    所以 recall **天然封顶**。不知道上限就去设阈值，等于可能设一个永远达不到的数。
    """
    try:
        import smoke_rag as S

        cases = S.load_cases(cases_file or DEFAULT_CASES)
    except Exception:
        return {}

    recall_caps, precision_caps = [], []
    for _q, _exp, _kind, _topic, rel in cases:
        gains = [float(r[2]) if len(r) > 2 else 1.0 for r in (rel or [])]
        gains = [g for g in gains if g > 0]
        if not gains:
            continue
        total = sum(gains)
        recall_caps.append(sum(sorted(gains, reverse=True)[:k]) / total)
        precision_caps.append(sum(sorted(gains, reverse=True)[:k]) / k)

    if not recall_caps:
        return {}
    return {
        "cases": len(recall_caps),
        "k": k,
        "recall_cap_mean": round(sum(recall_caps) / len(recall_caps), 4),
        "recall_cap_min": round(min(recall_caps), 4),
        "precision_cap_mean": round(sum(precision_caps) / len(precision_caps), 4),
        "precision_cap_min": round(min(precision_caps), 4),
    }


def build_card(report: dict, source_meta: dict | None = None) -> dict:
    """组装完整报告卡。

    Args:
        report: e2e_runner 产出的报告 dict。
        source_meta: 报告来源信息（文件、指纹、是否新鲜），由调用方补齐。
    """
    l1 = (report.get("layers") or {}).get("L1") or {}
    # 上限只需算一次：既进 context 给前端画参考线，也逐行补 ceiling 列。
    caps = gold_caps(report.get("cases_file") or "", l1.get("inject_k") or 3)
    rows = build_rows(report, caps)

    passed = sum(1 for r in rows if r["status"] == STATUS_PASS)
    failed = sum(1 for r in rows if r["status"] == STATUS_FAIL)
    unknown = sum(1 for r in rows if r["status"] == STATUS_UNKNOWN)
    info = sum(1 for r in rows if r["status"] == STATUS_INFO)
    # 达标率分母**不含** unknown 与 info：没跑的和不设阈值的都不该拉低这个数
    judged = passed + failed

    return {
        "generated_at": report.get("timestamp") or "",
        "source": source_meta or {},
        "context": {
            "granularity": l1.get("granularity"),
            "inject_k": l1.get("inject_k"),
            "scored_cases": l1.get("total"),
            "annotated_cases": l1.get("annotated_cases"),
            "ndcg_clamped_cases": l1.get("ndcg_clamped_cases"),
            "index_fingerprint": report.get("index_fingerprint") or "",
            "caps": caps,
        },
        "summary": {
            "total": len(rows),
            "passed": passed,
            "failed": failed,
            "unknown": unknown,
            "info": info,
            "judged": judged,
            "pass_rate": round(passed / judged, 4) if judged else None,
        },
        "gate": report.get("gate") or {},
        "columns": COLUMNS,
        "column_labels": COLUMN_LABELS,
        "rows": rows,
    }


# --------------------------------------------------------------------------
# 报告文件读写
# --------------------------------------------------------------------------


def latest_report_path() -> Path | None:
    """取最新的一份 e2e 报告。

    **按修改时间而不是文件名排序**。文件名排序看着更"稳"（名字里自带时间戳），
    但只要出现一个不含时间戳的命名（例如手工跑的 `e2e_metrics_fresh.json`），
    它就会因为 `m` > 数字而永久排在最后，之后**所有新报告都选不上**——
    报告会一直显示旧数据，且没有任何报错。mtime 不依赖命名约定，更可靠。
    """
    if not REPORTS_DIR.exists():
        return None
    candidates = [p for p in REPORTS_DIR.glob("e2e_*.json") if p.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda p: (p.stat().st_mtime, p.name))


def load_report(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError("报告文件不存在：%s" % p)
    return json.loads(p.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# CSV 导出
# --------------------------------------------------------------------------


# 列 → 行里取哪个字段。用表驱动而不是在 writerow 里手抄一遍列名：
# 手抄过一次就漂移过一次（加了 ceiling 列后 CSV 少一列，只有单测发现）。
_CSV_FIELD = {
    "group": "group",
    "name": "name",
    "unit": "unit",
    "value": "value_display",
    "target": "target_display",
    "ceiling": "ceiling_display",
    "direction": "direction",
    "status": "status",
    "formula": "formula",
    "advice": "advice",
    "source": "source",
}


def _csv_cell(row: dict, column: str) -> str:
    """取某一列要写进 CSV 的字符串（空单位显示「—」，与页面一致）。"""
    value = row.get(_CSV_FIELD.get(column, column), "") or ""
    if column == "unit" and not value:
        return "—"
    return str(value)


def rows_to_csv(rows: list[dict], bom: bool = True) -> str:
    """把表格行导出为 CSV 字符串，列顺序与 `COLUMNS` 完全一致。

    **关于 BOM**：`utf-8-sig` 是带 BOM 的 UTF-8，编码本身仍是 UTF-8。
    Excel（尤其中文 Windows）在没有 BOM 时会按本地 ANSI 码页解析，中文必然乱码。
    这是"UTF-8 导出中文乱码"最常见的原因，所以默认加 BOM；
    需要给程序解析时用 `bom=False` 拿纯 UTF-8。
    """
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow([COLUMN_LABELS[c] for c in COLUMNS])
    for row in rows:
        writer.writerow([_csv_cell(row, c) for c in COLUMNS])
    text = buf.getvalue()
    if bom:
        # 手动加 BOM：StringIO 不带编码信息，写文件侧统一用 utf-8-sig
        text = "﻿" + text
    return text


def write_csv(rows: list[dict], path: str | Path, bom: bool = True) -> Path:
    """写 CSV 文件。encoding 用 utf-8-sig（= UTF-8 + BOM）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(rows_to_csv(rows, bom=bom), encoding="utf-8-sig" if bom else "utf-8")
    return p


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def render_table(card: dict) -> str:
    """给人看的等宽表格。"""
    rows = card["rows"]
    widths = {
        "group": max(6, *(len(r["group"]) for r in rows)),
        "name": max(8, *(len(r["name"]) for r in rows)),
        "value": max(8, *(len(r["value_display"]) for r in rows)),
        "target": max(10, *(len(r["target_display"]) for r in rows)),
        "status": 6,
    }
    lines = []
    head = "%-*s  %-*s  %*s  %*s  %-*s" % (
        widths["group"], "分组",
        widths["name"], "指标",
        widths["value"], "当前值",
        widths["target"], "目标阈值",
        widths["status"], "判定",
    )
    lines.append(head)
    lines.append("-" * len(head))
    for r in rows:
        lines.append(
            "%-*s  %-*s  %*s  %*s  %-*s"
            % (
                widths["group"], r["group"],
                widths["name"], r["name"],
                widths["value"], r["value_display"],
                widths["target"], r["target_display"],
                widths["status"], r["status"],
            )
        )
    s = card["summary"]
    lines.append("-" * len(head))
    lines.append(
        "合计 %d 项：达标 %d / 未达标 %d / 未运行 %d / 不设阈值 %d（达标率 %.1f%%，分母只算已判定项）"
        % (s["total"], s["passed"], s["failed"], s["unknown"], s["info"],
           (s["pass_rate"] or 0) * 100)
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="检索链路评测指标报告卡")
    parser.add_argument("--report", default="", help="报告路径（缺省取最新一份）")
    parser.add_argument("--csv", default="", help="导出 CSV 到该路径")
    parser.add_argument("--no-bom", action="store_true", help="CSV 不加 BOM（给程序解析用）")
    parser.add_argument("--json", action="store_true", help="只输出 JSON")
    args = parser.parse_args(argv)

    path = Path(args.report) if args.report else latest_report_path()
    if path is None:
        print("没有找到任何 e2e 报告：先跑 python -m eval.e2e_runner --layers L1,L2")
        return 1
    report = load_report(path)
    card = build_card(report, {"report_file": str(path)})

    if args.json:
        print(json.dumps(card, ensure_ascii=False, indent=2))
        return 0

    print("报告来源：%s" % path)
    print(render_table(card))
    if args.csv:
        out = write_csv(card["rows"], args.csv, bom=not args.no_bom)
        print("\nCSV 已写入：%s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
