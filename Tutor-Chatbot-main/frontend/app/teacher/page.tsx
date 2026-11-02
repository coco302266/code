"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  addMember,
  ApiError,
  createClassroom,
  deleteClassroomDocument,
  getClassroom,
  getProfile,
  listClassroomDocuments,
  listMembers,
  listMessages,
  listScenarios,
  listUsers,
  pptOutline,
  resourceUrl,
  uploadClassroomDocument,
} from "@/lib/api";
import {
  loadIdentity,
  loadRecentClassrooms,
  rememberClassroom,
  saveIdentity,
  type Identity,
  type RecentClassroom,
} from "@/lib/identity";
import type {
  ClassroomDocument,
  Member,
  PptOutlineResult,
  ScenarioItem,
  StudentProfile,
  User,
} from "@/lib/types";
import { MessageRow, type UIMsg } from "@/components/chat";
import { ProfileView } from "@/components/ProfileView";
import { Shell } from "@/components/Shell";
import {
  Banner,
  Btn,
  Card,
  Empty,
  Field,
  Input,
  Select,
  Spinner,
  Tag,
} from "@/components/ui";

type Tab = "room" | "prepare" | "records";

const TABS: { key: Tab; label: string; hint: string }[] = [
  { key: "room", label: "课堂", hint: "创建 / 加入课堂，看学生名单" },
  { key: "prepare", label: "备课", hint: "输入主题 → 大纲 + PPT" },
  { key: "records", label: "记录", hint: "全量对话 + 学生画像" },
];

const AUDIENCE = [
  ["", "让模型自己判断"],
  ["beginner", "入门"],
  ["intermediate", "进阶"],
  ["advanced", "熟练"],
] as const;

/**
 * 建房时**开放**给教师的场景。
 *
 * 2026-11-01 起只剩两种：1v1 辅导、小组讨论。
 * `debate`（辩论对抗）按需求整体下线：场景表、背景图、选择项一并删除，
 * 存量课堂的 scenario 已由数据迁移归一为 v1。
 *
 * 出题助手不在这里选：它在任何场景下都存在，学生用 @ 点名才会发言，
 * 不需要在场景下拉里占一个位置。
 */
const OPEN_SCENARIOS = ["v1", "group"];

/** 场景拉不到时的兜底，保证建房流程不会因为一个下拉框失败而卡住。 */
const FALLBACK_SCENARIOS: ScenarioItem[] = [
  { key: "v1", label: "1v1 辅导", steps: [] },
];

/**
 * 页面 4／4：教师工作台。
 *
 * 把「创建课堂 / 学生名单 / 备课 / 课堂记录」四件事压进三个页签，
 * 学生端的「我的课堂」「画像抽屉」到这里都只是换个入口。
 *
 * 契约里点名的一个坑在这里被显式处理掉：
 * **创建课堂不会自动把创建者加为成员**，必须再 POST 一次
 * `/classrooms/{id}/members` 且 `role="teacher"`，否则备课必 403。
 */
