/**
 * AI 智能教室 —— 桌面客户端主进程。
 *
 * 两种运行方式：
 *   1) 开发态（默认）  `npm run client`
 *      —— 加载 http://127.0.0.1:3000；如果本地没起 Next，会自动帮忙拉起来，
 *         这样「双击一下就打开应用」这件事才成立。
 *   2) 静态态          `npm run client:static`
 *      —— 加载 `npm run build` 产出的 out/，用自定义 app:// 协议喂给渲染进程。
 *         这条路不依赖 Node 服务，是以后打包成 exe 的基础。
 *
 * 渲染进程不直连任何外部资源，只跟本机的 FastAPI 后端说话。
 */

const {
  app,
  BrowserWindow,
  Menu,
  protocol,
  net,
  session,
  shell,
  dialog,
} = require("electron");
const path = require("node:path");
const fs = require("node:fs");
const { spawn } = require("node:child_process");
const { pathToFileURL } = require("node:url");

const ROOT = path.join(__dirname, "..");
const OUT_DIR = path.join(ROOT, "out");
const DEV_URL = "http://127.0.0.1:3000";
const DEV_ORIGINS = ["http://127.0.0.1:3000", "http://localhost:3000"];

const USE_STATIC = process.argv.includes("--static");
const NO_SPAWN = process.argv.includes("--no-spawn");
const ENABLE_GPU = process.argv.includes("--enable-gpu");

// ---------------------------------------------------------------------------
// 渲染环境兜底
//
// 界面全是文本和简单 CSS，用不上 GPU；而虚拟机 / 远程桌面 / 受限环境里
// Chromium 的 GPU 进程经常起不来，日志会刷 "GPU process exited unexpectedly"，
// 最后以 FATAL "GPU process isn't usable. Goodbye." 直接把应用带崩。
//
// 下面这组开关是实测能跑通虚拟机环境的组合（必须都在 app ready 之前设置）：
//   --disable-gpu            不启用硬件加速
//   --use-angle=swiftshader  图形后端退回 SwiftShader 软件渲染
//   --disable-gpu-sandbox    GPU 进程不再单独沙箱化（受限环境下起不来）
// 真机上想用回硬件加速，加 --enable-gpu 即可。
// ---------------------------------------------------------------------------
if (!ENABLE_GPU) {
  app.disableHardwareAcceleration();
  app.commandLine.appendSwitch("disable-gpu");
  app.commandLine.appendSwitch("use-angle", "swiftshader");
  app.commandLine.appendSwitch("disable-gpu-sandbox");
}

let mainWindow = null;
let devServer = null;

// app:// 必须在 app ready 之前注册，且要声明为 standard + secure，
// 否则相对路径资源、fetch 与 SSE 都不工作。
protocol.registerSchemesAsPrivileged([
  {
    scheme: "app",
    privileges: {
      standard: true,
      secure: true,
      supportFetchAPI: true,
      corsEnabled: true,
      stream: true,
    },
  },
]);

// ---------------------------------------------------------------------------
// 静态资源协议：把 app://local/xxx 映射到 out/xxx
// ---------------------------------------------------------------------------
function registerStaticProtocol() {
  protocol.handle("app", (request) => {
    const { pathname } = new URL(request.url);
    let rel = decodeURIComponent(pathname);
    if (!rel || rel === "/") rel = "/index.html";

    // 页面路由（没有扩展名的路径）才允许回退到首页；静态资源一律不许回退。
    const looksLikeRoute = !/\.[a-z0-9]+$/i.test(rel);

    // next.config.ts 开了 trailingSlash，所以 /classroom/ 要落到
    // out/classroom/index.html
    const base = path.join(OUT_DIR, rel);
    const candidates = rel.endsWith("/")
      ? [path.join(base, "index.html")]
      : [base, `${base}.html`, path.join(base, "index.html")];

    for (const candidate of candidates) {
      const resolved = path.resolve(candidate);
      // 防目录穿越
      if (!resolved.startsWith(path.resolve(OUT_DIR))) continue;
      if (fs.existsSync(resolved) && fs.statSync(resolved).isFile()) {
        return net.fetch(pathToFileURL(resolved).toString());
      }
    }

    // 关键：静态资源（/_next/ 下的 JS、CSS 等）找不到时必须如实 404，
    // 不能回退到 index.html。否则渲染进程会把一份 HTML 当 JS 去执行，
    // 结果是页面白屏、控制台还没有任何线索 —— 极难排查。
    // 出现这种情况通常意味着 out/ 被重新构建过而客户端还在用旧页面。
    if (!looksLikeRoute) {
      return new Response("Not found", {
        status: 404,
        headers: { "content-type": "text/plain; charset=utf-8" },
      });
    }

    // 页面路由找不到（例如直接访问 /xxx/）就回首页，交给客户端路由决定显示什么
    return net.fetch(pathToFileURL(path.join(OUT_DIR, "index.html")).toString());
  });
}

