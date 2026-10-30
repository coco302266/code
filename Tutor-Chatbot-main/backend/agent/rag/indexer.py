import hashlib
import json
import os
import re
from functools import lru_cache
from pathlib import Path

# 模型下载环境变量的设置必须早于 fastembed / huggingface_hub 的导入（原因见 _env.py）。
# 抽成独立模块是因为加重排后 retriever.py 也要用 fastembed，两处都得保证这个顺序。
from . import _env  # noqa: F401  isort: skip

from langchain_community.embeddings import FastEmbedEmbeddings
from langchain_chroma import Chroma
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import PyPDFLoader
from langchain_core.documents import Document


# 中文八股知识库必须用中文 embedding：原项目的 bge-small-en-v1.5 是英文模型，
# 对中文语义的区分能力很差，检索会召回一堆不相关的片段。
EMBED_MODEL = os.getenv("EMBED_MODEL", "BAAI/bge-small-zh-v1.5")

SUBJECT = "计算机八股"

CATEGORY_NAMES = {
    "os": "操作系统",
    "network": "计算机网络",
    "cpp": "C/C++",
    "embedded": "嵌入式",
    "dsa": "数据结构与算法",
    "db": "数据库",
}


@lru_cache(maxsize=1)
def get_embeddings() -> FastEmbedEmbeddings:
    return FastEmbedEmbeddings(model_name=EMBED_MODEL)


# 单层（扁平）切片参数。提为模块常量而非写在函数里，是为了让
# chunk_params_signature() 与切片器读同一个值——两处漂移会让「切片参数变了
# 要不要全量重建」的判断失真（RAG_HANDBOOK §8 增量方案限制 3）。
FLAT_CHUNK_SIZE = int(os.getenv("RAG_CHUNK_SIZE", "500"))
FLAT_CHUNK_OVERLAP = int(os.getenv("RAG_CHUNK_OVERLAP", "80"))


def get_markdown_splitter() -> RecursiveCharacterTextSplitter:
    """中文友好的切分：优先按 Markdown 标题切，再按段落，最后按句号。"""
    return RecursiveCharacterTextSplitter(
        chunk_size=FLAT_CHUNK_SIZE,
        chunk_overlap=FLAT_CHUNK_OVERLAP,
        add_start_index=True,
        separators=["\n## ", "\n### ", "\n\n", "\n", "。", "；", ""],
    )


# ---------------- 父子切片（PROPOSAL_RAG_SCALE §4.4，opt-in）----------------
#
# 扩库后的切片策略：child（约 200 字）用于检索，向量更聚焦；parent（完整
# 二级小节 `##`，约 600~900 字）用于注入，上下文不断裂。命中 child 后按
# parent_id 聚合去重、返回 parent 文本。
#
# 默认**关闭**：当前 66 片库未到扩库触发条件（RAG_HANDBOOK §2：>15 篇或
# >150 片），打开会改变注入粒度与相关性下限口径，必须重建库并重刷基线。

H2_HEADING_RE = re.compile(r"(?m)^##\s+(.+?)\s*$")


def parent_child_enabled() -> bool:
    """父子切片是否启用（每次读环境变量，便于测试与构建脚本临时切换）。"""
    return os.getenv("RAG_PARENT_CHILD", "0").strip() == "1"


def get_child_splitter() -> RecursiveCharacterTextSplitter:
    """child 切片器：小块检索用，默认 200/40（§4.4 的「约 200 字」）。

    与单层 500/80 的区别不只是更小：分隔符优先 `###` 与空行，目的是让每个
    child 只表达「一件事」，避免 D8「综合型 chunk 成为万金油」。
    """
    return RecursiveCharacterTextSplitter(
        chunk_size=int(os.getenv("RAG_CHILD_CHUNK_SIZE", "200")),
        chunk_overlap=int(os.getenv("RAG_CHILD_CHUNK_OVERLAP", "40")),
        add_start_index=True,
        separators=["\n### ", "\n\n", "\n", "。", "；", ""],
    )


