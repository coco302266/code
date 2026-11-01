"""初始化数据库：建库 + 建表。

用法：
    python init_db.py

行为：
- 数据库不存在时自动创建（utf8mb4 / utf8mb4_unicode_ci）
- 已存在的表不会重复创建（create_all 是幂等的）
- 会打印每张表的结果，方便在 Navicat 里对照
"""

from __future__ import annotations

import os
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError, ProgrammingError

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db.models import (  # noqa: E402
    AgentRun,
    Classroom,
    ClassroomDocument,
    ClassroomMember,
    LearningBehavior,
    Message,
    StudentProfile,
    User,
)
from db.session import Base, DATABASE_URL  # noqa: E402


def _server_url() -> tuple[str, str]:
    """拆出「不带库名的连接串」和「库名」，用于先建库再建表。"""
    db_name = os.getenv("MYSQL_DATABASE", "ai_classroom")
    url = DATABASE_URL
    # mysql+pymysql://user:pwd@host:port/ai_classroom?charset=utf8mb4
    #                                  ^ 到这里截断
    try:
        head, tail = url.split("?", 1)
    except ValueError:
        head, tail = url, ""
    slash = head.rfind("/")
    if slash == -1:
        raise SystemExit(f"DATABASE_URL 缺少数据库名：{url}")
    root_url = head[:slash] + "/"
    if tail:
        root_url += "?" + tail
    return root_url, db_name


