/**
 * 后端接口封装 —— 所有页面都通过这里访问后端，页面里不出现裸 fetch。
 *
 * 契约依据：`docs/specs/API_CONTRACT.md`。三个最容易踩的点在这里一次性处理掉：
 *   1. 错误码 → 中文可读文案（不把状态码丢给用户看）
 *   2. `classroom_id` + `user_id` 必须成对传（缺一个会静默不落库）
 *   3. 请求体不能多字段（后端 extra="forbid"，多传直接 422）
 */

import { getBackendUrl } from "./identity";
import type {
  Behavior,
  Classroom,
  ClassroomDocument,
  ClassroomList,
  Health,
  Member,
  ChatMessage,
  PptOutlineResult,
  ScenarioItem,
  StudentProfile,
  User,
  UserRole,
} from "./types";

export class ApiError extends Error {
  status: number | null;
  detail: string;

  constructor(message: string, status: number | null = null, detail = "") {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

/** 状态码 → 人话。后端 `detail` 是英文，直接展示给同学看很奇怪。 */
const STATUS_TEXT: Record<number, string> = {
  400: "请求内容不合法",
  401: "用户名或密码不正确",
  403: "没有权限：你可能不在这个课堂，或以错误的身份加入",
  404: "找不到对应数据（课堂、用户或画像可能还不存在）",
  409: "操作被拒绝（名额已满或权限冲突）",
  413: "文件太大了（PDF 上限 10MB）",
  422: "请求参数不合法",
  429: "请求太频繁了，请等一分钟再试",
  502: "模型暂时不可用，请稍后重试",
  503: "后端未就绪：数据库或模型还没配好，请检查 backend/.env",
};

/**
 * 这几类错误的后端 `detail` 就是给人看的中文（「这是指定学生的[1v1教室]，
 * 你不能进入」「这个用户名已经被注册了」「密码至少 6 位」），比状态码
 * 兜底文案精确得多，直接透出。
 *
 * 其余状态码仍用兜底文案：那些 detail 是英文（如 "Classroom 7 not found."），
 * 原样展示给学生等于把后端日志扔到界面上。
 */
const DETAIL_FIRST_STATUS = new Set([400, 401, 409, 422]);

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const base = getBackendUrl();
  let res: Response;
  try {
    res = await fetch(`${base}${path}`, {
      ...init,
      // 必须带超时：防火墙「静默丢弃」时 fetch 会一直挂着（浏览器默认几十秒甚至更久），
      // 界面就永远停在「正在检查后端…」，看不出是连不上还是慢。
      signal: init?.signal ?? AbortSignal.timeout(15000),
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch (err) {
    const timeout =
      err instanceof Error && (err.name === "TimeoutError" || err.name === "AbortError");
    throw new ApiError(
      timeout
        ? `连不上后端（${base}）：请求超时。常见原因是防火墙没放行 8000，或后端没绑 0.0.0.0。`
        : `连不上后端（${base}）。请确认后端已启动：uvicorn app:app --port 8000`,
    );
  }

  if (!res.ok) {
    let detail = "";
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      // 非 JSON 响应体就走状态码兜底
    }
    const friendly = STATUS_TEXT[res.status] ?? `请求失败（HTTP ${res.status}）`;
    const message =
      detail && DETAIL_FIRST_STATUS.has(res.status) ? detail : friendly;
    throw new ApiError(message, res.status, detail);
  }

  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/** 拼完整下载地址。中文文件名必须 encodeURIComponent，否则会 404。 */
export function resourceUrl(downloadUrl: string): string {
  const name = downloadUrl.split("/").pop() ?? "";
  return `${getBackendUrl()}/resources/files/${encodeURIComponent(name)}`;
}

// --- 健康检查 ------------------------------------------------------------

export function getHealth(): Promise<Health> {
  // 5s 就够：这个接口只回一行 JSON。超时短一点，连不上时能立刻提示而不是干等。
  return request<Health>("/health", { signal: AbortSignal.timeout(5000) });
}

// --- 用户 / 课堂 / 成员 ---------------------------------------------------

/**
 * 注册（2026-11-02 起必须带密码）。
 *
 * 后端分配的 `id` 是账号的唯一凭证：老师开 1v1 教室时就是拿它指定学生，
 * 所以调用方要把返回的 id 展示给用户，而不是丢掉只留昵称。
 *
 * 重名会抛 409（ApiError.status === 409）——不再是「同名复用」，
 * 那是口令上线前的临时做法，留着等于谁都能接管别人的账号。
 */
export function register(
  username: string,
  password: string,
  role: UserRole,
): Promise<User> {
  return request<User>("/users", {
    method: "POST",
    body: JSON.stringify({ username, password, role }),
  });
}

/**
 * 登录：用户名 + 密码 → 账号信息。
 *
 * `password_bound` 为 true 表示这是口令功能上线前的老账号，本次登录
 * 顺手把密码绑定上了（后端行为），界面可以提示一句。
 * 密码错误与用户不存在都返回 401、同一句文案（避免用户名枚举）。
 */
export function login(
  username: string,
  password: string,
): Promise<User & { password_bound?: boolean }> {
  return request<User & { password_bound?: boolean }>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
}

/** 用户列表：教师建房时挑「指定学生」用。只回 id / username / role。 */
export function listUsers(role?: UserRole): Promise<{ count: number; items: User[] }> {
  const query = role ? `?role=${role}` : "";
  return request<{ count: number; items: User[] }>(`/users${query}`);
}

/**
 * 建房（M4 场景 + 2026-11-02 的 1v1 指定学生）。
 *
 * `targetStudentId` 只对 `scenario="v1"` 有意义：给了就只有这位学生能进。
 * `useTutor` 现在默认 true 且建房界面不再暴露——助教改为课堂内随开随关，
 * 建课时必须默认配备，否则老师一进来面对的是「没有讲解老师」的空课堂。
 */
export function createClassroom(params: {
  name: string;
  subject?: string;
  description?: string | null;
  scenario?: string;
  useTutor?: boolean;
  targetStudentId?: number | null;
}): Promise<Classroom> {
  const body: Record<string, unknown> = {
    name: params.name,
    subject: params.subject ?? "cs",
    description: params.description ?? null,
    use_tutor: params.useTutor ?? true,
  };
  if (params.scenario) body.scenario = params.scenario;
  // 只在真的选了学生时带上：显式传 null 与不传同义，但少一个字段更干净。
  if (params.targetStudentId != null) {
    body.target_student_id = params.targetStudentId;
  }

  return request<Classroom>("/classrooms", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/**
 * 改课堂设置（当前只有主讲助教开关）。
 *
 * 必须带 `teacher_id`：后端没有登录态，靠它查成员表确认操作者确实是
 * 这个课堂的教师；不带会是 422，带错人会是 403。
 */
export function updateClassroom(
  classroomId: number,
  teacherId: number,
  patch: { useTutor?: boolean },
): Promise<{ id: number; use_tutor: boolean; scenario: string }> {
  const body: Record<string, unknown> = {};
  if (patch.useTutor !== undefined) body.use_tutor = patch.useTutor;
  return request(`/classrooms/${classroomId}?teacher_id=${teacherId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

/**
 * M4：列出可用协作场景，建房下拉的直接数据源。
 *
 * 为什么每次都请求而不是前端写死一份：场景定义在后端 `agent/scenarios.py`，
 * 写死会让「后端加一个场景」变成改两处，且容易只对上一处。
 */
export function listScenarios(): Promise<{ items: ScenarioItem[] }> {
  return request<{ items: ScenarioItem[] }>("/scenarios");
}

export function getClassroom(classroomId: number): Promise<Classroom> {
  return request<Classroom>(`/classrooms/${classroomId}`);
}

/**
 * 我的课堂（M1 补）：按成员关系列出某用户加入的全部课堂。
 *
 * 之前这一屏只能读本机 localStorage，换台机器就空了。
 * 后端返回里的 `role` 是**该用户在这个课堂内的成员角色**，
 * 比 localStorage 记的 `identity.role`（全局角色）准 ——
 * 同一个人完全可能既是 A 房的老师、又是 B 房的学生。
 * 所以拿到后端数据时要**用返回的 role 覆盖本地记的角色**，别反过来。
 *
 * 不传 user_id 时后端返回空列表（避免泄露全量课堂），
 * 所以调用前一定要确认有身份；拿不到就由调用方回退本机记录。
 */
export function listClassrooms(userId: number): Promise<ClassroomList> {
  return request<ClassroomList>(`/classrooms?user_id=${userId}`);
}

/** 重复加入不会报错，返回已有记录。 */
export function addMember(
  classroomId: number,
  userId: number,
  role: UserRole,
): Promise<{ id: number; classroom_id: number; user_id: number; role: UserRole }> {
  return request(`/classrooms/${classroomId}/members`, {
    method: "POST",
    body: JSON.stringify({ user_id: userId, role }),
  });
}

export function listMembers(
  classroomId: number,
): Promise<{ classroom_id: number; count: number; members: Member[] }> {
  return request(`/classrooms/${classroomId}/members`);
}

/** 课堂全量对话（群聊视图）。 */
export function listMessages(
  classroomId: number,
  limit = 100,
): Promise<{ classroom_id: number; count: number; messages: ChatMessage[] }> {
  return request(`/classrooms/${classroomId}/messages?limit=${limit}`);
}

// --- 学生画像 ------------------------------------------------------------

/**
 * 带 `classroom_id` 返回**单个对象**；不带返回**列表包装**。
 * 契约里点名说这是最容易踩的坑，所以这里只暴露「带 classroom_id」这一种用法。
 */
export function getProfile(
  userId: number,
  classroomId: number,
): Promise<StudentProfile> {
  return request<StudentProfile>(
    `/students/${userId}/profile?classroom_id=${classroomId}`,
  );
}

export function listBehaviors(
  userId: number,
  classroomId: number,
  limit = 50,
): Promise<{ user_id: number; count: number; behaviors: Behavior[] }> {
  return request(
    `/students/${userId}/behaviors?classroom_id=${classroomId}&limit=${limit}`,
  );
}

/** 预览讲法，不消耗模型额度 —— 调提示词时用它，省额度。 */
export function promptPreview(
  userId: number,
  classroomId: number,
  topic = "",
  hintLevel = 0,
): Promise<Record<string, unknown>> {
  const query = new URLSearchParams({
    classroom_id: String(classroomId),
    topic,
    hint_level: String(hintLevel),
  });
  return request(`/students/${userId}/prompt-preview?${query.toString()}`);
}

// --- 教师备课 ------------------------------------------------------------

export function pptOutline(params: {
  topic: string;
  classroomId: number;
  teacherId: number;
  audience?: "" | "beginner" | "intermediate" | "advanced";
}): Promise<PptOutlineResult> {
  return request<PptOutlineResult>("/resources/ppt-outline", {
    method: "POST",
    body: JSON.stringify({
      topic: params.topic,
      classroom_id: params.classroomId,
      teacher_id: params.teacherId,
      audience: params.audience ?? "",
    }),
  });
}

// --- 课堂资料（教师上传的长期资料，XB-23/24）------------------------------

/**
 * multipart 上传必须**不带** `Content-Type`：
 * `request()` 固定塞 `application/json`，浏览器就不会自己生成
 * `multipart/form-data; boundary=...`，后端收到的表单是空的。
 * 这类错误表现为「上传成功但文件字段为空」，很难往请求头上想。
 */
async function requestForm<T>(path: string, form: FormData): Promise<T> {
  const base = getBackendUrl();
  let res: Response;
  try {
    res = await fetch(`${base}${path}`, { method: "POST", body: form });
  } catch {
    throw new ApiError(
      `连不上后端（${base}）。请确认后端已启动：uvicorn app:app --port 8000`,
    );
  }

  if (!res.ok) {
    let payload: unknown = null;
    try {
      payload = await res.json();
    } catch {
      // 非 JSON 响应体走状态码兜底
    }
    const detail = (payload as { detail?: unknown } | null)?.detail;
    const text = typeof detail === "string" ? detail : "";
    throw new ApiError(uploadErrorMessage(res.status, text, detail), res.status, text);
  }
  return (await res.json()) as T;
}

/** 上传失败的真实原因要能区分：配额满、内容重复、扫描件，处理方式完全不同。 */
function uploadErrorMessage(status: number, text: string, detail: unknown): string {
  if (status === 409) {
    const name =
      detail && typeof detail === "object" && "filename" in detail
        ? String((detail as { filename?: string }).filename ?? "")
        : "";
    return name
      ? `这份文件已经在课堂资料里了（「${name}」）。同一份内容不会重复入库。`
      : "这份文件已经在课堂资料里了。同一份内容不会重复入库。";
  }
  if (status === 413) {
    // 后端 413 有两种含义：文件超 10MB，或课堂配额满。
    return text.includes("quota")
      ? "这个课堂的资料配额满了（默认 20 份 / 200MB）。先在下方清单里删掉不再需要的资料。"
      : "文件太大了，单个 PDF 上限 10MB。";
  }
  if (status === 422 && text.includes("no extractable text")) {
    return "这份 PDF 抽不出文字（可能是扫描件或纯图片）。请上传可搜索文字的版本，或先做 OCR。";
  }
  if (status === 403) return STATUS_TEXT[403];
  if (status === 429) return "上传太频繁了（每小时 10 次），请稍后再试。";
  return text || STATUS_TEXT[status] || `上传失败（HTTP ${status}）`;
}

export function listClassroomDocuments(
  classroomId: number,
  teacherId: number,
): Promise<{ classroom_id: number; count: number; documents: ClassroomDocument[] }> {
  return request(
    `/classrooms/${classroomId}/documents?teacher_id=${teacherId}`,
  );
}

export function uploadClassroomDocument(params: {
  file: File;
  classroomId: number;
  teacherId: number;
  persist?: "classroom" | "session";
}): Promise<{
  doc_id?: string;
  session_id?: string;
  filename: string;
  chunk_count?: number;
  file_bytes?: number;
  persist: string;
}> {
  const form = new FormData();
  form.append("file", params.file);
  form.append("teacher_id", String(params.teacherId));
  form.append("classroom_id", String(params.classroomId));
  form.append("persist", params.persist ?? "classroom");
  return requestForm("/upload/teacher", form);
}

export function deleteClassroomDocument(
  classroomId: number,
  docId: string,
  teacherId: number,
): Promise<void> {
  return request(
    `/classrooms/${classroomId}/documents/${docId}?teacher_id=${teacherId}`,
    { method: "DELETE" },
  );
}
