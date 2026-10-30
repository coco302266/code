"""数据库连接与会话管理。

设计原则：**数据库是可选的**。
`DATABASE_URL` 没配、或连不上时，`engine` 为 None，`db_available()` 返回 False，
`/chat` 照常工作，只是不落库——避免本地没起 MySQL 时整个服务起不来。
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# 独立脚本（init_db.py / seed_demo.py）不走 app.py，这里兜底加载一次 .env，
# 保证任何入口 import db 都能读到 MYSQL_* 配置。重复调用无副作用。
#
# 必须按文件路径加载而不是依赖当前工作目录：本模块在 import 时就要读
# MYSQL_* 并建 engine，一旦从别的目录启动（uvicorn --app-dir、定时任务、
# 别的进程拉起），cwd 找不到 .env，服务就会静默退化成「无数据库」模式——
# 不报错、不落库，很难查。
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:  # pragma: no cover
    pass


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


def _build_url() -> str:
    """从环境变量拼 MySQL 连接串。

    优先读完整的 `DATABASE_URL`；否则用 `MYSQL_*` 分项拼装。
    """
    raw = os.getenv("DATABASE_URL")
    if raw:
        return raw

    user = os.getenv("MYSQL_USER", "root")
    password = os.getenv("MYSQL_PASSWORD", "")
    host = os.getenv("MYSQL_HOST", "127.0.0.1")
    port = os.getenv("MYSQL_PORT", "3306")
    name = os.getenv("MYSQL_DATABASE", "ai_classroom")
    return f"mysql+pymysql://{user}:{password}@{host}:{port}/{name}?charset=utf8mb4"


DATABASE_URL = _build_url()

# 只有显式配置过才尝试建 engine：没配就保持 None，让服务以「无数据库」模式运行。
DB_ENABLED = bool(
    os.getenv("DATABASE_URL")
    or os.getenv("MYSQL_HOST")
    or os.getenv("MYSQL_USER")
    or os.getenv("MYSQL_DATABASE")
)

engine: Engine | None = None
SessionLocal: sessionmaker | None = None

if DB_ENABLED:
    try:
        engine = create_engine(
            DATABASE_URL,
            pool_pre_ping=True,  # MySQL 默认 8 小时断连，取连接前先探活
            pool_recycle=3600,
            future=True,
        )
        SessionLocal = sessionmaker(
            bind=engine,
            autoflush=False,
            autocommit=False,
            future=True,
            # 关键：commit 后不要把实例属性标记为失效。
            # 否则在 `with get_db() as db` 结束后再读 obj.id 会抛
            # DetachedInstanceError（对象已脱离会话，无法回数据库刷新）。
            expire_on_commit=False,
        )
    except Exception as exc:  # pragma: no cover - 配置错误不该让进程崩
        print(f"[db] 创建 engine 失败，将以无数据库模式运行：{exc}", flush=True)
        engine = None
        SessionLocal = None


def db_available() -> bool:
    """数据库是否真正可用（配置存在且能连通）。"""
    if engine is None:
        return False
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@contextmanager
def get_db() -> Iterator:
    """FastAPI 依赖 / 脚本共用的会话上下文。

    用法：
        with get_db() as db:
            db.add(obj)

    数据库不可用时 yield None，调用方需自行判空——这样 T3 之前写的
    纯逻辑测试（run_tests.py）不会因为没数据库而集体失败。
    """
    if SessionLocal is None:
        yield None
        return

    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
