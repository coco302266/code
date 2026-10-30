"""本地 ONNX 交叉编码器：绕开 fastembed 对 HuggingFace 元数据的联网依赖。

为什么需要它
------------
fastembed 的 `TextCrossEncoder` 每次实例化都会先打 HuggingFace 取
revision + repo tree（`fastembed/common/model_management.py:385` 的
`model_info(...).sha`）。即使权重已经完整躺在本地缓存里，只要 Hub 不可达，
加载就会失败——本项目 2026-10-28 就因为这个在镜像 429/502 期间完全无法评测。

本模块直接从本地目录读 ONNX 图 + `tokenizer.json`，暴露与 fastembed 相同的
`rerank(query, documents, batch_size=...)` 接口——注意 fastembed 的返回是
**纯 float 迭代器**（`onnx_text_cross_encoder.py:228`），`retriever._rerank_docs`
直接拿这些 float 排序，所以这里也必须返回 float，返回对象会让排序抛 TypeError。

口径注意：输出的仍是 **logits**（未过 sigmoid），与 fastembed 的 bge 行为一致，
所以 `relevance_floor` / `per_doc_floor` 的标定方式不变；换模型仍需重标。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Sequence

# 交叉编码器单对最大长度。bge 系列按 512 训练；bge-m3 支持 8192，但线上
# rerank_text 是 child_title（约 250 字），512 足够且能挡住超长噪声。
DEFAULT_MAX_LENGTH = 512

_ONNX_CANDIDATES = ("onnx/model.onnx", "onnx/model_int8.onnx", "model.onnx")


class LocalOnnxCrossEncoder:
    """从本地目录加载的交叉编码器（ONNX Runtime + HF tokenizers）。"""

    def __init__(
        self,
        model_dir: str | os.PathLike[str],
        *,
        max_length: int = DEFAULT_MAX_LENGTH,
        threads: int | None = None,
    ) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self.model_dir = Path(model_dir)
        if not self.model_dir.is_dir():
            raise FileNotFoundError(f"模型目录不存在：{self.model_dir}")

        onnx_path = next(
            (self.model_dir / p for p in _ONNX_CANDIDATES if (self.model_dir / p).is_file()),
            None,
        )
        if onnx_path is None:
            found = sorted(str(p.relative_to(self.model_dir)) for p in self.model_dir.rglob("*.onnx"))
            raise FileNotFoundError(
                f"{self.model_dir} 下找不到 ONNX 图，候选 {_ONNX_CANDIDATES}；实际存在：{found}"
            )
        self.onnx_path = onnx_path

        tok_path = self.model_dir / "tokenizer.json"
        if not tok_path.is_file():
            raise FileNotFoundError(f"缺少 tokenizer.json：{tok_path}")
        self.tokenizer = Tokenizer.from_file(str(tok_path))
        self.tokenizer.enable_truncation(max_length=max_length)
        # 批内必须补齐，否则 ORT 认为各条输入形状不一致
        self.tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
        self.max_length = max_length

        so = ort.SessionOptions()
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        if threads:
            so.intra_op_num_threads = threads
        self.session = ort.InferenceSession(
            str(onnx_path), sess_options=so, providers=["CPUExecutionProvider"]
        )
        self.input_names = {i.name for i in self.session.get_inputs()}
        self.output_names = [o.name for o in self.session.get_outputs()]

    @property
    def name(self) -> str:
        return f"local:{self.model_dir}"

    # —— 与 fastembed 兼容的接口（返回 float 迭代器，口径见模块 docstring）
    def rerank(
        self,
        query: str,
        documents: Sequence[str],
        batch_size: int = 1,
        **_kwargs,
    ) -> Iterable[float]:
        if not documents:
            return []
        batch_size = max(1, int(batch_size))
        out: list[float] = []
        for start in range(0, len(documents), batch_size):
            chunk = documents[start : start + batch_size]
            encs = [self.tokenizer.encode(query, doc) for doc in chunk]
            feed = {
                "input_ids": [e.ids for e in encs],
                "attention_mask": [e.attention_mask for e in encs],
            }
            if "token_type_ids" in self.input_names:
                feed["token_type_ids"] = [e.type_ids for e in encs]
            logits = self.session.run(self.output_names, feed)[0]
            for row in logits:
                # [batch, 1] 或 [batch]
                out.append(float(row[0]) if hasattr(row, "__len__") else float(row))
        return out


def load_local_cross_encoder(
    model_dir: str | os.PathLike[str], *, threads: int | None = None
) -> LocalOnnxCrossEncoder:
    return LocalOnnxCrossEncoder(model_dir, threads=threads)
