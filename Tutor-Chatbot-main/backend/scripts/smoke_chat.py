"""端到端冒烟脚本：真实调用 LLM，验证 /chat 全链路。

用途：每次改动提示词、策略表或图之后跑一次，确认
  1. SSE 能流出中文 token
  2. topic 抽取正确
  3. hint_level 会随学生「没听懂」递进
  4. 学生表示懂了之后 resolved=true 且 hint_level 归零

用法（在 backend/ 目录下，用 venv 的 python）：
    python smoke_chat.py                 # 跑内置的四轮剧本
    python smoke_chat.py "什么是虚拟内存？"   # 只问一轮自定义问题

前置：backend/.env 里 LLM_API_KEY 必须已填写，且服务已在 8000 端口启动。
注意：会真实消耗 API 额度。
"""

import json
import sys
import time
import urllib.request

URL = "http://127.0.0.1:8000/chat"

DEFAULT_SCRIPT = [
    "什么是死锁？产生的必要条件有哪些？",
    "还是没太懂，能再讲清楚一点吗？",
    "那怎么预防死锁呢？举例说明一下",
    "明白了，谢谢！",
]


def call(message, topic, hint_level, misconception, resolved, history):
    payload = {
        "message": message,
        "topic": topic,
        "hint_level": hint_level,
        "misconception": misconception,
        "resolved": resolved,
        "history": history,
        "session_id": "",
        "provider": "openai",
    }

    req = urllib.request.Request(
        URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
    )
    started = time.time()
    parts = []
    state = None
    error = None
    try:
        resp = urllib.request.urlopen(req, timeout=120)
        for raw in resp:
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line.startswith("data: "):
                continue
            body = line[6:]
            if body == "[DONE]":
                break
            try:
                evt = json.loads(body)
            except ValueError:
                continue
            kind = evt.get("type")
            if kind == "token":
                parts.append(evt.get("content", ""))
            elif kind == "state":
                state = evt
            elif kind == "error":
                error = evt
    except Exception as exc:
        print("  REQUEST_FAILED:", type(exc).__name__, exc)
        if hasattr(exc, "read"):
            try:
                print("  body:", exc.read().decode("utf-8", errors="replace")[:300])
            except Exception:
                pass
        return None, None

    print("  (%.1fs, %d chars)" % (time.time() - started, len("".join(parts))))
    if error:
        print("  流内错误：%s" % error.get("content"))
    return "".join(parts), state


def run(messages):
    history = []
    topic = ""
    level = 0
    misconception = ""
    resolved = False
    # 末轮断言只在跑内置四轮剧本时开：自定义单轮提问（smoke_chat.py "xxx"）
    # 的最后一句未必是「听懂了」，不能要求 resolved=true。
    expect_final_resolved = messages == DEFAULT_SCRIPT

    for index, message in enumerate(messages, 1):
        print()
        print("### 第 %d 轮 学生：%s" % (index, message))
        print("    发出：topic=%r hint_level=%d resolved=%s" % (topic, level, resolved))
        answer, state = call(message, topic, level, misconception, resolved, history)
        if answer:
            preview = answer[:400].replace("\n", "\n          ")
            print("    助手：%s" % preview)
        if not state:
            raise SystemExit(
                "第 %d 轮没收到 state 事件。两种可能：① 链路异常；② 走了「未识别知识点」"
                "的降级路径——它现在不推 state（见 app.py），换一个明确的知识点提问。"
                "本轮提问=%r" % (index, message)
            )
        # 真 state 必带 personalized（值可能是 false，但键一定在）。
        # 降级路径以前推的全零假 state 没有这个键，靠它把两者分开。
        if "personalized" not in state:
            raise SystemExit(
                "第 %d 轮的 state 缺 personalized 字段 —— 这是降级路径的全零假 state，"
                "真 state 必带该键。收到：%r" % (index, state)
            )
        if not state.get("topic"):
            raise SystemExit(
                "第 %d 轮 topic 为空，知识点没抽出来。收到：%r" % (index, state)
            )
        print(
            "    回传：topic=%r hint_level=%d misconception=%r resolved=%s"
            % (state.get("topic"), state.get("hint_level"),
               state.get("misconception"), state.get("resolved"))
        )
        topic = state.get("topic", "")
        level = state.get("hint_level", 0)
        misconception = state.get("misconception", "")
        resolved = state.get("resolved", False)
        if answer:
            history.append({"role": "user", "content": message})
            history.append({"role": "assistant", "content": answer})
        time.sleep(1)

    if expect_final_resolved and not resolved:
        raise SystemExit(
            "末轮学生说「%s」，resolved 却是 false、hint_level 停在 %d —— "
            "要么提示词没兜住确认语，要么评估节点解析失败走了降级"
            "（后端日志里会有「评估节点解析失败」）。"
            % (messages[-1], level)
        )
    if expect_final_resolved and level != 0:
        raise SystemExit(
            "末轮学生说懂了，hint_level 应该归零，实际是 %d。" % level
        )
    return True


if __name__ == "__main__":
    script = sys.argv[1:] or DEFAULT_SCRIPT
    ok = run(script)
    print()
    print("SMOKE", "OK" if ok else "FAILED")
