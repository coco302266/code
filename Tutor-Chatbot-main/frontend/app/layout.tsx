import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI 智能教室",
  description:
    "面向计算机八股学习的 AI 智能教室：学生提问、AI 按画像个性化讲解、教师生成备课资源。",
};

/**
 * 全局外壳。
 *
 * 与开源原版的三点差别：
 *  - 去掉 next/font/google：静态导出 + 离线客户端不该依赖构建期拉字体
 *  - 去掉 @vercel/analytics：桌面客户端里没有意义
 *  - lang 改 zh-CN，字体走系统中文栈（见 globals.css）
 */
export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN" className="h-full">
      <body className="h-full">{children}</body>
    </html>
  );
}
