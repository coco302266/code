"""知识库缺口事件模块的单测（XB-14，2026-10-24）。

覆盖：log_kb_gap 追加写入 / 失败不抛异常 / 字段序列化兜底；
load_events 坏行跳过；normalize_topic 归一化；aggregate_events 纯函数聚合。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent.rag.kb_gap import (
    aggregate_events,
    load_events,
    log_kb_gap,
    normalize_topic,
)


class LogKbGapTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "events.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def test_appends_json_line_and_returns_true(self):
        self.assertTrue(log_kb_gap({"query": "redis 持久化", "topic": "redis 持久化"}, self.path))
        self.assertTrue(log_kb_gap({"query": "cap 定理", "topic": "cap 定理"}, self.path))
        lines = self.path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        first = json.loads(lines[0])
        self.assertEqual(first["query"], "redis 持久化")
        self.assertIn("ts", first)

    def test_unserializable_value_falls_back_to_str(self):
        # 不可 JSON 序列化的值降级为 str，事件不丢
        self.assertTrue(log_kb_gap({"query": "q", "obj": object()}, self.path))
        record = json.loads(self.path.read_text(encoding="utf-8").splitlines()[0])
        self.assertIsInstance(record["obj"], str)

    def test_failure_returns_false_without_raising(self):
        # 目标是一个目录而不是文件 → open 必然 OSError → 返回 False 不抛
        self.assertFalse(log_kb_gap({"query": "q"}, Path(self._tmp.name)))

    def test_default_ts_added(self):
        log_kb_gap({"query": "q"}, self.path)
        record = json.loads(self.path.read_text(encoding="utf-8").splitlines()[0])
        self.assertRegex(record["ts"], r"^\d{4}-\d{2}-\d{2}T")


class LoadEventsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "events.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def test_missing_file_returns_empty(self):
        self.assertEqual(load_events(self.path), [])

    def test_bad_lines_skipped(self):
        self.path.write_text(
            '{"query": "a", "topic": "t"}\n'
            "不是json\n"
            "\n"
            '[也不是dict]\n'
            '{"query": "b", "topic": "t"}\n',
            encoding="utf-8",
        )
        events = load_events(self.path)
        self.assertEqual([e["query"] for e in events], ["a", "b"])


class NormalizeTopicTests(unittest.TestCase):
    def test_strips_and_lowercases(self):
        self.assertEqual(normalize_topic("  TCP 与 UDP "), "tcp与udp")
        self.assertEqual(normalize_topic("CAP 定理"), "cap定理")
        self.assertEqual(normalize_topic(""), "")
        self.assertEqual(normalize_topic(None), "")


class AggregateEventsTests(unittest.TestCase):
    def test_empty_input(self):
        result = aggregate_events([])
        self.assertEqual(result["total_events"], 0)
        self.assertEqual(result["topics"], [])
        self.assertEqual(result["by_date"], {})

    def test_groups_by_normalized_topic_and_merges_spellings(self):
        events = [
            {"query": "q1", "topic": "CAP 定理", "ts": "2026-10-24T10:00:00"},
            {"query": "q2", "topic": "cap定理", "ts": "2026-10-24T11:00:00"},
            {"query": "q3", "topic": "redis 持久化", "ts": "2026-10-23T09:00:00"},
        ]
        result = aggregate_events(events)
        self.assertEqual(result["total_events"], 3)
        self.assertEqual(result["distinct_topics"], 2)
        # 按次数降序：cap定理 2 次 > redis持久化 1 次
        self.assertEqual(result["topics"][0]["topic"], "CAP 定理")
        self.assertEqual(result["topics"][0]["count"], 2)
        self.assertEqual(result["topics"][0]["last_date"], "2026-10-24")
        self.assertEqual(result["topics"][1]["first_date"], "2026-10-23")

    def test_sample_queries_capped(self):
        from agent.rag.kb_gap import SAMPLE_QUERIES_PER_TOPIC

        events = [
            {"query": "q%d" % i, "topic": "t", "ts": "2026-10-24T00:00:00"}
            for i in range(10)
        ]
        result = aggregate_events(events)
        self.assertEqual(len(result["topics"][0]["sample_queries"]), SAMPLE_QUERIES_PER_TOPIC)

    def test_missing_topic_grouped_under_placeholder(self):
        result = aggregate_events([{"query": "q", "ts": "2026-10-24T00:00:00"}])
        self.assertEqual(result["topics"][0]["topic"], "（无 topic）")

    def test_tie_breaks_by_topic_name(self):
        events = [
            {"query": "a", "topic": "bbb", "ts": "2026-10-24T00:00:00"},
            {"query": "b", "topic": "aaa", "ts": "2026-10-24T01:00:00"},
        ]
        result = aggregate_events(events)
        self.assertEqual([t["topic"] for t in result["topics"]], ["aaa", "bbb"])


if __name__ == "__main__":
    unittest.main()
