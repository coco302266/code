"use client";

import type { ReactNode } from "react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { getHealth } from "@/lib/api";
import { getBackendUrl } from "@/lib/identity";
import { PageBackdrop, type BackdropVariant } from "./PageBackdrop";
import { Tag } from "./ui";

export interface IdentityChip {
  username: string;
  role: "student" | "teacher";
  /**
   * 账号 id（2026-11-02）。顶栏常驻显示 `#12` 这类编号：
   * 老师指定 1v1 学生、学生自查「我是不是被指定的人」都要靠它，
   * 藏在注册成功页里第二天就忘了。
   */
  userId?: number;
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
  backdrop,
  children,
}: {
  title?: ReactNode;
  backHref?: string;
  backLabel?: string;
  identity?: IdentityChip | null;
  right?: ReactNode;
  /** 页面背景图变体，见 components/PageBackdrop.tsx。不传就没有背景图。 */
  backdrop?: BackdropVariant;
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
      ? "bg-ok"
      : db === "down"
        ? "bg-warn"
        : db === "disabled"
          ? "bg-muted"
          : db === "checking"
            ? "bg-idle"
            : "bg-error";

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
    <div className="relative flex h-full flex-col overflow-hidden bg-canvas">
      {backdrop && <PageBackdrop variant={backdrop} />}
      {/* 响应式：小屏下「数据库状态文字」和「身份标签」隐藏（状态灯保留为圆点），
          只留返回 + 标题 + 操作按钮。否则 390px 下这四组元素会把标题挤成竖排单字。 */}
      <header className="relative z-10 flex h-13 shrink-0 items-center gap-3 border-b border-line bg-panel px-3 py-3 sm:px-4">
        {backHref ? (
          <Link
            href={backHref}
            className="shrink-0 rounded-md px-2 py-1 text-[13px] text-sub transition-colors hover:bg-soft hover:text-ink"
          >
            ← {backLabel}
          </Link>
        ) : (
          <span className="shrink-0 text-[14px] font-medium text-ink">
            AI 智能教室
          </span>
        )}

        {title && (
          <>
            <span className="hidden h-4 w-px shrink-0 bg-line sm:block" />
            <span className="min-w-0 flex-1 truncate text-[14px] text-ink">
              {title}
            </span>
          </>
        )}

        <div className="ml-auto flex shrink-0 items-center gap-2.5">
          {right}
          <span
            title={`${dbText} · ${backend}`}
            className="hidden items-center gap-1.5 rounded-md border border-line bg-soft px-2 py-1 text-[12px] text-sub sm:flex"
          >
            <span className={`h-1.5 w-1.5 rounded-full ${dot}`} />
            {dbText}
          </span>
          <span
            title={`${dbText} · ${backend}`}
            className={`h-2 w-2 rounded-full sm:hidden ${dot}`}
          />
          {identity && (
            <span className="hidden sm:inline-flex">
              <Tag tone={identity.role === "teacher" ? "teacher" : "brand"}>
                {identity.username}
                {identity.role === "teacher" ? " · 教师" : " · 学生"}
                {identity.userId ? (
                  <span className="ml-1 font-mono opacity-70">
                    #{identity.userId}
                  </span>
                ) : null}
              </Tag>
            </span>
          )}
        </div>
      </header>
      {/* main 必须自己可滚：根节点是 overflow-hidden，如果这里不给滚动容器，
          内容一旦超过视口就被直接裁掉 —— 「我的课堂」列表变长时滚不动就是这么来的。
          聊天页不受影响：它的内容区是 h-full，再由内部那个 overflow-y-auto 滚动，
          所以在 main 这一层不会产生第二个滚动条。 */}
      <main className="relative z-10 min-h-0 flex-1 overflow-y-auto">
        {children}
      </main>
    </div>
  );
}
