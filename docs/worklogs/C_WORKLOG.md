# C 工作记录（前端）

> **owner**：C
> **给谁看**：A（后端 / 协议）、B（Prompt / RAG）
> **原则**：日常进度写在本文件；A/B 各自负责的文档默认不改。

本文件是 C 的变更日记，方便回溯和同步，**不是**新的接口契约或模型协议。

---

## 怎么记（给 A / B 和以后的自己）

| 改了什么 | 写哪里 | 要不要动原文档 |
| --- | --- | --- |
| 前端页面 / 路由 / 组件 / 交互 | **本文件** | 否 |
| 前端要用到的新接口字段、新 SSE 事件 | 先读 `docs/specs/API_CONTRACT.md`；契约没写就先问 A | 要，且由 A 改契约 |
| A/B 负责的代码（backend、knowledge、prompt） | **不要动**，需要就提 | — |

前端落点固定为 `Tutor-Chatbot-main/frontend/`。本文件只记重要改动，
不写排查过程的来龙去脉 —— 写清「改了什么 / 有没有行为变更 / 怎么回退」即可。

---

## 2026-10-21 前端教室改造：四页面 + 桌面客户端

### 一句话

把开源原版的「英文 CS Tutor」改造成「中文八股 AI 教室」，四个页面 + 一层 Electron 桌面外壳。
接口全部按 `docs/specs/API_CONTRACT.md` 对接，**后端、知识库、契约一个字没改**。

### 起因

1. 用户拍板：前端做成**桌面客户端**；页面先压成 **4 个**（最小可执行）。
2. 原版前端其实**跑不通本项目后端**：
   - 默认在请求体里发 `provider=groq` → 契约第 11 条明确说非 `openai` 会 **503**；
   - SSE 用 `buffer.split("\n")` 分帧，契约要求按 `\n\n`，跨 TCP 分片时会丢字。
3. 原版还有四处与本项目无关或语义不符：emailjs 上报、知情同意弹窗、
   把 `hint_level` 当「难度 Analogy/Hint/Revealing」展示、全英文文案。

### 对 XB-1 的处置（B 越界改的前端部分）

- **意图全部保留**：DeepSeek 通道、八股示例题、中文空状态文案。
- **实现方式被取代**：新前端**不传 `provider` 字段**（后端默认即 DeepSeek 兼容通道），
  比"传 `openai`"更贴合契约第 11 条，也不依赖后端通道名的存在性。
- `app/components/TutorChat.tsx`、`app/chat/page.tsx` 已删除 →
  **D6 随之消解**（前端不再展示 `hint_level`，不存在"文案与四层深度不符"的问题）。

### 改了哪些文件

**新增**

| 文件 | 作用 |
| --- | --- |
| `frontend/lib/types.ts` | 与契约逐字对应的数据类型（含 `SourceItem`） |
| `frontend/lib/identity.ts` | localStorage 身份态、后端地址、最近课堂 |
| `frontend/lib/api.ts` | REST 封装 + 错误码 → 中文文案 |
| `frontend/lib/chat.ts` | SSE 解析（`\n\n` 分帧、sources / token / state / error） |
| `frontend/components/ui.tsx` | 按钮、输入框、标签、横幅等基元 |
| `frontend/components/Shell.tsx` | 顶栏 + 后端连接状态灯 + 身份标签 |
| `frontend/components/chat.tsx` | 消息行 / 头像 / 资料依据 |
| `frontend/components/ProfileView.tsx` | 学生画像（掌握度条、薄弱点） |
| `frontend/app/classrooms/page.tsx` | 页面 2：我的课堂 |
| `frontend/app/classroom/page.tsx` | 页面 3：聊天室 |
| `frontend/app/teacher/page.tsx` | 页面 4：教师工作台 |
| `frontend/electron/main.js` | 客户端主进程（`app://` 协议 / CORS / 下载 / 单实例） |
| `frontend/start-client.bat` | 双击启动器 |
| `docs/plans/PLANS.md` | 前端设计说明（页面、接口映射、边界） |

