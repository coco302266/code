# AI Classroom 前后端接口契约

> **用途**：T8 前后端联调的唯一依据。后端已冻结，前端按本文档对接。
> **后端版本**：提交 `5ea03ed`（T0–T7 全部完成）
> **生成日期**：2026-10-05

---

## 0. 通用约定

| 项 | 值 |
|---|---|
| Base URL（本地） | `http://127.0.0.1:8000` |
| Swagger 自动文档 | `http://127.0.0.1:8000/docs` |
| 编码 | 全部 UTF-8，请求体 `Content-Type: application/json` |
| CORS | 已开启。后端读环境变量 `ALLOWED_ORIGINS`，默认 `["http://localhost:3000"]`，methods / headers 全开，`allow_credentials=true` |
| 错误响应体 | `{"detail": "错误描述（英文）"}` |
| 数据库未配置时 | 需要数据库的接口一律返回 **503**，`detail` 提示去填 `MYSQL_*` |

**限流**：命中限流返回 **429**。注意限流的计数键是**客户端 IP**（`get_remote_address`），不是用户 ID——同一个局域网内多个人会共享同一个额度。

| 接口 | 限额 |
|---|---|
| `POST /chat` | 10 次 / 分钟 |
| `POST /resources/ppt-outline` | 5 次 / 分钟 |
| `POST /resources/generate` | 5 次 / 分钟 |
| `POST /upload` | 3 次 / 小时 |
| 其余接口 | 不限 |

---

## 1. 必须先在 Dictionary 上对齐的三个概念

这三个词后端有明确语义，**跟日常理解不完全一样**，对齐之前不要开工。

### `classroom_id` + `user_id` —— 「要不要落库」的开关

`POST /chat` 是否读写数据库，取决于请求里**有没有同时带这两个字段**：

| 传参情况 | 行为 |
|---|---|
| 两个都传 | 校验成员身份 → 恢复历史 → 恢复教学状态 → 落库提问和回答 → 沉淀画像 |
| 只传 `classroom_id` | **不落库**（`SessionLocal` 为空时同样不落库），也不校验身份 |
| 两个都不传 | 退化为纯内存对话，旧前端行为不变 |

## 角色与 @ 出题助手（2026-11-01）

角色一共四个，中文名由后端 `agent/scenarios.py` 的 `ROLE_LABELS` 统一下发，
前端不再维护第二份映射：

```
角色键   中文名     何时发言
tutor    主讲助教   课堂配备主讲助教时的常规问答
teacher  主讲教师   教师身份发言（人类，不参与 AI 编排）
peer     出题助手   学生消息里出现 @出题助手 / @出题 / @出题人 / @quiz；或课堂关掉了主讲助教
examiner 考官       显式 agent_role 指定（评测与调试用）
```

两条规则值得单独记住：

1. **出题助手不进默认编排**。场景表里 v1 与 group 都只有 tutor 一个发言位；
   出题助手只在被 @ 点名时才被编进计划。这样每轮不会多一次 LLM 调用。
2. **@ 的优先级高于课堂开关**。即使课堂设置了 `use_tutor=false`，
   学生 @ 出题助手时仍然是出题；没 @ 时则由出题助手兜底应答（否则没人回答）。

> ⚠️ 前端**必须在第一次 `/chat` 之前**先建好 user / classroom / membership，否则消息不会持久化，刷新页面聊天记录就没了。

> **M4 修正：不带 `classroom_id` 也会记编排留痕**（`agent_runs.classroom_id` 为 `NULL`）。
> 早先这类请求整轮不落库，症状是「回答完全正常、`/evaluation/*` 全空、日志零报错」。
> 但**发言正文仍然不落库**——所以无课堂的轮次能进客观指标统计，人工评分表却没有原文可评。
> 这类留痕的数量见 `/evaluation/status` 的 `runs_without_classroom`；
> 按课堂查询时它们会被漏掉（NULL 不等于任何 id）。

### `hint_level` —— 讲解深度，不是启发级别

取值 `0–3`，**每一层都会直接给答案**，区别只在展开程度：

| level | 讲法 |
|---|---|
| 0 | 一句话结论 |
| 1 | 面试标准答案（分点、可背诵、200 字内） |
| 2 | 展开原理：为什么是这样，底层机制与设计取舍 |
| 3 | 原理 + 具体例子 + 一个面试追问方向 |

服务端字段 `hint_level` 是**技术产物**，前端**不需要展示成"难度 0/1/2/3"**给学生看，它只用于 debugging。

### `role` —— 一个人有两个 role

`users.role` 是全局身份，`classroom_members.role` 是**这个课堂里**的身份。同一个人在 A 课堂是学生、在 B 课堂可以是教师。**权限判定一律看课堂里的 role。**

---

## 2. 接口总表（共 27 个）

