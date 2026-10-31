"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ApiError,
  getClassroom,
  getProfile,
  listMembers,
  listMessages,
  listScenarios,
} from "@/lib/api";
import { normalizeSources, streamChat } from "@/lib/chat";
import { loadIdentity, type Identity } from "@/lib/identity";
import type { Member, SourceItem, StreamState, StudentProfile } from "@/lib/types";
import { Avatar, MessageRow, type UIMsg } from "@/components/chat";
import { ProfileView } from "@/components/ProfileView";
import { Shell } from "@/components/Shell";
import { Banner, Btn, Empty, Spinner, Tag, Textarea } from "@/components/ui";

const QUICK_ASKS = [
  "死锁产生的四个必要条件是什么？",
  "TCP 为什么是三次握手，不是两次？",
  "进程和线程有什么区别？",
  "虚拟内存和页面置换算法讲讲",
  "TCP 和 UDP 的区别",
];

const POLL_MS = 5000;

/**
 * 距底部多少像素以内算「用户还贴着底部、希望看到最新消息」。
 *
 * 取 80px 而不是 0：留一点容差，避免因为气泡行高、图片未加载完等
 * 微小间距算出「不在一屏底部」，导致新消息来时该跟随却没跟随。
 */
const SCROLL_BOTTOM_THRESHOLD = 80;

/**
 * 页面 3／4：聊天室（学生主界面，教师也能进来）。
 *
 * 四个设计决定值得说明：
 *  - **群聊样式**：所有人左对齐、带昵称头像，不做「自己的消息靠右」。
 *    产品决策是「一个课堂 = 一个群」，B 能看到 A 的问答并接力追问，
 *    靠右对齐会让人误以为是私聊。
 *  - **5 秒轮询**拉消息，实现「同学发言能看见」。契约里没有 WebSocket 端点。
 *  - **请求体只传三个字段**（message / classroom_id / user_id）：
 *    后端 extra="forbid" 多传一个就 422；走库时服务端自己恢复历史，
 *    所以不用也不该在前端维护 topic / hint_level 的本地状态。
 *    场景同理 —— 建房时已经定了，这里不传 `scenario`。
 *  - **一轮可能多个气泡**（M4）：`group` 是「助教主讲 + 学伴补充」，
 *    `debate` 再加「考官追问」。SSE 的 `agent` 事件是切换信号，
 *    收到就新开一个气泡，之后的 token 归到该气泡。
 */
