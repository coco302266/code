import asyncio
from dataclasses import dataclass
import hashlib
import ipaddress
import json
import logging
import os
import re
import secrets
import datetime as dt
from functools import lru_cache
from pathlib import Path
import tempfile
import time
from typing import List, Literal
import uuid

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile

# .env 必须在下面这些 import 之前加载：`db.session` 在模块级就读取 MYSQL_*
# 并建 engine，晚一步服务会静默退化成「无数据库」模式。
# 按文件路径加载而不是靠 cwd——从别的目录启动（uvicorn --app-dir）时
# cwd 里根本没有 .env。
load_dotenv(Path(__file__).resolve().parent / ".env")  # noqa: E402

# 项目此前没有 basicConfig，只有 WARNING+ 靠 logging.lastResort 打到 stderr，
# agent/ 里的 INFO/DEBUG 全丢。开了它，评估节点的 DEBUG 才会出来。
# uvicorn 自己的 logger 带独立 handler，不受这行影响。
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

# 注：/chat 主链路默认已不再整段跑 assessment_graph（O3 改为分步 + 并行，
# 见 run_assessment_with_parallel）；assessment_graph 仍是回退路径（开关置 0）
# 与 tests/、编排层在用。
from agent.graph import (  # noqa: E402
    AssessmentFailed,
    assessment_graph,
    get_llm,
    get_model,
    run_assessment_speculative,
    run_assessment_with_parallel,
)
from agent.evaluation import (
    SCORE_DIMENSIONS,
    render_report_md,
    run_to_dict,
    score_sheet_rows,
    summarize_runs,
)
from agent.knowledge_graph import graph_payload, recommend_next
from agent.orchestrator import agent_graph
from agent.roles import build_role_system
from agent.scenarios import list_scenarios, normalize_scenario, quiz_requested
from agent.pretest import (
    build_pretest,
    merge_ability_evidence,
    score_attempt,
    weak_points_from,
)
from agent.personalize import (
    apply_profile,
    build_profile_brief,
    load_profile,
    preview_prompt,
    suggest_start_level,
)
from agent.prompts import OFF_TOPIC_REPLY, RAG_GROUNDING_RULES
from agent.profiler import (
    apply_turn,
    derive_level,
    get_or_create_profile,
    merge_weak_points,
    profile_to_dict,
)
from agent.rag.indexer import build_index, load_knowledge_index
from eval.report_api import configure_metrics_api, router as metrics_router
from agent.rag import classroom_store
from agent.rag.classroom_store import retrieve_classroom_docs
from agent.rag.kb_gap import log_kb_gap
from agent.rag.retriever import clear_bm25_cache, clear_parent_index_cache
from agent.rag.retriever import (
    format_knowledge_docs,
    format_pdf_docs,
    get_knowledge_context,
    retrieve_knowledge_docs,
    retrieve_pdf_docs,
    summarize_sources,
)
from agent.resource_agent import OutlineError, generate_outline, outline_to_dict
from agent.resource_gen import (
    ResourceGenError,
    build_quiz_from_bank,
    difficulty_label,
    generate_resource,
    list_resource_types,
    resolve_difficulty,
)
from agent.resource_pptx import build_pptx
from agent.state import ChatRequest, HistoryMessage, HINT_STRATEGIES, TutorState
from sqlalchemy import select

from auth import hash_password, password_problem, verify_password
from db.diagnostics import diagnose, empty_result_hint
from db.models import (
    AgentRun,
    Base,
    Classroom,
    ClassroomMember,
    LearningBehavior,
    StudentProfile,
    User,
)
from db.repository import (
    add_member,
    add_classroom_document,
    classroom_document_usage,
    create_classroom,
    create_user,
    find_classroom_document_by_hash,
    count_members_by_role,
    get_classroom_document,
    get_user_by_username,
    is_member,
    list_classroom_documents,
    list_classrooms_for_user,
    list_messages,
    list_users,
    get_classroom_scenario,
    get_classroom_use_tutor,
    get_messages_by_ids,
    list_agent_runs,
    load_history,
    load_last_state,
    mark_classroom_document_deleted,
    save_agent_run,
    save_message,
    set_password_hash,
    set_use_tutor,
)
from db.session import SessionLocal, db_available, get_db

MAX_UPLOAD_BYTES = max(
    1,
    int(os.getenv("MAX_UPLOAD_BYTES", str(10 * 1024 * 1024))),
)
MAX_DOCUMENT_SESSIONS = max(1, int(os.getenv("MAX_DOCUMENT_SESSIONS", "100")))
SESSION_TTL_SECONDS = max(1, int(os.getenv("SESSION_TTL_SECONDS", "3600")))

# 常驻八股知识库：构建一次落盘，之后所有请求共享
KNOWLEDGE_PERSIST_DIR = os.getenv(
    "KNOWLEDGE_PERSIST_DIR",
    str(Path(__file__).resolve().parent / "chroma_kb"),
)
KNOWLEDGE_COLLECTION = os.getenv("KNOWLEDGE_COLLECTION", "cs_basics")

# XB-23 课堂资料（方案 B）：配额与注入条数。
# 配额是 MVP 建议值（提案 §4.5），可用环境变量调，不写死在逻辑里。
CLASSROOM_MAX_FILES = max(1, int(os.getenv("CLASSROOM_MAX_FILES", "20")))
CLASSROOM_MAX_BYTES = max(1, int(os.getenv("CLASSROOM_MAX_BYTES", str(200 * 1024 * 1024))))
CLASSROOM_TOP_K = max(1, int(os.getenv("CLASSROOM_TOP_K", "3")))

# O3（2026-10-27）：/chat 是否让「理解评估」与三路检索并行。
# 默认开；置 0 即回到「评估链路整段跑完再检索」的旧顺序。
# 留这个开关不只是为了 A/B——并行化落在主链路上，出问题时需要一行回退，
# 而不是靠改代码重新发版。
CHAT_PARALLEL_ASSESS = os.getenv("CHAT_PARALLEL_ASSESS", "1").strip() != "0"

# O12（2026-10-28）：/chat 是否「投机执行」——不等抽 topic，先用上一轮
# topic 把评估与检索跑起来，抽 topic 判沿用就直接采用，判换题则作废重跑。
# 延迟收益与「盲缓存 topic」完全相同，但正确性零损失（详见 graph.py 的
# 文档串，以及 XB-31 里盲缓存被否决的实测数据）。
# ⚠ 两个前提：① build_parallel 必须只读且幂等（它会被调两次）
#            ② 未命中时被取消的那次评估调用是否计费，取决于厂商
# 不放心就置 0，退回 O3 的分步并行。
CHAT_SPECULATE_TOPIC = os.getenv("CHAT_SPECULATE_TOPIC", "1").strip() != "0"


def _profile_mastery(db, user_id: int, classroom_id: int) -> dict[str, float]:
    """取画像里的掌握度，用于组卷和图谱叠加。

    没有画像时返回空 dict 而不是 None：「未测」和「取不到」在这里是同一个
    下游语义，返回 None 会让调用方走进空指针分支，还不如让它自然退化。
    """
    row = (
        db.execute(
            select(StudentProfile).where(
                StudentProfile.user_id == user_id,
                StudentProfile.classroom_id == classroom_id,
            )
        )
        .scalars()
        .first()
    )
    return dict(row.knowledge_mastery or {}) if row is not None else {}


@lru_cache(maxsize=1)
def get_knowledge_store():
    """返回常驻知识库；还没构建过时返回 None，检索函数会兜底返回空串。

    失败必须降级而不是抛出：知识库是回答质量的增强手段，模型自带知识
    仍然能给出合格的八股答案。若在这里抛异常，模型一下载不到（网络/
    镜像抖动）就会让整个 /chat 变成 500——辅助功能把主功能拖垮了。
    用 lru_cache 所以只会在第一次请求时偿付代价，失败也只打印一次。
    """
    try:
        return load_knowledge_index(KNOWLEDGE_COLLECTION, KNOWLEDGE_PERSIST_DIR)
    except Exception as exc:
        print(f"[kb] 知识库加载失败，本次对话降级为不用 RAG：{exc}", flush=True)
        return None

# RAG_GROUNDING_RULES 已归口到 agent/prompts.py（XB-18，对齐 AGENT_PROTOCOL §5 Prompt-1：
# 提示词只写在 prompts.py / state.py 两处）。此处仅转引，保持 add_rag_grounding 的
# 引用方式不变；文本改动请去 prompts.py。

def reload_knowledge_store() -> dict:
    """丢弃进程内的知识库句柄与 BM25 语料缓存，并立即重新加载（XB-19）。

    **为什么需要它**：`scripts/update_kb.py` 做增量更新时不 drop collection，
    数据确实落盘了，但**已经打开过该库的进程持有旧内存快照**（实测：全新进程
    能检索到增量数据，同进程新实例不能）。所以增量之后不重启就看不到新内容。
    本函数让调用方不必重启服务即可生效。

    **为什么连 BM25 一起清**：BM25 语料是独立于向量库句柄的第二份状态，
    只换句柄不清它，混合召回会拿旧语料匹配新切片，且不报错（见
    `retriever.clear_bm25_cache` 的说明）。

    并发：清空到重载完成之间若有请求进来，会各自触发一次加载（重复付一次
    I/O，不产生错误）。当前是单 worker 常驻服务，不额外加锁，避免把简单的
    运维接口复杂化。

    Returns:
        {"reloaded": bool, "chunks": int|None, "chunks_before": int|None,
         "bm25_cleared": int}
        chunks 为 None 表示库加载失败——沿用既有降级策略（不用 RAG），不抛异常。
    """
    before = None
    try:
        old = get_knowledge_store()
        if old is not None:
            before = old._collection.count()
    except Exception:
        # 取旧切片数只是为了回报前后对比，失败不影响重载本身
        before = None

    get_knowledge_store.cache_clear()
    bm25_cleared = clear_bm25_cache()
    # parent 索引（相邻小节扩展用）同样是独立于句柄的第三份状态，理由同 BM25。
    clear_parent_index_cache()

    try:
        store = get_knowledge_store()  # 立即重新加载，顺带预热
    except Exception as exc:
        # 与 get_knowledge_store 同一套取舍：知识库是增强项，重载失败只降级，
        # 不能让一个运维端点把 /chat 拖成 500
        print(f"[kb] 知识库重载失败，降级为不用 RAG：{exc}", flush=True)
        store = None

    after = None
    if store is not None:
        try:
            after = store._collection.count()
        except Exception as exc:
            print(f"[kb] 重载后取切片数失败：{exc}", flush=True)

    print(
        f"[kb] 知识库已热重载：切片 {before} -> {after}，"
        f"清掉 BM25 缓存 {bm25_cleared} 个",
        flush=True,
    )
    return {
        "reloaded": store is not None,
        "chunks": after,
        "chunks_before": before,
        "bm25_cleared": bm25_cleared,
    }


limiter = Limiter(key_func=get_remote_address)


@dataclass
class DocumentSession:
    vectorstore: object
    created_at: float
    # 上传者身份。默认 student 以兼容历史构造；教师上传件带 teacher_id /
    # classroom_id，便于审计「这份备课素材是谁、为哪堂课传的」。
    audience: str = "student"
    teacher_id: int | None = None
    classroom_id: int | None = None


app = FastAPI(title="CS Tutor Agent")
sessions: dict[str, DocumentSession] = {}
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# 显式白名单（来自 ALLOWED_ORIGINS，覆盖 localhost / 公网域名等固定来源）。
# 另用 allow_origin_regex 兜底放行「私网 / 局域网」来源：同一局域网内其他电脑
# 跑前端连本机后端时，其来源 IP 千变万化，用正则免维护、且允许带凭据。
allowed_origins = json.loads(
    os.getenv("ALLOWED_ORIGINS", '["http://localhost:3000"]')
)

# 局域网点对点：本机回环 + 10.x / 172.16-31.x / 192.168.x.x，任意端口。
_PRIVATE_ORIGIN_RE = re.compile(
    r"^https?://("
    r"localhost|127\.0\.0\.1|"
    r"192\.168\.\d{1,3}\.\d{1,3}|"
    r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
    r"172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r")(:\d+)?$"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_origin_regex=_PRIVATE_ORIGIN_RE.pattern,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _run_db_migrations():
    """启动时幂等建表 + 补列。

    - create_all：只建「不存在的表」。M4 新增的 agent_runs 就靠这一步落地——
      不跑它的后果是编排留痕每次都撞「表不存在」，而且是静默失败。
    - ensure_new_columns：给已存在的表补列（messages: topic / hint_level /
      sources / agent_role；classrooms: is_private / scenario）。表在而列缺失
      create_all 不管，不补的后果是读出来 None、写入直接 Unknown column 500。

    两者都在每次启动兜底跑一次，避免「改了模型忘了跑 init_db」导致线上
    表结构和代码悄悄偏离。engine 未配置（纯不落库模式）或连接失败时
    安全跳过，不让启动失败。
    """
    try:
        from db.session import engine as _engine
        from db.models import Base
        from init_db import ensure_new_columns

        if _engine is not None:
            # 必须先 import 过模型类，create_all 才认得它们——db.models 已在
            # 顶部导入，这里再引一次只为拿 Base，避免漏注册。
            Base.metadata.create_all(_engine)
        ensure_new_columns()
    except Exception as exc:  # noqa: BLE001 - 迁移失败不能拖垮启动
        logger.warning("数据库迁移未执行（可能未配置 MySQL 或连接失败）：%s", exc)


def _delete_vectorstore(vectorstore: object) -> None:
    try:
        vectorstore.delete_collection()
    except Exception:
        # Session cleanup should not make an otherwise successful request fail.
        pass


