"""T9.2 RAG 检索相关性测试 + 配置对比基线。

用法（在 backend/ 目录下，用 venv 的 python）：
    python smoke_rag.py                          # 跑全部配置并对比
    python smoke_rag.py --only full              # 只跑某一种配置（看每题明细）
    python smoke_rag.py --save-base base.json    # 存当前 full 配置结果为基线
    python smoke_rag.py --compare base.json      # 与基线对比，退化则非 0 退出
    python smoke_rag.py --cases other.json       # 换一份评测集（扩库后按规模各存一份）
    python smoke_rag.py --uplift                 # 重排提升度：同一套题跑 rerank on/off
    python smoke_rag.py --fingerprint            # 打印当前索引指纹

不打 LLM、不占 /chat 限流、不花额度——只做「问题 → 检索 → 是否命中」这一层。
检索不准的话，后面接什么模型都白搭，所以这一层要能单独测、能对比、能进 CI。

E2（PROPOSAL_RAG_QUALITY_EVAL）：除 top1/top3 外新增切片级指标
Recall@k / Precision@k / MRR / nDCG@k / 检索 p95。
**k = 实际注入条数**（`injected_k()`）：扁平模式 4、父子模式 `parent_final_k`=3。
**指标口径看 eval/metrics.py**：金标准分两档，汇总时分组报告、不混算——
  1. `gold_source: topic-derived`（71 条计分）：金标准 = 期望主题的切片全集，
     由 `python -m eval.build_gold` 从文档主题标签派生，分母已知。
     它度量「top-k 对主题切片全集的覆盖」，**不度量**「该片是否真含答案」。
     当前库是父子结构，金标准按 parent 派生（每题 4 个 parent，min 4 / 中位 4 / max 9），
     注入窗口只有 3 条。**但这不等于 Recall 封顶 0.75**：金标准里的 0 分片不进
     Recall 的分母，正增益片 ≤ k 时仍可接近 1.0（实测 0.794 已超过那个假上限）。
     跨粒度/跨库对比一律看 nDCG，别拿 Recall 直接和历史扁平口径比。
  2. 无标注（4 条 oos 库外题）：recall/precision 不进已标注组。
top1/top3 走 topic 匹配、不依赖坐标，**跨粒度/跨库仍可比**；nDCG 也不受分母
灌水影响，是换粒度后唯一适合做纵向对比的排序指标。
用 `EVAL_METRICS=0` 可关掉新指标，退回纯 top1/top3。

题目在 rag_eval_cases.json，不在代码里：扩库时题目要跟着扩，
否则新知识点没有对应题目，检索退化了也看不出来。

四种配置（对应 RAG 改造的每一步）：
    baseline  纯向量 k=4                      —— 改造前的原始行为
    hybrid    向量 + BM25 + RRF，k=6 取 4
    rerank    向量 k=6 → 重排取 4
    full      hybrid + rerank                —— 当前默认链路
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/（脚本已移入 scripts/）

from dotenv import load_dotenv

load_dotenv()

import agent.rag.retriever as R
from agent.rag.indexer import load_knowledge_index
from eval.metrics import aggregate, chunk_rel, p95
from eval.fingerprint import collection_fingerprint, load_baseline_meta, check_fingerprint

# 新指标默认开；EVAL_METRICS=0 退回纯 top1/top3（PROPOSAL §8 的回退开关）
METRICS_ON = os.getenv("EVAL_METRICS", "1").strip() != "0"

BACKEND_DIR = Path(__file__).resolve().parents[1]  # backend/（脚本已移入 scripts/）
PERSIST_DIR = str(BACKEND_DIR / "chroma_kb")
COLLECTION = "cs_basics"

# 计划文档里 T9.2 写的是 HashMap / JVM / ConcurrentHashMap。
# 知识库范围已确定为「操作系统 + 计算机网络」（不做 Java 八股），
# 所以这里换成 OS / 计网题目，语义位置等价。
#
# 题目本身放在 rag_eval_cases.json，不写在代码里：
# 扩库时必须能把题目一起扩，否则新知识点没有对应题目，检索退化了也看不出来。
# 格式说明见该 JSON 的 note 字段；判定口径是「归一化后子串匹配」。
CASES_FILE = BACKEND_DIR / "rag_eval_cases.json"

# 运行时由 load_cases() 填充，元素为 (query, expected_set, kind, topic)
CASES: list[tuple] = []


def load_cases(path: str = "") -> list[tuple]:
    """从 JSON 读取评测集，转回 (query, expected, kind, topic, relevant_chunks)。

    topic 是该轮之前评估节点抽出的知识点，用来覆盖「省略式追问」场景
    （"它有什么缺点？" 单看没有主题词，必须靠 topic 补全才检索得对）。
    评测集里不写 topic 的用例按第一轮直问处理。

    relevant_chunks 是**切片级金标准**，元素 [source, start_index]（可带第三个
    元素作为自定义增益）。当前 71 条计分用例带 `gold_source: topic-derived`
    派生金标准（`python -m eval.build_gold` 产出），4 条 oos 库外题无标注。
    该字段必须保持向后兼容：缺失即按 None 处理（指标退化为 topic 级代理，
    见 eval/metrics.py），旧评测集不用改也能跑。
    """
    p = Path(path) if path else CASES_FILE
    if not p.exists():
        # 报告 JSON 里的 `cases_file` 记的是「生成报告那台机器」的绝对路径：
        # 宿主机上跑出来的是 D:\...\backend\rag_eval_cases.json，容器里没有 D 盘，
        # 照原路径找必然落空 —— /evaluation/metrics 因此在容器里恒 500。
        # 文件名本身与机器无关，所以回落到仓库内的同名文件：
        # 宿主机路径存在时走原路径（行为不变），容器里才走回落。
        #
        # 反斜杠要先换成正斜杠：POSIX 下反斜杠不是路径分隔符，直接取 .name
        # 会拿到整条 Windows 路径而不是文件名。
        tail = Path(str(p).replace("\\", "/")).name
        fallback = BACKEND_DIR / tail
        if not fallback.exists():
            raise SystemExit(
                "找不到评测集：%s\n（题目已从代码移到 JSON，见项目根 docs/proposals/PROPOSALS.md 第 5 节）" % p
            )
        p = fallback

    data = json.loads(p.read_text(encoding="utf-8"))
    cases = []
    for item in data.get("cases", []):
        query = (item.get("query") or "").strip()
        if not query:
            raise SystemExit("评测集里有空 query：%s" % item)
        if item.get("kind") == "cascade":
            # cascade 层是级联失败评测（Q6），只给 eval/e2e_runner.run_cascade 用：
            # 它会把检索语句的 topic 强制替换成 assumed_wrong_topic 跑两遍，
            # 混进这里会把 L1 题集口径从 75 条悄悄变 83 条，
            # "top1 68/71(75题)" 的分数口径就断了（硬约束：引用分数必须带题集）。
            continue
        cases.append(
            (
                query,
                set(item.get("expected") or []),
                item.get("kind", "in-kb"),
                (item.get("topic") or "").strip(),
                item.get("relevant_chunks") or None,
            )
        )

    if not cases:
        raise SystemExit("评测集为空：%s" % p)
    return cases

# (名字, 重排开关, 混合开关, 粗召回 k, 最终 k)
# baseline 必须保持 4：它就是改造前"向量检索 k=4 直接用"的原始行为，
# 是后面三档的对照基线，不能跟着默认值一起改。
CONFIGS = [
    ("baseline", False, False, 4, 4),
    ("hybrid", False, True, 6, 4),
    ("rerank", True, False, 6, 4),
    ("full", True, True, 6, 4),
]


def norm(s: str) -> str:
    """归一化：去空格、转小写。保留斜杠以外的排版差异。"""
    return s.replace(" ", "").lower()


def top_hit(topics, expected) -> bool:
    """任一条召回的 topic 包含任一个期望子串即算命中。

    两边都要归一化：期望写成 "tcp 与 udp"（带空格），文档 topic 是
    "TCP 与 UDP"，只归一一边会永远匹配不上。
    """
    keys = [norm(e) for e in expected]
    return any(k in norm(t) for t in topics for k in keys)


def injected_k() -> int:
    """本轮实际注入条数，供 precision 的分母用。

    必须跟 `retrieve_knowledge_docs` 的 `default_k` 同源：父子模式注入
    `parent_final_k`（默认 3），扁平模式注入 `final_k`（CONFIGS 里是 4）。
    写死某个值会让 precision 的分母与真实返回值脱节——父子模式下用 4 当分母、
    实际只返回 3 条，precision 被系统性压低 25%，看起来像"重排变差了"。
    """
    if R.CONFIG.parent_child:
        return R.CONFIG.parent_final_k
    return R.CONFIG.final_k


def hits_label(topics):
    return " / ".join(topics) if topics else "（无）"


def configure(name: str) -> None:
    """切换配置。retriever 的函数体统一读 R.CONFIG，改它的字段即生效。

    注意：字段改的是同一个 CONFIG 对象，不能替换成新副本——否则函数体
    读到的和这里改的不是同一个对象，配置会静默失效。
    """
    for cfg_name, rerank, hybrid, coarse_k, final_k in CONFIGS:
        if cfg_name == name:
            R.CONFIG.enable_rerank = rerank
            R.CONFIG.enable_hybrid = hybrid
            R.CONFIG.coarse_k = coarse_k
            R.CONFIG.final_k = final_k
            R.get_reranker.cache_clear()
            return
    raise SystemExit(
        "未知配置：%s（可选 %s）" % (name, ", ".join(c[0] for c in CONFIGS))
    )


_TOPIC_RE = re.compile(r"\[知识点：([^｜\]]+)")


def parse_topics(context: str) -> list[str]:
    """从注入文本里把「[知识点：xxx｜...]」的 topic 抽出来。

    用正则而不是 startswith + 切片：资料块前面将来可能多出装饰性前缀，
    字符串切片一改格式就失效，而这类"格式一改测试就悄悄失真"的坑最贵。
    """
    topics = []
    for block in context.split("\n\n---\n\n"):
        match = _TOPIC_RE.search(block)
        if match:
            topics.append(match.group(1))
    return topics


def run_config(store, cfg_name: str, verbose: bool = False) -> dict:
    """跑一套配置，返回统计与每题明细。

    用 `retrieve_knowledge_docs` 而不是 `get_knowledge_context`：
    后者只返回格式化文本，切片身份（source + start_index）要从文本里正则反解
    ——那是给模型看的字符串，不是给程序读的接口（PROPOSAL §9 硬约束第 4 条）。
    """
    configure(cfg_name)

    # 预热：首次调用要加载重排模型，不能算进单题耗时
    if R.CONFIG.enable_rerank and store is not None:
        R.get_knowledge_context(store, "预热", k=1)

    rows = []
    rels = []
    for query, expected, kind, topic, relevant in CASES:
        trace: dict = {}
        t0 = time.perf_counter()
        docs = R.retrieve_knowledge_docs(store, query, topic=topic, trace=trace)
        elapsed = time.perf_counter() - t0

        # topic 直取 metadata，不再从格式化文本里正则反解
        topics = [str(d.metadata.get("topic") or "") for d in docs]
        any_hit = top_hit(topics, expected) if expected else None
        top1 = top_hit(topics[:1], expected) if expected else None

        row = {
            "query": query,
            "topic": topic,
            "kind": kind,
            "expected": sorted(expected),
            "got": topics,
            "any_hit": any_hit,
            "top1": top1,
            "secs": round(elapsed, 4),
            # 两个键都写：components 给 eval/slo_check.py 分类统计，
            # 逗号串给读 JSON 的人一眼看清（旧 trace 的写法）
            "degraded_components": list(trace.get("degraded_components") or []),
            "degraded": ",".join(trace.get("degraded_components") or []),
        }
        if METRICS_ON:
            rel = chunk_rel(docs, relevant, expected, k=injected_k() or len(docs))
            rels.append(rel)
            row.update(
                {
                    "annotated": rel.annotated,
                    "recall_at_k": rel.recall,
                    "precision_at_k": rel.precision,
                    "mrr": rel.mrr,
                    "ndcg_at_k": rel.ndcg,
                    "rerank_ms": trace.get("rerank_ms", 0),
                }
            )
        rows.append(row)

        if verbose:
            flag = "命中 ✓" if any_hit else ("未命中 ✗" if any_hit is False else "库外题目")
            print("=" * 78)
            print("[%s] 问：%s" % (kind, query))
            if topic:
                print("  上一轮知识点：%s（省略式追问场景）" % topic)
            print("  期望知识点：%s" % (hits_label(sorted(expected)) or "（库外，无期望）"))
            print("  实际召回：%s" % hits_label(topics))
            print(
                "  判定：%s（top1 %s）耗时 %.3fs"
                % (
                    flag,
                    "命中" if top1 else ("未命中" if top1 is False else "—"),
                    elapsed,
                )
            )

    scored = [r for r in rows if r["any_hit"] is not None]
    result = {
        "config": cfg_name,
        # 粒度与窗口随结果一起存：报告读的是 result，不该再去猜全局 CONFIG
        "granularity": "parent" if R.CONFIG.parent_child else "slice",
        "inject_k": injected_k(),
        "top1": sum(1 for r in scored if r["top1"]),
        "top3": sum(1 for r in scored if r["any_hit"]),
        "total": len(scored),
        "avg_secs": round(
            sum(r["secs"] for r in scored) / max(len(scored), 1), 4
        ),
        "p95_secs": round(p95([r["secs"] for r in scored]), 4),
        "degraded_cases": sum(1 for r in rows if r.get("degraded")),
        "cases": rows,
    }
    if METRICS_ON:
        # 库外题（无 expected）不参与指标：它们本来就该"不命中"，
        # 混进来会把所有指标系统性拉低，读不出检索质量。
        result["metrics"] = aggregate(
            [r for r, scored_ok in zip(rels, [x["any_hit"] is not None for x in rows]) if scored_ok]
        )
    return result


def print_summary(results: list[dict]) -> None:
    print("\n" + "=" * 68)
    print("%-12s %10s %10s %12s" % ("配置", "top1", "top3", "平均耗时"))
    print("-" * 68)
    for r in results:
        print(
            "%-12s %7d/%-3d %7d/%-3d %9.3fs"
            % (r["config"], r["top1"], r["total"], r["top3"], r["total"], r["avg_secs"])
        )
    print("=" * 68)
    for r in results:
        print_metrics(r)


def fmt(value) -> str:
    return "—" if value is None else "%.3f" % value


def print_metrics(result: dict) -> None:
    """打印切片级指标（E2）。缺金标准标注时明确标出，不假装是真值。"""
    m = result.get("metrics")
    if not m:
        return
    k = result.get("inject_k") or injected_k()
    is_parent = (result.get("granularity") or ("parent" if R.CONFIG.parent_child else "slice")) == "parent"
    gran = "parent（父子检索聚合后的整节）" if is_parent else "切片"
    print(
        "\n[%s] 切片级指标  粒度 %s  MRR %.3f  nDCG@%d %.3f  p95 %.3fs  降级用例 %d"
        % (
            result["config"],
            gran,
            m["mrr"],
            k,
            m["ndcg_at_k"],
            result.get("p95_secs", 0),
            result.get("degraded_cases", 0),
        )
    )
    print(
        "      已标注用例 %d（金标准=topic 派生，gain 三档为裁判 LLM 三票多数·待人工复核）：\n"
        "      Recall@%d %s  Precision@%d %s"
        % (m["annotated_cases"], k, fmt(m["recall_at_k"]), k, fmt(m["precision_at_k"]))
    )
    if is_parent:
        # 父子粒度金标准是整节（每题 4~9 片），窗口只有 k 条。但 **Recall 不因此封顶**：
        # 金标准里 0 分的片不进分母，正增益片数 ≤ k 时Recall 仍可接近 1.0
        # （实测 0.794 就超过了按片数算的 0.75 假上限）。别在这里写死的"上限"。
        print(
            "      注意：parent 金标准为整节（每题 4~9 片）而窗口 %d 条；Recall 分母只计"
            "正增益片，n>k 不等于封顶，跨粒度对比以 nDCG 为准" % k
        )
    if m["fallback_cases"]:
        print(
            "      未标注用例 %d：recall/precision 为 topic 级代理值（不参与上两项）；\n"
            "      其 nDCG@%d %.3f 的分母是「k 位全命中」，反映的是命中充分度×排序，"
            "不纯是排序质量" % (m["fallback_cases"], k, m["ndcg_at_k_fallback"])
        )
    if m.get("ndcg_clamped_cases"):
        # 越界被截断到 1.0：说明金标准不全，部分相关档在顶分母，该补标注了
        print(
            "      ⚠ %d 条用例的 nDCG 原值 > 1 已截断：金标准不完整 + 0.5 部分相关档叠加，"
            "请补 relevant_chunks 标注" % m["ndcg_clamped_cases"]
        )


def run_uplift(store) -> int:
    """重排提升度（E5）：同一套题跑 rerank on / off，比 nDCG@4。

    门禁语义：提升度 <= 0 说明重排在帮倒忙，直接非 0 退出。
    """
    on = run_config(store, "full")
    off = run_config(store, "hybrid")  # hybrid = 混合召回但不重排
    m_on, m_off = on.get("metrics") or {}, off.get("metrics") or {}
    ndcg_on = m_on.get("ndcg_at_k", 0.0)
    ndcg_off = m_off.get("ndcg_at_k", 0.0)
    uplift = round(ndcg_on - ndcg_off, 4)
    print("\n重排提升度（nDCG@4）")
    print("  重排开：%.4f（top1 %d/%d）" % (ndcg_on, on["top1"], on["total"]))
    print("  重排关：%.4f（top1 %d/%d）" % (ndcg_off, off["top1"], off["total"]))
    print("  提升度：%+.4f" % uplift)
    if uplift <= 0:
        print("  ✗ 重排未带来提升（提升度 <= 0），按门禁判定失败")
        return 1
    print("  ✓ 重排有正向收益")
    return 0


def find_regressions(result: dict, baseline: dict) -> list[str]:
    """逐题对比，返回退化列表。"""
    base_map = {c["query"]: c for c in baseline["cases"]}
    bad = []
    for case in result["cases"]:
        old = base_map.get(case["query"])
        if old is None or old["top1"] is None:
            continue
        if old["top1"] and not case["top1"]:
            bad.append(
                "top1 退化：%s（%s -> %s）"
                % (
                    case["query"],
                    hits_label(old["got"][:1]),
                    hits_label(case["got"][:1]),
                )
            )
        elif old["any_hit"] and not case["any_hit"]:
            bad.append(
                "完全未命中：%s（召回 %s）" % (case["query"], hits_label(case["got"]))
            )
    return bad


def main() -> int:
    global CASES

    parser = argparse.ArgumentParser()
    parser.add_argument("--only", default="", help="只跑某一种配置并打印明细")
    parser.add_argument("--save-base", default="", help="把 full 配置结果存为基线 JSON")
    parser.add_argument("--compare", default="", help="与基线 JSON 对比，退化则非 0 退出")
    parser.add_argument("--cases", default="", help="评测集 JSON 路径（默认 rag_eval_cases.json）")
    parser.add_argument("--verbose", action="store_true", help="打印每题明细")
    parser.add_argument("--uplift", action="store_true", help="重排提升度：full vs hybrid 的 nDCG@4 差值")
    parser.add_argument("--fingerprint", action="store_true", help="打印当前索引指纹并与基线比对")
    args = parser.parse_args()

    CASES = load_cases(args.cases)

    store = load_knowledge_index(COLLECTION, PERSIST_DIR)
    if store is None:
        raise SystemExit(
            "知识库还没构建：先在 backend/ 下跑 python build_kb.py\n"
            "（vector store 不存在时 load_knowledge_index 返回 None）"
        )

    scored = sum(1 for _, expected, _, _, _ in CASES if expected)
    print(
        "collection=%s  切片数=%d  题目=%d 条（计分 %d / 库外 %d）"
        % (
            COLLECTION,
            store._collection.count(),
            len(CASES),
            scored,
            len(CASES) - scored,
        )
    )

    # Wave 3：指纹先于指标。库都换了，指标再漂亮也比不出退化。
    if args.fingerprint:
        fp = collection_fingerprint(store)
        base_fp = load_baseline_meta().get("index_fingerprint", "")
        ok, msg = check_fingerprint(fp, base_fp)
        print("\n索引指纹")
        print("  当前：%s" % fp)
        print("  基线：%s" % (base_fp or "（基线未记录）"))
        print("  %s %s" % ("✓" if ok else "✗", msg))
        # 只在「两端都有指纹且不一致」时非 0；缺指纹是警告不是失败
        return 0 if ok else 1

    # Wave 2：重排提升度，跑完直接返回，不再跑四配置全量
    if args.uplift:
        if not METRICS_ON:
            raise SystemExit("--uplift 依赖切片级指标，不要用 EVAL_METRICS=0 跑")
        return run_uplift(store)

    targets = [args.only] if args.only else [c[0] for c in CONFIGS]
    results = [
        run_config(store, name, verbose=bool(args.only) or args.verbose)
        for name in targets
    ]

    if not args.only:
        print_summary(results)

    if args.save_base:
        full = next((r for r in results if r["config"] == "full"), None)
        if full is None:
            full = run_config(store, "full")
        Path(args.save_base).write_text(
            json.dumps(full, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            "\n基线已保存：%s（top1 %d/%d，top3 %d/%d）"
            % (args.save_base, full["top1"], full["total"], full["top3"], full["total"])
        )
        return 0

    if args.compare:
        base_path = Path(args.compare)
        if not base_path.exists():
            raise SystemExit("基线文件不存在：%s" % base_path)

        # Wave 3 收尾：先验指纹再比指标。库已换而基线没更新时，
        # 逐题对比会假阴（报"无退化"，实际比的根本不是同一个库）。
        # 任一端缺指纹仍只警告（跨库实验不拦）；两端都有且不一致才拒绝。
        fp = collection_fingerprint(store)
        base_fp = load_baseline_meta().get("index_fingerprint", "")
        ok, msg = check_fingerprint(fp, base_fp)
        print("\n索引指纹：%s" % fp)
        print("  %s %s" % ("✓" if ok else "✗", msg))
        if not ok:
            print("  拒绝对比：先重跑 python build_kb.py（会刷新基线元信息），")
            print("  再用 python smoke_rag.py --save-base %s 重存基线" % args.compare)
            return 1

        baseline = json.loads(base_path.read_text(encoding="utf-8"))
        current = next((r for r in results if r["config"] == "full"), None)
        if current is None:
            current = run_config(store, "full")

        print("\n与基线对比（%s）" % args.compare)
        print(
            "  top1  %d/%d  (基线 %d/%d)"
            % (current["top1"], current["total"], baseline["top1"], baseline["total"])
        )
        print(
            "  top3  %d/%d  (基线 %d/%d)"
            % (current["top3"], current["total"], baseline["top3"], baseline["total"])
        )

        bad = find_regressions(current, baseline)
        if bad:
            print("\n退化项：")
            for line in bad:
                print("  ✗ %s" % line)
            return 1
        print("\n无退化 ✓")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