def create_database_if_missing() -> None:
    root_url, db_name = _server_url()
    engine = create_engine(root_url, future=True)
    try:
        with engine.connect() as conn:
            conn.execute(
                text(
                    f"CREATE DATABASE IF NOT EXISTS `{db_name}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
            )
            conn.commit()
        print(f"数据库 `{db_name}` 已就绪（utf8mb4）")
    except (OperationalError, ProgrammingError) as exc:
        raise SystemExit(
            f"无法创建/连接数据库 `{db_name}`：{exc}\n"
            "请检查 backend/.env 里的 MYSQL_* 配置，并确认 MySQL 服务已启动。"
        ) from exc
    finally:
        engine.dispose()


def ensure_new_columns() -> None:
    """给已存在的表补列。

    `create_all()` 只对「不存在的表」生效，表在而列缺失它不管。
    后期每次给模型加字段，都要在这里同步登记一条 ALTER，否则线上
    表结构会和代码里的模型悄悄偏离（读出来是 None，写入直接报错）。
    """
    from sqlalchemy import inspect

    from db.session import engine

    insp = inspect(engine)
    existing = {c["name"] for c in insp.get_columns("messages")}

    additions = []
    if "topic" not in existing:
        additions.append("ADD COLUMN topic VARCHAR(64) NULL")
    if "hint_level" not in existing:
        additions.append("ADD COLUMN hint_level INT NOT NULL DEFAULT 0")
    if "sources" not in existing:
        additions.append("ADD COLUMN sources JSON NULL")
    if "agent_role" not in existing:
        additions.append("ADD COLUMN agent_role VARCHAR(16) NULL")

    if additions:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE messages " + ", ".join(additions)))
            conn.commit()
        print("messages 表补充列：", ", ".join(additions))

    # M1 聊天室：classrooms 表补 is_private（存量库没有，新库由 create_all 自带）
    classroom_cols = {c["name"] for c in insp.get_columns("classrooms")}
    if "is_private" not in classroom_cols:
        with engine.connect() as conn:
            conn.execute(
                text("ALTER TABLE classrooms ADD COLUMN is_private TINYINT(1) NOT NULL DEFAULT 0")
            )
            conn.commit()
        print("classrooms 表补充列： is_private TINYINT(1) NOT NULL DEFAULT 0")

    # 2026-11-01：classrooms 补 use_tutor（是否配备主讲助教）。
    # 默认 1 让存量课堂保持「有主讲助教」的既有体验。
    if "use_tutor" not in classroom_cols:
        with engine.connect() as conn:
            conn.execute(
                text(
                    "ALTER TABLE classrooms ADD COLUMN use_tutor TINYINT(1) NOT NULL DEFAULT 1"
                )
            )
            conn.commit()
        print("classrooms 表补充列： use_tutor TINYINT(1) NOT NULL DEFAULT 1")

    # M2 认知诊断：student_profiles 补能力向量 / 认知风格 / 施测时间。
    # cognitive_style 允许为 NULL（"样本不足"和"没风格"是两回事），
    # ability_vector 走 NOT NULL，因为它是纯累加结构，空字典就能表达未测。
    # 三列都写成 NULL：MySQL 的 JSON / DATETIME 无法给出现有行的 NOT NULL 值，
    # 硬写 NOT NULL 会让「表里有数据」的存量库迁移失败，而这个迁移是在
    # startup 钩子里静默吞异常的，失败后表现是端点报 Unknown column。
    profile_cols = {c["name"] for c in insp.get_columns("student_profiles")}
    profile_additions = []
    if "ability_vector" not in profile_cols:
        profile_additions.append("ADD COLUMN ability_vector JSON NULL")
    if "cognitive_style" not in profile_cols:
        profile_additions.append("ADD COLUMN cognitive_style JSON NULL")
    if "pretested_at" not in profile_cols:
        profile_additions.append("ADD COLUMN pretested_at DATETIME NULL")
    if profile_additions:
        with engine.connect() as conn:
            conn.execute(
                text("ALTER TABLE student_profiles " + ", ".join(profile_additions))
            )
            conn.commit()
        print("student_profiles 表补充列：", ", ".join(profile_additions))

    # M4 多场景：classrooms 补 scenario（复用上面已取到的列集合）。
    # 用 NOT NULL DEFAULT 'v1'：存量房间必须落到 1v1，否则老房间会突然
    # 走多 Agent（单轮 LLM 调用翻倍），属于静默的行为变更。
    if "scenario" not in classroom_cols:
        with engine.connect() as conn:
            conn.execute(
                text(
                    "ALTER TABLE classrooms "
                    "ADD COLUMN scenario VARCHAR(16) NOT NULL DEFAULT 'v1'"
                )
            )
            conn.commit()
        print("classrooms 表补充列： scenario VARCHAR(16) NOT NULL DEFAULT 'v1'")

    # M4-C 评估报告：agent_runs 补 rag_hits（本轮检索命中条数）。
    # 必须先判表是否存在——这行迁移跑在 startup 钩子里，而 agent_runs 是
    # M4 才新增的表；对还没有这张表的存量库直接 get_columns 会抛
    # NoSuchTableError，被吞掉之后表现是「后续所有补列都没执行」。
    if insp.has_table("agent_runs"):
        run_cols = {c["name"] for c in insp.get_columns("agent_runs")}
        if "rag_hits" not in run_cols:
            with engine.connect() as conn:
                conn.execute(
                    text(
                        "ALTER TABLE agent_runs "
                        "ADD COLUMN rag_hits INT NOT NULL DEFAULT 0"
                    )
                )
                conn.commit()
            print("agent_runs 表补充列： rag_hits INT NOT NULL DEFAULT 0")

        # classroom_id 从 NOT NULL 放宽到 NULL：无课堂的 /chat 也要留痕。
        # 不放宽的后果是这类请求「回答正常、零留痕」，/evaluation/* 全空且
        # 无任何报错——排查时几乎不可能想到是这里。
        # 无条件执行 MODIFY（而不是先判断可空性）：读现有列的 nullable 要
        # 跨 MySQL 版本的 information_schema 语义，判断错了反而不改；
        # 而 MODIFY 成「已经是的状态」是幂等的空操作。
        with engine.connect() as conn:
            conn.execute(
                text("ALTER TABLE agent_runs MODIFY COLUMN classroom_id INT NULL")
            )
            conn.commit()
        print("agent_runs 表放宽约束： classroom_id INT NULL")


def create_tables() -> None:
    from db.session import engine

    if engine is None:
        raise SystemExit("engine 未创建，请检查 backend/.env 的 MYSQL_* 配置")

    Base.metadata.create_all(engine)
    ensure_new_columns()
    print("数据表已创建：")
    for table in Base.metadata.sorted_tables:
        cols = ", ".join(c.name for c in table.columns)
        print(f"  - {table.name}: {len(table.columns)} 列 [{cols}]")


def verify() -> None:
    """建完立刻读一遍，确认表真的落在 MySQL 里（而不是只在 SQLAlchemy 元数据里）。"""
    from db.session import engine

    with engine.connect() as conn:
        rows = conn.execute(text("SHOW TABLES")).fetchall()
    names = sorted(r[0] for r in rows)
    print("\nMySQL 中实际存在的表：", names)

    # 导入即注册：create_all 只认已经被 import 过的模型类，
    # 新增表忘了在这里登记，表就不会被建出来（代码里却能用，伪成功）。
    expected = {
        "users",
        "classrooms",
        "classroom_members",
        "messages",
        "student_profiles",
        "learning_behaviors",
        # M4 多智能体编排留痕
        "agent_runs",
        # XB-23 课堂资料登记（方案 B）
        "classroom_documents",
    }
    missing = expected - set(names)
    if missing:
        raise SystemExit(f"以下表未创建成功：{sorted(missing)}")
    # 这里原来是「✓」：Windows 下把输出重定向到文件 / 管道时，控制台编码是
    # GBK，print 一个 GBK 装不下的字符会抛 UnicodeEncodeError 直接让脚本
    # 退出非 0——表现是「表都建好了，命令却失败」。验收标记用 ASCII 更安全。
    print(f"{len(expected)} 张表全部就绪 OK")


def main() -> None:
    # 只在密码非空时打码：str.replace('', '***') 会在每个字符之间插一串星号，
    # 空密码环境下把连接串打成 ***m***y***s***q***l... 完全没法看。
    _pwd = os.getenv("MYSQL_PASSWORD", "")
    shown = DATABASE_URL.replace(_pwd, "***") if _pwd else DATABASE_URL
    print(f"连接串：{shown}")
    create_database_if_missing()
    create_tables()
    verify()
    print("\n下一步：python seed_demo.py   # T3.6 五项验收")


if __name__ == "__main__":
    main()