**修改**

| 文件 | 改了什么 |
| --- | --- |
| `frontend/package.json` | 包名 → `ai-classroom-client`；加 `main`；**删** `@emailjs/browser`、`@vercel/analytics`；**加** `electron`；加 `client` / `client:static` 脚本 |
| `frontend/next.config.ts` | 空配置 → `output:"export"` + `trailingSlash` + `images.unoptimized` + `devIndicators:false` |
| `frontend/app/layout.tsx` | 去掉 `next/font/google` 与 `@vercel/analytics`；`lang` 改 `zh-CN`；metadata 改中文 |
| `frontend/app/globals.css` | 默认模板 → 浅色设计令牌 + 中文系统字体栈 |
| `frontend/app/page.tsx` | 英文落地页 → 入口页（昵称 / 身份 / 课堂号 / 后端地址 / 健康检查） |
| `frontend/README.md` | `create-next-app` 模板 → 客户端使用说明 + 排障 |

**删除**

| 文件 | 原因 |
| --- | --- |
| `app/chat/page.tsx` | 被 `/classroom` 取代 |
| `app/components/TutorChat.tsx` | 上述 503 + 丢字两个硬伤，逻辑重写进 `lib/chat.ts` |
| `components/WelcomeModal.tsx`、`lib/consent.ts` | 原版「知情同意」弹窗，与本项目无关 |

> `public/*.svg` 与 `CLAUDE.md` 此前已由 B 清理，本次未再处理。

### 行为变更

- **前端**：整体重写。路由 `/chat` 删除，新增 `/classrooms`、`/classroom`、`/teacher`。
- **新增能力**：渲染 `sources` 事件（原版没有），回答下方显示「依据：哪几篇资料」。
- **后端 / 契约 / 数据库**：**无**。A 不需要跟着改任何东西。

### 契约对齐（两处最容易漏的，都已落地）

**1. `sources` 事件（契约 3.1 节）**
第一个事件，`items` 不带正文。三种语义严格区分：

| 情况 | 界面 |
| --- | --- |
| `items` 非空 | 列出资料名（kb 用 topic，upload 用「第 N 页」） |
| `items: []` | 显示「模型自有知识（本轮未使用知识库资料）」 |
| 收不到事件 | 什么都不显示（这条流没覆盖，不代表没用资料） |

**2. `state` 不是必然出现的事件（XB-8）**
纯寒暄 / 未识别知识点时后端只发 `token` + `[DONE]`。
前端处理：**收到才更新，收不到就保持上一轮**，不清空、不当异常。

### 验证

| 项 | 结果 |
| --- | --- |
| `npm install` | 365 个包，成功 |
| `npx tsc --noEmit` | 通过，零报错 |
| `npm run build` | 编译 + 类型 + 7 个静态页，4 路由全部导出 |
| 静态导出实测 | `/`、`/classrooms/`、`/classroom/`、`/teacher/` 全 200；CSS 与 JS chunk 均 200；页面含真实中文内容 |
| SSE 解析契约测试 | **20 条断言全过**，含每 3 字节切片的极端分帧（验证不丢字）、sources 三态、降级流无 state、`type=error`、未知事件忽略 |

### 未能验证的（需要 A/B 的环境）

**真实 `/chat` 端到端没跑成**，原因是本机没有可用的后端环境：

- `backend/venv` 是队友机器（`E:\Python`，Python 3.11.8）的拷贝，**缺 `Scripts` 目录**，
  包也是 3.11 ABI 编译的，本机 3.12 无法复用；
- 本机 MySQL（3306）未启动。

请在 A/B 的环境上跑一次，确认三件事：流式有输出、答案下方出现「依据」、侧栏画像会变。

> 说明：`/chat` 的解析逻辑已用契约字节流覆盖，剩余风险主要在**环境**而非代码。

### 怎么跑

```bash
cd Tutor-Chatbot-main/frontend
npm install

npm run dev            # 浏览器调试：http://localhost:3000
npm run client         # 桌面客户端（自动拉起前端服务）
npm run build && npm run client:static   # 静态态客户端
```

