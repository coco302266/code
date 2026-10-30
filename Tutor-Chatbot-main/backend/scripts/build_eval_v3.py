"""构建 300+ 题评测集，并按「逐 parent 分档」的严格标准标注金标准。

背景（为什么这么设计）：
    旧集 `rag_eval_cases.json` 已有 71 条计分题，其金标准是
    `topic-derived+llm-refined`：每题平均 4.49 个 parent，其中仅 1.46 个是
    1.0 档、1.56 个 0.5 档、1.46 个 0.0 档。也就是说「收紧金标准」在旧集上
    其实已经做过，真正缺口是**样本量**（71 题 → 最小可检出效应约 ±2pp，
    任何小幅优化都测不出来）。

本脚本做的事：
    1. 从 chroma 索引读出全部 parent 切片（20 篇文档 × 4 小节 = 81 个 parent）；
    2. 按 topic 分组，对每篇文档调用项目 LLM 生成 N 个互不重复、且能仅凭
       该文档作答的中文问句；
    3. 再用一次 LLM 调用，对「每个问句 × 该文档全部 parent」打增益分
       （1.0 直接作答 / 0.5 部分支撑 / 0.0 同主题但不作答）；
    4. **显式输出全部 parent（含 0.0 档）**——这是收紧的关键：`eval/metrics.py`
       的 `chunk_gains` 对「同 topic 但不在金标准里」的切片会兜底给 0.5，
       只有把同主题 parent 全部写进 `relevant_chunks`，才能覆盖这个兜底，
       让「检索到同主题的错误小节」真正被扣分。
    5. 校验：每个问句至少 1 个 1.0 档，否则丢弃；跨题去重。

写法上刻意保持与 `rag_eval_cases.json` 同构，`smoke_rag.load_cases` 可直接读。

用法：
    python scripts/build_eval_v3.py --per-doc 16 --out eval/cases_v3.json
    python scripts/build_eval_v3.py --topics 死锁 --per-doc 4 --dry-run   # 试跑
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND_DIR / ".env")

# 生成问句与打分用两个温度：生成要多样性，打分要稳定。
TEMP_GEN = 0.7
TEMP_ANNO = 0.0
CALL_SLEEP = 0.6          # 逐次调用间留一点间隔，避免触发限流
MAX_RETRY = 3

GOLD_SOURCE = "llm-chunk-annotated-v3"
def judge_model_name() -> str:
    """实际生效的裁判/标注模型名（EVAL_JUDGE_MODEL > LLM_MODEL）。"""
    from eval.judge_llm import resolve_judge_config

    try:
        return resolve_judge_config(require_key=False)["model"]
    except SystemExit:
        return os.getenv("LLM_MODEL") or "unknown"


def gold_note() -> str:
    """写进试题文件里的标注来源说明。

    原来这里硬编码 deepseek-flash —— 换模型的当下没事，几个月后拿到这份
    cases_v3.json 的人会被这句注释误导。改成按实际生效的模型动态生成。
    """
    return "[AI标注·待人工复核] 逐 parent 分档由 %s 生成，需人工抽检" % judge_model_name()


# --------------------------------------------------------------------------
# LLM
# --------------------------------------------------------------------------
def make_llm(temperature: float):
    """出题与分档标注用的 LLM —— 走「裁判通道」而非生产通道。

    本脚本是离线批量工具（395 题 × 每篇逐 parent 标注），跑一次几百次调用，
    不应该消耗生产额度，也不该随生产换厂商而变。统一由 eval/judge_llm.py 解析。

    temperature 由调用方给：出题要多样性（>0），标注要稳定（=0）。
    """
    from langchain_openai import ChatOpenAI

    from eval.judge_llm import resolve_judge_config

    cfg = resolve_judge_config()
    kwargs = {
        "model": cfg["model"],
        "api_key": cfg["api_key"],
        "base_url": cfg["base_url"],
        "temperature": temperature,
        "timeout": 180,
        "max_tokens": 4096,
    }
    if cfg.get("extra_body"):  # Qwen3 等推理模型：关闭思考，省 token 且输出更贴格式
        kwargs["extra_body"] = cfg["extra_body"]
    return ChatOpenAI(**kwargs)


def extract_json(text: str):
    """从模型输出里抠出第一个完整的 JSON 数组/对象。

    模型经常在 JSON 外面包 ```json 围栏或加一句"好的，如下"——
    直接 json.loads 会炸，所以按括号配对扫描，而不是正则截取。
    """
    if not text:
        raise ValueError("空输出")
    cleaned = re.sub(r"```(?:json)?", "", text).strip()

    # 必须取「最先出现的括号」作为外层：`{"items": [...]}` 里 `[` 在 `{` 之后，
    # 若先扫 `[` 会把内层数组单独抠出来，标注结果就变成 list 而不是 dict。
    pairs = {"[": "]", "{": "}"}
    candidates = [(cleaned.find(o), o) for o in pairs if cleaned.find(o) >= 0]
    if not candidates:
        raise ValueError("未找到 JSON：%s" % text[:120])
    start, opener = min(candidates)
    closer = pairs[opener]

    depth = 0
    for i in range(start, len(cleaned)):
        ch = cleaned[i]
        if ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return json.loads(cleaned[start : i + 1])
    raise ValueError("括号未闭合：%s" % text[:120])