def _first_h1(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _has_body(text: str) -> bool:
    """是否含正文（忽略 Markdown 标题行与空行）。

    用来判断第一个 `##` 之前的内容值不值得单独成 parent：大多数文档那里
    只有一行 `# 标题`，单独留一个只含标题的 parent 是噪音。
    """
    return any(
        line.strip() and not line.lstrip().startswith("#")
        for line in text.splitlines()
    )


def _is_heading_only(text: str) -> bool:
    """整块是否只有 Markdown 标题行（无正文）。"""
    lines = [line for line in text.splitlines() if line.strip()]
    return bool(lines) and all(line.lstrip().startswith("#") for line in lines)


def _merge_short_chunks(parts: list[Document], min_chars: int) -> list[Document]:
    """把「孤立标题」或「短于 min_chars」的碎块并入下一块。

    child 分隔符以 `###` 优先，小节开头那行 `## 标题` 会因此单独成块：
    初版 159 个 child 里有 28 个 <50 字，最小只有 `## 面试常问` 7 字；合并孤立
    标题后仍剩 9 个，都是 `**预防的具体做法**` 这类列表前导粗体标签。这类块
    没有独立语义，检索命中它等于什么都没给模型，标题词还会污染 BM25。
    末尾的碎块并入前一块；整节都是碎块（极端情况）则原样保留，不丢内容。
    """
    merged: list[Document] = []
    pending_text = ""
    pending_start = None
    for part in parts:
        text = part.page_content
        if _is_heading_only(text) or len(text.strip()) < min_chars:
            if pending_start is None:
                pending_start = part.metadata.get("start_index")
            pending_text = (pending_text + "\n" + text).strip()
            continue
        if pending_text:
            part.page_content = (pending_text + "\n" + text).strip()
            if pending_start is not None:
                part.metadata["start_index"] = pending_start
            pending_text, pending_start = "", None
        merged.append(part)

    if pending_text:
        if merged:
            merged[-1].page_content = (
                merged[-1].page_content.rstrip() + "\n" + pending_text
            ).strip()
        else:
            merged.append(parts[-1])
    return merged


def split_markdown_sections(text: str) -> list[tuple[str, str, int]]:
    """按二级标题 `##` 把整篇 Markdown 切成小节。

    返回 [(小节标题, 小节全文, 起始偏移)]：
    - 第一个 `##` 之前若含正文 → 作为「前言」小节保留；只有 H1 标题行则丢弃。
    - 没有 `##` 的文档整篇当一个 parent（标题取 H1）。
    - 文末不丢：最后一节一直取到文末。
    """
    matches = list(H2_HEADING_RE.finditer(text))
    sections: list[tuple[str, str, int]] = []
    if not matches:
        if text.strip():
            sections.append((_first_h1(text) or "全文", text.strip(), 0))
        return sections

    pre = text[: matches[0].start()]
    if _has_body(pre):
        sections.append((_first_h1(pre) or "前言", pre.strip(), 0))

    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        section_text = text[start:end].strip()
        if section_text:
            sections.append((match.group(1).strip(), section_text, start))
    return sections


def build_parent_child_documents(documents: list[Document]) -> list[Document]:
    """把整篇文档切成「parent 小节 + child 切片」两层（§4.4 L2 父子检索）。

    每个 child 的 metadata 在原有 topic/category/source/start_index 之外额外带：
        parent_id    f"{source}#{小节序号}"，聚合去重的主键
        parent_title 二级标题文本
        parent_text  小节全文（注入文本）——内嵌而不另建 collection，
                     单 collection 的简单性优先（§3「单 collection + filter 已够」）
        parent_start 小节在源文件中的起始偏移
    """
    splitter = get_child_splitter()
    children: list[Document] = []
    for doc in documents:
        meta = doc.metadata or {}
        source = str(meta.get("source") or "")
        for index, (title, section_text, section_start) in enumerate(
            split_markdown_sections(doc.page_content)
        ):
            parent_id = "%s#%d" % (source, index)
            section_doc = Document(page_content=section_text, metadata=dict(meta))
            section_children = _merge_short_chunks(
                splitter.split_documents([section_doc]),
                int(os.getenv("RAG_CHILD_MIN_CHARS", "40")),
            )
            for child in section_children:
                # add_start_index 是相对**小节**的，换算成源文件内偏移，
                # 让 chunk_quality / 溯源看到的 start_index 仍是全文件口径。
                relative_start = int(child.metadata.get("start_index") or 0)
                child.metadata.update(
                    parent_id=parent_id,
                    parent_title=title,
                    parent_text=section_text,
                    parent_start=int(section_start),
                    start_index=relative_start + int(section_start),
                )
                children.append(child)
    return children


# ---------------- 增量更新所需的状态字段（RAG_HANDBOOK §8）----------------
#
# 增量更新要回答两个问题：「哪篇源文件变了」「切片参数变了没」。
# 答案全部写进切片 metadata，因此不需要外挂 docstore / RecordManager ——
# knowledge/ 目录遍历即得完整清单，状态跟着库走即可。
#
# 两个字段都**不影响 index_fingerprint**（fingerprint 只用
# source/start_index/page_content，不含 metadata），所以加了它们
# gold_index_fingerprint 校验与 --compare 门禁照常工作。


def file_hash(path: str | Path) -> str:
    """源文件内容的 sha256 前 16 位，作为「这篇文档变没变」的判据。

    读字节而非文本，避免换行符/编码差异造成假变更。
    """
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


def chunk_params_signature(parent_child: bool | None = None) -> dict:
    """当前切片参数的完整快照（改任何一项都必须全量重建，不能走增量）。"""
    if parent_child is None:
        parent_child = parent_child_enabled()
    return {
        "mode": "parent-child" if parent_child else "flat",
        "flat_size": FLAT_CHUNK_SIZE,
        "flat_overlap": FLAT_CHUNK_OVERLAP,
        "child_size": int(os.getenv("RAG_CHILD_CHUNK_SIZE", "200")),
        "child_overlap": int(os.getenv("RAG_CHILD_CHUNK_OVERLAP", "40")),
        "child_min_chars": int(os.getenv("RAG_CHILD_MIN_CHARS", "40")),
    }


def chunk_params_hash(parent_child: bool | None = None) -> str:
    """切片参数快照的哈希，写进切片 metadata 供增量脚本比对。"""
    raw = json.dumps(
        chunk_params_signature(parent_child), sort_keys=True, ensure_ascii=False
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def load_markdown_documents(knowledge_dir: str) -> list[Document]:
    """递归读取目录下所有 .md，按文件生成带 metadata 的 Document。

    metadata 结构（对应 DEVELOPMENT_PLAN T2.3）：
        subject  学科，目前固定「计算机八股」
        category 分类，取自子目录名（os → 操作系统）
        topic    知识点，取自文件的一级标题
        source   相对路径，便于溯源
        doc_hash 源文件内容的 sha256 前 16 位（增量更新判据，见 §8）
    """
    root = Path(knowledge_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"知识目录不存在：{knowledge_dir}")

    documents = []
    for path in sorted(root.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        title = ""
        for line in text.splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break

        relative = path.relative_to(root)
        # knowledge/cs/os/deadlock.md → category 取文件所在目录名「os」
        category_key = (
            relative.parts[-2] if len(relative.parts) >= 2 else ""
        )
        documents.append(
            Document(
                page_content=text,
                metadata={
                    "subject": SUBJECT,
                    "category": CATEGORY_NAMES.get(category_key, category_key),
                    "topic": title or path.stem,
                    "source": str(relative).replace("\\", "/"),
                    "doc_hash": file_hash(path),
                },
            )
        )
    return documents


def build_chunks(
    documents: list[Document], parent_child: bool | None = None
) -> list[Document]:
    """把 Document 列表切成最终入库的切片（全量构建与增量更新共用）。

    抽出来是为了让 `build_kb.py` 与 `scripts/update_kb.py` 走**同一套**切片逻辑：
    两条路径一旦各自实现，切片参数或 metadata 字段稍有差异，增量的产物就会
    与全量不一致，而指纹对比只在极端情况下才暴露，很难查。

    Args:
        parent_child: None（默认）读 `RAG_PARENT_CHILD` 环境变量。
    """
    if parent_child is None:
        parent_child = parent_child_enabled()

    if parent_child:
        chunks = build_parent_child_documents(documents)
    else:
        chunks = get_markdown_splitter().split_documents(documents)

    params_hash = chunk_params_hash(parent_child)
    for chunk in chunks:
        # 让每个切片带上所属知识点，检索结果才能说清"这段讲的是哪个知识点"
        chunk.metadata.setdefault("topic", chunk.metadata.get("topic", ""))
        # 切片参数快照：增量更新据此判断「参数变了必须全量」，不能只看文件内容
        chunk.metadata["chunk_params_hash"] = params_hash
    return chunks


def build_knowledge_index(
    knowledge_dir: str,
    collection_name: str = "cs_basics",
    persist_directory: str | None = None,
    parent_child: bool | None = None,
) -> Chroma:
    """把知识目录构建成常驻向量库。

    与 PDF 上传的会话级索引不同，这个库是跨请求复用的，
    persist_directory 给了就落盘，重启进程不用重建。

    Args:
        parent_child: 父子切片开关。None（默认）读 `RAG_PARENT_CHILD` 环境变量；
            True 用 child(~200 字)/parent(## 小节) 两层结构，False 保持单层 500/80。
            两种结构的 collection 布局不同，切换后必须重建并重刷基线。
    """
    documents = load_markdown_documents(knowledge_dir)
    if not documents:
        raise ValueError(f"知识目录里没有找到 .md 文件：{knowledge_dir}")

    chunks = build_chunks(documents, parent_child)

    kwargs = {"collection_name": collection_name}
    if persist_directory:
        kwargs["persist_directory"] = persist_directory

    return Chroma.from_documents(
        documents=chunks,
        embedding=get_embeddings(),
        **kwargs,
    )


def load_knowledge_index(
    collection_name: str = "cs_basics",
    persist_directory: str = "",
) -> Chroma | None:
    """加载已持久化的知识库；不存在则返回 None（调用方要兜底）。"""
    if not persist_directory or not Path(persist_directory).exists():
        return None
    return Chroma(
        collection_name=collection_name,
        embedding_function=get_embeddings(),
        persist_directory=persist_directory,
    )


def build_index(
    file_path: str,
    collection_name: str,
    persist_directory: str | None = None,
    metadata_extra: dict | None = None,
) -> Chroma:
    """Load PDF, split into chunks, embed and store in memory.

    保留原逻辑：学生上传的 PDF 仍然是会话级的、内存里的临时索引，
    与常驻知识库是两套独立的 collection。

    Args:
        persist_directory: 传了才落盘（课堂资料用，XB-23 方案 B）。
            不传 = 纯内存 Ephemeral——**这是「上传件不污染评测基线」的根因**，
            学生/教师备课素材走默认值，别"顺手"改成落盘。
        metadata_extra: 合并进每一片 metadata 的额外字段。课堂资料用它写入
            `doc_id` / `classroom_id` / `filename` / `source`：
            - `doc_id` 是**同一课堂多份 PDF 的去重键**。不写的话，
              两篇 PDF 的第 1 页第 1 行会被 `_dedupe(("page_number","line_start"))`
              当成同一片丢掉（实测语义：键相同就只留第一次出现的）；
            - `source` 必须覆盖成 `classroom:{id}/{doc_id}_{文件名}`：PyPDFLoader
              默认把临时文件绝对路径写进 source，落盘库一旦重建就指向不存在的路径。
    """

    # PDF load
    loader = PyPDFLoader(file_path)
    documents = loader.load()

    # Split to chunks

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=900,
        chunk_overlap=150,
        add_start_index=True,
    )
    chunks = splitter.split_documents(documents)
    for chunk in chunks:
        page_index = int(chunk.metadata.get("page", 0))
        page_text = documents[page_index].page_content
        start = int(chunk.metadata.get("start_index", 0))

        chunk.metadata.update(
            section="body",
            page_number=page_index + 1,
            line_start=page_text.count("\n", 0, start) + 1,
            line_end=page_text.count("\n", 0, start)
            + chunk.page_content.count("\n")
            + 1,
        )
        # 额外字段最后合并：doc_id / source 必须覆盖 PyPDFLoader 写入的临时路径。
        if metadata_extra:
            chunk.metadata.update(metadata_extra)

    first_page = documents[0].page_content
    front_metadata = {
        "section": "front_matter",
        "page_number": 1,
        "line_start": 1,
        "line_end": first_page.count("\n") + 1,
    }
    # front_matter 也带 doc_id：否则课堂库检索时它无法归属到具体某一份 PDF，
    # 去重键缺一维就会跨文档合并。
    if metadata_extra:
        front_metadata.update(metadata_extra)
    front_matter = Document(
        page_content=(
            "DOCUMENT FRONT MATTER: title, authors, affiliations and abstract.\n\n"
            + first_page
        ),
        metadata=front_metadata,
    )

    chunks.append(front_matter)
    # Embed and store in memory（传了 persist_directory 才落盘，见 docstring）
    kwargs: dict = {}
    if persist_directory:
        kwargs["persist_directory"] = persist_directory
    vectorstore = Chroma.from_documents(
        documents=chunks,
        embedding=get_embeddings(),
        collection_name=collection_name,
        **kwargs,
    )
    return vectorstore
