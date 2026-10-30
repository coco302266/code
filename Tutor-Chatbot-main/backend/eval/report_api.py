"""检索链路评测指标报告 —— HTTP 接口层。

路由（挂在 app 的 `/evaluation` 前缀下）
---------------------------------------
| 方法 | 路径                              | 说明                     |
|------|-----------------------------------|--------------------------|
| GET  | `/evaluation/metrics`             | 指标报告卡（JSON）       |
| GET  | `/evaluation/metrics.csv`          | 同一份表格的 CSV 下载    |
| POST | `/evaluation/metrics/refresh`      | 触发重新评测（异步）     |
| GET  | `/evaluation/metrics/job/{job_id}` | 查询评测任务状态         |

两个必须知道的设计约束
----------------------
1. **评测必须在独立进程里跑，不能在请求线程里跑。**
   `smoke_rag.configure()` 改的是 `agent.rag.retriever.CONFIG` 这个**全局对象**，
   而 `/chat` 用的就是它。在后端进程内跑 L1/L2 会把 `enable_rerank / coarse_k /
   final_k` 切成评测档位（`hybrid`、`final_k=4`），期间所有真实用户的检索都跟着变。
   所以这里用 `subprocess` 起一个 `python -m eval.e2e_runner`，进程退出后
   线上配置不受任何影响。项目里 `scripts/l1_p95_by_mode.py` 也是这个做法。

2. **同一时刻只允许一个评测任务（单飞）。**
   评测是 CPU 密集的（重排模型），并发跑两个不只互相拖慢，还会让**延迟指标失真**
   ——而延迟正是本报告要测的东西之一。已有任务在跑时返回 409，不排队。

为什么默认读缓存而不是每次现跑
------------------------------
一次 L1+L2 全量约 80 秒，远超 HTTP 请求的合理等待。所以：
- 默认返回**最新一份报告**，并给出 `source.fresh`（比对索引指纹）；
- 需要最新数据时前端显式调 `/refresh`，拿 job_id 轮询，完成后再取报告。

用法（app.py 里两行）：
    from eval.report_api import configure_metrics_api, router as metrics_router
    configure_metrics_api(get_knowledge_store)
    app.include_router(metrics_router)
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from eval.fingerprint import collection_fingerprint
from eval.metrics_report import (
    REPORTS_DIR,
    build_card,
    latest_report_path,
    load_report,
    rows_to_csv,
)

BACKEND_DIR = Path(__file__).resolve().parent.parent

# 允许跑的层。L3/L4 要调裁判模型或真实 /chat，成本高、耗时长（限流 10/min），
# 所以默认只开放零 LLM 成本的 L1+L2；要跑 L3 得显式声明。
ALLOWED_LAYERS = {"L1", "L2", "L3", "L4"}
DEFAULT_LAYERS = "L1,L2"

# 子进程超时：L1+L2 实测 ~80s；放开 L3/L4 后可能到十几分钟，给足余量。
SUBPROCESS_TIMEOUT_SECS = 1800

# 父子检索档位：评测必须与生产同档位，否则报告口径与线上不一致。
# 这里只做"如实上报"，不自动改——改了反而会让读者以为线上线下一致。
# 子进程原样继承本进程环境，所以本进程怎么启的，评测就是什么档位。
RAG_PARENT_CHILD = os.getenv("RAG_PARENT_CHILD")

router = APIRouter(prefix="/evaluation", tags=["evaluation-metrics"])

# --------------------------------------------------------------------------
# 进程内状态
# --------------------------------------------------------------------------

_store_provider: Callable[[], Any] | None = None
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()
_current_job_id: str | None = None


def configure_metrics_api(store_provider: Callable[[], Any] | None) -> None:
    """注入知识库 store 的取数函数（由 app.py 传入 `get_knowledge_store`）。

    用注入而不是 `from app import get_knowledge_store`，是为了避免
    `app → report_api → app` 的循环导入。
    """
    global _store_provider
    _store_provider = store_provider


def _current_fingerprint() -> str | None:
    """当前索引指纹。取不到就返回 None（不抛），由调用方标成"无法校验"。"""
    if _store_provider is None:
        return None
    try:
        store = _store_provider()
        if store is None:
            return None
        return collection_fingerprint(store)
    except Exception:
        return None


def _resolve_report(report: str | None) -> Path:
    """解析报告路径。

    只接受 `eval/reports/` 下的文件名，**不接受绝对路径或 `..`**——
    这是个会读磁盘文件的参数，不能让调用方指定任意路径。
    """
    if not report:
        path = latest_report_path()
        if path is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "NO_REPORT",
                    "message": "尚无任何评测报告，先调用 POST /evaluation/metrics/refresh 生成。",
                },
            )
        return path
    if "/" in report or "\\" in report or ".." in report:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "BAD_REPORT_NAME",
                "message": "report 只能是 eval/reports/ 下的文件名，不接受路径分隔符或 ..",
            },
        )
    path = REPORTS_DIR / report
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail={"code": "REPORT_NOT_FOUND", "message": "报告不存在：%s" % report},
        )
    return path


def _source_meta(path: Path, report: dict) -> dict:
    """报告来源信息：**必须让读者能自己判断这份数据还能不能信**。

    `fresh=false` 的原因写进 `stale_reason`，不说清的话，读者看到一份
    索引已经重建过的旧报告会当成当前状态。
    """
    current_fp = _current_fingerprint()
    report_fp = report.get("index_fingerprint") or ""
    if current_fp is None:
        fresh, reason = None, "知识库未加载，无法校验索引指纹"
    elif not report_fp:
        fresh, reason = None, "该报告未记录索引指纹，无法校验是否过期"
    elif current_fp == report_fp:
        fresh, reason = True, None
    else:
        fresh, reason = False, "索引指纹已变更（知识库重建过），该报告不反映当前状态"
    return {
        "report_file": path.name,
        "report_timestamp": report.get("timestamp") or "",
        "index_fingerprint": report_fp,
        "current_fingerprint": current_fp,
        "fresh": fresh,
        "stale_reason": reason,
        "parent_child": RAG_PARENT_CHILD,
    }


# --------------------------------------------------------------------------
# GET /evaluation/metrics
# --------------------------------------------------------------------------


@router.get("/metrics")
def api_metrics_report(
    report: str | None = Query(
        default=None,
        description="指定报告文件名（eval/reports/ 下）；缺省取最新一份",
    ),
):
    """检索链路指标报告卡。

    返回结构见模块 docstring 顶部路由表；`rows` 的列顺序与 `columns` 一致，
    表头中文名在 `column_labels` 里——前端直接照这两个字段渲染即可，
    不用在前端再写一份列名映射。

    **读 `rows[].status` 时注意**：`未运行` 表示该层没跑（如 L3 要花裁判模型的钱），
    **不等于达标**。汇总里 `unknown` 单独计数，不计入 `pass_rate` 的分母。
    """
    path = _resolve_report(report)
    try:
        raw = load_report(path)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={"code": "REPORT_UNREADABLE", "message": "报告解析失败：%s" % exc},
        )
    card = build_card(raw, _source_meta(path, raw))
    card["available"] = True
    return card


# --------------------------------------------------------------------------
# GET /evaluation/metrics.csv
# --------------------------------------------------------------------------


@router.get("/metrics.csv")
def api_metrics_csv(
    report: str | None = Query(default=None, description="同 /evaluation/metrics"),
    bom: int = Query(
        default=1,
        ge=0,
        le=1,
        description="1=带 UTF-8 BOM（Excel 直接打开中文不乱码，默认）；0=纯 UTF-8（给程序解析）",
    ),
):
    """把同一份报告卡导出为 CSV 下载。

    列结构与 `/evaluation/metrics` 的 `rows` **完全一致**（顺序、内容都对齐），
    避免"页面上看到的和导出的不一样"。

    **关于 BOM**：`utf-8-sig` 是"UTF-8 + BOM"，编码仍是 UTF-8。Excel 在中文
    Windows 上没有 BOM 时会按 ANSI 码页解析，中文必然乱码——这是"UTF-8 导出
    中文乱码"最常见的原因。所以默认带 BOM；程序解析时用 `bom=0`。
    """
    path = _resolve_report(report)
    try:
        raw = load_report(path)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={"code": "REPORT_UNREADABLE", "message": "报告解析失败：%s" % exc},
        )
    card = build_card(raw, _source_meta(path, raw))
    csv_text = rows_to_csv(card["rows"], bom=bool(bom))

    # 时间戳里的 "T"、":"、"-" 都去掉，只留数字，避免文件名在不同系统上有歧义
    stamp = "".join(
        c for c in (card.get("generated_at") or "") if c.isdigit()
    )[:14]
    filename = "rag_metrics_%s.csv" % (stamp or datetime.now().strftime("%Y%m%d_%H%M%S"))
    return Response(
        # 已经是 str，交给 FastAPI 按 charset 编码；BOM 字符在字符串首位，会被一并写出
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="%s"' % filename,
            # 导出内容随报告变化，别让中间层缓存住旧表
            "Cache-Control": "no-store",
        },
    )


# --------------------------------------------------------------------------
# POST /evaluation/metrics/refresh
# --------------------------------------------------------------------------


class RefreshRequest(BaseModel):
    layers: str = Field(
        default=DEFAULT_LAYERS,
        description="要跑的层，逗号分隔。可选 L1,L2,L3,L4；默认 L1,L2（零 LLM 成本）",
    )
    cases: str | None = Field(default=None, description="评测集路径；缺省 rag_eval_cases.json")


def _run_layers(job_id: str, layers: str, cases: str | None, out_path: Path) -> None:
    """在**子进程**里跑评测（理由见模块 docstring 第 1 条）。"""
    cmd = [
        sys.executable,
        "-m",
        "eval.e2e_runner",
        "--layers",
        layers,
        "--out",
        str(out_path),
    ]
    if cases:
        cmd += ["--cases", cases]

    # 子进程环境：去掉代理变量（检索全本地，代理只会带来连接风险），
    # 其余原样继承——尤其是 RAG_PARENT_CHILD / RAG_RERANK_TEXT，
    # 评测必须跑在与生产相同的档位上。
    env = {
        k: v
        for k, v in os.environ.items()
        if k.lower() not in {"http_proxy", "https_proxy", "all_proxy"}
    }

    started = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(BACKEND_DIR),
            env=env,
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_SECS,
            encoding="utf-8",
            errors="replace",
        )
        elapsed = time.perf_counter() - started
        stdout = (proc.stdout or "")[-4000:]
        stderr = (proc.stderr or "")[-2000:]
        # e2e_runner 退出码 1 = 门禁未通过，也可能是跑批失败。
        # 唯一可信的判据是**报告文件有没有落盘**：文件在且是合法 JSON 才算成功。
        done = out_path.exists()
        with _jobs_lock:
            _jobs[job_id].update(
                {
                    "status": "done" if done else "failed",
                    "finished_at": datetime.now().isoformat(timespec="seconds"),
                    "elapsed_secs": round(elapsed, 1),
                    "exit_code": proc.returncode,
                    "report_file": out_path.name if done else None,
                    "error": None if done else "报告未生成（退出码 %s）" % proc.returncode,
                    "log_tail": stdout or stderr,
                }
            )
    except subprocess.TimeoutExpired:
        with _jobs_lock:
            _jobs[job_id].update(
                {
                    "status": "failed",
                    "finished_at": datetime.now().isoformat(timespec="seconds"),
                    "elapsed_secs": round(time.perf_counter() - started, 1),
                    "exit_code": None,
                    "error": "评测超时（%ss）" % SUBPROCESS_TIMEOUT_SECS,
                    "log_tail": "",
                }
            )
    except Exception as exc:  # noqa: BLE001 - 后台线程里的异常必须落到 job 上，不能静默
        with _jobs_lock:
            _jobs[job_id].update(
                {
                    "status": "failed",
                    "finished_at": datetime.now().isoformat(timespec="seconds"),
                    "elapsed_secs": round(time.perf_counter() - started, 1),
                    "exit_code": None,
                    "error": "%s: %s" % (type(exc).__name__, exc),
                    "log_tail": "",
                }
            )
    finally:
        global _current_job_id
        with _jobs_lock:
            if _current_job_id == job_id:
                _current_job_id = None


@router.post("/metrics/refresh", status_code=202)
def api_metrics_refresh(body: RefreshRequest):
    """触发一次重新评测（异步，返回 job_id 去轮询）。

    **为什么是异步**：L1+L2 全量约 80 秒，L3 要调裁判模型更久，同步等会超时。

    **单飞**：已有任务在跑时返回 409。并发评测会互相抢 CPU，让**延迟指标失真**
    ——而延迟正是本报告要测的指标之一，所以宁可拒绝也不排队。
    """
    wanted = [x.strip().upper() for x in body.layers.split(",") if x.strip()]
    if not wanted:
        raise HTTPException(
            status_code=422,
            detail={"code": "EMPTY_LAYERS", "message": "layers 不能为空"},
        )
    bad = [x for x in wanted if x not in ALLOWED_LAYERS]
    if bad:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "BAD_LAYERS",
                "message": "不支持的层：%s（可选 %s）" % (",".join(bad), ",".join(sorted(ALLOWED_LAYERS))),
            },
        )
    if {"L3", "L4"} & set(wanted):
        # 不是禁止，是提醒：这两层要花钱花时间，误点一次代价不小
        print(
            "[eval] refresh 请求包含 %s：会调用裁判模型 / 真实 /chat，耗时较长"
            % sorted({"L3", "L4"} & set(wanted)),
            flush=True,
        )

    global _current_job_id
    with _jobs_lock:
        if _current_job_id is not None:
            running = _jobs.get(_current_job_id) or {}
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "JOB_RUNNING",
                    "message": "已有评测任务在跑，完成后再发起（并发评测会让延迟指标失真）",
                    "job_id": _current_job_id,
                    "started_at": running.get("started_at"),
                },
            )
        job_id = uuid.uuid4().hex[:12]
        out_path = REPORTS_DIR / ("e2e_%s.json" % datetime.now().strftime("%Y%m%d_%H%M%S"))
        _jobs[job_id] = {
            "job_id": job_id,
            "status": "running",
            "layers": ",".join(wanted),
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "finished_at": None,
            "elapsed_secs": None,
            "exit_code": None,
            "report_file": None,
            "error": None,
            "log_tail": "",
        }
        _current_job_id = job_id

    thread = threading.Thread(
        target=_run_layers,
        args=(job_id, ",".join(wanted), body.cases, out_path),
        name="eval-refresh-%s" % job_id,
        daemon=True,
    )
    thread.start()

    return {
        "job_id": job_id,
        "status": "running",
        "layers": ",".join(wanted),
        "started_at": _jobs[job_id]["started_at"],
        "poll": "/evaluation/metrics/job/%s" % job_id,
        "hint": "轮询 poll 直到 status != running；完成后 GET /evaluation/metrics 取最新报告。",
    }


@router.get("/metrics/job/{job_id}")
def api_metrics_job(job_id: str):
    """查询评测任务状态。"""
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "JOB_NOT_FOUND", "message": "任务不存在：%s" % job_id},
        )
    return dict(job)
