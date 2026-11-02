/**
 * 客户端本地状态。
 *
 * 2026-11-02 起后端有了注册/登录（用户名 + 密码，`POST /users`、`POST /auth/login`），
 * 但**仍未签发会话 token**：登录只用来核验口令并取回账号 id，之后的每个请求
 * 依旧靠请求体/查询参数里的 `user_id` 自证身份。也就是说这里存的身份仍是
 * 客户端的「自述」，不是服务端认证过的凭证——内网教学场景够用，
 * 但对公网暴露前必须补 token（见 HANDOFF 第六节）。
 */

import type { UserRole } from "./types";

const IDENTITY_KEY = "ai-classroom/identity";
const BACKEND_KEY = "ai-classroom/backend-url";
const RECENT_KEY = "ai-classroom/recent-classrooms";

// 构建期注入：静态导出时 NEXT_PUBLIC_BACKEND_URL 会被写死进产物。
// 容器部署用相对路径 /api（同源，由 nginx 反代到后端），避免远程用户连到自己的 127.0.0.1。
// 未设置时保持本地开发的默认值，行为不变。
export const DEFAULT_BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL || "http://127.0.0.1:8000";

export interface Identity {
  userId: number;
  username: string;
  role: UserRole;
  classroomId: number | null;
  classroomName?: string;
}

export interface RecentClassroom {
  id: number;
  name: string;
  role: UserRole;
}

/** SSR / 静态导出的构建期没有 window，所有读写都必须先过这一关。 */
function hasStorage(): boolean {
  return typeof window !== "undefined" && !!window.localStorage;
}

export function loadIdentity(): Identity | null {
  if (!hasStorage()) return null;
  try {
    const raw = window.localStorage.getItem(IDENTITY_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Identity;
    if (typeof parsed?.userId !== "number") return null;
    return parsed;
  } catch {
    return null;
  }
}

export function saveIdentity(identity: Identity): void {
  if (!hasStorage()) return;
  window.localStorage.setItem(IDENTITY_KEY, JSON.stringify(identity));
}

export function clearIdentity(): void {
  if (!hasStorage()) return;
  window.localStorage.removeItem(IDENTITY_KEY);
}

export function getBackendUrl(): string {
  if (!hasStorage()) return DEFAULT_BACKEND_URL;
  return window.localStorage.getItem(BACKEND_KEY) || DEFAULT_BACKEND_URL;
}

export function saveBackendUrl(url: string): void {
  if (!hasStorage()) return;
  const trimmed = url.trim().replace(/\/+$/, "");
  window.localStorage.setItem(BACKEND_KEY, trimmed || DEFAULT_BACKEND_URL);
}

/** 后端没有「列出课堂」的接口，所以最近进过的课堂由客户端自己记。 */
export function loadRecentClassrooms(): RecentClassroom[] {
  if (!hasStorage()) return [];
  try {
    const raw = window.localStorage.getItem(RECENT_KEY);
    return raw ? (JSON.parse(raw) as RecentClassroom[]) : [];
  } catch {
    return [];
  }
}

export function rememberClassroom(entry: RecentClassroom): void {
  if (!hasStorage()) return;
  const list = loadRecentClassrooms().filter((item) => item.id !== entry.id);
  list.unshift(entry);
  window.localStorage.setItem(RECENT_KEY, JSON.stringify(list.slice(0, 12)));
}
