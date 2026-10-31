"use client";

import { useState } from "react";
import type { MessageRole, SourceItem } from "@/lib/types";
import { sanitizeAssistantText } from "@/lib/answerFormat";
import { RichText } from "./RichText";
import { Spinner, Tag, shortTime } from "./ui";

export interface UIMsg {
  key: string;
  role: MessageRole;
  username: string | null;
  content: string;
  topic?: string | null;
  createdAt?: string | null;
  /** 正在流式接收中的那条 */
  streaming?: boolean;
  /**
   * 本轮引用的资料（来自 SSE 的 sources 事件）。
   * 用 `undefined` 和 `[]` 区分两件事：
   *   undefined = 这一轮没收到 sources 事件（这条流没覆盖）
   *   []        = 后端明确说了「本轮没用资料」
   */
  sources?: SourceItem[];
  /** M4：这条 AI 发言的角色键（tutor / teacher / peer / examiner）。 */
  agentRole?: string | null;
  /** M4：角色中文名，后端给的，前端不自己维护映射。 */
  agentLabel?: string | null;
  /** M4：该角色在本场景里的身份，如「主讲」「质疑」，作气泡副标题。 */
  agentStance?: string | null;
  /** M4：本条发言在本轮中的序号，从 0 开始。 */
  agentIndex?: number | null;
  /** M4：本轮总发言数；> 1 时才需要显示进度。 */
  agentTotal?: number | null;
}

/**
 * 角色 → 视觉。
 *
 * 四个 AI 角色必须一眼分得开，否则多智能体编排在界面上等于没做 ——
 * 学生看到的究竟是「同一段话被切成三块」还是「三个人各说各的」，
 * 全靠颜色和角标区分。
 */
const AGENT_STYLE: Record<
  string,
  { avatar: string; skin: string; accent: string; mark: string; label: string }
> = {
  // 气泡 = 更圆的淡色卡片 + 左侧一枚圆头色条 + 柔和投影，不描边。
  // 描边一圈会在淡色底上显得生硬（像表格），改成一枚「书签条」更像对话产品；
  // 色条用 absolute 圆头短棒（inset-y），不是整条边，视觉上更轻。
  tutor: {
    avatar: "bg-ai-soft text-ai ring-ai-line",
    skin: "bg-ai-soft text-ink",
    accent: "bg-ai",
    mark: "讲",
    label: "主讲助教",
  },
  teacher: {
    avatar: "bg-teacher-soft text-teacher ring-teacher-line",
    skin: "bg-teacher-soft text-ink",
    accent: "bg-teacher",
    mark: "师",
    label: "主讲教师",
  },
  peer: {
    avatar: "bg-brand-soft text-brand ring-brand-line",
    skin: "bg-brand-soft text-ink",
    accent: "bg-brand",
    mark: "伴",
    label: "学伴",
  },
  examiner: {
    avatar: "bg-soft text-sub ring-line",
    skin: "bg-soft text-ink",
    accent: "bg-hint",
    mark: "考",
    label: "考官",
  },
};

/** 拿一个角色的展示信息。未知 / 缺失一律退回「AI 助教」。 */
export function agentMeta(role?: string | null) {
  return (
    AGENT_STYLE[role ?? ""] ?? {
      avatar: "bg-ai-soft text-ai ring-ai-line",
      skin: "bg-ai-soft text-ink",
      accent: "bg-ai",
      mark: "AI",
      label: "AI 助教",
    }
  );
}

export function Avatar({
  name,
  role,
  agentRole,
}: {
  name: string | null;
  role: MessageRole;
  agentRole?: string | null;
}) {
  const isAi = role === "assistant";
  const meta = agentMeta(agentRole);
  // AI 用角色角标而不是首字母：一轮里可能有 3 个 AI，首字母全是 "A"，分不出来
  const label = isAi ? meta.mark : (name || "?").slice(0, 1);
  return (
    <span
      className={`grid h-9 w-9 shrink-0 place-items-center rounded-[10px] text-[14px] font-medium ring-1 ring-inset ${
        isAi
          ? meta.avatar
          : role === "teacher"
            ? "bg-teacher-soft text-teacher ring-teacher-line"
            : "bg-brand-soft text-brand ring-brand-line"
      }`}
    >
      {label}
    </span>
  );
}

/**
 * 一条消息。**所有人都左对齐**，不做「自己的消息靠右」——
 * 产品决策是「一个课堂 = 一个群」，靠右会让共享上下文看起来像私聊。
 */
