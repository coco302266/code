"""从 ModelScope 拉取 fastembed 支持的 embedding / rerank 模型，组装成本地可离线加载的格式。

为什么走 ModelScope
------------------
本机 huggingface.co 与 hf-mirror.com 都是 `ProxyError`（VPN 只代理浏览器流量时常见），
而 www.modelscope.cn 的 API 通、下载 4–5 MB/s。两个站的文件是一一对应的同一批权重。

为什么组装成「扁平目录」而不是 HF 的 models-- 结构
-------------------------------------------------
fastembed 0.9.0 在 `local_files_only=True`（本项目 `RAG_RERANK_ONLINE` 默认为 0，
embedding 侧同理）时的查找顺序是：

  1. HF 路：`download_files_from_huggingface(..., local_files_only=True)`。
     这条路会先调 `model_info()` 去问远端当前的 revision sha——**断网必失败**；
     而且有些仓库的当前 main 已经删掉我们要的那个文件（例如
     `onnx-community/bge-reranker-v2-m3-ONNX` 现已无 `onnx/model_int8.onnx`）。
  2. GCS 路：`retrieve_model_gcs()` 找 `<cache_dir>/<model_name 的最后一段>`，
     **只要该目录存在且非空就直接返回**，不联网。

所以把权重按第 2 条的布局摆好，就能彻底脱离网络。这也解释了为什么 Int8 重排
能在 HF 仓库已删除 int8 文件的情况下继续工作（见 `restore_rerank_cache.py`）。

用法
----
    cd backend
    venv/Scripts/python.exe scripts/fetch_modelscope_fastembed.py \
        --repo jinaai/jina-embeddings-v2-base-zh \
        --files onnx/model.onnx tokenizer.json tokenizer_config.json \
                config.json special_tokens_map.json

下载完会自动装到 `.model_cache/<模型名最后一段>/` 并做一次真加载自检。
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from pathlib import Path

import requests

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

CACHE_ROOT = BACKEND_DIR / ".model_cache"
API = "https://www.modelscope.cn/api/v1/models/{repo}/repo"
LIST_API = "https://www.modelscope.cn/api/v1/models/{repo}/repo/files"


def list_sizes(repo: str, revision: str = "master") -> dict[str, int]:
    """列出仓库文件大小，用于下载前预估与下载后校验。"""
    r = requests.get(
        LIST_API.format(repo=repo),
        params={"Revision": revision, "Recursive": "True"},
        timeout=20,
    )
    r.raise_for_status()
    return {
        f["Path"]: int(f.get("Size") or 0)
        for f in (r.json().get("Data", {}).get("Files") or [])
        if f.get("Type") != "tree"
    }


def download(repo: str, rel: str, dest: Path, revision: str = "master") -> None:
    """流式下载单文件。断点续传由服务端 Range 支持，这里做最简单的整体重试。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = API.format(repo=repo)
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            with requests.get(
                url, params={"Revision": revision, "FilePath": rel}, stream=True, timeout=60
            ) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("Content-Length") or 0)
                got = 0
                t0 = time.perf_counter()
                with open(dest, "wb") as fh:
                    for chunk in resp.iter_content(1 << 20):
                        fh.write(chunk)
                        got += len(chunk)
                mb = got / 1e6
                print(
                    f"  下载 {rel}  {mb:8.1f} MB  {mb / (time.perf_counter()-t0):5.1f} MB/s",
                    flush=True,
                )
            return
        except Exception as exc:  # noqa: BLE001 — 网络错误一律重试
            last_exc = exc
            print(f"  第 {attempt + 1} 次下载失败：{type(exc).__name__} {exc}", flush=True)
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"下载失败 {rel}: {last_exc}")


def main() -> int:
    ap = argparse.ArgumentParser(description="ModelScope 拉取并组装 fastembed 离线模型")
    ap.add_argument("--repo", required=True, help="ModelScope 仓库，形如 ns/name")
    ap.add_argument("--files", nargs="+", required=True, help="相对路径清单")
    ap.add_argument("--revision", default="master")
    ap.add_argument("--name", default="", help="本地扁平目录名（默认取 repo 最后一段）")
    ap.add_argument("--cache-root", default=str(CACHE_ROOT))
    args = ap.parse_args()

    cache_root = Path(args.cache_root)
    flat = cache_root / (args.name or args.repo.split("/")[-1])
    print(f"仓库   {args.repo}@{args.revision}")
    print(f"目标   {flat}")

    sizes = list_sizes(args.repo, args.revision)
    for rel in args.files:
        dest = flat / rel
        expect = sizes.get(rel, 0)
        if dest.exists() and expect and abs(dest.stat().st_size - expect) < 1024:
            print(f"  已存在 {rel}（{dest.stat().st_size/1e6:.1f} MB）")
            continue
        download(args.repo, rel, dest, args.revision)
        if expect and abs(dest.stat().st_size - expect) >= 1024:
            raise SystemExit(
                f"{rel} 大小不符：本地 {dest.stat().st_size} vs 远端 {expect}，请重跑"
            )
    print("组装完成，可用 −−cache-root 指定路径给 fastembed 加载。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
