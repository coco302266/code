"""知识库缺口事件（KB-gap）记录与聚合（2026-10-24，用户决策，XB-14）。

**解决什么问题**：检索到的知识库片段为空时，产品决策是**不在回答里告知学生**
（XB-13 的显式 notice 已按用户指示撤下），改为**结构化日志落盘 + 后期聚合报表**，
用于回答「知识库到底缺哪一类知识点」。

**数据从哪来**：/chat 每轮检索后，若 `kb_docs` 为空且评估节点抽出的 topic
不是 unknown（unknown = 纯寒暄，不算知识库缺失），记一条事件：
    ts / query / topic / used_topic / top_score / floor / filtered / user / classroom
逐行 JSON（JSONL），路径由 `RAG_KB_GAP_LOG` 环境变量控制，默认
`backend/logs/kb_gap_events.jsonl`。写入失败**绝不抛异常**——日志是旁路，
不能因为磁盘问题打断答题主链路。

**聚合口径**：topic 归一化与检索评测同约定（去空格 + 转小写），
`aggregate_events` 是纯函数（有单测）；报表 CLI 在 `eval/kb_gap_report.py`。

用法：
    from agent.rag.kb_gap import log_kb_gap
    log_kb_gap({"query": "...", "topic": "redis 持久化", ...})

    python -m eval.kb_gap_report            # 聚合当前事件出报表
"""

from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent  # agent/rag/ → backend/
DEFAULT_LOG_PATH = BACKEND_DIR / "logs" / "kb_gap_events.jsonl"
SAMPLE_QUERIES_PER_TOPIC = 3


def kb_gap_log_path() -> Path:
    """事件文件路径（环境变量可覆盖）。"""
    raw = (os.getenv("RAG_KB_GAP_LOG") or "").strip()
    return Path(raw) if raw else DEFAULT_LOG_PATH


def log_kb_gap(record: dict, path: str | Path | None = None) -> bool:
    """追加一条 KB-gap 事件（JSONL）。失败返回 False，不抛异常。

    记录字段不做白名单校验（调用方负责字段齐全），但必须能 JSON 序列化；
    序列化失败的键会被替换成 str() 兜底，保证事件不丢。
    """
    target = Path(path) if path is not None else kb_gap_log_path()
    safe: dict = {}
    for key, value in (record or {}).items():
        try:
            json.dumps(value, ensure_ascii=False)
            safe[str(key)] = value
        except (TypeError, ValueError):
            safe[str(key)] = str(value)
    safe.setdefault("ts", datetime.now().strftime("%Y-%m-%dT%H:%M:%S"))
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(safe, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return True
    except OSError:
        return False


def load_events(path: str | Path | None = None) -> list[dict]:
    """读回全部事件；坏行跳过并计数（不中断——半行日志不该废掉整份报表）。"""
    target = Path(path) if path is not None else kb_gap_log_path()
    if not target.exists():
        return []
    events: list[dict] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            events.append(parsed)
    return events


def normalize_topic(topic: str) -> str:
    """topic 归一化：去全部空格 + 转小写。

    与检索评测既有口径逐字符一致（build_gold.py / smoke_rag.py /
    chunk_quality.py 均为 `s.replace(" ", "").lower()`），不自造第二套。
    """
    return (topic or "").replace(" ", "").lower()


def aggregate_events(events: list[dict]) -> dict:
    """把事件按知识点聚合（纯函数，有单测）。

    聚合键 = 归一化 topic；同一 topic 的原始写法合并计数，
    样例 query 取最近写入的前 SAMPLE_QUERIES_PER_TOPIC 条。
    """
    groups: dict[str, dict] = {}
    for event in events:
        raw_topic = str(event.get("topic") or "（无 topic）")
        key = normalize_topic(raw_topic)
        group = groups.setdefault(
            key,
            {"topic": raw_topic, "count": 0, "sample_queries": [], "dates": Counter()},
        )
        group["count"] += 1
        query = str(event.get("query") or "").strip()
        if query:
            group["sample_queries"].append(query)
        ts = str(event.get("ts") or "")
        if ts[:10]:
            group["dates"][ts[:10]] += 1

    topics = []
    for group in groups.values():
        dates = dict(group["dates"])
        topics.append(
            {
                "topic": group["topic"],
                "count": group["count"],
                "sample_queries": group["sample_queries"][:SAMPLE_QUERIES_PER_TOPIC],
                "first_date": min(dates) if dates else None,
                "last_date": max(dates) if dates else None,
            }
        )
    topics.sort(key=lambda item: (-item["count"], item["topic"]))

    daily = Counter()
    for group in groups.values():
        for day, count in group["dates"].items():
            daily[day] += count

    return {
        "total_events": len(events),
        "distinct_topics": len(topics),
        "topics": topics,
        "by_date": dict(sorted(daily.items())),
    }