| # | 方法 | 路径 | 用途 | 前端谁用 |
|---|---|---|---|---|
| 1 | POST | `/chat` | 学生提问，SSE 流式返回回答（M1：`agent_role` 指定角色；**M4：`scenario` 指定场景，一轮可多个 Agent 依次发言**） | 学生 |
| 2 | GET | `/health` | 健康检查 | 双方 |
| 3 | POST | `/users` | 创建用户（幂等） | 双方 |
| 4 | POST | `/classrooms` | 创建课堂（M1：`is_private` / `members` / `teacher_members`；M4：`scenario`；**2026-11-01：`use_tutor` 是否配备主讲助教，默认 true**） | 教师 |
| 5 | POST | `/classrooms/{id}/members` | 加入课堂。**1v1 课堂有容量闸门：1 教师 + 1 学生，超员返回 409** | 双方 |
| 6 | GET | `/classrooms/{id}` | 课堂详情（M1：返回 `is_private`；M4：返回 `scenario`；**2026-11-01：返回 `use_tutor`**） | 双方 |
| 7 | GET | `/classrooms` | 列出某用户加入的课堂（"我的课堂"，**M4：返回 `scenario`**） | 双方 |
| 7.1 | GET | `/scenarios` | **M4 新增** 列出可用协作场景（建房时的下拉选项） | 双方 |
| 7.2 | GET | `/classrooms/{id}/runs` | **M4 新增** 编排留痕：每轮实际派了哪些角色 / 是否降级 / 耗时（排障 + 评估报告） | 调试 / 教师 |
| 7.3 | GET | `/evaluation/status` | **M4-C 新增** 留痕链路自检：`/evaluation/*` 为什么是空的（**数据库不可用时也返回 200，不报 503**） | 排障 |
| 7.4 | GET | `/evaluation/summary` | **M4-C 新增** 客观指标聚合（轮数 / 每轮 Agent 数 / 降级率 / 延迟分位 / 检索覆盖率）+ `diagnosis` | 教师 / 报告 |
| 7.5 | GET | `/evaluation/sheet` | **M4-C 新增** 人工评分表：**每个 Agent 每次发言一行**，四维留空 + `diagnosis` | 教师 / 报告 |
| 8 | GET | `/classrooms/{id}/members` | 成员列表 | 教师 |
| 9 | GET | `/classrooms/{id}/messages` | 聊天记录（全课堂；M1：支持 `after` 增量 + 返回 `agent_role`） | 双方 |
| 9.5 | POST | `/classrooms/{id}/messages` | **M1 新增** 群聊发消息（人类成员发言，不触发 AI） | 双方 |
| 10 | GET | `/students/{id}/profile` | 读画像 | 双方 |
| 11 | PUT | `/students/{id}/profile` | 手工修正画像 | 调试用 |
| 12 | GET | `/students/{id}/behaviors` | 学习行为流水 | 调试 / 教师 |
| 13 | GET | `/students/{id}/prompt-preview` | 预览讲法（不花额度） | 调试用 |
| 13.1 | POST | `/students/{id}/pretest` | **M2 新增** 认知诊断组卷（出题，不含答案） | 学生 |
| 13.2 | POST | `/students/{id}/pretest/submit` | **M2 新增** 批改并写入画像 | 学生 |
| 13.3 | GET | `/students/{id}/knowledge-graph` | **M2 新增** 带掌握度叠加的知识图谱 | 双方 |
| 14 | POST | `/resources/ppt-outline` | **教师**备课，生成大纲 + pptx | 教师 |
| 14.5 | POST | `/resources/generate` | **M3 新增** 统一资源生成：5 类个性化资源（slides/quiz/mindmap/cards/guide） | 教师 |
| 15 | GET | `/resources/files/{file_name}` | 下载生成的 pptx | 教师 |
| 16 | POST | `/upload` | 上传 PDF 建临时问答库 | 可选 |
| 17 | DELETE | `/sessions/{session_id}` | 销毁上传的临时会话 | 可选 |

---

## 3. 逐接口详解

### 3.1 `POST /chat` —— 核心接口

**请求体**

```json
{
  "message": "死锁产生的四个必要条件是什么？",
  "classroom_id": 1,
  "user_id": 3,
  "topic": "",
  "hint_level": 0,
  "misconception": "",
  "resolved": false,
  "history": [],
  "session_id": "",
  "provider": "openai",
  "agent_role": "tutor"
}
```

| 字段 | 类型 | 必填 | 约束 | 说明 |
|---|---|---|---|---|
| `message` | string | ✅ | 1–1000 字符 | 学生本轮提问 |
| `classroom_id` | int \| null | ➖ | — | 传了才落库 |
| `user_id` | int \| null | ➖ | — | 与上面成对出现 |
| `topic` | string | ❌ | ≤100 | **会被服务端覆盖**，见下方注意事项 |
| `hint_level` | int | ❌ | 0–3 | **会被服务端覆盖** |
| `misconception` | string | ❌ | ≤500 | 同上 |
| `resolved` | bool | ❌ | — | 同上 |
| `history` | array | ❌ | ≤50 条 | 走 DB 时前端**可以不传**，服务端自己加载 |
| `session_id` | string | ❌ | — | 只有上传过 PDF 才用得到 |
| `provider` | string | ❌ | ollama/groq/gemini/openai，默认 `openai` | 前端**不要传**，保持默认 |
| `agent_role` | string | ❌ | tutor/teacher/peer/examiner，默认 `tutor` | **M1**：AI 以哪个角色 Agent 发言（`tutor` 主讲助教 / `teacher` 主讲教师 / `peer` 学伴 / `examiner` 考官）。落库到 `messages.agent_role`。**只在 1v1 场景生效**——多步场景（group/debate）的角色序列由场景决定，该字段被忽略 |
| `scenario` | string \| null | ❌ | v1/group，默认 `null` | **M4**：本轮跑哪种协作形态。`null` = 跟随课堂自身的 `scenario`；显式传值则覆盖（调试 / 演示用）。非法值**不报 422**，后端回落到 `v1`。2026-11-01：`debate` 已下线，传它按未知场景处理（回落 v1 并记 degraded） |

> 请求模型开了 `extra="forbid"`，**多传一个未知字段会直接 422**。

**响应：`text/event-stream`（SSE）**，不是一个 JSON。

```
data: {"type": "sources", "items": [...]}\n\n
data: {"type": "agent", "role": "tutor", "label": "主讲助教", "stance": "立论", "index": 0, "total": 3}\n\n
data: {"type": "token", "content": "死锁"}\n\n
data: {"type": "token", "content": "产生"}\n\n
data: {"type": "agent", "role": "peer", "label": "学伴", "stance": "质疑", "index": 1, "total": 3}\n\n
data: {"type": "token", "content": "不过"}\n\n
...
data: {"type": "state", "topic": "死锁", "hint_level": 0, "misconception": "", "resolved": false, "personalized": true}\n\n
data: [DONE]\n\n
```

| 事件 | 时机 | 载荷 |
|---|---|---|
| `sources` | **第一个**事件，token 之前 | `{"type":"sources","items":[...]}`，见下 |
| `agent` | **每个角色发言前**各一条 | `{"type":"agent","role":"tutor","label":"主讲助教","stance":"立论","index":0,"total":3}`，见下 |
| `token` | 逐字流式输出 | `{"type":"token","content":"片段"}` |
| `state` | 流结束时**最后一条** | `topic` / `hint_level` / `misconception` / `resolved` / `personalized` |
| `error` | 流中途失败 | `{"type":"error","content":"..."}` |
| `[DONE]` | 结束标记，**字面量，不是 JSON** | — |

**`agent` 事件（M4 多智能体）**——一轮里可能有多个角色 Agent 依次发言：