async def _stash_upload(file: UploadFile) -> tuple[str, int, str]:
    """校验 PDF 并落到临时文件，返回 `(临时路径, 字节数, sha256)`。

    校验顺序：扩展名 → 大小 → 魔数（`%PDF-`）。大小是**边读边判**的，
    不先把整个文件吃进内存再看。哈希也在同一次读流里算完——课堂资料去重
    需要内容哈希，单独再读一遍文件是纯浪费。

    **调用方负责删除临时文件**（成功路径也一样）：这里不删，是因为
    `persist=classroom` 需要在返回前把文件拷进课堂原件目录。
    失败时这里自己清干净，不留垃圾。
    """
    filename = file.filename or ""
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    tmp_file_path = ""
    digest = hashlib.sha256()
    total_bytes = 0
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
            tmp_file_path = tmp_file.name
            header = b""

            while chunk := await file.read(1024 * 1024):
                total_bytes += len(chunk)
                if total_bytes > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            "PDF must be no larger than "
                            f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
                        ),
                    )
                digest.update(chunk)
                if len(header) < 1024:
                    header += chunk[: 1024 - len(header)]
                tmp_file.write(chunk)

        if b"%PDF-" not in header:
            raise HTTPException(
                status_code=400,
                detail="The uploaded file is not a valid PDF.",
            )
        return tmp_file_path, total_bytes, digest.hexdigest()
    except Exception:
        if tmp_file_path:
            try:
                os.unlink(tmp_file_path)
            except FileNotFoundError:
                pass
        raise
    finally:
        await file.close()


def _unlink_quietly(path: str | None) -> None:
    """删临时文件，文件不在就算了——清理失败不该改变请求结果。"""
    if not path:
        return
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


async def _index_upload(file: UploadFile, collection_name: str) -> object:
    """读取一份 PDF 并建成**内存**向量库，学生与教师会话级入口共用。

    返回的是纯内存库（`build_index` 不传 persist_directory），
    进程重启即消失，不会污染常驻知识库——这是「上传件不影响 L1 基线」的根因。
    长期生效的课堂资料走 `_index_classroom_upload`，不要在这里改成落盘。
    """
    tmp_path, _size, _digest = await _stash_upload(file)
    try:
        return await asyncio.to_thread(build_index, tmp_path, collection_name)
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail="The PDF could not be read or indexed.",
        ) from exc
    finally:
        _unlink_quietly(tmp_path)


async def _index_classroom_upload(
    file: UploadFile, classroom_id: int, teacher_id: int
) -> dict:
    """教师上传「长期生效的课堂资料」（XB-23 方案 B）。

    顺序刻意如此，每一步失败都必须把前一步的副作用清掉：
      校验/落临时文件 → 去重(409) → 配额(413) → 原件落盘 → 建派生索引(422)
      → 登记 DB → 删临时文件

    先查重再落盘：否则重复上传会在原件目录留一份孤儿文件。
    先落盘再建索引：索引失败还能用原件重建；反过来（先索引后落盘）则
    索引指向一个不存在的路径，重建时无处可读。
    """
    tmp_path, file_bytes, doc_hash = await _stash_upload(file)
    doc_id = uuid.uuid4().hex[:12]
    filename = file.filename or "document.pdf"
    try:
        with get_db() as db:
            if db is None:
                raise HTTPException(
                    status_code=503,
                    detail="Database is not configured. Fill MYSQL_* in backend/.env.",
                )
            # ① 同一课堂同一份内容只留一份。返回已有 doc_id 而不是报错，
            #    前端重复点击时是幂等的。
            existing = find_classroom_document_by_hash(db, classroom_id, doc_hash)
            if existing is not None:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": "This document already exists in the classroom.",
                        "doc_id": existing.id,
                        "filename": existing.filename,
                    },
                )
            # ② 配额：文件数与总字节双上限，防止单个课堂把磁盘吃满。
            count, used = classroom_document_usage(db, classroom_id)
            if count >= CLASSROOM_MAX_FILES or used + file_bytes > CLASSROOM_MAX_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=(
                        f"Classroom document quota exceeded: {count}/"
                        f"{CLASSROOM_MAX_FILES} files, {used + file_bytes}/"
                        f"{CLASSROOM_MAX_BYTES} bytes."
                    ),
                )

            # ③ 原件落盘（事实源）
            stored = await asyncio.to_thread(
                classroom_store.save_original, tmp_path, classroom_id, doc_id, filename
            )
            # ④ 派生索引
            try:
                store = await asyncio.to_thread(
                    classroom_store.index_classroom_document,
                    stored,
                    classroom_id,
                    doc_id,
                    filename,
                )
            except Exception as exc:
                classroom_store.remove_original(stored)
                raise HTTPException(
                    status_code=422,
                    detail="The PDF could not be read or indexed.",
                ) from exc
            # ④b 计量与「有没有正文」的判定。
            #     `count_chunks()` 数的是整个课堂 collection（一个课堂一个 collection，
            #     教第二份文件时会把上一份的片一起数进去），所以这里必须按 doc_id 数。
            #     实测后果（2026-10-26）：3 页空白 PDF 报 `chunk_count=188`。
            chunks, body_chunks = classroom_store.document_metrics(store, doc_id)
            #     抽不出正文的内容不该入库：扫描件 / 纯图片页只会产生一片空的
            #     front_matter，入库「成功」但永远检索不到——教师看到 200 就走人，
            #     学生问什么都命不中，属于最难发现的静默失败。宁可当场拒绝并说清原因。
            if body_chunks == 0:
                classroom_store.delete_document_chunks(classroom_id, doc_id)
                classroom_store.remove_original(stored)
                remaining, _used = classroom_document_usage(db, classroom_id)
                if remaining == 0:
                    classroom_store.purge_classroom(classroom_id)
                logger.warning(
                    "课堂资料无正文，已回滚｜课堂 %d 文件 %r 切片 %d",
                    classroom_id,
                    filename,
                    chunks,
                )
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "The PDF has no extractable text (possibly a scanned "
                        "document or images only). Upload a text-based PDF, or "
                        "run OCR on it first."
                    ),
                )
            # ⑤ 登记。DB 写失败要把原件与切片一并回滚，否则孤儿状态没人能发现。
            try:
                add_classroom_document(
                    db,
                    doc_id=doc_id,
                    classroom_id=classroom_id,
                    teacher_id=teacher_id,
                    # 存**原始文件名**（展示/审计用）；落盘路径在
                    # classroom_store 里已按 safe_filename 清洗过。
                    filename=filename[:255],
                    stored_path=str(stored),
                    doc_hash=doc_hash,
                    chunk_count=chunks,
                    file_bytes=file_bytes,
                )
            except Exception as exc:
                classroom_store.delete_document_chunks(classroom_id, doc_id)
                classroom_store.remove_original(stored)
                raise HTTPException(
                    status_code=500,
                    detail="Failed to register the classroom document.",
                ) from exc
    finally:
        _unlink_quietly(tmp_path)

    logger.info(
        "课堂资料入库｜课堂 %d 教师 %d 文档 %s 切片 %d 字节 %d",
        classroom_id,
        teacher_id,
        doc_id,
        chunks,
        file_bytes,
    )
    return {
        "doc_id": doc_id,
        "classroom_id": classroom_id,
        "filename": filename[:255],
        "chunk_count": chunks,
        "file_bytes": file_bytes,
        "persist": "classroom",
        "message": "Document indexed for this classroom.",
    }


def cleanup_expired_sessions(now: float | None = None) -> None:
    cutoff = (now if now is not None else time.monotonic()) - SESSION_TTL_SECONDS
    expired_ids = [
        session_id
        for session_id, session in sessions.items()
        if session.created_at <= cutoff
    ]
    for session_id in expired_ids:
        session = sessions.pop(session_id, None)
        if session is not None:
            _delete_vectorstore(session.vectorstore)


def evict_oldest_session_if_full() -> None:
    if len(sessions) < MAX_DOCUMENT_SESSIONS:
        return
    oldest_id = min(sessions, key=lambda key: sessions[key].created_at)
    session = sessions.pop(oldest_id)
    _delete_vectorstore(session.vectorstore)


def deserialize_history(
    history: List[HistoryMessage | dict],
) -> List[BaseMessage]:
    messages = []
    for item in history:
        if isinstance(item, HistoryMessage):
            role = item.role
            content = item.content
        else:
            role = item.get("role")
            content = item.get("content", "")

        if role == "user":
            messages.append(HumanMessage(content=content))
        elif role == "assistant":
            messages.append(AIMessage(content=content))
    return messages


def _classroom_of(body: ChatRequest) -> int | None:
    """本次请求是否要落库：只有传了 classroom_id 且数据库已配置才算。

    返回 classroom_id 或 None。旧前端只传 session_id 时返回 None，
    走原来的纯内存路径，行为完全不变。
    """
    return body.classroom_id if (body.classroom_id and SessionLocal) else None


def _persist_assistant(
    classroom_id: int | None,
    answer: str,
    topic: str = "",
    hint_level: int = 0,
    user_id: int | None = None,
    sources: list | None = None,
    agent_role: str | None = None,
) -> object | None:
    """把助手回复连同当时的教学状态落库。落库失败绝不能影响已经发出的回答。

    user_id 记的是「这条回复是给谁的」——不写的话下一次请求恢复状态时
    分不清这是谁的那条线，多人课堂会串台。
    sources 是本轮依据的检索资料（结构化 items，不含正文），刷新后前端仍能展示。
    agent_role 记「这条 AI 回复是哪个角色说的」，多 Agent 轮次里用于区分身份。

    返回落库后的 Message（或 None）：M4 要把这些 id 记进 agent_runs，
    评估报告才能按轮次回溯「这一轮每个 Agent 各说了什么」。
    """
    if classroom_id is None or not answer.strip():
        return None
    try:
        with get_db() as db:
            if db is not None:
                message = save_message(
                    db,
                    classroom_id,
                    user_id,
                    "assistant",
                    answer,
                    topic,
                    hint_level,
                    sources=sources,
                    agent_role=agent_role,
                )
                # id 要 flush 后才有；这里还在事务内，够用了。
                return message
    except Exception as exc:
        print(f"[db] 助手回复落库失败（忽略）：{exc}", flush=True)


# 只报一次的开关：一轮对话一次写库，跑一天可能失败几千次。每条都打会把真正
# 的信号淹掉，而排查只需要第一次的原因——后续同类失败看计数即可。
_RUNS_DDL_RETRIED = False
_RUNS_WARNED_NO_CLASSROOM = False


def _write_agent_run(payload: dict) -> tuple[bool, str]:
    """写一次留痕，返回（是否成功, 失败原因）。**不抛异常**。"""
    try:
        with get_db() as db:
            if db is None:
                return False, "数据库不可用（未配置 MYSQL_* 或连接失败）"
            if save_agent_run(db, **payload) is None:
                return False, "save_agent_run 返回 None"
        return True, ""
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _record_agent_run(
    classroom_id: int | None,
    user_id: int | None,
    *,
    scenario: str,
    topic: str,
    roles: list,
    degraded: bool,
    reason: str,
    duration_ms: int,
    message_ids: list,
    rag_hits: int = 0,
) -> None:
    """记一轮多智能体编排（M4）。留痕失败绝不能影响已经发出的回答。

    为什么必须记：编排层的降级（未知场景 / 协作被关 / 超上限）会把请求
    悄悄收敛成单 Agent，回答完全正常，唯一症状是「多场景没生效」。不落库
    就没有任何地方能回答「这轮到底派了几个 Agent、为什么」。
    同时它也是成果#4 评估报告的数据源（参与角色数 / 耗时 / 降级率）。

    **失败绝不能静默**：早先这里 print 一句就过，而 print 在 uvicorn 下会被
    访问日志冲掉，结果是「回答正常、/evaluation/* 全空、日志里什么都没有」。
    现在的处理顺序是：写 → 失败则补建表重试一次 → 仍失败则 logger.error
    并指明去 /evaluation/status 看诊断。
    """
    global _RUNS_DDL_RETRIED, _RUNS_WARNED_NO_CLASSROOM

    if classroom_id is None and not _RUNS_WARNED_NO_CLASSROOM:
        _RUNS_WARNED_NO_CLASSROOM = True
        logger.warning(
            "/chat 未带 classroom_id：本轮仍会记留痕（classroom_id 为 NULL），"
            "但发言正文不落库，评分表将没有原文可评。"
        )

    payload = dict(
        classroom_id=classroom_id,
        user_id=user_id,
        scenario=scenario,
        topic=topic,
        roles=roles,
        degraded=degraded,
        reason=reason,
        duration_ms=duration_ms,
        message_ids=message_ids,
        rag_hits=rag_hits,
    )

    ok, why = _write_agent_run(payload)
    if ok:
        return

    # 最常见的失败原因是 agent_runs 表不存在：漏跑 init_db.py，或 MySQL 比后端
    # 晚启动导致 startup 那次 create_all 扑空。症状正是「回答正常、留痕全丢」，
    # 所以这里补建一次再写。只试一次，避免每轮都发一条 DDL。
    if not _RUNS_DDL_RETRIED:
        _RUNS_DDL_RETRIED = True
        try:
            from db.session import engine

            if engine is not None:
                Base.metadata.create_all(engine, tables=[AgentRun.__table__])
                ok, why = _write_agent_run(payload)
                if ok:
                    logger.warning("agent_runs 表缺失，已自动补建并补记本轮留痕。")
                    return
        except Exception as exc:
            why = f"补建 agent_runs 也失败：{exc}"

    logger.error(
        "编排留痕写入失败，/evaluation/* 会缺少本轮数据：%s。"
        "排查先看 GET /evaluation/status 的 diagnosis，再跑 python init_db.py。",
        why,
    )


def _update_profile(
    classroom_id: int | None,
    user_id: int | None,
    topic: str,
    hint_level: int,
    misconception: str,
    resolved: bool,
    prev_topic: str,
    prev_hint_level: int,
) -> None:
    """一轮对话结束后沉淀画像。

    prev_topic / prev_hint_level 是「进入这一轮之前」的状态——DB 路径下
    已由 load_last_state 恢复，正好用来判断学生是换了知识点还是要求展开。
    画像只是副产品，出错一律记日志放过，不能把已经流出去的回答变成错误。
    """
    if classroom_id is None or user_id is None:
        return
    try:
        with get_db() as db:
            if db is not None:
                apply_turn(
                    db,
                    user_id,
                    classroom_id,
                    topic=topic,
                    hint_level=hint_level,
                    misconception=misconception,
                    resolved=resolved,
                    prev_topic=prev_topic,
                    prev_hint_level=prev_hint_level,
                )
    except Exception as exc:
        print(f"[db] 画像更新失败（忽略）：{exc}", flush=True)


