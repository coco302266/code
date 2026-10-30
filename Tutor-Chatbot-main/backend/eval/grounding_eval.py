"""L3 生成层评测：事实一致性 / 外幻觉率 / 资料利用率 / 空资料声明（提案 E3+Q5）。

**流程**：L1 召回 docs → 复原生产注入路径拿答案 → 裁判模型逐维度判 → 投票汇总。

**确定性与随机性的边界（本文件最重要的一条）**：
LLM 只负责吐 JSON；解析之后的每一步（投票、中位数、关键词匹配、门禁）都是
纯函数、有单测。裁判抖动只影响输入，不影响计算——所以改投票规则必须改
`majority_verdict` / `median_ratio` 并跑 `tests/test_eval_grounding.py`。

**空资料声明不走 LLM**（提案 §2.2 定死为关键词匹配）：它是硬门禁
（`empty_source_declaration = 1.0`），用关键词实现意味着它可复现、零成本、可进单测。

**回答怎么来**：与生产 /chat 同一条注入路径——
`get_knowledge_context`（含 topic 拼接与重排）→ `format_knowledge_docs` →
`app.add_rag_grounding`（含 RAG_GROUNDING_RULES）。资料为空时
`add_rag_grounding` 原样返回 prompt（生产行为，有测试锁定）——这是当前
外幻觉的已知根因之一，本评测**只量化它，不改它**（要不要改由 Q5 结论决定）。
"""

from __future__ import annotations

import json
import os
import statistics

from langchain_core.messages import HumanMessage

from eval.judge_prompts import (
    ANSWER_CORRECTNESS_PROMPT,
    ANSWER_RELEVANCY_PROMPT,
    FACTUAL_CONSISTENCY_PROMPT,
    HALLUCINATION_PROMPT,
    MATERIAL_UTILIZATION_PROMPT,
)

# 空资料声明关键词（提案 §3.5 case 1）。命中任一即视为已声明。
# 2026-10-24 补「未覆盖」：XB-13 时期的 notice 措辞，实测模型原样复述，
# 旧词表全不命中 → 声明率虚判 0%。教训与 XB-10 同源：改提示词措辞必须同步判定端。
# XB-14（同日用户决策）后 notice 已撤下，本词表保留用于记录模型**自发**声明率
#（信息性指标，不再硬门禁），模型若自发声明仍应被如实统计。
EMPTY_DECLARE_PATTERNS = (
    "暂无资料",
    "没有相关资料",
    "没有找到相关资料",
    "库里没有",
    "知识库中没有",
    "知识库里没有",
    "未提供资料",
    "没有资料",
    "资料中未",
    "资料未覆盖",
    "未覆盖",
)

# 裁判维度 -> 提示词模板（每维度独立 prompt，一次只问一个维度）
DIMENSION_PROMPTS = {
    "factual_consistency": FACTUAL_CONSISTENCY_PROMPT,
    "hallucination": HALLUCINATION_PROMPT,
    "material_utilization": MATERIAL_UTILIZATION_PROMPT,
    "answer_relevancy": ANSWER_RELEVANCY_PROMPT,
    "answer_correctness": ANSWER_CORRECTNESS_PROMPT,
}

# 分档型维度（答案相关性 / 答案正确率）的合法取值。
# 三档而非连续分：连续分在 temperature=0 下仍会抖出 0.7/0.8 这种不可复现的细粒度值，
# 三档让多票中位数有真正的"落点"，也让读报告的人知道 0.5 就是"半对"。
SCORE_LEVELS = (0.0, 0.5, 1.0)


def empty_declared(answer: str) -> bool:
    """空资料声明判定（关键词匹配，可复现）。"""
    return any(p in (answer or "") for p in EMPTY_DECLARE_PATTERNS)


