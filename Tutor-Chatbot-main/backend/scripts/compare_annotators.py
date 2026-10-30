"""双模型标注一致性比对（换裁判模型前的可行性验证）。

背景
----
`eval/cases_v3.json` 的 395 题金标准，是当年用 **DeepSeek-Flash 带思考**标出来的
（build_eval_v3.py 改动前是直接读 LLM_*，不带任何 extra_body）。

现在裁判模型换成阿里云 Qwen，若要全量重跑标注，金标准的「生产者」就换了，
历史结论（nDCG 0.8812 那一批）与新结果将不再严格可比。本脚本量化「会变多少」。

三组对照缺一不可
----------------
    G  现存金标准（cases_v3.json，历史 DeepSeek 标注）
    B  DeepSeek 用同一 prompt、同一历史配置重新标注（可复现性对照）
    A  Qwen 用同一 prompt、目标配置标注（我们要用的）

    A vs G   真正关心的问题：换成 Qwen 后，金标准会不会变
    B vs G   必要的对照组：如果连 DeepSeek 自己都复现不出历史结果，
             说明差异里有相当部分来自随机抖动与时间漂移，而不是模型差异。
             少了这一组，看到 A≠G 会误判成「模型的锅」
    A vs B   纯模型差异（同一时刻、同一 prompt）

※ B 刻意配置成「不带 extra_body」，与历史标注那一刻的行为完全一致。
  若给 B 也关掉思考，测到的差异就混杂了配置变更，无法归因。

为什么按文档分批而不是按题
--------------------------
分档标注的 prompt 天生是「一篇文档的 N 个小节 × M 个问句」批量打的，
单独问一句会丢掉小节之间的相对比较，而这正是 1.0/0.5/0.0 分档的依据。

用法
----
    venv/Scripts/python.exe scripts/compare_annotators.py                 # 默认 3 篇
    venv/Scripts/python.exe scripts/compare_annotators.py --docs 5        # 扩到 5 篇
    venv/Scripts/python.exe scripts/compare_annotators.py --topics 死锁,排序
    venv/Scripts/python.exe scripts/compare_annotators.py --dry-run       # 只对齐不调用
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
load_dotenv(BACKEND_DIR / ".env")

CASES = BACKEND_DIR / "eval" / "cases_v3.json"
REPORTS = BACKEND_DIR / "eval" / "reports"

LEVELS = [0.0, 0.5, 1.0]
LEVEL_NAME = {0.0: "0.0", 0.5: "0.5", 1.0: "1.0"}


# --------------------------------------------------------------------------
# LLM 构造
# --------------------------------------------------------------------------
def make_annotator(kind: str):
    """kind: 'qwen' 走裁判通道；'deepseek' 复现历史配置。"""
    from langchain_openai import ChatOpenAI

    if kind == "qwen":
        from eval.judge_llm import make_judge_llm

        return make_judge_llm(), "Qwen(裁判通道)"

    # 历史复刻：用的就是 LLM_* 三件套，且**不带**任何 extra_body，
    # 与 cases_v3.json 当年被标注那一刻的调用方式保持一致。
    model = os.getenv("LLM_MODEL") or "deepseek-flash"
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        raise SystemExit("LLM_API_KEY 未配置，无法构造 DeepSeek 对照组")
    kwargs = {
        "model": model,
        "temperature": 0.0,
        "api_key": api_key,
        "base_url": os.getenv("LLM_BASE_URL") or None,
        "timeout": 180,
        "max_tokens": 4096,
    }
    return ChatOpenAI(**kwargs), "DeepSeek(%s)" % model


# --------------------------------------------------------------------------
# 金标准对齐
# --------------------------------------------------------------------------
def ground_truth_map() -> dict[str, dict[tuple[str, int], float]]:
    """把 cases_v3 的 relevant_chunks 摊平成 {(source, parent_start): gain} → 按 query 索引。"""
    data = json.loads(CASES.read_text(encoding="utf-8"))
    out: dict[str, dict[tuple[str, int], float]] = {}
    for case in data.get("cases", []):
        chunks = case.get("relevant_chunks")
        if not chunks:
            continue
        query = (case.get("query") or "").strip()
        table: dict[tuple[str, int], float] = {}
        for row in chunks:
            if len(row) < 3:
                continue
            source, start, gain = row[0], int(row[1]), float(row[2])
            table[(source, start)] = gain
        if table:
            out[query] = table
    return out


def build_tasks(by_topic: dict, gt: dict, topics_filter: list[str], per_doc: int) -> list[dict]:
    """生成比对任务：每篇文档一组（parents + 该文档的问句）。"""
    source_to_topic: dict[str, str] = {}
    for topic, parents in by_topic.items():
        for p in parents:
            source_to_topic[p["source"]] = topic

    # 按文档归拢现有问句
    queries_by_source: dict[str, list[str]] = defaultdict(list)
    for query in gt:
        # 该题的 relevant_chunks 可能跨多篇，取其主文档（出现次数最多的）
        sources = [k[0] for k in gt[query]]
        if not sources:
            continue
        main_source = Counter(sources).most_common(1)[0][0]
        queries_by_source[main_source].append(query)

    tasks = []
    for topic, parents in sorted(by_topic.items()):
        if not parents:
            continue
        source = parents[0]["source"]
        if topics_filter and not any(t in topic or t in source for t in topics_filter):
            continue
        qs = sorted(set(queries_by_source.get(source, [])))
        if not qs:
            continue
        if per_doc > 0:
            qs = qs[:per_doc]
        tasks.append(
            {
                "topic": topic,
                "source": source,
                "parents": parents,
                "queries": [{"query": q} for q in qs],
            }
        )
    return tasks


# --------------------------------------------------------------------------
# 统计
# --------------------------------------------------------------------------
def cohens_kappa(pairs: list[tuple[float, float]]) -> float:
    """三分类（0.0/0.5/1.0）的 Cohen's kappa。

    用它是因为三档分布高度不均（样本里 1.0 和 0.0 占多数），
    简单一致率会被「多数类一致」虚高，kappa 扣掉随机一致的部分。
    """
    n = len(pairs)
    if n == 0:
        return float("nan")
    po = sum(1 for a, b in pairs if a == b) / n
    ca, cb = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    pe = sum((ca[l] / n) * (cb[l] / n) for l in LEVELS)
    if abs(1.0 - pe) < 1e-9:
        return float("nan")
    return (po - pe) / (1.0 - pe)


def compare(name_a: str, name_b: str, pairs: list[tuple[float, float]]) -> dict:
    n = len(pairs)
    agree = sum(1 for a, b in pairs if a == b)
    confuse = Counter((a, b) for a, b in pairs if a != b)
    # 1.0 档是「首要答案」，它的增减直接改变 oracle recall，单独看
    a_only = sum(1 for a, b in pairs if a == 1.0 and b != 1.0)
    b_only = sum(1 for a, b in pairs if a != 1.0 and b == 1.0)
    return {
        "a": name_a,
        "b": name_b,
        "points": n,
        "agree": agree,
        "agreement": round(agree / n, 4) if n else None,
        "kappa": round(cohens_kappa(pairs), 4) if n else None,
        "top_grade_only_in_a": a_only,
        "top_grade_only_in_b": b_only,
        "confusions": {
            "%s->%s" % (LEVEL_NAME[a], LEVEL_NAME[b]): c
            for (a, b), c in confuse.most_common()
        },
    }


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", type=int, default=3, help="抽取几篇文档做比对（默认 3）")
    ap.add_argument("--per-doc", type=int, default=0, help="每篇最多取几个问句（0=全部）")
    ap.add_argument("--topics", default="", help="只挑包含这些子串的文档，逗号分隔")
    ap.add_argument("--votes", type=int, default=1, help="每个模型重复标注几次（默认 1）")
    ap.add_argument("--collection", default="cs_basics")
    ap.add_argument("--persist-dir", default="chroma_kb")
    ap.add_argument("--dry-run", action="store_true", help="只做对齐检查，不调用 LLM")
    ap.add_argument("--only", choices=("a", "b"), default="",
                    help="只跑一侧（a=Qwen / b=DeepSeek），省一半耗时，另一侧用 None 占位")
    ap.add_argument("--dump-labels", default="",
                    help="导出逐点三元组到该 JSON 文件，供 scripts/simulate_qwen_relabel.py 做指标换算")
    args = ap.parse_args(argv)

    from scripts.build_eval_v3 import annotate, load_parents  # 复用同一套 prompt

    print("读取索引 parent ...")
    by_topic = load_parents(args.collection, args.persist_dir)
    print("  已载入 parent：%d 个 topic" % len(by_topic))

    gt = ground_truth_map()
    print("  已载入金标准：%d 题（含 gain 表）" % len(gt))

    topics_filter = [t.strip() for t in args.topics.split(",") if t.strip()]
    tasks = build_tasks(by_topic, gt, topics_filter, args.per_doc)
    if args.docs > 0:
        # 取题量最多的前 N 篇，样本更有代表性；也能用 --topics 精确指定
        tasks = sorted(tasks, key=lambda t: -len(t["queries"]))[: args.docs]
    if not tasks:
        raise SystemExit("没有可比对的文档，检查 --topics / 索引是否为空")

    print("\n比对样本：")
    for t in tasks:
        print("  %-32s %2d 小节 × %2d 问句" % (t["source"], len(t["parents"]), len(t["queries"])))

    if args.dry_run:
        print("\n(dry-run：未调用 LLM)")
        return 0

    if args.only == "b":
        llm_a = name_a = None
    else:
        llm_a, name_a = make_annotator("qwen")
    if args.only == "a":
        llm_b = name_b = None
    else:
        llm_b, name_b = make_annotator("deepseek")
    print("\n参评双方： A=%s   B=%s" % (name_a or "(跳过)", name_b or "(跳过)"))

    rows = []
    started = time.perf_counter()
    for task in tasks:
        parents = task["parents"]
        queries = task["queries"]
        gt_table = gt

        # —— 每个模型各跑 votes 次，取众数；众数同时给出「自洽率」
        def run(llm, name):
            all_runs, self_stats = [], []
            for _ in range(args.votes):
                res = annotate(llm, task["topic"], parents, queries)
                all_runs.append(res)
            # 逐点取众数
            merged = []
            for qi in range(len(queries)):
                row = []
                for pi in range(len(parents)):
                    vals = [r[qi][pi] for r in all_runs]
                    cnt = Counter(vals).most_common(1)[0]
                    row.append(cnt[0])
                    self_stats.append(1 if len(set(vals)) == 1 else 0)
                merged.append(row)
            if args.votes > 1:
                self_rate = sum(self_stats) / len(self_stats) if self_stats else 0
                print("    %s 自洽率（%d 票完全一致）：%.1f%%" % (name, args.votes, self_rate * 100))
            return merged

        res_a = [[None] * len(parents) for _ in range(len(queries))]
        res_b = [[None] * len(parents) for _ in range(len(queries))]
        cost_a = cost_b = 0.0
        if args.only != "b":
            t0 = time.perf_counter()
            res_a = run(llm_a, name_a)
            cost_a = time.perf_counter() - t0
        if args.only != "a":
            t0 = time.perf_counter()
            res_b = run(llm_b, name_b)
            cost_b = time.perf_counter() - t0

        # —— 对齐成 (A, B, G) 三元组，同时保留定位信息以便下游重算指标
        triplets, detail = [], []
        for qi, q in enumerate(queries):
            table = gt_table.get(q["query"], {})
            for pi, p in enumerate(parents):
                key = (p["source"], p["parent_start"])
                if key not in table:
                    continue
                av = res_a[qi][pi] if res_a is not None else None
                bv = res_b[qi][pi] if res_b is not None else None
                triplets.append((av, bv, table[key]))
                detail.append(
                    {
                        "query": q["query"],
                        "source": key[0],
                        "parent_start": key[1],
                        "a": av,
                        "b": bv,
                        "g": table[key],
                    }
                )

        covered = len(triplets) / (len(queries) * len(parents)) if queries else 0
        print(
            "  %-32s A %.1fs / B %.1fs，对齐 %d 个评分点（覆盖率 %.0f%%）"
            % (task["source"], cost_a, cost_b, len(triplets), covered * 100)
        )
        rows.append({"source": task["source"], "topic": task["topic"], "triplets": triplets,
                     "detail": detail})

    all_t = [t for r in rows for t in r["triplets"]]
    # --only 单侧运行时，缺失的一侧是 None，必须从统计里剔除，否则会把空值当成一个档位
    pairs_ag = [(a, g) for a, b, g in all_t if a is not None]
    pairs_bg = [(b, g) for a, b, g in all_t if b is not None]
    pairs_ab = [(a, b) for a, b, g in all_t if a is not None and b is not None]

    elapsed = time.perf_counter() - started
    print("\n" + "=" * 66)
    print("一致性比对结果（%d 个评分点，耗时 %.1fs）" % (len(all_t), elapsed))
    print("=" * 66)

    def line(title, name_x, name_y, pairs):
        """单侧运行（--only）时该组没有样本，必须明确标注跳过而不是报 0%。"""
        if not pairs:
            print("\n%s\n  （本轮未运行该侧，无样本，跳过）" % title)
            return {"agreement": None, "kappa": float("nan"), "confusions": {},
                    "top_grade_only_in_a": 0, "top_grade_only_in_b": 0}
        stat = compare(name_x, name_y, pairs)
        print(
            "\n%s\n  一致率 %5.1f%%  kappa %6.3f  G=1.0而对方非1.0: %2d  对方=1.0而G非1.0: %2d"
            % (
                title,
                (stat["agreement"] or 0) * 100,
                stat["kappa"] if stat["kappa"] == stat["kappa"] else float("nan"),
                stat["top_grade_only_in_b"],
                stat["top_grade_only_in_a"],
            )
        )
        if stat["confusions"]:
            top = list(stat["confusions"].items())[:5]
            print("  主要分歧：" + "，".join("%s %d 处" % (k, v) for k, v in top))
        return stat

    s_ag = line("① A vs G —— Qwen 对比现存金标准（最关心的指标）", name_a, "现存金标准 G", pairs_ag)
    s_bg = line("② B vs G —— DeepSeek 自己能否复现历史（可复现性基线）", name_b, "现存金标准 G", pairs_bg)
    s_ab = line("③ A vs B —— 纯模型差异（同一时刻同一 prompt）", name_a, name_b, pairs_ab)

    if args.dump_labels:
        labels = [d for r in rows for d in r["detail"]]
        out_lbl = Path(args.dump_labels)
        out_lbl.parent.mkdir(parents=True, exist_ok=True)
        out_lbl.write_text(
            json.dumps(
                {
                    "generated_at": datetime.now().strftime("%Y%m%d_%H%M%S"),
                    "votes": args.votes,
                    "docs": [r["source"] for r in rows],
                    "a_name": name_a,
                    "b_name": name_b,
                    "labels": labels,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print("\n逐点标签已导出：%s（%d 条）" % (out_lbl, len(labels)))

    print("\n" + "=" * 66)
    judge_ab, judge_bg = s_ab["agreement"] or 0, s_bg["agreement"] or 0
    if args.only:
        print("判读：单侧运行，缺对照组，一致性结论以双侧那一轮为准。")
    elif judge_bg >= 0.995:
        print("判读：DeepSeek 能近乎完整复现历史标注 ⇒ 差异主要来自换模型，非随机抖动。")
    else:
        print(
            "判读：DeepSeek 自己复现历史的一致率仅 %.1f%%，低于 100%% ⇒"
            " 标注存在不可复现成分，A vs G 的差异里有一部分不是模型造成的。" % (judge_bg * 100)
        )
    if not args.only:
        if judge_ab >= judge_bg - 0.02:
            print("  ⇒ 模型间一致率不低于自身复现率，换 Qwen 重标不会引入超出噪声的差异。")
        else:
            print("  ⇒ 模型间一致率明显低于自身复现率，换 Qwen 会实际改变金标准，建议人工抽检后再决定全量重跑。")

    REPORTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = REPORTS / ("annotator_agreement_%s.json" % stamp)
    out.write_text(
        json.dumps(
            {
                "generated_at": stamp,
                "docs": [r["source"] for r in rows],
                "points": len(all_t),
                "a_vs_g": s_ag,
                "b_vs_g": s_bg,
                "a_vs_b": s_ab,
                "per_source": {
                    r["source"]: compare(name_a, "G", [(a, g) for a, b, g in r["triplets"]])["agreement"]
                    for r in rows
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print("\n报告已写入：%s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
