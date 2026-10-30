"""eval/build_gold.py 的单元测试（金标准派生）。

锁四件事：
    1. topic 匹配必须与 smoke_rag.top_hit 同口径（归一化）——
       评测集里 `tcp 与 udp`（小写带空格）vs 库内 `TCP 与 UDP` 的历史写法
       曾让精确匹配整题失配，这里用真实案例锁死。
    2. 失配必须显式暴露（非 0 退出），不允许静默产出空金标准。
    3. **粒度必须跟随库形态**：父子库里 `aggregate_parent_docs` 把
       `start_index` 换成了 `parent_start`，金标准若仍按 child 坐标派生，
       L1 指标会全量错配且不报错（静默归零）。这里锁 parent 粒度的坐标与去重。
    4. flat 粒度行为逐字不变（改造前 66 片库的口径），防止升级时误伤。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.build_gold import (  # noqa: E402
    GRANULARITY_CHILD,
    GRANULARITY_FLAT,
    GRANULARITY_PARENT,
    chunk_key,
    detect_granularity,
    load_topic_chunks,
    norm,
)


class _FakeStore:
    """只实现 get() 的最小假库，load_topic_chunks 只用这一个方法。"""

    def __init__(self, docs, metas):
        self._docs = docs
        self._metas = metas

    def get(self, include=None):
        return {"documents": self._docs, "metadatas": self._metas}


class TestNorm(unittest.TestCase):
    def test_matches_smoke_rag_norm_semantics(self):
        """历史失配案例：评测集小写带空格 vs 库内标题大小写。"""
        self.assertEqual(norm("tcp 与 udp"), norm("TCP 与 UDP"))
        self.assertEqual(norm("死锁"), "死锁")

    def test_spaces_removed(self):
        self.assertEqual(norm("进程 和 线程"), norm("进程和线程"))


class TestDetectGranularity(unittest.TestCase):
    def test_parent_when_any_parent_id(self):
        # 只要有一片带 parent_id 就认定是父子库（混库里扁平片也不会把它判回 flat）
        metas = [{"source": "a.md", "start_index": 0}, {"source": "a.md", "parent_id": "a.md#0"}]
        self.assertEqual(detect_granularity(metas), GRANULARITY_PARENT)

    def test_flat_when_no_parent_id(self):
        self.assertEqual(detect_granularity([{"source": "a.md", "start_index": 0}]), GRANULARITY_FLAT)

    def test_empty_is_flat(self):
        # 空库不该被误判成父子库：那会让 load_topic_chunks 跳过全部切片，
        # 派生出一份全空的金标准，而 build_gold 会以"标注 0 条"正常退出
        self.assertEqual(detect_granularity([]), GRANULARITY_FLAT)

    def test_none_metas_tolerated(self):
        self.assertEqual(detect_granularity([None, {}]), GRANULARITY_FLAT)


class TestChunkKey(unittest.TestCase):
    def test_parent_uses_parent_start(self):
        # 关键：父子库检索返回的 parent 把 start_index 换成 parent_start，
        # 金标准用 child 的 start_index 就永远匹配不上
        meta = {"source": "a.md", "start_index": 300, "parent_start": 102}
        self.assertEqual(chunk_key(meta, GRANULARITY_PARENT), ("a.md", 102))

    def test_flat_uses_start_index(self):
        meta = {"source": "a.md", "start_index": 300, "parent_start": 102}
        self.assertEqual(chunk_key(meta, GRANULARITY_FLAT), ("a.md", 300))

    def test_missing_fields_default_zero(self):
        self.assertEqual(chunk_key({}, GRANULARITY_PARENT), ("", 0))
        self.assertEqual(chunk_key({}, GRANULARITY_FLAT), ("", 0))


class TestLoadTopicChunksParent(unittest.TestCase):
    """parent 粒度：按 parent_id 去重 + 用 parent_start 当坐标。"""

    def setUp(self):
        base = {"source": "cs/os/deadlock.md", "topic": "死锁"}
        self.store = _FakeStore(
            docs=["c1", "c2", "c3", "c4"],
            metas=[
                # 同一 parent 的两个 child：必须收敛成一片
                {**base, "parent_id": "p0", "parent_start": 6, "start_index": 6, "parent_text": "## 结论"},
                {**base, "parent_id": "p0", "parent_start": 6, "start_index": 102, "parent_text": "## 结论"},
                {**base, "parent_id": "p1", "parent_start": 922, "start_index": 922, "parent_text": "## 原理"},
                # 混合库里的扁平片：没有 parent 坐标，不能出现在 parent 金标准里
                {**base, "start_index": 1525},
            ],
        )

    def test_dedupes_by_parent_id(self):
        groups = load_topic_chunks(self.store, GRANULARITY_PARENT)
        self.assertEqual(groups[norm("死锁")], [("cs/os/deadlock.md", 6), ("cs/os/deadlock.md", 922)])

    def test_flat_piece_skipped(self):
        # 扁平片的 start_index=1525 不是任何 parent_start，混进来会造出查不到正文的金标准条目
        groups = load_topic_chunks(self.store, GRANULARITY_PARENT)
        self.assertNotIn(("cs/os/deadlock.md", 1525), groups[norm("死锁")])

    def test_three_children_collapse_to_two(self):
        # 不去重的话金标准会有 3 条（分母灌水 50%），Recall 被系统性压低
        groups = load_topic_chunks(self.store, GRANULARITY_PARENT)
        self.assertEqual(len(groups[norm("死锁")]), 2)


class TestLoadTopicChunksFlat(unittest.TestCase):
    """flat 粒度：不去重、用 start_index——改造前行为必须逐字保持不变。"""

    def setUp(self):
        base = {"source": "a.md", "topic": "T", "parent_id": "p0", "parent_start": 6}
        self.store = _FakeStore(
            docs=["c1", "c2", "c3"],
            metas=[
                {**base, "start_index": 6},
                {**base, "start_index": 102},
                {**base, "start_index": 292},
            ],
        )

    def test_keeps_every_chunk(self):
        groups = load_topic_chunks(self.store, GRANULARITY_FLAT)
        self.assertEqual(
            groups[norm("T")],
            [("a.md", 6), ("a.md", 102), ("a.md", 292)],
        )

    def test_ignores_parent_start(self):
        # flat 口径只看 start_index；若误用 parent_start，3 片会塌成 1 片
        groups = load_topic_chunks(self.store, GRANULARITY_FLAT)
        self.assertNotEqual(groups[norm("T")], [("a.md", 6)])


class TestTopicKeying(unittest.TestCase):
    def test_untopiced_chunk_skipped(self):
        store = _FakeStore(["x"], [{"source": "a.md", "parent_id": "p0", "parent_start": 0}])
        self.assertEqual(load_topic_chunks(store, GRANULARITY_PARENT), {})

    def test_case_and_space_insensitive_grouping(self):
        store = _FakeStore(
            ["x"],
            [{"source": "a.md", "topic": "TCP 与 UDP", "parent_id": "p0", "parent_start": 0}],
        )
        groups = load_topic_chunks(store, GRANULARITY_PARENT)
        # 评测集写的是 "tcp 与 udp"，必须能查到同一组
        self.assertIn(norm("tcp 与 udp"), groups)


class TestGranularityConstants(unittest.TestCase):
    def test_child_is_flat_alias_for_load(self):
        # load_topic_chunks 只认 parent，其余一律按 flat 处理（含 "child" 别名）
        store = _FakeStore(["x"], [{"source": "a.md", "topic": "T", "start_index": 7, "parent_id": "p0", "parent_start": 0}])
        self.assertEqual(load_topic_chunks(store, GRANULARITY_CHILD), {norm("T"): [("a.md", 7)]})


if __name__ == "__main__":
    unittest.main()
