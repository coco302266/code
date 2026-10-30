/**
 * POST /chat 的 SSE 流式解析。
 *
 * 契约里有五条硬要求，这里逐条落实：
 *   1. 分帧必须按 `\n\n`，不是 `\n`。按单行切会让跨 TCP 分片的 JSON
 *      解析失败，被 catch 静默吞掉 —— 表现为「AI 回答偶发丢字」。
 *   2. `sources` 是**第一个**事件，带本轮引用的资料切片；可能在 token 之前到达。
 *      三种语义要分开：items 非空 / items 为空 / 收不到事件（契约 3.1 节）。
 *   3. `agent`（M4）是**角色切换信号**：每个角色发言前各一条，
 *      之后的 `token` 归到该角色名下。老前端不认识就静默忽略。
 *   4. `state` **不是必然出现**的：走降级回答（纯寒暄、未识别知识点）时
 *      只发 token + [DONE]。收不到就保持上一轮状态，不要清空、也不要当异常。
 *   5. 回答过程中的错误走事件体的 `type=error`，HTTP 状态码这时已经是 200。
 *      不在循环里处理，模型挂掉时界面会一直转圈。
 */

import { getBackendUrl } from "./identity";
import type { ChatRequestBody, SourceItem, StreamState } from "./types";

/**
 * M4 的 `agent` 事件载荷 —— 一轮里第 index 个发言的角色。
 * `total` 是本轮总发言数：`v1`=1、`group`=2、`debate`=3。
 */
export interface AgentEvent {
  role: string;
  label: string;
  stance: string;
  index: number;
  total: number;
}

export interface StreamHandlers {
  onSources?: (items: SourceItem[]) => void;
  onAgent?: (agent: AgentEvent) => void;
  onToken: (token: string) => void;
  onState: (state: StreamState) => void;
  onError: (message: string) => void;
}

/**
 * 只挑契约里定义过的字段，避免把后端未来新增的字段带进来当脏数据。
 *
 * 导出给历史消息复用：流式那条路（SSE `sources` 事件）和刷新后那条路
 * （`GET /classrooms/{id}/messages` 里落库的 sources 列）必须走同一份归一化，
 * 否则两处对 `origin` / 空值的处理会悄悄漂移，表现为「刷新前后依据长得不一样」。
 */
export function normalizeSources(raw: unknown): SourceItem[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((item): item is Record<string, unknown> => !!item && typeof item === "object")
    .map((item) => ({
      // 白名单式归一：认不出的来源一律当 kb，但**必须显式列出 classroom**，
      // 否则教师课堂资料会被标成「知识库」（2026-10-26 实测的错标签问题）。
      origin:
        item.origin === "upload"
          ? "upload"
          : item.origin === "classroom"
            ? "classroom"
            : "kb",
      source: (item.source as string | null) ?? null,
      topic: (item.topic as string | null) ?? null,
      category: (item.category as string | null) ?? null,
      page: (item.page as number | null) ?? null,
      section: (item.section as string | null) ?? null,
      doc_id: (item.doc_id as string | null) ?? null,
      filename: (item.filename as string | null) ?? null,
    }));
}

/**
 * 发起一次提问并流式回调。
 * 返回 true 表示正常收流结束，false 表示中途出错（给调用方决定要不要重试）。
 */
export async function streamChat(
  body: ChatRequestBody,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<boolean> {
  const base = getBackendUrl();
  let res: Response;

  try {
    res = await fetch(`${base}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    });
  } catch (err) {
    if (err instanceof Error && err.name === "AbortError") return false;
    handlers.onError(
      `连不上后端（${base}）。请确认后端已启动，并且地址填对了。`,
    );
    return false;
  }

  if (res.status === 429) {
    handlers.onError("提问太快了（每分钟 10 次），请等一分钟再试。");
    return false;
  }
  if (!res.ok) {
    let detail = "";
    try {
      const payload = await res.json();
      if (typeof payload?.detail === "string") detail = payload.detail;
    } catch {
      // 忽略非 JSON 错误体
    }
    if (res.status === 403) {
      handlers.onError(
        "你没有加入这个课堂，AI 不会回答。请回到入口页重新进入。",
      );
    } else if (res.status === 503) {
      handlers.onError("后端未就绪：模型或数据库没配好，请检查 backend/.env。");
    } else {
      handlers.onError(detail || `请求失败（HTTP ${res.status}）`);
    }
    return false;
  }
  if (!res.body) {
    handlers.onError("后端返回了空响应。");
    return false;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let streamError = "";

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const frames = buffer.split("\n\n");
      buffer = frames.pop() ?? "";

      for (const frame of frames) {
        if (!frame.startsWith("data: ")) continue;
        const payload = frame.slice(6).trim();
        if (!payload) continue;
        if (payload === "[DONE]") return !streamError;

        let event: Record<string, unknown>;
        try {
          event = JSON.parse(payload) as Record<string, unknown>;
        } catch {
          // 半条帧，等下一轮 buffer 补齐
          continue;
        }

        if (event.type === "sources") {
          handlers.onSources?.(normalizeSources(event.items));
        } else if (event.type === "agent") {
          // 角色切换：之后的 token 都归到这个角色名下，直到下一条 agent 事件
          handlers.onAgent?.({
            role: String(event.role ?? "tutor"),
            label: String(event.label ?? ""),
            stance: String(event.stance ?? ""),
            index: Number(event.index ?? 0),
            total: Number(event.total ?? 1),
          });
        } else if (event.type === "token") {
          handlers.onToken(String(event.content ?? ""));
        } else if (event.type === "state") {
          handlers.onState({
            topic: String(event.topic ?? ""),
            hint_level: Number(event.hint_level ?? 0),
            misconception: String(event.misconception ?? ""),
            resolved: Boolean(event.resolved),
            personalized:
              event.personalized === undefined
                ? undefined
                : Boolean(event.personalized),
          });
        } else if (event.type === "error") {
          streamError = String(event.content ?? "模型调用失败");
          handlers.onError(
            "这一轮回答中断了（模型侧出错）。可以再发一次，或换个说法问。",
          );
        }
        // 未知 type 静默忽略 —— 后端先加事件、前端后接是安全的
      }
    }
  } catch (err) {
    if (err instanceof Error && err.name === "AbortError") return false;
    handlers.onError("读取回答时连接中断。");
    return false;
  }

  return !streamError;
}
