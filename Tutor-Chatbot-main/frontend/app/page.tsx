"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import {
  addMember,
  ApiError,
  getClassroom,
  getHealth,
  login,
  register,
} from "@/lib/api";
import {
  DEFAULT_BACKEND_URL,
  getBackendUrl,
  loadIdentity,
  rememberClassroom,
  saveBackendUrl,
  saveIdentity,
  type Identity,
} from "@/lib/identity";
import { Banner, Btn, Card, Field, Input, Spinner } from "@/components/ui";
import { PageBackdrop } from "@/components/PageBackdrop";
import type { Health, User, UserRole } from "@/lib/types";

/**
 * 页面 1／4：入口（注册 / 登录）。
 *
 * 2026-11-02 起后端有了口令，这里相应分成两条路：
 *   注册：用户名 + 密码 + 身份 → 后端分配 id，界面把 id 回显给用户
 *   登录：用户名 + 密码 → 拿回 id / 昵称 / 角色，恢复身份
 *
 * 两种方式最终都落到同一个「进入」流程：教师去工作台，学生用课堂号
 * 进教室。进教室那一步可能被 1v1 指定学生规则挡回来，那时把后端的
 * 原话（「这是指定学生的[1v1教室]，你不能进入」）直接展示出来——
 * 换成「进入失败」会让学生以为是网络问题，反复重试。
 */
