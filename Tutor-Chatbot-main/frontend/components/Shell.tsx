"use client";

import type { ReactNode } from "react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { getHealth } from "@/lib/api";
import { getBackendUrl } from "@/lib/identity";
import { Tag } from "./ui";

export interface IdentityChip {
  username: string;
  role: "student" | "teacher";
}

/**
 * 客户端外壳：固定顶栏 + 内容区。
 *
 * 顶栏右侧常驻一个后端状态灯 —— 桌面客户端最容易出现的问题就是
 * 「后端没起」，一眼能看到就不用翻控制台。
 */
export function Shell({
  title,
  backHref,
  backLabel = "返回",
  identity,
  right,
  children,
}: {
  title?: ReactNode;
  backHref?: string;
  backLabel?: string;
  identity?: IdentityChip | null;
  right?: ReactNode;
  children: ReactNode;
}) {
  const [db, setDb] = useState<string>("checking");
  // localStorage 在构建期读不到，放到 effect 里取，避免服务端/客户端渲染不一致
  const [backend, setBackend] = useState("");

  useEffect(() => {
    setBackend(getBackendUrl());
  }, []);

  useEffect(() => {
    let alive = true;
    getHealth()
      .then((h) => alive && setDb(h.database))
      .catch(() => alive && setDb("unreachable"));
    return () => {
      alive = false;
    };
  }, []);

  const dot =
    db === "up"
      ? "bg-[#12a06b]"
      : db === "down"
        ? "bg-[#d99a1f]"
        : db === "disabled"
          ? "bg-[#9aa2ab]"
          : db === "checking"
            ? "bg-[#c3c8cf]"
            : "bg-[#d0453b]";

  const dbText =
    db === "up"
      ? "记录已落库"
      : db === "down"
        ? "数据库连不上"
        : db === "disabled"
          ? "未配数据库"
          : db === "checking"
            ? "检查中"
            : "后端未启动";

  return (
    <div className="flex h-full flex-col bg-canvas">
      <header className="flex h-13 shrink-0 items-center gap-3 border-b border-line bg-panel px-4 py-3">
        {backHref ? (
          <Link
            href={backHref}
            className="rounded-md px-2 py-1 text-[12.5px] text-sub transition-colors hover:bg-soft hover:text-ink"
          >
            ← {backLabel}
          </Link>
        ) : (
          <span className="text-[13px] font-medium text-ink">AI 智能教室</span>
        )}

        {title && (
          <>
            <span className="h-4 w-px bg-line" />
            <span className="truncate text-[13px] text-ink">{title}</span>
          </>
        )}

        <div className="ml-auto flex items-center gap-2.5">
          {right}
          <span
            title={`${dbText} · ${backend}`}
            className="flex items-center gap-1.5 rounded-md border border-line bg-soft px-2 py-1 text-[11.5px] text-sub"
          >
            <span className={`h-1.5 w-1.5 rounded-full ${dot}`} />
            {dbText}
          </span>
          {identity && (
            <Tag tone={identity.role === "teacher" ? "teacher" : "brand"}>
              {identity.username}
              {identity.role === "teacher" ? " · 教师" : " · 学生"}
            </Tag>
          )}
        </div>
      </header>
      <main className="min-h-0 flex-1">{children}</main>
    </div>
  );
}
