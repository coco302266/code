"""细粒度金标准标注（PROPOSAL §2.2 的 gain 三档，Wave 7 收尾 / P2）。

**解决什么问题**：`gold_source: topic-derived` 的金标准 = 期望主题的切片全集，
每片都默认 gain=1。它度量「top-k 对主题切片全集的覆盖」，回答不了
「这片是否真含答案」——同一文档里往往只有 1~2 片真正对题，其余是
背景甚至无关小节，把 nDCG 的分母灌了水。

**本工具做什么**：用裁判 LLM（temperature=0，3 票多数，平票取保守低档）
对每条计分用例的主题切片集重新分档：
    gain 1.0  片段直接含答案核心
    gain 0.5  相关背景 / 部分支撑
    gain 0.0  无关（同文档其他小节）
写回 `rag_eval_cases.json`：`relevant_chunks` 元组从 [source, start_index]
升级为 [source, start_index, gain]，`gold_source` 升级为
`topic-derived+llm-refined`。metrics.chunk_rel 原生支持第三位增益
（[source, start_index, gain]），不需要改指标代码。

**人工复核约定**（与成果 #4 评分同一约定）：这是 AI 初评，
正确性由标注流程兜底（3 票 + 保守侧），`_note` 字段如实标注
`[AI标注·待人工复核]`；人工复核只需看低置信条目（投票不一致的），
报告会单独列出来。

**成本与节奏**：每条用例 1 次调用列出全部切片 × 3 票 ≈ 237 次裁判调用
（79 条计分用例，含 8 条 cascade）。用例级并发 `--workers 4` 约 15 分钟，
串行约 1 小时。一次性标注，标注完成后不用重跑；**库重建或换切片粒度
（flat ↔ parent）后必须重标**——坐标和 gain 会同时失效。

**粒度必须与金标准同源**：本工具按库形态（有无 parent_id）决定聚合，
用 `aggregate_parent_docs` 还原出与 `/chat` 注入一致的整节正文；
金标准声明的 `gold_granularity` 与库实际粒度不符时直接拒绝标注，
否则取不到正文 → 裁判一律给 0 → 金标准静默损坏。

**口径提醒**：金标准升级后 nDCG 与历史值**不可直比**（分母变了）；
top1/top3 判定不受影响（它们走 topic 匹配，不看 gain）。

用法（在 backend/ 下）：
    python -m eval.refine_gold --dry-run --limit 2   # 先看 2 条的效果
    python -m eval.refine_gold --apply               # 全量标注并写回
    python -m eval.refine_gold --report              # 只看上次的低置信条目
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from eval.judge_prompts import GAIN_REFINE_PROMPT  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parent.parent
CASES_FILE = BACKEND_DIR / "rag_eval_cases.json"
REPORT_PATH = Path(__file__).resolve().parent / "reports" / "refine_gold_report.json"

GAIN_FULL = 1.0
GAIN_PARTIAL = 0.5
GAIN_NONE = 0.0
VALID_GAINS = (GAIN_FULL, GAIN_PARTIAL, GAIN_NONE)
# 每片喂给裁判的字符上限。
# **必须覆盖整个注入单元**：早期取 200 是为了省 token（那时片只有 500 字），
# 换到 parent 粒度后每片 600~900 字，200 字截断会让裁判**看不到后半部分**——
# 实测「银行家算法是做什么的」整题被判全 0，而 deadlock.md 的「标准答案」节
# 第 900 字处明明写着银行家算法：裁判没看到，不是库里没有。
# 当前库 parent 中位 331 / 最长 1994 字，取 2000 可全覆盖；真有更长的片会被
# `truncated_chunks` 统计并打印提示，不静默截断。
CHUNK_TEXT_LIMIT = 2000
REFINED_GOLD_SOURCE = "topic-derived+llm-refined"
HUMAN_REVIEW_TAG = "[AI标注·待人工复核]"


# ---------------------------------------------------------------------------
# 纯函数（有单测）
# ---------------------------------------------------------------------------

def sanitize_gain(value) -> float | None:
    """裁判给的增益校验为三档之一；其他值返回 None（按废票处理）。"""
    try:
        gain = float(value)
    except (TypeError, ValueError):
        return None
    for valid in VALID_GAINS:
        if abs(gain - valid) < 1e-6:
            return valid
    return None


def majority_gain(votes: list[float | None]) -> tuple[float, bool]:
    """每片的多数增益。返回 (增益, 是否低置信)。

    平票取**保守低档**（1 与 0.5 平 → 0.5；0.5 与 0 平 → 0）——
    金标准宁严勿松：把弱相关标成强相关会让 nDCG 虚高，反之只是难看。
    全部废票按 0 处理并标低置信。
    """
    valid = [v for v in votes if v is not None]
    if not valid:
        return GAIN_NONE, True
    counts = Counter(valid)
    top = max(counts.values())
    winners = [g for g, c in counts.items() if c == top]
    if len(winners) > 1:
        return min(winners), True
    return winners[0], len(valid) < len(votes)  # 有废票也算低置信


def parse_gain_response(text: str, chunk_count: int) -> list[float | None]:
    """解析裁判输出 → 每片的增益（按片段编号对位）。

    编号缺失 / 越界的按 None（废票）处理；重复编号取首次出现
    （裁判偶尔重复，两次说法不一致时没有取舍依据，保守取先）。
    """
    from eval.grounding_eval import extract_json

    parsed = extract_json(text)
    if not parsed:
        return [None] * chunk_count
    gains: list[float | None] = [None] * chunk_count
    for item in parsed.get("gains") or []:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        if not 1 <= index <= chunk_count or gains[index - 1] is not None:
            continue
        gains[index - 1] = sanitize_gain(item.get("gain"))
    return gains


def build_judge_input(query: str, chunk_texts: list[str], limit: int | None = None) -> str:
    """组装裁判输入：编号片段列表（每片截断到 limit，默认 CHUNK_TEXT_LIMIT）。"""
    cap = CHUNK_TEXT_LIMIT if limit is None else limit
    blocks = [
        "[%d] %s" % (i + 1, (text or "").strip()[:cap])
        for i, text in enumerate(chunk_texts)
    ]
    return GAIN_REFINE_PROMPT.format(query=query, chunks="\n\n".join(blocks))


def truncated_chunks(chunk_texts: list[str], limit: int | None = None) -> int:
    """有多少片正文超过喂给裁判的上限（>0 说明裁判看的是残篇，读结论要打折）。"""
    cap = CHUNK_TEXT_LIMIT if limit is None else limit
    return sum(1 for t in chunk_texts if len((t or "").strip()) > cap)


# ---------------------------------------------------------------------------
# IO 层
# ---------------------------------------------------------------------------

def judge_case(judge_llm, query: str, chunk_texts: list[str], votes: int) -> tuple[list[float], int, int]:
    """一条用例的标注：votes 票逐片多数。

    返回 (与 chunk_texts 对位的增益, 废票数, 票间不一致的片数)。

    第三个值是"人工复核"的**直接证据**：`majority_gain` 在平票或有废票时
    会标 low，说明裁判自己没定下来。早期版本把它丢掉、改用"0.5 档占比 ≥50%"
    当代理——4 片粒度下 `1/2/1`（一个满分、两个部分、一个无关）是完全健康的
    分布，却会被判成低置信，名单涨到 32/75，等于没有指引。
    """
    from langchain_core.messages import HumanMessage

    prompt = build_judge_input(query, chunk_texts)
    per_vote: list[list[float | None]] = []
    dead = 0
    for _ in range(votes):
        try:
            resp = judge_llm.invoke([HumanMessage(content=prompt)])
        except Exception as exc:  # 网络抖动按废票处理，不让一条用例中断全量
            print("    裁判调用失败（按废票）：%s: %s" % (type(exc).__name__, exc), flush=True)
            per_vote.append([None] * len(chunk_texts))
            dead += 1
            continue
        per_vote.append(parse_gain_response(getattr(resp, "content", "") or "", len(chunk_texts)))
    refined: list[float] = []
    disagree = 0
    for position in range(len(chunk_texts)):
        gain, low = majority_gain([v[position] for v in per_vote])
        refined.append(gain)
        if low:
            disagree += 1
    return refined, dead, disagree


def load_store():
    from agent.rag.indexer import load_knowledge_index

    store = load_knowledge_index("cs_basics", str(BACKEND_DIR / "chroma_kb"))
    if store is None:
        raise SystemExit("知识库不存在：先跑 python build_kb.py")
    return store


def iter_refinable(data: dict, force: bool = False):
    """产出 (case, gold)；默认只处理带 relevant_chunks 且未升级过的计分用例。

    幂等保护：已升级的用例默认跳过，避免重跑覆盖已复核的结果。
    换粒度 / 换裁判 / 改低置信口径后需要重标时，用 `--force` 显式越过这层保护。
    """
    for case in data.get("cases", []):
        gold = case.get("relevant_chunks") or []
        if not gold:
            continue
        if not force and case.get("gold_source") == REFINED_GOLD_SOURCE:
            continue
        yield case, gold


def build_chunk_index(store) -> dict[tuple[str, int], str]:
    """按**检索返回粒度**建 (source, start_index) → 正文 的索引。

    父子库必须走聚合：金标准坐标是 `(source, parent_start)`，而库里存的是
    child 的 `start_index`——直查库里那张表，parent 坐标一个都命中不了，
    全部退化成空串，裁判只能一律给 0（写回后金标准静默损坏，且看起来"标完了"）。
    复用 `aggregate_parent_docs` 而不是重新实现一遍，保证这里看到的小节正文
    与 /chat 实际注入的完全一致。
    """
    from langchain_core.documents import Document

    from agent.rag.retriever import aggregate_parent_docs

    raw = store.get(include=["documents", "metadatas"])
    docs = [
        Document(page_content=text or "", metadata=meta or {})
        for text, meta in zip(raw.get("documents") or [], raw.get("metadatas") or [])
    ]
    index: dict[tuple[str, int], str] = {}
    for doc in aggregate_parent_docs(docs):
        meta = doc.metadata or {}
        index[(str(meta.get("source") or ""), int(meta.get("start_index") or 0))] = doc.page_content
    return index


def fetch_chunk_texts(store, gold: list, index: dict[tuple[str, int], str] | None = None) -> list[str]:
    """按 (source, start_index) 取金标准各片的正文，保持 gold 顺序。

    取不到的片正文记空串——调用方会统计空串比例，过高说明金标准与当前库
    不同源（比如换了切片粒度却没重刷金标准），必须停下而不是硬标。
    """
    by_key = index if index is not None else build_chunk_index(store)
    texts = []
    for item in gold:
        key = (str(item[0]), int(item[1]))
        texts.append(by_key.get(key, ""))
    return texts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="细粒度金标准标注（AI 初评，3 票保守）")
    parser.add_argument("--apply", action="store_true", help="写回评测集（缺省只做 dry-run 不落盘）")
    parser.add_argument("--votes", type=int, default=3, help="每条用例的裁判票数")
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="用例级并发数（每个 worker 内部仍串行投 votes 票，保序写回）。串行跑 79 题约 1 小时，4 并发约 15 分钟",
    )
    parser.add_argument("--limit", type=int, default=0, help="只标前 N 条（试跑用，0 = 全量）")
    parser.add_argument("--report", action="store_true", help="只打印上次报告的低置信条目")
    parser.add_argument(
        "--force",
        action="store_true",
        help="重新标注已升级的用例（换切片粒度 / 换裁判 / 改低置信口径后需要；默认跳过以保护已复核结果）",
    )
    args = parser.parse_args(argv)

    if args.report:
        if not REPORT_PATH.exists():
            print("还没有标注报告：%s" % REPORT_PATH)
            return 0
        report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
        low = report.get("low_confidence") or []
        print("低置信条目 %d 条（需人工复核）：" % len(low))
        for item in low:
            reason = item.get("reason")
            print(
                "  %s | %s%s"
                % (item.get("id"), item.get("query", "")[:30], "  ← %s" % reason if reason else "")
            )
        return 0

    data = json.loads(CASES_FILE.read_text(encoding="utf-8"))
    targets = list(iter_refinable(data, force=args.force))
    if args.limit:
        targets = targets[: args.limit]
    if not targets:
        print("没有待标注的用例（全部已升级或无 relevant_chunks）")
        return 0

    store = load_store()

    # 粒度守卫：金标准坐标必须与库的检索返回粒度同源，否则取正文全空、
    # 裁判一律给 0，写回后金标准静默损坏（而且控制台看起来"标注完成"）。
    from eval.build_gold import detect_granularity

    raw_metas = (store.get(include=["metadatas"]) or {}).get("metadatas") or []
    granularity = detect_granularity(raw_metas)
    gold_granularity = data.get("gold_granularity")
    if gold_granularity and gold_granularity != granularity:
        print(
            "✗ 金标准粒度 %s 与库实际粒度 %s 不符：先跑 python -m eval.build_gold 重刷金标准"
            % (gold_granularity, granularity)
        )
        return 1

    index = build_chunk_index(store)
    empty_probes = [
        sum(1 for t in fetch_chunk_texts(store, gold, index) if not t) for _case, gold in targets[:20]
    ]
    if empty_probes and sum(empty_probes) > 0.5 * sum(len(gold) for _c, gold in targets[:20]):
        print("✗ 前 20 条用例超过一半的切片取不到正文：金标准坐标与库不同源，拒绝标注。")
        print("  先跑 python -m eval.build_gold --dry-run 看看派生的粒度，再重刷金标准。")
        return 1

    from eval.grounding_eval import make_judge_llm

    judge_llm = make_judge_llm()

    report = {
        "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S%z"),
        "votes": args.votes,
        "applied": bool(args.apply),
        "granularity": granularity,
        "cases_total": len(targets),
        "low_confidence": [],
        "per_case": [],
    }
    changed = 0
    dead_votes = 0
    truncated_total = 0

    def annotate(item):
        """一个用例的完整标注（取正文 + votes 票裁判）。并发执行体。"""
        case, gold = item
        query = (case.get("query") or "").strip()
        texts = fetch_chunk_texts(store, gold, index)
        gains, dead, disagree = judge_case(judge_llm, query, texts, args.votes)
        return case, gold, query, gains, dead, disagree, truncated_chunks(texts)

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        for i, (case, gold, query, gains, dead, disagree, cut) in enumerate(
            pool.map(annotate, targets), 1
        ):
            dead_votes += dead
            truncated_total += cut
            partial_ratio = sum(1 for g in gains if g == GAIN_PARTIAL) / max(len(gains), 1)
            all_zero = bool(gains) and all(g == GAIN_NONE for g in gains)
            row = {
                "id": case.get("id"),
                "query": query,
                "chunks": len(gains),
                "gain_hist": {str(g): gains.count(g) for g in VALID_GAINS},
                "partial_ratio": round(partial_ratio, 3),
                "disagree_chunks": disagree,
            }
            # 低置信名单只收「有证据」的两种：
            #   1) 票间不一致（裁判自己没定下来）——直接证据；
            #   2) 整题全判 0——要么库里真没有，要么裁判整题判错。
            #      实测踩过：问「银行家算法是做什么的」四片全 0，而
            #      deadlock.md 的「标准答案」节明明写着银行家算法。
            # 「0.5 占比高」不再作为判据：4 片粒度下 1/2/1 是健康分布。
            if disagree > 0 or all_zero:
                reasons = []
                if disagree:
                    reasons.append("%d 片票间不一致" % disagree)
                if all_zero:
                    reasons.append("整题全判 0")
                report["low_confidence"].append(
                    {"id": case.get("id"), "query": query, "reason": "；".join(reasons)}
                )
            report["per_case"].append(row)

            if args.apply:
                case["relevant_chunks"] = [
                    [item[0], item[1], float(gain)] for item, gain in zip(gold, gains)
                ]
                case["gold_source"] = REFINED_GOLD_SOURCE
                case["_note"] = "%s gain 三档由裁判 LLM 标注（%d 票多数，平票取低档）" % (
                    HUMAN_REVIEW_TAG,
                    args.votes,
                )
                changed += 1
            print(
                "  [%d/%d] %s… %d 片（1/0.5/0 = %d/%d/%d）%s"
                % (
                    i,
                    len(targets),
                    query[:16],
                    len(gains),
                    gains.count(GAIN_FULL),
                    gains.count(GAIN_PARTIAL),
                    gains.count(GAIN_NONE),
                    "  [废票 %d]" % dead if dead else "",
                ),
                flush=True,
            )

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    report["dead_votes"] = dead_votes
    report["truncated_chunks"] = truncated_total
    report["chunk_text_limit"] = CHUNK_TEXT_LIMIT
    total_votes = len(targets) * max(args.votes, 1)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.apply and changed:
        CASES_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("已写回 %s（%d 条升级为 %s）" % (CASES_FILE, changed, REFINED_GOLD_SOURCE))
    else:
        print("dry-run 完成，未写回。确认后加 --apply 执行全量标注。")

    print(
        "低置信（票间不一致 / 整题全判 0）%d/%d 条，明细见 %s"
        % (len(report["low_confidence"]), len(targets), REPORT_PATH)
    )
    if dead_votes:
        rate = dead_votes / max(total_votes, 1)
        print("! 废票 %d/%d（%.1f%%）：废票按 gain=0 计入，比例偏高会把 nDCG 分母灌水" % (dead_votes, total_votes, rate * 100))
        if rate > 0.1:
            print("  超过 10%：多半是并发过高触发限流，降 --workers 重跑更稳。")
    if truncated_total:
        print(
            "! 有 %d 片正文超过裁判输入上限 %d 字被截断：残篇判出的 gain 偏保守，"
            "相关用例要人工看一眼" % (truncated_total, CHUNK_TEXT_LIMIT)
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
