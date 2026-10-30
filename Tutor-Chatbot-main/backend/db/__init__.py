"""数据层入口。

T3 阶段只负责四张核心表：User / Classroom / ClassroomMember / Message。
StudentProfile 与 LearningRecord 属于 T5/T6，届时再追加。
"""

from db.models import (
    Base,
    Classroom,
    ClassroomMember,
    Message,
    User,
)
from db.session import (
    DATABASE_URL,
    SessionLocal,
    db_available,
    engine,
    get_db,
)

__all__ = [
    "Base",
    "Classroom",
    "ClassroomMember",
    "Message",
    "User",
    "DATABASE_URL",
    "SessionLocal",
    "db_available",
    "engine",
    "get_db",
]
