/**
 * 后端数据模型 —— 与 `docs/specs/API_CONTRACT.md` 逐字对应。
 * 改这里之前先看契约，别照着直觉猜字段名。
 */

export type UserRole = "student" | "teacher";

/** 消息里的发言人角色。注意 AI 是 assistant，不是 teacher。 */
export type MessageRole = "student" | "teacher" | "assistant";

/**
 * AI 发言时是**哪个角色 Agent** 在说话（M1）。
 * 与 `MessageRole` 是两件事：role 区分「人还是 AI」，agent_role 区分「AI 扮演谁」。
 */
export type AgentRole = "tutor" | "teacher" | "peer" | "examiner";

export type Level = "beginner" | "intermediate" | "advanced";

export interface User {
  id: number;
  username: string;
  role: UserRole;
  /** 注册时间（后端 server_default now()），注册成功页用来确认账号已落库。 */
  created_at?: string | null;
}

export interface Classroom {
  id: number;
  name: string;
  subject: string;
  description: string | null;
  /** M1：true 表示两人私聊房间。 */
  is_private?: boolean;
  /** M4：建房即定的协作场景（v1 / group）。 */
  scenario?: string | null;
  /** 是否配备主讲助教。关掉后整轮问答交给出题助手（纯练习课堂）。 */
  use_tutor?: boolean;
  /**
   * 1v1 指定学生（2026-11-02）。非空时只有这位学生能进这个教室。
   * null / undefined = 未指定，回到「谁先进谁占位」。
   */
  target_student_id?: number | null;
  target_student_username?: string | null;
  created_at?: string | null;
}

/** M4 场景里的一个发言位。来自 `GET /scenarios` 的 `steps[]`。 */
export interface ScenarioStep {
  role: string;
  label: string;
  /** 该角色在本场景中的身份，如「主讲」「补充」「质疑」「追问」。 */
  stance: string;
  handoff?: string;
}

/**
 * M4 可用协作场景。
 *
 * 场景与角色序列的**唯一事实源在后端** `agent/scenarios.py`，
 * 前端只负责透传渲染 —— 不硬编码一份，否则后端加场景要改两处。
 */
export interface ScenarioItem {
  key: string;
  label: string;
  agents?: string[];
  steps: ScenarioStep[];
}

export interface Member {
  user_id: number;
  username: string;
  role: UserRole;
}

export interface ChatMessage {
  id: number;
  user_id: number | null;
  /** AI 的回复为 null，界面按 role 显示发言人。 */
  username: string | null;
  role: MessageRole;
  content: string;
  topic: string | null;
  hint_level: number;
  created_at: string | null;
  /**
   * M1：AI 回复由哪个角色 Agent 产出，人类消息为 null。
   * 一轮多个 Agent 时库里就是多行，agent_role 各不相同 —— 渲染时按它分气泡。
   */
  agent_role?: string | null;
  /**
   * 本条回复引用了哪几片资料（2026-10-21 起随消息落库）。
   * 后端统一返回 `sources or []`，即至少是空数组；
   * 老数据（未覆盖）可能为 null —— 前端据此不显示"依据"行。
   */
  sources?: SourceItem[] | null;
}

/** `GET /classrooms` 的单条记录：课堂信息 + 我在其中的成员角色。 */
export interface ClassroomListItem extends Classroom {
  /**
   * 该用户在这个课堂内的成员角色（来自 classroom_members）。
   * 不是 users.role —— 权限判定一律以它为准。
   */
  role: UserRole;
  created_at?: string | null;
}

/** `GET /classrooms?user_id=N` 的返回。 */
export interface ClassroomList {
  user_id: number | null;
  count: number;
  classrooms: ClassroomListItem[];
}

export interface StudentProfile {
  user_id: number;
  classroom_id: number;
  overall_level: Level;
  learning_preference: string;
  knowledge_mastery: Record<string, number>;
  weak_points: string[];
  mastery_average: number;
  topic_count: number;
  updated_at: string;
}