# 2026-10-24 XB-14（用户决策）：空资料**不再**在回答里显式告知学生（XB-13 的
# RAG_NO_MATERIAL_NOTICE 注入已撤下——产品上不希望模型把「知识库未覆盖」说给学生听）。
# 改为旁路记录：/chat 检索到 kb_docs 为空且 topic 非 unknown 时，
# 经 agent.rag.kb_gap.log_kb_gap 写结构化 JSONL 事件，供后期聚合报表
# 定位知识库缺口（见 eval/kb_gap_report.py）。已知代价：oos 题回答为
# 模型自有知识且不声明（L3 空资料声明率门禁已同步移除，见 e2e_runner）。
def add_rag_grounding(prompt: str, rag_context: str) -> str:
    if not rag_context:
        # 空资料原样返回 prompt（2026-10-24 用户决策恢复旧行为）；
        # 缺口事件由 /chat 主链路记录，这里不做任何注入。
        return prompt
    return (
        f"{prompt}\n\n"
        f"{RAG_GROUNDING_RULES}\n\n"
        "知识库资料：\n"
        f"{rag_context}"
    )


def _compose_rag_context(
    pdf_docs: list,
    kb_docs: list,
    classroom_docs: list,
) -> tuple[str, list]:
    """把三路资料拼成注入文本 + 事件层来源清单。

    抽成纯函数是为了可测：`/chat` 那个函数太长，把「注入顺序」和
    「sources 三类 origin」这种口径逻辑埋在里面，改错了只能靠端到端冒烟发现。

    **顺序有语义**：上传件（学生自己带的）→ 教材（kb）→ 课堂资料。
    教材在课堂资料之前，是为了让模型优先把「权威教材」当骨架、课堂资料当
    补充；反过来会出现「教师讲义覆盖教材」的教学不一致（D2 的取舍）。
    """
    doc_context = format_pdf_docs(pdf_docs)
    kb_context = format_knowledge_docs(kb_docs)
    # 课堂资料是 PDF 切片，格式与上传件同构（page_number / line_start 都在）。
    classroom_context = format_pdf_docs(classroom_docs)
    rag_context = "\n\n---\n\n".join(
        x for x in (doc_context, kb_context, classroom_context) if x
    )
    source_items = (
        summarize_sources(pdf_docs, "upload")
        + summarize_sources(kb_docs, "kb")
        + summarize_sources(classroom_docs, "classroom")
    )
    return rag_context, source_items


# 「本轮没有 AI 发言」的提示文案（2026-11-02）。
# 只在一处定义：前端展示、SSE notice、日志都引它，避免三份措辞漂移。
NO_TUTOR_NOTICE = (
    "主讲助教已关闭，这一轮没有 AI 回复。想让出题助手出题，请 @出题助手。"
)


def _notice_stream(text: str) -> StreamingResponse:
    """只发一条 notice 就结束的 SSE（本轮没有 AI 发言）。

    保留 `sources` 首事件：契约里它是每条流的第一个事件，前端靠它区分
    「这轮确实没用资料」和「这条流没覆盖」（见 API_CONTRACT §3.1）。
    这里 items 为空正是事实——没有 Agent 发言，也就没有检索。
    """

    async def gen():
        yield "data: " + json.dumps({"type": "sources", "items": []}) + "\n\n"
        yield "data: " + json.dumps({"type": "notice", "content": text}) + "\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/chat")
