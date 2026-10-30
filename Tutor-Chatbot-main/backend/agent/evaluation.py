"""M4-C 效果评估：把 `agent_runs` 的留痕聚合成「能写进报告的指标」。

对应阶段报告 §7 的成果 #4（评估报告）。这一层**只做聚合与渲染，零 IO、零 LLM**，
读数据由 `db/repository.list_agent_runs` 负责、拼报告由 `eval_report.py` 负责。
拆出来的理由和 `pretest.py` / `knowledge_graph.py` 一样：**能单测的东西不要
放进端点里**。指标口径（什么算一次降级、p95 怎么算）是会反复改的，纯函数才能
改一次测一次，不必为了验一个分位数去起服务、建数据。

设计取舍：

1. **客观指标与主观评分严格分开。** 延迟、角色数、降级率、检索覆盖率是机器能
   算的；「讲得准不准 / 贴不贴题 / 全不全 / 像不像给这个学生讲的」只能人评。
   把两者混在一张表里会让读者误以为前者也是打分打出来的。所以这里产出两份东西：
   `summarize_runs`（客观）+ 评分表（四列留空等人填）。
2. **延迟用分位数不用均值。** 均值会被一次冷启动（首次加载重排模型）拉偏，
   而「p95 多少毫秒」才是用户真实感知到的最坏情况。样本少时 p95 会退化成
   最大值，这是诚实的——报告里同时给出样本量，读者自己判断可信度。
3. **降级单独统计并保留原因原文。** 编排层的降级是静默的（回答完全正常），
   不单独列出来，「多场景没生效」在报告上就只是「平均 1.0 个 Agent」这种
   看不出问题的数字。
"""

from __future__ import annotations

import csv
import io
from collections import Counter

# 四项人工评分维度。顺序固定：评分表、CSV、报告表格都按这个顺序出，
# 换顺序等于让上一次填的表和下一次导出的列对不上。
SCORE_DIMENSIONS: tuple[tuple[str, str, str], ...] = (
    ("accuracy", "准确性", "讲解内容本身有没有事实错误（含术语、数字、机制描述）"),
    ("relevance", "相关性", "是否回答了学生真正问的那个点，有没有跑题或答非所问"),
    ("completeness", "完整性", "该讲到的要点是否覆盖（对照知识库对应章节判断）"),
    ("personalization", "个性化", "是否体现了该学生的画像（深浅、铺垫、历史薄弱点）"),
)

# 摘录长度：够人工判断「这条答得怎么样」，又不至于让评分表变成几千行。
EXCERPT_LEN = 120