export default function ClassroomPage() {
  const router = useRouter();

  const [identity, setIdentity] = useState<Identity | null>(null);
  const [ready, setReady] = useState(false);
  const [messages, setMessages] = useState<UIMsg[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [notice, setNotice] = useState("");
  const [state, setState] = useState<StreamState | null>(null);

  const [members, setMembers] = useState<Member[]>([]);
  const [selectedUserId, setSelectedUserId] = useState<number | null>(null);
  const [profile, setProfile] = useState<StudentProfile | null>(null);
  const [profileMissing, setProfileMissing] = useState(false);
  const [panelOpen, setPanelOpen] = useState(true);

  // M4：建房即定场景，这里只负责把 key 显示成人话
  const [scenarioLabels, setScenarioLabels] = useState<Record<string, string>>(
    {},
  );
  const [scenarioKey, setScenarioKey] = useState<string | null>(null);

  const scrollRef = useRef<HTMLDivElement>(null);
  /**
   * 滚动位置是否跟随最新消息。
   *
   * 默认 true —— 首次进入课堂、切换课堂时应当停在最新一条。
   *
   * 用户一旦往上翻看历史就必须置 false：本页每 POLL_MS(5s) 轮询一次
   * `refreshMessages`，每次都会 `setMessages(新数组)`，从而触发下面的滚动
   * effect。若该 effect 无条件滚到底，用户翻上去后最多 5 秒就被拽回底部，
   * 历史消息根本读不了。
   */
  const stickToBottomRef = useRef(true);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const streamingRef = useRef(false);
  const localSeq = useRef(0);
  /** 当前正在接收 token 的那个气泡 */
  const currentAiKeyRef = useRef<string | null>(null);
  /** 本轮已经开过的所有气泡，收流时统一收尾 */
  const turnKeysRef = useRef<string[]>([]);

  const classroomId = identity?.classroomId ?? null;

  useEffect(() => {
    const saved = loadIdentity();
    setIdentity(saved);
    if (saved) {
      setSelectedUserId(saved.userId);
      if (!saved.classroomId) {
        setNotice("还没有选课堂。请先从「我的课堂」进入。");
      }
    } else {
      setNotice("还没有身份。请先回到入口页填昵称和身份。");
    }
    setReady(true);
  }, []);

  useEffect(() => {
    streamingRef.current = streaming;
  }, [streaming]);

  useEffect(() => {
    // 只有用户本来就贴着底部时才跟随新内容。
    // 用户上翻后 stickToBottomRef=false，这里直接 return —— 滚动位置完全由用户控制。
    // 注意 effect 依赖仍是 messages：轮询重建数组会频繁触发本 effect，
    // 所以判断必须在最前面，否则会白白执行一次 scrollTo。
    if (!stickToBottomRef.current) return;
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [messages]);

  /**
   * 记录用户当前的滚动位置。上面 effect 里的程序化滚动同样会触发本回调，
   * 算出来的结论与实际位置一致，不会互相打架。
   */
  const handleScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    stickToBottomRef.current = distance <= SCROLL_BOTTOM_THRESHOLD;
  }, []);

  // --- 数据拉取 ----------------------------------------------------------

  const refreshMessages = useCallback(async () => {
    if (!classroomId) return;
    try {
      const data = await listMessages(classroomId, 200);
      if (streamingRef.current) return; // 流式进行中不覆盖本地气泡
      setMessages(
        data.messages.map((m) => ({
          key: `db-${m.id}`,
          role: m.role,
          username: m.username,
          content: m.content,
          topic: m.topic,
          createdAt: m.created_at,
          // M4：按角色分气泡，不要按 role=assistant 合并
          agentRole: m.agent_role ?? null,
          // sources 自 2026-10-21 起随消息落库，刷新后直接从这一列恢复"依据"。
          // 三种语义要保留：非空 = 有资料；[] = 本轮明确没用资料；
          // null/undefined = 老数据没覆盖（此时不显示"依据"行）。
          // 注意后端这里统一返回 `sources or []`，所以 NULL 目前会被压成 []，
          // 刷新后一律显示"未引用资料" —— 语义够用，但区分度不如流式那一刻。
          sources: m.sources == null ? undefined : normalizeSources(m.sources),
        })),
      );
      setNotice("");
    } catch (err) {
      // 403 表示当前用户不是该课堂成员，交给下面的提示处理
      if (err instanceof ApiError && err.status === 403) return;
      setNotice(err instanceof Error ? err.message : "读取聊天记录失败");
    }
  }, [classroomId]);

  const refreshMembers = useCallback(async () => {
    if (!classroomId) return;
    try {
      const data = await listMembers(classroomId);
      setMembers(data.members);
    } catch {
      // 成员列表拿不到不影响聊天
    }
  }, [classroomId]);

  const refreshProfile = useCallback(async () => {
    if (!classroomId || selectedUserId === null) return;
    try {
      const data = await getProfile(selectedUserId, classroomId);
      setProfile(data);
      setProfileMissing(false);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        setProfile(null);
        setProfileMissing(true);
      }
    }
  }, [classroomId, selectedUserId]);

  useEffect(() => {
    if (!ready || !classroomId) return;
    void refreshMessages();
    void refreshMembers();
  }, [ready, classroomId, refreshMessages, refreshMembers]);

  useEffect(() => {
    if (!ready || !classroomId) return;
    void refreshProfile();
  }, [ready, classroomId, refreshProfile]);

  useEffect(() => {
    if (!ready || !classroomId) return;
    const timer = setInterval(() => {
      if (!streamingRef.current) void refreshMessages();
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [ready, classroomId, refreshMessages]);

  // M4：场景名从后端拿，前端不维护第二份映射
  useEffect(() => {
    if (!ready) return;
    let alive = true;
    void listScenarios()
      .then((data) => {
        if (!alive) return;
        const map: Record<string, string> = {};
        for (const item of data.items ?? []) map[item.key] = item.label;
        setScenarioLabels(map);
      })
      .catch(() => {
        // 拿不到就退回显示 key，不影响聊天
      });
    return () => {
      alive = false;
    };
  }, [ready]);

  useEffect(() => {
    if (!ready || !classroomId) return;
    let alive = true;
    void getClassroom(classroomId)
      .then((room) => {
        if (alive) setScenarioKey(room.scenario ?? "v1");
      })
      .catch(() => {
        // 详情拿不到就不显示场景，不影响聊天
      });
    return () => {
      alive = false;
    };
  }, [ready, classroomId]);

  // --- 发送 --------------------------------------------------------------

  async function send(text: string) {
    const message = text.trim();
    if (!message || streaming || !identity || !classroomId) return;

    localSeq.current += 1;
    const seq = localSeq.current;
    const aiKey = `local-a-${seq}`;

    // 本轮气泡从占位那一个开始；后续每个 agent 事件再开一个新的
    turnKeysRef.current = [aiKey];
    currentAiKeyRef.current = aiKey;

    // 自己发的消息一定要看到结果：恢复跟随。这是唯一允许「接管」滚动位置的
    // 场景 —— 由用户主动发送触发，不会让用户在看历史时被莫名拽走。
    stickToBottomRef.current = true;

    setMessages((prev) => [
      ...prev,
      {
        key: `local-u-${seq}`,
        role: identity.role,
        username: identity.username,
        content: message,
        createdAt: new Date().toISOString(),
      },
      {
        key: aiKey,
        role: "assistant",
        username: null,
        content: "",
        streaming: true,
      },
    ]);
    setInput("");
    // 注意：这里**不清空 state**。契约 XB-8：降级回答（纯寒暄 / 未识别知识点）
    // 只发 token + [DONE]，不发 state。前端收到才更新、收不到就保持上一轮，
    // 清空会让「正在讲：死锁」的标记莫名消失。
    setStreaming(true);
    streamingRef.current = true;
    abortRef.current = new AbortController();

    let firstAgentSeen = false;

    const patchAi = (key: string | null, updater: (msg: UIMsg) => UIMsg) => {
      if (!key) return;
      setMessages((prev) =>
        prev.map((msg) => (msg.key === key ? updater(msg) : msg)),
      );
    };

    await streamChat(
      {
        message,
        classroom_id: classroomId,
        user_id: identity.userId,
      },
      {
        // sources 是第一个事件：先把「这轮依据哪几片资料」挂到第一个气泡上
        onSources: (items: SourceItem[]) =>
          patchAi(turnKeysRef.current[0] ?? null, (msg) => ({
            ...msg,
            sources: items,
          })),
        // 角色切换信号：第一个复用占位气泡，之后的每个新开一个
        onAgent: (agent) => {
          const meta = {
            agentRole: agent.role,
            agentLabel: agent.label || undefined,
            agentStance: agent.stance || undefined,
            agentIndex: agent.index,
            agentTotal: agent.total,
          };
          if (!firstAgentSeen) {
            firstAgentSeen = true;
            patchAi(turnKeysRef.current[0] ?? null, (msg) => ({
              ...msg,
              ...meta,
              streaming: true,
            }));
            return;
          }
          const key = `${aiKey}-${agent.index}`;
          turnKeysRef.current.push(key);
          const prevKey = currentAiKeyRef.current;
          currentAiKeyRef.current = key;
          setMessages((prev) => {
            // 上一个角色的气泡收尾，再追加新角色的空气泡
            const next = prev.map((msg) =>
              msg.key === prevKey ? { ...msg, streaming: false } : msg,
            );
            next.push({
              key,
              role: "assistant",
              username: null,
              content: "",
              streaming: true,
              ...meta,
            });
            return next;
          });
        },
        onToken: (token) =>
          patchAi(currentAiKeyRef.current, (msg) => ({
            ...msg,
            content: msg.content + token,
          })),
        onState: (next) => {
          setState(next);
          patchAi(turnKeysRef.current[0] ?? null, (msg) => ({
            ...msg,
            topic: next.topic,
          }));
        },
        onError: (text) =>
          patchAi(currentAiKeyRef.current, (msg) => ({
            ...msg,
            content: text,
          })),
      },
      abortRef.current.signal,
    );

    streamingRef.current = false;
    setStreaming(false);
    // 本轮开过几个气泡就收几个尾（group 是 2 个、debate 是 3 个）
    const keys = turnKeysRef.current;
    setMessages((prev) =>
      prev.map((msg) =>
        keys.includes(msg.key) ? { ...msg, streaming: false } : msg,
      ),
    );
    // 回答结束后拉一次权威数据：DB 里的 id / created_at 才是准的。
    // sources 与 agent_role 都已落库，所以这一步之后气泡会换成 DB 版本，
    // 刷新前后显示应保持一致。
    await refreshMessages();
    await refreshProfile();
    inputRef.current?.focus();
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

  const blocked = !identity || !classroomId;
  const iAmTeacher = identity?.role === "teacher";
  const scenarioLabel = scenarioKey
    ? (scenarioLabels[scenarioKey] ?? scenarioKey)
    : null;

  return (
    <Shell
      backHref="/classrooms/"
      backLabel="我的课堂"
      identity={identity}
      title={
        <span className="flex items-center gap-2">
          {identity?.classroomName ?? `课堂 ${classroomId ?? "—"}`}
          <span className="text-hint">#{classroomId ?? "—"}</span>
          {scenarioLabel && <Tag tone="neutral">场景：{scenarioLabel}</Tag>}
          {state?.topic && <Tag tone="brand">正在讲：{state.topic}</Tag>}
          {state?.personalized && <Tag tone="ai">已按你的画像讲解</Tag>}
          {state?.resolved && <Tag tone="ok">已掌握</Tag>}
        </span>
      }
      right={
        <div className="flex items-center gap-2">
          <Btn variant="ghost" onClick={() => void refreshMessages()}>
            刷新
          </Btn>
          <Btn
            variant="ghost"
            className="max-lg:hidden"
            onClick={() => setPanelOpen((v) => !v)}
          >
            {panelOpen ? "收起侧栏" : "课堂侧栏"}
          </Btn>
          {iAmTeacher && (
            <Btn variant="subtle" onClick={() => router.push("/teacher/")}>
              教师工作台
            </Btn>
          )}
        </div>
      }
    >
      <div className="flex h-full">
        <section className="flex min-w-0 flex-1 flex-col">
          {notice && (
            <Banner tone="warn" className="m-4 mb-0">
              {notice}
            </Banner>
          )}

          <div
            ref={scrollRef}
            onScroll={handleScroll}
            className="min-h-0 flex-1 overflow-y-auto px-3 py-4 sm:px-6 sm:py-5"
          >
            {messages.length === 0 ? (
              <div className="flex h-full flex-col items-center justify-center gap-3">
                <p className="text-[14px] text-hint">
                  这个课堂还没有人提问。挑一个知识点开始吧：
                </p>
                <div className="flex max-w-[520px] flex-wrap justify-center gap-2">
                  {QUICK_ASKS.map((q) => (
                    <button
                      key={q}
                      disabled={blocked}
                      onClick={() => {
                        setInput(q);
                        inputRef.current?.focus();
                      }}
                      className="rounded-full border border-line bg-panel px-3 py-1.5 text-[13px] text-sub transition-colors hover:border-brand hover:text-brand disabled:opacity-50"
                    >
                      {q}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <div className="mx-auto max-w-[820px] space-y-4">
                {messages.map((msg) => (
                  <MessageRow
                    key={msg.key}
                    msg={msg}
                    isMine={
                      msg.role !== "assistant" &&
                      msg.username === identity?.username
                    }
                  />
                ))}
              </div>
            )}
          </div>

          <div className="shrink-0 border-t border-line bg-panel px-3 py-3 sm:px-6 sm:py-3.5">
            {/* sm 以上把输入行右移 42px（头像 32 + 间距 10），
                让输入框左边界与气泡正文的左边界落在同一根竖线上。
                之前两者差 42px，是整页最显眼的一处不齐。 */}
            <div className="mx-auto flex max-w-[820px] items-end gap-2.5 sm:pl-[42px]">
              <Textarea
                ref={inputRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    void send(input);
                  }
                }}
                rows={3}
                placeholder={
                  blocked
                    ? "还没有进入课堂"
                    : "问一个计算机八股问题…（Enter 发送，Shift+Enter 换行）"
                }
                disabled={blocked}
                className="max-h-[160px] min-h-[68px]"
              />
              {streaming ? (
                <Btn
                  variant="ghost"
                  className="py-2.5"
                  onClick={() => abortRef.current?.abort()}
                >
                  停止
                </Btn>
              ) : (
                <Btn
                  variant="primary"
                  className="py-2.5"
                  disabled={blocked || !input.trim()}
                  onClick={() => void send(input)}
                >
                  发送
                </Btn>
              )}
            </div>
            <p className="mx-auto mt-1.5 max-w-[820px] text-[12px] text-hint sm:pl-[42px]">
              同一课堂共享上下文 —— 同学的提问你能看见，也能接着追问。
            </p>
          </div>
        </section>

        {panelOpen && (
          <aside
            className="hidden w-[304px] shrink-0 flex-col overflow-y-auto border-l border-line bg-panel lg:flex"
            data-selectable
          >
            <div className="border-b border-line px-4 py-3">
              <h2 className="text-[13px] font-medium text-ink">本课堂成员</h2>
            </div>

            <div className="space-y-1 p-3">
              {members.length === 0 ? (
                <p className="px-1 py-2 text-[13px] text-hint">加载中…</p>
              ) : (
                members.map((m) => (
                  <button
                    key={m.user_id}
                    onClick={() => setSelectedUserId(m.user_id)}
                    className={`flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left transition-colors ${
                      selectedUserId === m.user_id
                        ? "bg-brand-soft"
                        : "hover:bg-soft"
                    }`}
                  >
                    <Avatar name={m.username} role={m.role} />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[13px] text-ink">
                        {m.username}
                        {m.user_id === identity?.userId && (
                          <span className="ml-1 text-[12px] text-hint">我</span>
                        )}
                      </span>
                      <span className="text-[12px] text-hint">
                        {m.role === "teacher" ? "教师" : "学生"}
                      </span>
                    </span>
                  </button>
                ))
              )}
            </div>

            <div className="mt-auto border-t border-line">
              <div className="flex items-center gap-2 px-4 py-3">
                <h2 className="text-[13px] font-medium text-ink">学习画像</h2>
                <button
                  onClick={() => void refreshProfile()}
                  className="ml-auto text-[12px] text-hint hover:text-brand"
                >
                  刷新
                </button>
              </div>
              <div className="px-4 pb-4">
                {profileMissing ? (
                  <p className="text-[13px] leading-relaxed text-hint">
                    还没有这个学生的画像。画像是每轮对话自动沉淀的 ——
                    聊几轮之后就会出现。
                  </p>
                ) : profile ? (
                  <ProfileView profile={profile} />
                ) : (
                  <p className="text-[13px] text-hint">加载中…</p>
                )}
              </div>
            </div>
          </aside>
        )}
      </div>
    </Shell>
  );
}
