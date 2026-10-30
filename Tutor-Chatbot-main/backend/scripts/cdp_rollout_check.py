"""XB-25 滚动回归：用真实 Chrome（CDP）验证 /classroom 的「贴底跟随」。

为什么必须在滚动中途往库里插消息：本 bug 的触发源是 5 秒一次的轮询，
只在「有新内容到达」时才会暴露。若只测「不动会不会跳」，修复前后都能过，
测不出东西。

断言三条：
  A 首次加载应自动停在底部（stick 默认 true 生效）
  B 用户上滚后 + 新消息到达 → 节点数增加，但 scrollTop 不被改动（核心）
  C 用户自己滚回底部后 → 恢复跟随，新消息应把视图带到最新

用法：python _cdp_rollout.py
"""
import json
import sys
import time
import urllib.request

import websocket

BACKEND = "http://127.0.0.1:8000"
FRONTEND = "http://localhost:3000"
CDP_PORT = 9333
ROOM_NAME = "scdp-rollout-test"
USERNAME = "scdp-rollout-user"

# 关掉代理：本机 http_proxy 会把 127.0.0.1 的请求也送进代理，一律超时。
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http_json(method, url, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    with _opener.open(req, timeout=30) as resp:
        raw = resp.read().decode()
        return json.loads(raw) if raw else None


_id = [0]


def cdp(ws, method, params=None):
    _id[0] += 1
    ws.send(json.dumps({"id": _id[0], "method": method, "params": params or {}}))
    while True:
        msg = json.loads(ws.recv())
        if msg.get("id") == _id[0]:
            if "error" in msg:
                raise RuntimeError(f"{method} -> {msg['error']}")
            return msg.get("result", {})


def ev(ws, expression):
    r = cdp(ws, "Runtime.evaluate", {
        "expression": expression, "returnByValue": True, "awaitPromise": True,
    })
    res = r.get("result", {})
    if res.get("subtype") == "error":
        raise RuntimeError(f"JS error: {res.get('description')}")
    return res.get("value")


# 消息区滚动容器的选择器：classroom 页里唯一同时含 overflow-y-auto + px-6 + py-5 的 div
SCROLLER_EXPR = ("[...document.querySelectorAll('div')].find(d => "
                 "typeof d.className === 'string' "
                 "&& d.className.includes('overflow-y-auto') "
                 "&& d.className.includes('px-6') "
                 "&& d.className.includes('py-5'))")

_METRICS_TPL = """(() => {
  const el = __SCROLLER__;
  if (!el) return {found: false};
  return {
    found: true,
    scrollTop: Math.round(el.scrollTop),
    scrollHeight: Math.round(el.scrollHeight),
    clientHeight: Math.round(el.clientHeight),
    distance: Math.round(el.scrollHeight - el.scrollTop - el.clientHeight),
    bubbles: document.querySelectorAll('.space-y-4 > *').length,
  };
})()"""
METRICS = _METRICS_TPL.replace("__SCROLLER__", SCROLLER_EXPR)

SCROLL_BOTTOM = ("(() => { const el = " + SCROLLER_EXPR
                 + "; if (el) el.scrollTop = el.scrollHeight; return 1; })()")
SCROLL_UP = ("(() => { const el = " + SCROLLER_EXPR
             + "; if (el) el.scrollTop = el.scrollTop - 600; return 1; })()")


def main():
    result = {"steps": [], "pass": True}

    def check(name, ok, detail):
        result["steps"].append({"name": name, "ok": bool(ok), "detail": detail})
        if not ok:
            result["pass"] = False

    # ---------- 1. 造数据 ----------
    user = http_json("POST", f"{BACKEND}/users", {"username": USERNAME, "role": "student"})
    uid = user["id"]
    room = http_json("POST", f"{BACKEND}/classrooms", {
        "name": ROOM_NAME, "subject": "cs", "members": [uid],
    })
    cid = room["id"]

    filler = ("这是一条用于制造滚动溢出的测试消息，编号 %02d。"
              "内容刻意写长，确保消息列表高度远超视口，从而产生可滚动的历史区域。")
    for i in range(24):
        http_json("POST", f"{BACKEND}/classrooms/{cid}/messages",
                  {"user_id": uid, "content": filler % i})

    # ---------- 2. 打开页面 ----------
    tgt = http_json("PUT", f"http://127.0.0.1:{CDP_PORT}/json/new?about:blank")
    # suppress_origin：Chrome 默认只接受空 Origin 或白名单 Origin 的 WS 握手，
    # 否则直接 403（启动参数 --remote-allow-origins 也能解，但不必重启实例）。
    ws = websocket.create_connection(
        tgt["webSocketDebuggerUrl"], timeout=30, suppress_origin=True
    )
    cdp(ws, "Page.enable")
    cdp(ws, "Runtime.enable")

    # 先落同源页面，再写 localStorage（about:blank 写不了 localhost 的存储）
    cdp(ws, "Page.navigate", {"url": FRONTEND + "/"})
    time.sleep(2.0)
    identity = json.dumps({
        "userId": uid, "username": USERNAME, "role": "student",
        "classroomId": cid, "classroomName": ROOM_NAME,
    }, ensure_ascii=False)
    ev(ws, f"localStorage.setItem('ai-classroom/identity', {json.dumps(identity)});"
           f"localStorage.setItem('ai-classroom/backend-url', '{BACKEND}'); 1")

    cdp(ws, "Page.navigate", {"url": FRONTEND + "/classroom/"})
    time.sleep(4.0)

    m0 = ev(ws, METRICS)
    check("A 首次加载自动贴底", m0["found"] and m0["bubbles"] >= 24 and m0["distance"] <= 80, m0)

    # ---------- 3. 用户上滚，期间插入新消息 ----------
    ev(ws, SCROLL_BOTTOM)
    time.sleep(0.6)
    ev(ws, SCROLL_UP)
    time.sleep(0.6)
    before = ev(ws, METRICS)

    # 往库里塞 2 条：轮询 5s 后会拉到，DOM 变高
    for i in range(2):
        http_json("POST", f"{BACKEND}/classrooms/{cid}/messages",
                  {"user_id": uid, "content": f"轮询期间到达的新消息 {i}，" + filler % (90 + i)})

    time.sleep(7.5)  # 跨过至少一次 POLL_MS=5000
    after = ev(ws, METRICS)

    grew = after["bubbles"] > before["bubbles"]
    kept = abs(after["scrollTop"] - before["scrollTop"]) <= 5
    check("B 上滚后新消息到达，滚动位置不被夺走",
          grew and kept and before["distance"] > 80,
          {"before": before, "after": after, "bubbles_grew": grew,
           "scrolltop_drift": after["scrollTop"] - before["scrollTop"]})

    # ---------- 4. 滚回底部后应恢复跟随 ----------
    ev(ws, SCROLL_BOTTOM)
    time.sleep(0.8)
    http_json("POST", f"{BACKEND}/classrooms/{cid}/messages",
              {"user_id": uid, "content": "恢复跟随验证：" + filler % 99})
    time.sleep(7.5)
    final = ev(ws, METRICS)
    check("C 滚回底部后恢复跟随", final["distance"] <= 80,
          {"after": final, "bubbles": final["bubbles"]})

    ws.close()
    print(json.dumps({"classroom_id": cid, "user_id": uid, **result},
                     ensure_ascii=False, indent=2))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
