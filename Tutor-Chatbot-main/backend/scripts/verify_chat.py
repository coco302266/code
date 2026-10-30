#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
verify_chat.py  (stdlib only; the M2 section additionally imports the local
 `agent.pretest` item bank, which is stdlib-only too)
One-shot end-to-end check for backend changes:
  #2 sources persistence  -> inspect SSE 'sources' events + persisted message
  #3 GET /classrooms       -> 'my classrooms' listing
  #M1 chatroom             -> private DM + members, group post (student/teacher),
                              incremental poll (?after=), agent_role persisted
  #M2 CDT diagnosis        -> pretest paper (no leaked answers), IRT scoring,
                              ability_vector / cognitive_style persisted,
                              knowledge-graph mastery overlay
Run with the backend venv python while the server is up on :8000.
"""
import json
import os
import sys
import urllib.request
import urllib.error

# 保证从任何 cwd 启动都能 import 到 agent.*
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # backend/

# 只有 M2 需要：后端有意不在组卷结果里返回答案，本地要用标准答案构造"全对"和
# "概念题全错"两份答卷，只能直接读题库。读不到时 M2 段会跳过而不是崩掉，
# 这样只验 #2/#3/#M1 的场景依然能跑。
try:
    from agent.pretest import get_item as _get_item
except Exception:
    _get_item = None

BASE = "http://127.0.0.1:8000"

# force direct connection (avoid any system proxy intercepting localhost)
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
urllib.request.install_opener(_opener)


def _req(method, path, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with _opener.open(req, timeout=30) as r:
            raw = r.read().decode("utf-8", "replace")
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # timeout / connection refused
        return "ERR", type(e).__name__ + ": " + str(e)[:160]


def main():
    print("=== 1) create user ===")
    st, user = _req("POST", "/users", {"username": "verify_bot", "role": "student"})
    print("   status", st, "user:", user)
    uid = (user or {}).get("id") or (user or {}).get("user_id")
    if not uid:
        print("   !! cannot get user_id, abort")
        return

    print("=== 2) create classroom ===")
    st, room = _req("POST", "/classrooms",
                    {"name": "verify_room", "subject": "cs", "description": "verify"})
    print("   status", st, "room:", room)
    cid = (room or {}).get("id")
    if not cid:
        print("   !! cannot get classroom_id, abort")
        return

    print("=== 3) join member ===")
    st, _ = _req("POST", f"/classrooms/{cid}/members", {"user_id": uid})
    print("   status", st)

    print("=== 4) POST /chat (SSE) ===")
    payload = {
        "message": "死锁的四个必要条件是什么？",
        "classroom_id": cid,
        "user_id": uid,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        BASE + "/chat", data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    saw_sources_event = False
    sources_event_count = 0
    answer_chars = 0
    try:
        with _opener.open(req, timeout=90) as r:
            for raw in r:
                line = raw.decode("utf-8", "replace").rstrip("\n")
                if not line:
                    continue
                if line.startswith("event:"):
                    evt = line[len("event:"):].strip()
                    if evt == "sources":
                        saw_sources_event = True
                elif line.startswith("data:"):
                    d = line[len("data:"):].strip()
                    try:
                        obj = json.loads(d)
                    except Exception:
                        obj = None
                    if obj is not None and obj.get("type") == "sources":
                        sources_event_count += 1
                        n = len(obj.get("items", []))
                        print(f"   [sources event] items={n} "
                              f"sample={[ (i.get('topic') or i.get('title')) for i in obj.get('items', [])[:2] ]}")
                    if obj is not None and obj.get("type") in ("answer", "token"):
                        answer_chars += len(obj.get("content", ""))
                    if obj is not None and obj.get("type") == "error":
                        print("   [ERROR event from server] content=",
                              obj.get("content"))
    except Exception as e:
        print("   CHAT ERROR:", type(e).__name__, str(e)[:160])
    print(f"   saw_sources_event={saw_sources_event} sources_event_count={sources_event_count} answer_chars={answer_chars}")

    print("=== 5) GET /classrooms/{cid}/messages (check persisted sources) ===")
    st, msg_resp = _req("GET", f"/classrooms/{cid}/messages")
    # API returns a dict {classroom_id, count, messages:[...]}; unwrap to the list
    if isinstance(msg_resp, dict):
        msgs = msg_resp.get("messages", [])
    elif isinstance(msg_resp, list):
        msgs = msg_resp
    else:
        msgs = []
    print("   status", st, "msg_count=", len(msgs))
    if msgs:
        for m in msgs:
            if m.get("role") == "assistant":
                src = m.get("sources")
                print(f"   assistant msg id={m.get('id')} sources_type={type(src).__name__} "
                      f"sources_len={len(src) if isinstance(src, list) else 'n/a'}")
                if isinstance(src, list) and src:
                    print("   PERSISTED sources sample:",
                          [(s.get("topic") or s.get("title")) for s in src[:2]])
                break

    print("=== 6) GET /classrooms?user_id={uid} (#3) ===")
    st, cl = _req("GET", f"/classrooms?user_id={uid}")
    print("   status", st, "resp:", cl)

    print("\n=== M1) chatroom + private DM + agent_role ===")
    # (a) 私立 DM 房间：建房间时一次性把两名学生/老师加为成员
    st, tuser = _req("POST", "/users", {"username": "verify_teacher", "role": "teacher"})
    tid = (tuser or {}).get("id")
    st, dm = _req("POST", "/classrooms", {
        "name": "DM_bot_teacher", "subject": "cs", "is_private": True,
        "members": [uid],
        "teacher_members": [tid] if tid else None,
    })
    dmid = (dm or {}).get("id")
    print("   create private DM status", st, "is_private=", (dm or {}).get("is_private"))
    m1_private_ok = bool(dmid and (dm or {}).get("is_private") is True)
    m1_group_post_ok = False
    m1_after_ok = False
    m1_agent_role_ok = False
    if dmid:
        # (b) 群聊发消息：学生发言
        st, pm = _req("POST", f"/classrooms/{dmid}/messages",
                      {"user_id": uid, "content": "同学你好，一起复习死锁吧"})
        stu_role = (pm or {}).get("role")
        stu_name = (pm or {}).get("username")
        # (b) 群聊发消息：老师发言
        st, pm2 = _req("POST", f"/classrooms/{dmid}/messages",
                       {"user_id": tid, "content": "好的，先讲互斥条件"})
        tea_role = (pm2 or {}).get("role")
        tea_name = (pm2 or {}).get("username")
        # 学生发言 role=student 且带 username；老师发言 role=teacher 且带 username
        m1_group_post_ok = (
            stu_role == "student" and stu_name and
            tea_role == "teacher" and tea_name
        )
        print(f"   student post role={stu_role} name={stu_name}; "
              f"teacher post role={tea_role} name={tea_name}")
        # (c) 增量轮询：先全量，再 after=最后一条 id
        st, lst = _req("GET", f"/classrooms/{dmid}/messages")
        msgs_dm = lst.get("messages", []) if isinstance(lst, dict) else []
        last_id = msgs_dm[-1]["id"] if msgs_dm else 0
        st, after_lst = _req("GET", f"/classrooms/{dmid}/messages?after={last_id}")
        after_msgs = after_lst.get("messages", []) if isinstance(after_lst, dict) else []
        m1_after_ok = (len(after_msgs) == 0)  # after 最后一条 => 0 条新
        print(f"   list={len(msgs_dm)} msgs; after={last_id} returned {len(after_msgs)} new")

        # (d) /chat 以 teacher 角色发言，校验落库 agent_role
        payload = {
            "message": "死锁的不可剥夺条件是什么？",
            "classroom_id": dmid,
            "user_id": uid,
            "agent_role": "teacher",
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            BASE + "/chat", data=data, method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with _opener.open(req, timeout=90) as r:
                for raw in r:
                    line = raw.decode("utf-8", "replace").rstrip("\n")
                    if line.startswith("data:"):
                        d = line[len("data:"):].strip()
                        try:
                            obj = json.loads(d)
                        except Exception:
                            obj = None
                        if obj is not None and obj.get("type") in ("answer", "token"):
                            answer_chars += len(obj.get("content", ""))
        except Exception as e:
            print("   CHAT ERROR:", type(e).__name__, str(e)[:160])
        st, lst2 = _req("GET", f"/classrooms/{dmid}/messages")
        msgs_dm2 = lst2.get("messages", []) if isinstance(lst2, dict) else []
        for m in reversed(msgs_dm2):
            if m.get("role") == "assistant":
                m1_agent_role_ok = (m.get("agent_role") == "teacher")
                print(f"   assistant msg agent_role={m.get('agent_role')} "
                      f"(expect teacher)")
                break

    # --- M2 认知诊断 -----------------------------------------------------
    # 复用第 2 步建的课堂和第 1 步的用户，不再新建，避免每次跑灌一堆脏数据。
    m2_paper_ok = False
    m2_mastery_ok = False
    m2_style_ok = False
    m2_graph_ok = False

    print("\n=== M2) CDT pretest + knowledge graph ===")
    if _get_item is None:
        print("   !! cannot import agent.pretest (run this from backend/ with the venv), skip")
    else:
        st, paper = _req("POST", f"/students/{uid}/pretest",
                         {"classroom_id": cid, "limit": 8})
        items = (paper or {}).get("items", []) if isinstance(paper, dict) else []
        leaked = [i["id"] for i in items if "answer" in i or "explanation" in i]
        m2_paper_ok = st == 200 and len(items) >= 6 and not leaked
        print(f"   pretest paper: status={st} count={len(items)} "
              f"answer_leaked={leaked or 'NO'}")

        if not items:
            print("   !! empty paper, skip scoring checks")
        else:
            # 全对卷：所有知识点的掌握度都应被推到显著高于 0.5
            answers_right = [
                {"item_id": i["id"], "choice": _get_item(i["id"])["answer"]}
                for i in items if _get_item(i["id"])
            ]
            st, res = _req("POST", f"/students/{uid}/pretest/submit",
                           {"classroom_id": cid, "answers": answers_right})
            prof = (res or {}).get("profile", {}) if isinstance(res, dict) else {}
            updates = (res or {}).get("mastery_updates", {}) or {}
            m2_mastery_ok = (
                st == 200
                and (res or {}).get("accuracy") == 1.0
                and all(v > 0.5 for v in updates.values())
                and bool(prof.get("ability_vector"))
                and bool(prof.get("pretested_at"))
            )
            print(f"   all-right: status={st} accuracy={res.get('accuracy')} "
                  f"level={prof.get('overall_level')}")
            print(f"   ability_vector keys={len(prof.get('ability_vector') or {})} "
                  f"pretested_at={prof.get('pretested_at')}")

            # 混合卷：事实题对、概念题错 + 慢答 + 要线索 => 应判 sensing + reflective
            mixed = []
            for i in items:
                item = _get_item(i["id"])
                if not item:
                    continue
                if item["type"] == "conceptual":
                    choice = (item["answer"] + 1) % len(item["options"])
                else:
                    choice = item["answer"]
                mixed.append({"item_id": i["id"], "choice": choice,
                              "elapsed_ms": 12000, "hint_requested": True})
            st2, res2 = _req("POST", f"/students/{uid}/pretest/submit",
                             {"classroom_id": cid, "answers": mixed})
            style = (res2 or {}).get("cognitive_style") or {}
            poles = {d.get("key"): d.get("pole")
                     for d in (style.get("dimensions") or [])}
            m2_style_ok = (
                st2 == 200
                and style.get("ready") is True
                and poles.get("sensing_intuitive") == "sensing"
            )
            print(f"   mixed: status={st2} accuracy={res2.get('accuracy')} poles={poles}")
            print(f"   weak_points={len(((res2 or {}).get('profile') or {}).get('weak_points') or [])}")

            st3, kg = _req("GET", f"/students/{uid}/knowledge-graph?classroom_id={cid}")
            nodes = (kg or {}).get("nodes", []) if isinstance(kg, dict) else []
            measured = [n for n in nodes if n.get("measured")]
            m2_graph_ok = st3 == 200 and len(nodes) >= 8 and len(measured) >= 4
            print(f"   knowledge-graph: status={st3} nodes={len(nodes)} "
                  f"measured={len(measured)} "
                  f"edges={len((kg or {}).get('edges') or [])}")

    print("\n=== VERDICT ===")
    print("  #2 sources streamed in /chat :",
          "YES" if sources_event_count > 0 else "NO")
    print("  #2 sources persisted to DB   :",
          "YES" if isinstance(msgs, list) and any(
              m.get("role") == "assistant" and isinstance(m.get("sources"), list) and m.get("sources")
              for m in msgs) else "NO / check above")
    print("  #3 GET /classrooms works     :",
          "YES" if isinstance(cl, dict) and cl.get("count", 0) >= 1 else "NO / check above")
    print("  #M1 private DM + members     :",
          "YES" if m1_private_ok else "NO / check above")
    print("  #M1 group post (student/teacher):",
          "YES" if m1_group_post_ok else "NO / check above")
    print("  #M1 incremental poll after=  :",
          "YES" if m1_after_ok else "NO / check above")
    print("  #M1 agent_role persisted     :",
          "YES" if m1_agent_role_ok else "NO / check above")
    print("  #M2 pretest paper (no leak)  :",
          "YES" if m2_paper_ok else "NO / check above")
    print("  #M2 IRT scoring + evidence   :",
          "YES" if m2_mastery_ok else "NO / check above")
    print("  #M2 cognitive style inferred :",
          "YES" if m2_style_ok else "NO / check above")
    print("  #M2 knowledge-graph overlay  :",
          "YES" if m2_graph_ok else "NO / check above")


if __name__ == "__main__":
    main()