def percentile(values: list[float], p: float) -> float:
    """最近秩分位数（nearest-rank）。

    不用线性插值：样本量小（评估报告常常只有几十轮）时插值会凭空造出一个
    从未发生过的数值，读者按它下结论是错的。最近秩保证返回值一定是真实观测。
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, int(round(p / 100 * len(ordered))))
    return float(ordered[min(rank, len(ordered)) - 1])


def _ratio(numerator: int, denominator: int) -> float:
    """安全比例，避免除零炸掉整份报告。"""
    return round(numerator / denominator, 4) if denominator else 0.0


def _round(value: float) -> float:
    return round(float(value), 2)


def summarize_runs(runs: list[dict]) -> dict:
    """把一轮轮编排留痕聚合成客观指标。

    入参是「字典化的 run」，字段与 `AgentRun` 对齐（见 `run_to_dict`）：
    scenario / roles / degraded / reason / duration_ms / rag_hits / topic。
    用字典而不是 ORM 对象，是为了让这一层不依赖数据库——测试里手搓几个
    字典就能验口径，出报告时也可以直接吃 HTTP 返回的 JSON。
    """
    turns = len(runs)
    if turns == 0:
        return {
            "turns": 0,
            "agents_per_turn": 0.0,
            "scenarios": {},
            "roles": {},
            "degraded_turns": 0,
            "degraded_rate": 0.0,
            "degrade_reasons": {},
            "latency_ms": {},
            "rag": {},
            "topics": {},
        }

    durations = [float(r.get("duration_ms") or 0) for r in runs]
    rag_hits = [int(r.get("rag_hits") or 0) for r in runs]
    degraded = [r for r in runs if r.get("degraded")]

    role_counter: Counter[str] = Counter()
    for run in runs:
        # 同一个角色在一轮里最多算一次：辩论场景里若将来允许同角色发言两次，
        # 统计的应该是「有多少轮用到了这个角色」，不是「总共发言几次」。
        for role in set(run.get("roles") or []):
            role_counter[str(role)] += 1

    topic_counter: Counter[str] = Counter()
    for run in runs:
        topic = (run.get("topic") or "").strip()
        if topic:
            topic_counter[topic] += 1

    return {
        "turns": turns,
        "agents_per_turn": _round(
            sum(len(r.get("roles") or []) for r in runs) / turns
        ),
        "scenarios": dict(
            Counter(str(r.get("scenario") or "v1") for r in runs).most_common()
        ),
        "roles": dict(role_counter.most_common()),
        "degraded_turns": len(degraded),
        "degraded_rate": _ratio(len(degraded), turns),
        "degrade_reasons": dict(
            Counter((r.get("reason") or "").strip() for r in degraded if (r.get("reason") or "").strip()).most_common()
        ),
        "latency_ms": {
            "mean": _round(sum(durations) / len(durations)),
            "p50": _round(percentile(durations, 50)),
            "p95": _round(percentile(durations, 95)),
            "min": _round(min(durations)),
            "max": _round(max(durations)),
        },
        "rag": {
            "turns_with_hits": sum(1 for h in rag_hits if h > 0),
            "coverage_rate": _ratio(sum(1 for h in rag_hits if h > 0), turns),
            "avg_hits": _round(sum(rag_hits) / len(rag_hits)),
        },
        "topics": dict(topic_counter.most_common()),
    }


def run_to_dict(run) -> dict:
    """ORM 对象 → 聚合层吃的字典。

    单独一个函数而不是在 repository 里直接 `asdict`：落库模型加字段时，
    只要这里漏了就会在指标里表现为「恒为 0」——显式列出字段能让这种
    遗漏在 review 时看得见。
    """
    return {
        "id": run.id,
        "classroom_id": run.classroom_id,
        "user_id": run.user_id,
        "scenario": run.scenario,
        "topic": run.topic,
        "roles": list(run.roles or []),
        "degraded": bool(run.degraded),
        "reason": run.reason or "",
        "duration_ms": run.duration_ms or 0,
        "rag_hits": run.rag_hits or 0,
        "message_ids": list(run.message_ids or []),
        "created_at": run.created_at.isoformat() if run.created_at else None,
    }


def _excerpt(text: str | None) -> str:
    """评分表里的原文摘录：压平换行，避免把 Markdown 表格撑破。"""
    flat = " ".join((text or "").split())
    if len(flat) <= EXCERPT_LEN:
        return flat
    return flat[:EXCERPT_LEN] + "…"


def score_sheet_rows(
    runs: list[dict], messages_by_id: dict[int, str] | None = None
) -> list[dict]:
    """生成人工评分表的行：**每个 Agent 的每次发言一行**。

    为什么按「发言」而不是按「轮次」评分：一轮里 tutor 讲得很好、peer 在复读，
    按轮打分只能给一个折中分，看不出问题出在哪个角色上——而这恰恰是多智能体
    评估最该回答的问题（哪个角色在贡献价值、哪个在凑数）。

    `messages_by_id` 用 message_id 找原文；查不到（消息被清理 / 不落库模式）
    就留空摘录，行仍然生成——评分表少一行比错位一行容易发现。
    """
    messages_by_id = messages_by_id or {}
    rows: list[dict] = []
    for run in runs:
        roles = list(run.get("roles") or [])
        ids = list(run.get("message_ids") or [])
        for index, role in enumerate(roles):
            message_id = ids[index] if index < len(ids) else None
            rows.append(
                {
                    "run_id": run.get("id"),
                    "created_at": run.get("created_at") or "",
                    "scenario": run.get("scenario") or "v1",
                    "role": role,
                    "index": index,
                    "topic": run.get("topic") or "",
                    "message_id": message_id,
                    "excerpt": _excerpt(messages_by_id.get(message_id, "")),
                    "degraded": bool(run.get("degraded")),
                    # 四个人工评分维度留空，由评分人填（1–5 分）
                    **{key: "" for key, _, _ in SCORE_DIMENSIONS},
                }
            )
    return rows


def render_scores_csv(rows: list[dict]) -> str:
    """评分表导出 CSV（UTF-8 BOM，Excel 直接打开不乱码）。"""
    header = ["run_id", "created_at", "scenario", "role", "index", "topic", "excerpt"]
    header += [label for _, label, _ in SCORE_DIMENSIONS]
    header += ["comment"]

    buffer = io.StringIO()
    # BOM 不是装饰：Windows Excel 按本地编码（GBK）打开无 BOM 的 UTF-8，
    # 中文会整片乱码，评分人一打开就想放弃。
    buffer.write("\ufeff")
    writer = csv.writer(buffer)
    writer.writerow(header)
    for row in rows:
        writer.writerow(
            [
                row.get("run_id", ""),
                row.get("created_at", ""),
                row.get("scenario", ""),
                row.get("role", ""),
                row.get("index", ""),
                row.get("topic", ""),
                row.get("excerpt", ""),
            ]
            + [row.get(key, "") for key, _, _ in SCORE_DIMENSIONS]
            + [""]
        )
    return buffer.getvalue()


def render_report_md(
    summary: dict,
    rows: list[dict],
    *,
    title: str = "AI Classroom 多智能体协作效果评估",
    scope: str = "",
    generated_at: str = "",
    diagnosis: dict | None = None,
) -> str:
    """渲染评估报告正文（Markdown）。

    报告只包含**能追到数据的结论**与**待人填的评分表**，不做主观判断——
    「效果好 / 不好」是写报告的人根据这些数字下的，不是脚本该替他说的。

    `diagnosis` 来自 `db.diagnostics.diagnose`。样本为 0 时它就是报告里唯一
    有价值的一段：一份只写「样本为 0」的报告等于让人重新排查一遍，
    而留痕为空至少有四种成因，光看 0 区分不出来。
    """
    lines: list[str] = [f"# {title}", ""]
    if generated_at:
        lines += [f"> 生成时间：{generated_at}", ""]
    if scope:
        lines += [f"> 样本范围：{scope}", ""]

    if not summary.get("turns"):
        lines += [
            "## 结论",
            "",
            "**样本为 0，本次评估无数据。**",
            "",
            "留痕为空有四种成因，表现完全一样，按下面的顺序排查：",
            "",
            "1. 后端没配 `MYSQL_*`（不落库模式）——/chat 正常但零留痕；",
            "2. MySQL 连不上（服务没起 / 库没建 / 账号密码错）；",
            "3. `agent_runs` 表不存在——漏跑 `python init_db.py`；",
            "4. 数据写进去了但被过滤条件挡掉——课堂 id / 场景 / 学生 id 不对。",
            "",
        ]
        if diagnosis:
            lines += [
                "### 本次自检结果",
                "",
                "| 项 | 值 |",
                "|---|---|",
                f"| 数据库 | `{diagnosis.get('database')}` |",
                f"| agent_runs 表 | {'存在' if (diagnosis.get('table') or {}).get('exists') else '缺失'} |",
                f"| 留痕总行数（不受过滤影响） | {diagnosis.get('total_runs')} |",
                "",
            ]
            hint = diagnosis.get("hint")
            if hint:
                lines += [f"> **下一步：{hint}**", ""]
        return "\n".join(lines)

    # 有数据但全部无课堂：能出客观指标，评分表却没有原文可评。不提示的话
    # 评分人会对着一片空白的摘录列发懵。
    unscoped = (diagnosis or {}).get("runs_without_classroom") or 0
    if unscoped and unscoped == summary.get("turns"):
        lines += [
            "> 注意：全部留痕都没有 `classroom_id`（/chat 未带课堂），",
            "> 发言正文不落库，人工评分表没有原文可评。",
            "",
        ]

    latency = summary.get("latency_ms") or {}
    rag = summary.get("rag") or {}

    lines += [
        "## 一、客观指标（机器统计）",
        "",
        "| 指标 | 值 | 说明 |",
        "|---|---|---|",
        f"| 样本轮数 | {summary['turns']} | 一次 `/chat` = 一轮 |",
        f"| 平均每轮 Agent 数 | {summary['agents_per_turn']} | 1.0 表示多智能体未生效 |",
        f"| 降级轮数 | {summary['degraded_turns']} | 占比 {summary['degraded_rate']:.1%} |",
        f"| 延迟 均值 / p50 | {latency.get('mean', 0)} / {latency.get('p50', 0)} ms | 端到端一轮总耗时 |",
        f"| 延迟 p95 / 最大 | {latency.get('p95', 0)} / {latency.get('max', 0)} ms | 最坏情况 |",
        f"| 检索覆盖率 | {rag.get('coverage_rate', 0):.1%} | 有资料注入的轮次占比 |",
        f"| 平均命中资料数 | {rag.get('avg_hits', 0)} | 一轮只检索一次，全角色共用 |",
        "",
        "> 检索覆盖率注意：`rag_hits` 是 M4-C 才补的列，**在此之前落的留痕记为 0**，",
        "> 样本跨越该时间点时覆盖率会被低估。要干净的检索数据请只用新跑的轮次。",
        "",
    ]

    lines += ["### 场景分布", "", "| 场景 | 轮数 |", "|---|---|"]
    for scenario, count in (summary.get("scenarios") or {}).items():
        lines.append(f"| `{scenario}` | {count} |")
    lines.append("")

    lines += ["### 角色参与轮次", "", "| 角色 | 参与轮数 |", "|---|---|"]
    for role, count in (summary.get("roles") or {}).items():
        lines.append(f"| `{role}` | {count} |")
    lines.append("")

    reasons = summary.get("degrade_reasons") or {}
    lines += [
        "### 降级明细",
        "",
        "编排降级是**静默**的：回答完全正常，唯一症状是多场景没生效。",
        "下表为空表示样本期内没有发生过降级。",
        "",
        "| 原因 | 次数 |",
        "|---|---|",
    ]
    if reasons:
        for reason, count in reasons.items():
            lines.append(f"| {reason} | {count} |")
    else:
        lines.append("| （无） | 0 |")
    lines.append("")

    lines += [
        "## 二、人工评分（待填）",
        "",
        "四个维度各打 1–5 分（5 最好）。评分对象是**每个 Agent 的每次发言**，",
        "不是整轮——否则「tutor 讲得好、peer 在复读」会被折中成一个看不出问题的分。",
        "",
        "| 维度 | 含义 |",
        "|---|---|",
    ]
    for _, label, desc in SCORE_DIMENSIONS:
        lines.append(f"| {label} | {desc} |")
    lines += [
        "",
        f"评分表见同目录下的 `eval_scores.csv`，共 **{len(rows)}** 行。",
        "",
    ]

    if rows:
        lines += [
            "### 评分样本预览（前 10 行）",
            "",
            "| # | 场景 | 角色 | 知识点 | 发言摘录 |",
            "|---|---|---|---|---|",
        ]
        for row in rows[:10]:
            excerpt = (row.get("excerpt") or "").replace("|", "\\|")
            lines.append(
                f"| {row.get('run_id')} | `{row.get('scenario')}` | "
                f"`{row.get('role')}` | {row.get('topic') or '-'} | {excerpt or '（无原文）'} |"
            )
        lines.append("")

    return "\n".join(lines)
