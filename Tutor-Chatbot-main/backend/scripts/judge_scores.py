"""成果 #4 四维人工评分的初评辅助（**一次性数据工具，不进 CI**）。

保留下来只为复现评分表里的初评来源。注意它的定位是**辅助不是替代**：
分数写进 CSV 时一律带 `[AI初评·待人工复核]` 前缀，人工复核前不算定稿。

方法与立场（写进报告，不粉饰）：
- 四维评分口径上属于「人工评分」；本脚本用 DeepSeek（与生产同通道，temperature=0）
  逐条初评，每行一次调用、输出 4 个 1-5 分 + 一句理由，之后由人抽检复核并修订。
- 裁判 prompt 一次只评一行（四个维度一起出），不混入其他行，避免注意力涣散。
- 评分不看角色面子：tutor / peer / examiner 同一把尺子。

用法：
    env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy \
        ./venv/Scripts/python.exe judge_scores.py [--limit N]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # backend/

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[1] / ".env")  # backend/.env

from db.repository import get_messages_by_ids, list_agent_runs  # noqa: E402
from db.session import SessionLocal, db_available  # noqa: E402

# 裁判模型的三件套统一由 eval/judge_llm.py 解析（EVAL_JUDGE_* > LLM_* > 默认值）。
# 不再在模块顶层写死 deepseek 的地址——换模型应当是改 .env，不是改这里。
from eval.judge_llm import describe_judge, judge_payload_extras, resolve_judge_config  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "eval" / "reports" / "judge_scores.json"  # 与其他评测报告同目录

JUDGE_CFG: dict = {}  # 由 main() 填入，避免模块 import 阶段就因缺 key 而崩

SYSTEM = (
    "你是一名严格的教学质量评审。对一条 AI 助教在计算机八股辅导课堂中的发言打分。"
    "只输出 JSON 对象，不要任何其他文字，格式："
    '{"accuracy":1-5,"relevance":1-5,"completeness":1-5,"personalization":1-5,"comment":"一句话理由"}'
)

USER_TMPL = """课堂背景：计算机八股（操作系统+计算机网络）AI 辅导，学生逐题提问，AI 依次发言。
本次学生提问：{question}
发言角色：{role_label}（场景 {scenario}，本轮第 {index_disp} 个发言）
发言全文：
---
{content}
---

评分维度（各 1-5 分，5 最好，敢打低分）：
- accuracy 准确性：技术内容有无事实/术语/机制错误。有硬伤 ≤2；基本正确 4；正确且指出易错点 5。
- relevance 相关性：是否回答了学生真正问的点。答非所问 ≤2；部分跑题 3；紧扣 4-5。
- completeness 完整性：该知识点在这类提问下应覆盖的要点是否讲到（以主流八股答案为参照）。
- personalization 个性化：是否利用了本轮对话上下文（呼应前一轮、衔接前一位发言、按深度递进）。
  若该发言是本轮第一个发言且无历史可依，按 3 分中性处理，除非明显套模板。
注意：若问题本身超出课程知识库范围（如框架/工程题），回答用模型自有知识补位是合理的，
不因"没引资料"扣相关性分，但事实错误照扣准确性分。"""


def role_label(role: str) -> str:
    return {"tutor": "主讲助教", "peer": "学伴", "examiner": "考官", "teacher": "教师"}.get(role, role)


def call_judge(question: str, role: str, scenario: str, index: int, content: str) -> dict:
    prompt = USER_TMPL.format(
        question=question,
        role_label=role_label(role),
        scenario=scenario,
        index_disp=index + 1,
        index=index,
        content=content[:6000],
    )
    body = {
        "model": JUDGE_CFG["model"],
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "temperature": JUDGE_CFG["temperature"],
    }
    body.update(judge_payload_extras(JUDGE_CFG))  # Qwen3 等推理模型：关闭思考
    req = urllib.request.Request(
        JUDGE_CFG["base_url"] + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {JUDGE_CFG['api_key']}",
        },
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    text = data["choices"][0]["message"]["content"].strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError(f"裁判输出不是 JSON：{text[:200]}")
    out = json.loads(m.group(0))
    return {k: out.get(k) for k in ("accuracy", "relevance", "completeness", "personalization", "comment")}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="只评前 N 行（抽检验证用）")
    args = parser.parse_args()

    global JUDGE_CFG
    # 延迟到 main 里解析：缺 key 时给出可读提示，而不是 import 阶段就崩。
    JUDGE_CFG = resolve_judge_config(require_key=False)
    print(describe_judge(JUDGE_CFG), flush=True)

    if not db_available():
        raise SystemExit("数据库不可用")
    if not JUDGE_CFG["api_key"]:
        raise SystemExit("裁判 API Key 未配置：请填 EVAL_JUDGE_API_KEY（或 LLM_API_KEY）")

    with SessionLocal() as db:
        runs = [r for r in reversed(list_agent_runs(db, limit=500))]
        message_ids = [i for r in runs for i in (r.message_ids or [])]
        messages = get_messages_by_ids(db, message_ids)
        # 每轮的第 n 个 run 对应该课堂第 n 条 user 消息（一次 /chat = 一问 N 答）
        from sqlalchemy import select
        from db.models import Message

        question_map: dict[tuple[int, int], str] = {}
        for classroom_id in sorted({r.classroom_id for r in runs if r.classroom_id}):
            rows = db.execute(
                select(Message.content)
                .where(Message.classroom_id == classroom_id, Message.role == "user")
                .order_by(Message.id)
            ).scalars().all()
            for pos, text in enumerate(rows, start=1):
                question_map[(classroom_id, pos)] = text

    run_seq: dict[int, int] = {}
    counters: dict[int, int] = {}
    for run in runs:
        cid = run.classroom_id or 0
        counters[cid] = counters.get(cid, 0) + 1
        run_seq[run.id] = counters[cid]

    results = []
    if OUT.exists():
        results = json.loads(OUT.read_text(encoding="utf-8"))
        done = {(r["run_id"], r["index"]) for r in results}
        print(f"已有 {len(done)} 条初评，续跑")
    else:
        done = set()

    pending = []
    for run in runs:
        roles = list(run.roles or [])
        ids = list(run.message_ids or [])
        for idx, role in enumerate(roles):
            mid = ids[idx] if idx < len(ids) else None
            if (run.id, idx) in done:
                continue
            pending.append((run.id, run.classroom_id, run.scenario, role, idx, messages.get(mid, "")))

    if args.limit:
        pending = pending[: args.limit]
    print(f"待初评 {len(pending)} 行")

    for n, (run_id, classroom_id, scenario, role, idx, content) in enumerate(pending, 1):
        if not content:
            results.append(
                {
                    "run_id": run_id,
                    "classroom_id": classroom_id,
                    "scenario": scenario,
                    "role": role,
                    "index": idx,
                    "error": "no_content",
                }
            )
            continue
        question = question_map.get((classroom_id, run_seq[run_id]), "")
        try:
            scores = call_judge(question, role, scenario, idx, content)
        except Exception as exc:  # noqa: BLE001
            scores = {"error": f"{type(exc).__name__}: {exc}"[:200]}
        scores.update(
            run_id=run_id, classroom_id=classroom_id, scenario=scenario, role=role, index=idx
        )
        results.append(scores)
        if n % 10 == 0 or n == len(pending):
            OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"  {n}/{len(pending)}")
        time.sleep(0.2)

    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    errs = sum(1 for r in results if r.get("error"))
    print(f"完成：{len(results)} 行，失败 {errs} 行 → {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