def extract_json(text: str) -> dict | None:
    """从裁判输出里抠出第一个 JSON 对象；抠不到返回 None（调用方按废票处理）。

    裁判偶尔会在 JSON 前后加说明文字或包 ```json 代码块——
    先剥代码块围栏，再找首个 { 到最后一个 }。
    """
    if not text:
        return None
    cleaned = text.strip()
    if "```" in cleaned:
        parts = cleaned.split("```")
        for part in parts:
            candidate = part.strip()
            if candidate.startswith("json"):
                candidate = candidate[4:].strip()
            if candidate.startswith("{"):
                cleaned = candidate
                break
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def majority_verdict(votes: list[str]) -> str:
    """判定类投票：多数胜；平票取保守侧「矛盾」，不装作没看见。"""
    if not votes:
        return "无关"
    counts: dict[str, int] = {}
    for vote in votes:
        counts[vote] = counts.get(vote, 0) + 1
    best = max(counts.values())
    leaders = [v for v, c in counts.items() if c == best]
    if len(leaders) > 1:
        return "矛盾" if "矛盾" in leaders else leaders[0]
    return leaders[0]


def median_ratio(values: list[float]) -> float | None:
    """数值型投票取中位数（少数派剔除的自然等价物）。"""
    return statistics.median(values) if values else None


def build_numbered_context(docs: list) -> str:
    """把召回切片编号排版，供 MATERIAL_UTILIZATION_PROMPT 按 [n] 引用。"""
    parts = []
    for i, doc in enumerate(docs, 1):
        topic = (doc.metadata or {}).get("topic", "未标注")
        parts.append(f"[{i}] [知识点：{topic}]\n{doc.page_content}")
    return "\n\n".join(parts)


def make_judge_llm():
    """裁判模型：独立于生产通道，temperature=0（可复现优先）。

    由 eval/judge_llm.py 统一解析：EVAL_JUDGE_MODEL / _API_KEY / _BASE_URL，
    未配置则逐项回落 LLM_*。改造前只支持 EVAL_JUDGE_MODEL 一个变量，
    且 key 与端点仍取生产值——换厂商时必须三件套齐备，做不到。
    """
    from eval.judge_llm import make_judge_llm as _factory

    return _factory()


def make_answer_llm():
    """回答生成：直接复用生产 get_llm("openai")，保持与 /chat 同一行为。"""
    from agent.graph import get_llm

    return get_llm("openai")


def generate_answer(answer_llm, query: str, docs: list) -> str:
    """按生产 /chat 的注入路径生成答案（与 app.py 知识库分支同构）。"""
    import app as production_app
    from agent.rag.retriever import format_knowledge_docs

    rag_context = format_knowledge_docs(docs) if docs else ""
    base_prompt = (
        "你是计算机八股辅导助手。依据下面给出的知识库资料，"
        "简洁、准确地回答学生的问题。\n\n"
        f"学生提问：\n{query}"
    )
    prompt = production_app.add_rag_grounding(base_prompt, rag_context)
    reply = answer_llm.invoke([HumanMessage(content=prompt)])
    return str(getattr(reply, "content", "") or "")


def _judge_once(
    judge_llm, template: str, context: str, answer: str, extra: dict | None = None
) -> dict | None:
    """跑一次裁判并解析 JSON；解析失败重试一次，仍失败按废票计。

    extra 用于给模板补 context/answer 之外的占位符（如答案正确率的
    {question} / {reference}）——**不要**为了塞字段去拼 context 字符串，
    那样模板里的字段含义会漂。
    """
    fields = {"context": context or "（空）", "answer": answer}
    if extra:
        fields.update(extra)
    prompt = template.format(**fields)
    for _ in range(2):
        try:
            raw = str(judge_llm.invoke([HumanMessage(content=prompt)]).content)
        except Exception:
            continue
        parsed = extract_json(raw)
        if parsed is not None:
            return parsed
    return None