@limiter.limit("10/minute")
async def chat(request: Request, body: ChatRequest):
    try:
        llm = get_llm(body.provider)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    await asyncio.to_thread(cleanup_expired_sessions)

    classroom_id = _classroom_of(body)
    history = deserialize_history(body.history)
    # T6：本轮用来个性化的画像。查不到就是 None（新学生 / 未落库），
    # 此时提示词与 T5 之前完全一致。
    profile: dict | None = None
    # M4：本轮协作场景。请求体显式指定优先（调试 / 演示用），否则等下面
    # 读到课堂后取其 scenario；两者都没有，由编排层回落到 v1。
    effective_scenario = body.scenario
    # 这个课堂配不配备主讲助教。只有读到课堂才更新，读不到就按「有」处理。
    classroom_use_tutor = True
    # 课堂资料是「本课堂成员可见」的内容，所以**只有成员校验真的通过**才去查。
    # 不能只看 `classroom_id is not None`：下面那段读库是 fail-open 的
    # （读失败只打日志、对话继续），一旦 DB 抖动，仅凭 id 就去查课堂库
    # 等于把别班资料喂给任何知道课堂 id 的人。内容侧一律 fail-closed。
    classroom_verified = False

    # 落在课堂里时：校验身份 → 恢复历史 → 恢复教学状态 → 存下本条提问。
    # 顺序有讲究：先读历史和状态再存本条，否则刚存的消息会被自己读回来、
    # 和新消息重复。
    if classroom_id is not None:
        try:
            with get_db() as db:
                if db is not None:
                    # T4.4：后端必须知道「谁」——不是这个课堂的成员一律拒绝
                    if not is_member(db, classroom_id, body.user_id):
                        raise HTTPException(
                            status_code=403,
                            detail=(
                                f"User {body.user_id} is not a member of "
                                f"classroom {classroom_id}."
                            ),
                        )
                    # 走到这里才算「确认是本课堂成员」，课堂资料检索的放行条件。
                    classroom_verified = True
                    # 上下文用「群聊」全量，状态用「这个学生」自己的。
                    # 两者刻意分开：
                    # - 上下文共享，同学之间才能接力追问（A 问了死锁，
                    #   B 接着问「那怎么预防」才接得上）；
                    # - 状态按学生隔离，否则 B 会继承 A 讲到第 3 层的深度，
                    #   个性化讲解就废了。
                    # load_history 的 user_id 就是「群聊 / 单独聊」开关：
                    # 不传 = 群聊（当前），传了 = 只看这个人的对话线。
                    db_history = load_history(db, classroom_id)
                    if db_history:
                        history = deserialize_history(db_history)
                    # 场景的默认值来自课堂本身：建房时选了「小组讨论」，
                    # 之后每轮都按它走，不需要前端每轮回传。
                    if effective_scenario is None:
                        effective_scenario = get_classroom_scenario(db, classroom_id)
                    classroom_use_tutor = get_classroom_use_tutor(db, classroom_id)
                    # 服务端是状态的唯一真相源：只要落了 classroom_id，
                    # 就不再信客户端回传的 topic / hint_level。
                    body.topic, body.hint_level = load_last_state(
                        db, classroom_id, body.user_id
                    )
                    save_message(db, classroom_id, body.user_id, "student", body.message)
                    profile = load_profile(db, body.user_id, classroom_id)
        except HTTPException:
            raise
        except Exception as exc:
            print(f"[db] 读取历史/落库用户消息失败（改用前端历史）：{exc}", flush=True)

    # 关掉主讲助教、这句话也没有 @出题助手：本轮注定没有 AI 发言，直接短路。
    # 为什么不在编排层拦就够了：评估节点会把「花生在吗」判成「超出知识库范围」
    # 并抢先播一条固定拒答，学生看到的仍然是「有东西在回我」——2026-11-02 用户
    # 反馈的正是这个观感。放在这里还顺带省掉一次评估 LLM 调用（1~3 秒）。
    # 学生消息在上面的 save_message 已经落库，聊天记录不会丢。
    if (
        classroom_id is not None
        and not classroom_use_tutor
        and not quiz_requested(body.message)
    ):
        return _notice_stream(NO_TUTOR_NOTICE)

    new_message = HumanMessage(content=body.message)

    initial_state: TutorState = {
        "messages": history + [new_message],
        "topic": body.topic,
        "hint_level": body.hint_level,
        "misconception": body.misconception,
        "resolved": body.resolved,
        "llm": llm,
        "topic_changed": False,
    }

    # O3（2026-10-27）：评估链路的两节点不再串行地挡在检索前面。
    #
    # 原链路是 `assessment_graph.ainvoke` 一次调用：抽 topic（~1.24s）→ 理解评估
    # （~2.08s），两段都跑完才开始检索。但理解评估只读 topic / hint_level /
    # messages，**不读任何检索产物**——它和检索之间没有数据依赖，串起来是白等。
    #
    # 现在改成：抽 topic → gather(理解评估, 三路检索)。
    # ⚠ 收益是 **min(理解评估, 检索)**，不是理解评估全程：并行后耗时由较长的
    #   一侧决定，较长那侧省不掉。实测见 docs/plans/PLANS.md §7.7。
    #
    # assessment_graph 保留（测试与编排层仍在用），新增的分步入口在
    # agent/graph.py:run_assessment_with_parallel，两者行为等价。
    session = sessions.get(body.session_id) if body.session_id else None
    # trace 记录本轮检索的每一步（候选数、是否拼接 topic、重排走了哪条分支、
    # 耗时），扩库后排查「检索质量退化」时不用再靠复现。
    kb_trace: dict = {}
    classroom_trace: dict = {}

    async def _retrieve_all(kb_topic: str) -> tuple[list, list, list]:
        """与「理解评估」并发的那一段：三路检索，互不依赖。

        两套（其实是三套）检索来源：① 学生本次上传的 PDF（会话级、内存、
        一小时过期）② 常驻八股知识库（跨请求共享、落盘）③ 本课堂长期资料。
        每路只检索一次：文本给模型看，结构化来源给事件层上报。
        分开串行调用会各自付一份重排开销，所以这里并发。
        """

        async def _no_pdf() -> list:
            return []

        async def _no_classroom() -> list:
            return []

        # 用评估节点刚抽出的 topic 参与检索：追问（"那怎么预防呢"）本身不含
        # 检索关键词，只拿原话去搜会跑偏。unknown 时不传，避免把 "unknown"
        # 这个词拼进查询语句反而污染召回。
        kb_coro = asyncio.to_thread(
            retrieve_knowledge_docs,
            get_knowledge_store(),
            body.message,
            topic="" if kb_topic == "unknown" else kb_topic,
            trace=kb_trace,
        )
        pdf_coro = (
            asyncio.to_thread(retrieve_pdf_docs, session.vectorstore, body.message)
            if session is not None
            else _no_pdf()
        )
        # 第三路的放行条件是 **成员校验通过**（classroom_verified），
        # 不是「带了 classroom_id」：上面那段读库失败时是 fail-open 的，
        # 仅凭 id 就查会把别班资料当上下文喂给模型。
        # retrieve_classroom_docs 内部吞掉所有异常返回空列表：课堂库是增强项，
        # 它坏了不该让 /chat 500。
        classroom_coro = (
            asyncio.to_thread(
                retrieve_classroom_docs,
                classroom_id,
                body.message,
                CLASSROOM_TOP_K,
                classroom_trace,
            )
            if classroom_verified
            else _no_classroom()
        )
        kb_docs, pdf_docs, classroom_docs = await asyncio.gather(
            kb_coro, pdf_coro, classroom_coro
        )
        return kb_docs, pdf_docs, classroom_docs

    if CHAT_PARALLEL_ASSESS:
        # O12 只是把「等 E 出结果」这一步和 A/R 重叠起来；E 仍是唯一裁决者。
        _runner = (
            run_assessment_speculative
            if CHAT_SPECULATE_TOPIC
            else run_assessment_with_parallel
        )
        try:
            assessment_state, (kb_docs, pdf_docs, classroom_docs) = (
                await _runner(initial_state, _retrieve_all)
            )
        except AssessmentFailed as exc:
            # 只把「理解评估」的失败当模型不可用；检索失败照旧往上抛（500），
            # 并行化不能把两类错误混成一种，否则 detail 会误导排查方向。
            raise HTTPException(
                status_code=502,
                detail="The configured language model could not assess the message.",
            ) from exc
    else:
        # 回退路径（CHAT_PARALLEL_ASSESS=0）：与 2026-10-27 之前完全一致——
        # 评估整段跑完，再串行检索。
        try:
            assessment_state = await assessment_graph.ainvoke(initial_state)
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail="The configured language model could not assess the message.",
            ) from exc
        kb_docs, pdf_docs, classroom_docs = await _retrieve_all(
            assessment_state["topic"]
        )
    rag_context, source_items = _compose_rag_context(
        pdf_docs, kb_docs, classroom_docs
    )

    # 本轮到底注入了哪几片资料。这是 D11「检索静默失败不可观测」的解法：
    # 检索全错时模型会用自有知识答得很好，从回答上完全看不出来，
    # 只有把「系统喂了它什么」单独上报才能发现。
    logger.info(
        "本轮注入资料 %d 条（知识库 %d / 上传 %d / 课堂 %d）｜检索 query=%r "
        "拼topic=%s 路由=%s%s %dms→%d 篇 候选 %d+%d→%d 注入 %d 重排=%s %dms｜"
        "课堂库 %s top_distance=%s 门限=%s",
        len(source_items),
        len(kb_docs),
        len(pdf_docs),
        len(classroom_docs),
        kb_trace.get("query", ""),
        kb_trace.get("used_topic"),
        kb_trace.get("router", "?"),
        # 路由生效时把命中的文档名一并打出来：只报「成功收窄到 3 篇」看不出
        # 收窄到了谁，而判错文档正是这一层唯一的失败模式。
        ("(" + "、".join(os.path.basename(s) for s in kb_trace.get("router_docs") or []) + ")")
        if kb_trace.get("router_applied")
        else "",
        kb_trace.get("router_ms", 0),
        len(kb_trace.get("router_docs") or []),
        kb_trace.get("coarse_k", 0),
        kb_trace.get("bm25_k", 0),
        kb_trace.get("fused_k", 0),
        kb_trace.get("final_k", 0),
        kb_trace.get("rerank", "?"),
        kb_trace.get("rerank_ms", 0),
        classroom_trace.get("classroom", "skipped"),
        classroom_trace.get("classroom_top_distance"),
        classroom_trace.get("classroom_max_distance"),
    )

    # 降级必须可见：重排加载失败/报错时检索会静默退回向量排序，回答看上去仍然正常。
    # 用 WARNING 是为了不依赖日志配置——backend 默认没配 logging，
    # 只有 WARNING 及以上会被 Python 的 lastResort 打到 stderr（见 D2）。
    if kb_trace.get("degraded") or kb_trace.get("rerank") in {
        "unavailable",
        "error",
        "count_mismatch",
    }:
        logger.warning(
            "检索降级 degraded=%r 重排=%r query=%r",
            kb_trace.get("degraded"),
            kb_trace.get("rerank"),
            kb_trace.get("query"),
        )

    # XB-14（2026-10-24 用户决策）：知识库片段为空时**不在回答里告知学生**，
    # 改为旁路记录缺口事件（JSONL，agent/rag/kb_gap.py），供后期聚合报表
    # 定位知识库缺失的知识点（报表 CLI：python -m eval.kb_gap_report）。
    # 条件只看 kb_docs 为空：**不能**用「topic 非 unknown」过滤——实测
    # oos 题的评估节点常抽不出已知 topic（返回 unknown，拼topic=False），
    # 恰好是要抓的缺口场景，按 topic 过滤会把它们全漏掉。代价是纯寒暄
    # 也会记进来，聚合报表落「（无 topic）」组、样例提问可人工辨认。
    # 写入失败由 log_kb_gap 内部吞掉（返回 False），绝不影响答题主链路。
    if not kb_docs:
        log_kb_gap(
            {
                "query": body.message,
                "query_used": kb_trace.get("query", ""),
                "topic": (
                    assessment_state["topic"]
                    if assessment_state["topic"] != "unknown"
                    else ""
                ),
                "used_topic": kb_trace.get("used_topic"),
                "top_score": kb_trace.get("relevance_top_score"),
                "floor": kb_trace.get("relevance_floor"),
                "filtered": kb_trace.get("relevance_filtered"),
                "user_id": body.user_id,
                "classroom_id": classroom_id,
            }
        )

    async def off_topic_stream():
        """越界 / 无知识点共用的固定文案流（2026-10-31）。

        两种情况共用一条文案：① 本轮判定越界（off_topic）② 抽不出知识点且无上传件。
        措辞完全一致是刻意的——要求「不因问题类型不同而改变」，所以寒暄不再单独
        配一套招呼词。

        **这里不调用 LLM**。越界的正确行为是「不回答这个问题本身」，文案由服务端
        直接下发：既省一次模型往返，也杜绝模型自作主张把诗写出来——正是 2026-10-31
        前端截图里那次事故的行为（回复下方还标着「依据：模型自有知识」）。
        """
        try:
            # 与寒暄分支口径一致：发空数组而不是不发。
            # 「收到空的 sources」＝明确没有资料，「没收到」＝这条流没覆盖。
            yield (
                "data: "
                f"{json.dumps({'type': 'sources', 'items': []})}\n\n"
            )
            yield (
                "data: "
                f"{json.dumps({'type': 'token', 'content': OFF_TOPIC_REPLY})}\n\n"
            )
            _persist_assistant(
                classroom_id, OFF_TOPIC_REPLY, user_id=body.user_id,
                sources=[], agent_role=body.agent_role,
            )
            # 拒答不推 state：本轮没有知识点可推，发了会把上一轮真实的
            # topic / 深度清掉（理由同下：前端是「收到才更新」）。
            logger.info(
                "本轮拒答（超出知识库范围），不推 state 事件 message=%r",
                body.message[:100],
            )
            yield "data: [DONE]\n\n"
        except Exception:
            yield (
                "data: "
                f"{json.dumps({'type': 'error', 'content': 'Failed to send the reply.'})}\n\n"
            )

    # 越界拒答（2026-10-31）：
    # 判据是「本轮消息有明确诉求，但不属于计算机 / 编程领域」，与 topic 无关。
    #
    # 必须放在 unknown 分支**之前**：学生正讲着某个知识点、突然跳到无关话题时，
    # topic 仍是旧知识点（拒答刻意不清状态，下一轮回到正题还能接上），
    # 只有 off_topic 这个信号位能反映「本轮越界」。放在后面会被 unknown 分支挡住。
    if assessment_state.get("off_topic"):
        return StreamingResponse(off_topic_stream(), media_type="text/event-stream")

    if assessment_state["topic"] == "unknown":
        # 纯寒暄不该拿知识库去回答，只有学生上传过文档时才依据文档答。
        # 判据必须用 pdf_docs（本函数内 1019 行解包出来的）：原来写成
        # doc_context，而它只是 _compose_rag_context() 内部的局部变量，
        # 这里取不到 —— 2026-10-30 容器实测「帮我写一首关于秋天的诗」
        # 因此必 500（NameError）。只有 unknown 分支会走到，常规八股题的
        # 冒烟测不出来，所以是靠容器里打真实请求才暴露的。
        # 守护用例：tests/test_chat_unknown_topic.py
        if pdf_docs:

            async def doc_stream():
                try:
                    yield (
                        "data: "
                        f"{json.dumps({'type': 'sources', 'items': source_items})}\n\n"
                    )
                    answer_parts: list[str] = []
                    prompt = add_rag_grounding(
                        (
                            "你是计算机八股辅导助手。依据下面给出的知识库资料，"
                            "简洁、准确地回答学生的问题。\n\n"
                            f"学生提问：\n{body.message}"
                        ),
                        rag_context,
                    )

                    async for chunk in llm.astream([HumanMessage(content=prompt)]):
                        token = chunk.content
                        if token:
                            answer_parts.append(str(token))
                            yield (
                                "data: "
                                f"{json.dumps({'type': 'token', 'content': token})}\n\n"
                            )
                    _persist_assistant(
                        classroom_id, "".join(answer_parts), user_id=body.user_id,
                        sources=source_items, agent_role=body.agent_role,
                    )
                    # 没识别出知识点，就没有教学状态可推。以前这里伪造一个全零 state，
                    # 后果有两个：前端照单全收，把上一轮真实的 topic / 深度清掉
                    # （下一轮又原样回传，污染状态机）；而且它缺 personalized 字段，
                    # 和真 state 长得几乎一样，排查时根本分不出来。
                    # 不发最干净：前端是「收到才更新」，不发就保留上一轮状态。
                    logger.info(
                        "未识别知识点，走文档降级回答，不推 state 事件 message=%r",
                        body.message[:100],
                    )
                    yield "data: [DONE]\n\n"
                except Exception:
                    yield (
                        "data: "
                        f"{json.dumps({'type': 'error', 'content': 'The language model request failed.'})}\n\n"
                    )

            return StreamingResponse(doc_stream(), media_type="text/event-stream")

        # 抽不出知识点、又没有上传件可依据：与越界共用同一条固定文案。
        # 原来这里会让 LLM 现编一段招呼词（"用中文简短打个招呼…"），两个问题：
        # ① 白跑一次模型往返；② 模型不受控，实测就写过"秋日／风过疏林叶渐黄"
        #    这种完整的诗——越界提问被变相回答了。现在一律走固定文案。
        return StreamingResponse(off_topic_stream(), media_type="text/event-stream")

    async def event_stream():
        try:
            # sources 放在 token 之前：前端可以先把「本轮依据哪些资料」渲染出来，
            # 不必等回答结束。未知事件类型在前端是静默忽略的，老前端不会崩。
            yield (
                "data: "
                f"{json.dumps({'type': 'sources', 'items': source_items})}\n\n"
            )
            # T6：换知识点时，画像可以把起步深度从「一句话结论」抬到
            # 「标准答案」——但只在这个知识点历史上真掌握过时才抬。
            # 上限取评估节点给的值：画像不能反过来压过学生现场听不懂的证据。
            hint_level = assessment_state["hint_level"]
            if assessment_state.get("topic_changed"):
                hint_level = max(
                    hint_level,
                    suggest_start_level(profile, assessment_state["topic"]),
                )
            strategy = HINT_STRATEGIES[hint_level]
            misconception_note = (
                "学生目前的具体理解偏差是："
                f"{assessment_state['misconception']}"
                "。讲解时优先纠正这一点。"
                if assessment_state["misconception"]
                else "目前还不知道学生的具体理解偏差，按常规讲法讲解。"
            )

            # M4：经 langgraph 编排决定「本轮由哪些角色 Agent 依次发言」。
            # 编排本身零 LLM（主管用规则），只产出计划；LLM 调用在下面的循环里
            # 按计划逐个执行。降级（未知场景 / 协作关闭 / 超上限）不会让请求失败，
            # 只会收敛成单 Agent，并把原因记进 agent_runs。
            plan_state = await agent_graph.ainvoke({
                "scenario": effective_scenario,
                "agent_role": body.agent_role,
                # 学生 @ 出题助手 → 本轮只出题；课堂关掉主讲助教且没 @ →
                # 空计划（本轮没有 AI 发言，不再由出题助手代答）。
                # @ 的判定只看原话：出题助手是「点名才说话」的角色。
                "quiz_only": quiz_requested(body.message),
                "use_tutor": classroom_use_tutor,
                "topic": assessment_state["topic"],
                "hint_level": hint_level,
                "strategy": strategy,
                "misconception_note": misconception_note,
                "resolved": assessment_state["resolved"],
            })
            steps = plan_state["steps"]
            if plan_state.get("degraded"):
                # 降级必须可见：编排层静默退回单 Agent 时，表现是「多场景没生效」
                # 但回答完全正常，不记日志就只能靠猜。
                logger.info(
                    "编排降级 scenario=%r 实际角色=%s 原因=%s",
                    plan_state.get("scenario"),
                    plan_state.get("roles"),
                    plan_state.get("reason"),
                )

            run_started = time.perf_counter()

            # 空计划（2026-11-02）：课堂关掉了主讲助教，学生这句话也没 @ 出题助手。
            # 本轮刻意不派任何 Agent —— 助教不在了，问题就不该被出题助手代答。
            # 必须显式发一条 notice：否则前端只知道「没收到 token」，
            # 表现是消息发出去石沉大海，学生会当成系统坏了。
            if not steps:
                yield (
                    "data: "
                    f"{json.dumps({'type': 'notice', 'content': NO_TUTOR_NOTICE})}\n\n"
                )
                _record_agent_run(
                    classroom_id,
                    body.user_id,
                    scenario=plan_state.get("scenario", "v1"),
                    topic=assessment_state["topic"],
                    roles=[],
                    degraded=False,
                    reason=plan_state.get("reason", ""),
                    duration_ms=int((time.perf_counter() - run_started) * 1000),
                    message_ids=[],
                    rag_hits=len(source_items or []),
                )
                yield "data: [DONE]\n\n"
                return

            saved_ids: list[int] = []
            personalized = False
            # 前面 Agent 说过的原话。这是「协作」与「并联各答各的」的分界线：
            # 没有它，第二个 Agent 只看到学生的问题，会重复第一个 Agent 的内容。
            prior_speeches: list[dict] = []

            for step in steps:
                role = step["role"]
                system_content = build_role_system(
                    role,
                    topic=assessment_state["topic"],
                    strategy=strategy,
                    misconception_note=misconception_note,
                    resolved=assessment_state["resolved"],
                )

                # 画像段落接在规则之后：规则是硬约束，画像是讲法偏好，
                # 顺序反了会让模型觉得「学生基础差」比「用中文回答」更重要。
                system_content, step_personalized = apply_profile(
                    system_content, profile, assessment_state["topic"]
                )
                personalized = personalized or step_personalized

                system_content = add_rag_grounding(system_content, rag_context)

                if step["handoff"]:
                    system_content += (
                        f"\n\n【本轮协作要求】\n{step['handoff']}"
                    )
                if prior_speeches:
                    # 前文本身是模型的自由输出，拼进提示词前要钉住边界，
                    # 否则模型可能把上一位的话当成自己的指令继续执行。
                    transcript = "\n\n".join(
                        f"{p['label']}：{p['content']}" for p in prior_speeches
                    )
                    system_content += (
                        "\n\n【本轮前面已有的发言（不要重复，直接接着说）】\n"
                        f"{transcript}"
                    )

                # 角色切换事件放在该角色的 token 之前：前端据此新开一个气泡。
                # 未知事件类型在前端是静默忽略的，老前端只会看到一串连续 token，
                # 不会崩——这是 SSE 契约向后兼容的关键。
                yield (
                    "data: "
                    f"{json.dumps({'type': 'agent', 'role': role, 'label': step['label'], 'stance': step['stance'], 'index': step['index'], 'total': len(steps)})}\n\n"
                )

                if body.provider == "gemini":
                    msgs = assessment_state["messages"]
                    last_msg = msgs[-1]
                    messages = msgs[:-1] + [
                        HumanMessage(
                            content=(
                                system_content
                                + "\n\nStudent message: "
                                + last_msg.content
                            )
                        )
                    ]
                else:
                    messages = [
                        SystemMessage(content=system_content),
                        *assessment_state["messages"],
                    ]

                answer_parts: list[str] = []
                async for chunk in llm.astream(messages):
                    token = chunk.content
                    if token:
                        answer_parts.append(str(token))
                        yield (
                            "data: "
                            f"{json.dumps({'type': 'token', 'content': token})}\n\n"
                        )

                speech = "".join(answer_parts)
                # 每个 Agent 的发言各落一行：身份用 agent_role 区分，
                # 前端按角色渲染不同气泡，增量轮询 ?after= 也照常只补新增。
                # 不另建 agent_messages 表——那等于把同一份文本存两遍。
                saved = _persist_assistant(
                    classroom_id,
                    speech,
                    assessment_state["topic"],
                    hint_level,
                    body.user_id,
                    source_items,
                    agent_role=role,
                )
                if saved is not None and saved.id is not None:
                    saved_ids.append(saved.id)
                prior_speeches.append({"label": step["label"], "content": speech})

            _record_agent_run(
                classroom_id,
                body.user_id,
                scenario=plan_state.get("scenario", "v1"),
                topic=assessment_state["topic"],
                roles=plan_state.get("roles", []),
                degraded=bool(plan_state.get("degraded")),
                reason=plan_state.get("reason", ""),
                duration_ms=int((time.perf_counter() - run_started) * 1000),
                message_ids=saved_ids,
                rag_hits=len(source_items or []),
            )
            # 画像沉淀必须在「落库回答」之后：它依赖本轮最终的
            # topic / hint_level / resolved，早一步读到的还是旧状态。
            _update_profile(
                classroom_id,
                body.user_id,
                topic=assessment_state["topic"],
                hint_level=hint_level,
                misconception=assessment_state["misconception"],
                resolved=assessment_state["resolved"],
                prev_topic=body.topic,
                prev_hint_level=body.hint_level,
            )
            yield (
                "data: "
                f"{json.dumps({'type': 'state', 'topic': assessment_state['topic'], 'hint_level': hint_level, 'misconception': assessment_state['misconception'], 'resolved': assessment_state['resolved'], 'personalized': personalized})}\n\n"
            )
            yield "data: [DONE]\n\n"
        except Exception:
            yield (
                "data: "
                f"{json.dumps({'type': 'error', 'content': 'The tutor could not complete this response.'})}\n\n"
            )

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/health")
def health():
    provider = os.getenv("LLM_PROVIDER", "groq")
    if SessionLocal is None:
        db_state = "disabled"
    else:
        db_state = "up" if db_available() else "down"
    return {
        "status": "ok",
        "provider": provider,
        "model": get_model(provider),
        "database": db_state,
    }


