"""T7 端到端验收：教师备课资源生成（Teacher Resource Agent）。

用法（需先启动后端）：
    python smoke_resource.py

覆盖 T7.1~T7.5：
1. 教师输入主题 → 检索知识库 → 输出大纲（T7.2 / T7.3）
2. 输出结构符合 T7.4 的七个字段，且每一段都有内容（T7.4）
3. 同时生成一个真实可下载的 .pptx（T7.5）
4. 权限：学生不能备课、非成员不能备课
5. 主题带斜杠等非法字符时文件名依然合法

注意 /resources/ppt-outline 有 5 次/分钟限流，本脚本用掉 4 次。
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8000"
LOG = open("smoke_resource_out.txt", "w", encoding="utf-8")

TOPIC = "死锁"
TRICKY_TOPIC = "进程/线程 的区别"


def log(msg=""):
    print(msg)
    LOG.write(msg + "\n")
    LOG.flush()


def req(method, path, payload=None, timeout=180, expect_error=False):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    r = urllib.request.Request(
        BASE + urllib.parse.quote(path, safe="/?&=:"),
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        resp = urllib.request.urlopen(r, timeout=timeout)
        raw = resp.read()
        return (json.loads(raw.decode("utf-8")) if raw else {}), resp.status
    except urllib.error.HTTPError as e:
        if expect_error:
            return None, e.code
        raise SystemExit(f"{method} {path} -> HTTP {e.code}: {e.read().decode()[:300]}")


def download(path):
    r = urllib.request.Request(BASE + urllib.parse.quote(path, safe="/?&=:"))
    resp = urllib.request.urlopen(r, timeout=60)
    return resp.read(), dict(resp.headers)


def check_outline(outline, label):
    """T7.4 的七个字段都得有内容，空壳大纲比报错更坑教师。"""
    required = {
        "title": outline.get("title"),
        "objectives": outline.get("objectives"),
        "sections": outline.get("sections"),
        "key_points": outline.get("key_points"),
        "examples": outline.get("examples"),
        "questions": outline.get("questions"),
        "summary": outline.get("summary"),
    }
    for field, value in required.items():
        if not value:
            raise SystemExit(f"{label}：大纲字段 {field} 是空的")
    for section in outline["sections"]:
        if not section.get("title") or not section.get("points"):
            raise SystemExit(f"{label}：章节 {section} 缺标题或缺要点")


def main() -> None:
    h, _ = req("GET", "/health")
    log("health: %s" % h)
    if h.get("database") != "up":
        raise SystemExit("数据库不可用：%s" % h.get("database"))

    room = req("POST", "/classrooms",
               {"name": "T7 备课班", "subject": "cs", "description": "T7 验收"})[0]
    stamp = str(int(time.time()))[-6:]
    teacher = req("POST", "/users", {"username": f"teacher_{stamp}", "role": "teacher"})[0]
    student = req("POST", "/users", {"username": f"student_{stamp}", "role": "student"})[0]
    outsider = req("POST", "/users", {"username": f"outsider_{stamp}", "role": "teacher"})[0]

    req("POST", f"/classrooms/{room['id']}/members",
        {"user_id": teacher["id"], "role": "teacher"})
    req("POST", f"/classrooms/{room['id']}/members",
        {"user_id": student["id"], "role": "student"})
    log("1. 课堂 %s｜教师=%s 学生=%s 外人=%s"
        % (room["id"], teacher["id"], student["id"], outsider["id"]))

    # --- T7.2 / T7.3 / T7.4：生成大纲 ---
    result, _ = req("POST", "/resources/ppt-outline", {
        "topic": TOPIC, "classroom_id": room["id"], "teacher_id": teacher["id"],
    })
    outline = result["outline"]
    log()
    log("2. 主题「%s」-> 检索命中=%s" % (TOPIC, result["retrieved"]))
    log("   标题：%s" % outline["title"])
    log("   教学目标 %d 条：%s" % (len(outline["objectives"]), outline["objectives"][:2]))
    log("   章节 %d 个：" % len(outline["sections"]))
    for i, s in enumerate(outline["sections"], 1):
        log("     %d. %s（%d 要点）%s" % (i, s["title"], len(s["points"]), s["points"][:2]))
    log("   常考要点：%s" % outline["key_points"][:3])
    log("   例子：%s" % outline["examples"][:2])
    log("   提问：%s" % outline["questions"][:2])
    log("   总结：%s" % outline["summary"][:2])

    check_outline(outline, "教师备课")
    if not result["retrieved"]:
        raise SystemExit("知识库没命中——备课内容就没了依据，检查 chroma_kb 是否在")
    if len(outline["sections"]) < 3:
        raise SystemExit("章节只有 %d 个，少于约定的 3 个" % len(outline["sections"]))

    # --- T7.5：.pptx 真的生成了吗 ---
    path = result["file_path"]
    log()
    log("3. PPT 文件：%s" % path)
    if not path or not os.path.isfile(path):
        raise SystemExit("PPT 文件没生成：%r" % path)
    size = os.path.getsize(path)
    log("   本地大小 %d 字节" % size)
    if size < 5000:
        raise SystemExit("PPT 只有 %d 字节，多半是空壳" % size)

    blob, headers = download(result["download_url"])
    # HTTP 头名字大小写不保证，直接取可能拿到 None
    content_type = headers.get("Content-Type") or headers.get("content-type") or "?"
    log("   下载 %d 字节，Content-Type=%s" % (len(blob), content_type))
    if len(blob) != size:
        raise SystemExit("下载下来大小和本地不一致：%d vs %d" % (len(blob), size))
    if blob[:2] != b"PK":
        raise SystemExit("下载的不是 zip 结构（.pptx 应该是 PK 开头）")

    # --- 权限：备课是教师专属 ---
    _, code = req("POST", "/resources/ppt-outline", {
        "topic": TOPIC, "classroom_id": room["id"], "teacher_id": student["id"],
    }, expect_error=True)
    log()
    log("4. 学生调用备课 -> HTTP %s（应为 403）" % code)
    _, code2 = req("POST", "/resources/ppt-outline", {
        "topic": TOPIC, "classroom_id": room["id"], "teacher_id": outsider["id"],
    }, expect_error=True)
    log("   非成员教师调用 -> HTTP %s（应为 403）" % code2)
    if code != 403 or code2 != 403:
        raise SystemExit("权限没挡住：学生=%s 外人=%s" % (code, code2))

    # --- 文件名合法性：主题带斜杠 ---
    tricky, _ = req("POST", "/resources/ppt-outline", {
        "topic": TRICKY_TOPIC, "classroom_id": room["id"], "teacher_id": teacher["id"],
    })
    log()
    log("5. 主题「%s」-> 文件名 %s" % (TRICKY_TOPIC, tricky["file_name"]))
    if not tricky["file_name"] or not os.path.isfile(tricky["file_path"]):
        raise SystemExit("带非法字符的主题没生成出合法文件")
    for bad in ('/', '\\', ':', '*', '?', '"', '<', '>', '|'):
        if bad in tricky["file_name"]:
            raise SystemExit("文件名里仍有非法字符 %r" % bad)

    log()
    log("T7 验收全部通过 ✓")


if __name__ == "__main__":
    try:
        main()
    except SystemExit as exc:
        log("❌ 验收失败：" + str(exc))
        raise
