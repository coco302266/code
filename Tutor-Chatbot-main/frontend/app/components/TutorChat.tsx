"use client";

import {useState, useRef, useEffect} from "react";
import Link from "next/link";
import { useTutorConsent } from "@/lib/consent";
import { sanitizeAssistantText } from "@/lib/answerFormat";

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  includeInHistory?: boolean;
}

interface TutorState {
    topic: string;
    hint_level: number;
    misconceptions: string;
    resolved: boolean;
}

interface TutorChatProps {
  initialMessage?: string;
  provider?: "groq" | "gemini";
}

type Provider = NonNullable<TutorChatProps["provider"]>;

const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8000";

/**
 * EmailJS「发送报告」通道。
 *
 * 历史问题：这里原先 `import emailjs from "@emailjs/browser"`，但该包从未被
 * package.json 声明，导致 `tsc` / `next build` 报 TS2307；同时
 * NEXT_PUBLIC_EMAILJS_* 三件套也从未在 .env / env.example 里配置过，
 * 也就是说这个按钮即使装上依赖也发不出去 —— 一个编译不过、配了也不通的死代码。
 *
 * 改法：直接用 EmailJS 的公开 REST 端点（SDK 内部做的本来就是这件事），
 * 零依赖即可编译通过；配置缺失时明确提示「未配置」，而不是发一个注定失败的请求。
 */
const EMAILJS_ENDPOINT = "https://api.emailjs.com/api/v1.0/email/send";

interface EmailJsConfig {
  serviceId: string;
  templateId: string;
  publicKey: string;
}

/** 三项缺一不可，缺任一项返回 null，调用方据此提示「未配置」。 */
function getEmailJsConfig(): EmailJsConfig | null {
  const serviceId = process.env.NEXT_PUBLIC_EMAILJS_SERVICE_ID;
  const templateId = process.env.NEXT_PUBLIC_EMAILJS_TEMPLATE_ID;
  const publicKey = process.env.NEXT_PUBLIC_EMAILJS_PUBLIC_KEY;
  if (!serviceId || !templateId || !publicKey) return null;
  return { serviceId, templateId, publicKey };
}

async function sendViaEmailJs(
  cfg: EmailJsConfig,
  templateParams: Record<string, string>,
): Promise<void> {
  const res = await fetch(EMAILJS_ENDPOINT, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      service_id: cfg.serviceId,
      template_id: cfg.templateId,
      // EmailJS 把 public key 这个字段历史性地命名为 user_id
      user_id: cfg.publicKey,
      template_params: templateParams,
    }),
  });
  if (!res.ok) {
    throw new Error(`EmailJS ${res.status}: ${await res.text()}`);
  }
}

function newMessage(
  role: Message["role"],
  content: string,
  includeInHistory = true,
): Message {
  return {
    id: crypto.randomUUID(),
    role,
    content,
    includeInHistory,
  };
}

/**
 * 距底部多少像素以内算「用户还贴着底部」。
 * 留 80px 容差，避免因行高/未加载完的图片算出微小间距、导致该跟随时不跟随。
 */
const SCROLL_BOTTOM_THRESHOLD = 80;