# --- XB-20：管理端点鉴权（/admin/reload-kb）--------------------------------
# 背景：热重载会触发一次全库重新加载，是重操作。此前完全没鉴权，只靠
# 「服务监听 127.0.0.1」这道网络位置防线；一旦 --host 改成 0.0.0.0，
# 任何人都能随手触发全库重载。
#
# 两层闸门（默认即安全，不需要记得配什么）：
#   1. 未配置 ADMIN_RELOAD_TOKEN：只放行 loopback（本机）来源，其余 403。
#      —— 即使服务被暴露到公网，外网请求也进不来。
#   2. 配置了 ADMIN_RELOAD_TOKEN：必须带 X-Admin-Token 头且比对通过，否则 401。
#      此时不看来源（便于远程运维）；本机调用同样要带，行为一致才好排查。
#
# ⚠ 不信任 X-Forwarded-For：那个头可以任意伪造，用它判来源等于没鉴权。
#   注意反向代理场景：此时 request.client.host 是代理地址，若代理跑在本机
#   会被判成 loopback 而放行 —— **那种部署必须配置 ADMIN_RELOAD_TOKEN**。
ADMIN_TOKEN_ENV = "ADMIN_RELOAD_TOKEN"
_ADMIN_TOKEN_HEADER = "x-admin-token"


def _client_ip(request) -> str:
    """取 TCP 对端地址（不读 X-Forwarded-For，那个头可伪造）。"""
    client = getattr(request, "client", None)
    return (getattr(client, "host", "") or "") if client else ""


def _is_loopback_client(host: str) -> bool:
    """是否本机来源。`::ffff:127.0.0.1` 这类 IPv4-mapped 要先映射再判，
    否则 `ipaddress` 的 `is_loopback` 会返回 False，把本机请求误判成外部。"""
    if not host:
        return False
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    mapped = getattr(addr, "ipv4_mapped", None)
    if mapped is not None:
        addr = mapped
    return bool(addr.is_loopback)


def _admin_gate(request) -> tuple[str, int]:
    """管理端点的放行判断：返回 `("", 0)` 表示放行，否则 `(拒绝原因, HTTP 状态码)`。

    抽成纯函数是为了能直接单测——鉴权逻辑不能只靠「手动 curl 试一下」。
    原因串只说缺什么、不回显 token 本身，免得凭证泄进日志和响应体。
    """
    expected = os.getenv(ADMIN_TOKEN_ENV, "").strip()
    if expected:
        provided = (request.headers.get(_ADMIN_TOKEN_HEADER) or "").strip()
        if not provided:
            return "缺少 X-Admin-Token 头", 401
        if not secrets.compare_digest(provided, expected):
            return "管理令牌不正确", 401
        return "", 0
    if not _is_loopback_client(_client_ip(request)):
        return "未配置 %s，管理端点只放行本机（loopback）来源" % ADMIN_TOKEN_ENV, 403
    return "", 0


@app.post("/admin/reload-kb")
@limiter.limit("10/minute")
def admin_reload_kb(request: Request):
    """热重载常驻知识库（XB-19）：`scripts/update_kb.py` 增量更新后调一次，
    免去重启服务。

    返回 `chunks_before` / `chunks` 供调用方确认新数据确实被进程看到了——
    增量后若两者相等，说明磁盘上的变更没进到库里，该去查 update_kb 而不是
    怀疑热重载。

    鉴权（XB-20）：默认只放行本机来源；配置了 `ADMIN_RELOAD_TOKEN` 后改为
    校验 `X-Admin-Token` 头。被拒绝时记 WARNING 日志（含来源 IP，不含令牌）。

    用法：
        curl -X POST http://127.0.0.1:8000/admin/reload-kb
        # 配了令牌之后：
        curl -X POST http://127.0.0.1:8000/admin/reload-kb \\
             -H "X-Admin-Token: $ADMIN_RELOAD_TOKEN"
    """
    reason, status = _admin_gate(request)
    if reason:
        logger.warning(
            "管理端点拒绝：%s（来源 %s）", reason, _client_ip(request) or "未知"
        )
        raise HTTPException(status_code=status, detail=reason)
    result = reload_knowledge_store()
    result["status"] = "ok" if result["reloaded"] else "kb-unavailable"
    return result


# --- T3：用户 / 课堂 / 成员 / 聊天记录 的最小 HTTP 端点 -------------------
# 只覆盖 T3.6 验收要求的四项能力，成员身份校验等语义留给 T4。


class CreateUserRequest(BaseModel):
    """注册：用户名 + 密码 + 身份。

    id 由数据库自增分配，前端不能指定——让调用方传 id 等于把主键交给
    客户端，重放/试探都很容易。注册成功后把分配的 id 回显给用户。
    """

    username: str = Field(..., min_length=1, max_length=64)
    # 只卡长度上限，复杂度规则统一由 auth.password_problem 判定，
    # 避免「这里说 6 位、那里说 8 位」两套文案。
    password: str = Field(..., max_length=128)
    role: Literal["student", "teacher"] = "student"


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., max_length=128)


class UpdateClassroomRequest(BaseModel):
    """课堂设置的增量更新。当前只有「主讲助教」一个开关（随开随关）。"""

    use_tutor: bool | None = None


class CreateClassroomRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    subject: str = Field(default="cs", max_length=64)
    description: str | None = None
    # M1 聊天室：是否为「单人私聊」房间（前端当 DM 渲染）。
    is_private: bool = False
    # 创建时一次性把参与者加为成员，省去私聊建房间后再逐个 add_member。
    members: List[int] | None = None
    teacher_members: List[int] | None = None
    # 这个房间跑哪种协作形态（v1 / group）。
    # 非法值不报 422，由 normalize_scenario 回落到 v1——建房时选错场景
    # 不该让整个请求失败，回落即可，且前端能从返回的 scenario 看出被改了。
    scenario: str = "v1"
    # 是否配备主讲助教（2026-11-01）。关掉后整轮问答交给出题助手，
    # 课堂变成纯练习形态；默认开，与存量课堂行为一致。
    # 2026-11-02 起建房界面不再让教师选，一律 true；保留字段是为了让
    # 存量调用方（脚本/测试）仍能显式指定。
    use_tutor: bool = True
    # 1v1 指定学生（2026-11-02）。给了就只有这位学生能进这个教室；
    # 不给则退回「谁先进谁占位」的老行为。小组讨论课堂忽略该字段。
    target_student_id: int | None = None


class AddMemberRequest(BaseModel):
    user_id: int
    role: Literal["student", "teacher"] = "student"


class PostMessageRequest(BaseModel):
    """群聊发消息（人类成员发言，不触发 AI）。

    role 不在此传入——发言者身份由其在房间内的成员角色决定，防止前端
    伪造角色（比如学生把自己发言渲染成「教师」）。
    """

    user_id: int
    content: str = Field(..., min_length=1, max_length=4000)


def _require_db():
    if SessionLocal is None:
        raise HTTPException(
            status_code=503,
            detail="Database is not configured. Fill MYSQL_* in backend/.env.",
        )


def _user_payload(user: User) -> dict:
    """用户对外字段白名单。

    刻意不返回 password_hash：接口层唯一的口令防线就是「根本不吐出去」，
    靠前端小心是防不住的（抓包就能看到）。
    """
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


@app.post("/users", status_code=201)
def api_create_user(body: CreateUserRequest):
    """注册（2026-11-02 起必须设用户名 + 密码）。

    与旧行为的一处关键差别：**重名不再幂等返回已有账号，而是 409**。
    口令上线后，若还按昵称复用账号，任何人输入「张三」就能接管张三的
    课堂、画像和历史对话——这已经不是便利性问题了。

    id 由数据库自增分配，注册结果原样回显给前端展示。
    """
    _require_db()
    username = body.username.strip()
    if not username:
        raise HTTPException(status_code=422, detail="用户名不能为空。")
    problem = password_problem(body.password)
    if problem:
        raise HTTPException(status_code=422, detail=problem)

    with get_db() as db:
        if get_user_by_username(db, username) is not None:
            raise HTTPException(
                status_code=409,
                detail="这个用户名已经被注册了。换一个用户名，或用密码直接登录。",
            )
        user = create_user(db, username, body.role, hash_password(body.password))
        result = _user_payload(user)
    return result


@app.post("/auth/login")
def api_login(body: LoginRequest):
    """登录：用户名 + 密码，返回账号 id（前端据此恢复身份）。

    两条不能省的语义：
    1. 用户不存在与密码错误**返回同一句**「用户名或密码不正确」——分开说
       等于给了一个用户名枚举接口。
    2. 存量账号（password_hash 为 NULL）首次登录时把本次口令绑定上去。
       这样口令功能上线不用逼所有人重新注册，代价是「第一个登录的人
       认领这个账号」——对内网教学场景可接受，已在 HANDOFF 里注明。
    """
    _require_db()
    username = body.username.strip()
    if not username or not body.password:
        raise HTTPException(status_code=422, detail="用户名和密码都要填。")

    with get_db() as db:
        user = get_user_by_username(db, username)
        if user is None:
            raise HTTPException(status_code=401, detail="用户名或密码不正确。")

        bound = False
        if user.password_hash is None:
            # 老账号：口令功能上线前建的，没有密码可比。把这次输入绑定为
            # 它的密码，下次就必须用同一个口令登录。
            set_password_hash(db, user, hash_password(body.password))
            bound = True
        elif not verify_password(body.password, user.password_hash):
            raise HTTPException(status_code=401, detail="用户名或密码不正确。")

        result = _user_payload(user)
        result["password_bound"] = bound
    return result


@app.get("/users")
def api_list_users(
    role: Literal["student", "teacher"] | None = None,
    limit: int = 200,
):
    """用户列表（教师建房时挑「指定学生」的数据源）。

    只回 id / username / role：前端需要的就这三样，多给的每一列都是
    将来可能被误用的攻击面。
    """
    _require_db()
    with get_db() as db:
        rows = list_users(db, role, limit)
        items = [_user_payload(u) for u in rows]
    return {"count": len(items), "items": items}


@app.get("/users/{user_id}")
def api_get_user(user_id: int):
    """按 id 查用户（固定 id 的落地：登录后可用它核对身份）。"""
    _require_db()
    with get_db() as db:
        user = db.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail=f"User {user_id} not found.")
        result = _user_payload(user)
    return result


@app.post("/classrooms", status_code=201)
def api_create_classroom(body: CreateClassroomRequest):
    _require_db()
    with get_db() as db:
        for uid in (body.members or []) + (body.teacher_members or []):
            if db.get(User, uid) is None:
                raise HTTPException(
                    status_code=404, detail=f"User {uid} not found."
                )
        # 1v1 指定学生：必须真实存在，且确实是学生。允许把教师指定成
        # 「学生」会把 1v1 的人数闸门绕成教师自问自答，直接挡掉。
        if body.target_student_id is not None:
            target = db.get(User, body.target_student_id)
            if target is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"指定的学生（id={body.target_student_id}）不存在。",
                )
            if target.role != "student":
                raise HTTPException(
                    status_code=422,
                    detail="指定的账号不是学生，不能作为 1v1 教室的学生。",
                )
        room = create_classroom(
            db,
            body.name,
            body.subject,
            body.description,
            is_private=body.is_private,
            members=body.members,
            teacher_members=body.teacher_members,
            scenario=normalize_scenario(body.scenario),
            use_tutor=body.use_tutor,
            target_student_id=body.target_student_id,
        )
        result = {
            "id": room.id,
            "name": room.name,
            "subject": room.subject,
            "description": room.description,
            "is_private": bool(room.is_private),
            "scenario": room.scenario,
            "use_tutor": bool(room.use_tutor),
            "target_student_id": room.target_student_id,
            "target_student_username": (
                db.get(User, room.target_student_id).username
                if room.target_student_id
                else None
            ),
        }
    return result


@app.get("/scenarios")
def api_list_scenarios():
    """M4：列出可用协作场景（前端建房时的下拉选项）。

    场景定义唯一来源是 agent/scenarios.py，这里只是透传——
    前端不该自己硬编码一份「有哪些场景」，否则加场景要改两处。
    """
    return {"items": list_scenarios()}


