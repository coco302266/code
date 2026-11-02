"""注册登录 + 1v1 指定学生 + 助教开关（2026-11-02）。

三条新规则，每条都要有「反向也成立」的用例：

1. 注册必须带密码，且库里只存哈希（不能出现明文）；重名直接 409，
   不再「同名复用」——否则谁输对昵称就能接管别人的课堂。
2. 1v1 教室指定了学生后，只有那位学生能进；别人拿到的 409 正文必须
   说清「这是指定学生的[1v1教室]」。老师（teacher 角色）进门不受影响，
   否则老师自己都进不去自己的教室。
3. 助教开关只有本课堂教师能拨；学生拨要 403，不能靠前端藏按钮。

用内存 SQLite 跑（与 test_repository 一致）：这些是应用层判定，
不依赖 MySQL 方言，换库不影响结论，也不要求跑测试的人装数据库。
"""

from contextlib import contextmanager
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app as app_module
from app import (
    AddMemberRequest,
    CreateClassroomRequest,
    CreateUserRequest,
    LoginRequest,
    UpdateClassroomRequest,
    api_add_member,
    api_create_classroom,
    api_create_user,
    api_login,
    api_update_classroom,
)
from auth import hash_password, password_problem, verify_password
from db.models import Base, ClassroomMember
from db.repository import create_user


class PasswordHashingTests(unittest.TestCase):
    def test_hash_round_trip(self):
        stored = hash_password("cs-2026")
        self.assertNotIn("cs-2026", stored)  # 绝不能明文落库
        self.assertTrue(verify_password("cs-2026", stored))
        self.assertFalse(verify_password("cs-2027", stored))

    def test_same_password_gets_different_hash(self):
        # 盐随机：两个人设同一个密码，库里的值也必须不同
        self.assertNotEqual(hash_password("123456"), hash_password("123456"))

    def test_legacy_account_without_password_never_verifies(self):
        self.assertFalse(verify_password("anything", None))
        self.assertFalse(verify_password("anything", ""))
        self.assertFalse(verify_password("anything", "broken-format"))

    def test_password_rules(self):
        self.assertIsNotNone(password_problem("123"))       # 太短
        self.assertIsNotNone(password_problem(""))           # 空
        self.assertIsNotNone(password_problem(" 123456 "))   # 首尾空格
        self.assertIsNone(password_problem("123456"))


class _DbCase(unittest.TestCase):
    """把 app 的 get_db 换成内存库，直接调接口函数。"""

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, expire_on_commit=False)()

        @contextmanager
        def fake_get_db():
            yield self.db

        self._orig_get_db = app_module.get_db
        self._orig_session_local = app_module.SessionLocal
        app_module.get_db = fake_get_db
        app_module.SessionLocal = object()  # 让 _require_db() 放行

    def tearDown(self):
        app_module.get_db = self._orig_get_db
        app_module.SessionLocal = self._orig_session_local
        self.db.close()
        Base.metadata.drop_all(self.engine)

    def _register(self, username, password="123456", role="student"):
        return api_create_user(
            CreateUserRequest(username=username, password=password, role=role)
        )


class RegisterLoginTests(_DbCase):
    def test_register_assigns_and_returns_id(self):
        created = self._register("张三")
        self.assertIsInstance(created["id"], int)
        self.assertGreater(created["id"], 0)
        self.assertEqual(created["username"], "张三")
        # 接口不能把口令哈希吐出来
        self.assertNotIn("password_hash", created)
        self.assertNotIn("password", created)

    def test_register_duplicate_username_is_409(self):
        self._register("张三")
        with self.assertRaises(HTTPException) as ctx:
            self._register("张三", password="other123")
        self.assertEqual(ctx.exception.status_code, 409)

    def test_register_rejects_short_password(self):
        with self.assertRaises(HTTPException) as ctx:
            self._register("李四", password="123")
        self.assertEqual(ctx.exception.status_code, 422)

    def test_login_ok_and_wrong_password(self):
        created = self._register("王五", password="abc123")
        ok = api_login(LoginRequest(username="王五", password="abc123"))
        self.assertEqual(ok["id"], created["id"])
        self.assertFalse(ok["password_bound"])

        with self.assertRaises(HTTPException) as ctx:
            api_login(LoginRequest(username="王五", password="abc124"))
        self.assertEqual(ctx.exception.status_code, 401)

    def test_login_unknown_user_same_message_as_wrong_password(self):
        """用户不存在与密码错误必须同一句，否则等于用户名枚举接口。"""
        self._register("王五", password="abc123")
        wrong_pw = None
        unknown = None
        try:
            api_login(LoginRequest(username="王五", password="nope123"))
        except HTTPException as exc:
            wrong_pw = exc
        try:
            api_login(LoginRequest(username="查无此人", password="nope123"))
        except HTTPException as exc:
            unknown = exc
        self.assertEqual(wrong_pw.status_code, unknown.status_code)
        self.assertEqual(wrong_pw.detail, unknown.detail)

    def test_legacy_account_binds_password_on_first_login(self):
        legacy = create_user(self.db, "老账号", "student")  # 无 password_hash
        res = api_login(LoginRequest(username="老账号", password="first123"))
        self.assertTrue(res["password_bound"])
        self.assertEqual(res["id"], legacy.id)
        # 绑定之后必须按新密码校验
        with self.assertRaises(HTTPException):
            api_login(LoginRequest(username="老账号", password="other123"))
        self.assertEqual(
            api_login(LoginRequest(username="老账号", password="first123"))["id"],
            legacy.id,
        )