def judge_utilization(judge_llm, docs: list, answer: str, votes: int = 3) -> dict:
    """只判「资料利用率」一个维度（L3 四维里的第 3 维，L4 只复用这一维）。

    单独抽出来是为了**只有一份口径**：L3 与 L4 都调它，不会哪天 L4 自己写一份
    「used/total」算出一个不可比的值。聚合规则沿用 L3：
    - 多票取**并集**（对「用过」宽容、对「没用」严格——本指标是为了抓「资料白注入」）；
    - 裁判给的编号越界（> len(docs)）直接丢弃，空列表是合法票（一条都没用）；
    - docs 为空或一票都没解析出来 → 记 None（符合「没判就是 None，不填占位」）。

    Returns:
        {"material_utilization": float | None, "used_indices": list[int],
         "utilization_votes": int}
    """
    empty = {"material_utilization": None, "used_indices": [], "utilization_votes": 0}
    if not docs or not answer:
        return dict(empty)

    context = build_numbered_context(docs)
    used: list[set[int]] = []
    for _ in range(votes):
        parsed = _judge_once(judge_llm, MATERIAL_UTILIZATION_PROMPT, context, answer)
        if not parsed or not isinstance(parsed.get("used_indices"), list):
            continue
        try:
            indices = {int(i) for i in parsed["used_indices"]}
        except (TypeError, ValueError):
            continue
        valid = {i for i in indices if 1 <= i <= len(docs)}
        if valid or indices == set():
            used.append(valid)

    if not used:
        return dict(empty)

    union: set[int] = set()
    for item in used:
        union |= item
    return {
        "material_utilization": round(len(union) / len(docs), 4),
        "used_indices": sorted(union),
        "utilization_votes": len(used),
    }


def gold_reference(gold: list, texts: list[str], limit: int = 900) -> str:
    """把「金标准切片正文」拼成答案正确率的参考答案。

    为什么不用用例的 `expected`（知识点标签）当参考答案：标签只有一个词
    （"死锁"），裁判看到它和没看到一样，**实测 4 条全判 1.0**（2026-10-28）——
    锚点太粗，判不出"漏了半个要点"。金标准切片正文才是"这道题的答案长什么样"。

    只取 gain > 0 的片：gain=0 标的就是无关片，拿它当参考答案等于告诉裁判
    答错也算对。

    取文本走 `refine_gold.build_chunk_index`（父子库坐标必须聚合后才能命中），
    本身不碰 store——保持本函数纯函数可单测。
    """
    pairs: list[str] = []
    for item, text in zip(gold or [], texts or []):
        if not item or len(item) < 2 or not (text or "").strip():
            continue
        gain = 1.0
        if len(item) > 2:
            try:
                gain = float(item[2])
            except (TypeError, ValueError):
                gain = 1.0
        if gain > 0:
            pairs.append(text)
    return "\n---\n".join(pairs)[:limit]


def judge_score(
    judge_llm,
    template: str,
    question: str,
    answer: str,
    votes: int = 3,
    reference: str = "",
    levels: tuple[float, ...] = SCORE_LEVELS,
) -> dict:
    """分档型维度（答案相关性 / 答案正确率）的裁判：多票取中位数。

    与 `judge_utilization` 一样走「一次一维度 + 多票」；不同点在于聚合规则：
    - 利用率取**并集**（对"用过"宽容，因为它要抓的是"资料白注入"）；
    - 分档分取**中位数**（对偏离档位的票不宽容，0.7 这种不落档的值直接作废票）。

    0 分是合法值：任何 `if score` 的判断都会把"判了 0 分"当成"没判"，
    所以这里一律用 `is None` 判定缺失。
    """
    empty = {"score": None, "votes": 0}
    if not answer:
        return dict(empty)

    scores: list[float] = []
    for _ in range(votes):
        parsed = _judge_once(
            judge_llm,
            template,
            context="",
            answer=answer,
            extra={"question": question, "reference": reference or "（无参考答案要点）"},
        )
        if not parsed:
            continue
        try:
            value = float(parsed.get("score"))
        except (TypeError, ValueError):
            continue
        if value in levels:
            scores.append(value)

    return {"score": median_ratio(scores), "votes": len(scores)}