export default function TeacherPage() {
  const router = useRouter();

  const [identity, setIdentity] = useState<Identity | null>(null);
  const [ready, setReady] = useState(false);
  const [tab, setTab] = useState<Tab>("room");

  // 课堂
  const [newName, setNewName] = useState("计算机八股冲刺班");
  const [scenarios, setScenarios] = useState<ScenarioItem[]>([]);
  const [scenario, setScenario] = useState("v1");
  // 1v1 指定学生（2026-11-02）：建房时必须点名一位学生，否则任何一个
  // 学生先进来就占住了这个教室。
  // 主讲助教不再是建房选项：2026-11-02 起默认配备，课堂里随时可开关。
  const [students, setStudents] = useState<User[]>([]);
  const [targetStudentId, setTargetStudentId] = useState("");
  const [joinId, setJoinId] = useState("");
  const [busy, setBusy] = useState(false);
  const [roomError, setRoomError] = useState("");
  const [members, setMembers] = useState<Member[]>([]);
  const [recent, setRecent] = useState<RecentClassroom[]>([]);

  // 备课
  const [topic, setTopic] = useState("TCP 三次握手");
  const [audience, setAudience] = useState<string>("");
  const [preparing, setPreparing] = useState(false);
  const [prepError, setPrepError] = useState("");
  const [result, setResult] = useState<PptOutlineResult | null>(null);

  // 课堂资料（XB-24：此前教师端界面上根本没有上传入口，接口等于不可达）
  const [docs, setDocs] = useState<ClassroomDocument[]>([]);
  const [docsLoading, setDocsLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [docError, setDocError] = useState("");
  const [docNotice, setDocNotice] = useState("");

  // 记录
  const [messages, setMessages] = useState<UIMsg[]>([]);
  const [selectedUserId, setSelectedUserId] = useState<number | null>(null);
  const [profile, setProfile] = useState<StudentProfile | null>(null);
  const [profileNote, setProfileNote] = useState("");

  const classroomId = identity?.classroomId ?? null;

  useEffect(() => {
    const saved = loadIdentity();
    setIdentity(saved);
    setRecent(loadRecentClassrooms());
    setReady(true);
  }, []);

  // M4：场景下拉的数据源是 GET /scenarios，不写死在前端
  useEffect(() => {
    if (!ready) return;
    let alive = true;
    void listScenarios()
      .then((data) => {
        if (alive) setScenarios(data.items ?? []);
      })
      .catch(() => {
        // 失败就走兜底选项，不阻塞建房
      });
    return () => {
      alive = false;
    };
  }, [ready]);

  /**
   * 学生名单：1v1 建房时「指定学生」下拉的数据源。
   *
   * 切回「课堂」页签时重拉一次——学生可能刚在这台机器之外注册完，
   * 缓存住名单会让老师找不到人。失败就留空数组，由界面提示「还没有
   * 学生注册」，不要因为一个下拉把建房整条路堵死。
   */
  useEffect(() => {
    if (!ready) return;
    let alive = true;
    void listUsers("student")
      .then((data) => {
        if (alive) setStudents(data.items ?? []);
      })
      .catch(() => {
        if (alive) setStudents([]);
      });
    return () => {
      alive = false;
    };
  }, [ready, tab]);

  const scenarioOptions =
    scenarios.length > 0
      ? scenarios.filter((item) => OPEN_SCENARIOS.includes(item.key))
      : FALLBACK_SCENARIOS;
  const scenarioHint = (scenarioOptions.find((i) => i.key === scenario)?.steps ??
    [])
    .map((step) => step.label)
    .join(" → ");

  const refreshMembers = useCallback(async () => {
    if (!classroomId) return;
    try {
      const data = await listMembers(classroomId);
      setMembers(data.members);
    } catch {
      // 忽略：名单拿不到不影响主流程
    }
  }, [classroomId]);

  useEffect(() => {
    if (!ready || !classroomId) {
      setMembers([]);
      return;
    }
    void refreshMembers();
  }, [ready, classroomId, refreshMembers]);

  // --- 课堂资料 ----------------------------------------------------------

  const refreshDocs = useCallback(async () => {
    if (!classroomId || !identity) {
      setDocs([]);
      return;
    }
    setDocsLoading(true);
    try {
      const data = await listClassroomDocuments(classroomId, identity.userId);
      setDocs(data.documents ?? []);
      setDocError("");
    } catch (err) {
      // 403 = 你不是这个课堂的教师；其余按接口报错展示
      setDocError(
        err instanceof ApiError && err.status === 403
          ? "只有本课堂的教师能查看课堂资料。"
          : err instanceof Error
            ? err.message
            : "读取课堂资料失败",
      );
    } finally {
      setDocsLoading(false);
    }
  }, [classroomId, identity]);

  useEffect(() => {
    if (!ready) return;
    void refreshDocs();
  }, [ready, refreshDocs]);

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    // 清空 input，否则「删掉再传同一个文件」不会触发 onChange
    e.target.value = "";
    if (!file || !identity || !classroomId) return;

    setDocError("");
    setDocNotice("");
    // 前端先挡两道最常犯的错，省一次 16~20 秒的往返
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      setDocError("只支持 PDF 文件。");
      return;
    }
    if (file.size > 10 * 1024 * 1024) {
      setDocError("文件超过 10MB 上限。");
      return;
    }

    setUploading(true);
    try {
      const res = await uploadClassroomDocument({
        file,
        classroomId,
        teacherId: identity.userId,
        persist: "classroom",
      });
      setDocNotice(
        `「${res.filename}」已入库：${res.chunk_count ?? 0} 个切片。` +
          "本课堂学生下次提问就能检索到这份资料。",
      );
      await refreshDocs();
    } catch (err) {
      setDocError(err instanceof Error ? err.message : "上传失败");
    } finally {
      setUploading(false);
    }
  }

  async function handleDeleteDoc(doc: ClassroomDocument) {
    if (!identity || !classroomId) return;
    const ok = window.confirm(
      `删除「${doc.filename}」？\n删掉后学生立刻检索不到这份资料（原件与索引一起清掉）。`,
    );
    if (!ok) return;
    setDocError("");
    setDocNotice("");
    try {
      await deleteClassroomDocument(classroomId, doc.doc_id, identity.userId);
      setDocNotice(`已删除「${doc.filename}」。`);
      await refreshDocs();
    } catch (err) {
      setDocError(err instanceof Error ? err.message : "删除失败");
    }
  }

  useEffect(() => {
    if (!ready || !classroomId || tab !== "records") return;
    void (async () => {
      try {
        const data = await listMessages(classroomId, 200);
        setMessages(
          data.messages.map((m) => ({
            key: `db-${m.id}`,
            role: m.role,
            username: m.username,
            content: m.content,
            topic: m.topic,
            createdAt: m.created_at,
          })),
        );
      } catch (err) {
        setRoomError(err instanceof Error ? err.message : "读取记录失败");
      }
    })();
    void refreshMembers();
  }, [ready, classroomId, tab, refreshMembers]);

  useEffect(() => {
    if (!classroomId || selectedUserId === null) return;
    void (async () => {
      try {
        const data = await getProfile(selectedUserId, classroomId);
        setProfile(data);
        setProfileNote("");
      } catch (err) {
        setProfile(null);
        setProfileNote(
          err instanceof ApiError && err.status === 404
            ? "这个学生还没有画像 —— 聊过几轮才会生成。"
            : "读取画像失败。",
        );
      }
    })();
  }, [classroomId, selectedUserId]);

  // --- 课堂操作 ----------------------------------------------------------

  async function persistIdentity(next: Identity, name: string) {
    saveIdentity(next);
    setIdentity(next);
    rememberClassroom({ id: next.classroomId as number, name, role: "teacher" });
    setRecent(loadRecentClassrooms());
  }

  async function createRoom() {
    if (!identity) return;
    const name = newName.trim();
    if (!name) {
      setRoomError("课堂名不能为空。");
      return;
    }
    // 1v1 必须点名学生：不指定就等于开门让人抢座，教师事后也改不了
    // （课堂一旦有人进来，换指定学生会把已在里面的那位踢不出去）。
    const target = targetStudentId ? Number(targetStudentId) : null;
    if (scenario === "v1" && (!target || !Number.isInteger(target))) {
      setRoomError("1v1 教室必须指定一位学生。先在下方下拉里选人。");
      return;
    }
    setBusy(true);
    setRoomError("");
    try {
      const room = await createClassroom({
        name,
        scenario,
        // 助教建课时一律配备，进课堂后可以随开随关。
        useTutor: true,
        targetStudentId: target,
      });
      // 关键：创建课堂不会自动加成员，必须显式以 teacher 身份加入
      await addMember(room.id, identity.userId, "teacher");
      await persistIdentity(
        { ...identity, classroomId: room.id, classroomName: room.name },
        room.name,
      );
      setTab("prepare");
    } catch (err) {
      setRoomError(err instanceof Error ? err.message : "创建课堂失败");
    } finally {
      setBusy(false);
    }
  }

  async function joinRoom(id: number) {
    if (!identity) return;
    setBusy(true);
    setRoomError("");
    try {
      await addMember(id, identity.userId, "teacher");
      const room = await getClassroom(id);
      await persistIdentity(
        { ...identity, classroomId: room.id, classroomName: room.name },
        room.name,
      );
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        setRoomError(`课堂 ${id} 不存在。`);
      } else {
        setRoomError(err instanceof Error ? err.message : "加入课堂失败");
      }
    } finally {
      setBusy(false);
    }
  }

  // --- 备课 --------------------------------------------------------------

  async function prepare() {
    if (!identity || !classroomId) return;
    const text = topic.trim();
    if (!text) {
      setPrepError("先填一个教学主题，例如「TCP 三次握手」。");
      return;
    }
    setPreparing(true);
    setPrepError("");
    setResult(null);
    try {
      const data = await pptOutline({
        topic: text,
        classroomId,
        teacherId: identity.userId,
        audience: audience as "" | "beginner" | "intermediate" | "advanced",
      });
      setResult(data);
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) {
        setPrepError(
          "没有备课权限：你不是这个课堂的教师。请先在「课堂」页签里以教师身份加入课堂。",
        );
      } else if (err instanceof ApiError && err.status === 502) {
        setPrepError("模型这次没生成出可用的大纲，直接再点一次「生成」即可。");
      } else {
        setPrepError(err instanceof Error ? err.message : "生成失败");
      }
    } finally {
      setPreparing(false);
    }
  }

  // --- 渲染 --------------------------------------------------------------

  if (!ready) {
    return (
      <Shell>
        <Empty>
          <Spinner />
        </Empty>
      </Shell>
    );
  }

  if (!identity) {
    return (
      <Shell backHref="/" backLabel="入口">
        <div className="mx-auto max-w-[560px] px-6 py-16">
          <Banner tone="warn">
            还没有身份。请先回到
            <button
              onClick={() => router.push("/")}
              className="mx-1 text-brand underline-offset-2 hover:underline"
            >
              入口页
            </button>
            以教师身份登录。
          </Banner>
        </div>
      </Shell>
    );
  }

  return (
    <Shell
      backdrop="teacher"
      identity={identity}
      right={
        <div className="flex items-center gap-2">
          <Btn variant="ghost" onClick={() => router.push("/classrooms/")}>
            我的课堂
          </Btn>
          {classroomId && (
            <Btn variant="subtle" onClick={() => router.push("/classroom/")}>
              进入聊天室
            </Btn>
          )}
        </div>
      }
      title={
        <span className="flex items-center gap-2">
          <span>教师工作台</span>
          {classroomId ? (
            <Tag tone="teacher">
              {identity.classroomName ?? `课堂 ${classroomId}`} #{classroomId}
            </Tag>
          ) : (
            <Tag tone="warn">还没选课堂</Tag>
          )}
        </span>
      }
    >
      <div className="mx-auto max-w-[960px] px-6 py-6">
        <div className="flex gap-1.5 rounded-xl border border-white/60 bg-panel/64 p-1.5 shadow-[0_16px_40px_-20px_rgba(15,23,42,0.32)] backdrop-blur-2xl">
          {TABS.map((item) => (
            <button
              key={item.key}
              onClick={() => setTab(item.key)}
              className={`flex-1 rounded-lg px-3 py-2.5 text-left transition-colors ${
                tab === item.key ? "bg-brand-soft" : "hover:bg-soft"
              }`}
            >
              <span
                className={`block text-[14px] font-medium ${
                  tab === item.key ? "text-brand" : "text-ink"
                }`}
              >
                {item.label}
              </span>
              <span className="mt-0.5 hidden text-[12px] text-hint sm:block">
                {item.hint}
              </span>
            </button>
          ))}
        </div>

        {roomError && (
          <Banner tone="error" className="mt-5">
            {roomError}
          </Banner>
        )}

        {/* ------------------------------ 课堂 ------------------------------ */}
        {tab === "room" && (
          <div className="mt-5 grid gap-5 md:grid-cols-2">
            <Card glass className="p-5">
              <h2 className="text-[14px] font-medium text-ink">创建新课堂</h2>
              <p className="mt-1 text-[12px] text-hint">
                创建后你会自动成为这个课堂的教师
              </p>
              <div className="mt-4 space-y-3.5">
                <Field label="课堂名">
                  <Input
                    value={newName}
                    onChange={(e) => setNewName(e.target.value)}
                    maxLength={128}
                  />
                </Field>
                <Field
                  label="协作场景"
                  hint={scenarioHint || "建房后本课堂每轮都按此编排"}
                >
                  <Select
                    value={scenario}
                    onChange={(e) => setScenario(e.target.value)}
                  >
                    {scenarioOptions.map((item) => (
                      <option key={item.key} value={item.key}>
                        {item.label}
                      </option>
                    ))}
                  </Select>
                </Field>
                {scenario === "v1" ? (
                  <Field
                    label="指定学生"
                    hint={
                      students.length > 0
                        ? "只有这位学生能进这个 1v1 教室"
                        : "还没有学生注册 —— 先让学生在入口页注册，拿到 id 后这里就能选"
                    }
                  >
                    <Select
                      value={targetStudentId}
                      onChange={(e) => setTargetStudentId(e.target.value)}
                      disabled={students.length === 0}
                    >
                      <option value="">
                        {students.length > 0 ? "请选择学生…" : "暂无学生"}
                      </option>
                      {students.map((stu) => (
                        <option key={stu.id} value={String(stu.id)}>
                          {stu.username}（id={stu.id}）
                        </option>
                      ))}
                    </Select>
                  </Field>
                ) : (
                  <p className="rounded-lg border border-line bg-soft px-3 py-2.5 text-[12px] text-sub">
                    小组讨论课堂不指定学生，学生用课堂号自行加入。
                  </p>
                )}
                {/* 助教/出题助手不再是建房选项：建课时默认都配备，
                    进课堂后在聊天室右上角随时开关助教（2026-11-02）。 */}
                <p className="text-[12px] text-hint">
                  创建后默配备主讲助教与出题助手；进聊天室后可随时关掉助教。
                </p>
                <Btn
                  variant="primary"
                  className="w-full py-2.5"
                  disabled={busy}
                  onClick={() => void createRoom()}
                >
                  {busy && <Spinner />}
                  创建并进入
                </Btn>
              </div>
            </Card>

            <Card glass className="p-5">
              <h2 className="text-[14px] font-medium text-ink">加入已有课堂</h2>
              <p className="mt-1 text-[12px] text-hint">
                已经建过的课堂，用课堂号直接以教师身份加入
              </p>
              <div className="mt-4 space-y-3.5">
                <Field label="课堂号">
                  <Input
                    value={joinId}
                    onChange={(e) => setJoinId(e.target.value)}
                    inputMode="numeric"
                    placeholder="1"
                  />
                </Field>
                <Btn
                  variant="ghost"
                  className="w-full py-2.5"
                  disabled={busy}
                  onClick={() => {
                    const id = Number(joinId);
                    if (!Number.isInteger(id) || id <= 0) {
                      setRoomError("课堂号要填正整数。");
                      return;
                    }
                    void joinRoom(id);
                  }}
                >
                  以教师身份加入
                </Btn>
                <p className="mt-2 text-center text-[12px] text-hint">
                  课堂号问创建这个课堂的教师要
                </p>
              </div>

              {recent.length > 0 && (
                <div className="mt-5 border-t border-line pt-4">
                  <p className="text-[12px] font-medium text-sub">
                    最近用过的课堂
                  </p>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {recent.map((room) => (
                      <button
                        key={room.id}
                        disabled={busy}
                        onClick={() => void joinRoom(room.id)}
                        className="rounded-full border border-line px-3 py-1 text-[12px] text-sub transition-colors hover:border-brand hover:text-brand"
                      >
                        #{room.id} {room.name}
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </Card>

            <Card glass className="p-5 md:col-span-2">
              <div className="flex items-center gap-2">
                <h2 className="text-[14px] font-medium text-ink">学生名单</h2>
                <span className="text-[12px] text-hint">
                  {classroomId ? `共 ${members.length} 人` : "还没选课堂"}
                </span>
                <button
                  onClick={() => void refreshMembers()}
                  className="ml-auto text-[12px] text-hint hover:text-brand"
                >
                  刷新
                </button>
              </div>

              {!classroomId ? (
                <p className="mt-4 text-[13px] text-hint">
                  先创建或加入一个课堂。
                </p>
              ) : members.length === 0 ? (
                <p className="mt-4 text-[13px] text-hint">
                  还没有成员。把课堂号 {classroomId} 发给学生，他们从入口页进来即可。
                </p>
              ) : (
                <div className="mt-4 grid gap-2 sm:grid-cols-2">
                  {members.map((m) => (
                    <div
                      key={m.user_id}
                      className="flex items-center gap-2.5 rounded-lg border border-line px-3 py-2.5"
                    >
                      <Tag tone={m.role === "teacher" ? "teacher" : "brand"}>
                        {m.role === "teacher" ? "教师" : "学生"}
                      </Tag>
                      <span className="min-w-0 flex-1 truncate text-[13px] text-ink">
                        {m.username}
                      </span>
                      <span className="text-[12px] text-hint">#{m.user_id}</span>
                    </div>
                  ))}
                </div>
              )}
            </Card>
          </div>
        )}

        {/* ------------------------------ 备课 ------------------------------ */}
        {tab === "prepare" && (
          <div className="mt-5 grid gap-5 md:grid-cols-[340px_1fr] md:items-start">
            <div className="space-y-5">
            <MaterialsCard
              classroomId={classroomId}
              documents={docs}
              loading={docsLoading}
              uploading={uploading}
              error={docError}
              notice={docNotice}
              onUpload={handleUpload}
              onDelete={handleDeleteDoc}
            />
            <Card className="p-5">
              <h2 className="text-[14px] font-medium text-ink">备课</h2>
              <p className="mt-1 text-[12px] text-hint">
                一次真实的大模型调用，通常要 10–30 秒
              </p>

              <div className="mt-4 space-y-3.5">
                <Field label="教学主题" hint="最多 100 字">
                  <Input
                    value={topic}
                    onChange={(e) => setTopic(e.target.value)}
                    maxLength={100}
                    placeholder="例如：死锁的四个必要条件"
                  />
                </Field>
                <Field label="受众水平" hint="留空让模型判断">
                  <Select
                    value={audience}
                    onChange={(e) => setAudience(e.target.value)}
                  >
                    {AUDIENCE.map(([value, label]) => (
                      <option key={value} value={value}>
                        {label}
                      </option>
                    ))}
                  </Select>
                </Field>

                {!classroomId && (
                  <Banner tone="warn">
                    还没选课堂。备课时后端要校验「你是这个课堂的教师」，
                    请先在「课堂」页签里创建或加入。
                  </Banner>
                )}

                {prepError && <Banner tone="error">{prepError}</Banner>}

                <Btn
                  variant="primary"
                  className="w-full py-2.5"
                  disabled={preparing || !classroomId}
                  onClick={() => void prepare()}
                >
                  {preparing && <Spinner />}
                  {preparing ? "正在生成…" : result ? "重新生成" : "生成大纲"}
                </Btn>

                <p className="text-[12px] text-hint">
                  接口限流 5 次／分钟。生成时后端会先检索八股知识库，
                  所以大纲里的结论是有资料依据的。
                </p>
              </div>
            </Card>
            </div>

            <div className="min-w-0">
              {preparing ? (
                <Card glass className="grid place-items-center p-16">
                  <div className="text-center">
                    <Spinner className="h-5 w-5 text-brand" />
                    <p className="mt-3 text-[13px] text-sub">
                      正在检索知识库并生成大纲…
                      <br />
                      大模型要写完整套结构，请稍等
                    </p>
                  </div>
                </Card>
              ) : result ? (
                <OutlineCard result={result} />
              ) : (
                <Card glass className="p-16">
                  <Empty>
                    还没有生成。填好主题后点「生成大纲」，
                    结果会显示在这里，并附一个可下载的 .pptx
                  </Empty>
                </Card>
              )}
            </div>
          </div>
        )}

        {/* ------------------------------ 记录 ------------------------------ */}
        {tab === "records" && (
          <div className="mt-5 space-y-5">
            {!classroomId ? (
              <Card glass className="p-16">
                <Empty>先创建或加入一个课堂。</Empty>
              </Card>
            ) : (
              <>
                <div className="grid gap-5 md:grid-cols-[260px_1fr] md:items-start">
                  <Card className="p-4">
                    <h2 className="text-[13px] font-medium text-ink">
                      成员（点开看画像）
                    </h2>
                    <div className="mt-2.5 space-y-1">
                      {members.length === 0 ? (
                        <p className="py-2 text-[13px] text-hint">还没有成员</p>
                      ) : (
                        members.map((m) => (
                          <button
                            key={m.user_id}
                            onClick={() => setSelectedUserId(m.user_id)}
                            className={`flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left transition-colors ${
                              selectedUserId === m.user_id
                                ? "bg-brand-soft"
                                : "hover:bg-soft"
                            }`}
                          >
                            <Tag
                              tone={m.role === "teacher" ? "teacher" : "brand"}
                            >
                              {m.role === "teacher" ? "师" : "生"}
                            </Tag>
                            <span className="min-w-0 flex-1 truncate text-[13px] text-ink">
                              {m.username}
                            </span>
                          </button>
                        ))
                      )}
                    </div>
                  </Card>

                  <Card className="p-5">
                    <h2 className="text-[13px] font-medium text-ink">
                      学生画像
                    </h2>
                    <div className="mt-3">
                      {profile ? (
                        <ProfileView profile={profile} />
                      ) : (
                        <p className="text-[13px] text-hint">
                          {selectedUserId === null
                            ? "点左边的成员查看画像。"
                            : profileNote || "加载中…"}
                        </p>
                      )}
                    </div>
                  </Card>
                </div>

                <Card className="p-5">
                  <h2 className="text-[13px] font-medium text-ink">
                    课堂全量对话
                    <span className="ml-2 font-normal text-hint">
                      {messages.length} 条 · 群聊视图，不按学生隔离
                    </span>
                  </h2>
                  <div className="mt-4 max-h-[520px] space-y-4 overflow-y-auto pr-1">
                    {messages.length === 0 ? (
                      <p className="text-[13px] text-hint">
                        这个课堂还没有对话。
                      </p>
                    ) : (
                      messages.map((msg) => (
                        <MessageRow key={msg.key} msg={msg} />
                      ))
                    )}
                  </div>
                </Card>
              </>
            )}
          </div>
        )}
      </div>
    </Shell>
  );
}

// --- 大纲渲染 ------------------------------------------------------------

function OutlineCard({ result }: { result: PptOutlineResult }) {
  const { outline, download_url, file_name, retrieved } = result;

  return (
    <Card className="p-5">
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <h2 className="text-[16px] font-medium leading-snug text-ink">
            {outline.title || result.topic}
          </h2>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <Tag tone={retrieved ? "ai" : "warn"}>
              {retrieved
                ? "已检索到知识库资料"
                : "知识库没命中，模型自有知识生成"}
            </Tag>
            {result.audience && <Tag tone="neutral">{result.audience}</Tag>}
            <span className="text-[12px] text-hint">主题：{result.topic}</span>
          </div>
        </div>
        {download_url ? (
          <a
            href={resourceUrl(download_url)}
            className="inline-flex shrink-0 items-center gap-1.5 rounded-lg bg-brand px-3.5 py-2 text-[14px] font-medium text-white transition-colors hover:bg-brand-strong"
          >
            下载 PPT
          </a>
        ) : (
          <Tag tone="warn">PPT 渲染失败，大纲可用</Tag>
        )}
      </div>

      <div className="mt-5 space-y-5">
        <Section title="教学目标" items={outline.objectives} />
        <Section title="重难点" items={outline.key_points} />

        {(outline.sections?.length ?? 0) > 0 && (
          <div>
            <p className="text-[13px] font-medium text-sub">
              章节（{outline.sections.length}）
            </p>
            <div className="mt-2 space-y-2.5">
              {outline.sections.map((section, index) => (
                <div
                  key={`${section.title}-${index}`}
                  className="rounded-lg border border-line bg-soft px-3.5 py-3"
                >
                  <p className="text-[13px] font-medium text-ink">
                    {index + 1}. {section.title}
                  </p>
                  <ul className="mt-1.5 space-y-1">
                    {section.points?.map((point, i) => (
                      <li
                        key={i}
                        className="flex gap-2 text-[13px] leading-relaxed text-sub"
                      >
                        <span className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-neutral-dot" />
                        {point}
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          </div>
        )}

        <Section title="示例" items={outline.examples} />
        <Section title="课堂提问" items={outline.questions} />
        <Section title="小结" items={outline.summary} />
      </div>

      {file_name && (
        <p className="mt-5 border-t border-line pt-3 text-[12px] text-hint">
          文件：{file_name}（点击「下载 PPT」下载；桌面客户端会自动存到「下载」文件夹）
        </p>
      )}
    </Card>
  );
}

function Section({ title, items }: { title: string; items?: string[] }) {
  if (!items?.length) return null;
  return (
    <div>
      <p className="text-[13px] font-medium text-sub">{title}</p>
      <ul className="mt-1.5 space-y-1">
        {items.map((item, index) => (
          <li
            key={index}
            className="flex gap-2 text-[13px] leading-relaxed text-ink"
          >
            <span className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-brand" />
            {item}
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * 课堂资料卡片（XB-24）。
 *
 * 这块 UI 不是锦上添花：2026-10-26 实测发现，后端 `/upload/teacher` 做完之后
 * 教师端**一个入口都没有**——接口对真实教师是不可达的，功能等于不存在。
 *
 * 除上传外必须带清单和删除：教师传错一份讲义时，没有删除入口就等于
 * 永久污染这个课堂的检索结果（学生每问一句都可能被塞进错资料）。
 */
function MaterialsCard({
  classroomId,
  documents,
  loading,
  uploading,
  error,
  notice,
  onUpload,
  onDelete,
}: {
  classroomId: number | null;
  documents: ClassroomDocument[];
  loading: boolean;
  uploading: boolean;
  error: string;
  notice: string;
  onUpload: (e: React.ChangeEvent<HTMLInputElement>) => void;
  onDelete: (doc: ClassroomDocument) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);

  return (
    <Card className="p-5">
      <div className="flex items-baseline justify-between gap-2">
        <h2 className="text-[14px] font-medium text-ink">课堂资料</h2>
        {loading && <Spinner className="text-sub" />}
      </div>
      <p className="mt-1 text-[12px] leading-relaxed text-hint">
        上传的 PDF 会成为本课堂的长期资料，学生提问时能被检索到。
        单个文件 ≤ 10MB；抽不出文字的扫描件会被拒绝。
      </p>

      <div className="mt-3.5 space-y-3">
        <Btn
          variant="primary"
          className="w-full py-2"
          disabled={uploading || !classroomId}
          onClick={() => inputRef.current?.click()}
        >
          {uploading && <Spinner />}
          {uploading ? "正在解析并建索引…" : "选择 PDF 上传"}
        </Btn>
        <input
          ref={inputRef}
          type="file"
          accept="application/pdf,.pdf"
          className="hidden"
          onChange={onUpload}
        />

        {!classroomId && (
          <Banner tone="warn">
            还没选课堂。上传要校验「你是这个课堂的教师」，请先在「课堂」页签里选一个课堂。
          </Banner>
        )}
        {error && <Banner tone="error">{error}</Banner>}
        {notice && <Banner tone="ok">{notice}</Banner>}

        <div>
          <div className="flex items-baseline justify-between">
            <p className="text-[12px] text-sub">
              已上传 {documents.length} 份
            </p>
            {documents.length > 0 && (
              <p className="text-[12px] text-hint">上限 20 份 / 200MB</p>
            )}
          </div>

          {documents.length === 0 ? (
            <p className="mt-2 text-[12px] text-hint">
              {loading ? "读取中…" : "还没有资料。上传后学生就能在问答里查到。"}
            </p>
          ) : (
            <ul className="mt-2 divide-y divide-line">
              {documents.map((doc) => (
                <li key={doc.doc_id} className="flex items-start gap-2 py-2">
                  <div className="min-w-0 flex-1">
                    <p
                      className="truncate text-[13px] text-ink"
                      title={doc.filename}
                    >
                      {doc.filename}
                    </p>
                    <p className="mt-0.5 text-[12px] text-hint">
                      {doc.chunk_count} 片 · {formatBytes(doc.file_bytes)}
                      {doc.created_at ? ` · ${formatDay(doc.created_at)}` : ""}
                    </p>
                  </div>
                  <Btn
                    variant="ghost"
                    className="px-2 py-1 text-[12px]"
                    onClick={() => onDelete(doc)}
                  >
                    删除
                  </Btn>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </Card>
  );
}

function formatBytes(n: number): string {
  if (!n) return "0 B";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

/** 后端给的是 ISO 字符串；无效值原样返回，不要显示成 "Invalid Date"。 */
function formatDay(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(
    d.getMinutes(),
  ).padStart(2, "0")}`;
}
