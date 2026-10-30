# AI Classroom 桌面客户端（C 部分）

面向计算机八股学习的 AI 智能教室 —— Electron 桌面客户端。
本目录基于开源项目 Tutor-Chatbot 的 Next.js 前端二次开发，已整体改造为中文八股教学场景。

接口依据：仓库根目录的 `docs/specs/API_CONTRACT.md`（16 个接口 + SSE 事件表）。
变更记录：仓库根目录的 `docs/worklogs/C_WORKLOG.md`。设计说明：`docs/plans/PLANS.md`。

---

## 四个页面

| # | 路由 | 谁用 | 做什么 |
|---|---|---|---|
| 1 | `/` | 双方 | 输入昵称、选身份、填课堂号；顶部有后端连接状态 |
| 2 | `/classrooms/` | 学生 | 我的课堂（本机记录 + 用课堂号进入） |
| 3 | `/classroom/` | 双方 | 聊天室：群聊 + 流式讲解 + 资料依据 + 右侧成员/画像侧栏 |
| 4 | `/teacher/` | 教师 | 工作台：课堂 / 备课 / 记录 三个页签 |

> 后端没有「列出课堂」的接口，所以第 2 页的列表是客户端自己记的「最近进过的课堂」。
> 这是刻意的取舍 —— 不为一个列表去动已冻结的后端。

---

## 怎么跑

### 前置：先把后端起起来

```bash
cd ../backend
venv\Scripts\python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

打开 http://127.0.0.1:8000/health 看到 `database` 状态即正常。
后端起不来时客户端仍能打开，但顶部状态灯会是红的。

### 一、桌面客户端（推荐，演示用这个）

```bash
npm install

# 开发态：自动拉起前端服务 + 打开应用窗口
npm run client

# 静态态：先用 next build 产出 out/，再由客户端直接加载
npm run build
npm run client:static
```

也可以直接双击本目录下的 `start-client.bat`，效果同 `npm run client`。

`npm run client` 会先探测 `127.0.0.1:3000`，没探测到就自动执行 `npm run dev`，
等端口就绪后打开窗口；关窗口时会把拉起来的服务一起收掉。
不想要这个行为就加参数：`npx electron . --no-spawn`。

### 二、浏览器里调试（改样式时更快）

```bash
npm run dev
# 打开 http://localhost:3000
```

必须用 `localhost` 而不是 `127.0.0.1` —— 后端 `ALLOWED_ORIGINS` 里只有
`http://localhost:3000`。浏览器能跑说明代码没问题，这也是排查
「是界面问题还是客户端外壳问题」的快捷手段。

---

## 目录结构

```text
app/
  page.tsx              页面 1：入口（昵称 / 身份 / 后端地址 / 健康检查）
  classrooms/page.tsx   页面 2：我的课堂
  classroom/page.tsx    页面 3：聊天室（SSE 流式 + 5 秒轮询 + 画像侧栏）
  teacher/page.tsx      页面 4：教师工作台（课堂 / 备课 / 记录）
  layout.tsx            全局外壳（中文 metadata，无 next/font、无 analytics）
  globals.css           设计令牌（浅色）与全局样式
components/
  Shell.tsx             顶栏 + 后端状态灯 + 身份标签
  chat.tsx              消息行、头像、资料依据（群聊样式：全部左对齐）
  ProfileView.tsx       学生画像（掌握度条 / 薄弱点 / 学习偏好）
  ui.tsx                按钮、输入框、标签、横幅等基元
lib/
  api.ts                REST 封装 + 错误码 → 中文文案
  chat.ts               SSE 流式解析（按 \n\n 分帧，含 sources 事件）
  identity.ts           localStorage 身份态与后端地址
  types.ts              与 docs/specs/API_CONTRACT.md 对应的数据类型
electron/
  main.js               客户端主进程（开发态 / 静态态 / CORS / 下载）
```

---

## 与后端对接时最容易踩的七条

这些都在代码里处理过了，改代码时别改回去：

1. **`provider` 不要传。** 后端默认指向 DeepSeek 兼容端点，传 `groq`/`gemini` 会 503。
   （开源原版就是传了 `groq`，所以它在改造前连不上本项目的后端。）