- `role` 英文角色键（tutor/teacher/peer/examiner）、`label` 中文名、`stance` 本场景中的身份（立论 / 质疑 / 追问 / 补充 / 主讲）。
- `index` 从 0 开始，`total` 本轮总发言数；**`total == 1` 时等价于旧的 1v1 行为**。
- 前端据此**新开一个气泡**：收到 `agent` 就切换当前说话人，其后的 `token` 归到该气泡下。
- **向后兼容**：不认识 `agent` 事件的老前端会把它当未知类型静默忽略，表现为所有角色的 token 连成一整段——文字不丢、不会崩，只是不分气泡。所以后端可以先上。
- `total > 1` 时**每条发言各落库一行** `messages`，`agent_role` 不同；增量轮询 `?after=` 照常只补新增。

**`sources` 事件**——本轮到底给模型喂了哪几片资料（知识库切片 / 学生上传的 PDF 切片）：

```json
{"type":"sources","items":[
  {"origin":"kb","source":"cs/network/http-https.md","topic":"HTTPS 与 TLS 握手","category":"计算机网络"},
  {"origin":"upload","page":3,"section":"body"}
]}
```

- `origin` 只有 `kb` / `upload` 两种；`kb` 带 `source` / `topic` / `category`，`upload` 带 `page` / `section`。
- **不带正文**：正文已经在 token 流里了，重复传只会放大响应体积。
- **三条流都发**，且语义区分清楚：`items: []` 表示「这一轮明确没有用资料」（如纯寒暄）；
  收不到 `sources` 才表示这条流没覆盖——不要混为一谈。
- **后端先上、前端后接是安全的**：未知 `type` 在前端是静默忽略的（三个独立 `if`），
  老前端不会崩，只是不显示来源。
- 用途：检索全错时模型会用自有知识答得很好、从回答上完全看不出来（本项目已实测过），
  只有把「系统喂了它什么」单独上报才能发现质量退化。

> **例外：未识别出知识点时没有 `state`。**
> 学生说了纯寒暄、或模型判定 `topic == "unknown"` 时，服务端走降级回答，**只发 `token` + `[DONE]`，不发 `state`**
> （此时没有教学状态可推）。因此 **`state` 不是"必然出现"的事件**，前端**必须**允许一轮对话收不到 `state`：
> 收不到就保持上一轮的 `topic` / `hint_level` 不变，不要清空、也不要当成异常。
> 反过来，**只要收到 `state`，它一定是真的教学状态，且一定带 `personalized` 字段**——可以拿这一点做校验。

> ⚠️ **最关键的一条**：回答过程中的错误是**走 SSE 事件体里的 `type=error`**，不是 HTTP 状态码。HTTP 状态码在流开始时就已经是 200 了。前端**必须**在 SSE 解析循环里处理 `error`，否则 LLM 挂掉时界面会转圈到天荒地老。

HTTP 层面的错误码：

| 码 | 原因 |
|---|---|
| 403 | 传了 `classroom_id` 但该 `user_id` 不是这个课堂的成员 |
| 422 | 字段校验失败（含多传字段） |
| 429 | 限流（10 次/分钟，按 IP） |
| 502 | LLM 评估节点调用失败 |
| 503 | `provider` 对应的环境没配好 |

**前端解析参考**

```ts
const res = await fetch(`${BASE}/chat`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ message, classroom_id, user_id }),
});

const reader = res.body!.getReader();
const decoder = new TextDecoder();
let buffer = "";
let answer = "";
let state = null;

while (true) {
  const { done, value } = await reader.read();
  if (done) break;
  buffer += decoder.decode(value, { stream: true });

  const lines = buffer.split("\n\n");
  buffer = lines.pop() ?? "";

  for (const line of lines) {
    if (!line.startsWith("data: ")) continue;
    const payload = line.slice(6).trim();
    if (payload === "[DONE]") return { answer, state };
    const evt = JSON.parse(payload);
    if (evt.type === "token") answer += evt.content;
    else if (evt.type === "state") state = evt;
    else if (evt.type === "error") throw new Error(evt.content);
  }
}
```

---

### 3.2 用户 / 课堂 / 成员

#### `POST /users` → 201

```json
// request
{ "username": "张三", "role": "student" }      // role: student | teacher，默认 student
// response
{ "id": 1, "username": "张三", "role": "student" }
```

`username` 唯一。**重名不会报错，会直接返回已有那条记录**（幂等创建），前端重复提交不会产生脏数据。

#### `POST /classrooms` → 201

```json
// request
{
  "name": "计网八股冲刺班",
  "subject": "cs",
  "description": "可选",
  "is_private": false,          // M1：true 表示「单人私聊」房间（前端当 DM 渲染）
  "members": [3],               // M1：创建时一次性加学生成员，省去逐个 add_member
  "teacher_members": [1]         // M1：创建时一次性加教师成员
}
// response
{ "id": 1, "name": "计网八股冲刺班", "subject": "cs", "description": "可选", "is_private": false }
```

`members` / `teacher_members` 里的 user_id 若不存在 → 404。私聊场景用 `is_private=true` + 一次性把双方加为成员最省事。

#### `POST /classrooms/{classroom_id}/members` → 201

```json
// request
{ "user_id": 3, "role": "student" }
// response
{ "id": 5, "classroom_id": 1, "user_id": 3, "role": "student" }
```

重复添加同一人不会报错（联合唯一约束），返回已有记录。
错误：课堂不存在 → 404；用户不存在 → 404。

#### `GET /classrooms/{classroom_id}` → 200

```json
{ "id": 1, "name": "计网八股冲刺班", "subject": "cs", "description": "可选", "is_private": false }
```

不存在 → 404。

#### `GET /classrooms` → 200

"我的课堂"：列出某 `user_id` 加入的全部课堂。前端不必再把课堂列表存在本机 localStorage。

**查询参数**：`user_id`（可选；不传返回空列表，避免泄露全量课堂）。

```json
{
  "user_id": 3,
  "count": 2,
  "classrooms": [
    {"id": 1, "name": "计算机网络八股班", "subject": "cs", "description": null, "role": "student", "is_private": false, "created_at": "2026-10-05T15:00:00"},
    {"id": 2, "name": "操作系统八股班", "subject": "cs", "description": null, "role": "teacher", "is_private": true, "created_at": "2026-10-06T09:30:00"}
  ]
}
```