// ---------------------------------------------------------------------------
// CORS：桌面客户端在静态态下的页面源是 app://local，后端默认只放行
// http://localhost:3000，跨源请求会被浏览器内核拦掉。这里给本机后端的
// 响应补上许可头，避免为了跑客户端去改后端的 .env。
// ---------------------------------------------------------------------------
function patchLocalBackendCors() {
  session.defaultSession.webRequest.onHeadersReceived(
    { urls: ["http://127.0.0.1/*", "http://localhost/*"] },
    (details, callback) => {
      const type = details.resourceType;
      if (type !== "xhr" && type !== "fetch") {
        callback({ responseHeaders: details.responseHeaders });
        return;
      }
      // 别去动本地 Next 开发服务器的响应
      if (DEV_ORIGINS.some((origin) => details.url.startsWith(origin))) {
        callback({ responseHeaders: details.responseHeaders });
        return;
      }

      const headers = { ...(details.responseHeaders || {}) };
      for (const key of Object.keys(headers)) {
        const lower = key.toLowerCase();
        if (
          lower === "access-control-allow-origin" ||
          lower === "access-control-allow-headers" ||
          lower === "access-control-allow-methods" ||
          lower === "access-control-allow-credentials"
        ) {
          delete headers[key];
        }
      }
      headers["Access-Control-Allow-Origin"] = ["*"];
      headers["Access-Control-Allow-Headers"] = ["*"];
      headers["Access-Control-Allow-Methods"] = ["GET,POST,PUT,DELETE,OPTIONS"];
      callback({ responseHeaders: headers });
    },
  );
}

// ---------------------------------------------------------------------------
// 下载：备课生成的 .pptx 直接落到「下载」文件夹，完成后在资源管理器里选中
// ---------------------------------------------------------------------------
function wireDownloads() {
  session.defaultSession.on("will-download", (_event, item) => {
    const target = path.join(app.getPath("downloads"), item.getFilename());
    item.setSavePath(target);
    item.once("done", (_e, state) => {
      if (state === "completed") shell.showItemInFolder(target);
    });
  });
}

// ---------------------------------------------------------------------------
// 开发服务器
// ---------------------------------------------------------------------------
function startDevServer() {
  const npm = process.platform === "win32" ? "npm.cmd" : "npm";
  devServer = spawn(npm, ["run", "dev"], {
    cwd: ROOT,
    shell: process.platform === "win32",
    stdio: "ignore",
    windowsHide: true,
  });
  devServer.on("error", () => {
    devServer = null;
  });
}

async function waitForServer(url, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(url, { method: "GET" });
      if (res.status < 500) return true;
    } catch {
      // 还没起来，继续等
    }
    await new Promise((resolve) => setTimeout(resolve, 400));
  }
  return false;
}

function errorPage(message) {
  const html = `<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<style>
  body{margin:0;height:100vh;display:grid;place-items:center;background:#f4f5f7;
       font-family:"Microsoft YaHei",-apple-system,sans-serif;color:#1f2328}
  .box{max-width:520px;padding:28px 32px;background:#fff;border:1px solid #e3e5e9;border-radius:14px}
  h1{font-size:16px;font-weight:500;margin:0 0 10px}
  p{font-size:13px;line-height:1.8;color:#656d76;margin:0 0 8px}
  code{background:#f4f5f7;padding:1px 5px;border-radius:4px;font-size:12px}
</style>
<div class="box"><h1>客户端没能启动前端界面</h1>
<p>${message}</p>
<p>手动确认一下：在 <code>Tutor-Chatbot-main/frontend</code> 目录里执行 <code>npm install</code>，
然后 <code>npm run dev</code>，再重新打开客户端。</p>
</div></html>`;
  return `data:text/html;charset=utf-8,${encodeURIComponent(html)}`;
}

