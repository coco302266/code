"""ORM 模型（T3 四张核心表 + T5 画像两张表）。

字段严格按 docs/specs/DEVELOPMENT.md 第 6 节与 DEVELOPMENT_PLAN T3.2–T3.5 定义。

一处主动设计：`Message.user_id` 允许为空，但不代表「AI 用户」——
强行造一个 AI 用户会污染 User 表和角色语义。

assistant 回复现在也写 user_id（T6 改），含义是「这条回复是给谁的」。
同一个课堂里多个学生各有一条对话线，不记归属就只能按课堂粒度恢复
状态，A 的讲解深度会串到 B 头上。历史遗留的 NULL 老消息按公共消息处理。

T5 的两张表都挂在「学生 × 课堂」上而不是只挂学生：
本系统是一个教室 + 一个学科，同一个人在不同课堂里的掌握程度不该互相污染。
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.session import Base


class User(Base):
    """用户：学生或教师。"""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="student")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    memberships: Mapped[list["ClassroomMember"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    messages: Mapped[list["Message"]] = relationship(back_populates="user")

    def __repr__(self) -> str:
        return f"<User {self.id} {self.username} ({self.role})>"


class Classroom(Base):
    """课堂：一个教室 + 一个学科。"""

    __tablename__ = "classrooms"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    subject: Mapped[str] = mapped_column(String(64), nullable=False, default="cs")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # M1 聊天室：是否为「单人私聊」房间（2 人 DM）。普通群聊为 False。
    is_private: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # M4 多场景：这个房间跑哪种协作形态（v1 一对一 / group 小组讨论 /
    # debate 辩论对抗）。建房间时定，/chat 可按要求体覆盖。
    # 用缺省 'v1' 保证存量房间行为不变——没有它，老房间会突然变成多 Agent，
    # 单轮 LLM 调用次数翻倍，既烧额度又改变既有对话体验。
    scenario: Mapped[str] = mapped_column(
        String(16), nullable=False, default="v1", server_default="v1"
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    members: Mapped[list["ClassroomMember"]] = relationship(
        back_populates="classroom", cascade="all, delete-orphan"
    )
    messages: Mapped[list["Message"]] = relationship(
        back_populates="classroom", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Classroom {self.id} {self.name} ({self.subject})>"


class ClassroomMember(Base):
    """课堂成员：谁在哪个课堂里，以什么身份。"""

    __tablename__ = "classroom_members"
    __table_args__ = (
        UniqueConstraint("classroom_id", "user_id", name="uq_classroom_user"),
        Index("idx_member_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    classroom_id: Mapped[int] = mapped_column(
        ForeignKey("classrooms.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="student")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    classroom: Mapped["Classroom"] = relationship(back_populates="members")
    user: Mapped["User"] = relationship(back_populates="memberships")

    def __repr__(self) -> str:
        return f"<ClassroomMember classroom={self.classroom_id} user={self.user_id}>"


class Message(Base):
    """聊天记录。

    role: student / assistant / teacher
    user_id: assistant 消息为 NULL
    """

    __tablename__ = "messages"
    __table_args__ = (Index("idx_message_classroom", "classroom_id", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    classroom_id: Mapped[int] = mapped_column(
        ForeignKey("classrooms.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    # T4：教学状态随消息落库。原来 topic / hint_level 只在客户端内存中，
    # 刷新页面就归零、多轮递进断掉。现在每次回答把当时的知识点与讲解深度
    # 一并写进来，下次请求从最后一条消息恢复——服务端成为状态的唯一真相源。
    topic: Mapped[str | None] = mapped_column(String(64), nullable=True)
    hint_level: Mapped[int] = mapped_column(nullable=False, default=0)
    # sources 落库（2026-10-21，对应 C 待确认项 #2）：每轮助手回复依据的检索资料
    # （kb 知识库 / upload 上传 PDF），存 summarize_sources 产出的结构化 items 列表，
    # 不含正文。原先 sources 只在流式那一刻存在，落库消息无该字段，刷新后"依据"消失。
    # 空或未知场景存 None/[]，前端据此区分"明确无资料"与"未覆盖"。
    sources: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # M1 聊天室：这条 AI 回复由哪个角色 Agent 产出（tutor/teacher/peer/examiner）。
    # 人类消息（student/teacher）此列为 NULL。用于群聊房间里区分"AI 以什么身份发言"。
    agent_role: Mapped[str | None] = mapped_column(String(16), nullable=True)

    classroom: Mapped["Classroom"] = relationship(back_populates="messages")
    user: Mapped["User | None"] = relationship(back_populates="messages")

    def __repr__(self) -> str:
        return f"<Message {self.id} classroom={self.classroom_id} role={self.role}>"


class StudentProfile(Base):
    """学生画像（T5）：一个人在某个课堂里学到哪了、卡在哪了。

    不是手工填的表，而是每轮对话自动沉淀出来的：
    - knowledge_mastery：知识点 -> 0~1 掌握度，答对往上走、讲透还没懂往下走
    - weak_points：模型抓到的具体误解，一句话一条
    - overall_level：由掌握度均值推导，不让学生自己填
    - learning_preference：由学习行为统计推导

    PUT 接口允许老师/学生手工修正，但下一次对话又会按新证据继续更新。
    """

    __tablename__ = "student_profiles"
    __table_args__ = (
        UniqueConstraint("user_id", "classroom_id", name="uq_profile_user_classroom"),
        Index("idx_profile_classroom", "classroom_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    classroom_id: Mapped[int] = mapped_column(
        ForeignKey("classrooms.id", ondelete="CASCADE"), nullable=False
    )
    overall_level: Mapped[str] = mapped_column(
        String(16), nullable=False, default="beginner"
    )
    learning_preference: Mapped[str] = mapped_column(
        Text, nullable=False, default=""
    )
    # {"死锁": 0.3, "TCP 三次握手": 0.8}
    knowledge_mastery: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict
    )
    # ["把死锁理解成进程死循环", "以为 TCP 握手是为了协商端口"]
    weak_points: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    # --- M2 认知诊断（CDT）附加字段 ---------------------------------------
    # knowledge_mastery 是「分数」，ability_vector 是「凭什么给这个分」。
    # 两者必须分开：画像一旦被追问「为什么判他会了」，拿不出施测证据就成了
    # 黑箱；而且后续要换估计模型（比如换成三级 Rocket）时，留着原始作答
    # 计数才能重算，只留一个分数就只能从头施测。
    #   {"死锁": {"theta": 0.62, "attempts": 2, "last_difficulty": 0.7}}
    # 列本身允许 NULL：MySQL 的 JSON 类型不能有 DEFAULT，给已存在数据的表
    # 补 NOT NULL JSON 列会直接报 1138。所以让它可空，由调用方统一 `or {}`。
    ability_vector: Mapped[dict | None] = mapped_column(
        JSON, nullable=True, default=dict
    )
    # 认知风格。None 表示尚未达到可判定的样本量，不是"没有风格"。
    # 结构见 agent/pretest.py:infer_cognitive_style
    #   {"ready": true, "confidence": 0.75, "dimensions": [...]}
    cognitive_style: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    pretested_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime, nullable=True
    )

    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    user: Mapped["User"] = relationship()
    classroom: Mapped["Classroom"] = relationship()

    def __repr__(self) -> str:
        return (
            f"<StudentProfile user={self.user_id} "
            f"classroom={self.classroom_id} level={self.overall_level}>"
        )


class AgentRun(Base):
    """M4 多智能体编排留痕：一轮 /chat 里到底发生了什么。

    为什么只建这一张表、而不建 `agent_messages`：
    每个 Agent 的发言本身就是一条完整的助手消息，直接落在 `messages` 里
    （用 `agent_role` 区分身份）就能被现有查询 / 增量轮询 / 前端渲染复用。
    再造一张 agent_messages 等于把同一份文本存两遍、维护两套查询，
    是典型的「为了记元数据而复制数据」。所以这里只记**轮次级**的信息。

    用途有两个：
    - 可解释性：事后能回答「这轮为什么是 3 个 Agent」「为什么降级了」；
    - 成果#4 评估报告：按轮次统计参与角色数、耗时、降级率。

    degraded / reason 是关键：编排层任何异常输入都会收敛成单 Agent，
    如果不记下来，「多场景没生效」会变成一个完全静默的失败。
    """

    __tablename__ = "agent_runs"
    __table_args__ = (
        Index("idx_agent_run_classroom", "classroom_id", "id"),
        Index("idx_agent_run_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # 允许 NULL：**无课堂的 /chat 也必须留痕**。
    # 早先这里是非空的，后果是前端不带 classroom_id 时（纯问答页 / 老客户端）
    # 整轮静默不落库——回答完全正常，唯一症状是 /evaluation/* 全空，且没有任何
    # 报错。留痕的价值（这轮派了几个 Agent、有没有降级）与是否属于某个课堂无关，
    # 所以放开约束；代价是这类轮次没有发言正文可评分，由评估层的
    # `runs_without_classroom` 显式提示，而不是让它们直接消失。
    classroom_id: Mapped[int | None] = mapped_column(
        ForeignKey("classrooms.id", ondelete="CASCADE"), nullable=True
    )
    # 这一轮是「给谁」的回复。允许 NULL 以兼容消息被清理 / 匿名会话。
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    scenario: Mapped[str] = mapped_column(String(16), nullable=False, default="v1")
    topic: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 实际参与的角色序列 ["tutor","peer"]——与计划可能不同（截断 / 降级后）。
    roles: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    degraded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # 降级原因 / 编排说明。空字符串表示正常编排。
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 本轮编排总耗时（毫秒），评估报告的延迟数据就靠它——
    # 不记的话「多 Agent 到底慢多少」只能靠猜。
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 本轮各 Agent 发言落库后的 message id，供评估报告按轮次回溯原文。
    message_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # 本轮检索命中的资料条数（M4-C）。一轮 /chat 只检索一次、所有 Agent
    # 共用同一份上下文，所以「命中几条」是轮次级的量，不是消息级的。
    # 评估报告里的「回答有据可依的比例」就靠它，不记就只能去 join messages
    # 逐条看 sources 是否为空——那份工作每次出报告都要重做一遍。
    rag_hits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    classroom: Mapped["Classroom"] = relationship()
    user: Mapped["User | None"] = relationship()

    def __repr__(self) -> str:
        return (
            f"<AgentRun classroom={self.classroom_id} scenario={self.scenario} "
            f"roles={self.roles}>"
        )


class ClassroomDocument(Base):
    """课堂资料登记表（XB-23 方案 B）：教师上传的长期生效资料。

    **为什么要有这张表**：Chroma 里的 `classroom_{id}_docs` 只是**派生索引**，
    事实源是落盘的 PDF 原件 + 这一行登记。没有它会出现三个问题：
    ① 换 embedding 模型 / 改切片参数 / Chroma 迁移时课堂库不可重建（原件的
       路径与归属只在内存里，进程一重启就查不到）；
    ② 无法去重——同一份讲义重复上传会在同一 collection 里叠两份切片，检索
       返回重复片段且没人知道是同一篇；
    ③ 无法按课堂统计容量、无法软删后审计「谁在什么时候删了什么」。

    软删（status=deleted）而不是物理删行：教师误删后要能追溯，物理清理交给
    `scripts/purge_classroom_docs.py` 或后续后台任务。
    """

    __tablename__ = "classroom_documents"
    __table_args__ = (
        Index("idx_classroom_doc_classroom", "classroom_id", "status"),
        Index("idx_classroom_doc_hash", "classroom_id", "doc_hash"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    classroom_id: Mapped[int] = mapped_column(
        ForeignKey("classrooms.id", ondelete="CASCADE"), nullable=False
    )
    # 上传者。教师账号被删时置空而不是连资料一起删——资料属于课堂，不属于人。
    teacher_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_path: Mapped[str] = mapped_column(String(512), nullable=False)
    # 内容 sha256（整串，不是前缀）：去重与「同一份文件重复上传」判定用。
    doc_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    file_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # active / deleted
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", server_default="active"
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    classroom: Mapped["Classroom"] = relationship()

    def __repr__(self) -> str:
        return (
            f"<ClassroomDocument {self.id} classroom={self.classroom_id} "
            f"{self.filename} ({self.status})>"
        )


class LearningBehavior(Base):
    """学习行为流水（T5.3）：ask_question / repeat_question / request_explanation。

    画像里的 learning_preference 就是从这张表统计出来的——
    只留结论会丢掉推导依据，留着流水才能回头解释「为什么判定他是 beginner」。
    """

    __tablename__ = "learning_behaviors"
    __table_args__ = (
        Index("idx_behavior_user_topic", "user_id", "topic"),
        Index("idx_behavior_classroom", "classroom_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    classroom_id: Mapped[int] = mapped_column(
        ForeignKey("classrooms.id", ondelete="CASCADE"), nullable=False
    )
    topic: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    action: Mapped[str] = mapped_column(String(24), nullable=False)
    hint_level: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    user: Mapped["User"] = relationship()
    classroom: Mapped["Classroom"] = relationship()

    def __repr__(self) -> str:
        return f"<LearningBehavior user={self.user_id} {self.action} {self.topic}>"
