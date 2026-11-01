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
  403: "没有权限：你可能不在这个课堂，或以错误的身份加入",
  404: "找不到对应数据（课堂、用户或画像可能还不存在）",
  413: "文件太大了（PDF 上限 10MB）",
  422: "请求参数不合法",
  429: "请求太频繁了，请等一分钟再试",
  502: "模型暂时不可用，请稍后重试",
  503: "后端未就绪：数据库或模型还没配好，请检查 backend/.env",
};

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
    throw new ApiError(friendly, res.status, detail);
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

/** 幂等：重名不会报错，直接返回已有那条。 */
export function createUser(username: string, role: UserRole): Promise<User> {
  return request<User>("/users", {
    method: "POST",
    body: JSON.stringify({ username, role }),
  });
}

/**
 * 建房即定场景（M4）。
 *
 * `scenario` 是可选的：不传后端落 `v1`，行为与 M1 完全一致。
 * 非法值也不报 422 —— 后端 `normalize_scenario` 一律回落 `v1`，
 * 所以这里不做前端校验（契约第 3.2 节）。
 */
export function createClassroom(
  name: string,
  subject = "cs",
  description?: string,
  scenario?: string,
): Promise<Classroom> {
  const body: Record<string, unknown> = {
    name,
    subject,
    description: description ?? null,
  };
  if (scenario) body.scenario = scenario;

  return request<Classroom>("/classrooms", {
    method: "POST",
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