def call_json(llm, prompt: str, expect_types=(list, dict)):
    """带重试的 JSON 调用。返回解析后的对象。"""
    last = None
    for attempt in range(MAX_RETRY):
        try:
            out = llm.invoke(prompt).content
            data = extract_json(out)
            if isinstance(data, expect_types):
                return data
            last = f"类型不符: {type(data).__name__}"
        except Exception as exc:  # noqa: BLE001
            last = f"{type(exc).__name__}: {exc}"
        time.sleep(CALL_SLEEP * (attempt + 1))
    raise RuntimeError("调用失败（%d 次）：%s" % (MAX_RETRY, last))


# --------------------------------------------------------------------------
# 索引读取
# --------------------------------------------------------------------------
def load_parents(collection: str, persist_dir: str) -> dict[str, list[dict]]:
    """读出全部 parent 切片，按 topic 归组，组内按 parent_start 排序。

    parent 身份必须是 `(source, parent_start)`：父子模式下检索返回的 parent
    把 `start_index` 换成了 `parent_start`，金标准若用 child 偏移会静默归零。
    """
    from agent.rag.indexer import load_knowledge_index

    store = load_knowledge_index(collection, persist_dir)
    metas = store._collection.get(include=["metadatas"])["metadatas"]

    seen: dict[str, dict] = {}
    for meta in metas:
        pid = meta.get("parent_id") or "%s#%s" % (
            meta.get("source"),
            meta.get("parent_start"),
        )
        if pid in seen:
            continue
        seen[pid] = {
            "source": meta.get("source"),
            "parent_start": int(meta.get("parent_start") or 0),
            "parent_title": meta.get("parent_title") or "",
            "topic": (meta.get("topic") or "").strip(),
            "text": meta.get("parent_text") or "",
        }

    by_topic: dict[str, list[dict]] = defaultdict(list)
    for parent in seen.values():
        if parent["topic"]:
            by_topic[parent["topic"]].append(parent)
    for topic in by_topic:
        by_topic[topic].sort(key=lambda p: p["parent_start"])
    return dict(by_topic)


# --------------------------------------------------------------------------
# 生成问句
# --------------------------------------------------------------------------
GEN_PROMPT = """你是计算机专业课程的出题老师。下面是知识文档《{topic}》的四个小节：

{sections}

请出 {n} 个**互不重复**的中文评测问句，硬性要求：
1. 每个问句都必须能**仅凭上面四个小节的内容**回答，不要问需要外部知识或跨文档的问题；
2. 覆盖不同角度，不要都问定义：定义/概念、原理机制、步骤条件、对比区别、场景应用、常见误区排错、面试追问；
3. 问句长度 8~30 字，口语化和书面化混合，像学生真实会问的；
4. 不要出现"本文""上述""文档"这类指代；
5. 每个问句给出 aspect 字段，取值只能是：定义、原理、步骤、对比、场景、排错、面试。

只输出 JSON 数组，不要任何解释：
[{{"query": "问句", "aspect": "定义"}}]"""


