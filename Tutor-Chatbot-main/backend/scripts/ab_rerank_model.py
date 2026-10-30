"""重排模型/配置 A/B：同一批题、逐档独立进程、输出检索层全指标。

为什么每档必须一个进程（沿用 `ab_recall_pool.py` 的结论）：
`R.CONFIG` 是模块级全局，`get_reranker()` 是 `lru_cache(maxsize=1)` 单例。
同进程里连跑两档，第二档会拿到第一档加载的编码器，延迟与分数全部串档。
所以本脚本一次只跑**一档**，由外部 shell/循环起多次进程。

用法（backend/ 下）：

    # 现状档 B1
    RAG_PARENT_CHILD=1 venv/Scripts/python.exe scripts/ab_rerank_model.py \
        --tag B1 --model BAAI/bge-reranker-v2-m3-int8 --rerank-text child_title

    # 对照档：换模型（权重必须已在 .model_cache 里，本机网络不通）
    RAG_PARENT_CHILD=1 venv/Scripts/python.exe scripts/ab_rerank_model.py \
        --tag A2 --model jinaai/jina-reranker-v2-base-multilingual

    # B0 无重排
    RAG_PARENT_CHILD=1 venv/Scripts/python.exe scripts/ab_rerank_model.py \
        --tag B0 --no-rerank

    # 配置维度（R10：分离「模型判别力」与「运行时/配置」）
    RAG_PARENT_CHILD=1 venv/Scripts/python.exe scripts/ab_rerank_model.py \
        --tag C_parenthead --rerank-text parent_head --head-chars 480

输出：JSON 落 `--out`（缺省 eval/reports/rerank_ab_<tag>_<时间戳>.json），
同时打印一行 `RESULT {...}` 供上层脚本解析。

指标口径与 `eval/metrics.py` 完全一致（chunk_rel / p95），不新造轮子。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

import smoke_rag as S  # noqa: E402
from agent.rag import retriever as R  # noqa: E402
from agent.rag.indexer import load_knowledge_index  # noqa: E402
from eval.metrics import chunk_rel, p95  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CASES = str(BACKEND_DIR / "rag_eval_cases.json")
PERSIST_DIR = str(BACKEND_DIR / "chroma_kb")
COLLECTION = "cs_basics"
REPORT_DIR = BACKEND_DIR / "eval" / "reports"

# 方案 §4.3 锁定的双基线。换评测集/扩库后必须同步更新，否则差值无意义。
BASELINES = {
    "B0": {  # 无重排（RAG_RERANK=0），2026-10-28 实测
        "recall_at_k": 0.7909,
        "precision_at_k": 0.5634,
        "mrr": 0.9648,
        "ndcg_at_k": 0.8161,
        "top1": 63,
        "total": 71,
        "noise_rate": None,
        "rerank_p95_ms": 0.0,
        "l1_p95_secs": 1.16,
    },
    "B1": {  # 现状 bge-reranker-v2-m3-int8 + child_title，2026-10-28 实测
        "recall_at_k": 0.7909,
        "precision_at_k": 0.5634,
        "mrr": 0.9648,
        "ndcg_at_k": 0.8700,
        "top1": 68,
        "total": 71,
        "noise_rate": 0.3052,
        "rerank_p95_ms": 1082.0,
        "l1_p95_secs": 1.1027,
    },
}


def _mean(xs):
    return round(statistics.mean(xs), 4) if xs else None


def _apply_config(args) -> None:
    """把 CLI 参数写进 retriever 的全局 CONFIG。

    只在 `--no-rerank` 时改 `enable_rerank`；其余一律**只改被显式传入的字段**，
    保证「一次只变一个变量」。

    `--onnx-dir` 走本地目录直载（scripts/onnx_xenc.py），用于 Hub 不可达时
    仍能评测新模型；它覆盖 `get_reranker()` 单例，因此必须放在 `cache_clear()`
    之后，否则会把我们装的编码器清掉。
    """
    if args.no_rerank:
        R.CONFIG.enable_rerank = False
    if args.model:
        R.CONFIG.rerank_model = args.model
    if args.rerank_text:
        R.CONFIG.rerank_text = args.rerank_text
    if args.head_chars is not None:
        R.CONFIG.rerank_head_chars = args.head_chars
    if args.pool_k is not None:
        R.CONFIG.parent_pool_k = args.pool_k
    if args.overfetch is not None:
        R.CONFIG.child_overfetch = args.overfetch
    if args.batch is not None:
        R.CONFIG.rerank_batch = args.batch
    # 单例必须清：否则换 model 后还是旧的编码器（静默串档，最难查的一类错）
    R.get_reranker.cache_clear()
    if args.onnx_dir:
        from scripts.onnx_xenc import load_local_cross_encoder

        enc = load_local_cross_encoder(args.onnx_dir, threads=args.threads)
        R.CONFIG.rerank_model = enc.name
        R.get_reranker = lambda: enc  # noqa: E731 — 覆盖单例，绕开 fastembed
        print(f"INFO 本地 ONNX 直载：{enc.name}（图={enc.onnx_path.name}）", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="重排模型/配置 A/B（单档单进程）")
    ap.add_argument("--tag", required=True, help="档位标签，用于报告命名")
    ap.add_argument("--cases", default=DEFAULT_CASES)
    ap.add_argument("--model", default=None, help="fastembed 支持的 rerank 模型名")
    ap.add_argument("--onnx-dir", default=None,
                    help="本地模型目录（含 onnx/*.onnx 与 tokenizer.json）；"
                         "走 scripts/onnx_xenc.py 直载，不查 Hub")
    ap.add_argument("--threads", type=int, default=None,
                    help="ONNX Runtime intra_op 线程数（仅 --onnx-dir 生效）")
    ap.add_argument("--rerank-text", default=None,
                    choices=["parent", "child", "child_title", "parent_head"])
    ap.add_argument("--head-chars", type=int, default=None)
    ap.add_argument("--pool-k", type=int, default=None, help="0 = 不限")
    ap.add_argument("--overfetch", type=int, default=None)
    ap.add_argument("--batch", type=int, default=None)
    ap.add_argument("--repeat", type=int, default=1,
                    help="每题重复次数，延迟取中位；指标取首次（同题指标应确定）")
    ap.add_argument("--no-rerank", action="store_true", help="跑 B0 基线")
    ap.add_argument("--parent-child", action="store_true", default=None,
                    help="强制父子模式（默认跟随 RAG_PARENT_CHILD 环境变量）")
    ap.add_argument("--hardneg", default=str(BACKEND_DIR / "eval" / "cases_rerank_v2.json"),
                    help="带 hard_negatives 标注的评测集（缺失则不算该指标）")
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)

    # 硬负例标注按 query 索引：主评测集保持不动，标注单独挂一层，
    # 这样 B0/B1 与新档位始终跑同一批题，指标可比。
    hn_by_query: dict[str, set[str]] = {}
    hn_path = Path(args.hardneg)
    if hn_path.exists():
        try:
            hd = json.loads(hn_path.read_text(encoding="utf-8"))
            for c in hd.get("cases") or []:
                q = (c.get("query") or "").strip()
                negs = c.get("hard_negatives") or []
                if q and negs:
                    hn_by_query[q] = set(str(x) for x in negs)
        except Exception as exc:  # noqa: BLE001 — 标注缺失不该阻断评测
            print(f"WARN 读取硬负例标注失败，跳过该指标：{exc}", file=sys.stderr)

    if args.parent_child:
        R.CONFIG.parent_child = True
    if not R.CONFIG.parent_child:
        print("WARN 当前不是父子模式：本脚本的指标口径按 parent_final_k 计分",
              file=sys.stderr)

    _apply_config(args)
    inject_k = S.injected_k()

    S.CASES = S.load_cases(args.cases)
    store = load_knowledge_index(COLLECTION, PERSIST_DIR)

    # 预热：模型加载不进单题耗时（与 ab_recall_pool 一致）
    R.get_knowledge_context(store, "预热", k=1)

    rels, secs, rerank_ms, injected, pools = [], [], [], [], []
    top1 = top3 = total = degraded = rerank_fail = 0
    hn_hits = hn_slots = hn_cases = 0

    for query, expected, _kind, topic, relevant in S.CASES:
        if not relevant:
            continue  # oos 题不计分（与既有口径一致）
        first_rel = None
        for rep in range(max(1, args.repeat)):
            trace: dict = {}
            t0 = time.perf_counter()
            docs = R.retrieve_knowledge_docs(store, query, topic=topic, trace=trace)
            dt = time.perf_counter() - t0

            if rep == 0:
                first_rel = chunk_rel(docs, relevant, expected, k=inject_k)
                pools.append(trace.get("parents_k") or 0)
                injected.append(len(docs))
                if trace.get("degraded"):
                    degraded += 1
                # 只在重排本该生效的档位统计失败；B0（--no-rerank）不算失败
                if R.CONFIG.enable_rerank and trace.get("rerank") not in (
                    None, "ok", "skipped"
                ):
                    rerank_fail += 1
                if trace.get("rerank") == "ok" and trace.get("rerank_ms") is not None:
                    rerank_ms.append(float(trace["rerank_ms"]))
                topics = [str(d.metadata.get("topic") or "") for d in docs]
                if expected:
                    total += 1
                    top1 += 1 if S.top_hit(topics[:1], expected) else 0
                    top3 += 1 if S.top_hit(topics, expected) else 0
                # 硬负例误注入：注入条里有多少是「整节零增益」的节
                hn = hn_by_query.get(query)
                if hn and docs:
                    pid_list = [str(d.metadata.get("parent_id") or "") for d in docs]
                    hn_hits += sum(1 for p in pid_list if p in hn)
                    hn_slots += len(pid_list)
                    hn_cases += 1
            secs.append(dt)
        rels.append(first_rel)

    scored = len(rels)
    out = {
        "tag": args.tag,
        "ts": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "config": {
            "model": R.CONFIG.rerank_model,
            "enable_rerank": R.CONFIG.enable_rerank,
            "rerank_text": R.CONFIG.rerank_text,
            "head_chars": R.CONFIG.rerank_head_chars,
            "pool_k": R.CONFIG.parent_pool_k,
            "overfetch": R.CONFIG.child_overfetch,
            "batch": R.CONFIG.rerank_batch,
            "parent_child": R.CONFIG.parent_child,
            "inject_k": inject_k,
            "relevance_floor": R.CONFIG.relevance_floor,
            "per_doc_floor": R.CONFIG.per_doc_floor,
        },
        "cases": scored,
        "recall_at_k": _mean([r.recall for r in rels]),
        "precision_at_k": _mean([r.precision for r in rels]),
        "noise_rate": _mean([r.noise_rate for r in rels]),
        # 硬负例误注入率：只在有标注的题上算，分母是这些题的注入条数
        "hard_neg_rate": round(hn_hits / hn_slots, 4) if hn_slots else None,
        "hard_neg_cases": hn_cases,
        "mrr": _mean([r.mrr for r in rels]),
        "ndcg_at_k": _mean([r.ndcg for r in rels]),
        "top1": top1,
        "top3": top3,
        "total": total,
        "mean_parent_pool": _mean(pools),
        "mean_injected": _mean(injected),
        "rerank_p95_ms": round(p95(rerank_ms), 1) if rerank_ms else 0.0,
        "rerank_mean_ms": _mean(rerank_ms),
        "avg_secs": _mean(secs),
        "l1_p95_secs": round(p95(secs), 4) if secs else 0.0,
        "degraded_cases": degraded,
        "degraded_rate": round(degraded / scored, 4) if scored else 0.0,
        "rerank_fail_cases": rerank_fail,
    }

    # —— 与基线比：差值符号按「越大越好」统一，负数=劣化
    deltas = {}
    for name, base in BASELINES.items():
        d = {}
        for key in ("recall_at_k", "precision_at_k", "mrr", "ndcg_at_k"):
            if out.get(key) is not None and base.get(key) is not None:
                d[key] = round(out[key] - base[key], 4)
        if base.get("rerank_p95_ms"):
            d["rerank_p95_ms"] = round(out["rerank_p95_ms"] - base["rerank_p95_ms"], 1)
        deltas[name] = d
    out["delta_vs"] = deltas

    # —— 方案 §6 P1 的三条硬判定
    gates = {
        "beats_b0_ndcg": (out["ndcg_at_k"] or 0) > BASELINES["B0"]["ndcg_at_k"],
        "recall_ge_b1_plus_1_5pp": (out["recall_at_k"] or 0) >= 0.8059,
        "latency_within_safe_line": out["rerank_p95_ms"] <= 2160,
        "l1_p95_within_3s": out["l1_p95_secs"] <= 3.0,
        "degraded_rate_ok": out["degraded_rate"] <= 0.02,
    }
    gates["passed"] = all(gates.values())
    out["gates"] = gates

    out_path = Path(args.out) if args.out else (
        REPORT_DIR / f"rerank_ab_{args.tag}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print("RESULT " + json.dumps(out, ensure_ascii=False))
    print(f"报告已写入 {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
