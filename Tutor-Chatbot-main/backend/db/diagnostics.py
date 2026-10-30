"""M4-C 留痕链路自检：**让「/evaluation/* 全空」自己说出原因**。

为什么需要这一层（这是一个真踩过的坑）：
编排留痕丢失有四种成因，而它们的表现**完全一样**——接口返回 200、turns=0、
rows=[]。不额外做诊断，使用者只能靠猜：

1. 后端没配 `MYSQL_*`（不落库模式）→ 根本没写；
2. MySQL 连不上（服务没起 / 库没建）→ 写不进去；
3. `agent_runs` 表不存在（漏跑 `init_db.py`，或 MySQL 比后端晚启动）→ 写不进去；
4. `/chat` 没带 `classroom_id`，或带的课堂 id 与实际不符 → 写了但被过滤掉。

前三种是「数据没进来」，第四种是「数据在但没被选中」——**区分它们只需要一个
不受过滤条件影响的总行数**。所以 `diagnose()` 除状态外一定返回 `total_runs`：
过滤后 0 而总数 30，问题立刻定位到过滤条件，而不是去怀疑数据库。

设计约束：
- **零副作用**。这一层只读，绝不建表补列——建表是启动钩子和写路径的事。
  诊断顺手修问题会让「读接口有写行为」，排查时更难推理。
- **不抛异常**。诊断接口存在的意义就是「出问题的时候还能访问」，
  任何一步失败都降级成结构化状态，让调用方看得到原因。
"""

from __future__ import annotations

from sqlalchemy import func, inspect, select, text

from db.models import AgentRun

# 数据库三态。用字符串而不是 bool：true/false 表达不了「没配」和「配了但连不上」
# 这两种修法完全不同的情况。
DB_UP = "up"
DB_DOWN = "down"
DB_DISABLED = "disabled"

RUNS_TABLE = AgentRun.__tablename__


def inspect_database() -> dict:
    """数据库连通性。

    自己连一次而不是复用 `db_available()`：那个函数把异常吞成了 bool，
    而这里需要把原始错误带给使用者——「连不上」和「密码错了」的修法不同。
    """
    try:
        from db.session import SessionLocal, engine
    except Exception as exc:  # pragma: no cover - 配置损坏
        return {"state": DB_DOWN, "detail": f"导入 db.session 失败：{exc}"}

    if engine is None or SessionLocal is None:
        return {
            "state": DB_DISABLED,
            "detail": "未配置 DATABASE_URL / MYSQL_*，服务运行在「不落库」模式",
        }
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"state": DB_UP, "detail": ""}
    except Exception as exc:
        return {"state": DB_DOWN, "detail": f"{type(exc).__name__}: {exc}"}


def inspect_runs_table(db) -> dict:
    """agent_runs 表结构与模型的差异。

    期望列直接取 `AgentRun.__table__`：模型是唯一事实源，硬编码一份列名
    迟早会和代码漂移（rag_hits 就是这么补进来的）。
    """
    empty = {"exists": False, "missing_columns": [], "detail": ""}
    if db is None:
        return {**empty, "detail": "无数据库会话"}
    try:
        # 必须拿 engine 而不是 Session 去 inspect：sqlalchemy.inspect(会话对象)
        # 在这版上直接抛 "No inspection system is available"，被吞掉之后会变成
        # 「表不存在」——对着一张真实存在的表报缺失，比不自检更糟（会把人引去
        # 跑 init_db，而实际问题是过滤条件不对）。
        getter = getattr(db, "get_bind", None)
        inspector = inspect(getter() if callable(getter) else db)
        exists = inspector.has_table(RUNS_TABLE)
    except Exception as exc:
        return {**empty, "detail": f"读取表结构失败：{exc}"}
    if not exists:
        return {**empty, "detail": f"{RUNS_TABLE} 表不存在"}

    try:
        present = {col["name"] for col in inspector.get_columns(RUNS_TABLE)}
    except Exception as exc:
        return {"exists": True, "missing_columns": [], "detail": f"读取列失败：{exc}"}
    expected = [col.name for col in AgentRun.__table__.columns]
    missing = [name for name in expected if name not in present]
    return {
        "exists": True,
        "missing_columns": missing,
        "detail": f"缺列：{', '.join(missing)}" if missing else "",
    }


