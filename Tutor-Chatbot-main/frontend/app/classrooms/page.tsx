"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { addMember, ApiError, getClassroom, listClassrooms } from "@/lib/api";
import {
  loadIdentity,
  loadRecentClassrooms,
  rememberClassroom,
  saveIdentity,
  type Identity,
  type RecentClassroom,
} from "@/lib/identity";
import {
  Banner,
  Btn,
  Card,
  Empty,
  Field,
  Input,
  Spinner,
  Tag,
} from "@/components/ui";
import { Shell } from "@/components/Shell";

/**
 * 页面 2／4：我的课堂。
 *
 * 数据源是 `GET /classrooms?user_id=N`（M1 补的），比本机记录权威：
 * 换设备、清缓存都不丢，且返回的 role 是**房间内**角色，能正确处理
 * 「同一个人既是 A 房老师又是 B 房学生」。
 *
 * 本机 localStorage 仍然保留，但降级为**兜底**：后端连不上、或该用户
 * 在后端没有任何成员关系时，才拿它兜一下，避免整屏空白。
 * 两边都有时以后端为准并用后端角色覆盖本地角色（见 refresh）。
 */
export default function ClassroomsPage() {
  const router = useRouter();
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [recent, setRecent] = useState<RecentClassroom[]>([]);
  const [classroomId, setClassroomId] = useState("1");
  const [busy, setBusy] = useState<number | "manual" | null>(null);
  const [error, setError] = useState("");
  /** null = 还没拉过；true = 这次是后端给的；false = 后端没数据，用了本机兜底 */
  const [fromServer, setFromServer] = useState<boolean | null>(null);

  const refresh = useCallback(async () => {
    const saved = loadIdentity();
    setIdentity(saved);
    const local = loadRecentClassrooms();

    if (!saved?.userId) {
      setRecent(local);
      setFromServer(false);
      return;
    }

    try {
      const data = await listClassrooms(saved.userId);
      if (data.classrooms.length > 0) {
        // 以后端为准，并用房间内角色覆盖本地记的全局角色
        setRecent(
          data.classrooms.map((room) => ({
            id: room.id,
            name: room.name,
            role: room.role,
          })),
        );
        setFromServer(true);
        return;
      }
      // 后端说「你还没加入任何课堂」—— 这一屏就退回本机记录，
      // 让用户至少还能看到/回到最近进过的房间（多为老数据或本机残留）
      setRecent(local);
      setFromServer(false);
    } catch {
      // 后端不可用：静默回退本机，不让列表页变成报错页
      setRecent(local);
      setFromServer(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function join(id: number, source: number | "manual") {
    if (!identity) {
      router.push("/");
      return;
    }
    setBusy(source);
    setError("");
    try {
      await addMember(id, identity.userId, identity.role);
      const room = await getClassroom(id);
      saveIdentity({
        ...identity,
        classroomId: room.id,
        classroomName: room.name,
      });
      rememberClassroom({ id: room.id, name: room.name, role: identity.role });
      router.push(identity.role === "teacher" ? "/teacher/" : "/classroom/");
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        setError(`课堂 ${id} 不存在。请确认课堂号，或让教师先创建课堂。`);
      } else {
        setError(err instanceof Error ? err.message : "加入失败");
      }
    } finally {
      setBusy(null);
    }
  }

  return (
    <Shell backHref="/" backLabel="入口" identity={identity} title="我的课堂">
      <div className="mx-auto max-w-[960px] px-6 py-8">
        <h1 className="text-[18px] font-medium text-ink">我的课堂</h1>
        <p className="mt-1 text-[13px] text-sub">
          选一个课堂进去，或者输入教师给你的课堂号。
        </p>

        {!identity && (
          <Banner tone="warn" className="mt-5">
            还没有身份。请先回到
            <button
              onClick={() => router.push("/")}
              className="mx-1 text-brand underline-offset-2 hover:underline"
            >
              入口页
            </button>
            填昵称和身份。
          </Banner>
        )}

        {error && (
          <Banner tone="error" className="mt-5">
            {error}
          </Banner>
        )}

        <Card className="mt-6 p-5">
          <Field label="用课堂号进入" hint="教师创建课堂时会拿到课堂号">
            <div className="flex gap-2">
              <Input
                value={classroomId}
                onChange={(e) => setClassroomId(e.target.value)}
                inputMode="numeric"
                placeholder="1"
              />
              <Btn
                variant="primary"
                disabled={busy !== null || !identity}
                onClick={() => {
                  const id = Number(classroomId);
                  if (!Number.isInteger(id) || id <= 0) {
                    setError("课堂号要填正整数。");
                    return;
                  }
                  void join(id, "manual");
                }}
              >
                {busy === "manual" && <Spinner />}
                进入
              </Btn>
            </div>
          </Field>
        </Card>

        <h2 className="mt-8 text-[14px] font-medium text-ink">
          最近进过的课堂
        </h2>
        <p className="mt-1 text-[12px] text-hint">这份列表只保存在本机。</p>

        <div className="mt-3 space-y-2.5">
          {recent.length === 0 ? (
            <Card className="p-8">
              <Empty>
                还没有记录。用上面的课堂号进去一次，这里就会有记录。
              </Empty>
            </Card>
          ) : (
            recent.map((room) => (
              <Card key={room.id} className="flex items-center gap-3 px-4 py-3.5">
                <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-brand-soft text-[14px] font-medium text-brand">
                  {room.id}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[14px] font-medium text-ink">
                    {room.name}
                  </span>
                  <span className="mt-0.5 block text-[12px] text-hint">
                    课堂号 {room.id}
                  </span>
                </span>
                <Tag tone={room.role === "teacher" ? "teacher" : "brand"}>
                  {room.role === "teacher" ? "教师" : "学生"}
                </Tag>
                <Btn
                  variant="subtle"
                  disabled={busy !== null || !identity}
                  onClick={() => void join(room.id, room.id)}
                >
                  {busy === room.id && <Spinner />}
                  进入
                </Btn>
              </Card>
            ))
          )}
        </div>
      </div>
    </Shell>
  );
}
