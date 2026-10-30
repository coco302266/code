"""M4-C 留痕自检测试：**「/evaluation/* 全空」必须能自己说出原因**。

这些用例守的是一条很容易被改坏的约定：

- 留痕为空有四种成因（没配库 / 连不上 / 表缺失 / 被过滤掉），它们的表现
  完全一样（200 + turns=0）。任何一条把「查不出来」退化成「0」的改动，
  都会让使用者把「数据库挂了」误判成「还没跑对话」——所以 `count_runs`
  返回 None 而非 0 是被测试钉住的（test_count_runs_returns_none_not_zero）。
- 自检接口不能自己 503。`diagnose` 在任何输入下都不许抛。
- 无课堂的 /chat 必须留下痕。这是「回答正常、/evaluation 全空」最隐蔽的
  一条成因，`save_agent_run` 对 classroom_id=None 早退是被测试钉住的。
"""

from __future__ import annotations

import unittest
from contextlib import contextmanager

from db.diagnostics import (
    DB_DISABLED,
    DB_DOWN,
    DB_UP,
    build_hint,
    count_runs,
    diagnose,
    empty_result_hint,
    inspect_database,
    inspect_runs_table,
)


class InspectDatabaseTests(unittest.TestCase):
    def test_disabled_when_engine_missing(self):
        """没配 MYSQL_* 时必须报 disabled，而不是 down。

        「没配」和「配了连不上」修法完全不同：前者去填 .env，后者去看 MySQL。
        """
        import db.session as session

        saved_engine, saved_factory = session.engine, session.SessionLocal
        try:
            session.engine, session.SessionLocal = None, None
            result = inspect_database()
        finally:
            session.engine, session.SessionLocal = saved_engine, saved_factory
        self.assertEqual(result["state"], DB_DISABLED)
        self.assertIn("MYSQL_", result["detail"])

    def test_returns_up_or_down_never_raises(self):
        """连通性检查不许抛——它是给「出问题的时候」用的。"""
        state = inspect_database()["state"]
        self.assertIn(state, {DB_UP, DB_DOWN, DB_DISABLED})


class _FakeInspector:
    """替身：只实现 `inspect()` 返回对象上被用到的两个方法。

    不去伪造一个真的 Session——sqlalchemy.inspect() 要求完整的 ORM 协议，
    伪造它的成本远高于替换这一个函数。
    """

    def __init__(self, present: set[str], exists: bool = True):
        self._present = present
        self._exists = exists

    def has_table(self, _name):
        return self._exists

    def get_columns(self, _name):
        return [{"name": name} for name in sorted(self._present)]


@contextmanager
def _patch_inspect(inspector):
    """临时替换 db.diagnostics.inspect，让列比对逻辑可以脱离数据库测试。"""
    import db.diagnostics as diag

    saved = diag.inspect
    diag.inspect = lambda _db: inspector
    try:
        yield
    finally:
        diag.inspect = saved


class InspectTableTests(unittest.TestCase):
    def test_no_session_reports_missing(self):
        result = inspect_runs_table(None)
        self.assertFalse(result["exists"])
        self.assertEqual(result["missing_columns"], [])

    def test_unknown_columns_are_reported(self):
        """缺列要说清缺哪几列。

        rag_hits 就是这样补进来的：模型加了列而线上表没补，写入直接
        Unknown column，症状同样是「留痕全丢」。
        """
        with _patch_inspect(_FakeInspector(present={"id", "scenario"})):
            result = inspect_runs_table(object())
        self.assertTrue(result["exists"])
        self.assertIn("rag_hits", result["missing_columns"])
        self.assertIn("rag_hits", result["detail"])

    def test_inspection_failure_is_not_fatal(self):
        """读表结构失败不能让自检崩掉——它得把失败本身报出来。"""

        class BoomInspector:
            def has_table(self, _name):
                raise RuntimeError("access denied")

        with _patch_inspect(BoomInspector()):
            result = inspect_runs_table(object())
        self.assertFalse(result["exists"])
        self.assertIn("读取表结构失败", result["detail"])


class CountRunsTests(unittest.TestCase):
    def test_count_runs_returns_none_not_zero(self):
        """查不出来必须返回 None。

        返回 0 会让「表不存在」和「确实没数据」混为一谈，使用者会以为只是
        还没跑对话，而实际上数据库根本没接上。这个区分是本模块存在的理由。
        """
        self.assertIsNone(count_runs(None))

    def test_query_failure_returns_none(self):
        class Boom:
            def execute(self, _query):
                raise RuntimeError("no such table")

        self.assertIsNone(count_runs(Boom()))