> `role` 是该用户在这个课堂里的身份（`student` / `teacher`），与全局 `users.role` 独立。

#### `GET /classrooms/{classroom_id}/members` → 200

```json
{
  "classroom_id": 1,
  "count": 2,
  "members": [
    { "user_id": 1, "username": "王老师", "role": "teacher" },
    { "user_id": 3, "username": "张三", "role": "student" }
  ]
}
```

> AI **不在成员列表里**。它以 `messages.role = "assistant"` 参与对话，不占 user_id。

#### `GET /classrooms/{classroom_id}/messages` → 200

聊天室主数据源。`limit` 默认 100，最大 500。**M1 新增查询参数 `after`（消息 id）**：只返回 id 大于该值的消息，用于聊天室增量轮询（避免每次拉全量）。

```json
{
  "classroom_id": 1,
  "count": 3,
  "messages": [
    {
      "id": 12,
      "user_id": 3,
      "username": "张三",
      "role": "student",
      "content": "死锁的四个必要条件是什么？",
      "topic": null,
      "hint_level": 0,
      "sources": [],
      "agent_role": null,
      "created_at": "2026-10-05T15:20:33"
    },
    {
      "id": 13,
      "user_id": 3,
      "username": null,
      "role": "assistant",
      "content": "互斥、占有且等待……",
      "topic": "死锁",
      "hint_level": 0,
      "sources": [
        {"type": "kb", "source": "死锁.md", "topic": "死锁", "category": "os"},
        {"type": "kb", "source": "进程与线程.md", "topic": "死锁", "category": "os"}
      ],
      "agent_role": "teacher",
      "created_at": "2026-10-05T15:20:41"
    }
  ]
}
```

**字段说明**（这几个容易误解）：

- `username`：AI 的回复为 `null`；学生/教师消息为真实用户名。聊天室用它区分发言人。
- `role`：`student` / `assistant` / `teacher`。**注意 AI 是 `assistant` 不是 `teacher`**。
- `agent_role`（M1）：AI 回复由哪个角色 Agent 产出（`tutor` / `teacher` / `peer` / `examiner`），人类消息为 `null`。聊天室用它把 AI 回复区分成「助教说 / 教师说 / 学伴说 / 考官说」。
- `user_id`：在 assistant 消息上表示「这条回复是给谁的」，以便在多人课堂里区分对话线。
- `topic` / `hint_level`：只对 assistant 消息有意义，学生消息为 `null` / `0`。

> 这个端点返回**整个课堂的全量对话**（群聊视图），不按学生过滤——这是刻意为之，产品决策是「一个课堂 = 一个群」。

#### `POST /classrooms/{classroom_id}/messages` → 201  **（M1 新增 · 群聊发消息）**

人类成员（老师 / 学生）向房间发言，**不触发 AI**。AI 回复仍走 `POST /chat`（role=`assistant` 且带 `agent_role`）。

```json
// request
{ "user_id": 3, "content": "同学你好，一起复习死锁吧" }
// response（单条消息，结构与 GET messages 里的一致）
{
  "id": 14, "user_id": 3, "username": "张三", "role": "student",
  "content": "同学你好，一起复习死锁吧", "topic": null, "hint_level": 0,
  "sources": [], "agent_role": null, "created_at": "2026-10-05T15:21:00"
}
```

规则：
- 发言者**必须是该房间成员**，否则 403（防越权发言）。
- `role` 由其在房间内的**成员角色**决定（teacher → `teacher`，其余 → `student`），不从请求体传——防止前端伪造身份。同一个人在不同房间可能既是 A 房老师、又是 B 房学生。
- 不传 `content` 或超长（>4000 字符）→ 校验失败。

---

### 3.3 学生画像

#### `GET /students/{user_id}/profile`

`classroom_id` 是**可选 query 参数**，但**带不带返回的形状不一样**，这是最容易踩的坑：

**不带 `classroom_id`** → 返回列表包装：
```json
{ "user_id": 3, "count": 1, "profiles": [ {...画像对象...} ] }
```

**带 `classroom_id`** → 直接返回单个画像对象：
```json
{
  "user_id": 3,
  "classroom_id": 1,
  "overall_level": "beginner",
  "learning_preference": "倾向于先要结论再展开原理",
  "knowledge_mastery": { "死锁": 0.3, "TCP 三次握手": 0.8 },
  "weak_points": ["把死锁理解成进程死循环"],
  "mastery_average": 0.55,
  "topic_count": 2,
  "updated_at": "2026-10-05T15:20:41",

  "ability_vector": { "死锁": { "theta": 0.62, "attempts": 2, "correct": 1, "last_difficulty": 0.7 } },
  "cognitive_style": { "ready": true, "confidence": 0.75, "dimensions": [ ... ] },
  "pretested_at": "2026-10-21T15:55:59"
}
```

**M2 新增的三个字段**（存量画像没有 → 分别是 `{}` / `null` / `null`，不是报错）：

| 字段 | 含义 | 前端建议 |
|---|---|---|
| `ability_vector` | 每个知识点的 IRT 诊断证据（能力值、答题次数、正确次数、最后一道题的难度） | 只用于「为什么给这个分」的解释面板，别直接拿去渲染进度条（用 `knowledge_mastery`） |
| `cognitive_style` | 认知风格，见 `POST /students/{id}/pretest/submit` | **`ready=false` 时不要展示**，否则等于念遍是不够样本量的占卜 |
| `pretested_at` | 最后一次施测时间 | `null` 表示该学生还没做过预测试，可以在 UI 上引导一次 |

前端要用的是第二种。用户不存在 → 404；带 `classroom_id` 但没画像 → **404**（要判断是否有对话过）。

#### `PUT /students/{user_id}/profile` → 200

```json
// request（只传要改的字段）
{
  "classroom_id": 1,
  "knowledge_mastery": { "死锁": 0.9 }
}
```

`classroom_id` **必填**。只覆盖传了的字段。`knowledge_mastery` 的值会被钳制到 `[0, 1]` 并保留两位小数。
改了 `knowledge_mastery` 但没传 `overall_level` 时，服务端会自动重算等级。返回完整画像对象（同上）。

> 这是**调试/应急**接口，正常流程不调——画像是每轮对话自动沉淀的。

#### `GET /students/{user_id}/behaviors` → 200

