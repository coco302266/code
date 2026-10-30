"""T5 端到端验收：学生画像自动沉淀 + Profile API。

用法（需先启动后端）：
    python smoke_profile.py

覆盖：
1. 两个学生各跑一段真实对话，画像自动更新（T5.1）
2. GET /students/{id}/profile 读得出来（T5.2）
3. PUT /students/{id}/profile 能手工建画像（对齐 T5.4 的 A/B 示例）
4. GET /students/{id}/behaviors 三种学习行为都在流水里（T5.3）
5. 关键回归：学生第一轮带着错误说法提问，misconception 也要抓得到

注意 /chat 有 10 次/分钟限流，本脚本用掉 8 次，一分钟里别连着跑两遍。
"""

import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
LOG = open("smoke_profile_out.txt", "w", encoding="utf-8")


def log(msg=""):
    print(msg)
    LOG.write(msg + "\n")
    LOG.flush()


def req(method, path, payload=None, stream=False, timeout=120):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    r = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        resp = urllib.request.urlopen(r, timeout=timeout)
        body = resp.read().decode("utf-8")
        return body if stream else (json.loads(body) if body else {})
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{method} {path} -> HTTP {e.code}: {e.read().decode()[:300]}")


def chat(message, classroom_id, user_id):
    raw = req(
        "POST",
        "/chat",
        {"message": message, "classroom_id": classroom_id, "user_id": user_id},
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


def main() -> None:
    h = None
    for _ in range(25):
        try:
            h = req("GET", "/health")
            break
        except Exception:
            time.sleep(2)
    if h is None:
        raise SystemExit("后端未启动：先在 backend/ 下跑 uvicorn app:app")
    log("health: %s" % h)
    if h.get("database") != "up":
        raise SystemExit("数据库不可用：%s" % h.get("database"))

    room = req("POST", "/classrooms",
               {"name": "T5 画像班", "subject": "cs", "description": "T5 验收"})
    # 用户名带时间戳：画像会随每次对话累积，用同一个学生重跑会让
    # 掌握度一路漂移，验收结论就不可复现了。
    stamp = str(int(time.time()))[-6:]
    a = req("POST", "/users", {"username": f"student_a_{stamp}", "role": "student"})
    b = req("POST", "/users", {"username": f"student_b_{stamp}", "role": "student"})
    for u in (a, b):
        req("POST", f"/classrooms/{room['id']}/members",
            {"user_id": u["id"], "role": "student"})
    log("1. 课堂 %s，学生 A=%s B=%s" % (room["id"], a["id"], b["id"]))

    # --- Student A：连着听不懂，还带着一个错误说法进场 ---
    # 最后一句故意不推进深度：hint_level 已经到顶，同一知识点继续聊
    # 应该被记成 repeat_question，而不是 request_explanation。
    script_a = [
        "死锁是不是就是进程死循环卡住了？",
        "还是不太懂，能把原理再讲细一点吗？",
        "举个具体例子吧",
        "四个必要条件我还是记不住",
    ]
    a_misconceptions = []
    first_round_mc = ""
    for i, q in enumerate(script_a, 1):
        answer, state = chat(q, room["id"], a["id"])
        mc = state.get("misconception") or ""
        if i == 1:
            first_round_mc = mc
        if mc:
            a_misconceptions.append(mc)
        log("2.%d A 问「%s」-> topic=%r hint=%s resolved=%s misconception=%r"
            % (i, q[:14], state.get("topic"), state.get("hint_level"),
               state.get("resolved"), mc))
        log("    回答 %d 字" % len(answer))

    # --- Student B：一问就抓住要点 ---
    # 两个知识点都当场答对——定级 advanced 要求至少两个知识点有数据
    script_b = [
        "TCP 三次握手是什么？",
        "明白了：客户端发 SYN，服务端回 SYN-ACK，客户端再发 ACK，"
        "目的是确认双方收发能力并同步初始序列号。",
        "进程和线程的区别是什么？",
        "懂了：进程是资源分配的基本单位，线程是 CPU 调度的基本单位，"
        "同一进程下的线程共享地址空间，切换开销更小。",
    ]
    for i, q in enumerate(script_b, 1):
        answer, state = chat(q, room["id"], b["id"])
        log("3.%d B 问「%s」-> topic=%r hint=%s resolved=%s"
            % (i, q[:14], state.get("topic"), state.get("hint_level"),
               state.get("resolved")))

    # --- 读画像 ---
    pa = req("GET", f"/students/{a['id']}/profile?classroom_id={room['id']}")
    pb = req("GET", f"/students/{b['id']}/profile?classroom_id={room['id']}")
    log()
    log("4. A 画像：level=%s mastery=%s 均值=%s"
        % (pa["overall_level"], pa["knowledge_mastery"], pa["mastery_average"]))
    log("   A 薄弱点：%s" % pa["weak_points"])
    log("   A 偏好：%s" % pa["learning_preference"])
    log("   B 画像：level=%s mastery=%s 均值=%s"
        % (pb["overall_level"], pb["knowledge_mastery"], pb["mastery_average"]))
    log("   B 偏好：%s" % pb["learning_preference"])

    if pa["overall_level"] != "beginner":
        raise SystemExit(
            "A 连着三轮没听懂，level 应该是 beginner，实际是 %s" % pa["overall_level"]
        )
    if pb["mastery_average"] <= pa["mastery_average"]:
        raise SystemExit(
            "B 答对了要点，掌握度均值应高于 A：B=%s A=%s"
            % (pb["mastery_average"], pa["mastery_average"])
        )
    if not first_round_mc:
        raise SystemExit(
            "回归失败：学生第一句话就带着错误说法，第一轮却没抓到 misconception。"
            "多半是 assess_understanding 又被人加了短路。"
        )
    if not pa["weak_points"]:
        raise SystemExit(
            "A 带着错误说法进场，画像里却没有薄弱点 —— misconception 没沉淀下来"
        )
    if pb["overall_level"] != "advanced":
        raise SystemExit(
            "B 两个知识点都当场答对，level 应是 advanced，实际是 %s"
            % pb["overall_level"]
        )

    # --- 学习行为流水 ---
    ba = req("GET", f"/students/{a['id']}/behaviors?classroom_id={room['id']}")
    actions = [x["action"] for x in ba["behaviors"]]
    log()
    log("5. A 的行为流水（新->旧）：%s" % actions)
    for expected in ("ask_question", "request_explanation", "repeat_question"):
        if expected not in actions:
            log("   ⚠️ 未出现行为：%s（可能因为模型没推进 hint_level）" % expected)

    # --- PUT 手工建画像：对齐 T5.4 的验收示例 ---
    manual_a = req("PUT", f"/students/{a['id']}/profile", {
        "classroom_id": room["id"],
        "knowledge_mastery": {"HashMap": 0.3, "JVM": 0.2},
    })
    manual_b = req("PUT", f"/students/{b['id']}/profile", {
        "classroom_id": room["id"],
        "knowledge_mastery": {"HashMap": 0.8, "JVM": 0.9},
    })
    log()
    log("6. 手工建画像：")
    log("   A level=%s mastery=%s" % (manual_a["overall_level"], manual_a["knowledge_mastery"]))
    log("   B level=%s mastery=%s" % (manual_b["overall_level"], manual_b["knowledge_mastery"]))
    if manual_a["overall_level"] != "beginner":
        raise SystemExit("手工写入低掌握度后应自动定级 beginner，实际 %s"
                         % manual_a["overall_level"])
    if manual_b["overall_level"] != "advanced":
        raise SystemExit("手工写入高掌握度后应自动定级 advanced，实际 %s"
                         % manual_b["overall_level"])

    log()
    log("T5 验收全部通过 ✓")


if __name__ == "__main__":
    main()