⚠️ 浏览器必须用 `localhost` 而不是 `127.0.0.1` ——
`backend/.env` 的 `ALLOWED_ORIGINS` 只写了 `http://localhost:3000`。
桌面客户端不受影响（静态态由主进程补 CORS 头，不改后端 `.env`）。

### 回退

```bash
git checkout -- Tutor-Chatbot-main/frontend
```

本目录有 git，改造前的基线是 `528035f`（契约补 XB-8 说明）。
本次改动全在 `frontend/` 与两份新增 md 里，不涉及 backend / knowledge。

### 待确认（需要 A 拍板）—— ✅ 已全部结清 2026-10-24

A 回复「你看着办，做好记录就行」，于是由 C 逐条核实后端现状后拍板。
**结论：三条都已由 A 在后续迭代中解决，待确认项本身已过期。**
详见下方《2026-10-24 结清三条历史待确认项》。

1. **多场景** —— ✅ 已解决（M4）。`/chat` 支持 `scenario`，SSE 新增 `agent` 事件
   带 `role/label/stance/index/total`，`messages` 表加了 `agent_role` 列。
   前端已接 `v1` / `group`，本次再把 `debate` 开放。
2. **sources 落库** —— ✅ 已解决（2026-10-21，A 在 `db/models.py` 里已加 `sources` JSON 列，
   `init_db.py` 会给存量库自动补列）。**但前端漏接了**，本次补上映射。
3. **课堂列表** —— ✅ 已解决（M1）。`GET /classrooms?user_id=N` 已存在，
   按成员关系过滤并返回房间内角色。前端原本还在读 localStorage，本次改为后端优先。

---

## 2026-10-24 接 A 的 M4 交付：多智能体气泡 + 建房选场景

> 依据：`docs/handoff/HANDOFF.md` 给 C 的 P1 三件事；契约见 `docs/specs/API_CONTRACT.md` §3.1 / §4「M4 多场景接入要点」。

### 一句话

把后端的「一轮多个 Agent 依次发言」在界面上呈现出来：每个角色一个气泡，
建房时选场景（下拉项来自后端 `/scenarios`），刷新后按 `agent_role` 分得开。

### 三件事分别落在哪

| 交付项 | 落点 |
|---|---|
| SSE `agent` 事件分气泡 | `lib/chat.ts`（解析）+ `components/chat.tsx`（视觉）+ `app/classroom/page.tsx`（开气泡） |
| 建房时选场景 | `lib/api.ts`（`listScenarios` / `createClassroom` 带 `scenario`）+ `app/teacher/page.tsx`（下拉） |
| 历史记录按 `agent_role` 分气泡 | `app/classroom/page.tsx` 的 `refreshMessages` 映射 |

### 分气泡怎么实现的（关键，别改坏）

一轮对话里后端会先发 `sources`，然后按角色序列发 `agent` → `token`… → `agent` → `token`…，
最后 `state` + `[DONE]`。前端的对应处理：

1. 发送时先建**一个占位气泡**（`local-a-{seq}`），`currentAiKeyRef` 指向它。
2. 收到**第一个** `agent`（`index=0`）→ 把角色信息（role/label/stance/index/total）
   patch 到那个占位气泡上，复用它，不新建。
3. 收到**后续** `agent` → 先把当前气泡收尾（`streaming:false`），
   再 push 一个新气泡，`currentAiKeyRef` 切过去。新气泡 key 是 `${firstKey}-{index}`。
4. 之后所有 `token` 一律追加到 `currentAiKeyRef` 指向的气泡。
5. `sources` 到达时还没有 `agent`，所以固定挂到**第一个**气泡。
6. 收流后按 `turnKeysRef` 把本轮所有气泡统一收尾。

> 为什么第一个复用而不是每次都新建：不知道后端会不会发 `agent` 事件
> （降级流 / 老后端不发）。先建占位能保证「没有任何事件也有一条气泡在转圈」，
> 收到第一个 agent 再补角色信息，两种情况都不会多出空气泡。

