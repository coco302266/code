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
/**
 * 局域网联调：Next 16 的 dev 服务器默认只信任 localhost，别的机器用
 * http://<本机IP>:3000 打开时，/_next 下的开发资源会被判成跨源请求直接拦掉。
 * 表现是页面能打开、样式也在，但点任何按钮都没反应 —— 因为客户端脚本根本没跑起来。
 * 允许的来源从环境变量读，IP 变了只改 .env.local，不动代码。
 */
const devOrigins = (process.env.ALLOWED_DEV_ORIGINS ?? "")
  .split(",")
  .map((s) => s.trim())
  .filter(Boolean);

const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
  reactStrictMode: true,
  // 关掉左下角的 Next.js 开发角标 —— 演示和截图时不该挂着框架 logo。
  // 只在 dev 模式出现，静态构建本来就没有。
  devIndicators: false,
  ...(devOrigins.length > 0 ? { allowedDevOrigins: devOrigins } : {}),
};

export default nextConfig;
