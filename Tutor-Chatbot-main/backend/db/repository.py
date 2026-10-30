"""数据访问函数（T3）。

把 SQL 细节收在这里，`app.py` 只调用语义化函数，不直接写查询。
所有函数都接受 `db=None`——数据库没配时静默跳过，不阻断对话。
"""

from __future__ import annotations

import datetime as dt
from typing import List, Sequence

from sqlalchemy import func, or_, select, true
from sqlalchemy.orm import joinedload

from agent.state import HistoryMessage
from db.models import (
    AgentRun,
    Classroom,
    ClassroomDocument,
    ClassroomMember,
    Message,
    User,
)


def _owned_by(user_id: int | None):
    """筛出「属于这个学生」的消息。

    user_id 为空表示不按人过滤（旧行为，前端只传 session_id 时仍是课堂粒度）。

    为什么要兼容 user_id IS NULL：T6 之前 assistant 回复不记归属，
    库里已经攒了一批 NULL 的老消息。一刀切按 user_id 精确匹配会把它们
    全部过滤掉，升级后学生的上下文凭空断掉一截。折中做法是老数据当作
    公共消息继续可见，新数据（都带 user_id）严格按学生隔离。
    """
    if user_id is None:
        return true()
    return or_(Message.user_id == user_id, Message.user_id.is_(None))