def evaluate_case(
    case_query: str,
    case_topic: str,
    docs: list,
    answer: str,
    judge_llm,
    votes: int = 3,
    kind: str = "",
    expected: list[str] | None = None,
    gold_texts: list[str] | None = None,
    gold: list | None = None,
) -> dict:
    """评一条 case，返回四维指标（维度内部失败记 None，汇总时单独计数）。

    hallucination_ratio = hallucinated / total（total=0 视为 0，没有可验证陈述
    就没有幻觉可言，记 0 而不是 None——空答案不该把整条 case 作废）。

    空资料声明（提案 §3.5 case 1）判定对象是 **kind=oos（库外题）**：
    意图是「问了库外知识点，答案是否声明资料未覆盖」。**不能**按"检索结果为空"
    判定——有相关性下限（relevance_floor）后库外题确实检索为空，但 oos 的
    判定意图是声明行为本身；整题判定口径见 retriever.py。
    XB-14 后声明不再强制，本指标降级为信息性记录。
    """
    context = build_numbered_context(docs)
    result: dict = {"query": case_query, "topic": case_topic}

    # 维度 1：事实一致性（判定类，多数投票）
    verdicts = []
    for _ in range(votes):
        parsed = _judge_once(judge_llm, FACTUAL_CONSISTENCY_PROMPT, context, answer)
        if parsed and parsed.get("verdict") in {"一致", "矛盾", "无关"}:
            verdicts.append(parsed["verdict"])
    result["factual_consistency"] = majority_verdict(verdicts) if verdicts else None
    result["consistency_votes"] = len(verdicts)

    # 维度 2：外幻觉率（数值类，中位数）
    ratios = []
    for _ in range(votes):
        parsed = _judge_once(judge_llm, HALLUCINATION_PROMPT, context, answer)
        if not parsed:
            continue
        try:
            total = int(parsed.get("total_statements") or 0)
            bad = int(parsed.get("hallucinated_statements") or 0)
        except (TypeError, ValueError):
            continue
        if total < 0 or bad < 0 or bad > total:
            continue
        ratios.append(bad / total if total else 0.0)
    result["hallucination_ratio"] = median_ratio(ratios)
    result["hallucination_votes"] = len(ratios)

    # 维度 3：资料利用率（答案实际用到的编号 / 注入条数）
    # 口径只在 judge_utilization 里写一份（L4 复用同一函数，不许各算各的）
    result.update(judge_utilization(judge_llm, docs, answer, votes=votes))

    # 维度 4：空资料声明（纯关键词，不走 LLM）
    # 判定对象 = 库外题（kind=oos）；检索碰巧为空的普通题也顺带判，但当前
    # 链路下不会发生（无相关性阈值，永远召回 top-k）。
    result["empty_source_declared"] = (
        empty_declared(answer) if (kind == "oos" or not docs) else None
    )

    # 维度 5：答案相关性（分档，中位数）——答得对不对题
    rel = judge_score(judge_llm, ANSWER_RELEVANCY_PROMPT, case_query, answer, votes=votes)
    result["answer_relevancy"] = rel["score"]
    result["relevancy_votes"] = rel["votes"]

    # 维度 6：答案正确率（分档，中位数）——内容对不对。
    # 参考答案优先用**金标准切片正文**（真正含答案的片段）；取不到才退回
    # expected（知识点标签）——只有标签时裁判锚点太粗，实测会一律判 1.0。
    ref = gold_reference(gold or [], gold_texts or [])
    result["correctness_ref_kind"] = "gold_text" if ref else "expected_labels"
    if not ref:
        ref = "、".join(str(x) for x in (expected or []) if str(x).strip())
        result["correctness_ref_kind"] = "expected_labels" if ref else "none"
    cor = judge_score(
        judge_llm, ANSWER_CORRECTNESS_PROMPT, case_query, answer, votes=votes, reference=ref
    )
    result["answer_correctness"] = cor["score"]
    result["correctness_votes"] = cor["votes"]

    return result