export default function TutorChat({ initialMessage = "", provider = "groq" }: TutorChatProps) {
  const [messages, setMessages]   = useState<Message[]>([]);
  const [input, setInput]         = useState(initialMessage);
  const [streaming, setStreaming] = useState(false);
  const [activeProvider, setActiveProvider] = useState<Provider>(provider);
  const [state, setState]         = useState<TutorState>({
      topic: "",
      hint_level: 0,
      misconceptions: "",
      resolved: false,
  });
  const [sessionId, setSessionId]     = useState("");
  const [uploading, setUploading]     = useState(false);
  const [docUploaded, setDocUploaded] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const consent = useTutorConsent() === true;

  /** 消息滚动容器。用它 scrollTo 而不是 scrollIntoView：后者会连带滚动
   *  最近的可滚动祖先甚至 document 本身，在嵌入式布局里容易把整页顶跑。 */
  const scrollBoxRef = useRef<HTMLDivElement>(null);
  /**
   * 是否跟随最新消息。默认 true（首次进入停在最新一条）；
   * 用户上翻查看历史后置 false，此后新内容不再接管滚动位置。
   */
  const stickToBottomRef = useRef(true);
  const inputRef    = useRef<HTMLTextAreaElement>(null);
  const abortRef    = useRef<AbortController | null>(null);
  const uploadAbortRef = useRef<AbortController | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);



  useEffect(() => {
    // 仅当用户贴着底部时才跟随。上翻看历史后直接返回 —— 滚动位置由用户掌握。
    // 流式响应期间 messages 每个 token 都会变，本 effect 会被高频触发，
    // 所以判断必须在最前面。
    if (!stickToBottomRef.current) return;
    const el = scrollBoxRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [messages]);

  /** 记录用户滚动位置。程序化滚动同样触发，结论一致，不会互相打架。 */
  function handleScroll() {
    const el = scrollBoxRef.current;
    if (!el) return;
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    stickToBottomRef.current = distance <= SCROLL_BOTTOM_THRESHOLD;
  }

async function send() {
    const text = input.trim();
    if (!text || streaming || uploading) return;

    const userMessage = newMessage("user", text);
    const assistantMessage = newMessage("assistant", "");
    const assistantMessageId = assistantMessage.id;
    const history = messages
      .filter((message) => message.includeInHistory !== false)
      .map(({role, content}) => ({role, content}));

    setMessages((prev) => [...prev, userMessage, assistantMessage]);
    setInput("");
    setStreaming(true);
    // 自己发消息必须看到回复：恢复跟随。唯一允许接管滚动位置的场景。
    stickToBottomRef.current = true;

    abortRef.current = new AbortController();

    try {
      const res = await fetch(`${BACKEND_URL}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: abortRef.current.signal,
        body: JSON.stringify({
          message: text,
          history,
          topic: state.topic,
          hint_level: state.hint_level,
          misconception: state.misconceptions,
          resolved: state.resolved,
          session_id: sessionId,
          provider: activeProvider,
        }),
      });

      if (!res.ok) {
        let detail = `Server error: ${res.status}`;
        try {
          const errorBody = await res.json();
          if (typeof errorBody.detail === "string") detail = errorBody.detail;
        } catch {
          // Keep the status-based fallback when the body is not JSON.
        }
        throw new Error(detail);
      }
      if (!res.body) throw new Error("Server returned an empty response");

      const reader  = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer    = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const raw = line.slice(6).trim();
          if (raw === "[DONE]") continue;

          try {
            const event = JSON.parse(raw);

            if (event.type === "token") {
              setMessages((prev) =>
                prev.map((message) =>
                  message.id === assistantMessageId
                    ? {...message, content: message.content + event.content}
                    : message
                )
              );
            }

            if (event.type === "state") {
              setState({
                topic:         event.topic,
                hint_level:    event.hint_level,
                misconceptions: event.misconception,
                resolved:      event.resolved,
              });
            }

            if (event.type === "error") {
              setMessages((prev) =>
                prev.map((message) =>
                  message.id === assistantMessageId
                    ? {...message, content: `Error: ${event.content}`}
                    : message
                )
              );
            }
          } catch {
            // malformed SSE line, skip
          }
        }
      }
    } catch (err: unknown) {
      if (err instanceof Error && err.name !== "AbortError") {
        setMessages((prev) =>
          prev.map((message) =>
            message.id === assistantMessageId
              ? {
                  ...message,
                  content: err.message || "Connection error. Is the backend running?",
                }
              : message
          )
        );
      }
    } finally {
      setStreaming(false);
      inputRef.current?.focus();
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  async function releaseDocumentSession(id: string) {
    if (!id) return;
    try {
      await fetch(`${BACKEND_URL}/sessions/${encodeURIComponent(id)}`, {
        method: "DELETE",
      });
    } catch {
      // Backend TTL cleanup remains the fallback if the client disconnects.
    }
  }

  function reset() {
    abortRef.current?.abort();
    uploadAbortRef.current?.abort();
    const previousSessionId = sessionId;
    setMessages([]);
    // 清空对话后没有历史可看，恢复跟随，让后续新消息正常停到底部。
    stickToBottomRef.current = true;
    setState({ topic: "", hint_level: 0, misconceptions: "", resolved: false });
    setInput("");
    setStreaming(false);
    setUploading(false);
    setSessionId("");
    setDocUploaded(false);
    if (fileInputRef.current) fileInputRef.current.value = "";
    void releaseDocumentSession(previousSessionId);
  }

  const sendReport = async () => {
  if (isSending) return;
  if (messages.length === 0) {
    alert("No conversation to report.");
    return;
  }

  const cfg = getEmailJsConfig();
  if (!cfg) {
    alert(
      "反馈通道未配置：缺少 NEXT_PUBLIC_EMAILJS_SERVICE_ID / _TEMPLATE_ID / _PUBLIC_KEY，暂时无法发送。",
    );
    return;
  }

  setIsSending(true);
  try {
    // Format conversation for email
    let conversationLog = "";
    messages.forEach((msg) => {
      const role = msg.role === "user" ? "👤 User" : "🤖 AI Tutor";
      // 邮件正文同样是纯文本，标签在这里也要洗掉，否则收件人看到的是代码。
      const body =
        msg.role === "assistant" ? sanitizeAssistantText(msg.content) : msg.content;
      conversationLog += `${role}:\n${body}\n\n---\n\n`;
    });

    await sendViaEmailJs(cfg, {
      conversation_log: conversationLog,
      date: new Date().toLocaleString(),
      user_id: "anonymous",
    });
    alert("✅ Report sent! Thank you for your help.");
  } catch (error) {
    console.error("EmailJS error:", error);
    alert("❌ Failed to send report. Please try again later.");
  } finally {
    setIsSending(false);
  }
};


  // Upload file handler
  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file || uploading || streaming) return;

    setUploading(true);
    uploadAbortRef.current = new AbortController();
    const uploadMessage = newMessage(
      "assistant",
      `Uploading "${file.name}"...`,
      false,
    );
    const uploadMessageId = uploadMessage.id;

    // Immediately show uploading message in chat
    setMessages((prev) => [...prev, uploadMessage]);
    // 用户主动上传，让上传进度条可见。
    stickToBottomRef.current = true;

    const formData = new FormData();
    formData.append("file", file);

    try {
      const res = await fetch(`${BACKEND_URL}/upload`, {
        method: "POST",
        body: formData,
        signal: uploadAbortRef.current.signal,
      });

      if (!res.ok) throw new Error("Upload failed");

      const data = await res.json();
      const previousSessionId = sessionId;
      setSessionId(data.session_id);
      setDocUploaded(true);
      if (previousSessionId && previousSessionId !== data.session_id) {
        void releaseDocumentSession(previousSessionId);
      }

      // Replace the uploading message with success
      setMessages((prev) =>
        prev.map((message) =>
          message.id === uploadMessageId
            ? {
                ...message,
                content: `✓ "${file.name}" uploaded and indexed. I will now use it to help answer your questions.`,
              }
            : message
        )
      );
    } catch (error) {
      if (error instanceof Error && error.name === "AbortError") return;

      // Replace the uploading message with error
      setMessages((prev) =>
        prev.map((message) =>
          message.id === uploadMessageId
            ? {
                ...message,
                content: sessionId
                  ? "Failed to upload document. The previous document is still active."
                  : "Failed to upload document. Please try again.",
              }
            : message
        )
      );
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  // JSX

  const HINT_LABELS: Record<number, {label: string; color: string}> = {
    0: {label: "Analogy", color: "#10b981"},
    1: {label: "Hint", color: "#f59e0b"},
    2: {label: "Leading Q", color: "#f97316"},
    3: {label: "Revealing", color: "#ef4444"},
  };

  const hint = HINT_LABELS[state.hint_level];

  return (
    <div className="flex h-screen bg-zinc-850 items-center justify-center">
      <div className="flex flex-col w-full max-w-5xl h-[95vh] bg-zinc-900 rounded-2xl border border-zinc-800 overflow-hidden text-zinc-100">

        {/* Header */}
        <header className="flex items-center justify-between px-6 py-4 border-b border-zinc-800">
          <div className="flex items-center gap-3">
            <Link
              href="/"
              aria-label="Back to home"
              className="rounded-md text-lg font-semibold text-white transition-colors hover:text-zinc-300 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-white"
            >
              CS Tutor
            </Link>
            {state.topic && (
              <span className="text-xs px-2 py-0.5 rounded bg-zinc-800 text-zinc-400">
                {state.topic}
              </span>
            )}
          </div>
          <div className="flex items-center gap-4">
            <label className="flex items-center gap-2 text-xs text-zinc-400">
              <span className="sr-only">AI model</span>
              <select
                aria-label="AI model"
                value={activeProvider}
                onChange={(event) =>
                  setActiveProvider(event.target.value as Provider)
                }
                disabled={streaming}
                className="rounded-lg border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-xs text-zinc-200 outline-none transition-colors hover:border-zinc-500 focus:border-zinc-500 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <option value="groq">Groq · GPT-OSS 120B</option>
                <option value="gemini">Gemini · 2.5 Flash Lite</option>
              </select>
            </label>
            {state.topic && (
              <div className="flex items-center gap-2 text-xs">
                <span
                  className="w-2 h-2 rounded-full"
                  style={{ backgroundColor: hint.color }}
                />
                <span className="text-zinc-400">{hint.label}</span>
              </div>
            )}
            <button
              onClick={reset}
              className="text-xs text-zinc-500 hover:text-zinc-300 transition-colors"
            >
              new session
            </button>
          </div>
        </header>

        {/* Messages */}
        <div
          ref={scrollBoxRef}
          onScroll={handleScroll}
          className="flex-1 overflow-y-auto px-6 py-6 space-y-6"
        >
          {messages.length === 0 && (
            <div className="flex flex-col items-center justify-center h-full gap-4 text-center">
              <p className="text-zinc-500 text-sm max-w-sm">
                Ask me to explain any CS concept. I will guide you to the answer
                with analogies and questions instead of just telling you.
              </p>
              <div className="flex flex-wrap gap-2 justify-center">
                {["Explain recursion", "How does a hash table work?", "What is Big O notation?"].map((q) => (
                  <button
                    key={q}
                    onClick={() => { setInput(q); inputRef.current?.focus(); }}
                    className="text-xs px-3 py-1.5 rounded border border-zinc-700 text-zinc-400 hover:border-zinc-500 hover:text-zinc-200 transition-colors"
                  >
                    {q}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((msg, i) => (
            <div
              key={msg.id}
              className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
            >
              <div
                className={`max-w-[75%] rounded-2xl px-4 py-3 text-sm leading-relaxed whitespace-pre-wrap ${
                  msg.role === "user"
                    ? "bg-zinc-800 text-zinc-100 rounded-br-sm"
                    : "bg-zinc-900 border border-zinc-800 text-zinc-200 rounded-bl-sm"
                }`}
              >
                {msg.role === "assistant"
                  ? sanitizeAssistantText(msg.content)
                  : msg.content}
                {streaming && i === messages.length - 1 && msg.role === "assistant" && (
                  <span className="inline-block w-0.5 h-3.5 bg-zinc-400 ml-0.5 animate-pulse align-middle" />
                )}
              </div>
            </div>
          ))}
        </div>

        {/* Input */}
        <div className="px-6 py-4 border-t border-zinc-800">
          <div className="flex gap-3 items-end">
            <textarea
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Ask a question or answer mine..."
              rows={5}
              className="flex-1 resize-y bg-zinc-900 border border-zinc-700 rounded-xl px-4 py-3 text-sm text-zinc-100 placeholder-zinc-600 focus:outline-none focus:border-zinc-500 transition-colors"
              style={{ minHeight: "80px", maxHeight: "300px" }}
            />

            <input
              ref={fileInputRef}
              type="file"
              accept=".pdf"
              onChange={handleUpload}
              className="hidden"
              aria-label="Upload PDF document"
            />
            <button
              onClick={() => fileInputRef.current?.click()}
              disabled={uploading || streaming}
              className={`px-4 py-3 rounded-xl text-sm font-medium transition-colors border ${
                docUploaded
                  ? "border-green-500 text-green-500"
                  : "border-zinc-700 text-zinc-400 hover:border-zinc-500 hover:text-zinc-200"
              } disabled:opacity-30 disabled:cursor-not-allowed`}
            >
              {uploading ? "..." : docUploaded ? "Doc ✓" : "Upload"}
            </button>

            <button
              onClick={send}
              disabled={streaming || uploading || !input.trim()}
              className="px-4 py-3 rounded-xl bg-white text-zinc-950 text-sm font-medium hover:bg-zinc-200 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              {streaming ? "..." : "Send"}
            </button>
          </div>
          <p className="text-xs text-zinc-600 mt-2">
            Enter to send · Shift+Enter for new line
          </p>
        </div>
        {/* Send Report button (only if user consented) */}
          {consent && (
            <button
              onClick={sendReport}
              disabled={isSending}
              className="fixed bottom-4 right-4 bg-red-600 text-white px-4 py-2 rounded-full shadow-lg hover:bg-red-700 disabled:opacity-50 z-50"
            >
              {isSending ? "Sending..." : "📧 Send Report"}
            </button>
          )}

      </div>
    </div>
  );
}

