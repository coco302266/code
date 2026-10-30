"""课堂级资料库（XB-23 方案 B）：原件落盘 + 派生向量索引 + 检索。

**这一层解决什么**：教室传的讲义要「长期生效、本课堂学生能检索到」。
会话级内存库（`/upload` 那条路）1 小时就消失，做不到这件事；直接写共享库
`cs_basics` 会让一份错题集泄漏到全平台、并让 L1 金标准与指纹当场失效。

**三层职责必须分清**（改这里之前先读这段）：

| 层 | 谁 | 存哪 | 能不能重建 |
|---|---|---|---|
| 原件（事实源） | `save_original()` | `classroom_docs/{id}/` | —— 它是真相本身 |
| 登记 | `db/repository.py` | MySQL `classroom_documents` | 可重建（有原件和元数据） |
| 派生索引 | `index_classroom_document()` | `chroma_classrooms/classroom_{id}_docs` | **可以**：从原件重切重嵌 |

只往 Chroma 里写而不留原件，换 embedding 模型 / 改切片参数 / Chroma 升级迁移时
课堂库就永久不可重建——所以「原件落盘」不是可选项。

**一个已实测的关键前提**：同一进程内 chromadb 按路径共享一个 System
（`SharedSystemClient._identifier_to_system` 里只有一条），所以
「教师上传写入 → 学生 /chat 读出」不需要重启、也不需要清缓存。
实测（2026-10-26，临时探针已删）：先开的客户端能看到另一个客户端后写的数据。
**跨进程**写入仍然要重载（P5 教训），所以运维脚本改完库要重启服务或调热重载。
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from functools import lru_cache
from pathlib import Path

from langchain_chroma import Chroma
from langchain_core.documents import Document

from .indexer import build_index, get_embeddings
from .retriever import METADATA_QUERY, _dedupe

logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parents[2]

# 原件目录。刻意和 chroma_kb 分开：备份 / 迁移 / 清理时一眼能分清
# 「源文件」与「派生索引」，也避免任何脚本把课堂资料误算进共享库指纹。
DOCS_DIR = Path(
    os.getenv("CLASSROOM_DOCS_DIR", str(BACKEND_DIR / "classroom_docs"))
)
CHROMA_DIR = Path(
    os.getenv("CLASSROOM_CHROMA_DIR", str(BACKEND_DIR / "chroma_classrooms"))
)

_UNSAFE = re.compile(r'[\\/:*?"<>|\s]+')


def _max_distance() -> float | None:
    """课堂库注入的向量距离上限（越小越相关）。设 0/off 表示不设门限。

    实测依据（2026-10-26，`_eval_tmp/probe_distance.py`，Python 教材 187 片）：
      同类问句（字符串方法 / 列表元组） 0.72 ~ 0.98
      离题问句（天气 / 菜谱 / 诗 / 股市 / 电影） 1.19 ~ 1.48
    取 1.25 是「宁可漏挡、不可错杀」：能挡掉 5 条离题里的 3 条，同类的离得还远。

    ⚠ **这不是校准好的阈值**：只有一份教材、十个问句，且分布有重叠
    （离题的「红烧肉」1.1887 比同科的「快速重传」1.2038 还近——但后者对这份
    Python 教材本来就离题，说明我当时的标注样本不干净）。真实课堂语料进来后
    要重标一次。交叉重排能真正分开（同类 -0.86 vs 离题 ≤ -7.0），但本机
    3 条就要 4s，每轮都跑会击穿 p95，所以不用。
    """
    raw = os.getenv("CLASSROOM_MAX_DISTANCE", "1.25").strip().lower()
    if raw in ("", "off", "none", "0"):
        return None
    try:
        value = float(raw)
    except ValueError:
        logger.warning("CLASSROOM_MAX_DISTANCE 值无法解析（%r），本次不设门限", raw)
        return None
    return value if value > 0 else None


def classroom_collection_name(classroom_id: int) -> str:
    """一个课堂一个 collection。

    用 `classroom_{id}_docs` 而不是共享库加过滤条件：权限边界是**物理的**，
    学生查别班资料在机制上不可能，不依赖「每个调用点都记得传过滤参数」。
    代价是 collection 数随课堂数增长——课堂数 >20~30 时按
    `docs/proposals/PROPOSALS.md` §9.3 切课堂池，那是另一档。
    """
    return f"classroom_{int(classroom_id)}_docs"


def classroom_dir(classroom_id: int) -> Path:
    return DOCS_DIR / str(int(classroom_id))


def safe_filename(filename: str) -> str:
    """去掉路径分隔符与非法字符，保留扩展名。

    上传文件名是不可信输入：带 `../` 就能写到目录外，带 `\\` 会在落盘时
    变成另一条路径。这里只保留最后一段并清洗非法字符，截断到 120 字符。
    """
    name = (filename or "").replace("\\", "/").split("/")[-1].strip()
    name = _UNSAFE.sub("_", name).strip("._") or "document.pdf"
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    stem = name[:-4][:116]
    return f"{stem}.pdf"


def source_label(classroom_id: int, doc_id: str, filename: str) -> str:
    """切片 metadata 里的 `source` 标签。

    前缀化是刻意的（`docs/proposals/PROPOSALS.md` §2.3）：将来若并入 scope
    课堂池，`parent_id` / 去重键不会与共享库或其他课堂撞车。共享库的 source
    **绝不能**加前缀——它参与 `index_fingerprint`。
    """
    return f"classroom:{int(classroom_id)}/{doc_id}_{safe_filename(filename)}"


def save_original(
    tmp_path: str, classroom_id: int, doc_id: str, filename: str
) -> Path:
    """把临时文件拷进课堂原件目录，返回落盘路径。

    先 `mkdir(parents=True)` 再 float 拷贝：多线程同时上传同一课堂时目录可能
    被并发创建，`exist_ok=True` 让这一步幂等。
    """
    target_dir = classroom_dir(classroom_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{doc_id}_{safe_filename(filename)}"
    shutil.copyfile(tmp_path, target)
    return target


def remove_original(stored_path: str | Path) -> bool:
    """删原件。文件不在就返回 False，不抛——删除接口不该因为文件已丢而 500。"""
    try:
        Path(stored_path).unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        logger.warning("课堂原件删除失败 %s：%s", stored_path, exc)
        return False


@lru_cache(maxsize=1)
def _embeddings():
    """**必须缓存**：`get_embeddings()` 每次调用都会新建 FastEmbedEmbeddings，
    而它的构造函数会加载 ONNX 模型（约 1.3s）。不缓存的话每次 /chat 都要多付
    1.3s，p95 门禁（≤5s）直接被击穿。"""
    return get_embeddings()


def index_classroom_document(
    stored_path: str | Path,
    classroom_id: int,
    doc_id: str,
    filename: str,
) -> Chroma:
    """从原件建/追加派生索引，返回（落盘的）vectorstore。"""
    return build_index(
        str(stored_path),
        classroom_collection_name(classroom_id),
        persist_directory=str(CHROMA_DIR),
        metadata_extra={
            "doc_id": doc_id,
            "classroom_id": int(classroom_id),
            # metadata 里存**原始文件名**（只用于展示/溯源），落盘路径才用
            # `safe_filename()` 清洗——用户看到的应该是自己上传的名字。
            "filename": (filename or "document.pdf")[:255],
            "source": source_label(classroom_id, doc_id, filename),
        },
    )


def count_chunks(store: Chroma) -> int:
    """collection 总切片数（整个课堂，不是单个文件）。

    ⚠ 不要拿它当「刚上传这份文件有多少片」回报——一个课堂一个 collection，
    教第二份文件时它会把上一份的片一起数进去。实测后果（2026-10-26）：
    3 页空白 PDF 上传后报 `chunk_count=188`，教师以为成功了。
    单文件计量用 `document_metrics()`。
    """
    try:
        return int(store._collection.count())
    except Exception:
        return 0


def document_metrics(store: Chroma, doc_id: str) -> tuple[int, int]:
    """返回 (本文件切片数, 其中正文切片数)。

    第二个数是「这份 PDF 到底有没有可检索的文字」。只有 `front_matter` 一片
    时说明整份文档抽不出文本（扫描件 / 纯图片页），此时入库成功也永远检索不到，
    属于最坏的静默失败：教师看到 200 就走了，学生问什么都命不中。
    """
    try:
        got = store._collection.get(
            where={"doc_id": doc_id}, include=["metadatas"]
        )
    except Exception as exc:
        logger.warning("课堂切片计量失败 doc=%s：%s", doc_id, exc)
        return 0, 0
    total = len(got.get("ids") or [])
    body = sum(
        1
        for meta in (got.get("metadatas") or [])
        if (meta or {}).get("section") != "front_matter"
    )
    return total, body


def collection_exists(classroom_id: int) -> bool:
    """课堂库是否存在。目录都不存在时直接 False，避免顺手创建空目录。

    chromadb 1.x 的 `list_collections()` 返回 Collection 对象（旧版返回名字），
    这里两种都兼容——换版本不该让这段静默失效。
    """
    if not CHROMA_DIR.exists():
        return False
    try:
        import chromadb

        client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        names = {
            getattr(item, "name", item) for item in client.list_collections()
        }
        return classroom_collection_name(classroom_id) in names
    except Exception as exc:
        logger.warning("课堂库列举失败 classroom=%s：%s", classroom_id, exc)
        return False


_STORE_CACHE: dict[int, Chroma] = {}


def get_classroom_store(classroom_id: int) -> Chroma | None:
    """取课堂 vectorstore（带缓存）；没有这个课堂库就返回 None。

    缓存的是**句柄**，不是数据：同进程内的写入对已缓存句柄即时可见
    （见模块 docstring 的实测结论），所以不需要「写一次就清一次」。
    删除 collection 时必须 `invalidate()`，否则手里握的是已销毁的句柄——
    那会抛 NotFoundError（P5：drop 后常驻进程句柄悬空）。
    """
    cid = int(classroom_id)
    cached = _STORE_CACHE.get(cid)
    if cached is not None:
        return cached
    if not collection_exists(cid):
        return None
    store = Chroma(
        persist_directory=str(CHROMA_DIR),
        collection_name=classroom_collection_name(cid),
        embedding_function=_embeddings(),
    )
    _STORE_CACHE[cid] = store
    return store


def invalidate(classroom_id: int) -> None:
    _STORE_CACHE.pop(int(classroom_id), None)


def delete_document_chunks(classroom_id: int, doc_id: str) -> int:
    """删一份文档的全部切片，返回删除条数。

    按 `doc_id` 删而不是按 `source`：source 带文件名，教师改个文件名重传
    就会留下孤儿切片；doc_id 是入库时生成的主键，不会漂。
    """
    store = get_classroom_store(classroom_id)
    if store is None:
        return 0
    try:
        got = store.get(where={"doc_id": doc_id})
        ids = list(got.get("ids") or [])
        if not ids:
            return 0
        store.delete(ids=ids)
        return len(ids)
    except Exception as exc:
        logger.warning(
            "课堂切片删除失败 classroom=%s doc=%s：%s", classroom_id, doc_id, exc
        )
        return 0


def purge_classroom(classroom_id: int) -> dict:
    """连根清掉一个课堂的资料（collection + 原件目录 + 句柄缓存）。

    **只在课堂本身被删时调用**。结课不等于删除（`Classroom` 也没有结课字段）：
    学生结课后回来复习本课讲义是正当需求，删数据是错的。
    """
    cid = int(classroom_id)
    removed = {"collection": False, "files_dir": False}
    try:
        # 目录不存在时不要建它：`PersistentClient` 会顺手 mkdir，
        # 清理动作不该产生副作用（否则"清理完"反而多出一个空向量目录）。
        if CHROMA_DIR.exists():
            import chromadb

            client = chromadb.PersistentClient(path=str(CHROMA_DIR))
            names = {
                getattr(item, "name", item) for item in client.list_collections()
            }
            if classroom_collection_name(cid) in names:
                client.delete_collection(classroom_collection_name(cid))
                removed["collection"] = True
    except Exception as exc:
        logger.warning("课堂 collection 清理失败 classroom=%s：%s", cid, exc)
    finally:
        # 无论 collection 是否删成功都要清句柄：留着等于握着可能已悬空的引用。
        invalidate(cid)

    target = classroom_dir(cid)
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
        removed["files_dir"] = True
    return removed


def list_storage_classroom_ids() -> set[int]:
    """磁盘上有资料的课堂 id（原件目录名就是 id），清理脚本对账用。"""
    if not DOCS_DIR.exists():
        return set()
    ids = set()
    for child in DOCS_DIR.iterdir():
        if child.is_dir() and child.name.isdigit():
            ids.add(int(child.name))
    return ids


def list_storage_collection_ids() -> set[int]:
    """向量目录里存在课堂 collection 的课堂 id。

    与 `list_storage_classroom_ids()` 分开查：原件目录和 collection 是两份
    独立状态，任一侧残留都会让「下一个同 id 课堂」拿到上一位老师的数据。
    """
    if not CHROMA_DIR.exists():
        return set()
    try:
        import chromadb

        client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        ids = set()
        for item in client.list_collections():
            name = getattr(item, "name", item)
            match = re.fullmatch(r"classroom_(\d+)_docs", str(name))
            if match:
                ids.add(int(match.group(1)))
        return ids
    except Exception as exc:
        logger.warning("课堂 collection 列举失败：%s", exc)
        return set()


def retrieve_classroom_docs(
    classroom_id: int,
    query: str,
    k: int = 3,
    trace: dict | None = None,
) -> list[Document]:
    """课堂资料检索（纯向量 + 距离门限），返回去重后的切片。

    为什么**不**走共享库那条完整链路（向量 + BM25 + RRF + 重排 + floor）：
    1. `retrieve_knowledge_docs()` 是单库签名，并行召回要调两次各自重排，
       重排成本 ×2（约 2.2s → 4.4s），直接击穿 p95 ≤5s；
    2. `_BM25_CACHE` 按 collection 名做键，每个课堂首次检索都要现建一次
       BM25 语料（jieba 分词是瓶颈），课堂一多就是「偶发极慢」；
    3. `relevance_floor` 是按教材**重排 logits** 定的（-2.0），课堂库这条
       路没有 logits，共用会张冠李戴。
    课堂资料通常只有几十~几百片，纯向量召回够用。升级触发条件（写进提案）：
    单课堂片数 >2000 或实测命中率不足，再评估预建 BM25 + 重排。

    **门限是必须的**（2026-10-26 实测缺陷）：原先纯 top-k 无任何过滤，
    学生问「今天天气怎么样？」也会硬塞 3 片课文进上下文。现在按向量距离
    卡一道（见 `_max_distance()` 的实测数据），最高距离都不达标就整题不注入
    ——与共享库「整题判定」的口径一致，不做逐片裁剪（会误伤 follow-up）。

    **任何异常都返回空列表**：课堂库是增强项，它坏了不该让 /chat 500。
    """
    try:
        store = get_classroom_store(classroom_id)
        if store is None:
            if trace is not None:
                trace["classroom"] = "no_collection"
            return []
        pairs = store.similarity_search_with_score(query, k=k)
        limit = _max_distance()
        top = float(pairs[0][1]) if pairs else None
        if trace is not None:
            trace["classroom_candidates"] = len(pairs)
            trace["classroom_top_distance"] = round(top, 4) if top is not None else None
            trace["classroom_max_distance"] = limit
        if limit is not None and top is not None and top > limit:
            if trace is not None:
                trace["classroom"] = "filtered_by_distance"
            logger.info(
                "课堂资料最高距离 %.3f 超过门限 %.2f，本轮不注入 classroom=%s",
                top,
                limit,
                classroom_id,
            )
            return []
        docs = [doc for doc, score in pairs if limit is None or float(score) <= limit]
        # 与 retrieve_pdf_docs 同款特判：问「这篇论文的作者/摘要」时要带首页。
        if METADATA_QUERY.search(query):
            front_matter = store.similarity_search(
                query, k=1, filter={"section": "front_matter"}
            )
            docs = front_matter + docs
        # 键必须带 doc_id：同一课堂多份 PDF 都有「第 1 页第 1 行」，
        # 不带 doc_id 会把第二份以后的内容整段当重复项丢掉（静默召回缺失）。
        deduped = _dedupe(docs, ("doc_id", "page_number", "line_start"))
        if trace is not None:
            trace["classroom"] = f"injected:{len(deduped)}"
        return deduped
    except Exception as exc:
        logger.warning("课堂资料检索失败 classroom=%s：%s", classroom_id, exc)
        if trace is not None:
            trace["classroom"] = f"error:{type(exc).__name__}"
        return []