def gen_queries(llm, topic: str, parents: list[dict], n: int) -> list[dict]:
    sections = "\n".join(
        "- 【%s】%s" % (p["parent_title"], p["text"][:220].replace("\n", " "))
        for p in parents
    )
    prompt = GEN_PROMPT.format(topic=topic, sections=sections, n=n)
    data = call_json(llm, prompt, expect_types=(list,))
    out = []
    for item in data:
        if isinstance(item, dict):
            q = str(item.get("query") or "").strip()
            aspect = str(item.get("aspect") or "定义").strip() or "定义"
        elif isinstance(item, str):
            q, aspect = item.strip(), "定义"
        else:
            continue
        if 6 <= len(q) <= 60:
            out.append({"query": q, "aspect": aspect})
    return out


# --------------------------------------------------------------------------
# 标注金标准
# --------------------------------------------------------------------------
ANNO_PROMPT = """下面是知识文档《{topic}》的四个小节全文，编号 P0~P3：

{parents}

下面是 {n} 个问句。请对**每个问句**，判断每个小节对它的作答程度，打三档：
- 1.0：直接、完整地回答了该问句（首要答案所在）
- 0.5：部分相关，提供了支撑信息，但不是主要答案
- 0.0：同属该文档主题，但**并不回答**这个具体问句

硬性约束：
1. 每个问句**至少有一个 1.0**；
2. 不要整行都给 1.0——那样等于没有区分，必须区分出主要答案与无关小节；
3. gains 数组长度必须正好 {np}，依次对应 P0~P3 的分数。

问句列表：
{questions}

只输出 JSON，不要解释：
{{"items": [{{"query_id": 0, "gains": [{zeros}]}}, ...]}}"""