`classroom_id` 可选，`limit` 默认 50 最大 500，按时间倒序。

```json
{
  "user_id": 3,
  "count": 2,
  "behaviors": [
    { "id": 7, "classroom_id": 1, "topic": "死锁", "action": "mastered", "hint_level": 1, "created_at": "..." },
    { "id": 6, "classroom_id": 1, "topic": "死锁", "action": "repeat_question", "hint_level": 2, "created_at": "..." }
  ]
}
```

`action` 取值：`ask_question` / `repeat_question` / `request_explanation` / `mastered`。

#### `GET /students/{user_id}/prompt-preview` → 200

`classroom_id` **必填**，另有 `topic`、`hint_level`(0–3)。

```json
{
  "user_id": 3,
  "classroom_id": 1,
  "topic": "死锁",
  "hint_level": 0,
  "start_level_suggested": 1,
  "effective_hint_level": 1,
  "strategy": "给出面试里的标准答案……",
  "personalized": true,
  "profile_brief": "该学生整体水平 beginner……",
  "profile": { ...完整画像对象... }
}
```

**不消耗 LLM 额度**，调提示词时用它迭代。新学生（`personalized=false`）时 `profile_brief` 为空串、`profile` 为 `null`。

#### `POST /students/{user_id}/pretest` → 200（M2 认知诊断组卷）

在学生开口之前先测一遍能力面，让 `/chat` 的个性化从第一节课就有依据，
而不是聊了十轮才开始。**不消耗 LLM 额度**（题库是标定好的）。

```json
// request
{ "classroom_id": 1, "topics": null, "limit": 8 }
// response
{
  "user_id": 3,
  "classroom_id": 1,
  "count": 8,
  "topics": ["进程与线程", "TCP/IP 分层模型与 ARP / DNS", "进程调度算法", "TCP 与 UDP"],
  "items": [
    {
      "id": "OS-PROC-1",
      "topic": "进程与线程",
      "stem": "关于同一进程内的多个线程，下列说法正确的是？",
      "options": ["它们共享进程的堆和全局变量……", "……"],
      "type": "factual",
      "difficulty": 0.25
    }
  ]
}
```

| 字段 | 说明 |
|---|---|
| `topics` | 不传 → 按掌握度 + 知识图谱自动选题；传了 → 只考指定知识点 |
| `limit` | 1–16，默认 8 |
| `type` | `factual` 事实记忆题 / `conceptual` 概念推理题。**两类都会出**，认知风格要靠它们对比推断 |
| `difficulty` | 0–1 的 IRT 难度参数 |

> ⚠️ **返回值里没有 `answer` 也没有 `explanation`**，这是刻意的：答案泄漏不会报错，
> 只会让批改出来的能力值全是噪声。别催后端补这个字段。
>
> `topics` 传了题库里没有的知识点，会按原样返回（组卷内容可能为空），
> 前端要自己做空列表兜底。

#### `POST /students/{user_id}/pretest/submit` → 200（M2 批改）

```json
// request
{
  "classroom_id": 1,
  "answers": [
    { "item_id": "OS-PROC-1", "choice": 0, "elapsed_ms": 8300, "hint_requested": false }
  ]
}
// response
{
  "user_id": 3, "classroom_id": 1,
  "graded": 8, "invalid_items": [], "correct": 6, "accuracy": 0.75,
  "mastery_updates": { "进程与线程": 0.74 },
  "per_question": [
    {
      "item_id": "OS-PROC-1", "topic": "进程与线程", "item_type": "factual",
      "difficulty": 0.25, "choice": 0, "correct": true, "correct_answer": 0,
      "p_correct_before": 0.924, "ability_before": 0.8, "ability_after": 0.83,
      "delta": 0.03, "explanation": "线程共享所属进程的地址空间……"
    }
  ],
  "cognitive_style": {
    "ready": true, "confidence": 1.0,
    "dimensions": [
      { "key": "reflective_active", "dimension": "沉思型 ↔ 主动型", "value": 1.0,
        "pole": "reflective", "evidence": { "answers": 8, "hints_requested": 8 } }
    ]
  },
  "recommended_next": [{ "topic": "死锁", "score": null, "blocked_by": [] }],
  "profile": { ...更新后的完整画像对象... }
}
```

**需要理解的三个设计**

1. **能力更新不是"答对加分"**。用 IRT-lite 的残差更新：`Δ = lr × (实际作答 − 期望答对概率)`。
   所以「学霸答对送分题」只涨 0.03（期望之内，信息量低），「学霸答错同一题」跌 0.37（强信号）。
   直接加点会把所有人的掌握度刷到 1.0，等于没有测量。
2. **`mastery_updates` 的键是归一后的**（小写、去空格），和 `knowledge_mastery` 同规则。
   提交时会**覆盖本次测到的知识点**、保留没测到的，不会抹掉历史对话证据。
3. **`cognitive_style.ready=false` 时不要用**。样本不足（<4 题）时
   `note` 会写明原因；此时服务端也**不会覆盖**已有结论，避免一次小测验把之前的判断冲掉。

错误码：`answers` 为空或 `item_id` 全部无效 → **400**；`choice` 不在 0–3 → **422**；
用户 / 课堂不存在 → **404**。

#### `GET /students/{user_id}/knowledge-graph` → 200（M2 知识图谱）

`classroom_id` **必填**。画学习地图用，掌握度已叠加在节点上。

```json
{
  "user_id": 3, "classroom_id": 1,
  "categories": ["操作系统", "计算机网络"],
  "node_count": 8,
  "nodes": [
    { "topic": "死锁", "category": "操作系统", "doc": "cs/os/deadlock.md",
      "mastery": 0.56, "measured": true }
  ],
  "edges": [
    { "from": "进程与线程", "to": "死锁", "type": "prerequisite" },
    { "from": "死锁", "to": "进程调度算法", "type": "related" }
  ],
  "learning_order": ["进程与线程", "进程调度算法", "死锁", ...],
  "recommended_next": [
    { "topic": "死锁", "category": "操作系统", "score": null, "mastery": null,
      "measured": false, "blocked_by": [], "order": 2 }
  ]
}
```

- `mastery` 为 `null` 表示**没测过**，不是"得了 0 分"——别画成红色进度条。
- `recommended_next` 已过滤掉「先修知识点尚未达标」的节点，`blocked_by` 会说明被谁挡住。
- `learning_order` 是拓扑序，可直接作为地图的默认排布。

