"""真实 LLM 链路小测：POST /chat（带 classroom_id+成员，走完整链路：成员校验+落库+RAG 检索+SSE）。

会烧一条 DeepSeek 消息额度。解析 SSE 验证：AI 回复非空、sources 事件上报（D11 修复点）。
"""
import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"


def chat(user_id, classroom_id, message, timeout=90):
    body = {
        "user_id": user_id,
        "classroom_id": classroom_id,
        "message": message,
        "provider": "openai",
    }
    data = json.dumps(body).encode()
    req = urllib.request.Request(BASE + "/chat", data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "text/event-stream")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, "", {"error": e.read().decode()[:300], "events": 0, "sources": []}

    # SSE 格式：所有事件都是 `data: {json}`，json 内带 type 字段
    # （{"type":"sources","items":[...]} / {"type":"token","content":"..."} / "data: [DONE]"）
    # 没有 event: 行，按 type 分发解析。
    tokens, sources, event_count = [], [], 0
    for line in raw.split("\n"):
        line = line.strip()
        if not line.startswith("data:"):
            continue
        d = line[5:].strip()
        if d == "[DONE]":
            continue
        try:
            j = json.loads(d)
        except Exception:
            continue
        event_count += 1
        if not isinstance(j, dict):
            continue
        t = j.get("type")
        if t == "token":
            tokens.append(str(j.get("content") or ""))
        elif t == "sources":
            sources.append(j.get("items", []))
    return 200, "".join(tokens), {"events": event_count, "sources": sources}


if __name__ == "__main__":
    # 用 verify_live 刚建的群聊课堂(38) 与学生(49)；49 是 38 的成员
    code, text, info = chat(
        49, 38, "用一句话解释什么是死锁，并给出一个生活中的例子。"
    )
    print("HTTP 状态码:", code)
    print("SSE 事件数:", info.get("events"))
    print("AI 回复字数:", len(text))
    print("回复摘要(前220字):", text[:220].replace("\n", " "))
    print("sources 事件数:", len(info.get("sources", [])))
    print("sources 内容:", json.dumps(info.get("sources", []), ensure_ascii=False)[:600])
    if code == 200 and len(text) > 0:
        print("\n[RESULT] /chat 真实 LLM 链路 OK")
    else:
        print("\n[RESULT] /chat 链路异常:", info.get("error"))