export default function EntryPage() {
  const router = useRouter();

  // 登录 / 注册。默认登录：老用户每次进来都点「注册」是很烦的事。
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<UserRole>("student");
  // 注册成功后先停一下，把系统分配的 id 显示出来（下一次登录/被指定靠它）。
  const [created, setCreated] = useState<User | null>(null);
  const [classroomId, setClassroomId] = useState("1");
  const [backend, setBackend] = useState(DEFAULT_BACKEND_URL);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [previous, setPrevious] = useState<Identity | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const saved = loadIdentity();
    setPrevious(saved);
    setBackend(getBackendUrl());
    if (saved) {
      setUsername(saved.username);
      setRole(saved.role);
      if (saved.classroomId) setClassroomId(String(saved.classroomId));
    }
    setReady(true);
  }, []);

  useEffect(() => {
    if (!ready) return;
    let alive = true;
    setHealth(null);
    setHealthError("");
    getHealth()
      .then((h) => alive && setHealth(h))
      .catch((err) =>
        alive &&
        setHealthError(err instanceof Error ? err.message : "后端没有响应"),
      );
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, showAdvanced, backend]);

  function applyBackend(next: string) {
    setBackend(next);
    saveBackendUrl(next);
    setShowAdvanced(false);
  }

  /** 点「登录 / 注册」：先过口令这一关，再交给 enter()。 */
  async function submitAuth() {
    const name = username.trim();
    if (!name) {
      setError(
        mode === "register"
          ? "先给自己取个用户名 —— 老师和同学要靠它认出你。"
          : "填一下用户名。",
      );
      return;
    }
    if (!password) {
      setError(mode === "register" ? "设置一个密码（至少 6 位）。" : "填一下密码。");
      return;
    }
    setBusy(true);
    setError("");
    try {
      if (mode === "register") {
        const user = await register(name, password, role);
        // 不立刻跳转：先把系统分配的 id 回显出来，用户抄下来（老师指定 1v1
        // 教室、以后换设备登录都靠它）。点「继续」再走下面的进入流程。
        setCreated(user);
        setBusy(false);
        return;
      }
      const user = await login(name, password);
      await enter(user);
    } catch (err) {
      setError(authErrorMessage(err, mode));
    } finally {
      setBusy(false);
    }
  }

  /** 身份到手后的统一入口：教师去工作台，学生进课堂。 */
  async function enter(user: User) {
    const base: Identity = {
      userId: user.id,
      username: user.username,
      role: user.role,
      classroomId: null,
    };

    if (user.role === "teacher") {
      saveIdentity(base);
      router.push("/teacher/");
      return;
    }

    const cid = Number(classroomId);
    if (!Number.isInteger(cid) || cid <= 0) {
      setError("课堂号要填正整数（默认课堂是 1）。");
      return;
    }
    await addMember(cid, user.id, "student");
    const room = await getClassroom(cid).catch(() => null);
    saveIdentity({
      ...base,
      classroomId: cid,
      classroomName: room?.name,
    });
    rememberClassroom({
      id: cid,
      name: room?.name ?? `课堂 ${cid}`,
      role: "student",
    });
    router.push("/classroom/");
  }

  /** 把后端/网络异常翻成一句学生看得懂的话。 */
  function authErrorMessage(err: unknown, current: "login" | "register"): string {
    if (err instanceof ApiError) {
      if (err.status === 404) {
        return `课堂 ${classroomId} 还不存在。课堂要由教师先在「教师工作台」里创建，你再拿着课堂号进来。`;
      }
      if (err.status === 503) {
        return "后端数据库没有接通，暂时进不去。请联系管理员。";
      }
      // 401 / 409 / 422 的 detail 本身就是中文原话，直接用：
      // 密码错、用户名被占用、1v1 教室不是给你的——这几种必须说清原因。
      if ([401, 409, 422].includes(err.status ?? 0)) return err.message;
    }
    if (current === "register") return "注册失败，请重试。";
    return err instanceof Error ? err.message : "进入失败，请重试";
  }

  return (
    <div className="relative h-full overflow-hidden">
      <PageBackdrop variant="home" />
      <div className="relative z-10 flex h-full items-start justify-center overflow-auto px-6 py-10">
        <div className="grid w-full max-w-[1000px] gap-6 rounded-2xl border border-white/60 bg-panel/52 p-6 shadow-[0_24px_60px_-28px_rgba(15,23,42,0.38)] backdrop-blur-2xl sm:p-8 md:grid-cols-[1.02fr_1fr] md:items-start">
        <section className="pt-2">
          <h1 className="text-[26px] font-medium leading-snug text-ink">
            AI 智能教室
          </h1>
          <p className="mt-2 text-[14px] leading-relaxed text-sub">
            面向计算机八股（操作系统 / 计算机网络）的 AI 教学系统。
            一个教室，一个学科，学生在群里提问，AI 按本人画像给出个性化讲解；
            教师输入主题，AI 生成备课大纲与 PPT。
          </p>

          <ul className="mt-6 space-y-3">
            {[
              ["个性化讲解", "同一个问题，基础弱和基础好的学生拿到的讲法不同"],
              ["群聊式课堂", "同一教室里所有人看得见彼此的问答，可以接力追问"],
              ["资料可追溯", "每轮回答都会标出引用了哪几篇知识库资料"],
            ].map(([head, body]) => (
              <li key={head} className="flex gap-2.5">
                <span className="mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full bg-brand" />
                <span className="text-[13px] leading-relaxed">
                  <span className="font-medium text-ink">{head}</span>
                  <span className="text-sub"> —— {body}</span>
                </span>
              </li>
            ))}
          </ul>

          {previous && (
            <Card className="mt-6 p-3.5">
              <p className="text-[13px] text-sub">
                上次以{" "}
                <span className="font-medium text-ink">
                  {previous.username}
                </span>
                （{previous.role === "teacher" ? "教师" : "学生"}）进入
                {previous.classroomId ? `，课堂 ${previous.classroomId}` : ""}。
              </p>
              <div className="mt-2.5 flex gap-2">
                <Btn
                  variant="subtle"
                  onClick={() =>
                    router.push(
                      previous.role === "teacher" ? "/teacher/" : "/classroom/",
                    )
                  }
                >
                  直接继续
                </Btn>
                <Btn variant="ghost" onClick={() => setPrevious(null)}>
                  换一个身份
                </Btn>
              </div>
            </Card>
          )}
        </section>

        <Card className="p-6">
          <h2 className="text-[16px] font-medium text-ink">
            {created ? "账号已创建" : mode === "register" ? "注册新账号" : "登录"}
          </h2>
          <p className="mt-1 text-[13px] text-sub">
            {created
              ? "系统已分配账号 ID，请记下来 —— 老师指定 1v1 教室、换设备登录都要用它。"
              : mode === "register"
                ? "设置用户名和密码，账号 ID 由系统自动分配。"
                : "用注册时的用户名和密码登录。"}
          </p>

          {/* 注册成功：先把 id 亮出来再放行。这一步不能省——用户看不到 id，
              下次登录只记得昵称，老师那边也无法按 id 指定学生。 */}
          {created ? (
            <div className="mt-5 space-y-4">
              <div className="rounded-xl border border-brand-line bg-brand-soft px-4 py-4">
                <p className="text-[12px] text-brand">你的账号 ID</p>
                <p className="mt-1 font-mono text-[28px] font-medium leading-none text-ink">
                  {created.id}
                </p>
                <p className="mt-2 text-[12px] text-sub">
                  用户名 {created.username} ·{" "}
                  {created.role === "teacher" ? "教师" : "学生"}
                </p>
              </div>
              {created.role === "student" && (
                <Field label="课堂号" hint="教师创建课堂时会告诉你">
                  <Input
                    value={classroomId}
                    onChange={(e) => setClassroomId(e.target.value)}
                    inputMode="numeric"
                    placeholder="1"
                  />
                </Field>
              )}
              {error && <Banner tone="error">{error}</Banner>}
              <Btn
                variant="primary"
                className="w-full py-2.5"
                disabled={busy}
                onClick={() => {
                  setBusy(true);
                  setError("");
                  void enter(created)
                    .catch((err) => setError(authErrorMessage(err, "register")))
                    .finally(() => setBusy(false));
                }}
              >
                {busy && <Spinner />}
                {created.role === "teacher" ? "进入教师工作台" : "进入教室"}
              </Btn>
              <Btn
                variant="ghost"
                className="w-full"
                disabled={busy}
                onClick={() => {
                  setCreated(null);
                  setPassword("");
                  setMode("login");
                }}
              >
                改用这个账号登录
              </Btn>
            </div>
          ) : (
            <div className="mt-5 space-y-4">
              {/* 登录 / 注册切换。放在最上面而不是藏进链接里：
                  新用户第一眼要能看出「这里能注册」。 */}
              <div className="flex gap-1 rounded-lg border border-line bg-soft p-1">
                {(
                  [
                    ["login", "登录"],
                    ["register", "注册"],
                  ] as const
                ).map(([value, label]) => (
                  <button
                    key={value}
                    type="button"
                    onClick={() => {
                      setMode(value);
                      setError("");
                    }}
                    className={`flex-1 rounded-md px-3 py-2 text-[13px] transition-colors ${
                      mode === value
                        ? "bg-panel text-ink shadow-[0_1px_2px_rgba(15,23,42,0.08)]"
                        : "text-sub hover:text-ink"
                    }`}
                  >
                    {label}
                  </button>
                ))}
              </div>

              <Field label="用户名" hint={mode === "register" ? "注册后不能改名" : undefined}>
                <Input
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder="例如：张三"
                  maxLength={64}
                  autoFocus
                />
              </Field>

              <Field
                label="密码"
                hint={mode === "register" ? "至少 6 位" : undefined}
              >
                <Input
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  type="password"
                  placeholder={mode === "register" ? "设置一个密码" : "输入密码"}
                  maxLength={64}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !busy) void submitAuth();
                  }}
                />
              </Field>

              {/* 身份只在注册时选：登录时身份从账号里带出来，
                  让人再选一次等于给了「学生登进教师账号」的口子。 */}
              {mode === "register" && (
                <Field label="身份">
                  <div className="grid grid-cols-2 gap-2">
                    {(
                      [
                        ["student", "学生", "提问 · 看画像"],
                        ["teacher", "教师", "建课堂 · 备课"],
                      ] as const
                    ).map(([value, label, desc]) => (
                      <button
                        key={value}
                        type="button"
                        onClick={() => setRole(value)}
                        className={`rounded-lg border px-3 py-2.5 text-left transition-colors ${
                          role === value
                            ? "border-brand bg-brand-soft"
                            : "border-line bg-panel hover:bg-soft"
                        }`}
                      >
                        <span
                          className={`block text-[14px] font-medium ${role === value ? "text-brand" : "text-ink"}`}
                        >
                          {label}
                        </span>
                        <span className="mt-0.5 block text-[12px] text-hint">
                          {desc}
                        </span>
                      </button>
                    ))}
                  </div>
                </Field>
              )}

              {role === "student" && (
                <Field
                  label={mode === "register" ? "课堂号（可稍后再进）" : "课堂号"}
                  hint="教师创建课堂时会拿到"
                >
                  <Input
                    value={classroomId}
                    onChange={(e) => setClassroomId(e.target.value)}
                    inputMode="numeric"
                    placeholder="1"
                  />
                </Field>
              )}
            </div>
            )}

            {/* 后端连接状态两支共用：注册页也要能看出「后端没起来」。 */}
            <div className="mt-4 rounded-lg border border-line bg-soft px-3 py-2.5">
              <div className="flex items-center gap-2">
                <span
                  className={`h-1.5 w-1.5 rounded-full ${
                    healthError
                      ? "bg-error"
                      : health
                        ? "bg-ok"
                        : "bg-idle"
                  }`}
                />
                <span
                  className="text-[13px] text-sub"
                  title={
                    health
                      ? `模型 ${health.model} · 数据库 ${health.database}`
                      : undefined
                  }
                >
                  {healthError
                    ? "后端未启动"
                    : health
                      ? "后端正常"
                      : "正在检查后端…"}
                </span>
                <button
                  type="button"
                  onClick={() => setShowAdvanced((v) => !v)}
                  className="ml-auto text-[12px] text-hint underline-offset-2 hover:text-brand hover:underline"
                >
                  {showAdvanced ? "收起" : "后端地址"}
                </button>
              </div>
              {/* 数据库状态只在「不正常」时才值得占一行：正常是默认状态，
                  把它当常驻信息展示等于把调试面板放进了首屏。 */}
              {health && health.database !== "up" && (
                <p className="mt-1 text-[12px] text-hint">
                  数据库：{health.database === "disabled"
                    ? "未配置，聊天记录不会保存"
                    : health.database === "down"
                      ? "连不上，聊天记录不会保存"
                      : health.database}
                </p>
              )}

              {showAdvanced && (
                <div className="mt-3">
                  <Input
                    value={backend}
                    onChange={(e) => setBackend(e.target.value)}
                    placeholder={DEFAULT_BACKEND_URL}
                  />
                  <div className="mt-2 flex gap-2">
                    <Btn
                      variant="subtle"
                      onClick={() => applyBackend(backend)}
                      disabled={busy}
                    >
                      保存并重测
                    </Btn>
                    <Btn
                      variant="ghost"
                      onClick={() => applyBackend(DEFAULT_BACKEND_URL)}
                    >
                      恢复默认
                    </Btn>
                  </div>
                </div>
              )}
            </div>

            {!created && error && <Banner tone="error" className="mt-4">{error}</Banner>}

            {!created && (
              <>
                <Btn
                  variant="primary"
                  className="mt-4 w-full py-2.5"
                  onClick={() => void submitAuth()}
                  disabled={busy}
                >
                  {busy && <Spinner />}
                  {busy
                    ? mode === "register"
                      ? "正在注册…"
                      : "正在登录…"
                    : mode === "register"
                      ? "注册"
                      : "登录"}
                </Btn>

                <p className="mt-3 text-center text-[12px] text-hint">
                  学生也可以
                  <Link
                    href="/classrooms/"
                    className="mx-1 text-brand underline-offset-2 hover:underline"
                  >
                    浏览我的课堂
                  </Link>
                </p>
              </>
            )}
        </Card>
        </div>
      </div>
    </div>
  );
}
