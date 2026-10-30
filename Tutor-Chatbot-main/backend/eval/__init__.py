"""评测层：端到端评测执行器、指标、SLO 检查、裁判提示词。

目录结构与命名沿用 `PROPOSAL_RAG_QUALITY_EVAL.md` 第 2.4 节的约定。
本层全部是**纯函数或独立脚本**，不 import `app.py`，不参与 `/chat` 主链路——
评测代码出问题不该让服务起不来。
"""

from __future__ import annotations
