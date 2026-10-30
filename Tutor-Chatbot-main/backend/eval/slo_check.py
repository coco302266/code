"""降级显式化：把「降级」从耗时里剥出来单独报（PROPOSAL Wave 4）。

**为什么不直接报 p95**：链路一旦降级（重排模型没起来、BM25 语料没建、库是空的），
走的是快路径，耗时天然更短。降级多了，p95 会**变好看**——
一个"性能达标"的报告可能只是一堆失败请求在拉低平均值。
所以耗时必须分两组报：全量 p95 与「无降级子集」p95，后者才是真实体验。

**降级名单**（来自 `agent.rag.retriever._mark_degraded`，改名要同步这里）：
    no_store              知识库没接上（vectorstore=None）
    bm25_unavailable      BM25 语料没建起来，混合召回整条失效
    rerank_unavailable    重排模型加载失败
    rerank_error          重排调用抛异常
    rerank_count_mismatch 重排返回分数条数对不上

用法（在 backend/ 下）：
    python -m eval.slo_check --input reports/combined/traces.json
    python -m eval.slo_check --input rag_baseline.json --p95 3.0 --degraded-rate 0.02
    python -m eval.slo_check --input rag_baseline.json --json   # 给 CI 吃

输入兼容两种形态（都从 smoke_rag.py 产出）：
    {"cases": [...]}   单配置结果（--save-base 的产出）
    [...]              裸列表
每行至少要有 `secs`；降级看 `degraded_components`（缺则退回 `degraded` 字符串）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from eval.metrics import p95

# retriever 里 _mark_degraded 用到的组件名。改名字要同步这两处，
# 否则降级会被这里当成"未知组件"，照样计数但归类说不清。
DEGRADED_COMPONENTS = (
    "no_store",
    "bm25_unavailable",
    "rerank_unavailable",
    "rerank_error",
    "rerank_count_mismatch",
)

TIME_KEYS = ("secs", "seconds", "elapsed_s", "elapsed")


def degraded_of(record: dict) -> list[str]:
    """取一条记录的降级组件列表。

    兼容三种写法：`degraded_components`（列表，首选）、`degraded`（列表）、
    `degraded`（逗号串，旧 trace 的写法）。
    """
    comps = record.get("degraded_components")
    if isinstance(comps, (list, tuple)):
        return [str(c) for c in comps if c]
    raw = record.get("degraded")
    if isinstance(raw, (list, tuple)):
        return [str(c) for c in raw if c]
    if raw:
        return [c for c in str(raw).split(",") if c]
    return []


def secs_of(record: dict) -> float:
    for key in TIME_KEYS:
        if key in record:
            try:
                return float(record[key])
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def load_records(path: str | Path) -> list[dict]:
    """读输入文件，兼容 {"cases": [...]} 与裸列表两种形态。"""
    target = Path(path)
    if not target.exists():
        raise SystemExit("输入文件不存在：%s" % target)
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit("输入不是合法 JSON：%s（%s）" % (target, exc))
    if isinstance(data, dict):
        return list(data.get("cases") or [])
    if isinstance(data, list):
        return data
    raise SystemExit("输入格式不认识：期望 {\"cases\": [...]} 或 [...]，实际 %s" % type(data).__name__)


def summarize(records: list[dict]) -> dict:
    """按「有没有降级」分组统计耗时与降级分布。"""
    degraded_rows = [r for r in records if degraded_of(r)]
    clean_rows = [r for r in records if not degraded_of(r)]

    by_component: dict[str, int] = {}
    for row in degraded_rows:
        for comp in degraded_of(row):
            by_component[comp] = by_component.get(comp, 0) + 1

    all_secs = [secs_of(r) for r in records]
    clean_secs = [secs_of(r) for r in clean_rows]

    return {
        "cases": len(records),
        "clean_cases": len(clean_rows),
        "degraded_cases": len(degraded_rows),
        "degraded_rate": round(len(degraded_rows) / len(records), 4) if records else 0.0,
        "by_component": dict(sorted(by_component.items(), key=lambda kv: -kv[1])),
        "p95_all": round(p95(all_secs), 4),
        "p95_clean": round(p95(clean_secs), 4),
        "mean_all": round(sum(all_secs) / len(all_secs), 4) if all_secs else 0.0,
        "mean_clean": round(sum(clean_secs) / len(clean_secs), 4) if clean_secs else 0.0,
    }


def check(
    records: list[dict],
    p95_budget: float | None = None,
    degraded_budget: float | None = None,
) -> tuple[bool, list[str]]:
    """按预算判定是否通过，返回 (是否通过, 违规说明)。

    p95 判的是**无降级子集**：降级请求走的是另一条路径，混进来没有意义。
    降级率单独判，两个维度都不许糊过去。
    """
    stat = summarize(records)
    violations = []

    if not records:
        violations.append("没有记录，无法判定（空输入不算通过）")
        return False, violations

    if p95_budget is not None:
        if stat["clean_cases"] == 0:
            violations.append("全部请求都降级了，无降级 p95 无从计算")
        elif stat["p95_clean"] > p95_budget:
            violations.append(
                "无降级 p95 %.3fs 超出预算 %.3fs（样本 %d 条）"
                % (stat["p95_clean"], p95_budget, stat["clean_cases"])
            )

    if degraded_budget is not None and stat["degraded_rate"] > degraded_budget:
        violations.append(
            "降级率 %.2f%% 超出预算 %.2f%%（%d/%d 条）"
            % (
                stat["degraded_rate"] * 100,
                degraded_budget * 100,
                stat["degraded_cases"],
                stat["cases"],
            )
        )

    return (not violations), violations


def describe(records: list[dict], p95_budget=None, degraded_budget=None) -> str:
    """给人看的多行报告。"""
    stat = summarize(records)
    lines = [
        "降级与耗时（降级显式化）",
        "  样本 %d 条：无降级 %d / 降级 %d（降级率 %.2f%%）"
        % (
            stat["cases"],
            stat["clean_cases"],
            stat["degraded_cases"],
            stat["degraded_rate"] * 100,
        ),
        "  耗时 p95：全体 %.3fs ｜ 无降级子集 %.3fs" % (stat["p95_all"], stat["p95_clean"]),
        "  耗时均值：全体 %.3fs ｜ 无降级子集 %.3fs" % (stat["mean_all"], stat["mean_clean"]),
    ]
    if stat["by_component"]:
        lines.append("  降级分布：")
        for comp, count in stat["by_component"].items():
            known = "" if comp in DEGRADED_COMPONENTS else "（未知组件，检查 retriever 是否已改名）"
            lines.append("    - %s × %d%s" % (comp, count, known))
    else:
        lines.append("  降级分布：无")

    if p95_budget is not None:
        lines.append("  p95 预算 %.3fs（只对无降级子集判定）" % p95_budget)
    if degraded_budget is not None:
        lines.append("  降级率预算 %.2f%%" % (degraded_budget * 100))

    ok, violations = check(records, p95_budget, degraded_budget)
    if ok:
        lines.append("  ✓ 在预算内")
    else:
        lines.append("  ✗ 未达标：")
        lines.extend("    - %s" % v for v in violations)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="RAG 降级显式化与耗时 SLO 检查")
    parser.add_argument("--input", required=True, help="smoke_rag.py --save-base 产出的 JSON")
    parser.add_argument("--p95", type=float, default=None, help="无降级子集的 p95 预算（秒）")
    parser.add_argument("--degraded-rate", type=float, default=None, help="降级率预算（0~1）")
    parser.add_argument("--json", action="store_true", help="只输出 JSON，给 CI 解析")
    args = parser.parse_args(argv)

    records = load_records(args.input)
    if args.json:
        stat = summarize(records)
        ok, violations = check(records, args.p95, args.degraded_rate)
        print(json.dumps({"ok": ok, "violations": violations, **stat}, ensure_ascii=False, indent=2))
        return 0 if ok else 1

    print(describe(records, args.p95, args.degraded_rate))
    ok, _ = check(records, args.p95, args.degraded_rate)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
