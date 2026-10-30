"""把本机已有的 BGE 重排权重组装到 fastembed 认得的缓存位置。

为什么需要它
------------
`.model_cache` 放在 backend 目录下、目录 Specialist clean 被外部清理过一次。
清理之后的现象是：

    ERROR download_model:743 - Could not find model in cache_dir
    WARNING 重排模型加载失败，本次检索退化为纯向量排序

即 `RAG_RERANK=1` 但重排**静默失效**，指标退回无重排基线（Recall 0.7539）。
根因链（fastembed 0.9.0）：

  1. `retriever.get_reranker()` 传 `local_files_only=not CONFIG.rerank_online`，
     而 `rerank_online` 默认 False ⇒ fastembed 只查本地，不联网。
  2. HF 路：模型的 HF 源是 `onnx-community/bge-reranker-v2-m3-ONNX`，
     model_file = `onnx/model_int8.onnx`。但**该仓库当前的 main revision
     已经不含 int8 文件**（只剩 FP32 的 model.onnx），所以按 main 解析必失败。
  3. 退回 GCS 路：找扁平目录 `<cache_dir>/bge-reranker-v2-m3-int8`，
     不存在 ⇒ 抛 ValueError ⇒ `get_reranker()` 吞掉异常返回 None。

所以只要把这个扁平目录补上（内含 int8 onnx + tokenizer 全套），第 3 步就命中，
原生路径无需联网即可加载。权重文件本机本来就有——它是同一个 HF 仓库
**旧 revision** 的快照（930e3860），本轮全部用它跑过完整评测，
与 fastembed 原生加载逐项一致。

用法
----
    cd backend
    venv/Scripts/python.exe scripts/restore_rerank_cache.py            # 组装
    venv/Scripts/python.exe scripts/restore_rerank_cache.py --verify   # 只验证能否加载

默认用 **NTFS 硬链接**，570MB 的 onnx 不会占第二份空间；不支持硬链接时退回复制。
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
# 本脚本被当作 `python scripts/xxx.py` 直接启动时，sys.path[0] 是 scripts/，
# 不补这一行连 `agent` 都 import 不到（与 ab_rerank_model.py 同款处理）。
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

CACHE_ROOT = BACKEND_DIR / ".model_cache"

MODEL_NAME = "BAAI/bge-reranker-v2-m3-int8"
# HF 缓存里的历史快照：这才是本机 int8 权重的实际存放位置。
HF_REPO_DIR = "models--onnx-community--bge-reranker-v2-m3-ONNX"
HF_SNAPSHOT = "930e38609a0e848e391a2bba0bf52317850e7b25"

# fastembed 加载 TextCrossEncoder 所需的最小文件集：
#   tokenizer.json / tokenizer_config.json 是硬的（preprocessor_utils.load_tokenizer）
#   config.json / special_tokens_map.json 是推荐的（缺了会退默认词表参数）
NEEDED_FILES = [
    "onnx/model_int8.onnx",
    "tokenizer.json",
    "tokenizer_config.json",
    "config.json",
    "special_tokens_map.json",
]


def _pick_source() -> Path:
    """挑一个含全部必需文件的源目录。

    优先 HF 快照里的固定 revision；若那个 revision 也不在了，就在同一 repo
    下**扫描所有 snapshot**——仓库将来把 int8 挪到别的 revision 也能兜住。
    """
    candidates = [CACHE_ROOT / HF_REPO_DIR / "snapshots" / HF_SNAPSHOT]
    snap_root = CACHE_ROOT / HF_REPO_DIR / "snapshots"
    if snap_root.is_dir():
        candidates += sorted(p for p in snap_root.iterdir() if p.is_dir())

    for src in candidates:
        if all((src / f).exists() for f in NEEDED_FILES):
            return src
    raise SystemExit(
        f"找不到含 int8 权重的源目录，已尝试：\n"
        + "\n".join(f"  {c}" for c in candidates)
        + "\n请联网下载一次：见 docs/plans/PLANS.md §9.7 的下载配方。"
    )


def restore(cache_root: Path = CACHE_ROOT, dry_run: bool = False) -> Path:
    src = _pick_source()
    dst = cache_root / MODEL_NAME.split("/")[-1]
    print(f"源       {src}")
    print(f"目标     {dst}")

    if dry_run:
        print("--dry-run：不做任何改动")
        return dst

    dst.mkdir(parents=True, exist_ok=True)
    (dst / "onnx").mkdir(exist_ok=True)

    for rel in NEEDED_FILES:
        s, d = src / rel, dst / rel
        if d.exists():
            if d.stat().st_size == s.stat().st_size:
                print(f"  已存在  {rel}")
                continue
            d.unlink()  # 大小对不上：多半是上次下载被截断，重来
        try:
            os.link(s, d)
            how = "硬链接"
        except OSError:
            shutil.copy2(s, d)
            how = "复制"
        print(f"  {how}  {rel}  ({s.stat().st_size / 1e6:.1f} MB)")

    print("组装完成。")
    return dst


def verify(cache_root: Path) -> int:
    """真加载一次：不是看文件在不在，而是让 fastembed 自己走一遍原生路径。"""
    from agent.rag import _env  # noqa: F401  必须早于 fastembed

    from fastembed.rerank.cross_encoder import TextCrossEncoder

    enc = TextCrossEncoder(
        model_name=MODEL_NAME, cache_dir=str(cache_root), local_files_only=True
    )
    q = "死锁产生的四个必要条件是什么"
    docs = [
        "互斥条件、请求与保持条件、不可抢占条件、循环等待条件",
        "进程调度的时间片轮转算法",
    ]
    scores = [round(float(s), 4) for s in enc.rerank(q, docs)]
    print(f"原生路径加载成功。样例得分 = {scores}")
    if scores[0] <= scores[1]:
        print("WARN 相关句得分没有高于无关句，模型可能有问题，请人工确认")
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cache-root", default=str(CACHE_ROOT))
    ap.add_argument("--verify", action="store_true", help="只验证能否加载")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cache_root = Path(args.cache_root)
    if args.verify:
        return verify(cache_root)
    restore(cache_root, dry_run=args.dry_run)
    print()
    return verify(cache_root)


if __name__ == "__main__":
    sys.exit(main())
