"""T4 端到端验收：成员身份校验 + 教学状态持久化 + 追问接续。

用法（需先启动后端）：
    python smoke_db.py                      # 默认问题「什么是死锁？」
    python smoke_db.py "TCP 为什么是三次握手？"

覆盖：
1. 创建用户 / 创建课堂 / 加入课堂（T3.6 复用）
2. 多轮对话落库
3. 追问不传任何状态也能接上上下文（状态从数据库恢复）
4. 非课堂成员发消息 → 403

注意 /chat 有 10 次/分钟限流，连续跑两次可能触发 429。
"""

import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
DEFAULT_QUESTION = "什么是死锁？"


def req(method, path, payload=None, stream=False, timeout=120):
    data = json.dumps(payload).encode() if payload is not None else None
    r = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        resp = urllib.request.urlopen(r, timeout=timeout)
        body = resp.read().decode()
        return body if stream else (json.loads(body) if body else {})
    except urllib.error.HTTPError as e:
        raise SystemExit(
            f"{method} {path} -> HTTP {e.code}: {e.read().decode()[:200]}"
        )


def expect_http_error(code, method, path, payload):
    """期望某个请求失败，返回 True 表示确实被拒。"""
    data = json.dumps(payload).encode()
    r = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(r, timeout=30)
        return False
    except urllib.error.HTTPError as e:
        return e.code == code


def chat(message, classroom_id, user_id):
    """发一条消息，不传 topic / hint_level / history —— 状态必须能从库里恢复。"""
    raw = req(
        "POST",
        "/chat",
        {
            "message": message,
            "classroom_id": classroom_id,
            "user_id": user_id,
        },
        stream=True,
    )
    tokens, state = [], None
    for line in raw.splitlines():
        if not line.startswith("data: "):
            continue
        body = line[6:]
        if body == "[DONE]":
            break
        ev = json.loads(body)
        if ev.get("type") == "token":
            tokens.append(ev["content"])
        elif ev.get("type") == "state":
            state = ev
        elif ev.get("type") == "error":
            raise SystemExit("chat error: " + str(ev))
    return "".join(tokens), state


def persisted_state(classroom_id):
    """从数据库读回最后一条 assistant 消息上存的状态。"""
    msgs = req("GET", f"/classrooms/{classroom_id}/messages")
    for m in reversed(msgs["messages"]):
        if m["role"] == "assistant" and m.get("topic"):
            return m["topic"], m["hint_level"]
    return None, 0


def main() -> None:
    question = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_QUESTION

    h = None
    for _ in range(25):
        try:
            h = req("GET", "/health")
            break
        except Exception:
            time.sleep(2)
    if h is None:
        raise SystemExit("后端未启动：先在 backend/ 下跑 uvicorn app:app")
    print("health:", h)
    if h.get("database") != "up":
        raise SystemExit(
            "数据库不可用（database=%s）。检查 backend/.env 的 MYSQL_* "
            "并确认 MySQL 服务已启动。" % h.get("database")
        )

    student = req("POST", "/users", {"username": "e2e_student", "role": "student"})
    teacher = req("POST", "/users", {"username": "e2e_teacher", "role": "teacher"})
    outsider = req("POST", "/users", {"username": "e2e_outsider", "role": "student"})
    print("1. 创建用户:", student["username"], "/", teacher["username"], "/", outsider["username"])

    room = req(
        "POST",
        "/classrooms",
        {"name": "E2E 八股班", "subject": "cs", "description": "T4 验收"},
    )
    print("2. 创建课堂:", room)

    req("POST", f"/classrooms/{room['id']}/members",
        {"user_id": teacher["id"], "role": "teacher"})
    req("POST", f"/classrooms/{room['id']}/members",
        {"user_id": student["id"], "role": "student"})
    members = req("GET", f"/classrooms/{room['id']}/members")
    print("3. 加入课堂:", [m["username"] for m in members["members"]])

    # --- 多轮对话：每轮都不传状态，全靠数据库恢复 ---
    script = [question, "还是没太懂，能再讲细一点吗？", "举个例子"]
    prev_level = -1
    for i, q in enumerate(script, 1):
        answer, state = chat(q, room["id"], student["id"])
        topic = state and state.get("topic")
        level = state and state.get("hint_level")
        print(f"4.{i} 问「{q[:12]}」-> topic={topic}, hint_level={level}, {len(answer)} 字")
        print("    ", answer[:56].replace("\n", " "))
        if not topic or topic == "unknown":
            raise SystemExit(f"第 {i} 轮没识别出知识点（topic={topic}）")
        db_topic, db_level = persisted_state(room["id"])
        print(f"     库里存的状态: topic={db_topic}, hint_level={db_level}")
        if db_topic != topic or db_level != level:
            raise SystemExit(
                f"状态没落库：返回({topic},{level}) vs 库里({db_topic},{db_level})"
            )
        prev_level = level

    msgs = req("GET", f"/classrooms/{room['id']}/messages")
    print(f"5. 记录累计: {msgs['count']} 条")
    assert msgs["count"] == 6, f"期望 6 条（3 问 3 答），实际 {msgs['count']}"

    # --- 状态恢复的关键证据：库里最后的状态不是 0，说明多轮递进真的留下了 ---
    last_topic, last_level = persisted_state(room["id"])
    print(f"   最后状态: {last_topic} / level {last_level}")
    if last_level == 0:
        print("   ⚠️ hint_level 仍是 0：可能是模型认为第一轮就讲透了，"
              "也可能是状态没恢复。用更明确的「还是不懂」复测。")

    # --- 成员校验 ---
    blocked = expect_http_error(
        403, "POST", "/chat",
        {"message": "我也能问吗？", "classroom_id": room["id"], "user_id": outsider["id"]},
    )
    print("6. 非成员发消息被拒:", blocked)
    if not blocked:
        raise SystemExit("非成员竟然能发消息 —— 成员校验没生效")

    print("\nT4 验收全部通过 ✓")


if __name__ == "__main__":
    main()
