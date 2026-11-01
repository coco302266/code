# AI Classroom 前端设计说明（C 部分）

> 面向作业分工中 C（系统集成与前端交互）的设计依据。
> 接口依据：`API_CONTRACT.md`（后端已冻结，16 个接口 + SSE 事件表）。
> 变更记录：`C_WORKLOG.md`。运行方式：`Tutor-Chatbot-main/frontend/README.md`。

---

## 1. 一句话

后端已经把「建用户 / 建课堂 / 聊天 / 画像 / 备课」全部做完了，
前端要做的是**把这 16 个接口组织成四个界面**：一个入口、一个课堂列表、一个聊天室、一个教师工作台。
技术栈不新选，沿用仓库里已有的 Next.js + TypeScript + Tailwind。

---

## 2. 形态与页面

**桌面客户端（Electron 外壳 + Next.js 静态导出）**，四个静态路由：

```
/                      入口页（输入昵称 + 选身份）→ 分流
│
├─ 学生端
│   ├─ /classrooms              我的课堂（最近进过 + 用课堂号进入）
│   └─ /classroom               聊天室（群聊 + 流式讲解 + 右侧成员/画像）
│
└─ 教师端
    └─ /teacher                 教师工作台（课堂 / 备课 / 记录 三个页签）
```

**为什么只有 4 个页面**：画像与成员做成聊天室右侧抽屉、备课与记录做成工作台页签，
把页面数从 6 压到 4。演示时一页就能讲完「提问 → 个性化回答 → 画像变化」这条主线。

**为什么路由全部是静态的**（不带 `[id]` 动态段）：
`output: "export"` 导出时动态段需要 `generateStaticParams`，且 Electron 的自定义协议
对嵌套路径的解析更容易出错。课堂 id 走 localStorage，代价是「我的课堂」列表只能记在本机
（后端也没有「列出课堂」接口）。

---

## 3. 接口 → 界面映射

| 模块 | 前端做什么 | 调用接口 |
| --- | --- | --- |
| 进入前 | 建用户 · 加课堂成员 | `POST /users`、`POST /classrooms/{id}/members` |
| 聊天室 | 拉历史 · 提问（SSE 流式） | `GET /classrooms/{id}/messages`、`POST /chat` |
| 画像侧栏 | 读画像 · 成员列表 | `GET /students/{id}/profile?classroom_id=`、`GET /classrooms/{id}/members` |
| 教师工作台 | 建课堂 · 生成大纲 · 下载 PPT · 课堂记录 | `POST /classrooms`、`POST /resources/ppt-outline`、`GET /resources/files/{name}` |

未使用的接口：`PUT /students/{id}/profile`（调试用）、`GET /behaviors`、
`GET /prompt-preview`（前端暂未接，调提示词时可直接用 Swagger）、`POST /upload`（P2）。

---

## 4. 关键技术点

### 4.1 身份态（后端没有鉴权）

后端不校验身份，前端是唯一的「身份真相源」：

```ts
// localStorage: ai-classroom/identity
{ userId: 3, username: "张三", role: "student", classroomId: 1 }
```

登录 = 输入昵称 + 选身份 → `POST /users`（幂等）→ 落 localStorage。
**权限判定看课堂内 role，不是 users.role**（契约第 7 条）。

### 4.2 SSE 解析（严格按契约）

```ts
buffer += decoder.decode(value, { stream: true });
const frames = buffer.split("\n\n");     // 必须 \n\n，不是 \n
buffer = frames.pop() ?? "";
```

四类事件分别处理：`sources`（首个，带资料切片）、`token`、`state`（可能没有）、`error`。
未知 `type` 静默忽略 —— 后端先加事件、前端后接是安全的。

### 4.3 请求体白名单

`ChatRequest` 开了 `extra="forbid"`，**多传一个字段直接 422**。
所以 `/chat` 只传三个字段：`message` / `classroom_id` / `user_id`。
走库时服务端自己恢复历史与教学状态，前端**不应**维护 `topic` / `hint_level`。

### 4.4 群聊「实时」

契约没有 WebSocket。方案：**发送后立即拉一次 + 5 秒轮询** `GET /messages`。
成本低、演示效果够；若后端后续加 SSE 广播端点，只需替换这一处。

### 4.5 客户端外壳

| 能力 | 做法 |
| --- | --- |
| 加载静态导出 | 自定义 `app://` 协议映射到 `out/`；`/_next/` 等静态资源缺失时**如实 404**（不回退 index.html，否则白屏无提示） |
| 静态态跨源 | 主进程给本机后端的 XHR/fetch 响应补 CORS 头，**不改后端 `.env`** |
| 下载 PPT | `will-download` 自动落「下载」文件夹并选中文件 |
| 虚拟机 GPU 崩溃 | 默认 `disableHardwareAcceleration` + `--use-angle=swiftshader`；`--enable-gpu` 可恢复 |