export function MessageRow({
  msg,
  isMine = false,
}: {
  msg: UIMsg;
  isMine?: boolean;
}) {
  const isAi = msg.role === "assistant";
  const meta = agentMeta(msg.agentRole);
  // 优先用后端给的角色名；拿不到（老后端 / 降级流）就退回「AI 助教」
  const name = isAi
    ? (msg.agentLabel || meta.label || "AI 助教")
    : (msg.username ?? "未知用户");

  return (
    <div className="rise flex gap-3" data-selectable>
      <Avatar
        name={msg.username}
        role={msg.role}
        agentRole={msg.agentRole}
      />
      {/* 一条消息 = 一张卡片：名字行、正文、依据都收在卡片里。
          这样在整屏照片背景上，任何文字都落在淡色底上，不会出现
          「时间戳和依据条压在照片上读不清」的情况。头像留在卡片外侧，
          保证一屏扫下来仍是聊天流的节奏，而不是一列卡片。 */}
      <div
        className={`min-w-0 flex-1 rounded-[18px] px-5 py-3.5 shadow-[0_1px_2px_rgba(15,23,42,0.05),0_14px_30px_-24px_rgba(15,23,42,0.45)] ${
          isAi
            ? meta.skin
            : msg.role === "teacher"
              ? "bg-teacher-soft text-ink"
              : isMine
                ? "bg-brand-soft text-ink"
                : "bg-panel text-ink"
        }`}
      >
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[13px] font-medium text-ink">{name}</span>
          {isAi && <Tag tone="ai">AI</Tag>}
          {isAi && msg.agentStance && (
            <Tag tone="neutral">{msg.agentStance}</Tag>
          )}
          {isAi &&
            msg.agentTotal !== null &&
            msg.agentTotal !== undefined &&
            msg.agentTotal > 1 && (
              <span className="text-[12px] text-hint">
                {(msg.agentIndex ?? 0) + 1}/{msg.agentTotal}
              </span>
            )}
          {msg.role === "teacher" && <Tag tone="teacher">教师</Tag>}
          {isMine && !isAi && <Tag tone="brand">我</Tag>}
          {msg.topic && <Tag tone="neutral">{msg.topic}</Tag>}
          <span className="ml-auto text-[12px] text-hint">
            {shortTime(msg.createdAt)}
          </span>
        </div>

        <div className="mt-2 whitespace-pre-wrap text-[14px] leading-[1.75]">
          {/* AI 回答统一过一遍清洗：模型偶尔漏出的 HTML 标签在这里被还原成
              纯文本，否则学生会看到一串裸露的 <td>。详见 lib/answerFormat.ts。
              清洗完再交给 RichText：把「表头 + 虚线上划 + 数据行」的文本块
              用栅格重排 —— 比例字体下靠空格对齐的表格必然错位。 */}
          {isAi ? <RichText text={sanitizeAssistantText(msg.content)} /> : msg.content}
          {msg.streaming && msg.content && (
            <span className="ml-0.5 inline-block h-3.5 w-[2px] translate-y-[2px] animate-pulse bg-ai" />
          )}
          {msg.streaming && !msg.content && (
            <span className="text-[13px] text-hint">
              <Spinner className="mr-1 align-middle" />
              正在检索知识库并组织讲解…
            </span>
          )}
        </div>

        {isAi && <SourceLine sources={msg.sources} />}
      </div>
    </div>
  );
}

/**
 * 本轮资料依据。
 *
 * 契约里专门强调过三种状态不能混为一谈：
 *   - 收到非空 items  → 列出来
 *   - 收到空 items    → 明确「本轮没用资料」（例如纯寒暄）
 *   - 没收到事件      → 什么都不显示（这条流没覆盖，不代表没用资料）
 *
 * 这个信息对学生是有用的（知道答案有没有依据），
 * 对排查检索退化更是唯一线索 —— 检索全错时模型照样答得很好，从回答上看不出来。
 */
function SourceLine({ sources }: { sources?: SourceItem[] }) {
  // 折叠态只留一个入口：移动端一行四个截断标签会占掉三行，把正文挤下去，
  // 而「本轮有没有依据」这件事一个计数就够，具体是哪几篇点开再看。
  const [open, setOpen] = useState(false);
  if (!sources) return null;

  if (sources.length === 0) {
    return (
      <p className="mt-1.5 pl-1 text-[12px] text-hint">依据：模型自有知识</p>
    );
  }

  // 同一文件可能命中多片，按展示标签去重
  const seen = new Set<string>();
  const chips = sources
    .map((item) => {
      if (item.origin === "upload") {
        const label = item.page ? `上传文档 · 第 ${item.page} 页` : "上传文档";
        return { key: `u-${label}`, label };
      }
      if (item.origin === "classroom") {
        // 一个课堂可能传了十几份讲义，「第 3 页」单独拿出来无法定位是哪一份，
        // 所以标签带文件名（过长时截断，完整名放 title）。
        const name = item.filename || fileName(item.source) || "课堂资料";
        const short = name.length > 16 ? `${name.slice(0, 15)}…` : name;
        const label = item.page ? `课堂 · ${short} 第 ${item.page} 页` : `课堂 · ${short}`;
        return {
          key: `c-${item.doc_id ?? name}-${item.page ?? ""}`,
          label,
          title: [name, item.page ? `第 ${item.page} 页` : "", item.section]
            .filter(Boolean)
            .join(" · "),
        };
      }
      const label = item.topic || fileName(item.source) || "知识库";
      return {
        key: `k-${item.source ?? label}`,
        label,
        title: [item.category, item.source].filter(Boolean).join(" · "),
      };
    })
    .filter((chip) => {
      if (seen.has(chip.key)) return false;
      seen.add(chip.key);
      return true;
    });

  return (
    <div className="mt-1.5 pl-1">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-1 rounded-md px-1 py-0.5 text-[12px] text-hint transition-colors hover:bg-soft hover:text-brand"
        aria-expanded={open}
      >
        依据 · {chips.length} 篇
        <span className="text-[12px]">{open ? "收起" : "展开"}</span>
      </button>
      {open && (
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
          {chips.map((chip) => (
            <span
              key={chip.key}
              title={"title" in chip ? chip.title : undefined}
              className="rounded-md border border-line bg-soft px-1.5 py-0.5 text-[12px] leading-5 text-sub"
            >
              {chip.label}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function fileName(source?: string | null): string {
  if (!source) return "";
  const parts = source.split(/[\\/]/);
  return parts[parts.length - 1] ?? "";
}
