"""检索准确率「天花板归因」：把 Recall@3 / nDCG@3 的损失拆成两块。

为什么需要它
------------
换重排模型已经摸到边际收益（B1 nDCG 0.8700，五候选里最高）。要回答
「还能不能再涨」，必须先知道剩下的 13pp 到底卡在哪一级，而不是继续试模型：

    loss_retrieval（召回层漏）  = 1            − oracle_recall
        gold 小节**根本没进 parent 候选池**。再好的重排也看不见它，
        只能靠改召回（扩候选 / 换向量 / 加 BM25 权重 / 换切分）。
    loss_rerank（重排层排错）   = oracle_recall − actual_recall
        gold 在池里，但被排到注入窗口之外。理论上换个更强的重排能救，
        这是**重排模型的真实剩余空间**。
    achieved                   = actual_recall
        当前实际拿到手的。

Oracle 的定义（重要，别误读）
----------------------------
「上帝视角」= 已知每条候选的真实增益，从**同一个候选池**里贪心取最优的
inject_k 条。它只解除「排序」这一个约束，**不解除召回约束**（池外的文档
依然拿不到），也不解除 K=3 的窗口约束。所以 oracle 不是全局上限，
而是「在给定候选池 + 给定注入条数下，排序能做到最好是多少」。

用法（backend/ 下，单档单进程）
------------------------------
    RAG_PARENT_CHILD=1 venv/Scripts/python.exe scripts/diag_acc_ceiling.py \
        --tag POOL1
    RAG_PARENT_CHILD=1 venv/Scripts/python.exe scripts/diag_acc_ceiling.py \
        --tag POOL3 --overfetch 3
"""

from __future__ import annotations

import argparse
import json
import os
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
from eval.metrics import chunk_gains, chunk_rel, p95  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CASES = str(BACKEND_DIR / "rag_eval_cases.json")
PERSIST_DIR = str(BACKEND_DIR / "chroma_kb")
COLLECTION = "cs_basics"
REPORT_DIR = BACKEND_DIR / "eval" / "reports"


def _mean(xs):
    return round(statistics.mean(xs), 4) if xs else None


