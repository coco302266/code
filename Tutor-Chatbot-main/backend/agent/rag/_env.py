"""embedding / 重排模型的下载环境变量集中设置。

**这个模块必须在 `fastembed`、`huggingface_hub` 被导入之前 import。**
huggingface_hub 在模块加载时就把这些变量求值成常量，之后再 setdefault 无效。

三个变量各自解决一个问题：
- HF_ENDPOINT：直连 huggingface.co 在国内网络下经常超时/502，走镜像。
- HF_HUB_DISABLE_SYMLINKS：Windows 默认建不了符号链接且是静默失败，
  表现为「下载成功但 snapshots 目录是空的」。
- FASTEMBED_CACHE_PATH：默认 cache 在系统 Temp 下，磁盘清理一扫就没了，
  模型会被悄悄删掉。落到 backend/.model_cache 稳定目录。

原来这段写在 indexer.py 顶部，加重排后 retriever.py 也要用 fastembed，
两处都要保证执行顺序，所以抽成独立模块——少一个「谁先 import」的坑。
"""

import os
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[2]

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
os.environ.setdefault(
    "FASTEMBED_CACHE_PATH", str(_BACKEND_DIR / ".model_cache")
)