@app.get("/classrooms/{classroom_id}/runs")
def api_list_agent_runs(classroom_id: int, limit: int = 20):
    """M4：查某课堂最近的多智能体编排留痕（供评估报告 / 排障用）。

    排障价值：多场景「没生效」时，这里能直接看到每轮实际派了哪些角色、
    是否被降级、降级原因是什么，不用去翻日志。
    """
    _require_db()
    limit = max(1, min(limit, 100))
    with get_db() as db:
        rows = (
            db.execute(
                select(AgentRun)
                .where(AgentRun.classroom_id == classroom_id)
                .order_by(AgentRun.id.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
        items = [
            {
                "id": r.id,
                "scenario": r.scenario,
                "topic": r.topic,
                "roles": r.roles,
                "degraded": bool(r.degraded),
                "reason": r.reason,
                "duration_ms": r.duration_ms,
                "message_ids": r.message_ids,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    return {"items": items}


def _eval_scope(
    classroom_id: int | None, user_id: int | None, scenario: str | None, limit: int
) -> str:
    """把过滤条件写成一句话，随结果一起返回。

    报告里的数字脱离样本范围就没有意义（"平均延迟 4.2s"——多少轮？哪个课堂？）。
    让接口自己把口径说清楚，避免出报告的人回头去翻他当初传了什么参数。
    """
    parts = []
    parts.append(f"课堂 {classroom_id}" if classroom_id else "全部课堂")
    if user_id:
        parts.append(f"学生 {user_id}")
    if scenario:
        parts.append(f"场景 {scenario}")
    parts.append(f"最近 {limit} 轮")
    return "｜".join(parts)


def _collect_eval_runs(
    classroom_id: int | None,
    user_id: int | None,
    scenario: str | None,
    limit: int,
) -> tuple[list[dict], dict, str | None]:
    """取留痕 + 自检，返回 (runs, diagnosis, error)。

    **查询失败也不抛**：这是「/evaluation/* 全空」能不能自解释的关键。
    查询炸了却返回 500，使用者面对一个状态码只能猜；返回 200 + diagnosis
    + error，他一眼就能看出是表缺失还是过滤条件不对。
    """
    diagnosis: dict = {"ready": False, "hint": ""}
    try:
        with get_db() as db:
            diagnosis = diagnose(db)
            runs = [
                run_to_dict(r)
                for r in list_agent_runs(
                    db,
                    classroom_id=classroom_id,
                    user_id=user_id,
                    scenario=scenario or None,
                    limit=limit,
                )
            ]
        # 库里有数据却一条没选出来：问题在过滤条件，不在数据库。
        # 不补这一句的话，使用者会对着「0」去跑 init_db.py，越修越远。
        if not runs and diagnosis.get("total_runs"):
            diagnosis["hint"] = empty_result_hint(diagnosis["total_runs"])
        return runs, diagnosis, None
    except Exception as exc:
        logger.error("读取编排留痕失败：%s", exc)
        try:
            with get_db() as db:
                diagnosis = diagnose(db)
        except Exception:
            pass
        return [], diagnosis, f"{type(exc).__name__}: {exc}"


@app.get("/evaluation/status")
def api_evaluation_status():
    """M4-C：留痕链路自检——**接口自己解释「为什么 /evaluation/* 是空的」**。

    与 summary 的区别：这个端点**不要求数据库可用**（DB 没配时返回
    `database=disabled` 而不是 503）。它的职责就是在出问题的时候还能被读到——
    一个自身会 503 的自检接口等于没有。
    """
    try:
        with get_db() as db:
            return diagnose(db)
    except Exception as exc:
        return {
            "ready": False,
            "database": "down",
            "database_detail": f"{type(exc).__name__}: {exc}",
            "table": {"exists": False, "missing_columns": [], "detail": ""},
            "total_runs": None,
            "runs_without_classroom": None,
            "hint": "自检执行本身失败，检查 MySQL 连接与权限。",
        }


@app.get("/evaluation/summary")
def api_evaluation_summary(
    classroom_id: int | None = None,
    user_id: int | None = None,
    scenario: str | None = None,
    limit: int = 200,
):
    """M4-C：多智能体协作的客观指标（成果 #4 评估报告的数据源）。

    只统计**机器能算的**：轮数、每轮 Agent 数、降级率、延迟分位、检索覆盖率。
    主观质量（准不准 / 贴不贴题）走 `/evaluation/sheet` 由人打分——
    两者混在一起会让读者以为客观指标也是打出来的分。

    `turns` 为 0 时**先读 `diagnosis.hint`**：留痕为空有四种成因（没配库 /
    连不上 / 表缺失 / 过滤条件不对），光看 0 区分不出来。
    """
    _require_db()
    limit = max(1, min(limit, 1000))
    runs, diagnosis, error = _collect_eval_runs(
        classroom_id, user_id, scenario, limit
    )
    return {
        "scope": _eval_scope(classroom_id, user_id, scenario, limit),
        "turns": len(runs),
        "summary": summarize_runs(runs),
        "diagnosis": diagnosis,
        "error": error,
    }


@app.get("/evaluation/sheet")
def api_evaluation_sheet(
    classroom_id: int | None = None,
    user_id: int | None = None,
    scenario: str | None = None,
    limit: int = 200,
):
    """M4-C：人工评分表——**每个 Agent 的每次发言一行**。

    按发言而不是按轮次评分：一轮里 tutor 讲得很好、peer 在复读，按轮打分只能
    给个折中分，看不出问题出在哪个角色——而这正是多智能体评估最该回答的问题。
    四个维度留空等人填，维度定义随结果一起返回，评分人不用再翻文档。

    `rows` 为空时读 `diagnosis.hint`；`diagnosis.runs_without_classroom` 大于 0
    表示有轮次没带课堂——那些轮次没有发言正文（`excerpt` 为空），评不了分。
    """
    _require_db()
    limit = max(1, min(limit, 1000))
    runs, diagnosis, error = _collect_eval_runs(
        classroom_id, user_id, scenario, limit
    )
    # 按时间正序：评分人从上往下读，逆序会让人搞不清哪句是接着哪句的。
    runs = list(reversed(runs))
    with get_db() as db:
        message_ids = [i for r in runs for i in (r.get("message_ids") or [])]
        messages = get_messages_by_ids(db, message_ids) if db is not None else {}
    rows = score_sheet_rows(runs, messages)
    return {
        "scope": _eval_scope(classroom_id, user_id, scenario, limit),
        "dimensions": [
            {"key": key, "label": label, "desc": desc}
            for key, label, desc in SCORE_DIMENSIONS
        ],
        "count": len(rows),
        "rows": rows,
        "diagnosis": diagnosis,
        "error": error,
    }


# ---- 检索链路指标报告（/evaluation/metrics*）----
# 单独成模块（eval/report_api.py）而不是堆在 app.py 里：评测跑批要起子进程、
# 要维护任务状态，逻辑不短。注入 store 取数函数以避免循环导入。
configure_metrics_api(get_knowledge_store)
app.include_router(metrics_router)


@app.post("/classrooms/{classroom_id}/members", status_code=201)
def api_add_member(classroom_id: int, body: AddMemberRequest):
    _require_db()
    with get_db() as db:
        # 先确认课堂和用户存在，否则外键约束会变成一句看不懂的 500
        room = db.get(Classroom, classroom_id)
        if room is None:
            raise HTTPException(
                status_code=404, detail=f"Classroom {classroom_id} not found."
            )
        if db.get(User, body.user_id) is None:
            raise HTTPException(
                status_code=404, detail=f"User {body.user_id} not found."
            )
        # 已在课堂里的人重复加入必须放行（add_member 幂等）：刷新页面就会
        # 重发一次加入请求，不该因为「名额已满」把老成员挡在外面。
        already = any(
            m.user_id == body.user_id
            for m in db.execute(
                select(ClassroomMember).where(
                    ClassroomMember.classroom_id == classroom_id
                )
            ).scalars()
        )

        # 1v1 指定学生（2026-11-02）：教师建房时点名了学生，这个教室就只属于
        # 那一位。别的学生进来一律 409 —— 不是「满了」，是「不归你」，文案要
        # 说清这一点，否则学生会以为是人数限制，反复重试。
        if (
            room.scenario == "v1"
            and room.target_student_id is not None
            and body.role == "student"
            and body.user_id != room.target_student_id
            and not already
        ):
            target = db.get(User, room.target_student_id)
            who = (
                f"{target.username}（id={target.id}）"
                if target is not None
                else f"id={room.target_student_id}"
            )
            raise HTTPException(
                status_code=409,
                detail=(
                    "这是指定学生的[1v1教室]，你不能进入。"
                    f"本教室指定学生：{who}。"
                    "如需上课，请联系老师把这个教室指定给你。"
                ),
            )

        # 1v1 课堂的人数闸门（2026-11-01）：除 AI 的主讲/出题助手外，
        # 只能有 1 位教师 + 1 位学生。AI 不落 classroom_members，所以直接数成员即可。
        # 已在本课堂的人再次加入要放行（add_member 本身幂等），否则刷新页面就会 409。
        if room is not None and room.scenario == "v1" and not already:
            counts = count_members_by_role(db, classroom_id)
            if counts.get(body.role, 0) >= 1:
                who = "教师" if body.role == "teacher" else "学生"
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"这是 1v1 课堂，只能有 1 位教师和 1 位学生。"
                        f"当前已经有{who}了，请换一个课堂号，"
                        f"或让教师把它建成小组讨论。"
                    ),
                )
        member = add_member(db, classroom_id, body.user_id, body.role)
        result = {
            "id": member.id,
            "classroom_id": member.classroom_id,
            "user_id": member.user_id,
            "role": member.role,
        }
    return result


@app.get("/classrooms/{classroom_id}/messages")
def api_list_messages(
    classroom_id: int, limit: int = 100, after: int | None = None
):
    """课堂全量消息（群聊视图）。

    不管 AI 内部按谁隔离上下文，这个端点一律返回整个课堂的对话——
    前端画聊天室要的是「所有人都能看到彼此」，助教/教师也要看全量。
    username 一并给出，否则前端分不清哪条是谁说的（AI 的回复为 null）。

    after：只返回 id 大于该值的消息（M1 聊天室增量轮询，避免每次拉全量）。
    """
    _require_db()
    limit = max(1, min(limit, 500))
    with get_db() as db:
        rows = list_messages(db, classroom_id, limit, after=after)
        # 序列化必须在 session 生命周期内完成。凡是要访问 ORM 对象的
        # relationship（这里每条消息都要读 m.user.username），一旦放到
        # with 块外面就会 detached，触发延迟加载直接 500。
        # username 是给前端渲染「谁说的」用的。assistant 消息虽然也记了
        # user_id（T6 改的，含义是「这条回复是给谁的」，用来在多人课堂里
        # 区分对话线），但直接拿它取 username 会把 AI 的回答渲染成学生的
        # 发言 —— 张冠李戴。AI 的发言人一律为 null，前端按 role 显示「AI」。
        messages = [
            {
                "id": m.id,
                "user_id": m.user_id,
                "username": (
                    m.user.username if (m.user and m.role != "assistant") else None
                ),
                "role": m.role,
                "content": m.content,
                "topic": m.topic,
                "hint_level": m.hint_level,
                "sources": m.sources or [],
                "agent_role": m.agent_role,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in rows
        ]
    return {"classroom_id": classroom_id, "count": len(messages), "messages": messages}


@app.post("/classrooms/{classroom_id}/messages", status_code=201)
def api_post_message(classroom_id: int, body: PostMessageRequest):
    """群聊发消息：人类成员（老师 / 学生）向房间发言，不触发 AI。

    校验发言者必须是该房间成员；role 以其在房间内的成员角色为准，而不是
    用户全局角色——同一个人可能既是 A 房的老师、又是 B 房的学生。前端
    据此把教师发言渲染成「教师」、学生发言渲染成「同学」。

    AI 回复仍走 /chat（role='assistant' 且带 agent_role），不在此处理。
    """
    _require_db()
    with get_db() as db:
        room = db.get(Classroom, classroom_id)
        if room is None:
            raise HTTPException(
                status_code=404, detail=f"Classroom {classroom_id} not found."
            )
        if not is_member(db, classroom_id, body.user_id):
            raise HTTPException(
                status_code=403,
                detail="You are not a member of this classroom.",
            )
        user = db.get(User, body.user_id)
        if user is None:
            raise HTTPException(
                status_code=404, detail=f"User {body.user_id} not found."
            )
        # 发言者角色以房间成员角色为准（同人在不同房间角色可能不同）。
        member_role = (
            db.execute(
                select(ClassroomMember.role).where(
                    ClassroomMember.classroom_id == classroom_id,
                    ClassroomMember.user_id == body.user_id,
                )
            ).scalar_one_or_none()
            or "student"
        )
        role = "teacher" if member_role == "teacher" else "student"
        msg = save_message(db, classroom_id, body.user_id, role, body.content)
        db.flush()
        result = {
            "id": msg.id,
            "user_id": msg.user_id,
            "username": user.username,
            "role": msg.role,
            "content": msg.content,
            "topic": msg.topic,
            "hint_level": msg.hint_level,
            "sources": msg.sources or [],
            "agent_role": msg.agent_role,
            "created_at": msg.created_at.isoformat() if msg.created_at else None,
        }
    return result


@app.get("/classrooms/{classroom_id}")
def api_get_classroom(classroom_id: int):
    _require_db()
    with get_db() as db:
        room = db.get(Classroom, classroom_id)
        if room is None:
            raise HTTPException(
                status_code=404, detail=f"Classroom {classroom_id} not found."
            )
        result = {
            "id": room.id,
            "name": room.name,
            "subject": room.subject,
            "description": room.description,
            "is_private": bool(room.is_private),
            # M4：房间跑哪种协作形态，前端据此决定要不要按角色分气泡渲染。
            "scenario": room.scenario,
            "use_tutor": bool(room.use_tutor),
            # 1v1 指定学生：前端要能显示「本教室指定给谁」，学生端也靠它
            # 提前判断自己该不该点进来。
            "target_student_id": room.target_student_id,
            "target_student_username": (
                db.get(User, room.target_student_id).username
                if room.target_student_id
                else None
            ),
        }
    return result


@app.patch("/classrooms/{classroom_id}")
def api_update_classroom(
    classroom_id: int, body: UpdateClassroomRequest, teacher_id: int = Query(...)
):
    """课堂设置增量更新（当前只有主讲助教开关）。

    为什么必须带 teacher_id：后端没有登录态，拦截伪造只能靠「让调用方
    自证身份」——查一遍成员表，确认这个人确实是这个课堂的教师。少了这一步，
    任何学生都能把全班的助教关掉。

    为什么用 PATCH 而不是 PUT：课堂对象还有 name/scenario/target_student
    等字段，PUT 语义要求全量提交，前端只想改一个开关就会误清空别的设置。
    """
    _require_db()
    with get_db() as db:
        room = db.get(Classroom, classroom_id)
        if room is None:
            raise HTTPException(
                status_code=404, detail=f"Classroom {classroom_id} not found."
            )
        is_teacher = db.execute(
            select(ClassroomMember).where(
                ClassroomMember.classroom_id == classroom_id,
                ClassroomMember.user_id == teacher_id,
                ClassroomMember.role == "teacher",
            )
        ).scalar_one_or_none()
        if is_teacher is None:
            raise HTTPException(
                status_code=403,
                detail="只有本课堂的教师才能修改助教开关。",
            )

        if body.use_tutor is not None:
            set_use_tutor(db, classroom_id, body.use_tutor)

        result = {
            "id": room.id,
            "use_tutor": bool(room.use_tutor),
            "scenario": room.scenario,
        }
    return result


@app.get("/classrooms")
def api_list_classrooms(user_id: int | None = None):
    """列出某用户所属的全部课堂（"我的课堂"）。

    客户端课堂列表原先只能存在本机 localStorage，换设备/刷新就丢。
    这里按成员关系返回该用户加入的课堂及其在其中的角色；不传 user_id
    时返回空列表，避免泄露全量课堂。
    """
    _require_db()
    if user_id is None:
        return {"user_id": None, "count": 0, "classrooms": []}
    with get_db() as db:
        classrooms = list_classrooms_for_user(db, user_id)
    return {"user_id": user_id, "count": len(classrooms), "classrooms": classrooms}


@app.get("/classrooms/{classroom_id}/members")
def api_list_members(classroom_id: int):
    """课堂成员：Teacher / Student。

    （AI 不作为成员记录——它以 messages.role='assistant' 参与对话，
    给它造一个 User 会污染 User 表和角色语义。）
    """
    _require_db()
    with get_db() as db:
        if db.get(Classroom, classroom_id) is None:
            raise HTTPException(
                status_code=404, detail=f"Classroom {classroom_id} not found."
            )
        rows = (
            db.execute(
                select(ClassroomMember).where(
                    ClassroomMember.classroom_id == classroom_id
                )
            )
            .scalars()
            .all()
        )
        result = {
            "classroom_id": classroom_id,
            "count": len(rows),
            "members": [
                {
                    "user_id": m.user_id,
                    "username": m.user.username,
                    "role": m.role,
                }
                for m in rows
            ],
        }
    return result


# --- T5：学生画像 ---------------------------------------------------------
# 画像是每轮对话自动沉淀的（见 agent/profiler.py），这里的接口负责
# 「读出来给人看」和「让人手工修正」两件事。


class UpdateProfileRequest(BaseModel):
    """手工修正画像。

    只覆盖传了的字段，没传的保持原样。注意：手工改完并不会锁死——
    下一轮对话仍会按新证据继续更新，这是画像而不是配置。
    """

    classroom_id: int
    overall_level: Literal["beginner", "intermediate", "advanced"] | None = None
    learning_preference: str | None = None
    knowledge_mastery: dict[str, float] | None = None
    weak_points: list[str] | None = None


@app.get("/students/{user_id}/profile")
def api_get_profile(user_id: int, classroom_id: int | None = None):
    """读画像。给了 classroom_id 返回单个，不给返回该学生全部课堂的画像。"""
    _require_db()
    with get_db() as db:
        if db.get(User, user_id) is None:
            raise HTTPException(
                status_code=404, detail=f"User {user_id} not found."
            )
        query = select(StudentProfile).where(StudentProfile.user_id == user_id)
        if classroom_id is not None:
            query = query.where(StudentProfile.classroom_id == classroom_id)
        rows = db.execute(query).scalars().all()
        result = [profile_to_dict(r) for r in rows]

    if classroom_id is not None:
        if not result:
            raise HTTPException(
                status_code=404,
                detail=f"No profile for user {user_id} in classroom {classroom_id}.",
            )
        return result[0]
    return {"user_id": user_id, "count": len(result), "profiles": result}


@app.put("/students/{user_id}/profile")
def api_update_profile(user_id: int, body: UpdateProfileRequest):
    _require_db()
    with get_db() as db:
        if db.get(User, user_id) is None:
            raise HTTPException(
                status_code=404, detail=f"User {user_id} not found."
            )
        if db.get(Classroom, body.classroom_id) is None:
            raise HTTPException(
                status_code=404,
                detail=f"Classroom {body.classroom_id} not found.",
            )

        profile = get_or_create_profile(db, user_id, body.classroom_id)

        if body.overall_level is not None:
            profile.overall_level = body.overall_level
        if body.learning_preference is not None:
            profile.learning_preference = body.learning_preference
        if body.knowledge_mastery is not None:
            # 掌握度定义在 0~1，手工写超范围会给 T6 的分级逻辑喂脏数据
            profile.knowledge_mastery = {
                topic: round(min(1.0, max(0.0, float(score))), 2)
                for topic, score in body.knowledge_mastery.items()
            }
        if body.weak_points is not None:
            profile.weak_points = body.weak_points

        # 手工改了掌握度就要重新定级，否则会出现「 mastery 全 0.9 但
        # 还挂着 beginner」这种自相矛盾的画像。
        if body.knowledge_mastery is not None and body.overall_level is None:
            profile.overall_level = derive_level(profile.knowledge_mastery)

        db.flush()
        result = profile_to_dict(profile)
    return result


@app.get("/students/{user_id}/behaviors")
def api_list_behaviors(
    user_id: int, classroom_id: int | None = None, limit: int = 50
):
    """学习行为流水：画像里那些结论的推导依据。"""
    _require_db()
    limit = max(1, min(limit, 500))
    with get_db() as db:
        if db.get(User, user_id) is None:
            raise HTTPException(
                status_code=404, detail=f"User {user_id} not found."
            )
        query = select(LearningBehavior).where(LearningBehavior.user_id == user_id)
        if classroom_id is not None:
            query = query.where(LearningBehavior.classroom_id == classroom_id)
        rows = db.execute(query.order_by(LearningBehavior.id.desc()).limit(limit))
        rows = rows.scalars().all()
        result = [
            {
                "id": b.id,
                "classroom_id": b.classroom_id,
                "topic": b.topic,
                "action": b.action,
                "hint_level": b.hint_level,
                "created_at": b.created_at.isoformat() if b.created_at else None,
            }
            for b in rows
        ]
    return {"user_id": user_id, "count": len(result), "behaviors": result}


# --- T6：个性化提示词 -----------------------------------------------------
# 画像在 /chat 里自动注入。这里额外开一个「不打 LLM 的预览」，
# 用来验收同一道题在不同画像下讲法到底差在哪。


@app.get("/students/{user_id}/prompt-preview")
def api_prompt_preview(
    user_id: int,
    classroom_id: int,
    topic: str = "",
    hint_level: int = 0,
):
    """预览这个学生接下来会拿到什么讲法。不消耗 LLM 额度。"""
    _require_db()
    hint_level = max(0, min(hint_level, 3))
    with get_db() as db:
        if db.get(User, user_id) is None:
            raise HTTPException(
                status_code=404, detail=f"User {user_id} not found."
            )
        if db.get(Classroom, classroom_id) is None:
            raise HTTPException(
                status_code=404, detail=f"Classroom {classroom_id} not found."
            )
        profile = load_profile(db, user_id, classroom_id)

    result = preview_prompt(profile, topic, hint_level, HINT_STRATEGIES)
    result["user_id"] = user_id
    result["classroom_id"] = classroom_id
    result["profile"] = profile
    return result


# --- M2：认知诊断（CDT）--------------------------------------------------
# 预测试组卷 / 批改 / 知识图谱三段。这一段是 T5 画像的「上游数据源」：
# 以前画像只能等对话慢慢漂，现在能在学生开口之前先把能力面测一遍，
# 让 personalized 讲法在第一节课就有依据，而不是聊了十轮才开始个性化。


class PretestRequest(BaseModel):
    classroom_id: int
    # 不传就按掌握度（图谱推荐）自动选题；传了就只考指定知识点。
    topics: list[str] | None = None
    limit: int = Field(default=8, ge=1, le=16)


class PretestAnswer(BaseModel):
    item_id: str
    choice: int = Field(ge=0, le=3)
    # 这两个是认知风格的证据来源，前端不给也不影响批改
    elapsed_ms: int | None = None
    hint_requested: bool = False


class PretestSubmitRequest(BaseModel):
    classroom_id: int
    answers: list[PretestAnswer] = Field(default_factory=list, max_length=64)


@app.post("/students/{user_id}/pretest")
def api_create_pretest(user_id: int, body: PretestRequest):
    """出一卷认知诊断题（不含答案）。"""
    _require_db()
    with get_db() as db:
        if db.get(User, user_id) is None:
            raise HTTPException(
                status_code=404, detail=f"User {user_id} not found."
            )
        if db.get(Classroom, body.classroom_id) is None:
            raise HTTPException(
                status_code=404,
                detail=f"Classroom {body.classroom_id} not found.",
            )
        prior = _profile_mastery(db, user_id, body.classroom_id)

    paper = build_pretest(prior, body.topics, body.limit)
    paper["user_id"] = user_id
    paper["classroom_id"] = body.classroom_id
    return paper


@app.post("/students/{user_id}/pretest/submit")
def api_submit_pretest(user_id: int, body: PretestSubmitRequest):
    """批改并把诊断结果写进画像。

    写完会顺带重算 overall_level：预测试一次可能把掌握度整体推高/压低，
    不重算就会出现「mastery 全 0.8 却还挂着 beginner」的自相矛盾画像。
    """
    _require_db()
    with get_db() as db:
        if db.get(User, user_id) is None:
            raise HTTPException(
                status_code=404, detail=f"User {user_id} not found."
            )
        if db.get(Classroom, body.classroom_id) is None:
            raise HTTPException(
                status_code=404,
                detail=f"Classroom {body.classroom_id} not found.",
            )

        profile = get_or_create_profile(db, user_id, body.classroom_id)
        prior_mastery = dict(profile.knowledge_mastery or {})

        result = score_attempt(
            [a.model_dump() for a in body.answers], prior_mastery
        )
        if result["graded"] == 0:
            raise HTTPException(
                status_code=400,
                detail="没有可批改的作答：answers 为空或 item_id 全部无效。",
            )

        # 掌握度合并：本次测到的知识点覆盖旧值，没测到的保持原样。
        # 直接整体替换会把历史积累的对话证据一次性抹掉。
        merged = dict(prior_mastery)
        merged.update(result["mastery"])
        profile.knowledge_mastery = merged

        profile.ability_vector = merge_ability_evidence(
            profile.ability_vector, result["details"]
        )

        style = result["cognitive_style"]
        # 样本不足（ready=False）时不覆盖已有结论：一次小样本测验不该
        # 把之前累积的风格判断冲掉，宁可保留旧值。
        if style.get("ready"):
            profile.cognitive_style = style

        for note in weak_points_from(result["details"]):
            profile.weak_points = merge_weak_points(profile.weak_points or [], note)

        profile.overall_level = derive_level(merged)
        profile.pretested_at = dt.datetime.utcnow()
        db.flush()

        updated = profile_to_dict(profile)
        next_topics = recommend_next(merged, limit=3)

    return {
        "user_id": user_id,
        "classroom_id": body.classroom_id,
        "graded": result["graded"],
        "invalid_items": result["invalid_items"],
        "correct": result["correct"],
        "accuracy": result["accuracy"],
        "mastery_updates": result["mastery"],
        "per_question": result["details"],
        "cognitive_style": style,
        "recommended_next": next_topics,
        "profile": updated,
    }


@app.get("/students/{user_id}/knowledge-graph")
def api_knowledge_graph(user_id: int, classroom_id: int):
    """带掌握度叠加的知识图谱：前端画学习地图用。"""
    _require_db()
    with get_db() as db:
        if db.get(User, user_id) is None:
            raise HTTPException(
                status_code=404, detail=f"User {user_id} not found."
            )
        if db.get(Classroom, classroom_id) is None:
            raise HTTPException(
                status_code=404, detail=f"Classroom {classroom_id} not found.",
            )
        mastery = _profile_mastery(db, user_id, classroom_id)

    payload = graph_payload(mastery)
    payload["user_id"] = user_id
    payload["classroom_id"] = classroom_id
    payload["recommended_next"] = recommend_next(mastery, limit=3)
    return payload


# --- T7：教师备课资源（Teacher Resource Agent）---------------------------
# 教师给一个主题 → 检索常驻八股知识库 → 生成大纲 JSON + 一个 .pptx 文件。


class PptOutlineRequest(BaseModel):
    topic: str = Field(..., min_length=1, max_length=100)
    classroom_id: int
    teacher_id: int
    # 受众水平：beginner / intermediate / advanced，留空让模型自己判断
    audience: Literal["", "beginner", "intermediate", "advanced"] = ""


class GenerateResourceRequest(BaseModel):
    """M3 统一资源生成：教师为某课堂（可指定目标学生做个性化）生成 5 类资源。"""

    resource_type: Literal[
        "slides", "quiz", "mindmap", "cards", "guide"
    ]
    topic: str = Field(..., min_length=1, max_length=100)
    classroom_id: int
    teacher_id: int
    # 个性化目标学生：传了就按该学生画像（薄弱点/认知风格/水平）定制资源
    user_id: int | None = None
    # 0-1 难度系数；不传则由画像推导（见 resolve_difficulty）
    difficulty: float | None = Field(default=None, ge=0.0, le=1.0)
    audience: Literal["", "beginner", "intermediate", "advanced"] = ""
    # quiz 的题量 / cards 的卡片数 / 其它类型的规模
    count: int = Field(default=5, ge=1, le=30)
    # 是否检索常驻知识库作为依据（备课怕模型凭印象讲错，默认开）
    retrieve: bool = True
    # 仅 slides 生效：是否顺带渲染一个 .pptx 文件
    render_pptx: bool = False
    # 是否在提示词里注入 few-shot 样例（稳定输出格式，默认开）
    few_shot: bool = True


def _require_teacher(db, classroom_id: int, teacher_id: int) -> None:
    """备课是教师专属动作，学生不能拿它批量生成讲义。"""
    if db.get(User, teacher_id) is None:
        raise HTTPException(
            status_code=404, detail=f"User {teacher_id} not found."
        )
    if db.get(Classroom, classroom_id) is None:
        raise HTTPException(
            status_code=404, detail=f"Classroom {classroom_id} not found."
        )
    membership = (
        db.execute(
            select(ClassroomMember).where(
                ClassroomMember.classroom_id == classroom_id,
                ClassroomMember.user_id == teacher_id,
            )
        )
        .scalars()
        .first()
    )
    if membership is None:
        raise HTTPException(
            status_code=403,
            detail=f"User {teacher_id} is not a member of classroom {classroom_id}.",
        )
    if membership.role != "teacher":
        raise HTTPException(
            status_code=403,
            detail=(
                f"User {teacher_id} joined classroom {classroom_id} as "
                f"'{membership.role}', not as a teacher."
            ),
        )


@app.post("/resources/ppt-outline")
@limiter.limit("5/minute")
async def api_ppt_outline(request: Request, body: PptOutlineRequest):
    """生成备课大纲：返回结构化 JSON，同时落一个 .pptx 文件。"""
    _require_db()
    try:
        llm = get_llm()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    with get_db() as db:
        _require_teacher(db, body.classroom_id, body.teacher_id)

    # 备课最怕模型凭印象讲错，所以知识库检索是硬要求
    context = await asyncio.to_thread(
        get_knowledge_context, get_knowledge_store(), body.topic
    )

    try:
        outline = await generate_outline(llm, body.topic, context, body.audience)
    except OutlineError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="The configured language model could not generate the outline.",
        ) from exc

    # 结构合法但内容全空的大纲，渲染出来就是一张封面的 PPT。
    # 这时候报失败让教师重试，比让他拿个空壳去上课好。
    if outline.is_shell():
        raise HTTPException(
            status_code=502,
            detail="模型返回了一份空大纲，请重试。",
        )

    try:
        path = build_pptx(outline, subtitle=f"课堂 {body.classroom_id}")
    except Exception as exc:
        # 大纲已经生成好了，PPT 渲染失败不该让这次备课整个白做
        print(f"[pptx] 渲染失败（大纲照常返回）：{exc}", flush=True)
        path = ""

    return {
        "topic": body.topic,
        "classroom_id": body.classroom_id,
        "audience": body.audience,
        "retrieved": bool(context),
        "outline": outline_to_dict(outline),
        "file_path": path,
        "file_name": os.path.basename(path) if path else None,
        "download_url": f"/resources/files/{os.path.basename(path)}" if path else None,
    }


def _resource_response(
    body: GenerateResourceRequest,
    resolved: float,
    profile: dict | None,
    *,
    retrieved: bool,
    resource: dict,
    from_bank: bool = False,
) -> dict:
    """统一资源响应外壳：把"这次到底生成了什么"讲清楚，便于前端与评估。"""
    personalized = bool(profile and build_profile_brief(profile, body.topic))
    return {
        "resource_type": body.resource_type,
        "topic": body.topic,
        "classroom_id": body.classroom_id,
        "difficulty": round(resolved, 2),
        "difficulty_label": difficulty_label(resolved),
        "personalized": personalized,
        "retrieved": retrieved,
        "from_bank": from_bank,
        "resource": resource,
    }


@app.post("/resources/generate")
@limiter.limit("5/minute")
async def api_generate_resource(request: Request, body: GenerateResourceRequest):
    """M3 统一资源生成端点。

    教师为某课堂生成 5 类个性化资源之一（slides/quiz/mindmap/cards/guide）。
    可选 user_id 指定目标学生，按其画像做个性化；否则按课堂通用讲法。
    quiz 在主题命中 M2 标定题库时走零-LLM 题库组卷（难度可控、可复现），
    其余情况与所有其它类型都走 LLM 生成。
    """
    _require_db()
    try:
        llm = get_llm()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    with get_db() as db:
        _require_teacher(db, body.classroom_id, body.teacher_id)
        profile: dict | None = None
        if body.user_id is not None:
            if db.get(User, body.user_id) is None:
                raise HTTPException(
                    status_code=404, detail=f"User {body.user_id} not found."
                )
            profile = load_profile(db, body.user_id, body.classroom_id)

    resolved = resolve_difficulty(body.difficulty, profile, body.topic)

    # quiz 零-LLM 题库路径：命中题库知识点就直接出卷，不烧额度、可复现
    if body.resource_type == "quiz" and body.retrieve:
        bank = build_quiz_from_bank([body.topic], resolved, body.count)
        if bank is not None:
            return _resource_response(
                body, resolved, profile, retrieved=False, resource=bank, from_bank=True
            )

    context = ""
    if body.retrieve:
        context = await asyncio.to_thread(
            get_knowledge_context, get_knowledge_store(), body.topic
        )

    try:
        resource = await generate_resource(
            llm,
            body.resource_type,
            body.topic,
            profile=profile,
            difficulty=body.difficulty,
            audience=body.audience,
            context=context,
            few_shot=body.few_shot,
            count=body.count,
        )
    except ResourceGenError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="The configured language model could not generate the resource.",
        ) from exc

    response = _resource_response(
        body, resolved, profile, retrieved=bool(context), resource=resource
    )

    # slides 可选渲染 pptx：复用 T7 的 build_pptx，渲染失败不连累资源本体
    if body.resource_type == "slides" and body.render_pptx:
        try:
            from agent.resource_agent import PptOutline

            outline = PptOutline(**resource)
            path = build_pptx(outline, subtitle=f"课堂 {body.classroom_id}")
            response["file_name"] = os.path.basename(path)
            response["download_url"] = (
                f"/resources/files/{os.path.basename(path)}"
            )
        except Exception as exc:  # noqa: BLE001 - 大纲已生成，PPT 渲染失败不该整体失败
            print(f"[pptx] 渲染失败（资源照常返回）：{exc}", flush=True)

    return response


@app.get("/resources/files/{file_name}")
def api_download_resource(file_name: str):
    """下载生成的 .pptx。只认文件名，不认路径——防目录穿越。"""
    directory = Path(
        os.getenv("RESOURCE_OUTPUT_DIR")
        or Path(__file__).resolve().parent / "generated"
    )
    safe_name = os.path.basename(file_name)
    path = directory / safe_name
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"{safe_name} not found.")
    return FileResponse(
        str(path),
        filename=safe_name,
        media_type=(
            "application/vnd.openxmlformats-officedocument."
            "presentationml.presentation"
        ),
    )


@app.post("/upload")
@limiter.limit("3/hour")
async def upload_document(request: Request, file: UploadFile = File(...)):
    """学生上传 PDF：建成**会话级内存库**，1 小时过期或被 LRU 淘汰。

    与常驻八股知识库是两套独立的东西：这里不落盘、不进 `chroma_kb`，
    所以不会污染评测基线（L1 金标准只针对 `cs_basics`）。
    """
    session_id = str(uuid.uuid4())
    collection_name = f"student_upload_{session_id.replace('-', '_')}"
    vectorstore = await _index_upload(file, collection_name)

    await asyncio.to_thread(cleanup_expired_sessions)
    await asyncio.to_thread(evict_oldest_session_if_full)
    sessions[session_id] = DocumentSession(
        vectorstore=vectorstore,
        created_at=time.monotonic(),
        audience="student",
    )

    return {
        "session_id": session_id,
        "audience": "student",
        "message": "Document indexed successfully.",
    }


def _form_default_str(value: object, fallback: str) -> str:
    """把 FastAPI 的 `Form(...)` 默认哨兵折叠成真正的字符串。

    HTTP 请求进来时带 `Form` 声明的参数一定是 `str`；只有**直接调用端点函数**
    （单测、内部复用）时才会拿到 `Form` 对象本身。不折叠的后果是
    「不传 persist」这种最正常的调用被判成非法值 400——实测踩过（522 例回归里
    两条旧用例就是这么红的），而且报错信息（persist must be ...）与真实原因
    毫无关系，很难一眼看出。
    """
    return value if isinstance(value, str) else fallback


@app.post("/upload/teacher")
@limiter.limit("10/hour")
async def upload_teacher_document(
    request: Request,
    file: UploadFile = File(...),
    teacher_id: int = Form(...),
    classroom_id: int = Form(...),
    persist: str = Form("session"),
):
    """教师上传 PDF：`persist` 决定**这份内容放到哪里**。

    两种语义绝不能混（`docs/proposals/PROPOSALS.md` §一）：

    - `persist=session`（**默认**，XB-21 方案 A）：备课素材，用后即弃。
      建会话级内存库（TTL 1 小时、进程重启消失），只有上传者这次能检索到；
      不落盘、不进 `chroma_kb`，所以不影响评测基线（L1 只针对 `cs_basics`）。
    - `persist=classroom`（XB-23 方案 B）：课程资料，长期生效。
      原件落盘 `backend/classroom_docs/{id}/`、派生索引落
      `backend/chroma_classrooms/`、登记 `classroom_documents`；**本课堂学生
      在 /chat 里能检索到**。生命周期跟随 `Classroom` 行——结课不等于删除。

    默认值保存量行为零变化：老调用方不传 persist，走的仍是方案 A。
    两个入口共用 `_stash_upload()` 的校验（扩展名/大小/魔数），权限都走
    `_require_teacher()`（校验 role，不是只查 membership）。
    """
    # 直接调用端点时 persist 会是 Form 哨兵而非字符串，先折叠（见 _form_default_str）。
    persist = _form_default_str(persist, "session")
    if persist not in {"session", "classroom"}:
        raise HTTPException(
            status_code=400,
            detail="persist must be 'session' or 'classroom'.",
        )

    _require_db()
    with get_db() as db:
        _require_teacher(db, classroom_id, teacher_id)

    if persist == "classroom":
        return await _index_classroom_upload(file, classroom_id, teacher_id)

    session_id = str(uuid.uuid4())
    collection_name = f"teacher_upload_{session_id.replace('-', '_')}"
    vectorstore = await _index_upload(file, collection_name)

    await asyncio.to_thread(cleanup_expired_sessions)
    await asyncio.to_thread(evict_oldest_session_if_full)
    sessions[session_id] = DocumentSession(
        vectorstore=vectorstore,
        created_at=time.monotonic(),
        audience="teacher",
        teacher_id=teacher_id,
        classroom_id=classroom_id,
    )
    logger.info(
        "教师上传备课素材｜课堂 %d 教师 %d 文件 %r 会话 %s",
        classroom_id,
        teacher_id,
        file.filename,
        session_id,
    )

    return {
        "session_id": session_id,
        "audience": "teacher",
        "classroom_id": classroom_id,
        "persist": "session",
        "message": "Document indexed successfully.",
    }


@app.get("/classrooms/{classroom_id}/documents")
def api_list_classroom_documents(classroom_id: int, teacher_id: int = Query(...)):
    """课堂资料清单（教师视角，不含正文）。

    只给教师看：这是备课侧的资产管理视图，学生侧只需要「问答时能被检索到」，
    不需要看到别人传了什么文件——少一个接口就少一个泄漏面。
    """
    _require_db()
    with get_db() as db:
        _require_teacher(db, classroom_id, teacher_id)
        documents = list_classroom_documents(db, classroom_id)
    return {"classroom_id": classroom_id, "count": len(documents), "documents": documents}


@app.delete("/classrooms/{classroom_id}/documents/{doc_id}", status_code=204)
def api_delete_classroom_document(
    classroom_id: int, doc_id: str, teacher_id: int = Query(...)
):
    """教师删除本课堂的一份资料（软删登记 + 删切片 + 删原件）。

    三步的顺序是刻意的：先删切片（检索立刻失效），再软删登记（审计留痕），
    最后删原件。反过来（先删原件）一旦切片删除失败，索引里会留下指向
    不存在文件的片段，重建时又读不到源——最难排查的那种状态。
    """
    _require_db()
    with get_db() as db:
        _require_teacher(db, classroom_id, teacher_id)
        doc = get_classroom_document(db, classroom_id, doc_id)
        if doc is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Document {doc_id} not found in classroom {classroom_id}."
                ),
            )
        chunks = classroom_store.delete_document_chunks(classroom_id, doc_id)
        mark_classroom_document_deleted(db, doc)
        classroom_store.remove_original(doc.stored_path)
        # 删到一份不剩时把空 collection 与空目录一起清掉。
        # 不清的后果：每删一批就留一个 0 切片的 collection，课堂换了几届之后
        # 运维看到一堆空库，分不清哪个还活着；而且每次检索都要为它建一次句柄。
        # 只清存储，**登记行保留**（软删，审计要能查到谁删了什么）。
        remaining, _bytes = classroom_document_usage(db, classroom_id)
        if remaining == 0:
            classroom_store.purge_classroom(classroom_id)
    logger.info(
        "课堂资料删除｜课堂 %d 教师 %d 文档 %s 切片 %d",
        classroom_id,
        teacher_id,
        doc_id,
        chunks,
    )
    return Response(status_code=204)


@app.delete("/sessions/{session_id}", status_code=204)
async def delete_document_session(session_id: str) -> Response:
    session = sessions.pop(session_id, None)
    if session is not None:
        await asyncio.to_thread(_delete_vectorstore, session.vectorstore)
    return Response(status_code=204)