---

### 3.4 教师备课

#### `POST /resources/ppt-outline` → 200

```json
// request
{ "topic": "TCP 三次握手", "classroom_id": 1, "teacher_id": 1, "audience": "" }
```

`audience` 可填 `""` / `beginner` / `intermediate` / `advanced`，留空让模型自己判断。`topic` 最长 100 字符。

```json
// response
{
  "topic": "TCP 三次握手",
  "classroom_id": 1,
  "audience": "",
  "retrieved": true,
  "outline": {
    "title": "TCP 三次握手",
    "objectives": ["理解为什么需要三次而不是两次"],
    "sections": [
      { "title": "为什么是三次", "points": ["确认双方收发能力正常"] }
    ],
    "section_titles": ["为什么是三次"],
    "key_points": ["SYN / SYN-ACK / ACK"],
    "examples": ["WireShark 抓包示意"],
    "questions": ["为什么不是两次？"],
    "summary": ["三次握手的本质是双向确认"]
  },
  "file_path": "D:\\...\\backend\\generated\\TCP_三次握手_xxx.pptx",
  "file_name": "TCP_三次握手_xxx.pptx",
  "download_url": "/resources/files/TCP_三次握手_xxx.pptx"
}
```

`sections` 是 `{title, points}` 对象数组；`section_titles` 是给前端快速渲染目录用的扁平数组（同一份数据的两种形态）。

错误码：

| 码 | 原因 |
|---|---|
| 403 | 不是该课堂成员，或在该课堂的身份不是 `teacher` |
| 404 | 用户 / 课堂不存在 |
| 429 | 超过 5 次/分钟 |
| 502 | LLM 调用失败，**或模型返回了空壳大纲** —— 提示模型偶发，前端要允许重试 |

> `file_path` 是服务端绝对路径，**前端不要用**，下载一律走 `download_url`（相对路径，拼 Base URL）。
> `.pptx` 渲染失败时 `file_name` / `download_url` 会是 `null`，但大纲照常返回——大纲是主产物，PPT 是附赠。

#### `POST /resources/generate` → 200 **（M3 新增 · 统一资源生成）**

把"教师为课堂生成学习资源"做成一个算法库驱动的端点，支持 **5 类个性化资源**：

| `resource_type` | 含义 | 返回 `resource` 结构 |
|---|---|---|
| `slides` | 课件讲义大纲（同 ppt-outline），可顺带渲染 pptx | `{title, objectives, sections[{title,points}], key_points, examples, questions, summary}` |
| `quiz` | 练习题 / 测验（含答案与解析） | `{questions[{id, stem, options[4], answer, explanation, difficulty, type}]}` |
| `mindmap` | 思维导图（层级结构） | `{root, nodes[{title, children[]}]}` |
| `cards` | 知识卡片 / 速记卡 | `{cards[{front, back, tag}]}` |
| `guide` | 循序渐进学习路径（讲稿式） | `{overview, steps[{step, title, detail}], checkpoints[]}` |

```json
// request
{
  "resource_type": "quiz",
  "topic": "进程与线程",
  "classroom_id": 1,
  "teacher_id": 1,
  "user_id": 3,            // 可选：指定目标学生，按其画像个性化
  "difficulty": null,       // 可选 0-1；不传则由画像推导
  "audience": "",           // 可选 beginner/intermediate/advanced
  "count": 5,               // quiz 题量 / cards 卡片数
  "retrieve": true,         // 是否检索知识库作为依据（默认开）
  "render_pptx": false,     // 仅 slides 生效：是否渲染 pptx
  "few_shot": true          // 是否注入 few-shot 样例（稳定格式，默认开）
}
```

```json
// response
{
  "resource_type": "quiz",
  "topic": "进程与线程",
  "classroom_id": 1,
  "difficulty": 0.3,
  "difficulty_label": "基础",
  "personalized": true,
  "retrieved": false,
  "from_bank": true,        // quiz 命中标定题库时走零-LLM 组卷（难度可控、可复现）
  "resource": { "questions": [ ... ] }
}
```

**三大算法特性**

1. **难度控制（`resolve_difficulty`）**：优先级 `请求显式指定 > 学生整体水平 > 该知识点掌握度`，
   把学生真实水平翻译成 0-1 难度目标（弱→更低/更基础）；quiz 还会把难度目标落地成
   "从标定题库按难度带挑题"（见 `from_bank`）。
2. **Few-shot 稳定格式**：每类资源在提示词里注入 1 个同类型 JSON 样例，显著降低模型不守格式的概率；
   关掉 `few_shot=false` 即不注入。
3. **个性化**：传了 `user_id` 就按该学生画像（薄弱点 / 认知风格 / 水平）定制资源内容，
   响应里 `personalized` 标记是否真的注入了画像；不传则按课堂通用讲法。

**行为说明**

- 教师门禁同 ppt-outline：调用者必须是该课堂的 `teacher`，否则 403。
- `quiz` + 主题命中 M2 标定题库（如「进程与线程」「死锁」等 8 个知识点）时，**不调 LLM**，
  直接从题库按难度带出卷（`from_bank=true`、`retrieved=false`），可复现、零额度；
  主题不在题库里则退回 LLM 生成。
- 其余类型与 quiz 的非题库路径都走 LLM 生成，失败（模型返回非法 JSON / 空壳）报 **502**。
- `slides` 且 `render_pptx=true` 时，在返回大纲的同时渲染 `.pptx`，响应追加
  `file_name` / `download_url`（走 `GET /resources/files/{file_name}` 下载）。
- 错误码：教师门禁失败 403；用户 / 课堂不存在 404；LLM 失败 / 空壳 502；限流 429（5 次/分钟）。

#### `GET /resources/files/{file_name}` → 200

返回二进制 pptx 文件，`Content-Type` 为 pptx MIME。不存在 → 404。
`file_name` 直接拼在 URL 里，**中文文件名需要 `encodeURIComponent`**。

---

### 3.5 文档上传（可选功能）

#### `POST /upload` → 200

`multipart/form-data`，字段名 `file`，只接受 PDF。

```json
{ "session_id": "3f2a...-...", "message": "Document indexed successfully." }
```

