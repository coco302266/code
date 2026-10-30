"""索引版本指纹（PROPOSAL_RAG_QUALITY_EVAL 的 Q2）。

**解决什么问题**：`build_kb.py` 重建知识库后，旧基线 `rag_baseline.json` 可能对不上
新库的切片集。库变了但基线没更新，`--compare` 的退化检测会**假阴**——
报告说"无退化"，实际比的根本不是同一个库。

指纹只覆盖切片身份与正文，不含 embedding：
    sha256( 排序后的 (source, start_index, page_content) 三元组 )
embedding 模型版本另记在 baseline_meta.json 的 embed_model 字段，
换模型不该和"改了文档"混成同一件事。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
META_PATH = BACKEND_DIR / "eval" / "baseline_meta.json"


def compute_fingerprint(chunks) -> str:
    """对切片集算 sha256，作为索引版本号。

    Args:
        chunks: 任意带 `.metadata` 与 `.page_content` 的对象序列（Document）。
    """
    rows = []
    for chunk in chunks:
        meta = getattr(chunk, "metadata", {}) or {}
        rows.append(
            (
                str(meta.get("source") or ""),
                str(meta.get("start_index") or ""),
                str(getattr(chunk, "page_content", "") or ""),
            )
        )
    digest = hashlib.sha256()
    for row in sorted(rows):
        digest.update("\x1f".join(row).encode("utf-8"))
        digest.update("\x1e".encode("utf-8"))
    return "sha256:" + digest.hexdigest()[:32]


def collection_fingerprint(vectorstore) -> str:
    """从已持久化的 collection 直接算指纹（不必重新切分文档）。"""
    raw = vectorstore.get()
    return compute_fingerprint(
        [
            type("_C", (), {"metadata": meta or {}, "page_content": text or ""})()
            for text, meta in zip(raw.get("documents") or [], raw.get("metadatas") or [])
        ]
    )


def save_baseline_meta(
    fingerprint: str,
    chunks_count: int,
    embed_model: str,
    cases_count: int = 0,
    extra: dict | None = None,
    path: str | Path = "",
) -> Path:
    """写 eval/baseline_meta.json（build_kb.py 每次重建后调一次）。"""
    target = Path(path) if path else META_PATH
    payload = {
        "index_fingerprint": fingerprint,
        "embed_model": embed_model,
        "chunks_count": chunks_count,
        "eval_cases_count": cases_count,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    if extra:
        payload.update(extra)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def load_baseline_meta(path: str | Path = "") -> dict:
    """读 baseline_meta.json；不存在返回空 dict（调用方按"指纹未知"处理）。"""
    target = Path(path) if path else META_PATH
    if not target.exists():
        return {}
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def check_fingerprint(current: str, baseline_fp: str) -> tuple[bool, str]:
    """比对指纹，返回 (是否一致, 说明)。

    两端只要有一端没有指纹就**不阻断**，只给警告：
    跨库对比实验（换切片策略）时指纹必然不同，硬拦会挡掉正常的实验流程。
    """
    if not baseline_fp:
        return True, "基线未记录指纹，跳过指纹校验（建议重存基线）"
    if not current:
        return True, "当前库指纹取不到，跳过指纹校验"
    if current == baseline_fp:
        return True, "索引指纹一致"
    return False, "索引指纹不一致（库已变更，旧基线不可比，请 --save-base 重存）"