def _install_pool_hook() -> dict:
    """包一层 `_rerank_docs`，把每次送进重排的候选池原样捞出来。

    用 monkeypatch 而不是改 retriever 源码：归因是一次性诊断，不该给生产
    链路加字段。钩子只存 candidates 的引用（不复制正文），开销可忽略。

    取池的方式是「最后一次调用」而不是按 query 文本索引：查询改写开启时，
    送进重排的文本不等于原始 query，按文本索引会静默取空。
    """
    orig = R._rerank_docs
    box: dict = {"last_query": None, "last_pool": []}

    def wrapper(query, candidates, want, trace=None):
        box["last_query"] = query
        box["last_pool"] = list(candidates)
        return orig(query, candidates, want, trace)

    R._rerank_docs = wrapper
    return box


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="检索准确率天花板归因")
    ap.add_argument("--tag", required=True)
    ap.add_argument("--cases", default=DEFAULT_CASES)
    ap.add_argument("--overfetch", type=int, default=None,
                    help="child 超额召回倍数（放大 parent 候选池）")
    ap.add_argument("--pool-k", type=int, default=None,
                    help="parent 候选池截断条数（0=不限）。RRF 序截断=廉价的粗排，"
                         "与 --overfetch 组合即最小改动的两段式检索")
    ap.add_argument("--inject-k", type=int, default=None,
                    help="注入条数上限（缺省跟随 S.injected_k()）")
    ap.add_argument("--onnx-dir", default=None,
                    help="本地 ONNX 目录（含 onnx/*.onnx + tokenizer.json）；"
                         "走 scripts/onnx_xenc.py 直载，不查 Hub")
    ap.add_argument("--threads", type=int, default=None,
                    help="ONNX Runtime intra_op 线程数（仅 --onnx-dir 生效）")
    ap.add_argument("--no-rerank", action="store_true",
                    help="关重排跑归因（用于分离「召回层」自身的上限）")
    ap.add_argument("--rerank-text", default=None,
                    choices=["parent", "child", "child_title", "parent_head"])
    ap.add_argument("--head-chars", type=int, default=None,
                    help="送进重排的正文长度上限（仅 rerank_text=parent_head 生效）")
    ap.add_argument("--adjacent-radius", type=int, default=None,
                    help="相邻小节扩展半径（O13）：按 parent 序号补同文档 ±N 节进候选池")
    ap.add_argument("--fusion-w", type=float, default=None,
                    help="重排名次与 RRF 名次的融合权重（0=纯重排）")
    ap.add_argument("--persist-dir", default=PERSIST_DIR,
                    help="向量库目录；换 embedding 时用独立目录，别动现库")
    ap.add_argument("--embed-model", default="",
                    help="粗排 embedding 模型名。给定时改用本地直载，绕过 langchain 的 "
                         "FastEmbedEmbeddings——那个封装不传 local_files_only，离线必失败")
    ap.add_argument("--build", action="store_true",
                    help="先全量重建索引（换 embedding 时必须重建）：父子进程 dear 待定")
    ap.add_argument("--knowledge-dir", default=str(BACKEND_DIR.parent.parent / "knowledge"),
                    help="--build 时的知识目录")
    ap.add_argument("--hardneg",
                    default=str(BACKEND_DIR / "eval" / "cases_rerank_v2.json"),
                    help="带 hard_negatives 标注的评测集（缺失则不算该指标）")
    ap.add_argument("--dump-per-case", action="store_true",
                    help="把每题明细写进报告（体积大，排查单题时开）")
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)

    # 硬负例标注按 query 索引：主评测集保持不动，标注单独挂一层，
    # 这样各档位始终跑同一批题，指标可比。
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

    if args.overfetch is not None:
        R.CONFIG.child_overfetch = args.overfetch
    if args.pool_k is not None:
        R.CONFIG.parent_pool_k = args.pool_k
    if args.rerank_text:
        R.CONFIG.rerank_text = args.rerank_text
    if args.head_chars is not None:
        R.CONFIG.rerank_head_chars = args.head_chars
    if args.adjacent_radius is not None:
        R.CONFIG.adjacent_radius = args.adjacent_radius
    if args.fusion_w is not None:
        R.CONFIG.rerank_fusion_w = args.fusion_w
    if args.no_rerank:
        R.CONFIG.enable_rerank = False
    R.get_reranker.cache_clear()
    if args.onnx_dir:
        from scripts.onnx_xenc import load_local_cross_encoder

        enc = load_local_cross_encoder(args.onnx_dir, threads=args.threads)
        R.CONFIG.rerank_model = enc.name
        R.get_reranker = lambda: enc  # noqa: E731 — 覆盖单例，绕开 fastembed
        print(f"INFO 本地 ONNX 直载：{enc.name}", file=sys.stderr)

    # 换粗排模型时才需要这一步。langchain 的 FastEmbedEmbeddings 不传
    # local_files_only（默认 False），断网时必然抛 "Could not load model"，
    # 所以这里用一个本地直载的薄封装替换 indexer.get_embeddings。
    # get_embeddings 是模块级名字查找 + lru_cache，覆盖模块属性即可生效
    # （与前面覆盖 R.get_reranker 同一套路）。
    if args.embed_model:
        import agent.rag.indexer as IDX
        from langchain_core.embeddings import Embeddings

        class _LocalEmbed(Embeddings):
            """直接持有 fastembed.TextEmbedding，只为能传 local_files_only。"""

            def __init__(self, name: str, cache: str) -> None:
                from fastembed import TextEmbedding

                self._enc = TextEmbedding(
                    model_name=name, cache_dir=cache, local_files_only=True
                )
                self.model_name = name

            def _run(self, texts):
                return [list(map(float, v)) for v in self._enc.embed(list(texts))]

            def embed_documents(self, texts: list[str]):
                return self._run(texts)

            def embed_query(self, text: str):
                return self._run([text])[0]

        _emb = _LocalEmbed(args.embed_model, os.getenv("FASTEMBED_CACHE_PATH") or "")
        IDX.get_embeddings = lambda: _emb
        print(f"INFO 粗排 embedding：{args.embed_model}（本地直载）", file=sys.stderr)

    if args.build:
        from agent.rag.indexer import build_knowledge_index

        built = build_knowledge_index(
            knowledge_dir=args.knowledge_dir,
            collection_name=COLLECTION,
            persist_directory=args.persist_dir,
            parent_child=True,
        )
        print(f"INFO 已重建索引 {args.persist_dir}  切片数={built._collection.count()}",
              file=sys.stderr)

    inject_k = args.inject_k or S.injected_k()
    if args.inject_k:
        # 必须显式传给检索：这里的 k 是**最终注入条数**，不是计分窗口宽度。
        # 只改计分不改注入会让 K=4 实验退化成「拿 4 条窗口给 3 条结果打分」。
        R.CONFIG.parent_final_k = args.inject_k
    S.CASES = S.load_cases(args.cases)
    store = load_knowledge_index(COLLECTION, args.persist_dir)

    captured = _install_pool_hook()
    # 预热：模型/索引加载不进单题耗时
    R.get_knowledge_context(store, "预热", k=1)

    rows = []
    secs, rerank_ms = [], []
    acc = {"top1": 0, "top3": 0, "total": 0, "hn_hits": 0, "hn_slots": 0}

    for query, expected, _kind, topic, relevant in S.CASES:
        if not relevant:
            continue  # oos 题不计分，与既有口径一致

        captured["last_pool"] = []
        trace: dict = {}
        t0 = time.perf_counter()
        docs = R.retrieve_knowledge_docs(
            store, query, k=inject_k, topic=topic, trace=trace
        )
        secs.append(time.perf_counter() - t0)

        # 重排被跳过（--no-rerank / floor 全砍）时钩子不会触发 ⇒ 空池。
        # 这类题在归因里全部计入召回层损失，正是我们想要的口径。
        pool = captured["last_pool"]

        gains_pool = chunk_gains(pool, relevant, expected)
        gold_total = _gold_total(relevant)

        # Oracle：池内按真实增益降序取前 inject_k 条。
        # 增益相同的并列（多为 0.5 的 topic 档）保持原池顺序，稳定可复现。
        order = sorted(range(len(pool)), key=lambda i: (-gains_pool[i], i))
        oracle_docs = [pool[i] for i in order[:inject_k]]

        actual = chunk_rel(docs, relevant, expected, k=inject_k)
        oracle = chunk_rel(oracle_docs, relevant, expected, k=inject_k)
        pool_all = chunk_rel(pool, relevant, expected, k=len(pool) or 1)

        rr = trace.get("rerank_ms")
        if rr is not None:
            rerank_ms.append(float(rr))

        rows.append({
            "query": query[:60],
            "expected": sorted(expected or []),
            "gold_n": len(relevant or []),
            "gold_gain_sum": round(gold_total, 3),
            "pool_n": len(pool),
            "pool_hit_n": sum(1 for g in gains_pool if g > 0),
            "pool_recall": round(pool_all.recall, 4),
            "oracle_recall": round(oracle.recall, 4),
            "actual_recall": round(actual.recall, 4),
            "oracle_ndcg": round(oracle.ndcg, 4),
            "actual_ndcg": round(actual.ndcg, 4),
            "oracle_precision": round(oracle.precision, 4),
            "actual_precision": round(actual.precision, 4),
            "injected_n": len(docs),
            "loss_retrieval": round(1 - oracle.recall, 4),
            "loss_rerank": round(oracle.recall - actual.recall, 4),
            "hard_miss": int(sum(1 for g in gains_pool if g > 0) == 0),
            "noise_rate_actual": round(actual.noise_rate, 4),
            "noise_rate_oracle": round(oracle.noise_rate, 4),
        })

        # Top1/Top3 与既有报告同口径（S.top_hit），便于横向对比
        topics_txt = [str(d.metadata.get("topic") or "") for d in docs]
        if expected:
            acc["top1"] += 1 if S.top_hit(topics_txt[:1], expected) else 0
            acc["top3"] += 1 if S.top_hit(topics_txt, expected) else 0
            acc["total"] += 1
        # 硬负例误注入率：分母是这些题的实际注入条数
        hn = hn_by_query.get(query)
        if hn and docs:
            pid_list = [str(d.metadata.get("parent_id") or "") for d in docs]
            acc["hn_hits"] += sum(1 for p in pid_list if p in hn)
            acc["hn_slots"] += len(pid_list)

    def col(key):
        return [r[key] for r in rows]

    out = {
        "tag": args.tag,
        "ts": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "config": {
            "model": R.CONFIG.rerank_model,
            "enable_rerank": R.CONFIG.enable_rerank,
            "rerank_text": R.CONFIG.rerank_text,
            "child_overfetch": R.CONFIG.child_overfetch,
            "parent_pool_k": R.CONFIG.parent_pool_k,
            "inject_k": inject_k,
            "source_quota": R.CONFIG.source_quota,
        },
        "cases": len(rows),
        "mean_pool_n": _mean(col("pool_n")),
        "mean_gold_n": _mean(col("gold_n")),
        "mean_pool_hit_n": _mean(col("pool_hit_n")),
        "hard_miss_cases": sum(col("hard_miss")),
        "top1": acc["top1"],
        "top3": acc["top3"],
        "total": acc["total"],
        "noise_rate": _mean(col("noise_rate_actual")),
        "noise_rate_oracle": _mean(col("noise_rate_oracle")),
        "hard_neg_rate": (round(acc["hn_hits"] / acc["hn_slots"], 4)
                          if acc["hn_slots"] else None),
        # —— 三层阶梯：整池全塞 / 池内最优 K 条 / 实际注入 K 条 ——
        "recall_pool_all": _mean(col("pool_recall")),
        "recall_oracle": _mean(col("oracle_recall")),
        "recall_actual": _mean(col("actual_recall")),
        "ndcg_oracle": _mean(col("oracle_ndcg")),
        "ndcg_actual": _mean(col("actual_ndcg")),
        "precision_oracle": _mean(col("oracle_precision")),
        "precision_actual": _mean(col("actual_precision")),
        "achieved": _mean(col("actual_recall")),
        "loss_rerank": _mean(col("loss_rerank")),
        "loss_retrieval": _mean(col("loss_retrieval")),
        "rerank_headroom_pp": round(
            (_mean(col("oracle_ndcg")) - _mean(col("actual_ndcg"))) * 100, 2
        ),
        "latency": {
            "rerank_p95_ms": round(p95(rerank_ms), 1) if rerank_ms else 0.0,
            "retrieval_p95_ms": round(p95([s * 1000 for s in secs]), 1),
        },
    }
    if args.dump_per_case:
        out["per_case"] = rows

    # —— 损失三段分解（三段相加 = 1 − recall_actual）——
    # 1) 召回损失：gold 小节压根没进 parent 候选池 ⇒ 再好的排序也看不见它
    # 2) 窗口损失：gold 在池里，但 inject_k 个坑装不下（K 太小 / gold 条数太多）
    # 3) 排序损失：gold 在池里也装得下，却被排到窗口外 ⇒ 这才是重排的真实空间
    # 扩池只会把 1) 兑换成 2)+3)，所以看这三项的**结构变化**比看 Recall 总数有用。
    out["loss_breakdown"] = {
        "recall_loss_pp": round((1 - out["recall_pool_all"]) * 100, 2),
        "window_loss_pp": round((out["recall_pool_all"] - out["recall_oracle"]) * 100, 2),
        "ranking_loss_pp": round((out["recall_oracle"] - out["recall_actual"]) * 100, 2),
        "sum_pp": round((1 - out["recall_actual"]) * 100, 2),
    }

    out_path = Path(args.out) if args.out else (
        REPORT_DIR / f"ceiling_{args.tag}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print("RESULT " + json.dumps({k: v for k, v in out.items() if k != "per_case"},
                                 ensure_ascii=False))
    print(f"归因报告已写入 {out_path}")
    return 0


def _gold_total(relevant) -> float:
    """金标准增益总和（chunk_rel 里 recall 的分母）。"""
    total = 0.0
    for item in relevant or []:
        if not item or len(item) < 2:
            continue
        try:
            total += float(item[2]) if len(item) > 2 else 1.0
        except (TypeError, ValueError):
            total += 1.0
    return total


if __name__ == "__main__":
    raise SystemExit(main())
