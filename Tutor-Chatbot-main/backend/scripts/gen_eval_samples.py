"""为成果 #4 生成 `/chat` 真实留痕样本（**一次性数据工具，不进 CI**）。

保留下来只为复现：`reports/` 里的 66 轮留痕和评分表是它产出的，
删了下次就得重写。不会被引擎或测试引用，也不会自动跑。

为什么要用真实 HTTP 而不是直接调内部函数：评估要测的是端到端延迟与真实编排行为，
绕开接口等于没测（与 PROPOSAL_RAG_QUALITY_EVAL 里 L4 的立场一致）。

用法：
    env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy \
        ./venv/Scripts/python.exe gen_eval_samples.py --turns-per-scenario 22

行为：
    1. 按场景各建一个课堂（`M4评估-{scenario}`），把 demo_student(#2) 加为学生、
       demo_teacher(#1) 加为教师；
    2. 依次 POST /chat（带 classroom_id + user_id，服务端自行恢复历史与状态）；
    3. 收 SSE 到 [DONE]，统计 token 长度 / sources 条数 / 是否降级；
    4. 限速 10 次每分钟：两次请求开始时刻至少隔 7 秒，遇 429 退避 65 秒重试一次。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
STUDENT_ID = 2
TEACHER_ID = 1

# 22 题，覆盖 8 个知识点 × 五类问法 + 省略式追问 + 1 条库外题。
# 三个场景用同一套题，横向比较才有意义。
QUESTIONS: list[tuple[str, bool]] = [
    ("什么是死锁？", True),
    ("死锁产生的四个必要条件是什么？", True),
    ("那该怎么预防死锁？", True),          # 省略式追问：靠 topic 补全
    ("银行家算法是怎么工作的？", True),
    ("进程和线程有什么区别？", True),
    ("为什么线程切换比进程切换开销小？", True),
    ("进程调度算法有哪些？", True),
    ("时间片轮转和优先级调度该怎么选？", True),
    ("它有什么缺点？", True),              # 省略式追问
    ("什么是虚拟内存？", True),
    ("页面置换 LRU 是怎么工作的？", True),
    ("LRU 和 FIFO 有什么区别，会出现 Belady 异常吗？", True),
    ("TCP 三次握手的过程是怎样的？", True),
    ("为什么需要三次握手，两次不行吗？", True),
    ("TCP 四次挥手为什么要等 TIME_WAIT？", True),
    ("TCP 和 UDP 该怎么选？", True),
    ("HTTPS 和 HTTP 有什么不同？", True),
    ("TLS 握手大概做了什么事？", True),
    ("TCP/IP 分层模型有几层，各层负责什么？", True),
    ("为什么 DNS 用 UDP？", True),
    ("换成 mybatis 的缓存该怎么设计？", False),  # 库外题：检验"没资料"时的行为
    ("刚才说的这些里面，哪个最常在面试被追问？", True),  # 收尾式追问
]

SCENARIOS = ["v1", "group", "debate"]


def http_json(method: str, path: str, payload: dict | None = None, timeout: float = 60.0):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def create_classroom(scenario: str) -> int:
    status, payload = http_json(
        "POST",
        "/classrooms",
        {
            "name": f"M4评估-{scenario}",
            "subject": "cs",
            "description": f"成果 #4 留痕样本 · 场景 {scenario}",
            "members": [STUDENT_ID],
            "teacher_members": [TEACHER_ID],
            "scenario": scenario,
        },
    )
    if status != 201:
        raise SystemExit(f"建课堂失败 status={status} body={payload}")
    print(f"  课堂 #{payload['id']} {payload['name']} scenario={payload['scenario']}")
    return payload["id"]


def post_chat(classroom_id: int, message: str, timeout: float = 120.0) -> dict:
    """POST /chat 并消费 SSE，返回本轮观测到的摘要。"""
    body = {
        "message": message,
        "topic": "",
        "hint_level": 0,
        "classroom_id": classroom_id,
        "user_id": STUDENT_ID,
        "provider": "openai",
    }
    req = urllib.request.Request(
        BASE + "/chat",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
    )
    started = time.perf_counter()
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        return {"status": exc.code, "error": detail[:300], "elapsed_ms": 0}
    if resp.status == 429:
        return {"status": 429, "elapsed_ms": round((time.perf_counter() - started) * 1000)}

    events: list[dict] = []
    text_len = 0
    # 正文也要攒下来（不只记长度）：L4 的「资料利用率」裁判要拿答案原文去比对，
    # 只给字符数判不了。token 事件之外不再另要一次请求。
    text_parts: list[str] = []
    try:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            content = line[5:].strip()
            if content == "[DONE]":
                break
            try:
                item = json.loads(content)
            except json.JSONDecodeError:
                continue
            events.append(item)
            if item.get("type") == "token":
                piece = item.get("content") or ""
                text_len += len(piece)
                text_parts.append(piece)
    finally:
        resp.close()

    elapsed = round((time.perf_counter() - started) * 1000)
    types = {}
    for item in events:
        key = item.get("type")
        types[key] = types.get(key, 0) + 1
    sources = next((i.get("items") or [] for i in events if i.get("type") == "sources"), [])
    agents = [i for i in events if i.get("type") == "agent"]
    state = next((i for i in events if i.get("type") == "state"), None)
    error = next((i for i in events if i.get("type") == "error"), None)
    return {
        "status": 200,
        "elapsed_ms": elapsed,
        "event_counts": types,
        "sources": len(sources),
        "source_topics": sorted({s.get("topic") for s in sources if s.get("topic")}),
        "agents": [(a.get("role"), a.get("index"), a.get("total")) for a in agents],
        "chars": text_len,
        "text": "".join(text_parts),
        "topic": (state or {}).get("topic", ""),
        "hint_level": (state or {}).get("hint_level"),
        "resolved": (state or {}).get("resolved"),
        "personalized": (state or {}).get("personalized"),
        "error": (error or {}).get("content", "") if error else "",
    }


def run_scenario(scenario: str, turns: int, pause: float) -> list[dict]:
    print(f"\n=== 场景 {scenario} ===")
    classroom_id = create_classroom(scenario)
    results: list[dict] = []
    questions = QUESTIONS[:turns]
    last_start = 0.0
    for index, (question, _in_kb) in enumerate(questions, start=1):
        gap = pause - (time.perf_counter() - last_start)
        if gap > 0:
            time.sleep(gap)
        last_start = time.perf_counter()

        summary = post_chat(classroom_id, question)
        if summary.get("status") == 429:
            print(f"  [{index:02d}] 429 限流，退避 65s 后重试")
            time.sleep(65)
            summary = post_chat(classroom_id, question)

        summary.update(index=index, question=question, classroom_id=classroom_id)
        results.append(summary)
        flag = "" if summary.get("status") == 200 else "  <<< 异常"
        print(
            f"  [{index:02d}] {summary.get('elapsed_ms')}ms "
            f"agent={len(summary.get('agents') or [])} "
            f"sources={summary.get('sources')} "
            f"topic={summary.get('topic')!r} "
            f"hint={summary.get('hint_level')} chars={summary.get('chars')}{flag}"
        )
        if summary.get("error"):
            print(f"        error: {summary['error'][:160]}")
        if summary.get("status") != 200:
            print(f"        status={summary.get('status')} {str(summary.get('error'))[:200]}")
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="生成成果 #4 的 /chat 留痕样本")
    parser.add_argument("--scenarios", default="v1,group,debate")
    parser.add_argument("--turns-per-scenario", type=int, default=22)
    parser.add_argument("--pause", type=float, default=7.0, help="两次 /chat 之间的最小间隔秒数")
    parser.add_argument("--out", default="eval_samples_raw.json")
    args = parser.parse_args()

    all_runs: dict[str, list[dict]] = {}
    for scenario in [s.strip() for s in args.scenarios.split(",") if s.strip()]:
        all_runs[scenario] = run_scenario(scenario, args.turns_per_scenario, args.pause)

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(all_runs, fh, ensure_ascii=False, indent=2)

    print(f"\n原始观测已写入 {args.out}")
    total_turns = sum(len(v) for v in all_runs.values())
    ok = sum(1 for v in all_runs.values() for r in v if r.get("status") == 200)
    print(f"总轮数 {total_turns}，成功 {ok}")
    return 0 if ok == total_turns else 1


if __name__ == "__main__":
    raise SystemExit(main())