def annotate(llm, topic: str, parents: list[dict], queries: list[dict]) -> list[list[float]]:
    np_ = len(parents)
    blocks = "\n\n".join(
        "P%d 【%s】\n%s" % (i, p["parent_title"], p["text"][:1400])
        for i, p in enumerate(parents)
    )
    qlist = "\n".join("%d. %s" % (i, q["query"]) for i, q in enumerate(queries))
    prompt = ANNO_PROMPT.format(
        topic=topic,
        parents=blocks,
        n=len(queries),
        np=np_,
        questions=qlist,
        zeros=", ".join(["0.0"] * np_),
    )
    data = call_json(llm, prompt, expect_types=(dict,))
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise RuntimeError("标注返回缺少 items 数组")

    by_id: dict[int, list[float]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        qid = item.get("query_id")
        gains = item.get("gains")
        if not isinstance(qid, int) or not isinstance(gains, list):
            continue
        if len(gains) != np_:
            continue
        norm = []
        for g in gains:
            try:
                v = float(g)
            except (TypeError, ValueError):
                v = 0.0
            norm.append(1.0 if v >= 0.99 else (0.5 if v >= 0.25 else 0.0))
        by_id[qid] = norm

    result = []
    for i in range(len(queries)):
        gains = by_id.get(i)
        result.append(gains if gains else [0.0] * np_)
    return result


# --------------------------------------------------------------------------
# 组装
# --------------------------------------------------------------------------
def slug(text: str) -> str:
    keep = [c for c in text if c.isalnum() or c in "-_"]
    return "".join(keep)[:18] or "case"


def build(args) -> tuple[list[dict], dict]:
    by_topic = load_parents(args.collection, args.persist_dir)
    topics = sorted(by_topic)
    if args.topics:
        wanted = [t.strip() for t in args.topics.split(",") if t.strip()]
        topics = [t for t in topics if any(w.lower() in t.lower() for w in wanted)]

    if not topics:
        raise SystemExit("没有匹配到任何 topic（检查 --topics / 索引）")

    gen_llm = make_llm(TEMP_GEN)
    anno_llm = make_llm(TEMP_ANNO)

    seen_queries: set[str] = set()
    cases: list[dict] = []
    stats = {
        "topics": 0,
        "generated": 0,
        "kept": 0,
        "dropped_no_positive": 0,
        "dropped_dup": 0,
        "dropped_empty": 0,
        "failures": [],
    }

    for topic in topics:
        parents = by_topic[topic]
        if not parents:
            continue
        stats["topics"] += 1
        print("· 生成 [%s] %d 个 parent" % (topic, len(parents)), flush=True)
        try:
            queries = gen_queries(gen_llm, topic, parents, args.per_doc)
        except Exception as exc:  # noqa: BLE001
            stats["failures"].append({"topic": topic, "stage": "gen", "error": str(exc)})
            print("  ✗ 生成失败：%s" % exc, flush=True)
            continue
        stats["generated"] += len(queries)

        # 先按 query 去重（同一文档内 + 跨文档）
        uniq = []
        for q in queries:
            key = q["query"].strip()
            if key in seen_queries:
                stats["dropped_dup"] += 1
                continue
            seen_queries.add(key)
            uniq.append(q)
        if not uniq:
            stats["dropped_empty"] += len(queries)
            continue

        try:
            gains_rows = annotate(anno_llm, topic, parents, uniq)
        except Exception as exc:  # noqa: BLE001
            stats["failures"].append({"topic": topic, "stage": "anno", "error": str(exc)})
            print("  ✗ 标注失败：%s" % exc, flush=True)
            continue

        topic_slug = slug(topic)
        for q, gains in zip(uniq, gains_rows):
            if sum(gains) <= 0:
                stats["dropped_no_positive"] += 1
                continue
            rel = [
                [p["source"], p["parent_start"], round(g, 1)]
                for p, g in zip(parents, gains)
            ]
            # 用全局自增序号防 id 撞车（不同 topic 的 slug 截断后可能相同）
            seq = len(cases)
            cases.append(
                {
                    "id": "v3-%03d-%s" % (seq, topic_slug),
                    "query": q["query"],
                    "expected": [topic],
                    "topic": topic,
                    "kind": "in-kb",
                    "aspect": q["aspect"],
                    "relevant_chunks": rel,
                    "gold_source": GOLD_SOURCE,
                    "_note": gold_note(),
                }
            )
        print(
            "  → 保留 %d 题（本题 1.0 档均值 %.2f）"
            % (
                sum(1 for c in cases if c["topic"] == topic),
                (
                    sum(sum(1 for g in row if g >= 1.0) for row in gains_rows)
                    / max(1, len(gains_rows))
                ),
            ),
            flush=True,
        )
        time.sleep(CALL_SLEEP)

    stats["kept"] = len(cases)
    return cases, stats


def merge_existing(cases: list[dict], existing: Path, drop_old_dupes: bool = True) -> tuple[list[dict], int]:
    """把旧集并进来，按 query 去重（新旧重复时保留旧集）。"""
    data = json.loads(existing.read_text(encoding="utf-8"))
    old = [
        c for c in data.get("cases", []) if c.get("kind") not in (None, "cascade")
    ]
    old_q = {str(c.get("query") or "").strip() for c in old}
    merged = list(old)
    skipped = 0
    for c in cases:
        if drop_old_dupes and c["query"].strip() in old_q:
            skipped += 1
            continue
        merged.append(c)
    return merged, skipped


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="构建 300+ 题评测集（逐 parent 分档金标准）")
    ap.add_argument("--per-doc", type=int, default=16, help="每篇文档生成问句数")
    ap.add_argument("--topics", default="", help="只处理包含这些子串的 topic，逗号分隔")
    ap.add_argument("--collection", default="cs_basics")
    ap.add_argument("--persist-dir", default="chroma_kb")
    ap.add_argument("--out", default=str(BACKEND_DIR / "eval" / "cases_v3.json"))
    ap.add_argument("--include-existing", action="store_true", help="并入旧集 rag_eval_cases.json")
    ap.add_argument("--dry-run", action="store_true", help="只打印统计，不写文件")
    args = ap.parse_args(argv)

    cases, stats = build(args)

    skipped = 0
    if args.include_existing:
        existing = BACKEND_DIR / "rag_eval_cases.json"
        if existing.exists():
            cases, skipped = merge_existing(cases, existing)
            stats["merged_from_existing"] = skipped

    payload = {
        "version": 3,
        "note": (
            "v3 评测集：每篇文档生成问句，金标准逐 parent 分档（1.0/0.5/0.0），"
            "显式覆盖同主题全部 parent 以覆盖 metrics 的 0.5 兜底。"
            "生成模型=%s（裁判通道，见 eval/judge_llm.py）。" % judge_model_name()
        ),
        "gold_granularity": "parent",
        "gold_source": GOLD_SOURCE,
        "generated_by": "scripts/build_eval_v3.py",
        "cases": cases,
    }

    print("\n=== 统计 ===")
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    print("最终题数：", len(cases))

    if args.dry_run:
        print("（dry-run：未写文件）")
        return 0

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("已写入：", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