### 四个角色怎么区分

角色中文名与 `stance` 都由后端给（`agent.label` / `agent.stance`），
前端只负责把它们显示出来，不自己维护一份映射。视觉上：

| 角色 | 头像角标 | 配色 |
|---|---|---|
| `tutor` 主讲助教 | 讲 | 青绿 |
| `teacher` 主讲教师 | 师 | 琥珀 |
| `peer` 学伴 | 伴 | 蓝 |
| `examiner` 考官 | 考 | 中性灰 |

头像用角标不用首字母 —— 一轮里三个 AI 的首字母都是 "A"，分不出来。

### 没碰的东西（重要）

- **`/chat` 请求体仍然是三个字段**，没加 `scenario`：建房时已经定了场景，
  服务端按课堂走，前端不重复指定（契约 §4：传了也只是覆盖，调试时才用）。
- 后端、契约、`knowledge/` 一字未改。
- 轮询机制没动，仍是 5 秒全量。

### 顺手清理的死文件

`app/chat/page.tsx`、`app/components/TutorChat.tsx`、`components/WelcomeModal.tsx`、
`lib/consent.ts` —— 开源原版遗留，只互相引用，且默认发 `provider=groq`（本项目后端会 503）。
上一轮记录里已写明「已删除」但实际没落地，本次补上（备份在
`.workbuddy/backup-legacy-frontend/`）。删掉后 `npx tsc --noEmit` 才是零报错。

### 验证

| 项 | 结果 |
|---|---|
| `npx tsc --noEmit` | ✅ 零报错 |
| `npm run build` | ✅ 4 路由全部静态导出 |

### 加了个假后端（mock-backend/server.js）

**起因**：本机确实跑不起真后端（无 `.env`、无 venv、MySQL 3306 未监听），
四个页面全卡在入口页 —— 点「进入」调 `POST /users` 直接失败，后面三个界面进不去。

**做法**：`frontend/mock-backend/server.js`，零依赖（只用 `node:http`），内存态。
覆盖了前端实际用到的 14 个路由，SSE 严格按契约发帧（`\n\n` 分隔，末尾字面量 `[DONE]`）。
场景表与 `backend/agent/scenarios.py` 逐字对齐，预置课堂 1（group）方便直接进。

**跑法**：
```
cd Tutor-Chatbot-main/frontend
node mock-backend/server.js     # 8000 端口，与入口页默认值一致
npm run dev                     # 另开一个终端，3000 端口
```
⚠️ 只是联调替身：**不入库、不调模型**。备课接口会返回 `download_url: null`
（没有真的 pptx），前端按契约正常处理即可。

### 验证（补充）

| 项 | 结果 |
|---|---|
| `npx tsc --noEmit` | ✅ 零报错 |
| `npm run build` | ✅ 4 路由全部静态导出 |
| 4 个路由 HTTP | ✅ `/`、`/classrooms/`、`/classroom/`、`/teacher/` 均 200 |
| `GET /health` | ✅ 返回 model / database |
| `GET /scenarios` | ✅ 3 场景，steps 字段齐全 |
| SSE `agent` 事件 | ✅ 2 条（index 0/1，total=2），tutor → peer |
| SSE `state` / `[DONE]` | ✅ state 在最后，[DONE] 恰好 1 条 |
| 落库分行 | ✅ 一轮 = 1 学生 + 2 assistant，agent_role 分别为 tutor / peer |

⚠️ **仍未验证的**：真实模型下的 `/chat`（本机无后端环境）。
上面这些只证明了「前端解析 + 分气泡」这条链路是对的，
真正的措辞质量、是否复读、延迟多少，还是要 A/B 在他们的环境上跑一次。

---

## 2026-10-24 结清三条历史待确认项

> 起因：A 回复「你看着办，做好记录就行」，把三条待确认的决策权交给 C。
> 下方逐条**先核实后端现状、再定方案**，不凭旧记录下判断。

### 核实结论：三条都已过期，后端早就做完了