2. **SSE 必须按 `\n\n` 分帧**，不是 `\n`。按单行切会丢字。
3. **错误在事件体里**：回答中途出错走 `type=error`，这时 HTTP 状态码已经是 200。
   不处理的话模型挂掉界面会一直转圈。
4. **`state` 不是必然出现的事件**。纯寒暄 / 未识别知识点时后端只发 `token` + `[DONE]`。
   收不到就保持上一轮状态，**不要清空**。
5. **`sources` 是第一个事件**，带本轮引用了哪几片资料。`items: []` 表示"明确没用资料"，
   收不到事件表示"这条流没覆盖"，两者不能混。
6. **`classroom_id` + `user_id` 必须成对传**，缺一个就静默不落库，
   表现是「刷新页面聊天记录全没」。
7. **请求体不能多字段**（后端 `extra="forbid"`），多传一个直接 422。
   所以 `/chat` 只传 `message` / `classroom_id` / `user_id` 三个。

另外三条只在调数据时用得到：

- **创建课堂不会自动把创建者加为成员**。教师必须再显式
  `POST /classrooms/{id}/members` 且 `role="teacher"`，否则备课接口 403。
- `GET /students/{id}/profile`：带 `classroom_id` 返回单个对象，不带返回列表包装。
  本客户端只走「带参」这一种用法。
- 限流按**客户端 IP** 计：`/chat` 10 次/分、备课 5 次/分。连着点会撞 429，界面已给中文提示。

---

## 客户端的两个实现决定

**CORS 在客户端侧解决，不动后端的 `.env`。**
静态态下页面源是 `app://local`，后端默认只放行 `http://localhost:3000`。
`electron/main.js` 里给本机后端的 XHR/fetch 响应补上了许可头，
所以不用为了跑客户端去改后端配置。

**下载的 .pptx 自动落到「下载」文件夹**，完成后在资源管理器里选中，
不回弹保存对话框 —— 演示时少一步操作。

---

## 排障

**客户端双击后一闪就没 / 报 `GPU process isn't usable`。**
虚拟机、远程桌面或受限环境里 Chromium 的 GPU 进程起不来。
`electron/main.js` 已经默认关掉硬件加速并退回 SwiftShader 软件渲染，
正常不会遇到。真机上想用回硬件加速：`npx electron . --enable-gpu`。

**报 `Cannot read properties of undefined (reading 'registerSchemesAsPrivileged')`。**
环境里有 `ELECTRON_RUN_AS_NODE=1`，Electron 被降级成纯 Node 在跑。
在干净的终端里不会有；清掉再启动：

```bash
# Git Bash
env -u ELECTRON_RUN_AS_NODE npm run client
```

**`npm install` 之后 `electron` 用不了（提示找不到二进制）。**
Electron 的二进制有时不会随安装一起下载，手动补一次：

```bash
ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/ \
  node node_modules/electron/install.js
```

**页面白屏，控制台报 `_next/... 404`。**
`out/` 被重新构建过，而客户端还在用旧页面（资源哈希对不上）。
**关掉客户端 → 重新 `npm run build` → 再打开**。协议层已改为静态资源如实 404，
这类问题会在控制台直接报出来，不会再静默白屏。

---

## 当前边界（对照作业要求，写报告时要说明）

- 本目录是**四个页面**的最小可执行版本，先跑通主链路。
- 作业要求的「多场景（1v1 辅导 / 小组讨论 / 辩论对抗）」与
  「≥4 类角色 Agent 协同」**后端目前不支持**：`/chat` 是固定流水线，
  没有 router、没有角色参数，消息角色只有 `student` / `assistant` / `teacher`。
  所以界面现在渲染的是「1v1 讲解 + 群聊共享上下文」。
- 没有登录鉴权，身份存在本机 localStorage（后端本身也不校验身份）。
- `sources` 只存在于流式那一刻，落库消息不含该字段，所以**刷新后「依据」会消失**。
  这是后端没存 sources 的必然结果，不是前端 bug。
