"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

/**
 * `/chat` 的兼容入口。
 *
 * 这个路由原本挂着一套独立的深色 UI（`app/components/TutorChat.tsx`，zinc 主题、
 * 气泡靠右、只认 groq/gemini），与主干的四页业务流完全不是一套设计语言，
 * 而且没有任何页面链到它 —— 属于历史遗留。
 *
 * 处理方式是「保留 URL、换掉实现」：
 *   - 组员或书签里可能还留着 /chat，直接 404 会让人以为坏了；
 *   - 所以这里只做一次客户端跳转到 /chatroom 对应页（/classroom/），
 *     不渲染任何旧界面。
 *
 * 为什么用客户端跳转而不是服务端 redirect()：`next.config.ts` 是
 * `output: "export"` 静态导出，构建期没有 Node 运行时，redirect() 不生效；
 * 静态导出下这份产物就是一个带跳转脚本的 HTML，Electron 里同样能跳。
 */
export default function LegacyChatRedirectPage() {
  const router = useRouter();

  useEffect(() => {
    router.replace("/classroom/");
  }, [router]);

  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 px-6">
      <p className="text-[14px] text-sub">正在进入课堂…</p>
      <p className="text-[13px] text-hint">
        如果没有自动跳转，
        <Link
          href="/classroom/"
          className="mx-1 text-brand underline-offset-2 hover:underline"
        >
          点这里
        </Link>
      </p>
    </div>
  );
}