def aggregate_cases(results: list[dict]) -> dict:
    """把逐条结果聚合成 L3 报告段。缺票的 case 分组计数，不混进均值。"""
    def mean_of(key: str) -> tuple[float | None, int]:
        values = [r[key] for r in results if r.get(key) is not None]
        if not values:
            return None, 0
        return round(sum(values) / len(values), 4), len(values)

    factual = [r for r in results if r.get("factual_consistency")]
    contradictions = sum(1 for r in factual if r["factual_consistency"] == "矛盾")
    halluc_rate, halluc_n = mean_of("hallucination_ratio")
    util_rate, util_n = mean_of("material_utilization")
    relevancy, relevancy_n = mean_of("answer_relevancy")
    correctness, correctness_n = mean_of("answer_correctness")

    empty_cases = [r for r in results if r.get("empty_source_declared") is not None]
    declared = sum(1 for r in empty_cases if r["empty_source_declared"])

    return {
        "cases": len(results),
        # 矛盾率 = 判为「矛盾」的 case / 有效判定 case
        "contradiction_rate": round(contradictions / len(factual), 4) if factual else None,
        "factual_judged": len(factual),
        "hallucination_rate": halluc_rate,
        "hallucination_judged": halluc_n,
        "material_utilization": util_rate,
        "utilization_judged": util_n,
        # 答案相关性 / 答案正确率（2026-10-28 补，对齐 RAGAS 的生成层口径）
        "answer_relevancy": relevancy,
        "relevancy_judged": relevancy_n,
        "answer_correctness": correctness,
        "correctness_judged": correctness_n,
        # 答案正确率的参考锚点来源：gold_text=用了金标准切片正文（可信），
        # expected_labels=只有知识点标签（锚点太粗，实测会一律判 1.0）。
        # 报出来是为了让读报告的人知道这个数**值不值得信**。
        "correctness_ref_gold": sum(
            1 for r in results if r.get("correctness_ref_kind") == "gold_text"
        ),
        "correctness_ref_labels": sum(
            1 for r in results if r.get("correctness_ref_kind") == "expected_labels"
        ),
        # 空资料声明率：分母是库外题（kind=oos，见 evaluate_case 的口径说明）；
        # 样本里没有 oos 用例时为 None，门禁按"无从判定"处理
        "empty_source_declaration": round(declared / len(empty_cases), 4) if empty_cases else None,
        "empty_source_cases": len(empty_cases),
    }


def run_grounding(cases: list[dict], store, sample: int | None, votes: int = 3) -> dict:
    """L3 主入口：抽样本 → 生成答案 → 裁判 → 汇总。

    抽样规则：oos（库外题）**全保留**——空资料声明门禁依赖它们，
    随机抽样可能一条都抽不到，硬门禁就没数据可判了；其余按固定种子随机抽。
    """
    import random

    from agent.rag.retriever import retrieve_knowledge_docs

    oos = [c for c in cases if (c.get("kind") or "") == "oos"]
    rest = [c for c in cases if (c.get("kind") or "") != "oos"]
    if sample is None:
        picked = list(cases)
    else:
        n_rest = max(sample - len(oos), 0)
        picked = oos + random.Random(20261024).sample(rest, min(n_rest, len(rest)))

    judge_llm = make_judge_llm()
    answer_llm = make_answer_llm()

    # 金标准切片正文索引：答案正确率要用它当参考答案，**建一次复用**。
    # 走 refine_gold 的实现而不是自己写：父子库的金标准坐标是 parent_start，
    # 直查库里的 child 表一个都命中不了（那里踩过一次，见 refine_gold 注释）。
    chunk_index = None
    try:
        from eval.refine_gold import build_chunk_index

        chunk_index = build_chunk_index(store)
    except Exception as exc:  # 取不到就退回知识点标签，别让整层挂掉
        print("  [warn] 金标准正文索引不可用，答案正确率退回知识点标签：%s" % exc, flush=True)

    results = []
    for i, case in enumerate(picked, 1):
        query = (case.get("query") or "").strip()
        if not query:
            continue
        gold = case.get("relevant_chunks") or []
        gold_texts = []
        if chunk_index is not None and gold:
            from eval.refine_gold import fetch_chunk_texts

            gold_texts = fetch_chunk_texts(store, gold, chunk_index)
        trace: dict = {}
        docs = retrieve_knowledge_docs(
            store, query, topic=(case.get("topic") or "").strip(), trace=trace
        )
        answer = generate_answer(answer_llm, query, docs)
        row = evaluate_case(
            query,
            case.get("topic") or "",
            docs,
            answer,
            judge_llm,
            votes,
            kind=case.get("kind") or "",
            expected=case.get("expected") or [],
            gold_texts=gold_texts,
            gold=gold,
        )
        row["kind"] = case.get("kind") or ""
        row["retrieved_chunks"] = len(docs)
        results.append(row)
        print(
            "  [%d/%d] %s… 一致=%s 幻觉=%s 利用=%s 相关=%s 正确=%s 空资料声明=%s"
            % (
                i,
                len(picked),
                query[:18],
                row["factual_consistency"],
                row["hallucination_ratio"],
                row["material_utilization"],
                row["answer_relevancy"],
                row["answer_correctness"],
                row["empty_source_declared"],
            ),
            flush=True,
        )

    return {"aggregate": aggregate_cases(results), "per_case": results}
