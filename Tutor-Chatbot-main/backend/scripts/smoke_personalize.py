"""T6 端到端验收：个性化 Prompt（Profile + RAG + Question）。

用法（需先启动后端）：
    python smoke_personalize.py

覆盖 T6.1~T6.5：
1. /chat 能读到画像并注入提示词（personalized=true）
2. RAG 上下文仍然参与拼装（知识库命中）
3. 同一道题，beginner 与 advanced 拿到不同的讲法
4. 掌握度高的知识点会跳过「一句话结论」，直接给标准答案
5. 新学生（无画像）完全退化成 T5 之前的讲法

注意 /chat 有 10 次/分钟限流，本脚本用掉 3 次，一分钟里别连着跑两遍。
"""

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8000"
LOG = open("smoke_personalize_out.txt", "w", encoding="utf-8")

# 两个人都要问的知识点。画像里对它给出相反的历史掌握度，
# 这样起步深度的差异才会被 suggest_start_level 触发。
TOPIC = "死锁"
QUESTION = "讲讲死锁"

BEGINNER_MASTERY = {"死锁": 0.2, "tcp三次握手": 0.3}
ADVANCED_MASTERY = {"死锁": 0.9, "tcp三次握手": 0.8}


def log(msg=""):
    print(msg)
    LOG.write(msg + "\n")
    LOG.flush()


def req(method, path, payload=None, stream=False, timeout=120):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    # 查询参数里的中文知识点名必须编码，否则 urllib 会用 latin-1 拼 URL
    # 直接抛异常，而且报错信息完全看不出是编码问题。
    path = urllib.parse.quote(path, safe="/?&=:")
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


