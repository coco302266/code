"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import {
  addMember,
  ApiError,
  createUser,
  getClassroom,
  getHealth,
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
import type { Health, UserRole } from "@/lib/types";

/**
 * 页面 1／4：入口。
 *
 * 后端没有登录接口，所以这里做三件事：
 *   1. 幂等建用户（POST /users，重名直接返回已有记录）
 *   2. 学生顺手加入课堂（POST /members）—— 漏了这一步，权限判定会 403
 *   3. 把身份写进 localStorage，桌面客户端重启后还在
 */
export default function EntryPage() {
  const router = useRouter();

  const [username, setUsername] = useState("");
  const [role, setRole] = useState<UserRole>("student");
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

  async function enter() {
    const name = username.trim();
    if (!name) {
      setError("先给自己取个昵称 —— 课堂是群聊，同学和老师要靠它认出你。");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const user = await createUser(name, role);
      const base: Identity = {
        userId: user.id,
        username: user.username,
        role: user.role,
        classroomId: null,
      };

      if (role === "teacher") {
        saveIdentity(base);
        router.push("/teacher/");
        return;
      }

      const cid = Number(classroomId);
      if (!Number.isInteger(cid) || cid <= 0) {
        setError("课堂号要填正整数（默认课堂是 1）。");
        setBusy(false);
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
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        setError(
          `课堂 ${classroomId} 还不存在。课堂要由教师先在「教师工作台」里创建，你再拿着课堂号进来。`,
        );
      } else if (err instanceof ApiError && err.status === 503) {
        setError(
          "后端数据库没有接通，暂时进不去。请联系管理员。",
        );
      } else {
        setError(err instanceof Error ? err.message : "进入失败，请重试");
      }
    } finally {
      setBusy(false);
    }
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
          <h2 className="text-[16px] font-medium text-ink">进入教室</h2>
          <p className="mt-1 text-[13px] text-sub">
            不需要密码：这里选的身份，决定了你能用哪些功能。
          </p>

          <div className="mt-5 space-y-4">
            <Field label="昵称" hint="同名会复用已有账号">
              <Input
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="例如：张三"
                maxLength={64}
                autoFocus
              />
            </Field>

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

            {role === "student" && (
              <Field label="课堂号" hint="教师创建课堂时会拿到">
                <Input
                  value={classroomId}
                  onChange={(e) => setClassroomId(e.target.value)}
                  inputMode="numeric"
                  placeholder="1"
                />
              </Field>
            )}

            <div className="rounded-lg border border-line bg-soft px-3 py-2.5">
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

            {error && <Banner tone="error">{error}</Banner>}

            <Btn
              variant="primary"
              className="w-full py-2.5"
              onClick={enter}
              disabled={busy}
            >
              {busy && <Spinner />}
              {busy
                ? "正在进入…"
                : role === "teacher"
                  ? "进入教师工作台"
                  : "进入教室"}
            </Btn>

            <p className="text-center text-[12px] text-hint">
              学生也可以
              <Link
                href="/classrooms/"
                className="mx-1 text-brand underline-offset-2 hover:underline"
              >
                浏览我的课堂
              </Link>
            </p>
          </div>
        </Card>
        </div>
      </div>
    </div>
  );
}