| # | 待确认项 | 后端现状（本次实测） |
|---|---|---|
| 1 | 多场景 / `agent_role` | ✅ M4 已落地：`/chat` 收 `scenario`，`GET /scenarios`，SSE 有 `agent` 事件，`messages.agent_role` 已建列 |
| 2 | `sources` 落库 | ✅ 2026-10-21 已落地：`db/models.py:150` 加 `sources` JSON 列，`init_db.py` 还会给存量库自动 `ALTER TABLE` |
| 3 | `GET /classrooms` | ✅ M1 已落地：`app.py:1340`，按 `classroom_members` 过滤，返回房间内 `role` |

**所以真正的问题不是"要不要做"，而是"后端已经给了，前端漏接"。**

### 改动一：补上落库 `sources` 的映射（这是真 bug，不是优化）

`refreshMessages` 之前映射了 `agent_role`，**唯独漏了 `sources`**。
后果：一轮回答结束时 `await refreshMessages()` 会用 DB 版本覆盖本地气泡，
而 DB 版本没有 sources —— **刷新后「依据」消失**。

更麻烦的是代码里还留着一句过期注释：

> "注意 sources 只存在于流式那一刻，落库消息没有这个字段"

这句在 2026-10-21 之后就不成立了，但它会持续误导后来的人以为这是后端边界、
从而不去修前端。**已一并改正。**

配套改动：`normalizeSources` 从 `lib/chat.ts` 导出，让流式路径与历史路径
共用同一份归一化逻辑 —— 否则两处对 `origin` / 空值的处理迟早会漂移，
表现为「刷新前后依据长得不一样」。

**行为变更**：刷新后每条 AI 回复下方会显示「依据」行。

### 改动二：开放 `debate` 场景

`OPEN_SCENARIOS` 从 `["v1", "group"]` 改为 `["v1", "group", "debate"]`。

理由：作业明确要求三种协作形态，少一个就无法验收。
`debate` 一轮 3 个 Agent、约 14 秒 —— **慢是可感知的等待，缺项是验收不通过**。
而且等待已经前置处理过了：SSE `agent` 事件带 `index`/`total`，
界面会显示「第 2/3 位发言中」，把「卡住了」变成「看得见的进度」。

若后续真觉得慢，正确做法是后端并行或裁剪，而不是把场景藏起来。

**回退**：把 `"debate"` 从数组里删掉即可，其他代码不用动。

### 改动三：「我的课堂」改为后端优先，本机记录降级为兜底

原来这一屏只读 localStorage，换台机器就空了。现在：

1. 有身份 → 调 `GET /classrooms?user_id=N`
2. 后端返回非空 → **以后端为准**，并用返回的 `role` 覆盖本地记的角色
3. 后端返回空 / 报错 → 静默回退本机记录，不让列表页变成报错页

第 2 点里的角色覆盖是刻意的：后端给的是**房间内**角色（来自 `classroom_members`），
比 localStorage 里的全局 `identity.role` 准。同一个人完全可能既是 A 房老师、
又是 B 房学生 —— 用全局角色显示会标错身份。

**行为变更**：同一账号在不同设备上能看到同样的课堂列表；跨房间的角色显示更准。

### 一个留下的语义缺口（不阻塞，建议 A 后续处理）

后端 `app.py:1255` 返回的是 `"sources": m.sources or []`，
把 `NULL`（老数据/未覆盖）和 `[]`（本轮明确没用资料）**压成了同一个值**。

契约 §3.1 原本是要区分这三种的：非空 / 空 / 没收到事件。
现在的实际效果是：刷新后所有消息一律显示「未引用资料」，包括那些
其实是「老数据没记」的。语义够用，但区分度不如流式那一刻。

**前端已按规范写好三分支**（`null` → 不显示依据行），
后端哪天改成返回原始 `m.sources` 就能立刻接住，前端无需再改。

### 验证

| 项 | 结果 |
|---|---|
| `npx tsc --noEmit` | ✅ 零报错 |
| `npm run build` | ✅ 4 路由全部静态导出 |