def count_runs(db, *, without_classroom: bool = False) -> int | None:
    """留痕总行数。返回 None 表示查不出来（表缺失 / 连不上），**不是 0**。

    为什么区分 None 和 0：「查询失败」和「确实没有数据」是两回事，
    前者要修库，后者要去跑几轮对话。
    """
    if db is None:
        return None
    try:
        query = select(func.count()).select_from(AgentRun)
        if without_classroom:
            query = query.where(AgentRun.classroom_id.is_(None))
        return int(db.execute(query).scalar() or 0)
    except Exception:
        return None


def build_hint(state: str, table: dict, total: int | None, unscoped: int | None) -> str:
    """把状态翻译成一句「下一步该干什么」。

    诊断信息如果只给状态码，等于把解释成本又推回给使用者。
    """
    if state == DB_DISABLED:
        return (
            "后端没配数据库，当前是「不落库」模式：/chat 正常但零留痕。"
            "在 backend/.env 填好 MYSQL_* 后重启服务。"
        )
    if state == DB_DOWN:
        return "MySQL 连不上：确认服务已启动、库 ai_classroom 已建、账号密码正确。"
    if not table.get("exists"):
        return (
            "agent_runs 表不存在。服务启动时会自动建；若仍缺失，"
            "在 backend/ 下执行 python init_db.py 后重启。"
        )
    if table.get("missing_columns"):
        return f"agent_runs 缺列：{', '.join(table['missing_columns'])}。" \
               "执行 python init_db.py 补列，缺列期间新留痕会写入失败。"
    if total is None:
        return "留痕表读不出来：检查 MySQL 用户是否对该表有 SELECT 权限。"
    if total == 0:
        return (
            "表里一条留痕都没有。确认 /chat 请求至少跑过一轮——"
            "编排留痕只在实际产生回答后写入。"
        )
    if unscoped and unscoped == total:
        return (
            f"全部 {total} 条留痕都没有 classroom_id（/chat 未带课堂）。"
            "不带 classroom_id 时发言正文不落库，能统计客观指标但评分表无原文。"
        )
    return ""


def empty_result_hint(total: int | None) -> str:
    """「过滤后 0 条、但库里有数据」——四种成因里最容易误判的一种。

    症状和「数据库根本没接上」一模一样（都是 0），但修法完全相反：
    前者改查询参数，后者去修库。所以这个提示必须由**知道过滤结果的调用方**
    来加，diagnose() 自己看不到过滤条件，给不出来。
    """
    if not total:
        return ""
    return (
        f"库里共有 {total} 条留痕，但当前过滤条件下一条都没选中："
        "检查 classroom_id / scenario / user_id 是否与实际数据一致"
        "（不带 classroom_id 的请求留下的痕，classroom_id 为 NULL，按课堂查会漏掉）。"
    )


def diagnose(db) -> dict:
    """产出完整的留痕链路自检结果（端点与脚本共用同一份口径）。"""
    database = inspect_database()
    state = database["state"]

    if state != DB_UP:
        hint = build_hint(state, {"exists": True}, None, None)
        return {
            "ready": False,
            "database": state,
            "database_detail": database["detail"],
            "table": {"exists": False, "missing_columns": [], "detail": ""},
            "total_runs": None,
            "runs_without_classroom": None,
            "hint": hint,
        }

    table = inspect_runs_table(db)
    total = count_runs(db)
    unscoped = count_runs(db, without_classroom=True) if table["exists"] else None

    return {
        "ready": bool(table["exists"] and not table["missing_columns"] and total),
        "database": state,
        "database_detail": database["detail"],
        "table": table,
        "total_runs": total,
        "runs_without_classroom": unscoped,
        "hint": build_hint(state, table, total, unscoped),
    }
