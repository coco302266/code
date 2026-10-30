"""真实 MySQL + 真实服务 端到端验证（零-LLM / 纯 DB 路径）。

打 http://127.0.0.1:8000，覆盖：
  M1  群聊课堂创建 / 私聊(is_private) / 发消息(role判定) / 增量轮询(after) / agent_role 字段
  M2  pretest 组卷 / submit 批改写画像 / knowledge-graph 叠加掌握度
  M3  /resources/generate(quiz) 命中题库零-LLM 出卷 + 教师门禁
不烧 DeepSeek 额度（quiz 走题库、其余为纯算法/DB）。
"""
import json
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
results = []


def call(method, path, body=None, timeout=30):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, e.read().decode()


def check(name, cond, detail=""):
    results.append((name, "PASS" if cond else "FAIL", detail))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}  {detail}")


ts = int(time.time())
# a. 创建教师 / 学生
st, t = call("POST", "/users", {"username": f"t{ts}", "role": "teacher"})
check("M1 创建教师", st == 201, f"tid={t.get('id')}")
tid = t["id"]
st, s = call("POST", "/users", {"username": f"s{ts}", "role": "student"})
check("M1 创建学生", st == 201, f"sid={s.get('id')}")
sid = s["id"]

# b. 群聊课堂（教师+学生成员）
st, c = call("POST", "/classrooms", {"name": "群聊课堂", "subject": "cs", "members": [sid], "teacher_members": [tid]})
check("M1 创建群聊课堂", st == 201, f"cid={c.get('id')}")
cid = c["id"]
# c. 私聊课堂（is_private）
st, dm = call("POST", "/classrooms", {"name": "私聊", "is_private": True, "members": [sid], "teacher_members": [tid]})
check("M1 创建私聊(is_private)", st == 201 and dm.get("is_private") is True, f"dm_id={dm.get('id')} is_private={dm.get('is_private')}")

# d. 学生发言 → role 应为 student, agent_role 应为 None
st, m1 = call("POST", f"/classrooms/{cid}/messages", {"user_id": sid, "content": "老师好"})
check("M1 学生发言 role=student/agent_role=null", st == 201 and m1.get("role") == "student" and m1.get("agent_role") is None, f"id={m1.get('id')}")
m1id = m1["id"]
# e. 教师发言 → role 应为 teacher（以房间成员角色为准）
st, m2 = call("POST", f"/classrooms/{cid}/messages", {"user_id": tid, "content": "同学们好"})
check("M1 教师发言 role=teacher", st == 201 and m2.get("role") == "teacher", f"id={m2.get('id')}")
m2id = m2["id"]
# f. 增量轮询：after=m1id 应只返回 m2
st, lst = call("GET", f"/classrooms/{cid}/messages?after={m1id}")
ids = [x["id"] for x in lst["messages"]]
check("M1 增量轮询(after)", st == 200 and m1id not in ids and m2id in ids, f"返回ids={ids}")
# g. 私聊标志持久化
st, dminfo = call("GET", f"/classrooms/{dm['id']}")
check("M1 私聊标志落库", dminfo.get("is_private") is True)

# h. 先取知识图谱节点名，作为 pretest 的合法 topics（保证命中题库）
st, kg0 = call("GET", f"/students/{sid}/knowledge-graph?classroom_id={cid}")
nodes = kg0.get("nodes", [])
topic_names = [n.get("topic") for n in nodes[:2]]
check("M2 知识图谱节点", st == 200 and len(nodes) > 0, f"nodes={len(nodes)} topics={topic_names}")

# i. pretest 组卷（零-LLM）
st, paper = call("POST", f"/students/{sid}/pretest", {"classroom_id": cid, "topics": topic_names, "limit": 8})
items = paper.get("items", [])
check("M2 pretest 组卷", st == 200 and len(items) > 0, f"出题={len(items)}")
item_ids = [it["id"] for it in items]

# j. pretest 提交（全部选 0，验证批改+写画像链路）
st, sub = call("POST", f"/students/{sid}/pretest/submit", {"classroom_id": cid, "answers": [{"item_id": i, "choice": 0} for i in item_ids]})
prof = sub.get("profile", {})
check("M2 提交批改 graded>0", st == 200 and sub.get("graded", 0) > 0, f"graded={sub.get('graded')} correct={sub.get('correct')} acc={sub.get('accuracy')}")
check("M2 画像写入(pretested_at)", bool(prof.get("pretested_at")), f"pretested_at={prof.get('pretested_at')}")
check("M2 掌握度已更新", bool(prof.get("knowledge_mastery")), f"mastery_keys={list((prof.get('knowledge_mastery') or {}).keys())}")

# k. knowledge-graph 叠加掌握度（提交后应有 mastery>0 的节点）
st, kg1 = call("GET", f"/students/{sid}/knowledge-graph?classroom_id={cid}")
mastery_vals = [n.get("mastery") for n in kg1.get("nodes", []) if n.get("mastery")]
check("M2 图谱叠加掌握度", st == 200 and any(v > 0 for v in mastery_vals), f"有掌握度节点={sum(1 for v in mastery_vals if v)}")

# l. M3 quiz 命中题库零-LLM 出卷 + 教师门禁
st, quiz = call("POST", "/resources/generate", {"resource_type": "quiz", "topic": topic_names[0], "classroom_id": cid, "teacher_id": tid, "count": 5})
q = quiz.get("resource", {})
check("M3 quiz 零-LLM 题库出卷", st == 200 and quiz.get("from_bank") is True and len(q.get("questions", [])) > 0, f"from_bank={quiz.get('from_bank')} n={len(q.get('questions', []))}")
# l2. 学生当 teacher_id → 应 403（教师门禁）
st2, _ = call("POST", "/resources/generate", {"resource_type": "quiz", "topic": topic_names[0], "classroom_id": cid, "teacher_id": sid, "count": 3})
check("M3 教师门禁(学生被拒403)", st2 == 403, f"status={st2}")

print("\n=== SUMMARY ===")
fails = [r for r in results if r[1] == "FAIL"]
print(f"total={len(results)} pass={len(results)-len(fails)} fail={len(fails)}")
for r in fails:
    print("  FAIL:", r)