拿到 `session_id` 后传给 `/chat` 的 `session_id` 字段，回答会基于这份 PDF。

限制：单文件 ≤ 10MB，3 次/小时。**会话存在内存里，1 小时过期，服务重启即丢，最多同时保留 100 个。**

错误：非 PDF → 400；文件头不是 `%PDF-` → 400；超过大小 → 413；解析失败 → 422。

#### `DELETE /sessions/{session_id}` → 204

主动销毁会话，无响应体。

---

### 3.6 `GET /health` → 200

```json
{ "status": "ok", "provider": "openai", "model": "deepseek-flash", "database": "up" }
```

`database` 取值：`up` / `down` / `disabled`（未配 `MYSQL_*`）。
**建议前端启动时打一次**：`database` 不是 `up` 时聊天记录不会落库，应给用户提示。

---

## 4. 前端接入流程

### 学生端

```
1. POST /users          { username, role:"student" }        → 拿到 user_id
1.5 GET /classrooms?user_id=…                              → 列出我已加入的课堂（"我的课堂"，含 is_private）
2. POST /classrooms/{cid}/members { user_id }               → 加入课堂（cid 由产品决定，MVP 可写死）
3. GET  /classrooms/{cid}/messages                          → 渲染历史聊天记录
4. POST /chat           { message, classroom_id, user_id, agent_role? }  → SSE 流式渲染回答
5. 回答结束后可以再拉一次 messages，或直接把 assistant 消息 push 进本地列表
   （messages 里 assistant 消息带 `sources` + `agent_role`，刷新后"依据"/"谁说的"不丢）
6. M1 群聊：人类成员用 POST /classrooms/{cid}/messages { user_id, content } 发言（不触发 AI）
7. M1 增量轮询：GET /classrooms/{cid}/messages?after=<last_id> 只拉新消息，避免每次拉全量
```

> 第 5 步两种方式都行。差别是：重新拉 `messages` 拿到的 `id` 和 `created_at` 是权威的，本地 push 则省一次请求。**建议响应结束后统一重新拉一次**，避免本地列表与服务端不一致。

### 教师端

```
1. POST /users          { username, role:"teacher" }
2. POST /classrooms     { name, subject:"cs", is_private?, members?, teacher_members? } → 拿到 classroom_id
3. POST /classrooms/{cid}/members { user_id, role:"teacher" } → 必须显式以 teacher 身份加入
4. GET  /classrooms/{cid}/members                           → 查看学生名单
5. POST /resources/ppt-outline { topic, classroom_id, teacher_id } → 渲染大纲 / 下载 pptx
```

> ⚠️ 第 3 步容易漏。**创建课堂并不会自动把创建者加为成员**，教师必须显式 `POST members` 且 `role="teacher"`，否则第 5 步会被 403。M1 后也可用 `POST /classrooms` 的 `members` / `teacher_members` 在建房间时一次性加好，省去这步往返。

### M1 聊天室 / 私聊 接入要点

- **一个课堂 = 一个聊天室（群）**：`GET /classrooms/{cid}/messages` 返回全量对话，前端渲染成「所有人都能看到彼此」的群聊。
- **私聊（DM）**：用 `POST /classrooms` 传 `is_private=true` + `members`/`teacher_members` 一次性建好两人私有房间；`GET /classrooms` 会回 `is_private`，前端据此当 DM 渲染。无需新建表。
- **AI 回复的身份**：`/chat` 带 `agent_role`（tutor/teacher/peer/examiner），落库到 `messages.agent_role`；前端把 `role=assistant` 的回复按 `agent_role` 显示为「助教/教师/学伴/考官 说」。
- **人类发言不触 AI**：学生/教师互聊走 `POST /classrooms/{cid}/messages`，角色由其在房间的成员身份决定，前端不能伪造。

### M2 认知诊断 接入要点

```
1. GET  /students/{uid}/profile?classroom_id=…   → pretested_at 为 null 说明还没测过
2. POST /students/{uid}/pretest { classroom_id, limit }  → 渲染试卷（字段里没有答案）
3. 逐题记录 elapsed_ms（可选）：做题耗时是认知风格的证据之一，前端不传也能批改
4. POST /students/{uid}/pretest/submit { classroom_id, answers }  → 批改结果 + 更新后画像
5. GET  /students/{uid}/knowledge-graph?classroom_id=…  → 画学习地图 / 推荐下一步
```

- **预测试是可选的**：不做也不影响 `/chat`，只是个性化要等对话积累几轮才开始。
- **`mastery: null` = 没测过**，不要当成 0 分画红色。
- **`cognitive_style.ready=false` 直接不展示**，别把「样本不足」渲染成「你是 xx 型」。
- **`recommended_next` 已经替你排过序**（先修未达标的会被挡掉），不要自己再按 mastery 升序排，否则会把啃不动的知识点排到第一位。

### M4 多场景 / 多智能体 接入要点

```
1. GET  /scenarios                          → 建房时的场景下拉选项（不要前端硬编码）
2. POST /classrooms { ..., scenario:"debate" } → 建房即定场景，之后每轮自动按它走
3. POST /chat { classroom_id, user_id, message }  → 不用传 scenario，服务端按课堂来
4. SSE 里每收到一个 agent 事件 → 新开一个气泡；token 归到当前气泡
```

- **场景决定角色序列**：`v1`→[tutor]、`group`→[tutor, peer]、`debate`→[tutor, peer, examiner]。
  前端**不需要**逐个指定角色，也**不要**自己拼角色序列——后端是唯一编排方。
- **`agent` 事件是角色切换信号**，`index`/`total` 给全了；老前端忽略它也能正常显示（文字连成一段）。
- **一轮多条 assistant 消息是正常现象**：`total=3` 时库里就是 3 行 `messages`，`agent_role` 各不同。
  渲染聊天记录时按 `agent_role` 区分气泡，不要按 `role=assistant` 合并。
- **`scenario` 非法值不报 422**，后端回落 `v1`；`POST /classrooms` 的返回值里能看到最终生效的 `scenario`。
- **多 Agent 会变慢**（每个 Agent 一次完整 LLM 调用）：`group` 约 2 倍、`debate` 约 3 倍耗时。
  演示前先确认额度；后端有 `AGENT_COLLAB=0` 总开关，一关就退回单 Agent，前端无需改动。
