"""切片质量诊断（PROPOSAL_RAG_QUALITY_EVAL §3.4 / Q4，Wave 7）。

**定位**：诊断工具，不进 CI。`build_kb.py` 重建库后跑一次，报告归档，
用来回答「这片切片是不是万金油 / 是不是被切碎了」，供人工决定要不要调
切片策略（修法属 PROPOSAL_RAG_SCALE 范畴，本工具只量化）。

**诊断粒度必须跟随「实际注入的单元」**（2026-10-25 扩库后新增）：
父子库（§4.4）里向量库存的是 child（约 200 字），但 `/chat` 注入的是
聚合后的 parent（整节 600~900 字）。诊断对象若停在 child，量出来的是
「检索用的碎块质量」，而线上暴露的问题出在「注入用的整节」——两份报告
对不上号，读的人会以为切片很干净而注入很脏。因此粒度按库形态自动选：

    metadata 有 parent_id → parent 粒度（默认）：诊断聚合后的整节
    否则                  → child 粒度：诊断库内切片本身（改造前行为）

parent 粒度下三处口径必须显式说明，否则数字会被误读：

1. **向量**：parent 没有自己的向量（库里只有 child 的）。这里对 parent 正文
   **重新 embed**，而不是拿 child 向量取均值——均值是"多个子话题的重心"，
   算出来的独立性会系统性偏高，看着漂亮但不对应任何真实检索。
2. **综合度**：检索返回的是 child，命中后按 parent_id 映射回收敛到 parent
   坐标再统计；同一 parent 的多个 child 同时进 top3 会自然合并成一条。
3. **完整度**：`heading_orphan`（片尾挂一个孤立标题）在 parent 上**不适用**——
   parent 以 `## 标题` 开头、以正文结束，结构上不可能标题悬空。换用
   `empty_body`（去掉标题行后正文过短 = 只有标题没有内容）。
   `mid_sentence_end` 仍适用：parent 结尾应接下一节标题（换行）或文件末尾。

三个指标（口径固定，报告里同名词同含义）：

1. **语义独立性 independence**：对每片，算它与同 topic 其他片的余弦距离
   均值、与跨 topic 片的距离均值，比值 cross/same 越大越好
   （理想形态：片内聚、片间散）。比值 < 1 说明该片和别的主题贴得比
   和自己主题还近，检索时容易被隔壁主题抢走。单主题库（无跨片）为 None。

2. **综合度 generalist**：对每片，统计「期望主题各不相同的评测 query 里，
   有多少个不同主题的 query 把它带进过 top3」。出现在 ≥3 个不同主题的
   top3 = 综合型 chunk（万金油黑洞），标记出来人工复核。
   query 集复用 rag_eval_cases.json（83 条、期望主题已知、零 LLM 成本）；
   检索用纯向量 similarity_search(k=3)——这里量的是「这片内容本身像不像
   万金油」，与生产链路（重排后注入）口径不同，报告里不与 L1 指标混算。
   评测集缺失时退化为「每个 topic 名当一个 query」。

3. **切片完整度 completeness**：句号/分号密度 + 源文件回读检测切断处。
   - density：每百字的「。；」数，低 → 长段落被切成碎句。
   - heading_orphan：片尾最后一行是 Markdown 标题（### xxx）且后面没有
     正文——标题和它的内容被切开了，注入模型后是一句"空头支票"。
     （parent 粒度下不适用，恒为 False 且不计入统计，见上。）
   - empty_body：parent 去掉标题行后正文过短，等于只注入了一个小节名。
   - mid_sentence_end：对照源文件，片尾下一个字符是正文（非空白、非句读），
     即这片是在句子中间被切断的。
   - 切片开头落在句子中间**不算缺陷**：splitter 的 chunk_overlap=80 是
     有意让切片重叠，开头承接上文是设计行为（此处显式声明，避免报告里
     出现一堆假警报）。

输出：`eval/reports/chunk_quality_{fingerprint}_{granularity}.json`
（fingerprint 与 eval/fingerprint.py 同源，库重建后指纹变化，报告自然分版本归档；
文件名带粒度，两种口径的报告不会互相覆盖）。

用法（在 backend/ 下）：
    python -m eval.chunk_quality                  # 按库形态自动选粒度，写报告
    python -m eval.chunk_quality --top 10         # 只打印最值得看的 10 条
    python -m eval.chunk_quality --granularity child   # 强制诊断库内切片本身
    python -m eval.chunk_quality --granularity parent  # 强制诊断聚合后的整节
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from agent.rag.indexer import load_knowledge_index  # noqa: E402
from eval.fingerprint import collection_fingerprint  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CASES = BACKEND_DIR / "rag_eval_cases.json"
DEFAULT_KNOWLEDGE_DIR = BACKEND_DIR.parents[1] / "knowledge"
DEFAULT_OUT_DIR = Path(__file__).resolve().parent / "reports"
COLLECTION = "cs_basics"
PERSIST_DIR = str(BACKEND_DIR / "chroma_kb")

GENERALIST_TOPIC_THRESHOLD = 3  # 出现在 ≥3 个不同主题的 top3 → 综合型
TOP3 = 3

# 诊断粒度（与 build_gold.py 的粒度常量同口径）
GRANULARITY_AUTO = "auto"
GRANULARITY_CHILD = "child"
GRANULARITY_PARENT = "parent"
GRANULARITIES = (GRANULARITY_AUTO, GRANULARITY_CHILD, GRANULARITY_PARENT)

# parent 去掉标题行后正文少于这个字符数 = 只有小节名、没有内容
BODY_MIN_CHARS = 20

# 句读终止符：完整度判定用（中文场景，不含英文句点——知识文档是中文八股）
SENTENCE_TERMINATORS = "。；！？"


# ---------------------------------------------------------------------------
# 纯函数（有单测，零 IO）
# ---------------------------------------------------------------------------

def cosine_distance(a, b) -> float:
    """余弦距离 1 - cos(a,b)。零向量返回 1.0（无方向，视为最不相似）。"""
    import math

    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 1.0
    return 1.0 - dot / (na * nb)


def independence_ratio(same_topic_dists: list[float], cross_topic_dists: list[float]) -> float | None:
    """语义独立性 = 跨主题平均距离 / 同主题平均距离，越大越好。

    分母为 0（同主题只有自己 / 完全重复切片）或任一侧为空 → None（不可判）。
    """
    if not same_topic_dists or not cross_topic_dists:
        return None
    same = statistics.fmean(same_topic_dists)
    if same <= 1e-9:
        return None
    return round(statistics.fmean(cross_topic_dists) / same, 4)


def sentence_density(text: str) -> float:
    """每百字的句号/分号密度（只数中文句读 。；）。空文本返回 0。"""
    if not text:
        return 0.0
    hits = text.count("。") + text.count("；")
    return round(hits * 100.0 / len(text), 2)


def heading_orphan(text: str) -> bool:
    """片尾最后一行（忽略空行）是否是 Markdown 标题且后面没有正文。"""
    lines = [ln.strip() for ln in text.rstrip().splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    return bool(lines) and lines[-1].startswith("#")


def detect_granularity(metas) -> str:
    """按库里 metadata 的实际形态判断粒度：有 parent_id 就是父子库。

    与 eval/build_gold.detect_granularity 同口径（本模块被单测用 importlib
    独立加载，不 import build_gold，避免把 dotenv/路径副作用带进测试）。
    是否父子库只看库本身，不看环境变量——诊断对象由库决定。
    """
    for meta in metas or []:
        if (meta or {}).get("parent_id"):
            return GRANULARITY_PARENT
    return GRANULARITY_CHILD


def body_without_heading(text: str) -> str:
    """去掉开头的小节标题行（`## xxx`）后的正文。

    只去一行：parent 结构固定是「一行 `## 标题` + 正文」，
    多去会把正文里的小标题也当标题删掉。
    """
    lines = (text or "").splitlines()
    start = 1 if lines and lines[0].lstrip().startswith("#") else 0
    return "\n".join(lines[start:]).strip()


def empty_body(text: str, min_chars: int = BODY_MIN_CHARS) -> bool:
    """parent 是否"只有标题没有正文"。

    parent 粒度下 `heading_orphan` 结构上不可能成立（parent 以标题开头、
    以正文结尾），真正该防的反面是：某个 `## 小节` 下面压根没写内容，
    注入模型后相当于只给了一个小节名——这是 parent 版的"空头支票"。
    """
    return len(body_without_heading(text)) < min_chars


def locate_chunk(source_text: str, start_index: int, chunk_text: str) -> tuple[int, bool]:
    """在源文件里定位切片，返回 (实际起点, 偏移是否可信)。

    先信 start_index；对不上（切片器会 strip 分隔符，个别边界会漂移）
    就按前 30 个字符回搜；再搜不到返回 (start_index, False)，
    调用方对偏移不可信的片跳过"源文件对照"类检查，不硬判。
    """
    start = max(int(start_index or 0), 0)
    if source_text[start:start + len(chunk_text)] == chunk_text:
        return start, True
    probe = chunk_text[:30].strip()
    if probe:
        found = source_text.find(probe)
        if found >= 0:
            return found, True
    return start, False


def end_cut_kind(source_text: str, start: int, length: int) -> str:
    """切片结尾形态：'eof'（源文件到头）/ 'ok'（句读或空白收尾）/ 'mid_sentence'。

    只在偏移可信时调用；判定对象是**源文件里紧跟切片的下一个字符**，
    不看切片自身最后字符——切片器可能把尾部空白 strip 掉，那不代表切断。
    """
    nxt = start + length
    if nxt >= len(source_text):
        return "eof"
    ch = source_text[nxt]
    if ch.isspace() or ch in SENTENCE_TERMINATORS:
        return "ok"
    return "mid_sentence"


def generalist_from_hits(hit_topics_per_chunk: dict[str, set[str]], threshold: int) -> dict[str, list[str]]:
    """综合度聚合：每片出现过它的「不同期望主题」集合 → 筛出综合型。

    hit_topics_per_chunk: 片标识 → 把它带进 top3 的 query 的期望主题集合。
    纯函数；≥ threshold 个不同主题即综合型。
    """
    flagged = {
        cid: sorted(topics)
        for cid, topics in hit_topics_per_chunk.items()
        if len(topics) >= threshold
    }
    return dict(sorted(flagged.items(), key=lambda kv: -len(kv[1])))


# ---------------------------------------------------------------------------
# 主流程（IO 层，不进单测）
# ---------------------------------------------------------------------------

def load_queries(cases_file: Path, topics: list[str]) -> list[tuple[str, str]]:
    """诊断用 query 集：评测集 (query, expected 主题)；缺失则退化为 topic 名。

    返回 [(query, 期望主题)]。expected 按 norm 归一化（去空格 + 转小写）——
    评测集里 "TCP 与 UDP" / "tcp 与 udp" 两种写法并存，不归一化会把同一个
    主题数成两个，综合片的主题数被系统性虚高（与 smoke_rag.top_hit 同一把尺子）。
    评测集里 expected 为空的（oos 库外题）跳过——它们没有"期望主题"，
    参与综合度统计只会引入噪声。
    """
    if cases_file.exists():
        data = json.loads(cases_file.read_text(encoding="utf-8"))
        pairs = []
        for item in data.get("cases", []):
            expected = item.get("expected") or []
            if expected:
                pairs.append(
                    ((item.get("query") or "").strip(), norm_topic(str(expected[0])))
                )
        pairs = [(q, t) for q, t in pairs if q]
        if pairs:
            return pairs
    return [(t, t) for t in topics if t]


def norm_topic(s: str) -> str:
    """topic 归一化：去空格、转小写（与 smoke_rag.norm 同口径）。"""
    return s.replace(" ", "").lower()


def load_units(store, granularity: str = GRANULARITY_CHILD) -> dict:
    """取诊断单元：正文 / 坐标 / topic / 向量，三项指标共用这一层。

    child 粒度直接用库里的切片与它的向量（改造前的口径，逐字不变）。
    parent 粒度先用 `aggregate_parent_docs` 聚合（与 /chat 注入同一份代码），
    再对 parent 正文**重新 embed**——库里没有 parent 向量，而"取 child 向量
    均值"算出来的独立性会系统性偏高（均值是多个子话题的重心，与谁都不够像），
    漂亮但不对应任何真实检索。

    返回的 `child_to_unit` 用于综合度映射：检索始终返回 child，
    parent 粒度下要把命中收敛回所属小节再统计主题数。
    """
    import numpy as np

    raw = store.get(include=["documents", "metadatas", "embeddings"])
    docs = raw.get("documents") or []
    metas = raw.get("metadatas") or []
    # embeddings 可能是 ndarray：不能写 `or []`（对多元素数组会抛歧义 ValueError）
    embs = raw.get("embeddings")
    if embs is None:
        raise SystemExit("向量库取不到 embeddings：chromadb 版本可能不支持 include=embeddings")
    if not docs:
        raise SystemExit("向量库是空的：先跑 python build_kb.py")
    if isinstance(embs, np.ndarray):
        embs = embs.tolist()

    child_count = len(docs)
    if granularity != GRANULARITY_PARENT:
        return {
            "granularity": GRANULARITY_CHILD,
            "texts": [t or "" for t in docs],
            "keys": [_chunk_id(m) for m in metas],
            "topics": [str((m or {}).get("topic") or "") for m in metas],
            "vecs": [np.asarray(e, dtype=float) for e in embs],
            "child_count": child_count,
            "parent_count": None,
            # child 粒度下检索结果就是诊断单元本身，映射为恒等
            "child_to_unit": {_chunk_id(m): _chunk_id(m) for m in metas},
        }

    from langchain_core.documents import Document

    from agent.rag.indexer import get_embeddings
    from agent.rag.retriever import aggregate_parent_docs

    parents = aggregate_parent_docs(
        [Document(page_content=t or "", metadata=m or {}) for t, m in zip(docs, metas)]
    )
    texts = [p.page_content for p in parents]
    parent_vecs = get_embeddings().embed_documents(texts)

    child_to_unit = {}
    for meta in metas:
        meta = meta or {}
        key = _chunk_id(meta)
        if meta.get("parent_id"):
            child_to_unit[key] = (
                str(meta.get("source") or ""),
                int(meta.get("parent_start") or 0),
            )
        else:
            # 混库里的扁平片：没有父小节，自己就是一个单元
            child_to_unit[key] = key

    return {
        "granularity": GRANULARITY_PARENT,
        "texts": texts,
        "keys": [_chunk_id(p.metadata) for p in parents],
        "topics": [str((p.metadata or {}).get("topic") or "") for p in parents],
        "vecs": [np.asarray(v, dtype=float) for v in parent_vecs],
        "child_count": child_count,
        "parent_count": len(parents),
        "child_to_unit": child_to_unit,
    }


def run_diagnosis(
    store,
    cases_file: Path,
    knowledge_dir: Path,
    granularity: str = GRANULARITY_AUTO,
) -> dict:
    """跑三项诊断，返回报告 dict（不落盘，由 main 写文件）。"""
    raw_metas = (store.get(include=["metadatas"]) or {}).get("metadatas") or []
    detected = detect_granularity(raw_metas)
    if granularity == GRANULARITY_AUTO:
        granularity = detected
    elif granularity != detected:
        # 允许显式指定，但必须说清后果：库是父子结构却按 child 诊断，
        # 报告描述的对象与线上注入的对象不是同一个。
        print(
            "! 显式指定粒度 %s，而库里实际是 %s（由 metadata 是否有 parent_id 决定）"
            % (granularity, detected)
        )

    units = load_units(store, granularity)
    import numpy as np

    texts = units["texts"]
    keys = units["keys"]
    topics = units["topics"]
    vecs = units["vecs"]
    n = len(texts)
    is_parent = units["granularity"] == GRANULARITY_PARENT

    fingerprint = collection_fingerprint(store)

    # ---- 预计算：同一片与其它片的余弦距离，按同/跨主题分桶 ----
    mat = np.asarray(vecs)  # (n, dim)
    norms = np.linalg.norm(mat, axis=1)
    norms[norms == 0.0] = 1.0  # 零向量：点积为 0，除 1 得 cos=0 → 距离 1（与纯函数口径一致）
    sim = (mat @ mat.T) / np.outer(norms, norms)
    dist = 1.0 - sim
    dist_same: list[list[float]] = [[] for _ in range(n)]
    dist_cross: list[list[float]] = [[] for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            (dist_same[i] if topics[j] == topics[i] else dist_cross[i]).append(float(dist[i][j]))

    # ---- 指标 1：语义独立性 ----
    indep_rows = []
    for i in range(n):
        ratio = independence_ratio(dist_same[i], dist_cross[i])
        indep_rows.append(
            {
                "chunk": keys[i],
                "topic": topics[i],
                "independence": ratio,
                "same_topic_mean_dist": round(statistics.fmean(dist_same[i]), 4) if dist_same[i] else None,
                "cross_topic_mean_dist": round(statistics.fmean(dist_cross[i]), 4) if dist_cross[i] else None,
            }
        )
    ratios = [r["independence"] for r in indep_rows if r["independence"] is not None]
    independence_summary = {
        "median": round(statistics.median(ratios), 4) if ratios else None,
        "min": min(ratios) if ratios else None,
        "max": max(ratios) if ratios else None,
        "below_1": sum(1 for r in ratios if r < 1.0),
        "note": "比值 = 跨主题平均距离 / 同主题平均距离，越大越好；<1 的片与隔壁主题贴得比同主题还近",
    }

    # ---- 指标 2：综合度 ----
    # 检索恒返回 child；parent 粒度下按 parent_id 收敛回所属小节再统计主题数。
    # 同一 parent 的多个 child 同时进 top3 会合并成一条——这是真实情况
    # （纯向量检索本来就容易一窝端同一小节），不是统计口径的缺陷。
    child_to_unit = units["child_to_unit"]
    queries = load_queries(cases_file, sorted(set(topics)))
    hit_topics: dict[str, set[str]] = {}
    for query, expected_topic in queries:
        for doc in store.similarity_search(query, k=TOP3):
            cid = _chunk_id(doc.metadata)
            hit_topics.setdefault(child_to_unit.get(cid, cid), set()).add(expected_topic)
    generalists = generalist_from_hits(hit_topics, GENERALIST_TOPIC_THRESHOLD)
    generalist_summary = {
        "queries_used": len(queries),
        "top_k": TOP3,
        "threshold": GENERALIST_TOPIC_THRESHOLD,
        "flagged_count": len(generalists),
        # tuple 键转 [chunk, topics] 数组对：JSON 对象键必须是字符串
        "flagged": [{"chunk": list(cid), "topics": ts} for cid, ts in generalists.items()],
        "note": "出现在 ≥3 个不同期望主题 query 的 top3 = 综合型 chunk（万金油），建议人工拆分；"
                "口径是纯向量检索，与生产重排链路不同，不与 L1 指标混算",
    }

    # ---- 指标 3：切片完整度（源文件回读）----
    source_cache: dict[str, str] = {}
    completeness_rows = []
    for i in range(n):
        key = keys[i]
        text = texts[i] or ""
        rel_source = str(key[0] or "")
        row = {
            "chunk": key,
            "topic": topics[i],
            "density_per_100": sentence_density(text),
            # parent 以 `## 标题` 开头、以正文结尾，结构上不可能标题悬空 →
            # 该维度在 parent 粒度不适用，恒 False 且不计入 summary（用 None 区分
            # "没缺陷"和"没测"）。换用 empty_body 抓 parent 版空头支票。
            "heading_orphan": False if is_parent else heading_orphan(text),
            "empty_body": empty_body(text) if is_parent else None,
            "mid_sentence_end": None,  # 偏移不可信时保持 None，不假装判过
            "offset_trusted": False,
        }
        if rel_source:
            if rel_source not in source_cache:
                path = knowledge_dir / rel_source
                source_cache[rel_source] = path.read_text(encoding="utf-8") if path.exists() else ""
            src = source_cache[rel_source]
            if src:
                start, trusted = locate_chunk(src, int(key[1]), text)
                row["offset_trusted"] = trusted
                if trusted:
                    row["mid_sentence_end"] = end_cut_kind(src, start, len(text))
        completeness_rows.append(row)
    completeness_summary = {
        "count": n,
        "heading_orphan": None if is_parent else sum(1 for r in completeness_rows if r["heading_orphan"]),
        "empty_body": sum(1 for r in completeness_rows if r.get("empty_body")) if is_parent else None,
        "mid_sentence_end": sum(1 for r in completeness_rows if r["mid_sentence_end"] == "mid_sentence"),
        "offset_untrusted": sum(1 for r in completeness_rows if not r["offset_trusted"]),
        "density_min": min((r["density_per_100"] for r in completeness_rows), default=0.0),
        "density_median": statistics.median([r["density_per_100"] for r in completeness_rows]) if completeness_rows else 0.0,
        "note": "切片开头落在句中不算缺陷（chunk_overlap=80 的有意重叠）；mid_sentence_end 只看结尾；"
                "heading_orphan / empty_body 互斥：child 粒度看前者，parent 粒度看后者（另一个为 null = 不适用）",
    }

    return {
        "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S%z"),
        "granularity": units["granularity"],
        "detected_granularity": detected,
        "index_fingerprint": fingerprint,
        "chunks_count": n,
        "child_count": units["child_count"],
        "parent_count": units["parent_count"],
        "topics": {t: topics.count(t) for t in sorted(set(topics))},
        "independence": {"summary": independence_summary, "per_chunk": indep_rows},
        "generalist": generalist_summary,
        "completeness": {"summary": completeness_summary, "per_chunk": completeness_rows},
    }


def _chunk_id(meta: dict) -> tuple:
    """片标识 (source, start_index)，与评测集 relevant_chunks 的二元组同形。

    返回 tuple 而不是 list：它要当综合度统计的 dict 键用（list 不可哈希），
    JSON 序列化时 tuple 自动变数组，报告形态不受影响。
    """
    return (str((meta or {}).get("source") or ""), int((meta or {}).get("start_index") or 0))


def print_digest(report: dict, top: int) -> None:
    """终端摘要：只打最值得人工看的部分，全文看 JSON。"""
    gran = report.get("granularity") or GRANULARITY_CHILD
    scope = (
        "%d 个 parent（由 %d 个 child 聚合）" % (report["chunks_count"], report.get("child_count") or 0)
        if gran == GRANULARITY_PARENT
        else "%d 个切片" % report["chunks_count"]
    )
    print("粒度 %s：诊断 %s  指纹 %s" % (gran, scope, report["index_fingerprint"]))
    ind = report["independence"]["summary"]
    print(
        "\n[语义独立性] 中位 %s  min %s  max %s  <1 的片 %d 个"
        % (ind["median"], ind["min"], ind["max"], ind["below_1"])
    )
    worst = sorted(
        (r for r in report["independence"]["per_chunk"] if r["independence"] is not None),
        key=lambda r: r["independence"],
    )[:top]
    for r in worst:
        print("  %.3f  [%s] %s" % (r["independence"], r["topic"], r["chunk"]))

    gen = report["generalist"]
    print("\n[综合度] query %d 条 → 综合型 %d 片（≥%d 个主题的 top3）"
          % (gen["queries_used"], gen["flagged_count"], gen["threshold"]))
    for item in gen["flagged"][:top]:
        print("  %s ← %s" % (item["chunk"], "、".join(item["topics"])))

    comp = report["completeness"]["summary"]
    if gran == GRANULARITY_PARENT:
        struct = "空正文 %s（标题悬空对 parent 不适用）" % comp["empty_body"]
    else:
        struct = "标题悬空 %s" % comp["heading_orphan"]
    print(
        "\n[完整度] %s  句中截断 %d  偏移不可信 %d  句读密度中位 %.2f/百字"
        % (struct, comp["mid_sentence_end"], comp["offset_untrusted"], comp["density_median"])
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="切片质量诊断（零 LLM，不进 CI）")
    parser.add_argument("--cases", default=str(DEFAULT_CASES), help="评测集路径（综合度 query 来源）")
    parser.add_argument("--knowledge-dir", default=str(DEFAULT_KNOWLEDGE_DIR), help="知识文档根目录（完整度回读用）")
    parser.add_argument("--out", default="", help="报告输出路径（缺省 eval/reports/chunk_quality_{指纹}_{粒度}.json）")
    parser.add_argument("--top", type=int, default=10, help="终端摘要各榜单显示条数")
    parser.add_argument(
        "--granularity",
        default=GRANULARITY_AUTO,
        choices=GRANULARITIES,
        help="诊断粒度：auto（按库形态，默认）/ child（库内切片）/ parent（聚合后的整节）",
    )
    args = parser.parse_args(argv)

    store = load_knowledge_index(COLLECTION, PERSIST_DIR)
    if store is None:
        raise SystemExit("知识库不存在：先跑 python build_kb.py")

    report = run_diagnosis(store, Path(args.cases), Path(args.knowledge_dir), args.granularity)

    out_path = Path(args.out) if args.out else DEFAULT_OUT_DIR / (
        "chunk_quality_%s_%s.json"
        % (report["index_fingerprint"].replace("sha256:", ""), report["granularity"])
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print_digest(report, args.top)
    print("\n报告已写入：%s" % out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
