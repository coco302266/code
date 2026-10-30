"""多学生课堂的状态隔离（T6 回归）。

用内存 SQLite 跑，不依赖 MySQL——这几条规则是 SQL 语义层面的，
换方言不影响结论，而依赖真库的测试在别人机器上跑不起来。

背景：T4 的状态恢复只按课堂取最后一条消息。单人课堂看不出问题，
一旦同一个课堂里有两个学生，A 的 topic / hint_level 就会被 B 继承，
表现为「新学生第一次提问就拿到第 3 层讲解」。

两个函数的 user_id 参数语义不同，别搞混：

- `load_last_state(user_id=...)`：**永远按学生隔离**。讲解深度是 per-student
  的，共享就等于废掉个性化。
- `load_history(user_id=...)`：这是「群聊 / 单独聊」开关。不传 = 群聊
  （当前产品形态，同学之间能接力追问）；传了 = 只看这个人的对话线
  （未来做单独聊时用）。
"""

import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models import Classroom, Message, User
from db.repository import (
    add_member,
    create_classroom,
    create_user,
    is_member,
    list_messages,
    load_history,
    load_last_state,
    save_message,
)
from db.session import Base


class IsolationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, expire_on_commit=False)()

        self.room = create_classroom(self.db, "隔离测试班")
        self.a = create_user(self.db, "stu_a")
        self.b = create_user(self.db, "stu_b")
        for u in (self.a, self.b):
            add_member(self.db, self.room.id, u.id, "student")

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(self.engine)

    def _turn(self, user_id, topic, hint_level, question):
        """模拟一轮：学生提问（不带状态）+ AI 回复（带状态、记归属）。"""
        save_message(self.db, self.room.id, user_id, "student", question)
        save_message(
            self.db, self.room.id, user_id, "assistant", f"{topic} 的讲解",
            topic, hint_level,
        )

    def test_state_is_per_student(self):
        """A 在学死锁讲到第 3 层，B 刚开口，不该继承 A 的状态。"""
        self._turn(self.a.id, "死锁", 3, "死锁怎么预防")
        topic, level = load_last_state(self.db, self.room.id, self.b.id)
        self.assertEqual((topic, level), ("", 0))

    def test_state_survives_for_the_same_student(self):
        self._turn(self.a.id, "死锁", 2, "还是不懂")
        topic, level = load_last_state(self.db, self.room.id, self.a.id)
        self.assertEqual((topic, level), ("死锁", 2))

    def test_new_student_starts_from_zero(self):
        """回归：新学生第一次提问必须是第 0 层，不能继承别人的深度。"""
        self._turn(self.a.id, "死锁", 3, "死锁怎么预防")
        self._turn(self.b.id, "tcp三次握手", 2, "握手为什么要三次")
        level = load_last_state(self.db, self.room.id, self.b.id)[1]
        self.assertEqual(level, 2)  # B 自己的
        fresh = create_user(self.db, "stu_fresh")
        add_member(self.db, self.room.id, fresh.id, "student")
        self.assertEqual(load_last_state(self.db, self.room.id, fresh.id), ("", 0))

    def test_history_is_per_student(self):
        """传 user_id 时是「单独聊」：只看这个人的对话线。"""
        self._turn(self.a.id, "死锁", 1, "死锁是什么")
        self._turn(self.b.id, "tcp三次握手", 1, "握手是什么")
        history = load_history(self.db, self.room.id, self.a.id)
        contents = [h.content for h in history]
        self.assertIn("死锁是什么", contents)
        self.assertNotIn("握手是什么", contents)

    def test_history_without_user_id_is_group_chat(self):
        """不传 user_id 时是「群聊」：课堂里所有人的问答都要看得见。

        当前产品形态是共用聊天室，同学要能看到彼此的提问，
        B 才能接在 A 的问答后面追问「那怎么预防」。
        """
        self._turn(self.a.id, "死锁", 1, "死锁是什么")
        self._turn(self.b.id, "tcp三次握手", 1, "握手是什么")
        contents = [h.content for h in load_history(self.db, self.room.id)]
        self.assertIn("死锁是什么", contents)
        self.assertIn("握手是什么", contents)

    def test_legacy_null_messages_stay_visible(self):
        """老数据里 assistant 回复不记归属，升级后不能凭空断掉上下文。"""
        save_message(self.db, self.room.id, None, "assistant", "旧回复", "死锁", 1)
        history = load_history(self.db, self.room.id, self.a.id)
        self.assertIn("旧回复", [h.content for h in history])

    def test_without_user_id_keeps_classroom_scope(self):
        """不传 user_id 时保持 T4 的课堂粒度（旧前端路径）。"""
        self._turn(self.b.id, "tcp三次握手", 2, "握手是什么")
        topic, level = load_last_state(self.db, self.room.id)
        self.assertEqual((topic, level), ("tcp三次握手", 2))

    def test_membership_still_checked(self):
        outsider = create_user(self.db, "outsider")
        self.assertTrue(is_member(self.db, self.room.id, self.a.id))
        self.assertFalse(is_member(self.db, self.room.id, outsider.id))
        self.assertFalse(is_member(self.db, self.room.id, None))


class DetachedSessionTests(unittest.TestCase):
    """`GET /classrooms/{id}/messages` 的 500 回归。

    背景：这个端点要在每条消息上读 `m.user.username` 给前端标发言人，
    而序列化原先写在 `with get_db()` 块外面——session 已关，访问
    relationship 触发延迟加载，直接 DetachedInstanceError → HTTP 500。

    单元测试测不到路由注册，也测不到「session 生命周期」，所以这里
    显式制造那个场景：先取消息，再关 session，最后才访问 username。
    """

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, expire_on_commit=False)()

        self.room = create_classroom(self.db, "detach 班")
        self.stu = create_user(self.db, "stu_a")

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(self.engine)

    def test_username_readable_after_session_closed(self):
        """session 关闭后仍能安全读出 username——joinedload 必须生效。

        这里 dict结尾的两个角色要覆盖到：学生消息要拿到名字，
        assistant 消息（T6 起也记 user_id）由调用层决定怎么显示，
        但至少要能读、不能抛异常。
        """
        save_message(self.db, self.room.id, self.stu.id, "student", "死锁是什么")
        save_message(
            self.db, self.room.id, self.stu.id, "assistant", "互斥…", "死锁", 0
        )

        rows = list_messages(self.db, self.room.id)
        self.db.close()  # 模拟 `with get_db()` 退出

        rendered = [(m.role, m.user.username if m.user else None) for m in rows]
        self.assertEqual(
            rendered,
            [("student", "stu_a"), ("assistant", "stu_a")],
        )

    def test_assistant_with_null_user_is_safe(self):
        """T6 之前的老 assistant 消息 user_id 是 NULL，不能炸。"""
        save_message(self.db, self.room.id, None, "assistant", "旧回复", "死锁", 1)

        rows = list_messages(self.db, self.room.id)
        self.db.close()

        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0].user)


if __name__ == "__main__":
    unittest.main()
