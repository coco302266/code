"""端到端评测执行器（提案 §2.4，Wave 5 骨架 + Wave 6：L1 + L2 + L3 + L4）。

**分层与门禁**（与提案 §2.5 对齐，有两处**有意偏离**，原因写明）：
- L1：复用 `smoke_rag.run_config`（不重写一遍检索逻辑——同一套代码两个口径
  迟早分叉）。门禁只卡 p95 ≤ 3.0s；recall/precision/mrr/nDCG **只报告不设门禁**
  ——topic 派生金标准的分母是主题切片全集（min 6 / 中位 10 / max 20），
  recall 天然封顶在 k/N，拿提案写的 0.85 去卡会永久红，那是口径错配不是检索缺陷。
- L2：提升度 > 0（重排在帮倒忙即失败）。
- L3：外幻觉率 ≤ 0.15（CI 硬门禁）；空资料声明率已改为**信息性指标**
  （XB-14：2026-10-24 用户决策不再要求声明，缺口走日志 + 聚合报表）。
- L4 端到端（Wave 6）：POST 真实 /chat 收 SSE + cascade 级联评测。
  **不进 CI，只出报告**（提案 §2.5：成本高，周跑）；
  级联 hold_rate 是软门禁（提案 §3.6），用于评估 topic 抽错时检索的兜底能力。
  e2e 部分复用 `gen_eval_samples` 的 SSE 消费与限流逻辑（间隔 ≥7s、
  429 退避 65s 重试一次）——同一套口径不写两份，避免分叉。

用法（在 backend/ 下）：
    python -m eval.e2e_runner                                  # 全量 L1+L2+L3（贵，周跑）
    python -m eval.e2e_runner --sample 10                      # dev 小样本（oos 全保留）
    python -m eval.e2e_runner --layers L1,L2                   # 只跑零 LLM 成本层（可进 CI）
    python -m eval.e2e_runner --layers L4 --sample 10          # L4 端到端（需先起后端）
    python -m eval.e2e_runner --out reports/my_run.json        # 指定输出
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# smoke_rag / gen_eval_samples 已移入 backend/scripts/（2026-10-24 结构整合），
# 本模块 4 处惰性 `import smoke_rag / import gen_eval_samples` 依赖这里补的搜索路径。
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from agent.rag.indexer import load_knowledge_index  # noqa: E402
from eval.fingerprint import collection_fingerprint  # noqa: E402
from eval.metrics import p95  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CASES = str(BACKEND_DIR / "rag_eval_cases.json")
DEFAULT_OUT_DIR = Path(__file__).resolve().parent / "reports"
COLLECTION = "cs_basics"
PERSIST_DIR = str(BACKEND_DIR / "chroma_kb")

# 门禁阈值（提案 §2.5 / §3.1）
#
# **GATE_L1_P95 是统一预算，不按切片粒度分档**（2026-10-27 定稿）：
# 手册曾短暂写过「父子 ≤5s / 扁平 ≤3s」的分档，理由是父子模式注入整节（1000+ 字）、
# 重排耗时随文本长度涨，照抄 3s 会把它判成永久不达标。但那个分档的前提
# ——**重排打 parent 全文**——已被消除：现在父子模式重排读的是小节全文截断
# 480 字（`RAG_RERANK_TEXT=parent_head`，2026-10-29 起为默认），L1 实测
# p95 1.967s，仍在 3.0s 预算内，分档不再有存在理由。
#
# 档位沿革：child_title（~250 字，p95 1.20s）→ parent_head/480（~480 字，
# p95 1.97s）。换档依据是 395 题新集上 nDCG +2.62pp、噪声率 −2.09pp
# 三项显著（见 docs/reports/REPORTS.md）。
#
# ⚠ 若把 RAG_RERANK_TEXT 改成 `parent`（全文），p95 会回到约 3.5–3.7s 而**判红**
# ——那是预期内的：分档预算本就是为 parent 全文打分才设的。回退前先看
# `scripts/l1_p95_by_mode.py` 的三档对照表。
GATE_L1_P95 = 3.0
GATE_L3_HALLUCINATION = 0.15
GATE_L3_EMPTY_DECLARE = 1.0

# L4 端到端（提案 §2.2：与 gen_eval_samples 同一套限流口径）
DEFAULT_BASE_URL = "http://127.0.0.1:8000"
CHAT_MIN_INTERVAL_SECS = 7.0  # 限流 10/min：两次 /chat 开始时刻至少隔 7 秒
RATE_LIMIT_BACKOFF_SECS = 65.0  # 429 退避后重试一次
SSE_TIMEOUT_SECS = 120.0
RANDOM_SEED = 20261024  # 与 grounding_eval.run_grounding 同种子，两层的抽样可对齐复盘


def evaluate_gate(report: dict) -> tuple[bool, list[str]]:
    """按报告判门禁，返回 (是否通过, 违规说明列表)。纯函数，有单测。

    层缺失或指标为 None **不算通过**——没跑出来的东西不能绿灯。
    L1 的 recall/precision/mrr/nDCG 不设门禁（口径封顶问题见模块 docstring）。
    """
    layers = report.get("layers") or {}
    failures: list[str] = []

    if not any(layers.values()):
        failures.append("报告里没有任何层的数据，无法判定门禁")
        return False, failures

    l1 = layers.get("L1")
    if l1 is not None:
        p95_secs = l1.get("p95_secs")
        if p95_secs is None:
            failures.append("L1 p95 缺失，无法判定")
        elif p95_secs > GATE_L1_P95:
            failures.append("L1 检索 p95 %.3fs 超预算 %.1fs" % (p95_secs, GATE_L1_P95))

    l2 = layers.get("L2")
    if l2 is not None:
        uplift = l2.get("uplift")
        if uplift is None:
            failures.append("L2 提升度缺失，无法判定")
        elif uplift <= 0:
            failures.append("L2 重排提升度 %s ≤ 0（重排在帮倒忙）" % uplift)

    l3 = layers.get("L3")
    if l3 is not None:
        # 兼容两种形态：{"aggregate": {...}}（报告实际结构）与扁平 dict（单测直接喂）
        stats = l3.get("aggregate", l3) if isinstance(l3, dict) else {}
        stats = stats or {}
        rate = stats.get("hallucination_rate")
        if rate is None:
            failures.append("L3 外幻觉率缺失，无法判定")
        elif rate > GATE_L3_HALLUCINATION:
            failures.append(
                "L3 外幻觉率 %.2f%% 超门禁 %.0f%%" % (rate * 100, GATE_L3_HALLUCINATION * 100)
            )
        empty = stats.get("empty_source_declaration")
        # 2026-10-24 XB-14（用户决策）：空资料不再要求模型声明（告知学生的
        # notice 已撤下，缺口改走日志记录 + 聚合报表）。声明率从硬门禁
        # （必须 = 1.0）降级为**信息性指标**照常上报，不判失败——
        # 旧门禁 GATE_L3_EMPTY_DECLARE 保留常量仅供历史报告对照。
        if empty is not None and empty < GATE_L3_EMPTY_DECLARE:
            print(
                "  [info] L3 空资料声明率 %.0f%%（XB-14 后不再硬门禁，仅记录）"
                % (empty * 100),
                flush=True,
            )

    return (not failures), failures


def run_l1_l2(store, cases_file: str) -> tuple[dict, dict]:
    """L1 + L2：复用 smoke_rag 的同一套检索与指标代码。"""
    import smoke_rag as S

    S.CASES = S.load_cases(cases_file)
    full = S.run_config(store, "full")
    off = S.run_config(store, "hybrid")  # hybrid = 混合召回但不重排
    # configure 改的是模块级全局 CONFIG（改字段不换对象），上面最后停在 hybrid。
    # 本进程之后若接着跑 L3，检索必须回到生产默认（full，带重排），
    # 否则 L3 的召回会静默变成"无重排版"，与 /chat 行为不一致。
    S.configure("full")

    m = full.get("metrics") or {}
    l1 = {
        # 粒度与注入窗口必须进报告：parent 粒度每题金标准多为 4 条而窗口只有 3，
        # Recall 有 0.75 的物理上限；不写清就会被当成"检索退化"。
        "granularity": full.get("granularity"),
        "inject_k": full.get("inject_k"),
        "top1": full["top1"],
        "top3": full["top3"],
        "total": full["total"],
        "recall_at_k": m.get("recall_at_k"),
        "precision_at_k": m.get("precision_at_k"),
        "noise_rate": m.get("noise_rate"),
        "mrr": m.get("mrr"),
        "ndcg_at_k": m.get("ndcg_at_k"),
        "annotated_cases": m.get("annotated_cases"),
        "fallback_cases": m.get("fallback_cases"),
        "ndcg_clamped_cases": m.get("ndcg_clamped_cases"),
        "avg_secs": full["avg_secs"],
        "p95_secs": full["p95_secs"],
        "degraded_cases": full["degraded_cases"],
    }
    rerank_ms_values = [c.get("rerank_ms") or 0 for c in full["cases"]]
    l2 = {
        "ndcg_rerank_on": m.get("ndcg_at_k"),
        "ndcg_rerank_off": (off.get("metrics") or {}).get("ndcg_at_k"),
        "uplift": round(
            (m.get("ndcg_at_k") or 0.0) - ((off.get("metrics") or {}).get("ndcg_at_k") or 0.0),
            4,
        ),
        "rerank_p95_ms": p95(rerank_ms_values) if rerank_ms_values else 0,
        "top1_on": full["top1"],
        "top1_off": off["top1"],
    }
    return l1, l2


def run_l3(store, cases_file: str, sample: int | None, votes: int) -> dict:
    from eval.grounding_eval import run_grounding

    raw = json.loads(Path(cases_file).read_text(encoding="utf-8"))
    return run_grounding(raw.get("cases", []), store, sample=sample, votes=votes)


def run_cascade(store, cases_file: str) -> dict:
    """级联失败评测（提案 §3.6 / Q6，Wave 6）：评估节点 topic 抽错时检索兜不兜得住。

    每条 cascade 用例跑两遍：topic=""（第一轮直问，baseline）与
    topic=assumed_wrong_topic（模拟抽错）。判定复用 smoke_rag.top_hit
    （归一化子串匹配，与 L1 同一把尺子）。

    **口径说明**：生产里 `_should_augment_with_topic` 会先判断要不要拼 topic，
    这里强制传错 topic 走检索，比生产更严——测的是「错 topic 拼进去后
    检索还剩多少抵抗力」，属软门禁出报告，不阻断。
    """
    import smoke_rag as S

    from agent.rag.retriever import retrieve_knowledge_docs

    raw = json.loads(Path(cases_file).read_text(encoding="utf-8"))
    cases = [c for c in raw.get("cases", []) if (c.get("kind") or "") == "cascade"]
    rows = []
    for case in cases:
        query = (case.get("query") or "").strip()
        expected = set(case.get("expected") or [])
        wrong_topic = (case.get("assumed_wrong_topic") or "").strip()
        if not query or not expected or not wrong_topic:
            raise SystemExit(
                "cascade 用例缺字段（query / expected / assumed_wrong_topic 都必填）：%s"
                % case.get("id")
            )
        base_docs = retrieve_knowledge_docs(store, query, topic="")
        wrong_docs = retrieve_knowledge_docs(store, query, topic=wrong_topic)
        base_topics = [str(d.metadata.get("topic") or "") for d in base_docs]
        wrong_topics = [str(d.metadata.get("topic") or "") for d in wrong_docs]
        rows.append(
            {
                "id": case.get("id"),
                "query": query,
                "expected": sorted(expected),
                "assumed_wrong_topic": wrong_topic,
                "baseline_top1": S.top_hit(base_topics[:1], expected),
                "baseline_top3": S.top_hit(base_topics, expected),
                "wrong_top1": S.top_hit(wrong_topics[:1], expected),
                "wrong_top3": S.top_hit(wrong_topics, expected),
                "wrong_got": wrong_topics,
            }
        )
    n = len(rows)
    return {
        "cases_count": n,
        "baseline_top1_rate": round(sum(1 for r in rows if r["baseline_top1"]) / n, 4) if n else None,
        "cascade_top1_hold_rate": round(sum(1 for r in rows if r["wrong_top1"]) / n, 4) if n else None,
        "cascade_top3_hold_rate": round(sum(1 for r in rows if r["wrong_top3"]) / n, 4) if n else None,
        "per_case": rows,
        "note": "软门禁（提案 §3.6）：hold_rate = topic 被换成错主题后 top1/top3 仍命中的比例。"
                "强制拼错 topic 比生产 _should_augment_with_topic 更严，量的是最坏情况。",
    }


def run_l4(
    cases_file: str,
    sample: int | None,
    base_url: str,
    pause: float,
    store=None,
    votes: int = 3,
) -> dict:
    """L4 端到端（Wave 6）：真实 POST /chat 收 SSE，量编排层行为。

    复用 gen_eval_samples 的 SSE 消费与限流实现（间隔 ≥7s、429 退避 65s 重试一次），
    不重写第二份。抽样子与 L3 相同（oos 全保留 + 固定种子），小样本复盘可对齐。

    **口径说明**：提案 §2.2 的 L4 含「答案正确性 LLM 判 0-2 分」，本实现先不接
    裁判——L3 已在生成层判过事实一致/外幻觉，L4 量的是编排层（延迟 / sources /
    state / 主题保持），重复判答案只会多花一倍调用费。主题保持用
    state.topic 与 expected 的归一化匹配做代理值，与 L1 的 top_hit 同尺。

    **资料利用率（XB-16，2026-10-25）**：sources>0 的用例再判一次利用率，
    函数复用 `eval.grounding_eval.judge_utilization`（与 L3 同一份口径）。
    SSE 的 sources 事件只带 topic/source/category 元数据、不带正文，
    所以这里用同一 query + state.topic **本地重跑一次检索**取回切片给裁判看——
    是生产注入内容的近似重建，不是逐字复现（生产当轮的候选池与本地完全同源、
    同参数，差异只可能来自 topic 抽取时序）。库外题 sources=0 不判 → 记 None。
    """
    import random
    import time

    import smoke_rag as S
    import gen_eval_samples as G

    # gen_eval_samples 的 http_json/post_chat 读模块级 BASE；显式指向本次评测的后端
    G.BASE = base_url

    raw = json.loads(Path(cases_file).read_text(encoding="utf-8"))
    cases = [c for c in raw.get("cases", []) if (c.get("kind") or "") != "cascade"]
    oos = [c for c in cases if (c.get("kind") or "") == "oos"]
    rest = [c for c in cases if (c.get("kind") or "") != "oos"]
    if sample is None:
        picked = list(cases)
    else:
        n_rest = max(sample - len(oos), 0)
        picked = oos + random.Random(RANDOM_SEED).sample(rest, min(n_rest, len(rest)))

    classroom_id = G.create_classroom("L4评测")

    # 利用率裁判按需创建：LLM_API_KEY 缺失或网络不通时不应该让整个 L4 崩掉，
    # 此时逐条记 None（符合「没判就是 None」），报告里 utilization_judged=0 会看出来。
    judge = None
    if store is not None:
        from eval.grounding_eval import judge_utilization, make_judge_llm

        try:
            judge = make_judge_llm()
        except Exception as exc:
            print("  [warn] 利用率裁判不可用，L4 利用率全部记 None：%s" % exc, flush=True)

    rows = []
    last_start = 0.0
    for index, case in enumerate(picked, 1):
        query = (case.get("query") or "").strip()
        gap = pause - (time.perf_counter() - last_start)
        if gap > 0:
            time.sleep(gap)
        last_start = time.perf_counter()

        summary = G.post_chat(classroom_id, query, timeout=SSE_TIMEOUT_SECS)
        if summary.get("status") == 429:
            print("  [%02d] 429 限流，退避 %.0fs 后重试" % (index, RATE_LIMIT_BACKOFF_SECS), flush=True)
            time.sleep(RATE_LIMIT_BACKOFF_SECS)
            summary = G.post_chat(classroom_id, query, timeout=SSE_TIMEOUT_SECS)

        expected = set(case.get("expected") or [])
        state_topic = str(summary.get("topic") or "")
        answer_text = str(summary.get("text") or "")

        # 利用率：只在「生产确实注入了资料」的条上判（sources>0），
        # 库外题（sources=0）没有可判对象 → None，不填 0（填 0 会把「没判」伪装成「没用」）。
        util_row = {"material_utilization": None, "used_indices": []}
        if judge is not None and store is not None and (summary.get("sources") or 0) > 0:
            try:
                from agent.rag.retriever import retrieve_knowledge_docs

                docs = retrieve_knowledge_docs(store, query, topic=state_topic)
                util_row = judge_utilization(judge, docs, answer_text, votes=votes)
            except Exception as exc:
                print("  [warn] 第 %02d 条利用率判定失败，记 None：%s" % (index, exc), flush=True)

        rows.append(
            {
                "id": case.get("id"),
                "kind": case.get("kind"),
                "query": query,
                "status": summary.get("status"),
                "elapsed_secs": round((summary.get("elapsed_ms") or 0) / 1000, 3),
                "sources": summary.get("sources"),
                "source_topics": summary.get("source_topics"),
                "chars": summary.get("chars"),
                "state_topic": state_topic,
                "hint_level": summary.get("hint_level"),
                "state_present": summary.get("personalized") is not None,
                "topic_match": (S.top_hit([state_topic], expected) if expected else None),
                "material_utilization": util_row["material_utilization"],
                "used_indices": util_row["used_indices"],
                "error": (summary.get("error") or "")[:200],
            }
        )
        flag = "" if summary.get("status") == 200 else "  <<< 异常"
        print(
            "  [%02d] %sms sources=%s state_topic=%r chars=%s 利用率=%s%s"
            % (
                index,
                summary.get("elapsed_ms"),
                summary.get("sources"),
                state_topic,
                summary.get("chars"),
                util_row["material_utilization"],
                flag,
            ),
            flush=True,
        )

    ok_rows = [r for r in rows if r["status"] == 200]
    scored = [r for r in rows if r["topic_match"] is not None]
    util_scored = [r for r in rows if r["material_utilization"] is not None]
    elapsed = [r["elapsed_secs"] for r in ok_rows]
    sources = [r["sources"] or 0 for r in ok_rows]
    return {
        "base_url": base_url,
        "classroom_id": classroom_id,
        "cases_count": len(rows),
        "success": len(ok_rows),
        "success_rate": round(len(ok_rows) / len(rows), 4) if rows else None,
        "e2e_p95_secs": round(p95(elapsed), 3) if elapsed else None,
        "avg_sources": round(sum(sources) / len(sources), 2) if sources else None,
        "state_present_rate": round(
            sum(1 for r in ok_rows if r["state_present"]) / len(ok_rows), 4
        )
        if ok_rows
        else None,
        "topic_match_rate": round(
            sum(1 for r in scored if r["topic_match"]) / len(scored), 4
        )
        if scored
        else None,
        # 资料利用率：与 L3 同一函数同一口径（judge_utilization），
        # 只对 sources>0 的条求均值；一条都没判出来就是 None（不是 0）
        "material_utilization": (
            round(
                sum(r["material_utilization"] for r in util_scored) / len(util_scored), 4
            )
            if util_scored
            else None
        ),
        "utilization_judged": len(util_scored),
        "per_case": rows,
        "note": "提案 §2.5：L4 不进 CI，只出报告。state_present_rate 以 personalized 键存在为准"
                "（降级流无 state，见 B_WORKLOG XB-6/XB-8）；topic_match_rate 是主题保持的代理值"
                "（state.topic 归一化匹配 expected），非提案的逐轮漂移率口径。"
                "**已知假阴来源（读数前必看）**：同义不同名（SJF=短作业优先、RR=时间片轮转）"
                "与细粒度 topic（模型抽「银行家算法」，文档级 expected 是「死锁」）都会记 False，"
                "为不引入第二套同义词口径（与 top_hit 分叉）此处不做别名映射，"
                "读报告请对照 per_case 的 state_topic 原值人工复核。"
                "**资料利用率（XB-16）**：只对 sources>0 的条判（库外题 sources=0 记 None，"
                "不填 0——填 0 会把「没判」伪装成「没用」）；SSE 的 sources 事件不带正文，"
                "切片由本地用同一 query + state.topic 重跑一次检索取得，是生产注入内容的"
                "近似重建；判定函数与 L3 共用 judge_utilization，口径一致可比。",
    }


def build_report(
    l1: dict | None,
    l2: dict | None,
    l3: dict | None,
    l4: dict | None,
    cases_file: str,
    store,
) -> dict:
    store_fp = ""
    try:
        store_fp = collection_fingerprint(store)
    except Exception:
        pass
    return {
        "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S%z"),
        "index_fingerprint": store_fp,
        "cases_file": cases_file,
        "layers": {
            # 没跑的层必须是 None（而不是 {"aggregate": None}）——
            # 门禁按「层缺失即跳过」处理，占位 dict 会被误当成"有数据"
            "L1": l1,
            "L2": l2,
            "L3": ({"aggregate": l3["aggregate"], "per_case": l3["per_case"]} if l3 else None),
            "L4": l4,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="RAG 分层评测执行器（L1/L2/L3/L4）")
    parser.add_argument("--cases", default=DEFAULT_CASES, help="评测集 JSON 路径")
    parser.add_argument("--layers", default="L1,L2,L3", help="要跑的层，逗号分隔")
    parser.add_argument("--sample", type=int, default=None, help="L3/L4 抽样条数（oos 全保留）；缺省全量")
    parser.add_argument("--votes", type=int, default=3, help="每个维度裁判投票次数")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="L4 用的后端地址")
    parser.add_argument("--pause", type=float, default=CHAT_MIN_INTERVAL_SECS, help="L4 两次 /chat 的最小间隔秒数")
    parser.add_argument("--out", default="", help="报告输出路径（缺省 eval/reports/<时间戳>.json）")
    args = parser.parse_args(argv)

    layers = [x.strip().upper() for x in args.layers.split(",") if x.strip()]
    unknown = [x for x in layers if x not in {"L1", "L2", "L3", "L4"}]
    if unknown:
        raise SystemExit("不支持的层：%s（当前支持 L1/L2/L3/L4）" % unknown)

    store = load_knowledge_index(COLLECTION, PERSIST_DIR)
    if store is None:
        raise SystemExit("知识库不存在：先跑 python build_kb.py")

    l1 = l2 = l3 = None
    if {"L1", "L2"} & set(layers):
        print("== L1/L2 检索与重排层（零 LLM 成本）==", flush=True)
        l1, l2 = run_l1_l2(store, args.cases)
        print(
            "  top1 %s/%s  nDCG@4 %s  p95 %ss  提升度 %s"
            % (l1["top1"], l1["total"], l1["ndcg_at_k"], l1["p95_secs"], l2["uplift"]),
            flush=True,
        )
    if "L3" in layers:
        print("== L3 生成层（裁判模型 temperature=0，%d 票/维度）==" % args.votes, flush=True)
        l3 = run_l3(store, args.cases, sample=args.sample, votes=args.votes)

    l4 = None
    extra_failures: list[str] = []
    if "L4" in layers:
        # cascade 不依赖后端，先跑（后端不可达时这部分成果不丢）
        print("== L4-cascade 级联评测（零 LLM 成本）==", flush=True)
        cascade = run_cascade(store, args.cases)
        print(
            "  用例 %d  top1 兜底率 %s  top3 兜底率 %s"
            % (
                cascade["cases_count"],
                cascade["cascade_top1_hold_rate"],
                cascade["cascade_top3_hold_rate"],
            ),
            flush=True,
        )
        l4 = {"cascade": cascade, "e2e": None}

        # 后端可达性先探明再跑 e2e：不可达就如实记失败，不静默跳过
        import gen_eval_samples as G

        G.BASE = args.base_url
        status, payload = G.http_json("GET", "/health")
        if status != 200:
            print("  /health 不可达（status=%s），e2e 部分未执行" % status, flush=True)
            extra_failures.append(
                "L4 e2e 未执行：/health 不可达（先起后端：env -u HTTP_PROXY -u HTTPS_PROXY "
                "-u http_proxy -u https_proxy ./venv/Scripts/python.exe -m uvicorn app:app "
                "--host 127.0.0.1 --port 8000）"
            )
        else:
            print(
                "== L4-e2e 端到端（POST /chat 真实流，间隔 ≥%ss）==" % args.pause,
                flush=True,
            )
            l4["e2e"] = run_l4(
                args.cases,
                sample=args.sample,
                base_url=args.base_url,
                pause=args.pause,
                store=store,
                votes=args.votes,
            )
            e2e = l4["e2e"]
            print(
                "  成功 %s/%s  e2e p95 %ss  平均 sources %s  state 存在率 %s  主题保持率 %s"
                "  资料利用率 %s（判出 %s 条）"
                % (
                    e2e["success"],
                    e2e["cases_count"],
                    e2e["e2e_p95_secs"],
                    e2e["avg_sources"],
                    e2e["state_present_rate"],
                    e2e["topic_match_rate"],
                    e2e["material_utilization"],
                    e2e["utilization_judged"],
                ),
                flush=True,
            )

    report = build_report(l1, l2, l3, l4, args.cases, store)

    ok, failures = evaluate_gate(report)
    failures = failures + extra_failures
    ok = ok and not extra_failures
    report["gate"] = {"passed": ok, "failures": failures}

    out_path = Path(args.out) if args.out else DEFAULT_OUT_DIR / (
        "e2e_%s.json" % datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    print("门禁判定：%s" % ("✓ 通过" if ok else "✗ 未通过"))
    for f in failures:
        print("  - %s" % f)
    print("报告已写入：%s" % out_path)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