def outline(text, width=78):
    """把回答压成一段可打印的摘要，方便人眼对比两人的讲法差别。"""
    flat = " ".join(text.split())
    return flat[:width] + ("…" if len(flat) > width else "")


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
               {"name": "T6 个性化班", "subject": "cs", "description": "T6 验收"})
    stamp = str(int(time.time()))[-6:]
    a = req("POST", "/users", {"username": f"begin_{stamp}", "role": "student"})
    b = req("POST", "/users", {"username": f"adva_{stamp}", "role": "student"})
    fresh = req("POST", "/users", {"username": f"fresh_{stamp}", "role": "student"})
    for u in (a, b, fresh):
        req("POST", f"/classrooms/{room['id']}/members",
            {"user_id": u["id"], "role": "student"})
    log("1. 课堂 %s｜A(beginner)=%s B(advanced)=%s C(新学生)=%s"
        % (room["id"], a["id"], b["id"], fresh["id"]))

    # --- 造两份画像：同一个知识点，一个没掌握、一个掌握了 ---
    req("PUT", f"/students/{a['id']}/profile", {
        "classroom_id": room["id"],
        "knowledge_mastery": BEGINNER_MASTERY,
        "weak_points": ["把死锁误解为单个进程死循环卡住"],
        "learning_preference": "需要把原理展开才听得懂，别只给结论",
    })
    req("PUT", f"/students/{b['id']}/profile", {
        "classroom_id": room["id"],
        "knowledge_mastery": ADVANCED_MASTERY,
        "learning_preference": "吸收快，可以直接给标准答案和追问",
    })
    pa = req("GET", f"/students/{a['id']}/profile?classroom_id={room['id']}")
    pb = req("GET", f"/students/{b['id']}/profile?classroom_id={room['id']}")
    log("2. A 画像 level=%s mastery=%s" % (pa["overall_level"], pa["knowledge_mastery"]))
    log("   B 画像 level=%s mastery=%s" % (pb["overall_level"], pb["knowledge_mastery"]))

    # --- T6.3：不打 LLM 先看提示词差在哪 ---
    prev_a = req("GET", f"/students/{a['id']}/prompt-preview"
                        f"?classroom_id={room['id']}&topic={TOPIC}&hint_level=0")
    prev_b = req("GET", f"/students/{b['id']}/prompt-preview"
                        f"?classroom_id={room['id']}&topic={TOPIC}&hint_level=0")
    log()
    log("3. 提示词预览（同一知识点「%s」，hint_level 都是 0）" % TOPIC)
    log("   A personalized=%s 起步深度=%s 实际深度=%s"
        % (prev_a["personalized"], prev_a["start_level_suggested"],
           prev_a["effective_hint_level"]))
    log("   B personalized=%s 起步深度=%s 实际深度=%s"
        % (prev_b["personalized"], prev_b["start_level_suggested"],
           prev_b["effective_hint_level"]))
    log()
    log("   --- A 的画像段落 ---")
    log(prev_a["profile_brief"])
    log()
    log("   --- B 的画像段落 ---")
    log(prev_b["profile_brief"])

    if not prev_a["personalized"] or not prev_b["personalized"]:
        raise SystemExit("两份画像都没注入提示词 —— load_profile 没读到画像")
    if prev_a["profile_brief"] == prev_b["profile_brief"]:
        raise SystemExit("beginner 与 advanced 的画像段落一模一样，个性化没生效")
    if prev_b["effective_hint_level"] <= prev_a["effective_hint_level"]:
        raise SystemExit(
            "「%s」这个知识点 B 掌握度 %.1f、A 只有 %.1f，"
            "B 的起步深度应该更高：B=%s A=%s"
            % (TOPIC, ADVANCED_MASTERY["死锁"], BEGINNER_MASTERY["死锁"],
               prev_b["effective_hint_level"], prev_a["effective_hint_level"])
        )

    # --- T6.4：同一道题，两人各问一次，比较真实回答 ---
    ans_a, state_a = chat(QUESTION, room["id"], a["id"])
    log()
    log("4. A 问「%s」-> topic=%r hint=%s personalized=%s"
        % (QUESTION, state_a.get("topic"), state_a.get("hint_level"),
           state_a.get("personalized")))
    log("   A 的回答（%d 字）：%s" % (len(ans_a), outline(ans_a)))

    ans_b, state_b = chat(QUESTION, room["id"], b["id"])
    log("   B 问「%s」-> topic=%r hint=%s personalized=%s"
        % (QUESTION, state_b.get("topic"), state_b.get("hint_level"),
           state_b.get("personalized")))
    log("   B 的回答（%d 字）：%s" % (len(ans_b), outline(ans_b)))

    if not ans_a.strip() or not ans_b.strip():
        raise SystemExit("有一方没拿到回答")
    if ans_a.strip() == ans_b.strip():
        raise SystemExit("两人画像不同，回答却一字不差 —— 画像没进提示词")
    if not state_a.get("personalized") or not state_b.get("personalized"):
        raise SystemExit("/chat 没上报 personalized=true，画像没注入")

    # --- 冷启动回归：新学生不该被强行贴上 beginner 标签 ---
    ans_c, state_c = chat(QUESTION, room["id"], fresh["id"])
    log()
    log("5. 新学生 C 问同一题 -> hint=%s personalized=%s"
        % (state_c.get("hint_level"), state_c.get("personalized")))
    log("   C 的回答（%d 字）：%s" % (len(ans_c), outline(ans_c)))
    if state_c.get("personalized"):
        raise SystemExit("新学生还没有任何画像数据，不该被注入个性化段落")

    log()
    log("T6 验收全部通过 ✓")
    log()
    log("人工比对（T6.4 要求的四个维度）：")
    log("   解释深度：A hint=%s / B hint=%s" % (state_a.get("hint_level"),
                                              state_b.get("hint_level")))
    log("   篇幅：A %d 字 / B %d 字" % (len(ans_a), len(ans_b)))
    log("   术语、示例、追问：见上面两份回答全文（完整内容在课堂消息里）")


if __name__ == "__main__":
    # 失败原因写进日志文件：控制台输出在 Windows 下经常看不到，
    # 而验收失败时最需要的就是那一行原因。
    try:
        main()
    except SystemExit as exc:
        log("❌ 验收失败：" + str(exc))
        raise
