import type { NextConfig } from "next";

/**
 * 桌面客户端用 `next build` 产出静态站点（out/），由 Electron 直接加载。
 *
 * - output: "export"      —— 纯客户端渲染，不需要 Node 服务，Electron 才装得下
 * - trailingSlash: true   —— 生成 classrooms/index.html 这种目录形式，
 *                            自定义协议下相对路径才解析得对
 * - images.unoptimized    —— 静态导出没有图片优化服务，必须关掉
 *
 * 为此本项目的路由**全部是静态路由**（不带 [id] 动态段），
 * 课堂 id 走 localStorage，避免 generateStaticParams 的麻烦。
 */
const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
  reactStrictMode: true,
  // 关掉左下角的 Next.js 开发角标 —— 演示和截图时不该挂着框架 logo。
  // 只在 dev 模式出现，静态构建本来就没有。
  devIndicators: false,
};

export default nextConfig;