- **排障用 `GET /classrooms/{id}/runs`**：能直接看到每轮实际派了哪些角色、`degraded` 是否为真、
  `reason` 写了什么。发现「多场景没生效」先查这里，别去猜。

### M4-C 评估报告接口（成果 #4）

```
1. GET /evaluation/status                      → 留痕链路自检（数据库不可用也返回 200）
2. GET /evaluation/summary?classroom_id=&user_id=&scenario=&limit=
   → { scope, turns, summary:{ agents_per_turn, degraded_rate, latency_ms{mean,p50,p95}, rag{coverage_rate} }, diagnosis, error }
3. GET /evaluation/sheet?classroom_id=&scenario=&limit=
   → { scope, dimensions:[{key,label,desc}], count, rows:[{run_id, role, excerpt, accuracy, relevance, completeness, personalization}], diagnosis, error }
4. python eval_report.py --classroom-id 3      # 后端本地一键出报告（不调 LLM、零额度）
   → eval_report.md（客观指标）+ eval_scores.csv（人工评分表）
```

- **客观与主观严格分开**：`/evaluation/summary` 只给机器能算的量，四个质量维度一律不在里面。
  别把两者混成一张表，读者会以为客观指标也是打分打出来的。
- **评分粒度是「发言」不是「轮次」**：一轮 3 个 Agent 就是 3 行。`rows[].index` 对应该 Agent 在本轮中的顺序。
- **`dimensions` 随结果一起返回**，评分维度定义以后改了前端不用硬编码。
- **`rag_hits` 是 M4-C 才补的列**，更早的留痕记为 0，跨时间段的覆盖率会被低估——出正式报告请只用新跑的轮次。
- **`/evaluation/summary` 与 `/evaluation/sheet` 需要数据库**（与其他落库接口一致，无 DB 时 503）。
  `/evaluation/status` **不需要**——它存在的意义就是在数据库出问题时还能被读到。

#### `/evaluation/*` 全空怎么办：先读 `diagnosis`

`turns=0` / `count=0` 有**四种成因，表现完全一样**，光看 0 区分不出来。
`summary` / `sheet` 的 `diagnosis` 字段就是用来回答这件事的：

```jsonc
{
  "ready": false,                    // 留痕链路是否健康
  "database": "up",                  // up / down / disabled（三者修法完全不同）
  "database_detail": "",
  "table": { "exists": true, "missing_columns": [], "detail": "" },
  "total_runs": 1,                   // 不受过滤条件影响的总行数
  "runs_without_classroom": 0,       // 有多少条没带 classroom_id
  "hint": "库里共有 1 条留痕，但当前过滤条件下一条都没选中：…"
}
```

| 成因 | 怎么从 `diagnosis` 看出来 | 怎么修 |
|---|---|---|
| ① 没配 `MYSQL_*`（不落库模式） | `database="disabled"` | 填 `backend/.env` 后**重启服务** |
| ② MySQL 连不上 | `database="down"` + `database_detail` 带原始错误 | 起 MySQL / 建库 / 校账号密码 |
| ③ `agent_runs` 表缺失或缺列 | `table.exists=false` 或 `missing_columns` 非空 | 跑 `python init_db.py` |
| ④ 数据写了但被过滤掉 | `total_runs>0` 但 `turns=0` | 改 `classroom_id` / `scenario` / `user_id` |

> **`total_runs` 是区分 ③④ 的关键**：它不受任何过滤条件影响。
> 它是 `null` 表示「查不出来」（表/库有问题），是 `0` 才是「真的一条都没有」。
>
> 不带 `classroom_id` 的 `/chat` 留下的痕 `classroom_id` 为 `NULL`，
> **按课堂查询必然漏掉**——`runs_without_classroom` 就是它们的数量。

---

## 5. 联调注意事项（按踩坑概率排序）

1. **`/chat` 的错误在 SSE 流里，不在 HTTP 状态码上。** 必须在 SSE 循环里处理 `type=error`。
2. **`(classroom_id, user_id)` 必须成对传**，缺一个就静默不落库，不报错——表现为"刷新页面记录全没了"。
3. **客户端回传的 `topic` / `hint_level` / `misconception` / `resolved` 一律被服务端覆盖。** 服务端是状态的唯一真相源。前端不用维护这几个值的本地状态，也不用回传。
4. **限流按 IP 计数**，不是按用户。本机联调时前端和后端同一 IP，10 次/分钟的 `/chat` 额度很容易打满（表现为 429）。调试期可临时关。
5. **`GET /students/{id}/profile` 带不带 `classroom_id` 返回结构不同**（对象 vs 列表包装），必须分支处理。
6. **请求体不能多字段**（`extra="forbid"`），多传一个就 422。后端加字段不会通知，所以别写死 request 类型，留宽松。
7. **`role` 有两个来源**：`users.role` 是全局，`classroom_members.role` 是课堂内。权限看后者。
8. **AI 消息的 `role` 是 `assistant`**，不是 `teacher`；它的 `username` 是 `null`。
9. **聊天室是全课堂共享的**（不按学生隔离上下文）。这是产品决策：B 能看到 A 的问答并接力追问。
10. **`download_url` 是相对路径**，要拼 Base URL；中文文件名要 `encodeURIComponent`。
11. **`provider` 字段别传**，保持默认 `openai`（实际指向 DeepSeek 兼容端点）。传了别的会因为环境没配而 503。
12. **PPT 生成是慢操作**（一次真实 LLM 调用，通常 10–30 秒），教师要有一眼可见的 loading 态。

---

## 6. 后端已知遗留（不影响联调，但需要知道）

| 项 | 影响 |
|---|---|
| `requirements.txt` 落后于 T7，缺 `python-pptx` / `lxml` / `XlsxWriter` | 换机器部署会缺依赖，**不影响本机** |
| `weak_points` 可能存在语义重复的条目 | 画像展示时可能看到相似描述出现多次 |
| 备课暂不看课堂学生画像 | `audience` 需教师手工选择 |
| 上传文档的会话存在内存里 | 服务重启后 `session_id` 失效，前端取到也无妨（会退化成不检索文档） |
| `db/repository.py` 中 `load_history` 的 docstring 写着"按学生隔离" | 与实际调用不符：`/chat` 调用时**刻意不传 `user_id`**（群聊共享上下文）。注释过时，以本文为准 |