def create_user(db, username: str, role: str = "student") -> User | None:
    if db is None:
        return None
    existing = db.execute(
        select(User).where(User.username == username)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    user = User(username=username, role=role)
    db.add(user)
    db.flush()
    return user


def create_classroom(
    db,
    name: str,
    subject: str = "cs",
    description: str | None = None,
    is_private: bool = False,
    members: list[int] | None = None,
    teacher_members: list[int] | None = None,
    scenario: str = "v1",
) -> Classroom | None:
    """创建课堂。

    M1 聊天室新增两个可选能力：
    - is_private：标记为「单人私聊」房间（前端当 DM 渲染）。
    - members / teacher_members：创建时一次性把参与者加为成员，省去
      私聊场景里「先建房间再逐个 add_member」的两三次往返。

    M4 再加 scenario：建房时就定好这个房间跑哪种协作形态。
    归一化交给 `agent.scenarios.normalize_scenario`——这里只做透传，
    repository 不该替业务决定「非法值怎么办」。
    """
    if db is None:
        return None
    room = Classroom(
        name=name,
        subject=subject,
        description=description,
        is_private=is_private,
        scenario=scenario,
    )
    db.add(room)
    db.flush()
    for uid in members or []:
        add_member(db, room.id, uid, "student")
    for uid in teacher_members or []:
        add_member(db, room.id, uid, "teacher")
    return room


def add_member(db, classroom_id: int, user_id: int, role: str) -> ClassroomMember | None:
    if db is None:
        return None
    existing = db.execute(
        select(ClassroomMember).where(
            ClassroomMember.classroom_id == classroom_id,
            ClassroomMember.user_id == user_id,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    member = ClassroomMember(classroom_id=classroom_id, user_id=user_id, role=role)
    db.add(member)
    db.flush()
    return member


def save_message(
    db,
    classroom_id: int,
    user_id: int | None,
    role: str,
    content: str,
    topic: str | None = None,
    hint_level: int = 0,
    sources: list | None = None,
    agent_role: str | None = None,
) -> Message | None:
    """落一条消息。

    topic / hint_level 只对 assistant 消息有意义：记录「这次回答讲的是
    哪个知识点、讲到第几层」，下一次请求据此恢复状态。

    assistant 回复也要记 user_id（T6 改）：它表示「这条回复是给谁的」。
    原来一律写 None，导致同一个课堂里所有学生的对话混在一起分不开，
    状态恢复只能按课堂粒度，多人课堂必然串台。

    agent_role（M1）：AI 回复由哪个角色 Agent 产出（tutor/teacher/...），
    人类消息为 None。
    """
    if db is None or not content:
        return None
    message = Message(
        classroom_id=classroom_id,
        user_id=user_id,
        role=role,
        content=content,
        topic=topic,
        hint_level=hint_level,
        sources=sources,
        agent_role=agent_role,
    )
    db.add(message)
    db.flush()
    return message


def get_classroom_scenario(db, classroom_id: int) -> str | None:
    """取课堂的场景（v1 / group / debate），查不到返回 None。

    单独一个函数而不是顺手在 /chat 里 join，是因为「课堂场景」要能被
    建房接口和清单接口共用；返回 None 让调用方自己决定回落值，
    避免 repository 层悄悄替业务做默认决策。
    """
    if db is None or classroom_id is None:
        return None
    return db.execute(
        select(Classroom.scenario).where(Classroom.id == classroom_id)
    ).scalar_one_or_none()


def save_agent_run(
    db,
    classroom_id: int,
    *,
    user_id: int | None = None,
    scenario: str = "v1",
    topic: str | None = None,
    roles: list | None = None,
    degraded: bool = False,
    reason: str = "",
    duration_ms: int = 0,
    message_ids: list | None = None,
    rag_hits: int = 0,
) -> AgentRun | None:
    """记一轮多智能体编排（M4）。

    留痕失败不能影响已经流出去的回答——调用方负责吞异常，
    这里只管把事实写进去。

    classroom_id 允许为 None（无课堂的 /chat）：早先这里直接 return，
    结果是这类请求回答正常、却一行留痕都没有，/evaluation/* 全空且无任何报错。
    """
    if db is None:
        return None
    run = AgentRun(
        classroom_id=classroom_id,
        user_id=user_id,
        scenario=scenario,
        topic=topic,
        roles=roles or [],
        degraded=degraded,
        reason=reason or "",
        duration_ms=duration_ms,
        message_ids=message_ids,
        rag_hits=rag_hits,
    )
    db.add(run)
    db.flush()
    return run


def list_agent_runs(
    db,
    *,
    classroom_id: int | None = None,
    user_id: int | None = None,
    scenario: str | None = None,
    limit: int = 200,
) -> list[AgentRun]:
    """按条件查编排留痕（M4-C 评估报告的数据入口）。

    为什么单独抽一个函数、而不是让评估脚本直接写 select：
    过滤条件（课堂 / 学生 / 场景）会随报告口径变，只改这一处即可；
    而且端点与脚本共用同一份查询，避免「接口看到的」和「报告算的」不一致。

    limit 由调用方钳制，这里只兜底防负数——把业务上限写进 repository
    会让「出整学期报告」这种正当需求被迫改底层。
    """
    if db is None:
        return []
    query = select(AgentRun).order_by(AgentRun.id.desc())
    if classroom_id is not None:
        query = query.where(AgentRun.classroom_id == classroom_id)
    if user_id is not None:
        query = query.where(AgentRun.user_id == user_id)
    if scenario:
        query = query.where(AgentRun.scenario == scenario)
    return list(
        db.execute(query.limit(max(1, limit))).scalars().all()
    )


def get_messages_by_ids(db, ids: list[int]) -> dict[int, str]:
    """按 id 批量取正文（M4-C：评估报告要展示「这条发言说了什么」给人打分）。

    一次 `in_()` 批量取而不是在循环里逐条查：一轮 3 个 Agent、一份报告几十轮，
    逐条查会把出报告变成几百次往返。返回 `{id: content}`，查不到的 id 直接缺席，
    由调用方决定降级文案。
    """
    if db is None or not ids:
        return {}
    rows = db.execute(
        select(Message.id, Message.content).where(Message.id.in_(ids))
    ).all()
    return {int(row[0]): row[1] for row in rows}


def load_last_state(
    db, classroom_id: int, user_id: int | None = None
) -> tuple[str, int]:
    """取这个学生在这个课堂里最后一条「有效教学状态」。

    服务端是状态的唯一真相源：只要落了 classroom_id，就不再信客户端
    回传的 topic / hint_level，一律从这里恢复。这样刷新页面、换设备、
    甚至前端压根不传状态，多轮讲解深度都不会断。

    必须按学生隔离（T6 修）。原先只按课堂取最后一条，同一个课堂里
    A 在学死锁、B 在学 TCP，两人的状态会互相覆盖：B 提问时恢复出的是
    A 的 topic，讲解深度直接串台。表现是新学生第一次提问就拿到
    hint_level=2 的长篇大论——他其实什么都没问过。
    """
    if db is None:
        return "", 0
    row = (
        db.execute(
            select(Message)
            .where(
                Message.classroom_id == classroom_id,
                _owned_by(user_id),
                Message.topic.isnot(None),
                Message.topic != "",
            )
            .order_by(Message.id.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    if row is None:
        return "", 0
    return row.topic or "", row.hint_level or 0


def is_member(db, classroom_id: int, user_id: int | None) -> bool:
    """这个人是否真的在这个课堂里（T4.4：后端必须知道「谁」）。"""
    if db is None or user_id is None:
        return False
    return (
        db.execute(
            select(ClassroomMember).where(
                ClassroomMember.classroom_id == classroom_id,
                ClassroomMember.user_id == user_id,
            )
        )
        .scalars()
        .first()
        is not None
    )


def load_history(
    db, classroom_id: int, user_id: int | None = None, limit: int = 20
) -> List[HistoryMessage]:
    """从数据库读回这个学生最近的对话，转成 LangChain 能吃的 history。

    取最近 N 条后按 id 升序排列——先取末尾再反转，避免把最老的消息
    截掉（直接 order_by(id).limit(n) 拿到的是最早的 n 条）。

    同样按学生隔离（T6 修）：喂给模型的上下文里如果混着别人的问答，
    「追问」「还是不懂」这些指代就没法判断，评估节点会以为这个学生
    已经听了好几轮还没懂，一上来就把讲解深度抬上去。
    教师要看全课堂的对话请用 list_messages。
    """
    if db is None:
        return []
    rows = (
        db.execute(
            select(Message)
            .where(Message.classroom_id == classroom_id, _owned_by(user_id))
            .order_by(Message.id.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    ordered: Sequence[Message] = list(reversed(rows))
    return [
        HistoryMessage(
            role="user" if m.role in ("student", "teacher") else "assistant",
            content=m.content,
        )
        for m in ordered
    ]


def list_messages(
    db, classroom_id: int, limit: int = 100, after: int | None = None
) -> List[Message]:
    """课堂全量消息（群聊视图），按 id 升序。

    after：只返回 id 大于该值的消息（M1 聊天室增量轮询，避免每次拉全量）。

    预加载 user：调用方（app.py 的 api_list_messages）要用 m.user.username
    给每条消息标出发言人。不预加载的话，每条消息都会各触发一次延迟查询
    （100 条消息 = 101 次 SQL），而且在 session 关闭后再访问会直接抛
    DetachedInstanceError —— 500。
    """
    if db is None:
        return []
    query = (
        select(Message)
        .where(Message.classroom_id == classroom_id)
        .options(joinedload(Message.user))
        .order_by(Message.id)
    )
    if after is not None:
        query = query.where(Message.id > after)
    query = query.limit(limit)
    # .unique() 是 Result 的方法（db.execute 的返回值），不是 Select 的。
    # 挂错位置会在运行时抛 AttributeError -> 端点 500。
    return list(db.execute(query).unique().scalars().all())


def list_classrooms_for_user(db, user_id: int) -> List[dict]:
    """返回该用户所属的全部课堂（"我的课堂"）。

    前端原先只能把课堂列表存在本机 localStorage，换设备/刷新就丢。
    这里按成员关系返回该用户加入的课堂及其在其中的角色。
    """
    if db is None or user_id is None:
        return []
    rows = (
        db.execute(
            select(Classroom, ClassroomMember.role)
            .join(
                ClassroomMember,
                ClassroomMember.classroom_id == Classroom.id,
            )
            .where(ClassroomMember.user_id == user_id)
            .order_by(Classroom.created_at)
        )
        .all()
    )
    return [
        {
            "id": room.id,
            "name": room.name,
            "subject": room.subject,
            "description": room.description,
            "role": role,
            "is_private": bool(room.is_private),
            "scenario": room.scenario,
            "created_at": room.created_at.isoformat() if room.created_at else None,
        }
        for room, role in rows
    ]


# ---------------- 课堂资料登记（XB-23 方案 B） ----------------
#
# 这一组函数只碰 `classroom_documents` 登记表，不碰 Chroma。向量库是派生索引，
# 它的增删在 `agent/rag/classroom_store.py` 里；两处必须由调用方成对调用，
# 否则会出现「表里有行但检索不到」或「检索得到但没人能删」的静默不一致。
#
# 统一约定：只操作 status='active' 的行。软删的行保留作审计，不参与配额、
# 不参与去重判定——这样教师删掉一份后能重新上传同一份。


def _active_classroom_docs(db, classroom_id: int):
    return select(ClassroomDocument).where(
        ClassroomDocument.classroom_id == classroom_id,
        ClassroomDocument.status == "active",
    )


def find_classroom_document_by_hash(
    db, classroom_id: int, doc_hash: str
) -> ClassroomDocument | None:
    """同一课堂内按内容哈希找已存在的资料；没有就返回 None（去重依据）。"""
    if db is None or not doc_hash:
        return None
    return db.execute(
        _active_classroom_docs(db, classroom_id).where(
            ClassroomDocument.doc_hash == doc_hash
        )
    ).scalars().first()


def get_classroom_document(
    db, classroom_id: int, doc_id: str
) -> ClassroomDocument | None:
    """按 (课堂, 文档 id) 取 active 行。跨课堂查不到——这是权限边界的一半。"""
    if db is None or not doc_id:
        return None
    return db.execute(
        _active_classroom_docs(db, classroom_id).where(
            ClassroomDocument.id == doc_id
        )
    ).scalars().first()


def classroom_document_usage(db, classroom_id: int) -> tuple[int, int]:
    """返回 (active 文件数, 总字节数)，配额判定用。

    一条 SQL 聚合而不是先取全表再 len()：课堂资料长大后前者是常数开销。
    """
    if db is None:
        return 0, 0
    row = db.execute(
        select(
            func.count(ClassroomDocument.id),
            func.coalesce(func.sum(ClassroomDocument.file_bytes), 0),
        ).where(
            ClassroomDocument.classroom_id == classroom_id,
            ClassroomDocument.status == "active",
        )
    ).one()
    return int(row[0] or 0), int(row[1] or 0)


def add_classroom_document(
    db,
    *,
    doc_id: str,
    classroom_id: int,
    teacher_id: int | None,
    filename: str,
    stored_path: str,
    doc_hash: str,
    chunk_count: int,
    file_bytes: int,
) -> ClassroomDocument | None:
    if db is None:
        return None
    doc = ClassroomDocument(
        id=doc_id,
        classroom_id=classroom_id,
        teacher_id=teacher_id,
        filename=filename,
        stored_path=stored_path,
        doc_hash=doc_hash,
        chunk_count=chunk_count,
        file_bytes=file_bytes,
        status="active",
    )
    db.add(doc)
    db.commit()
    return doc


def mark_classroom_document_deleted(db, doc: ClassroomDocument) -> bool:
    """软删。返回是否真的改动了（幂等：已删返回 False）。"""
    if db is None or doc is None or doc.status != "active":
        return False
    doc.status = "deleted"
    doc.deleted_at = dt.datetime.now()
    db.commit()
    return True


def list_classroom_documents(db, classroom_id: int) -> list[dict]:
    """课堂资料清单（给前端 / 管理用）。不含正文，只带元信息。"""
    if db is None:
        return []
    rows = db.execute(
        _active_classroom_docs(db, classroom_id).order_by(
            ClassroomDocument.created_at
        )
    ).scalars().all()
    return [
        {
            "doc_id": row.id,
            "filename": row.filename,
            "teacher_id": row.teacher_id,
            "chunk_count": row.chunk_count,
            "file_bytes": row.file_bytes,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]


def list_classroom_ids_with_documents(db) -> set[int]:
    """有登记行的课堂 id 集合（清理脚本用：和磁盘目录对账）。"""
    if db is None:
        return set()
    rows = db.execute(select(ClassroomDocument.classroom_id).distinct()).all()
    return {int(r[0]) for r in rows}