class OneOnOneTargetTests(_DbCase):
    def setUp(self):
        super().setUp()
        self.teacher = self._register("李老师", role="teacher")
        self.target = self._register("花生")
        self.other = self._register("路人")
        self.room = api_create_classroom(
            CreateClassroomRequest(
                name="1v1 辅导",
                scenario="v1",
                target_student_id=self.target["id"],
            )
        )

    def _join(self, user_id, role="student"):
        return api_add_member(
            self.room["id"], AddMemberRequest(user_id=user_id, role=role)
        )

    def test_room_echoes_target_student(self):
        self.assertEqual(self.room["target_student_id"], self.target["id"])
        self.assertEqual(self.room["target_student_username"], "花生")

    def test_target_student_auto_joins_on_create(self):
        rows = (
            self.db.query(ClassroomMember)
            .filter(ClassroomMember.classroom_id == self.room["id"])
            .all()
        )
        self.assertEqual([r.user_id for r in rows], [self.target["id"]])

    def test_designated_student_can_enter(self):
        res = self._join(self.target["id"])
        self.assertEqual(res["user_id"], self.target["id"])

    def test_other_student_gets_409_with_required_wording(self):
        with self.assertRaises(HTTPException) as ctx:
            self._join(self.other["id"])
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("这是指定学生的[1v1教室]，你不能进入", ctx.exception.detail)
        self.assertIn("花生", ctx.exception.detail)

    def test_teacher_can_still_enter(self):
        res = self._join(self.teacher["id"], role="teacher")
        self.assertEqual(res["role"], "teacher")

    def test_target_must_be_a_student(self):
        with self.assertRaises(HTTPException) as ctx:
            api_create_classroom(
                CreateClassroomRequest(
                    name="错误指定",
                    scenario="v1",
                    target_student_id=self.teacher["id"],
                )
            )
        self.assertEqual(ctx.exception.status_code, 422)

    def test_group_room_ignores_target_rule(self):
        """小组课堂没有指定学生时，谁都能进（不能把 1v1 规则外溢）。"""
        group = api_create_classroom(
            CreateClassroomRequest(name="小组讨论", scenario="group")
        )
        api_add_member(
            group["id"], AddMemberRequest(user_id=self.teacher["id"], role="teacher")
        )
        res = api_add_member(
            group["id"], AddMemberRequest(user_id=self.other["id"], role="student")
        )
        self.assertEqual(res["user_id"], self.other["id"])


class TutorToggleTests(_DbCase):
    def setUp(self):
        super().setUp()
        self.teacher = self._register("李老师", role="teacher")
        self.student = self._register("花生")
        self.room = api_create_classroom(
            CreateClassroomRequest(
                name="1v1 辅导",
                scenario="v1",
                target_student_id=self.student["id"],
            )
        )
        api_add_member(
            self.room["id"],
            AddMemberRequest(user_id=self.teacher["id"], role="teacher"),
        )

    def test_teacher_can_toggle_off_and_on(self):
        off = api_update_classroom(
            self.room["id"],
            UpdateClassroomRequest(use_tutor=False),
            teacher_id=self.teacher["id"],
        )
        self.assertFalse(off["use_tutor"])
        on = api_update_classroom(
            self.room["id"],
            UpdateClassroomRequest(use_tutor=True),
            teacher_id=self.teacher["id"],
        )
        self.assertTrue(on["use_tutor"])

    def test_student_cannot_toggle(self):
        with self.assertRaises(HTTPException) as ctx:
            api_update_classroom(
                self.room["id"],
                UpdateClassroomRequest(use_tutor=False),
                teacher_id=self.student["id"],
            )
        self.assertEqual(ctx.exception.status_code, 403)

    def test_outsider_cannot_toggle(self):
        outsider = self._register("路人")
        with self.assertRaises(HTTPException) as ctx:
            api_update_classroom(
                self.room["id"],
                UpdateClassroomRequest(use_tutor=False),
                teacher_id=outsider["id"],
            )
        self.assertEqual(ctx.exception.status_code, 403)

    def test_create_defaults_to_tutor_on(self):
        """建房界面不再问助教，后端默认必须是开——否则老师进来面对空课堂。"""
        self.assertTrue(self.room["use_tutor"])


if __name__ == "__main__":
    unittest.main()