async function resolveStartUrl() {
  if (USE_STATIC) {
    if (!fs.existsSync(path.join(OUT_DIR, "index.html"))) {
      return {
        url: errorPage(
          "没有找到 <code>out/</code> 目录。请先在 frontend 目录执行 <code>npm run build</code>。",
        ),
      };
    }
    return { url: "app://local/index.html" };
  }

  if (await waitForServer(DEV_URL, 1200)) return { url: DEV_URL };

  if (NO_SPAWN) {
    return {
      url: errorPage(
        "没有检测到 <code>http://127.0.0.1:3000</code>，而本次启动带了 <code>--no-spawn</code>。",
      ),
    };
  }

  startDevServer();
  if (await waitForServer(DEV_URL, 90000)) return { url: DEV_URL };

  return { url: errorPage("自动启动前端服务超时（90 秒）。") };
}

// ---------------------------------------------------------------------------
// 窗口
// ---------------------------------------------------------------------------
function createWindow(startUrl) {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 1080,
    minHeight: 700,
    backgroundColor: "#f4f5f7",
    title: "AI 智能教室",
    show: false,
    autoHideMenuBar: false,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      spellcheck: false,
    },
  });

  mainWindow.once("ready-to-show", () => mainWindow.show());

  // 站内路由照常走，外链交给系统浏览器，别让客户端窗口被导航走
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:/.test(url) && !DEV_ORIGINS.some((o) => url.startsWith(o))) {
      shell.openExternal(url);
      return { action: "deny" };
    }
    return { action: "allow" };
  });

  mainWindow.webContents.on("will-navigate", (event, url) => {
    const ok =
      url.startsWith("app://") ||
      DEV_ORIGINS.some((origin) => url.startsWith(origin));
    if (!ok) {
      event.preventDefault();
      if (/^https?:/.test(url)) shell.openExternal(url);
    }
  });

  mainWindow.on("closed", () => {
    mainWindow = null;
  });

  mainWindow.loadURL(startUrl).catch((err) => {
    mainWindow?.loadURL(errorPage(String(err?.message ?? err)));
  });
}

function buildMenu() {
  const template = [
    { label: "文件", submenu: [{ role: "quit", label: "退出" }] },
    {
      label: "视图",
      submenu: [
        { role: "reload", label: "重新加载" },
        { role: "forceReload", label: "强制重新加载" },
        { type: "separator" },
        { role: "resetZoom", label: "实际大小" },
        { role: "zoomIn", label: "放大" },
        { role: "zoomOut", label: "缩小" },
        { type: "separator" },
        { role: "togglefullscreen", label: "全屏" },
      ],
    },
    {
      label: "窗口",
      submenu: [
        { role: "minimize", label: "最小化" },
        { role: "close", label: "关闭" },
      ],
    },
    {
      label: "帮助",
      submenu: [
        { role: "toggleDevTools", label: "开发者工具" },
        { type: "separator" },
        {
          label: "关于",
          click: () => {
            dialog.showMessageBox({
              type: "info",
              title: "AI 智能教室",
              message: "AI 智能教室 · 桌面客户端",
              detail: [
                `版本：${app.getVersion()}`,
                "后端地址：客户端内可在入口页修改，默认 http://127.0.0.1:8000",
                `界面来源：${USE_STATIC ? "静态导出 (out/)" : DEV_URL}`,
              ].join("\n"),
            });
          },
        },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

// ---------------------------------------------------------------------------
// 生命周期
// ---------------------------------------------------------------------------

// 只允许开一个实例，第二次启动时把已有窗口顶到前面
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });

  app.whenReady().then(async () => {
    if (USE_STATIC) registerStaticProtocol();
    patchLocalBackendCors();
    wireDownloads();
    buildMenu();

    const { url } = await resolveStartUrl();
    createWindow(url);

    app.on("activate", () => {
      if (BrowserWindow.getAllWindows().length === 0) createWindow(url);
    });
  });

  app.on("window-all-closed", () => {
    if (process.platform !== "darwin") app.quit();
  });

  // 退出时把自动拉起来的前端服务一起带走，别留孤儿进程占着 3000 端口
  app.on("before-quit", () => {
    if (devServer && !devServer.killed) {
      try {
        if (process.platform === "win32") {
          spawn("taskkill", ["/pid", String(devServer.pid), "/T", "/F"], {
            windowsHide: true,
          });
        } else {
          devServer.kill("SIGTERM");
        }
      } catch {
        // 收尾失败就算了，不要拦住退出
      }
      devServer = null;
    }
  });
}