export interface Behavior {
  id: number;
  classroom_id: number;
  topic: string;
  action: "ask_question" | "repeat_question" | "request_explanation" | "mastered";
  hint_level: number;
  created_at: string | null;
}

export interface OutlineSection {
  title: string;
  points: string[];
}

export interface Outline {
  title: string;
  objectives: string[];
  sections: OutlineSection[];
  section_titles: string[];
  key_points: string[];
  examples: string[];
  questions: string[];
  summary: string[];
}

export interface PptOutlineResult {
  topic: string;
  classroom_id: number;
  audience: string;
  retrieved: boolean;
  outline: Outline;
  /** 服务端绝对路径，前端不要用。 */
  file_path: string;
  file_name: string | null;
  /** 相对路径，要拼 Base URL；中文名需 encodeURIComponent。 */
  download_url: string | null;
}

export interface Health {
  status: string;
  provider: string;
  model: string;
  database: "up" | "down" | "disabled";
}

/**
 * SSE 的 `sources` 事件 —— 本轮到底给模型喂了哪几片资料。
 * 契约第 3.1 节：第一个事件，在 token 之前，**不带正文**。
 *
 * 三种状态要区分清楚（契约里专门强调过）：
 *   items 非空  = 本轮用了这些资料
 *   items 为空  = 本轮明确没用资料（纯寒暄等）
 *   收不到事件  = 这条流没覆盖，不代表没用资料
 */
export interface SourceItem {
  /**
   * kb       = 共享教材库（cs_basics）
   * upload   = 学生本次会话上传的 PDF（1 小时后失效）
   * classroom = 教师上传的课堂资料（长期生效，本课堂学生可见）
   *
   * ⚠ classroom 是 2026-10-26 新增的第三类。之前这里只有 kb|upload，
   * `normalizeSources` 会把 classroom 悄悄归一成 kb，学生看到的依据徽章
   * 于是标成「知识库」——数据没错、标签错，最难被发现的错法。
   */
  origin: "kb" | "upload" | "classroom";
  /** origin=kb 时：知识库文件路径，如 cs/network/http-https.md */
  source?: string | null;
  /** origin=kb 时：知识点名 */
  topic?: string | null;
  /** origin=kb 时：学科分类名 */
  category?: string | null;
  /** origin=upload / classroom 时：PDF 页码 */
  page?: number | null;
  /** origin=upload / classroom 时：切片所在小节 */
  section?: string | null;
  /** origin=classroom 时：课堂资料 id（一个课堂可能有多份，光有页码无法定位） */
  doc_id?: string | null;
  /** origin=classroom 时：教师上传时的原始文件名 */
  filename?: string | null;
}

/** SSE 流结束时最后一条 state 事件的载荷。 */
export interface StreamState {
  topic: string;
  hint_level: number;
  misconception: string;
  resolved: boolean;
  /**
   * 是否注入了学生画像。
   * 契约约定：只要收到 state，它一定是真的，且一定带这个键。
   */
  personalized?: boolean;
}

/** POST /chat 的请求体。后端开了 extra="forbid"，多一个字段就 422。 */
export interface ChatRequestBody {
  message: string;
  classroom_id?: number;
  user_id?: number;
  topic?: string;
  hint_level?: number;
  misconception?: string;
  resolved?: boolean;
  history?: { role: string; content: string }[];
  session_id?: string;
  /**
   * M4：本轮跑哪种协作形态。
   * 平时**不要传** —— 服务端按建房时定的场景走；只有调试 / 演示时才显式覆盖。
   */
  scenario?: string | null;
}

/**
 * 课堂资料（教师上传、长期生效、本课堂学生可检索）。
 *
 * 后端 `GET /classrooms/{id}/documents` 的返回项。`chunk_count` 是
 * **这份文件自己**的切片数（2026-10-26 修：原先是整个课堂 collection 的总数，
 * 传第二份文件时会把第一份的片一起数进去，教师看到 188 片以为成功了）。
 */
export interface ClassroomDocument {
  doc_id: string;
  filename: string;
  teacher_id: number | null;
  chunk_count: number;
  file_bytes: number;
  created_at: string | null;
}
