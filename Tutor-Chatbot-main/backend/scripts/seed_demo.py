"""T3.6 验收脚本：创建用户 → 创建课堂 → 加入课堂 → 发送消息 → 查询聊天记录。

用法：
    python seed_demo.py

幂等：重复运行不会重复建用户/课堂（按 username / name 查找复用），
但会追加一条新消息，方便观察记录累积。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # backend/

from sqlalchemy import select  # noqa: E402

from db.models import Classroom, ClassroomMember, Message, User  # noqa: E402
from db.session import SessionLocal, engine  # noqa: E402

DEMO_TEACHER = "demo_teacher"
DEMO_STUDENT = "demo_student"
DEMO_CLASSROOM = "计算机网络八股班"


def get_or_create_user(db, username: str, role: str) -> User:
    user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
    if user is None:
        user = User(username=username, role=role)
        db.add(user)
        db.flush()
        print(f"  创建用户 #{user.id} {username} ({role})")
    else:
        print(f"  复用用户 #{user.id} {username} ({user.role})")
    return user


def get_or_create_classroom(db, name: str) -> Classroom:
    room = db.execute(
        select(Classroom).where(Classroom.name == name)
    ).scalar_one_or_none()
    if room is None:
        room = Classroom(
            name=name, subject="cs", description="操作系统 + 计算机网络八股学习"
        )
        db.add(room)
        db.flush()
        print(f"  创建课堂 #{room.id} {name}")
    else:
        print(f"  复用课堂 #{room.id} {name}")
    return room


def join_classroom(db, room: Classroom, user: User, role: str) -> None:
    exists = db.execute(
        select(ClassroomMember).where(
            ClassroomMember.classroom_id == room.id,
            ClassroomMember.user_id == user.id,
        )
    ).scalar_one_or_none()
    if exists is None:
        db.add(
            ClassroomMember(classroom_id=room.id, user_id=user.id, role=role)
        )
        db.flush()
        print(f"  {user.username} 以 {role} 身份加入课堂 #{room.id}")
    else:
        print(f"  {user.username} 已在课堂 #{room.id} 中（{exists.role}）")


def main() -> None:
    if engine is None:
        raise SystemExit("数据库未配置，请先填 backend/.env 的 MYSQL_* 再运行")

    with SessionLocal() as db:
        print("1. 创建用户")
        teacher = get_or_create_user(db, DEMO_TEACHER, "teacher")
        student = get_or_create_user(db, DEMO_STUDENT, "student")

        print("2. 创建课堂")
        room = get_or_create_classroom(db, DEMO_CLASSROOM)

        print("3. 加入课堂")
        join_classroom(db, room, teacher, "teacher")
        join_classroom(db, room, student, "student")

        print("4. 发送消息")
        pairs = [
            (student, "student", "TCP 为什么是三次握手，而不是两次？"),
            (None, "assistant", "两次握手无法阻止历史重复连接初始化，会浪费服务端资源。"),
            (student, "student", "那四次挥手为什么要等 2MSL？"),
        ]
        for user, role, content in pairs:
            db.add(
                Message(
                    classroom_id=room.id,
                    user_id=user.id if user else None,
                    role=role,
                    content=content,
                )
            )
        db.flush()
        print(f"  写入 {len(pairs)} 条消息")

        print("5. 查询聊天记录")
        rows = db.execute(
            select(Message)
            .where(Message.classroom_id == room.id)
            .order_by(Message.id)
        ).scalars().all()
        print(f"  课堂 #{room.id} 共 {len(rows)} 条记录：")
        for m in rows:
            who = m.user.username if m.user else "assistant"
            ts = m.created_at.strftime("%H:%M:%S") if m.created_at else "--:--:--"
            print(f"    [{ts}] {m.role:9s} {who:14s} {m.content[:34]}")

        members = db.execute(
            select(ClassroomMember).where(ClassroomMember.classroom_id == room.id)
        ).scalars().all()
        print(f"\n课堂成员 {len(members)} 人：")
        for mem in members:
            print(f"    {mem.user.username} ({mem.role})")

        db.commit()

    print("\nT3.6 五项验收全部通过 ✓")
    print(f"可在 Navicat 打开 `{os.getenv('MYSQL_DATABASE', 'ai_classroom')}` 查看")


if __name__ == "__main__":
    main()