---

## 5. 已知边界（写报告时要说明）

| 边界 | 说明 |
| --- | --- |
| 多场景不支持 | 作业要求 1v1 / 小组讨论 / 辩论对抗，后端 `/chat` 是固定流水线、无 router 与角色参数。前端目前只渲染「1v1 讲解 + 群聊共享上下文」 |
| sources 不落库 | 「依据」只在流式那一刻存在，刷新后消失（后端未存该字段） |
| 无鉴权 | 身份存本机 localStorage，换机器就丢；这是后端现状，不是前端设计选择 |
| 课堂列表在本机 | 后端无 `GET /classrooms`，见第 2 节 |

---

## 6. 待 A 拍板

1. `/chat` 是否加 `scenario` 参数、`token` 事件是否带 `agent_role` —— 决定前端能否做多角色对话流。
2. `messages` 表是否存 `sources` —— 决定「依据」能否在刷新后保留。
3. 是否补 `GET /classrooms`（按 user 过滤）—— 决定「我的课堂」能否跨机器。

---

## 7. MVP 范围与验收清单

> 用户 2026-10-21 明确：**先把最小可执行的前端做出来，新功能（含鉴权）后面再加。**
> 本节就是那条线 —— 在第 1 节范围内的都做完了，第 2 节的一律没碰。

### 7.1 本期做（已落地）

| 能力 | 落点 | 状态 |
| --- | --- | --- |
| 身份（昵称 + 角色，存本机） | `app/page.tsx` + `lib/identity.ts` | ✅ |
| 学生加入课堂 | 入口页 → `POST /members` | ✅ |
| 看历史聊天记录 | `/classroom` → `GET /messages` | ✅ |
| 流式提问（含 sources / state 例外 / error） | `lib/chat.ts` → `POST /chat` | ✅ |
| 看自己的学习画像 | `components/ProfileView.tsx` → `GET /profile` | ✅ |
| 群聊同步（同学发言能看见） | `/classroom` 5 秒轮询 | ✅ |
| 教师建课堂（自动以教师身份加入） | `/teacher`「课堂」页签 | ✅ |
| 教师备课：大纲 + 下载 .pptx | `/teacher`「备课」页签 | ✅ |
| 教师看名单 / 学生画像 / 全量记录 | `/teacher`「课堂」「记录」页签 | ✅ |
| 后端状态灯 + 中文错误提示 | `components/Shell.tsx` + `lib/api.ts` | ✅ |
| 桌面客户端外壳（开发态 / 静态态） | `electron/main.js` | ✅ |

### 7.2 本期不做（后续加时动哪里）

| 功能 | 为什么现在不做 | 后续要动哪里 |
| --- | --- | --- |
| **登录鉴权** | 后端 16 个接口**没有鉴权**，前端造不出来；硬做会变成"假登录" | ① 后端加 `/auth/*` 与 token 校验；② `lib/api.ts` 的 `request()` 统一带 `Authorization`；③ `lib/identity.ts` 改存 token，不再存裸 `userId`；④ 各页面启动时校验登录态（现在只读 localStorage）；⑤ SSE 用 fetch 发请求，`lib/chat.ts` 也要同步加头 |
| PDF 上传问答 | 契约里标为可选（3 次/小时，内存会话 1 小时过期） | `lib/api.ts` 加 `POST /upload`；聊天室加附件按钮，`session_id` 随 `/chat` 透传 |
| 行为流水 / 讲法预览 | 调试用，学生界面不需要 | 教师工作台加「行为时间线」页签；`GET /behaviors`、`GET /prompt-preview` **已在 `lib/api.ts` 封装好**，直接调即可 |
| 多场景（1v1 / 小组讨论 / 辩论对抗） | 后端 `/chat` 是固定流水线，无 router、无角色参数 | 见第 6 节第 1 条：需 A 先加 `scenario` + `agent_role` |
| 打包成安装包 | 演示只需本机打开窗口 | 加 `electron-builder`；`out/` 与 `electron/` 的结构已经具备条件 |

### 7.3 一条原则

加新功能时**先看 `API_CONTRACT.md`**。契约里没有的能力（鉴权就是典型），
先找 A 定接口，再动前端 —— 否则前端会先长出一套跟后端对不上的"影子协议"，
这正是本项目一开始踩过的坑（原版前端传 `provider=groq` 而后端只认 `openai`）。