class BuildHintTests(unittest.TestCase):
    def test_disabled_points_to_env(self):
        hint = build_hint(DB_DISABLED, {"exists": True}, None, None)
        self.assertIn("MYSQL_", hint)
        self.assertIn("不落库", hint)

    def test_down_points_to_mysql(self):
        hint = build_hint(DB_DOWN, {"exists": True}, None, None)
        self.assertIn("MySQL", hint)

    def test_missing_table_points_to_init_db(self):
        hint = build_hint(DB_UP, {"exists": False}, None, None)
        self.assertIn("init_db.py", hint)

    def test_missing_column_points_to_init_db(self):
        hint = build_hint(DB_UP, {"exists": True, "missing_columns": ["rag_hits"]}, 5, 0)
        self.assertIn("rag_hits", hint)
        self.assertIn("init_db.py", hint)

    def test_zero_rows_says_run_some_chats(self):
        hint = build_hint(DB_UP, {"exists": True, "missing_columns": []}, 0, 0)
        self.assertIn("/chat", hint)

    def test_all_rows_without_classroom_is_called_out(self):
        """全部留痕都没课堂 → 明确说出来。

        这类轮次能进客观统计但评不了分（无正文），不提示的话评分人会对着
        一片空白的摘录列发懵。
        """
        hint = build_hint(DB_UP, {"exists": True, "missing_columns": []}, 7, 7)
        self.assertIn("classroom_id", hint)
        self.assertIn("7", hint)

    def test_healthy_state_has_no_hint(self):
        self.assertEqual(
            build_hint(DB_UP, {"exists": True, "missing_columns": []}, 12, 0), ""
        )


class EmptyResultHintTests(unittest.TestCase):
    def test_empty_db_has_no_hint(self):
        """库里本来就 0 条时不能甩锅给过滤条件。"""
        self.assertEqual(empty_result_hint(0), "")
        self.assertEqual(empty_result_hint(None), "")

    def test_filtered_out_points_to_filter(self):
        hint = empty_result_hint(30)
        self.assertIn("30", hint)
        self.assertIn("过滤条件", hint)
        # 无课堂留痕的 classroom_id 是 NULL，按课堂查必然漏掉——这是最常见的
        # 「明明跑过对话却查不到」的原因，提示里必须写到。
        self.assertIn("NULL", hint)


class DiagnoseTests(unittest.TestCase):
    def test_never_raises_and_has_all_keys(self):
        """自检本身崩掉是最糟的情况：唯一能解释问题的工具失效了。"""
        for db in (None, _BoomDb(), object()):
            result = diagnose(db)
            for key in (
                "ready",
                "database",
                "database_detail",
                "table",
                "total_runs",
                "runs_without_classroom",
                "hint",
            ):
                self.assertIn(key, result)

    def test_ready_false_when_no_session(self):
        self.assertFalse(diagnose(None)["ready"])


class NoClassroomRunTests(unittest.TestCase):
    def test_save_agent_run_accepts_missing_classroom(self):
        """无课堂的 /chat 必须落痕。

        早先 save_agent_run 对 classroom_id=None 直接 return，后果是这类请求
        回答完全正常、却一行留痕都没有——/evaluation/* 全空且零报错。
        """
        from db.repository import save_agent_run

        captured = {}

        class FakeDb:
            def add(self, obj):
                captured["obj"] = obj

            def flush(self):
                captured["flushed"] = True

        run = save_agent_run(
            FakeDb(),
            None,
            scenario="group",
            roles=["tutor", "peer"],
            duration_ms=1234,
        )
        self.assertIsNotNone(run, "classroom_id 为 None 也必须写入，不能静默丢弃")
        self.assertIsNone(captured["obj"].classroom_id)


class WriteAgentRunTests(unittest.TestCase):
    def test_failure_is_reported_not_swallowed(self):
        """写库失败必须把原因交出去，不能只 print 一句。

        print 在 uvicorn 下会被访问日志冲掉，而「回答正常、/evaluation 全空、
        日志里什么都没有」正是这次要根治的症状。
        """
        import app

        @contextmanager
        def fake_get_db():
            yield _BoomDb()

        saved = app.get_db
        try:
            app.get_db = fake_get_db
            ok, why = app._write_agent_run(
                {"classroom_id": 1, "scenario": "v1", "roles": ["tutor"]}
            )
        finally:
            app.get_db = saved

        self.assertFalse(ok)
        self.assertIn("RuntimeError", why)

    def test_db_unavailable_reports_actionable_reason(self):
        import app

        @contextmanager
        def fake_get_db():
            yield None

        saved = app.get_db
        try:
            app.get_db = fake_get_db
            ok, why = app._write_agent_run({"classroom_id": 1, "roles": []})
        finally:
            app.get_db = saved

        self.assertFalse(ok)
        self.assertIn("MYSQL_", why)


class _BoomDb:
    def __init__(self):
        self._fail = True

    def add(self, _obj):
        raise RuntimeError("Table 'ai_classroom.agent_runs' doesn't exist")


if __name__ == "__main__":
    unittest.main()
