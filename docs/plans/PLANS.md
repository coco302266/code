# AI-Classroom 开发与调优计划合集

> 本文件由根目录 4 份计划合并而成（2026-10-30）。合并只为止住根目录散落，正文一字未改，仅把标题层级整体下调两级。计划类文档按「当时已知」写成，落地后的事实以 docs/specs/RAG_HANDBOOK.md 与 docs/worklogs/B_WORKLOG.md 为准。

## 原名索引（按旧文件名查）

- `DEVELOPMENT_PLAN.md`（AI Classroom 三周开发计划） —— 第 1 节，[跳转](#development-plan)
- `FRONTEND_PLAN.md`（AI Classroom 前端设计说明（C 部分）） —— 第 2 节，[跳转](#frontend-plan)
- `RAG_TUNING_PLAN.md`（RAG 系统性调优方案） —— 第 3 节，[跳转](#rag-tuning-plan)
- `RAG_RERANK_SELECTION_PLAN.md`（重排（Rerank）模型选型与落地方案） —— 第 4 节，[跳转](#rag-rerank-selection-plan)

---

<a id="development-plan"></a>

## 1、原名 DEVELOPMENT_PLAN.md

### AI Classroom 三周开发计划

#### 0. 总体开发策略

本项目采用：

> 开源项目运行 → 代码理解 → 功能复用 → 增量开发 → 阶段验收

而不是从零开发。

核心路线：

```text
Tutor-Chatbot
    ↓
运行原项目
    ↓
理解 FastAPI / Agent / RAG
    ↓
计算机八股知识库
    ↓
MySQL
    ↓
Student / Teacher / Classroom
    ↓
StudentProfile
    ↓
个性化 Prompt
    ↓
Teacher Resource Agent
    ↓
前端联调
    ↓
测试与报告
```

---

#### 第一阶段：开源项目接管

##### T0.1 项目检查

目标：

> 了解当前开源项目，不修改业务代码。

任务：

- 检查目录
- 检查 README
- 检查依赖
- 检查 Python 版本
- 检查 FastAPI
- 检查 Agent
- 检查 RAG
- 检查 ChromaDB
- 检查 LLM 配置
- 检查测试

验收：

```text
[ ] 项目结构明确
[ ] 启动方式明确
[ ] LLM 配置明确
[ ] Chat 调用链明确
[ ] Agent 调用链明确
[ ] RAG 调用链明确
```

---

##### T0.2 运行原项目

目标：

> 原项目能够在本地运行。

验收：

```text
[ ] Python 环境正常
[ ] 依赖安装成功
[ ] FastAPI 启动
[ ] Swagger 可访问
[ ] Chat 可使用
[ ] RAG 可使用
```

Git：

```bash
git add .
git commit -m "chore: import and verify base tutor project"
```

---

#### 第二阶段：理解核心代码

##### T1.1 FastAPI

理解：

```text
main/app
 ↓
router
 ↓
service
 ↓
response
```

重点：

- API 路由
- Request
- Response
- Service

---

##### T1.2 Agent

理解：

```text
graph
state
node
edge
```

重点：

- Agent 输入
- Agent 输出
- Agent State
- Agent 调度

---

##### T1.3 RAG

理解：

```text
Document
 ↓
Chunk
 ↓
Embedding
 ↓
Vector DB
 ↓
Retriever
 ↓
Context
 ↓
LLM
```

验收：

能够自己解释：

> “学生的一句话是怎么最终变成 AI 回答的。”

---

#### 第三阶段：计算机八股知识库

##### T2.1 建立知识目录

第一版：

```text
knowledge/
└── java/
    ├── basics/
    ├── collection/
    ├── jvm/
    └── concurrent/
```

---

##### T2.2 准备知识文档

第一版重点：

```text
HashMap
ArrayList
ConcurrentHashMap
JVM
GC
synchronized
volatile
```

数量优先少而精。

---

##### T2.3 RAG 导入

实现：

```text
Markdown
 ↓
Chunk
 ↓
Embedding
 ↓
ChromaDB
```

metadata：

```text
subject
category
topic
source
```

---

##### T2.4 RAG 验收

测试：

```text
HashMap为什么线程不安全？

JVM有哪些内存区域？

synchronized和volatile有什么区别？
```

检查检索结果是否与问题相关。

Git：

```bash
git add .
git commit -m "feat: add computer science interview knowledge base"
```

---

#### 第四阶段：MySQL

##### T3.1 数据库连接

配置：

```text
MySQL
SQLAlchemy
Environment Variables
```

---

##### T3.2 User

字段：

```text
id
username
role
created_at
```

---

##### T3.3 Classroom

字段：

```text
id
name
subject
description
created_at
```

---

##### T3.4 ClassroomMember

字段：

```text
id
classroom_id
user_id
role
```

---

##### T3.5 Message

字段：

```text
id
classroom_id
user_id
role
content
created_at
```

---

##### T3.6 验收

必须能够：

```text
创建用户
创建课堂
加入课堂
发送消息
查询聊天记录
```

Git：

```bash
git add .
git commit -m "feat: add mysql persistence"
```

---

#### 第五阶段：Student / Teacher / Classroom

##### T4.1 Student

学生：

```text
User.role = student
```

---

##### T4.2 Teacher

教师：

```text
User.role = teacher
```

---

##### T4.3 Classroom

支持：

```text
Teacher
Student
AI
```

---

##### T4.4 Chat

请求：

```json
{
  "classroom_id": 1,
  "user_id": 100,
  "message": "HashMap为什么线程不安全？"
}
```

后端必须知道：

```text
谁
在哪个课堂
问了什么
```

---

##### T4.5 验收

```text
[ ] 教师可以进入课堂
[ ] 学生可以加入课堂
[ ] 学生可以聊天
[ ] 聊天记录与课堂绑定
[ ] 聊天记录与用户绑定
```

Git：

```bash
git add .
git commit -m "feat: add classroom and user roles"
```

---

#### 第六阶段：StudentProfile

##### T5.1 创建画像

字段：

```text
student_id
overall_level
learning_preference
knowledge_mastery
weak_points
```

---

##### T5.2 Profile API

```text
GET /students/{id}/profile
PUT /students/{id}/profile
```

---

##### T5.3 学习行为

记录：

```text
ask_question
repeat_question
request_explanation
```

---

##### T5.4 验收

可以创建：

##### Student A

```text
level = beginner
HashMap = 0.3
JVM = 0.2
```

##### Student B

```text
level = advanced
HashMap = 0.8
JVM = 0.9
```

Git：

```bash
git add .
git commit -m "feat: add student profile"
```

---

#### 第七阶段：个性化 Prompt

##### T6.1 获取 Profile

流程：

```text
student_id
 ↓
StudentProfile
```

---

##### T6.2 RAG

```text
question
 ↓
Retriever
 ↓
Context
```

---

##### T6.3 Prompt

组合：

```text
Profile
+
RAG
+
Question
```

---

##### T6.4 个性化回答

测试同一个问题：

```text
HashMap为什么线程不安全？
```

分别使用：

```text
Beginner
Advanced
```

比较：

```text
解释深度
术语
示例
追问
```

---

##### T6.5 验收

满足：

```text
[ ] AI能够读取Profile
[ ] AI能够读取RAG
[ ] Prompt能够组合二者
[ ] Beginner回答更基础
[ ] Advanced回答更深入
```

Git：

```bash
git add .
git commit -m "feat: implement personalized teaching prompt"
```

---

#### 第八阶段：Teacher Resource Agent

##### T7.1 Agent

创建：

```text
Teacher Resource Agent
```

---

##### T7.2 输入

```text
teacher_id
classroom_id
topic
```

---

##### T7.3 RAG

获取：

```text
topic
 ↓
相关知识
```

---

##### T7.4 输出

```json
{
  "title": "...",
  "objectives": [],
  "sections": [],
  "key_points": [],
  "examples": [],
  "questions": [],
  "summary": []
}
```

---

##### T7.5 验收

输入：

```text
HashMap
```

能够得到合理的教学 PPT 大纲。

Git：

```bash
git add .
git commit -m "feat: add teacher resource agent"
```

---

#### 第九阶段：前后端联调

##### T8.1 学生流程

```text
学生进入课堂
 ↓
发送问题
 ↓
POST /chat
 ↓
AI回答
 ↓
显示在聊天室
```

---

##### T8.2 教师流程

```text
教师进入课堂
 ↓
输入教学主题
 ↓
POST /resources/ppt-outline
 ↓
AI生成大纲
 ↓
前端展示
```

---

##### T8.3 联调问题

重点检查：

```text
CORS
JSON格式
字段名称
错误码
API路径
超时
LLM调用失败
```

---

##### T8.4 验收

```text
[ ] 学生聊天正常
[ ] AI回答正常
[ ] RAG正常
[ ] Profile生效
[ ] 教师资源生成正常
```

Git：

```bash
git add .
git commit -m "feat: integrate frontend classroom chat"
```

---

#### 第十阶段：测试

##### T9.1 API测试

使用 Postman。

测试：

```text
Health
Classroom
Student
Chat
Profile
Resource
```

---

##### T9.2 RAG测试

至少测试：

```text
HashMap
JVM
ConcurrentHashMap
```

记录：

```text
问题
检索结果
相关性
回答
```

---

##### T9.3 Profile测试

测试：

```text
Beginner
Advanced
```

相同问题的回答差异。

---

##### T9.4 Agent测试

测试：

```text
Student → Teaching Agent

Teacher → Resource Agent
```

---

#### 第十一阶段：性能和质量记录

记录：

```text
API响应时间
LLM响应时间
RAG检索耗时
总回答耗时
```

AI回答质量可以人工评估：

```text
准确性
相关性
完整性
个性化程度
```

不要求第一版实现复杂自动评测系统。

---

#### 第十二阶段：项目整理

##### T10.1 README

必须说明：

- 项目介绍
- 技术栈
- 系统架构
- 快速启动
- 环境变量
- API
- RAG
- StudentProfile
- Agent
- 项目截图

---

##### T10.2 API文档

FastAPI Swagger + Markdown。

---

##### T10.3 架构图

至少包含：

```text
Frontend
   ↓
FastAPI
   ↓
Service
   ↓
Agent
 ┌─┴──┐
RAG Profile
 └─┬──┘
   ↓
 LLM
```

---

##### T10.4 项目报告

重点描述：

1. 用户画像
2. RAG
3. Prompt
4. Agent
5. 个性化学习
6. 教师资源生成
7. 系统测试

---

##### 最终 MVP 验收清单

##### 后端

- [ ] FastAPI
- [ ] MySQL
- [ ] User
- [ ] Student
- [ ] Teacher
- [ ] Classroom
- [ ] Message
- [ ] StudentProfile

##### AI

- [ ] LLM
- [ ] RAG
- [ ] ChromaDB
- [ ] Teaching Agent
- [ ] Teacher Resource Agent
- [ ] Personalized Prompt

##### 前后端

- [ ] Student Chat
- [ ] Teacher Resource Generation
- [ ] Chat History

##### 测试

- [ ] API
- [ ] RAG
- [ ] Profile
- [ ] Agent
- [ ] 基本性能记录

##### 文档

- [ ] README
- [ ] API 文档
- [ ] 架构图
- [ ] 测试报告
- [ ] 项目报告

---

##### 开发优先级

如果时间不足，按照以下顺序保功能：

##### P0 必须完成

```text
FastAPI
MySQL
Classroom
Student
Teacher
Chat
LLM
RAG
StudentProfile
Personalized Prompt
```

##### P1 应该完成

```text
Teaching Agent
Teacher Resource Agent
PPT Outline
Chat History
```

##### P2 有时间再做

```text
视频推荐
自动更新 mastery
复杂学习分析
知识图谱
复杂多 Agent
真实 PPT 文件生成
```

如果进入最后一周仍有 P0 未完成：

> 立即停止 P1/P2 功能开发，优先完成 P0。

---

##### Git 提交规范

每完成一个独立阶段提交一次。

示例：

```bash
git add .
git commit -m "chore: import and verify base tutor project"

git commit -m "feat: add computer science knowledge base"

git commit -m "feat: add mysql persistence"

git commit -m "feat: add classroom and user roles"

git commit -m "feat: add student profile"

git commit -m "feat: implement personalized teaching"

git commit -m "feat: add teacher resource agent"

git commit -m "feat: integrate frontend chat"

git commit -m "test: add api and rag tests"

git commit -m "docs: update project documentation"
```

每次提交后确认：

```bash
git status
```

工作区没有意外修改后再进入下一阶段。

---

##### Codex 执行规则

Codex 每次只能执行当前任务。

不得一次执行多个未验收阶段。

每个任务完成后必须输出：

```text
## 完成内容

## 修改文件

## 新增文件

## 测试方法

## 测试结果

## 已知问题

## 下一步
```

只有当前任务通过验收，才能开始下一个任务。
---

---

<a id="frontend-plan"></a>

## 2、原名 FRONTEND_PLAN.md

### AI Classroom 前端设计说明（C 部分）

> 面向作业分工中 C（系统集成与前端交互）的设计依据。
> 接口依据：`docs/specs/API_CONTRACT.md`（后端已冻结，16 个接口 + SSE 事件表）。
> 变更记录：`docs/worklogs/C_WORKLOG.md`。运行方式：`Tutor-Chatbot-main/frontend/README.md`。

---

#### 1. 一句话

后端已经把「建用户 / 建课堂 / 聊天 / 画像 / 备课」全部做完了，
前端要做的是**把这 16 个接口组织成四个界面**：一个入口、一个课堂列表、一个聊天室、一个教师工作台。
技术栈不新选，沿用仓库里已有的 Next.js + TypeScript + Tailwind。

---

#### 2. 形态与页面

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

#### 3. 接口 → 界面映射

| 模块 | 前端做什么 | 调用接口 |
| --- | --- | --- |
| 进入前 | 建用户 · 加课堂成员 | `POST /users`、`POST /classrooms/{id}/members` |
| 聊天室 | 拉历史 · 提问（SSE 流式） | `GET /classrooms/{id}/messages`、`POST /chat` |
| 画像侧栏 | 读画像 · 成员列表 | `GET /students/{id}/profile?classroom_id=`、`GET /classrooms/{id}/members` |
| 教师工作台 | 建课堂 · 生成大纲 · 下载 PPT · 课堂记录 | `POST /classrooms`、`POST /resources/ppt-outline`、`GET /resources/files/{name}` |

未使用的接口：`PUT /students/{id}/profile`（调试用）、`GET /behaviors`、
`GET /prompt-preview`（前端暂未接，调提示词时可直接用 Swagger）、`POST /upload`（P2）。

---

#### 4. 关键技术点

##### 4.1 身份态（后端没有鉴权）

后端不校验身份，前端是唯一的「身份真相源」：

```ts
// localStorage: ai-classroom/identity
{ userId: 3, username: "张三", role: "student", classroomId: 1 }
```

登录 = 输入昵称 + 选身份 → `POST /users`（幂等）→ 落 localStorage。
**权限判定看课堂内 role，不是 users.role**（契约第 7 条）。

##### 4.2 SSE 解析（严格按契约）

```ts
buffer += decoder.decode(value, { stream: true });
const frames = buffer.split("\n\n");     // 必须 \n\n，不是 \n
buffer = frames.pop() ?? "";
```

四类事件分别处理：`sources`（首个，带资料切片）、`token`、`state`（可能没有）、`error`。
未知 `type` 静默忽略 —— 后端先加事件、前端后接是安全的。

##### 4.3 请求体白名单

`ChatRequest` 开了 `extra="forbid"`，**多传一个字段直接 422**。
所以 `/chat` 只传三个字段：`message` / `classroom_id` / `user_id`。
走库时服务端自己恢复历史与教学状态，前端**不应**维护 `topic` / `hint_level`。

##### 4.4 群聊「实时」

契约没有 WebSocket。方案：**发送后立即拉一次 + 5 秒轮询** `GET /messages`。
成本低、演示效果够；若后端后续加 SSE 广播端点，只需替换这一处。

##### 4.5 客户端外壳

| 能力 | 做法 |
| --- | --- |
| 加载静态导出 | 自定义 `app://` 协议映射到 `out/`；`/_next/` 等静态资源缺失时**如实 404**（不回退 index.html，否则白屏无提示） |
| 静态态跨源 | 主进程给本机后端的 XHR/fetch 响应补 CORS 头，**不改后端 `.env`** |
| 下载 PPT | `will-download` 自动落「下载」文件夹并选中文件 |
| 虚拟机 GPU 崩溃 | 默认 `disableHardwareAcceleration` + `--use-angle=swiftshader`；`--enable-gpu` 可恢复 |

---

#### 5. 已知边界（写报告时要说明）

| 边界 | 说明 |
| --- | --- |
| 多场景不支持 | 作业要求 1v1 / 小组讨论 / 辩论对抗，后端 `/chat` 是固定流水线、无 router 与角色参数。前端目前只渲染「1v1 讲解 + 群聊共享上下文」 |
| sources 不落库 | 「依据」只在流式那一刻存在，刷新后消失（后端未存该字段） |
| 无鉴权 | 身份存本机 localStorage，换机器就丢；这是后端现状，不是前端设计选择 |
| 课堂列表在本机 | 后端无 `GET /classrooms`，见第 2 节 |

---

#### 6. 待 A 拍板

1. `/chat` 是否加 `scenario` 参数、`token` 事件是否带 `agent_role` —— 决定前端能否做多角色对话流。
2. `messages` 表是否存 `sources` —— 决定「依据」能否在刷新后保留。
3. 是否补 `GET /classrooms`（按 user 过滤）—— 决定「我的课堂」能否跨机器。

---

#### 7. MVP 范围与验收清单

> 用户 2026-10-21 明确：**先把最小可执行的前端做出来，新功能（含鉴权）后面再加。**
> 本节就是那条线 —— 在第 1 节范围内的都做完了，第 2 节的一律没碰。

##### 7.1 本期做（已落地）

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

##### 7.2 本期不做（后续加时动哪里）

| 功能 | 为什么现在不做 | 后续要动哪里 |
| --- | --- | --- |
| **登录鉴权** | 后端 16 个接口**没有鉴权**，前端造不出来；硬做会变成"假登录" | ① 后端加 `/auth/*` 与 token 校验；② `lib/api.ts` 的 `request()` 统一带 `Authorization`；③ `lib/identity.ts` 改存 token，不再存裸 `userId`；④ 各页面启动时校验登录态（现在只读 localStorage）；⑤ SSE 用 fetch 发请求，`lib/chat.ts` 也要同步加头 |
| PDF 上传问答 | 契约里标为可选（3 次/小时，内存会话 1 小时过期） | `lib/api.ts` 加 `POST /upload`；聊天室加附件按钮，`session_id` 随 `/chat` 透传 |
| 行为流水 / 讲法预览 | 调试用，学生界面不需要 | 教师工作台加「行为时间线」页签；`GET /behaviors`、`GET /prompt-preview` **已在 `lib/api.ts` 封装好**，直接调即可 |
| 多场景（1v1 / 小组讨论 / 辩论对抗） | 后端 `/chat` 是固定流水线，无 router、无角色参数 | 见第 6 节第 1 条：需 A 先加 `scenario` + `agent_role` |
| 打包成安装包 | 演示只需本机打开窗口 | 加 `electron-builder`；`out/` 与 `electron/` 的结构已经具备条件 |

##### 7.3 一条原则

加新功能时**先看 `docs/specs/API_CONTRACT.md`**。契约里没有的能力（鉴权就是典型），
先找 A 定接口，再动前端 —— 否则前端会先长出一套跟后端对不上的"影子协议"，
这正是本项目一开始踩过的坑（原版前端传 `provider=groq` 而后端只认 `openai`）。

---

<a id="rag-tuning-plan"></a>

## 3、原名 RAG_TUNING_PLAN.md

### RAG 系统性调优方案

> owner：B（Prompt / RAG 方向）｜ 2026-10-27
> 本文所有数字均为本轮**实跑**所得，脚本与命令附在每节末尾，可复现。
> 与本文配套的基线/结论文档：`docs/specs/RAG_HANDBOOK.md`（现行参数口径）、
> `docs/handoff/HANDOFF.md`（未决项 U1–U11）、`docs/worklogs/B_WORKLOG.md`（逐次变更记录）。

---

#### 0. 结论摘要

| # | 瓶颈 | 实测证据 | 优化项 | 预期收益 | 优先级 | 风险 |
|---|---|---|---|---|---|---|
| B1 | 重排读 parent 全文，占检索端到端 **97.7%** | 端到端 mean 2149ms，重排 2098ms，最长 9.5s | **重排改打 child 命中片**（O1） | 耗时待 A/B；判别力↑、floor 余量↑ | **P0** | 低（开关可关） |
| B2 | ~~库内题被 floor 误杀~~ → **已证伪，见 §2 B2** | 3 条零注入里 2 条库内真无内容、1 条仅在 topic 缺失时发生 | 保持 floor；补语义鸿沟观察（O2 降级） | — | P2 | — |
| B3 | 评估链路两次串行 LLM 排在检索**之前** | 抽 topic 1.24s + 理解评估 2.08s = 3.31s；O1 上线后 TTFT 几乎没动，说明它才是第一杠杆 | **理解评估与检索并行**（O3） | 前置段 −0.94~1.76s（−23%~−36%）；**已落地，见 §7.7** | ~~P0（TTFT 第一杠杆）~~ **已完成** | 中（改 /chat 主链路；已有 `CHAT_PARALLEL_ASSESS=0` 回退） |
| B4 | 注入体积偏大且无上限 | mean 1366 字 / max 2550 字（≈1900 token） | 注入窗口裁剪（O4） | 生成段 −0.2~0.4s、成本 −20~30% | P1 | 中（可能切碎答案） |
| B5 | 切分/召回参数未做过扫描 | 200/40 与 k=6 均为一次性选定 | 离线参数扫描（O5） | 召回率待实测 | P1 | 低（离线，不动生产） |
| B6 | 无任何结果缓存，命中率未知 | 83 条用例 79 个不同 query | 检索结果缓存 + 命中埋点（O6） | 命中才省 2.1s；**需先埋点** | P1 | 低 |
| B7 | 检索/重排/生成的分段耗时不可观测 | 只有 `rerank_ms` 一个埋点 | 分段埋点（O7） | 让 B1–B4 可持续追踪 | P1 | 低 |

---

#### 1. 链路全景与实测基线

```
/chat 请求
  │
  ├─① 评估链路（2 次串行 LLM，必须在检索前，因为检索要用它抽出的 topic）
  │     extract_topic        mean 1237ms
  │     assess_understanding mean 2077ms          ← 与检索无依赖，可并行（B3）
  │
  ├─② 检索链路（asyncio.to_thread，同步执行）
  │     文档路由 L1（BM25 全库打分 + 宽向量召回 → 收窄到 3 篇）  mean   35ms
  │     粗召回：向量 k=6（带 source filter）     ┐
  │              BM25 k=6（同源收窄）            ├ 合计   15ms
  │             RRF(k=60) 融合 → 父子聚合        ┘
  │     重排 bge-reranker-v2-m3-int8，CPU，batch=1  mean 2098ms  ← 97.7%（B1）
  │     两级下限：整题 -2.0 / 逐条 -5.0                          ← 误杀（B2）
  │
  ├─③ 上下文拼装：format_knowledge_docs → 3 条 parent
  │     mean 1366 字 / max 2550 字（估算 1024~1912 token）       ← 体积（B4）
  │
  └─④ 生成：ChatOpenAI(deepseek-flash, t=0.4) 流式
        首字 mean 1624ms
```

**端到端实测（真实 /chat SSE，`scripts/measure_ttft.py`，4 条探针）：**

| 指标 | mean | max |
|---|---|---|
| 前置段（评估 + 检索） | 7765ms | 11762ms |
| 首字 TTFT | 9388ms | 13412ms |
| 生成段（sources → 首 token） | 1624ms | 2071ms |
| 请求总时长 | 11238ms | 15299ms |

**检索链路实测（`scripts/profile_rag_pipeline.py`，20 条探针 × 2 轮，248 片库）：**

| 阶段 | mean | p50 | p95 |
|---|---|---|---|
| 检索端到端 | 2149ms | 2007ms | 3444ms |
| 文档路由 | 35ms | 38ms | 73ms |
| **重排** | **2098ms** | 1970ms | 3362ms |
| 其余（embed + 召回 + 融合 + 格式化） | 15ms | — | — |

候选池与注入：`coarse_k=6`、`bm25_k=6`、`fused_k` mean 4.5（min 3）、`final_k` mean 2.5（max 3）。

**判据**：重排占检索端到端 97.7%，其余各段加起来 1.6% + 0.7%。所以任何"优化召回/融合"的
努力在本库规模下的天花板是 **50ms 量级**；要动延迟只能动重排（B1）与它前面的串行 LLM（B3）。

---

#### 2. 瓶颈定位与判断依据

##### B1 重排是绝对瓶颈，且输入文本选错了对象

- 现象：重排 mean 2098ms，占 97.7%；单条最长 9.5s（「SELECT * 为什么不推荐？」）。
- 依据：`profile_rag_pipeline.py` 分段计时；`measure_ttft.py` 的前置段 7.8s 与该值吻合。
- 判断：父子模式在 `aggregate_parent_docs()` 里把正文换成**整节 parent（600~900 字）**，
  交叉编码器成本随序列长度增长（本机 CPU ONNX int8，无 GPU），长序列直接放大耗时。
- 为什么不是别的原因：embedding 查询、BM25 打分、RRF 全部加起来只有 15ms；
  加大 `coarse_k` 也已被既有实测否决（366 片模拟下 k=6/12/24 的 top1 完全相同）。

##### B2 「零注入 = floor 误杀」——**此判断已被证伪，保留过程备查**

我最初的假设是：长 parent 稀释分数 → 库内题被 `-2.0` 误杀。自查后**假设不成立**，
下面是证伪过程与真实归因：

- 第一步：关掉 floor 看实际命中——三条都能命中"看起来对"的文档，所以初判为误杀。
- 第二步：改成 child 打分后重测，三条**依然零注入**（−3.177 / −4.962 / −3.669）。
  若根因是长文本稀释，child 短文本必然把分数拉上来——没有。假设被否。
- 第三步：直接查语料。**`knowledge/cs/db/` 下「失效」零命中、「SELECT *」零命中**——
  这两条是课程没讲的超纲题，零注入是**正确拒答**，不是缺陷。
- 第四步：「快排的时间复杂度是多少？」库里有内容（`sorting.md` 写了"平均 O(n log n)"），
  只是**没出现"时间复杂度"这个词**——语义鸿沟。且它只在 `topic=''` 时误伤：
  带上评估节点抽出的 topic（`排序算法`）后 top=−0.823、正常注入 3 条。
  线上评估节点会补 topic，所以实际风险低。

**修正后的结论**：`relevance_floor=-2.0` 在本库是基本安全的（与 `diag_floor_margin.py`
在 83 条上的"库内零误伤"一致）。真正值得跟的是两件小事：
① 语义鸿沟（问法用词 ≠ 文档用词，U10 同类）；② 评测集要补"短术语 + 属性"问法，
否则下次还会把超纲题误判成误杀。O2（floor 重标定）因此**降级为 P2**。

##### B3 评估链路两次串行 LLM 排在检索之前

- 现象：`measure_assessment.py` 实测抽 topic mean 1237ms、理解评估 mean 2077ms，合计 3314ms。
- 依据：前置段 7765ms − 检索 2149ms ≈ 5.6s 的其余开销与该值同量级（含 DB 与个人化读写）。
- 判断：`assess_understanding_node` 只产出 `hint_level / misconception / resolved`，
  **检索只用 `topic`**，两者无数据依赖 → 可以并行。这是纯结构问题，不是模型慢。
- **已解决（2026-10-27，O3 落地）**：前置段 −0.94~1.76s，实测见 §7.7。
  注意收益上限是 **min(理解评估, 检索)**——并行后耗时由较长的一侧决定，
  较长那侧省不掉；所以再往下的空间不在"继续并行"，而在减少评估 LLM 的调用次数或耗时。

##### B4 注入体积无上限

- 现象：注入 mean 1366 字、max 2550 字（估算 1024~1912 token）。
- 依据：`profile_rag_pipeline.py` 的字符统计。
- 判断：3 条 parent 全文里，真正回答问题的往往只是其中一段。生成段 1.6s 与 token 成本
  都与它正相关。但**裁剪有切碎答案的风险**，必须 A/B，不能直接上。

##### B5 切分与召回参数未做过扫描

- 现象：`RAG_CHILD_CHUNK_SIZE=200 / OVERLAP=40`、`coarse_k=6` 均为一次性选定，
  从未跑过参数网格。
- 判断：不做扫描就无法回答"200/40 是不是最优"。好消息是这类实验可以**离线做**
  （另建 collection，不动生产库），成本只是重建与跑评测的时间。

##### B6 无结果缓存，且命中率从未测过

- 现象：83 条评测集里 79 条 query 各不相同（重复率 5%）。
- 判断：评测集不代表课堂真实分布（多个学生问同一道题、同一人反复追问更常见），
  所以**缓存收益必须先埋点再决定**，不能凭"应该会命中"就上。

##### B7 分段耗时不可观测

- 现象：`trace` 里只有 `rerank_ms` 与 `router_ms`；embed / 召回 / 融合 / 格式化没有埋点；
  端到端只有本次临时脚本能拆。
- 判断：B1 之所以到今天才被量出来，就是因为此前没有任何脚本对检索做分段计时。
  量具要先于调参落地，否则下次换模型/换库又会盲调。

---

#### 3. 优化方案（逐项：做法 / 预期影响 / 优先级 / 风险 / 回退）

##### O1 重排改打 child 命中片（"小块打分、大块注入"）

- 做法：父子聚合时把命中那片 child 的正文存进 `metadata["child_text"]`；
  新增 `RAG_RERANK_TEXT=parent|child`（**默认 parent，行为不变**）；
  重排读 `rerank_input(doc)`，顺序与注入仍用 parent 全文。
- 状态：**已实现并已跑 A/B**（`scripts/ab_rerank_text.py`，83 条全集，结果见 §6.1）。
- 预期影响：重排耗时↓（短序列）、top 分数↑ → floor 余量变宽 → 库内零注入↓、
  端到端 p95↓、top1 持平或↑。**不影响**：召回集合、注入文本、索引、金标准坐标。
- 优先级：**P0**。风险：低（环境变量归 `parent` 即回退）。

##### O2 floor 重标定（依赖 O1）

- 做法：O1 落地后重跑 `diag_perdoc_floor.py` / `diag_floor_margin.py`，
  按新的分数分布重选 `-2.0 / -5.0`；候选判据是"库内零注入 = 0 且库外零注入不劣化"。
- 预期影响：召回率（库内题注入成功率）↑、忠实度↑（不再拿空资料硬答）。
- 优先级：**P0（在 O1 之后）**。风险：中——改判据会改变"注入了哪些资料"，
  `HANDOFF` §"换 floor 必须重跑 L3/L4"，需要重刷 L3 基线与 L4 分母。
- 回退：环境变量 `RAG_RELEVANCE_FLOOR` / `RAG_PER_DOC_FLOOR` 改回原值。

##### O3 理解评估与检索并行 —— **2026-10-27 已落地并转默认，见 §7.7**

- 做法：把 `assessment_graph.ainvoke` 拆成两步——先 `extract_topic_node` 拿 topic，
  再 `asyncio.gather(assess_understanding_node(...), 三路检索)`。
  `assessment_graph` 保留（测试与编排层仍在用），新增分步入口
  `agent/graph.py:run_assessment_with_parallel`。
- 预期影响：**TTFT −2.08s（−22%）**，首字延迟与端到端耗时↓；对召回率/准确率**零影响**
  （理解评估不读任何检索产物，只是顺序变了，输入没变）。
- 优先级：**P0**。风险：中——改动落在 `/chat` 主链路。
- **回退：`CHAT_PARALLEL_ASSESS=0`**（一行环境变量，走回「评估整段跑完再检索」）。
  留开关不只是为 A/B，而是主链路出问题时要能一行退回，不靠改代码重发。

##### O4 注入窗口裁剪

- 做法：注入时不用整节 parent，改为"命中的 child ± 相邻 1 片"或按字数上限截断
  （如每片 ≤500 字），配 `RAG_INJECT_MAX_CHARS`，默认 0（不裁剪）。
- 预期影响：生成段 −0.2~0.4s、每轮 token 成本 −20~30%、p95↓；
  风险是**忠实度/准确率下降**（答案被切断）。
- 优先级：P1。风险：中。验证：必须同时跑 L1（nDCG）+ L3（幻觉率/资料利用率），
  L3 任一指标退化即否决。

##### O5 切分与召回参数离线扫描

- 做法：另建 collection（不碰 `chroma_kb`），跑网格
  `child_size ∈ {150,200,300,400} × overlap ∈ {0,40,80} × coarse_k ∈ {6,10,16}`，
  指标用 Recall@10 / nDCG@4 / 注入相关率。
- 预期影响：召回率与 nDCG 可能↑；成本为一次性重建 + 评测时间。
- 优先级：P1。风险：低（离线）；落地时风险中（改切片 = 指纹变化 = 重刷 L1 金标准）。

##### O6 检索结果缓存

- 做法：LRU + TTL，key = `库指纹 + query + topic + 生效参数集合`；
  `clear_bm25_cache()` 的同款时机清空（热重载/重建）。
- 预期影响：命中时省 2.1s 检索 + 重排算力；未命中时仅多一次哈希。
  **命中率未知 → 必须先埋点**（O7），跑够样本再决定是否保留。
- 优先级：P1（埋点）/ P2（启用）。风险：低（TTL + 指纹失效）。

##### O7 分段埋点与常态化体检

- 做法：`trace` 增加 `embed_ms / vector_ms / bm25_ms / fuse_ms / inject_chars`；
  把 `profile_rag_pipeline.py` 与 `measure_ttft.py` 挂进周跑，输出进 `eval/reports`。
- 预期影响：不直接改性能，但让 B1–B4 后续可追踪、可回归。
- 优先级：P1。风险：低（只加字段）。

##### O8 查询改写（HyDE / 多查询扩展）——**建议不做**

- 判断：本库 top1 已 59/61、route@3 71/71，剩余错误是**排序**问题不是**召回**问题；
  查询改写每次要额外付 1 次 LLM（HyDE）或 N 次 embedding，而 B1 显示检索侧
  真正贵的是重排（2.1s）。投入产出比明显劣于 O1/O3。
- 优先级：P3。风险：高（改召回语义，需重刷全部基线）。

##### O9 embedding 模型升级（bge-small-zh → bge-base-zh / 多语言）

- 判断：可在**离线库**上用 O5 同一套指标验证，不动生产。若 Recall@10 提升 <3pp，
  不值得付 512→768 维的存储与重建成本。
- 优先级：P2（先离线验证）。风险：中（换模型 = 全量重建 + 指纹变化 + 重刷基线）。

---

#### 4. 需要采集的数据（缺这些就无法判定优化是否真的有效）

| 数据 | 采集方式 | 用途 | 现状 |
|---|---|---|---|
| 分段耗时 embed/向量/BM25/融合/重排/格式化 | `trace` 扩充（O7） | 定位下一次瓶颈 | 只有 `rerank_ms`/`router_ms` |
| 每轮注入字符数 / 估算 token | `trace["inject_chars"]`（O7） | 成本与生成延迟归因 | 仅临时脚本 |
| 真实提问的**重复率**与近义率 | `/chat` 埋点：query 归一化后计数，命中/未命中分别统计 | 判定 O6 缓存值不值 | 无 |
| 库内题被 floor 拒成零注入的清单 | `diag_floor_margin.py`（已可跑，退出码 1 = 有硬误伤） | O2 阈值重标定 | 有，但未周跑 |
| 真实课堂的 TTFT 分布（非探针） | `measure_ttft.py` 扩成周跑，取样真实课堂消息 | O3 收益验证 | 仅 4 条探针 |
| 学生侧满意度（回答"有没有帮上"） | 产品埋点（超出 RAG 范围） | 唯一能证伪"指标变好但体验没变"的数据 | 无 |

**评测集现状**：`rag_eval_cases.json` 83 条（kb-interview 35 / in-kb 26 / follow-up 8 /
cascade 8 / oos 4 / cross 2），金标准为 LLM 三票细化后的 `relevant_chunks` 增益档
（1 / 0.5 / 0），`gold_granularity` + `gold_index_fingerprint` 随库绑定。
**已知缺口**：`oos` 4 条里有 2 条已被扩库覆盖而失效（U6）；"短术语 + 属性"类问法
（快排时间复杂度、索引失效）**不在集内**，正是 B2 漏检的原因。

---

#### 5. 指标与门禁（沿用现有四层，本文只补增量）

| 层 | 指标 | 门禁 | 本次新增关注 |
|---|---|---|---|
| L1 检索 | top1 / Recall@k / Precision@k / MRR / **nDCG@k** / p95 | nDCG 不设硬门禁但必须不退化；p95 ≤5s（父子） | **库内零注入条数**（新增，必须为 0） |
| L2 重排 | nDCG(有重排) − nDCG(无重排) | 必须 > 0 | 重排 mean 耗时（新增，作为 O1 的直接判据） |
| L3 生成 | 幻觉率 / 矛盾率 / 资料利用率 | 幻觉率 ≤0.15 | 资料利用率用于判 O4 裁剪是否切碎答案 |
| L4 端到端 | SSE 真实链路 / 库外 0 注入率 | 不进 CI，周跑 | **TTFT 前置段/生成段拆分**（新增，O3 判据） |

**新增的两个判据值得单独强调**：
1. **库内零注入条数**：召回率指标看不到"整题被判成库外"，而这是学生体感最差的一类失败。
2. **注入相关率**（XB-27 引入）：多样性这类"看起来对"的指标必须有它做对照，
   否则会出现"来源变多了、资料变差了"还以为在修问题的情况。

---

#### 6. 实验对比与回归验证方案

##### 6.1 O1 的 A/B（已跑）

脚本：`scripts/ab_rerank_text.py`，同一进程切 `CONFIG.rerank_text`，83 条全集，
对比 parent（现状）与 child（候选）。判据按优先级：
① top1 不退化 → ② 库内零注入不增加 → ③ 重排耗时↓ → ④ 注入相关率不退化。

> 结果见 §7（脚本输出原文回填）。

##### 6.2 通用回归顺序（任何一项改动落地都要走）

1. `./venv/Scripts/python.exe run_tests.py`（当前基线 **562/562**）——零回归是底线。
2. 针对性 A/B 脚本（`ab_rerank_text.py` / `ab_source_quota.py` / `diag_floor_margin.py`）。
3. L1 全量：`scripts/smoke_rag.py`（top1 / nDCG / p95），与 `eval/reports` 里的最近一次对比。
4. **判指纹**：`eval/fingerprint.py`，索引指纹仍须为 `bbe0cdc4`、248 片。
   指纹没变 ⇒ L3/L4 基线不必重跑（手册 §"换库或换 floor 才重跑 L3/L4"）。
5. 端到端：`scripts/measure_ttft.py`，前置段与 TTFT 与本文 §1 基线对比。
6. 只有动了「注入哪些资料」的改动（O2 / O4 / O5 落地）才追加 L3 + L4，并重刷基线值。

##### 6.3 上线的收口方式

- 全部改动走**环境变量开关**，默认保持现状；A/B 通过后再改默认。
- 每个开关在 `env.example` 里写明"为什么默认关/开"，避免后人手痒。
- 否决的改动也要写进 `HANDOFF` 与 `B_WORKLOG`（含对照数据），防止三个月后重提。

---

#### 7. O1（重排改打 child）实测结果

##### 7.1 全量 A/B（`scripts/ab_rerank_text.py`，83 条，同进程切开关）

| 指标 | parent（原行为） | **child（候选）** | 差值 |
|---|---|---|---|
| top1 命中 | 59/61 = 96.7% | **60/61 = 98.4%** | **+1** |
| 注入相关率 | 169/215 = 78.6% | 160/208 = 76.9% | −1.7pp |
| 零注入（库内 / 库外） | 0 / 6 | **0 / 5** | 0 / −1 |
| 重排 mean | 9216ms | **4627ms** | **−49.8%** |
| 重排 p95 | 13452ms | **6030ms** | −55.2% |
| 检索端到端 p95 | 13.789s | **6.180s** | −55.2% |
| top 分数 mean / min | 3.27 / −5.45 | **3.71** / −8.32 | +0.44 |

##### 7.2 顺序偏差排查（必须做，否则上面的 −50% 不可信）

A/B 两组是先后跑的，且期间我还并行跑了别的任务。**后跑的一组天然占便宜**（缓存预热、
并发负载下降），所以又用独立进程、反向顺序复跑了一遍（`profile_rag_pipeline.py`，
20 条探针 × 2 轮，先 child 后 parent）：

| 顺序 | 重排 mean | 检索端到端 mean |
|---|---|---|
| child（先跑） | **4787ms** | 4968ms |
| parent（后跑） | 8212ms | 8390ms |

child 仍然快 **41.7%**，与 A/B 的 49.8% 同向同量级 → **结论不是顺序效应**。
（绝对值是高负载下的数字，不能与 §1 低负载基线的 2.1s 直接比；比值可用。）

##### 7.3 L1 同口径对照（这是决定取舍的关键，不能只看 A/B）

A/B 只看耗时与命中，还差一层：**排序质量**。用 L1 官方口径
（`scripts/smoke_rag.py --uplift`，75 题 / 计分 71）跑同口径对照：

| 配置 | **nDCG@4** | top1 | 重排提升度 | 重排 mean |
|---|---|---|---|---|
| parent（原行为） | **0.8818** | 68/71 | +0.0657 | 8212ms |
| child（7.4 时的新默认） | 0.8549 | 68/71 | +0.0388 | 4787ms |
| 差值 | **−0.0269（−3.0%）** | 0 | −0.0269 | −41.7% |

> 7.4 是**阶段结论**，保留原样以记录推理。随后追加的第三档 `child_title` 严格更优，
> **当前默认已改为 `child_title`**，见 §7.5——读完 7.4 请接着看 7.5，别只按这里下结论。

也就是说：child 换来了**重排耗时减半**，代价是**排序质量 −3%**，且
**重排本身的边际价值缩水 42%**（提升度 +0.0657 → +0.0388，因为短文本的判别力本来就弱些）。
top1 两个口径下都没退化（L1 68/71 持平；A/B 的 61 题口径 +1）。

##### 7.5 第三档 `child_title`：把取舍变成双赢（2026-10-27 追加）

7.4 的结论是"用 −3% nDCG 换 −50% 耗时"，那是个取舍。但 child 丢质量的原因很具体：
**它丢掉了「这段属于哪个主题」**。`parent_title`（小节标题）恰好是整节里信息密度
最高的几十字，把它拼到命中片前面，只需多付 ~10% 序列长度。

新增第三档 `child_title`，用同一套 L1 门禁口径（各档**独立进程**串行，避免顺序效应，
`scripts/l1_p95_by_mode.py`）实测：

| 档位 | top1 | top3 | **nDCG@4** | 重排提升度 | 平均 | p95 | 3.0s 门禁 |
|---|---|---|---|---|---|---|---|
| `parent`（整节全文） | 68/71 | 70/71 | **0.8818** | +0.0657 | 2.384s | 3.655s | ✗ 超 0.655s |
| `child`（命中片） | 68/71 | 70/71 | 0.8549 | +0.0388 | 0.963s | 1.103s | ✓ |
| `child_title`（片+标题） | 68/71 | 70/71 | **0.8651** | **+0.0490** | 0.973s | **1.134s** | ✓ |

**判定：`child_title` 严格优于 `child`——多付 1% 耗时（0.963→0.973s），
换回 parent→child 那 0.0269 排序损失中的 0.0102，即 38%。** 提升度
也从 +0.0388 回到 +0.0490（重排的边际价值回升 26%）。

**已转默认 `RAG_RERANK_TEXT=child_title`**。此时相对 parent 的完整账是：
耗时 −59%、p95 −69%、top1/top3 **完全不变**、nDCG 仅 −1.67%、p95 从"超预算 0.655s"
变成"低于预算 1.87s"。这个代价可以接受，延迟收益远高于代价 ⇒ **执行**。

配套：单测扩到 13 条（新增 child_title 拼标题 / 标题已内联不重复拼 / 无标题退化 /
无 child_text 回退），`env.example` 写明三档对照与回退警示。

##### 7.6 延迟预算冲突的解决（原 §8 第 1 条的未决项，已闭环）

冲突是：代码 `eval/e2e_runner.py:GATE_L1_P95 = 3.0`（单一常量）
vs 手册「父子 ≤5s / 扁平 ≤3s」（分档）。

**这不是"哪个数字对"的问题，是分档的前提已失效**：手册设分档的理由是
「父子模式重排打整节 1000+ 字，照抄 3s 会永久不达标」。child_title 落地后，
重排读的是 ~250 字，父子与扁平的成本回到同量级（实测父子 p95 **1.134s**），
分档不再有存在理由。

⇒ **统一回 3.0s，删除分档**。已在三处落盘：
`eval/e2e_runner.py`（常量注释写明由来与回退警示）、`docs/specs/RAG_HANDBOOK.md §3`
（门禁列改统一 + 分档由来/失效说明 + 三档对照表）、本文件 §7.5。
⚠ 回退成 `parent` 会让 p95 回到约 3.65s 而判红——预期内，回退前先看对照表。

**注意 1**：A/B 只证明了"打分输入"这一项。§2 B2 里我以为会顺带解决的零注入问题
**并没有被解决**（三条依旧零注入），那是另一回事，已在 B2 修正。

**注意 2（重要，别被 −50% 误导）**：把 O1 落到线上后复测端到端 TTFT，
**几乎没变**（mean 9388ms → 9321ms，前置段 7765ms → 7191ms，均在噪声内）。
原因是前置段的主体是**评估链路两次 LLM（3.31s）与网络往返**，重排省下的 1~2s
被 LLM 耗时波动吃掉了（四次复测的生成段在 0.5~3.2s 之间大幅摆动）。

⇒ 结论：**要压 TTFT，第一杠杆是 O3（评估与检索并行，−2.08s），不是 O1**。
O1 的价值在于把检索侧 p95 从 13.8s 压到 6.2s（高并发与慢机器上的差距会放大），
以及省掉一半重排算力，而不是单点首字。

---

#### 7.7 O3（理解评估与检索并行）实测 —— 已落地并转默认

**改法**：`/chat` 不再整段跑 `assessment_graph.ainvoke`，改为
`抽 topic → gather(理解评估, 三路检索)`。依据是依赖分析：理解评估只读
`topic / topic_changed / hint_level / messages`，**不读任何检索产物**，
两者之间没有数据依赖，串起来是白等。分步入口 `agent/graph.py:run_assessment_with_parallel`。

**A/B（`scripts/measure_ttft.py`，各 8 条，各重启一次后端，正反序各一轮）**：

| 轮次 | 组 | 前置段 mean | TTFT mean | 生成段 mean |
|---|---|---|---|---|
| 正序（并行先） | 并行 | **3126ms** | **4429ms** | 1302ms |
| 正序（并行先） | 串行 | 4061ms | 5900ms | 1838ms |
| 反序（串行先） | 串行 | 4883ms | 7488ms | 2605ms |
| 反序（串行先） | 并行 | **3122ms** | **4737ms** | 1614ms |

**判定：并行在前置段上稳定快 0.94~1.76s（−23%~−36%），且两轮方向一致**。
一个很有说服力的旁证：**并行侧的数值高度稳定（3126 / 3122ms），串行侧波动大
（4061 / 4883ms）**——并行后前置段由「评估 LLM」单一因素决定，检索被完全藏在其下；
串行则是两段相加，两边的抖动都叠加进来。这正是理论预期：
**并行收益 = min(理解评估, 检索)，较长那侧省不掉**。

**读数纪律（别把噪声算成收益）**：TTFT 差值（−1.47~−2.75s）比前置段差值更大，
但**生成段也差了 0.5~1.0s**，而 O3 根本不碰生成——说明存在组间噪声（机器负载、
限流排队）。所以 **前置段差值才是 O3 的直接证据**，TTFT 的节省里有一部分不是 O3 带来的。
方案原文预估「TTFT −2.08s」，与实测同量级，但读的时候应当以前置段为准。

**落地清单**：
- `agent/graph.py`：`run_assessment_with_parallel()` + `AssessmentFailed`。
  **错误分类是刻意的**——评估失败包成 `AssessmentFailed`（调用方转 502），
  检索失败原样抛出（转 500）。并行化最容易犯的错就是把两类失败混成一种，
  届时 detail 会指向「模型不可用」，而真实故障在知识库。
- `app.py`：`_retrieve_all()`（三路检索并发）+ `CHAT_PARALLEL_ASSESS` 开关（默认 1）。
- `tests/test_assessment_parallel.py`：**9 例**，其中两条是核心——
  ① 耗时接近 max 而非 sum（并行真的发生了，串行也能"看起来对"）；
  ② 分步结果与 `assessment_graph.ainvoke` 等价（两个入口并存，等价性靠单测锁住）。
- 真实课堂冒烟：课堂 #17，两条带 `classroom_id` 的请求均 200、sources=3、
  topic 正确抽出（'死锁' / 'b+树索引'）——`classroom` 那路检索也在并行分支里，已验。
- `run_tests.py` **584/584 OK**。回退：`CHAT_PARALLEL_ASSESS=0`。

---

##### §7.8 「追问场景缓存 topic」可行性评估（XB-31，2026-10-27）

**命题**：/chat 关键路径是 `抽 topic（E ~1.08s）→ gather(理解评估 A ~2.08s, 三路检索
R ~1.0s)`。E 是 A 与 R 的**共同前驱**，纯串行。于是想：**追问时 topic 没变，跳过 E
直接沿用上一轮**，省 1.08s。

**先说一个容易漏的结构性结论**：盲缓存与「投机执行」的**延迟收益完全相同**，
但前者牺牲正确性、后者不牺牲。所以只要决定要省这 1.08s，就该做投机执行，
没有理由做盲缓存。下面三条是支撑这个判断的实测。

**实测 1 —— 收益天花板：追问句上 E 判「沿用」的比例（`diag_topic_cache.py`，27 例）**

| 用例集 | n | E 的判定 | 含义 |
|---|---|---|---|
| 省略式追问（"那怎么预防呢？""为什么不是两次？"） | 12 | 判沿用 **11/12 = 92%** | 缓存命中率上限 |
| 换题句（含"那死锁呢？""那 TCP 和 UDP 呢？"句式陷阱） | 15 | 判换新 **15/15 = 100%** | 盲缓存必然误伤的比例 |

E 单次 mean **1079ms / 中位 1107ms**（比 §7.7 里记的 1.24s 略快，同量级）。

**实测 2 —— 真实日志里的沿用率（messages 表，224 条 assistant）：相邻两轮
topic 不变 = 119/211 = 56.4%**。但课堂间差异极大（#5/#6/#7/#14 只有 11%，
#4 达 82%）——脚本批量生成的序列几乎每轮换题，真实深挖型课堂则以追问为主。
⇒ **p 只能按课堂类型分档估计，不能用一个全局数**；取 0.56 定期望：省约 0.61s
（前置段的 19%）。

**实测 3 —— 误伤的伤害（换题句上，用上一轮 topic 检索 vs 用新 topic 检索）**

| 结果 | 条数 | 说明 |
|---|---|---|
| 完全一致（无害） | 1 | 原话主题词足够强，topic 没起作用 |
| **期望文档仍命中但被稀释** | **12** | 注入 4 条里 2 条是旧知识点（如"讲讲虚拟内存？"+topic=死锁 → 2×memory + 2×deadlock） |
| **期望文档完全丢失（有害）** | **2** | "HTTPS 和 HTTP 有什么区别？"+topic=进程与线程 → 全是 process-thread |

⇒ **换题轮上 93% 会出现不同程度退化，13% 完全答错知识点**。叠加 p_switch≈43.6%，
即**约 41% 的请求会拿到被污染的上下文**，换 0.61s 的期望延迟。

**实测 4 —— 「用本地 embedding 相似度判沿用」已否决（O10）**
想法：把 1.08s 的 LLM 判定换成 ~4ms 的 `cos(embed(消息), embed(上一轮 topic))`。
实测分布（bge-small-zh）：

| | n | min | p25 | 中位 | p75 | max | mean |
|---|---|---|---|---|---|---|---|
| 追问句 | 12 | 0.186 | 0.210 | 0.293 | 0.381 | 0.593 | 0.311 |
| 换题句 | 15 | 0.176 | 0.303 | 0.351 | 0.422 | 0.538 | 0.355 |

**两类几乎完全重叠**（追问 max 0.593 > 换题 max 0.538），最佳阈值 t=0.472 下准确率
仅 **59.3%**，比"一律判换题"（55.6%）只高 3.7pp。**原因很本质**：省略式追问之所以是
追问，正因为它**不含主题词**——向量上它离任何主题词都远，和换题句无法区分。
⇒ **O10 不可行，不用再试。**

**结论与建议排序**

1. **盲缓存（跳过 E）：不建议。** 唯一的风险升级是「topic 会被落库、并被下一轮
   `load_last_state` 读回去」，还会写进 `_update_profile` 的 mastery —— 一次误判不是
   只脏一轮，而是**跨轮累积且静默**（D11）。0.61s 换 41% 请求上下文退化，不划算。
2. **投机执行（推荐，P1）**：`gather(E, A(prev_topic), R(prev_topic))`；E 返回 same
   就直接采用，换题则取消重跑。**延迟收益与盲缓存完全相同，正确性不打折**；代价是换题轮
   上一次被中止的评估调用（约 52% 进度，是否计费取决于 provider，需确认）。
3. **先做 P0：把 E 本身做快。** 1.08s 换一个词，明显是 API 侧 TTFT + prefill 主导。
   ① 精简 prompt（现含 8 个示例，输出只有几个 token，省的是 prefill）；
   ② 显式 `max_tokens`；③ 必要时换更快模型。**这条对 100% 请求生效（不只追问），
   且零正确性风险，应该排在投机执行前面。**
4. **结果缓存（可选叠加）**：key = (上一轮 topic, 最近 N 条历史哈希, 消息)；只对高频
   固定追问有效，且必须随 topic 变化失效，收益有限、维护成本不低，暂不推荐。

**复现**：`./venv/Scripts/python.exe scripts/diag_topic_cache.py`（`--no-llm` 只跑第 1 部分）。

---

##### §7.9 O11 落地：抽 topic 关掉推理模型的思考（XB-32，2026-10-27）

**为什么不是砍 prompt**：§7.8 已经证伪盲缓存，于是先试"把 E 做快"。第一反应是
prompt 太长（现含 8 个示例 + 5 条规则，707 字），于是做了三变体 A/B
（`ab_extract_prompt.py`，各 27 例 × 2 轮正反序）：

| 变体 | 相对现行改了什么 | mean | 中位 | 准确率 |
|---|---|---|---|---|
| `base`（现行） | —— | **1004ms** | 946 | 96.3% |
| `slim` | 去掉 8 个示例、规则压缩 | 1170ms（+17%） | 1110 | 96.3% |
| `slim_win3` | slim + 历史 6→3 条、300→120 字 | 1242ms（+24%） | 1202 | 96.3% |
| `static_first` | 内容不变，静态段整体挪到动态段前 | 1402ms（+40%） | 1422 | 96.3% |

**三个精简变体全都更慢**。这说明"prefill 长度是瓶颈"的假设不成立。

**真正的瓶颈（分解实测，`probe_llm_fixed_cost.py`）**：把 prompt 从 6 字拉到 4040 字，
耗时 926 → 850ms**几乎不变**；边际 prefill 仅 **21.6ms/千字**，把现行 prompt
砍掉一半只省 **7ms**。同时 token 用量暴露了真相：

    completion_tokens=25   completion_tokens_details.reasoning_tokens=23

**输出的 25 个 token 里 23 个是 reasoning**。`deepseek-flash` 是推理模型，
哪怕只要它回 "same" 一个词，它也要先想 20~30 个 token——那 1.0s 主要是想出来的。

**做法**：只给**抽取节点**单独 `bind(extra_body={"thinking": {"type": "disabled"}})`。
理解评估 A **不关**——判断学生是否真懂，让它想是值得的。

**A/B（`ab_extract_prompt.py`，base vs base_nothink，各 27 例 × 3 轮正反序）**：

| 变体 | mean | 中位 | p95 | 判定准确率 |
|---|---|---|---|---|
| base（保留思考） | 1012ms | 940 | 1509 | 96.3% |
| **base_nothink** | **656ms（−35%）** | 647 | **878（−42%）** | **97.5%（+1.2pp）** |

判定质量不降反升：81 例里决策不一致仅 1 条（`那挥手为什么要四次？`：
base 判成新 topic「TCP 四次挥手」，关思考后判成 `same`——**关掉后更对**，
因为上一轮 topic 就是「TCP 三次握手」，四次挥手本属同一知识点下）。

**端到端复核（`ab_ttft_nothink.py`，各 8 条 + 1 条预热丢弃）**：

| 组 | 前置 mean | 中位 | p95 | TTFT mean |
|---|---|---|---|---|
| 关（保留思考） | 2861ms | 2794 | 3418 | 4396ms |
| 开（关思考） | **2817ms** | **2572** | **2900** | **4090ms** |

mean 只差 44ms（−1.5%，落在噪声里，n=8 分辨力不足），但**中位 −222ms、
p95 −518ms** 与节点级结论同向。**报数时以节点级为准**（81 样本/组），
端到端只作方向性确认——前置段还有 A（2.08s）这个更长的极在压着。

**为什么留开关**：`thinking` 不是标准 OpenAI 字段，换厂商（groq/gemini/ollama）
会报未知参数。所以 `RAG_TOPIC_NO_THINK=0` 可一行回退；且带**失败自动降级**
（带参数调用抛错 → 用原参数重试一次），降级会打 WARNING，不会让 /chat 挂掉，
也不会每轮重复白送一次失败请求（按模型记在 `_NO_THINK_UNSUPPORTED`）。

**验证**：`tests/test_topic_no_think.py` **9 例**（bind 参数 / 关闭时不 bind /
降级重试 + 留痕 / 降级记忆 / 原句柄失败照旧上抛 / 判定口径不受影响）；
`run_tests.py` **593/593 OK**（584 + 9）。真实课堂追问冒烟：课堂 #18 两轮
`讲讲死锁？` → `那怎么预防呢？` 均 200、topic 一致抽出 `死锁`、sources=3、
日志中**无降级告警**（说明 DeepSeek 确实接受该字段）。

**文件**：`agent/graph.py`（`TOPIC_NO_THINK` / `_topic_llm()` / 降级分支）、
`tests/test_topic_no_think.py`（新）、`scripts/ab_extract_prompt.py`（新）、
`scripts/probe_llm_fixed_cost.py`（新）、`scripts/probe_thinking_off.py`（新）、
`scripts/ab_ttft_nothink.py`（新）、`env.example`。

**未做的事**：理解评估 A 关思考没试——它是 2.08s 的更长极，潜在收益更大
（可能 −1s 级），但需要先有 A 的质量判据（resolved / hint_level 是否与
人工一致），否则无从判断"变快了但变差了"。

---

##### 7.10 给 A 装尺子 + A 关思考 A/B + 投机执行（2026-10-28，XB-33）

三步连做。前两步是"先造尺子再动刀"，第三步是换一条不伤正确性的提速路。

###### 7.10.1 尺子（第 1 步）——先说一个查库时才发现的尴尬

想给 A 做标注集，第一反应是"从历史对话里抽"。抽完发现：messages 表里
**162 条 student 话语几乎全是提问**（"什么是死锁？""那怎么预防呢？"），
没有一句是"学生陈述自己理解到什么程度"。

这不是数据没存够——是**日志里本来就没有这类样本**。A 的判断对象（学生听完
讲解后的回应）在历史对话里根本没出现过。所以：
- 真实日志可以回答"线上 resolved 判准率是多少"，但构造不出 balanced 的判据集
- 判据集只能手写，代价是**它覆盖难度分布，不覆盖频率分布**

这一点写进了标注集的 `limitations` 字段，避免以后有人拿它当线上指标用。

**产出**：
- `eval/data/assess_gold.json` — 30 条人工标注，6 类难度
  （明确表示懂了 6 / 复述关键结论 5 / 典型误解与沾边跑偏 8 / 不知道 4 / 探索性追问 4 / 自述仍混 2 / 含糊 1）
- `eval/assess_quality.py` — 三指标评测：resolved 准确率、误解检出一致率、hint 方向一致率
- `scripts/export_assess_candidates.py` — 从库里抽候选（就是它暴露了上面的问题）

**怎么做到不改动生产代码就能切档**：`assess_understanding_node` 直接调
`state["llm"].ainvoke(...)`，所以外面把 state 里的 llm 换成
`llm.bind(extra_body={"thinking": {"type": "disabled"}})` 的句柄即可。

###### 7.10.2 A 关思考的 A/B（第 2 步）——结论是**不转默认**

正反序各 2 轮、每档 60 样本，两次独立运行结果高度复现：

| 档位 | resolved | 误解检出 | hint方向 | strict | mean | p95 |
|---|---|---|---|---|---|---|
| think（现行） | 96.7% | **83.3%** | 80~83% | **70.0%** | 2410~2674ms | 5770~6000ms |
| nothink | 96.7% | **73.3%** | 80~83% | **63.3%** | 764~772ms | 975~1107ms |

延迟 **−68%**，很诱人。但被尺子拦住了：**误解检出 −10pp**。

看逐条差异，退化不是"漏掉误解"，而是**过度检出**——把"完全没思路，老师
直接讲吧"记成"学生不了解 ReadView 判断可见性的机制"。

这条不能放行，因为它正好撞在 D11 那个坑上：`misconception` 会喂给
`_update_profile` 记 mastery，画像被记错之后**从回答文本上看不出来**。
用 1.6s 换一个静默的画像污染，不划算。

**保留结论**：A 节点继续用思考模式。想再试的话，得先解决"怎么让关思考
的模型别把'不知道'写成'误解'"——可以在 prompt 里加一条显式约束重测。

###### 7.10.3 投机执行（第 3 步）——已落地并转默认（O12）

做法：`gather(E, A(prev_topic), R(prev_topic))`，E 判沿用就直接采用，
判换题则作废重跑。延迟收益与"盲缓存 topic"完全相同，但 E 仍是唯一裁决者。

**三层验证**：

| 层次 | 结果 | 说明 |
|---|---|---|
| 单测（固定延迟） | **250ms vs 507ms** | 干净的因果证明：差 257ms ≈ 注入的 E=250ms |
| 节点级配对（大间隔） | **4097→3140ms（−23.3%）**，12/15 更快 | 避开限流后收益兑现，与理论 24% 吻合 |
| 节点级配对（小间隔） | 命中组 −170ms，14/24 更快 | 见下方风险 |

**⚠ 一个必须写下来的发现：限流会吃掉投机收益**

同样的代码，把请求间隔从 0.8s 拉到 9s（远离 10/min 限流阈值），
命中路径的收益从 −170ms 变成 +956ms。

原因是投机执行把 3 个请求（抽 topic + 评估 + 检索）**同时**打出去，
在限流环境下更容易排队。所以在**限流宽松**的部署里收益明确，
在**限流严格**的部署里可能持平甚至倒退 —— 而且会额外消耗一次被
取消的评估调用（是否计费取决于厂商）。

当前环境限流 10/min 属于后者，因此：
- 开关 `CHAT_SPECULATE_TOPIC` 保持**默认 1**（正确性零损失，收益环境相关）
- 若上线后发现限流紧张，置 0 一行回退到 O3 分步并行

**文件**：`agent/graph.py`（`run_assessment_speculative` / `_cancel_all`）、
`app.py`（`CHAT_SPECULATE_TOPIC` / `_runner` 选择）、
`tests/test_assessment_speculative.py`（新，11 例）、
`eval/data/assess_gold.json`、`eval/assess_quality.py`、
`scripts/ab_speculative_node.py`、`scripts/ab_speculative.py`、
`scripts/export_assess_candidates.py`、`env.example`。

**测试基线**：604/604（593 + 11）。

---

#### 8. 需要外部决策的信息（其余配置已自行读码取得）

已确认的现行配置（无需再问）：向量库 Chroma `PersistentClient`（248 片，单 collection
`cs_basics`，指纹 `bbe0cdc4`）；embedding `BAAI/bge-small-zh-v1.5`；切分父子
child 200/40 + `##` 小节 parent；召回 路由收窄 3 篇 → 向量 k=6 + BM25 k=6 → RRF(k=60)；
重排 `BAAI/bge-reranker-v2-m3-int8`（CPU，batch=1）；下限 -2.0 / -5.0；注入 3 条 parent；
生成 `ChatOpenAI(deepseek-flash, temperature=0.4)`。

仍需拍板的三项（都不是能靠读码得到的）：

1. ~~延迟与排序质量怎么权衡~~ **已闭环（2026-10-27，§7.5 / §7.6）**：
   追加第三档 `child_title` 后不再是取舍——它在 child 的耗时上拿回 38% 排序损失，
   已转默认。同时延迟预算冲突也解了：分档前提（重排打 parent 全文）已失效，
   统一回 3.0s。若日后教学口径仍是"宁可慢也要准"，`RAG_RERANK_TEXT=parent` 一行回退，
   代价与收益见 §7.5 对照表（并注意 p95 会判红）。
2. **成本口径**：O4 裁剪会把准确率（可能）换成 token 成本，两者权重由产品定。
3. **`oos` 题集要不要修**：4 条里 2 条已失效（U6），修了要重算 L1 金标准与 L4 分母。

---

#### 7.11 O13：召回率 / 准确率归因与实测（2026-10-28，XB-35）

起因：指标卡上「召回率 78.27% / 准确率 55.87%」两项长期未达标（目标 85% / 60%）。
这一节记录**归因过程与全部实测档位**，包括四条负结论——不做记录的话，
下一个人会按同样的直觉再试一遍，白烧几小时。

##### 7.11.1 先把天花板算出来，再谈目标

`scripts/ab_recall_pool.py` / `eval/metrics_report.gold_caps()`：

| 注入窗口 K | 召回率上限 | 准确率上限 |
|---|---|---|
| 3 | **0.9159** | **0.6573** |
| 4 | 0.9760 | 0.5387 |
| 5 | 0.9891 | 0.4408 |

两个结论直接改掉了原优化方向：

1. **K=4 是死路**。准确率上限随 K 增大反而掉到 0.539 —— 准确率目标 0.60
   在 K≥4 时**数学上不可能**，注入条数只能是 3。
2. **K 固定时召回与准确率不是跷跷板**。二者分子是同一个「命中的增益和」，
   比值恒为「金标准增益和 / K」（本评测集约 0.71）。所以任何提高召回的改动
   都会按比例带上准确率——报告里原先把它们写成此消彼长是错的，已改正。

目标 85% / 60% 分别是各自上限的 **92.8% / 91.3%**；改动前实测是上限的
85.5% / 85.0%。要达标就得拿到九成以上的上限，而金标准是 AI 标注、平票取低档。

##### 7.11.2 缺口三段拆解（`scripts/diag_pool_loss.py`，不跑重排）

Recall 缺口 0.9159 − 0.7827 = 13.3pp，拆成三段：

| 段 | 大小 | 含义 |
|---|---|---|
| 路由丢的 | **−2.2pp** | 路由是**帮忙的**（收窄到 3 篇后池上限 0.9021 > 不路由 0.8798），放宽到 5/8 篇反而掉回 0.8798 |
| 粗召回丢的 | 6.9pp | coarse_k 6→16，池上限 0.8336 → 0.9021 |
| 重排挑错的 | 5.1pp | 池里有的金标准片没被挑进 top-3 |

按题型看（71 题）：`follow-up` 最惨，池损失 0.254（其余 0.04~0.08），
8 条 × 0.174 / 71 ≈ 2.9pp 的全局缺口都在这一类上。

##### 7.11.3 逐档实测（71 题、父子模式、注入 3 条，每档独立进程）

| 档位 | Recall | Precision | nDCG | p95 |
|---|---|---|---|---|
| 基线（-5.0） | 0.7827 | 0.5587 | 0.8651 | 1.075s |
| **per_doc_floor → −8.0** | **0.7909** | **0.5634** | **0.8700** | **1.081s** |
| per_doc_floor → off | 0.7909 | 0.5634 | 0.8653 | 1.160s |
| child_overfetch=3（池 4.2→9.2） | 0.7847 | 0.5634 | 0.8690 | 1.567s |
| overfetch=3 + 关下限 | 0.7883 | 0.5657 | 0.8713 | 1.564s |
| 相邻小节扩展 ±1 | 0.7806 | 0.5563 | 0.8641 | 1.521s |
| 相邻小节扩展 ±2 | 0.7834 | 0.5587 | 0.8664 | 1.685s |
| rerank_text=parent_head(480) | 0.7306 | 0.5399 | 0.8080 | 1.869s |
| 重排×RRF 融合 w=0.5 | 0.7782 | 0.5563 | 0.8568 | 1.571s |
| 重排×RRF 融合 w=1.5 | 0.7565 | 0.5352 | 0.8309 | 1.681s |

四条负结论（**别再试**）：

1. **盲目扩粗召回无效**。池从 4.2 放大到 9.2 条，池上限涨 6.9pp，但重排挑错同步
   涨 6.7pp，净收益 ≈ 0。重排器的判别力才是瓶颈，喂更多候选只会给它更多犯错机会。
2. **相邻小节扩展无效**。理论上更划算（每多送一条进重排换来的池上限是盲目扩召回的
   1.27 倍：7.37 条 → 0.8884 vs 9.24 条 → 0.9021），但实测仍低于不扩——
   扩出来的邻居同样会挤掉 top-3 里的金标准片。
3. **给重排更长的文本反而更差**。`parent_head`(480 字) 掉到 0.7306，top1 从 68 掉到 64。
   交叉编码器看整节并没有比看「片+标题」更能区分「这一节是否回答了这个问题」。
4. **重排名次与 RRF 名次融合无效**，w=0.5 / 1.5 都更差——RRF 名次给不了重排缺的判别力。

##### 7.11.4 唯一转默认的改动：per_doc_floor −5.0 → −8.0

`-5.0` 这个值是 **inject_k=4 的扁平模式**下标定的：那时准确率分母是 4，
砍掉一条垃圾能直接让分子占比上升（实测 +6.2pp）。父子模式 inject_k=3 后，
分母恒为 3，**砍掉零增益片不再带来任何准确率收益，只剩误杀的成本**。
−8.0 拿到了「关掉下限」的全部收益（Recall +0.82pp）却仍留兜底，且几乎不付延迟。

官方基线（`eval/reports/e2e_20261028_130646.json`，门禁 ✓）：

| 指标 | 改前 | 改后 |
|---|---|---|
| 召回率@3 | 0.7827 | **0.7909** |
| 准确率@3 | 0.5587 | **0.5634** |
| nDCG@3 | 0.8651 | **0.8700** |
| MRR / top1 / top3 | 0.9648 / 68 / 70 | 0.9648 / 68 / 70（不变） |
| p95 | 1.2322s | **1.1027s** |
| 重排提升度 | 0.049 | **0.0539** |

##### 7.11.5 剩下的路（都不是调参能解决的）

1. **换更强的重排模型**（唯一有量级希望的一条）。本机只有
   `bge-reranker-v2-m3-int8` 的缓存，换 fp16 或更大模型**需要联网下载**，
   且成本约 2~3 倍（当前重排 1.08s / p95 预算 3.0s，还有约 1.9s 余量，装得下）。
2. **人工复核金标准**。当前是 AI 标注、平票取低档（`_note: 待人工复核`），
   0.0 档里可能有假阴性；上限 0.9159 本身也可能被低估。
3. 报告卡已加「物理上限」列（11 列），召回率现在显示 `79.09% / 上限 91.59% /
   达成率 86.4%`，不再让读报告的人把 0.79 当成"离 0.85 差一大截"。

**文件**：`agent/rag/retriever.py`（`per_doc_floor` 默认 / `child_overfetch` /
`parent_pool_k` / `adjacent_radius` / `rerank_fusion_w` / `rerank_text=parent_head` /
`_get_parent_index` / `expand_adjacent_parents`）、`app.py`（热重载连清 parent 索引）、
`eval/metrics_report.py`（`ceiling` 列 + `CEILING_KEY_BY_METRIC` + CSV 表驱动）、
`scripts/`（`diag_recall_precision.py` / `diag_pool_loss.py` / `diag_rerank_choice.py` /
`diag_adjacency.py` / `ab_recall_pool.py`）、`tests/test_recall_pool.py`（新，16 例）。

**测试基线**：659/659（641 + 16 新 + 2 改）。

---

#### 7.12 指标体扩展：13 → 21 项（2026-10-28，XB-36）

起因：用户给了一张 RAGAS 风格指标表（成功率 / 检索层 / 生成层 / 上下文层），
要求参考它完善本项目指标。**完整过程与实测数据见 `docs/worklogs/B_WORKLOG.md` XB-36**，
这里只记两条会影响后续调优判断的结论。

##### 7.12.1 「先装尺子，再调参」

新增的**注入噪声率**（零增益条数 / K）立刻改写了 XB-35 的结论：

| per_doc_floor | 噪声率 | 召回率 | 平均注入条数 |
|---|---|---|---|
| −5.0 | 0.2582 | 0.7827 | 2.76 |
| −8.0（当前默认） | **0.3052** | **0.7909** | **2.96** |

XB-35 把 −8.0 记为「白捡 +0.8pp」，那是**没有噪声率这把尺子时得出的结论**——
实际是「每轮多注入 0.2 条垃圾换来的」。并且「收紧下限砍不掉噪声」也是错的：
收紧确实能砍 −4.7pp，代价是召回 −0.82pp。当前选召回优先，取舍已写进
该指标的 `basis`。

👉 往后任何"调参 → 看 recall/precision"的动作，都要先看噪声率，
   否则会把「换来的」当成「白捡的」。

##### 7.12.2 生成层的数暂时不能当结论

新增的答案相关性 / 答案正确率**首次实测全 1.0**（8 条），改用金标准切片正文
作参考答案后仍是 11/12 条判 1.0。根因是**裁判与生成同源**（都走 deepseek-flash），
裁判对自己风格的答案天然宽容。在两个指标的 `basis` 里已写明：
**接入异源裁判（`EVAL_JUDGE_MODEL`）或人工复核前，只能看趋势**。

另外 answer correctness 的参考答案**不能用知识点标签**（用例的 `expected`
只有"死锁"两个字），锚点太粗判不出「漏了半个要点」；现已改用金标准切片正文，
并在报告里报 `correctness_ref_gold` / `correctness_ref_labels` 两个计数，
让读报告的人知道锚点有没有退化。

##### 7.12.3 一个静默取空的坑

L4 报告是 `{"cascade": …, "e2e": …}` 两段，端到端指标在 **e2e 段**。
`_l4` 按顶层取会静默拿到 None → 报告明明跑了 L4，指标卡却显示「未运行」，
**且不报任何错**。已加两条单测锁死。凡是"分层报告 + 子段"的结构，
取值函数都要显式声明取哪一段。

---

<a id="rag-rerank-selection-plan"></a>

## 4、原名 RAG_RERANK_SELECTION_PLAN.md

### 重排（Rerank）模型选型与落地方案

> 适用项目：AI-Classroom / Tutor-Chatbot（Python + FastAPI + Chroma + fastembed）
> 文档性质：**可执行方案 + 指令集**。本文只给出步骤与命令，**不执行**任何评测/训练/部署。
> 所有命令块标注「待执行」，由执行方按阶段逐步运行。
> 现状基线：`eval/reports/e2e_20261028_130646.json`（71 题，父子模式，inject_k=3）

---

#### 0. 结论先行

##### 0.1 一句话结论

**当前瓶颈已定位为「重排器判别力」而非「候选池大小」——换模型是本轮唯一能继续撬动 Recall/精确率的方向；但在本机无 GPU 的前提下，首选路线是「先换更轻/更强的开源交叉编码器并用服务化运行时部署」，云端 API 只作为「判别力上限探针」参与离线评测，不直接进入生产链路。**

##### 0.2 三条硬约束（决定选型空间）

| 约束 | 实测值 | 来源 | 对选型的影响 |
|---|---|---|---|
| 重排占检索总耗时 | **98.0%**（rerank p95 **1082ms** / 检索 p95 **1102.7ms**） | L2 `rerank_p95_ms=1082.0`，L1 `p95_secs=1.1027` | 换模型≈直接改端到端延迟，线性放大 |
| L1 p95 门禁 | **3.0s**（现状 1.1027s） | `RetrievalConfig` 注释、`eval/slo_check.py` | 新模型耗时倍数上限 **3.0/1.08 ≈ 2.77×**，安全线取 **2.0×（≈2.16s）** |
| 端到端 p95 门禁 | **12s**（现状 10.404s） | `eval/reports/e2e_20261028_142722.json` | 端到端只剩 1.6s 余量，比 L1 更紧，**以 L1 3.0s 为准** |

##### 0.3 推荐首选与备选（不做最终绑定，待实测确认）

| 角色 | 方案 | 理由 | 触发条件 |
|---|---|---|---|
| **首选（主路线）** | `Qwen/Qwen3-Reranker-0.6B`（Apache-2.0，0.6B，32K ctx，100+ 语言） | 同体量下中文/多语言判别力强于 bge-m3；Apache-2.0 商用无碍；有 vLLM / TEI / Infinity 成熟服务化路径 | **必须**先过「延迟闸门」：CPU 直跑大概率超 2.0× 安全线，需 GPU 或 ONNX/量化 + 服务化后复测 |
| **首选同档兜底** | `Alibaba-NLP/gte-reranker-modernbert-base`（149M，8192 ctx，FlashAttention-2） | 体量极小，公开自部署榜单 Hit@1 达 83%（优于 bge-m3 的 77.3%）；CPU 上最可能跑赢现状 | 若 0.6B 在 CPU 上超预算，优先退到这一档 |
| **备选（成本最低）** | 维持 `BAAI/bge-reranker-v2-m3-int8`，但**换运行时**（fastembed→ONNX Runtime 直调 / TEI / Infinity） | 现状 int8 ONNX 已是量化版，剩余收益可能在**运行时与批处理**，而非权重本身 | 当 P1 显示「所有候选模型增益 < 1.5pp」时启用 |
| **备选（上限探针）** | 云端重排 API（Jina Reranker API / Cohere Rerank 4 Fast / 国内托管 bge-m3） | Jina API 约 188ms，**比本机 CPU 的 1082ms 快约 5.8 倍**，可直接回答「判别力天花板在哪」 | 仅在 P1 离线评测中使用，**不进生产**（数据出网 + 网络依赖） |
| **不推荐** | `Qwen3-Reranker-4B/8B`、`bge-reranker-v2.5-gemma2` | 8–32GB 显存需求，本机无 GPU；CPU 推理必然远超 3.0s 门禁 | 除非 Q1（GPU）确认可用 |

##### 0.4 预期收益区间（上限已被物理限制锁死）

- K=3 注入条数下，**Recall@3 物理上限 0.9159、精确率上限 0.6573**（金标准口径）。
- 现状 Recall **0.7909** / 精确 **0.5634** → **理论剩余空间仅 +12.5pp / +9.4pp**，且其中一部分来自金标准漏标，非重排可解。
- **现实预期：换模型带来 Recall@3 +1.5～4.0pp、nDCG@3 +0.02～0.05 即算成功**。超过 +5pp 应怀疑是评测集/金标准问题而非模型神迹。

---

#### 1. 现状盘点（执行前必读，避免重复踩坑）

##### 1.1 当前重排配置（`agent/rag/retriever.py` `RetrievalConfig`）

| 参数 | 默认值 | 环境变量 | 说明 |
|---|---|---|---|
| `rerank_model` | `BAAI/bge-reranker-v2-m3-int8` | `RAG_RERANK_MODEL` | fastembed `TextCrossEncoder` 加载 |
| `enable_rerank` | `True` | `RAG_RERANK` | 0 = 关重排 |
| `rerank_batch` | `1` | `RAG_RERANK_BATCH` | **CPU 下 batch=1 最快**（6 条：b1 0.73s / b2 0.90s / b4 1.52s） |
| `rerank_text` | `child_title` | `RAG_RERANK_TEXT` | 打分输入=子片+小节标题；`parent` 全文会致 p95 3.65s **超门禁** |
| `rerank_head_chars` | `480` | `RAG_RERANK_HEAD_CHARS` | `parent_head` 模式截断长度 |
| `rerank_online` | `False` | `RAG_RERANK_ONLINE` | 默认只读本地 `.model_cache` |
| `relevance_floor` | `-2.0` | `RAG_RELEVANCE_FLOOR` | 整题下限（logits 量纲） |
| `per_doc_floor` | `-8.0` | `RAG_PER_DOC_FLOOR` | 逐条下限（logits 量纲） |
| `coarse_k` / `coarse_k_cap` | `6` / `16` | — | 粗召回条数 / 池上限 |
| `parent_final_k` | `3` | — | 注入条数（**不可改**，精确率上限约束） |
| `child_overfetch` / `parent_pool_k` | `1`（关）/ `0`（不限） | `RAG_CHILD_OVERFETCH` / `RAG_PARENT_POOL_K` | 父子模式超额召回与重排池上限 |

##### 1.2 已实测基线（**不要重跑，直接引用**）

| 层 | 指标 | 值 |
|---|---|---|
| L1（71 题，K=3） | top1 / top3 | **68/71** / **70/71** |
| L1 | Recall@3 / Precision@3 / MRR / nDCG@3 | **0.7909** / **0.5634** / **0.9648** / **0.8700** |
| L1 | avg / p95 / 降级数 | 0.9639s / **1.1027s** / 0 |
| L2（重排增益） | nDCG on / off / uplift | 0.8700 / 0.8161 / **+0.0539** |
| L2 | top1 on / off | **68** / **63**（重排 +5 条） |
| L2 | rerank p95 | **1082.0 ms** |

**基线编号**（后续所有对比都必须挂这两个编号）：
- **B0 = 无重排**（`RAG_RERANK=0`，纯向量+BM25+RRF）：nDCG 0.8161、top1 63/71
- **B1 = 现状**（bge-m3-int8 + child_title + 池≈4.2 parent）：上表 L1 全量值

##### 1.3 已证伪的方向（**不要重复投入**）

1. **扩大候选池**：A/B 实测 0.7827 → 0.7712（**劣化**），瓶颈不在池大小。
2. **相邻小节扩展**（`adjacent_radius`）：补齐 `child_text` 后仍劣于不扩，已拒绝。
3. **RRF 融合权重**（`rerank_fusion_w` 0.5/1.5）：Recall 0.778/0.757 vs 纯重排 0.788，默认 0 不动。
4. **`per_doc_floor` 收紧到 -5.0**：Recall −0.82pp 只换噪声 −4.7pp，在 K=3 下不划算。

---

#### 2. 选型维度、权重与打分方式

##### 2.1 权重表（合计 100%）

| # | 维度 | 权重 | 为什么是这个权重 |
|---|---|---|---|
| D1 | **中文与多语言效果** | **30%** | 语料是中文八股技术文档，短查询占多数；多语言只作加分项（当前语料基本纯中文，含英文术语） |
| D2 | **延迟与吞吐** | **25%** | 重排已占检索 98%，延迟是硬闸门，超 3.0s 直接判死 |
| D3 | **GPU/显存与部署成本** | **15%** | 本机无 GPU，需新增硬件或服务的成本必须计入 |
| D4 | **开源许可与合规** | **10%** | 一票否决项，但权重不宜过高（大多数候选是 Apache-2.0） |
| D5 | **私有化 vs 云端 API** | **10%** | 教学资料出网是政策问题，不是技术问题 |
| D6 | **可维护性与社区活跃度** | **10%** | 决定长期能否跟上（量化版、服务化镜像、issue 响应） |

##### 2.2 打分方式（1–5 分，每档给客观锚点）

**D1 中文与多语言效果（30%）** — 以**本项目评测集实测**为准，公开榜单只作先验
- 5 = Recall@3 较 B1 **+3.0pp 以上** 或 nDCG@3 **+0.04 以上**
- 4 = Recall@3 +1.5～3.0pp
- 3 = Recall@3 0～+1.5pp（与现状持平）
- 2 = Recall@3 0～−1.5pp
- 1 = Recall@3 劣化 > 1.5pp
- *未实测时*：用公开中文榜（CMTEB-R / MTEB 中文子集）相对位次给**先验分**，实测后强制覆盖。

**D2 延迟与吞吐（25%）** — 以 `rerank_p95_ms`（6 条候选池，本项目配置）为准
- 5 = ≤ 400ms（较现状快 2.7 倍）
- 4 = 400～800ms
- 3 = 800～1600ms（含现状 1082ms）
- 2 = 1600～2160ms（超安全线但在 3.0s 门禁内）
- 1 = > 2160ms 或导致 L1 p95 > 3.0s

**D3 GPU/显存与部署成本（15%）**
- 5 = CPU 可跑且 ≤ 现状内存占用，零新增硬件
- 4 = CPU 可跑但内存/耗时增加 ≤ 50%
- 3 = 需 ≤ 4GB 显存的小卡（如 T4/A10G 切分）
- 2 = 需 8–16GB 显存
- 1 = 需 ≥ 24GB 显存或多卡

**D4 开源许可与合规（10%）**
- 5 = Apache-2.0 / MIT（可商用、可私有化）
- 3 = 自定义开放许可（如 Qwen License，商用需核对条款）
- 2 = CC-BY-NC（**非商用**，禁止商用自托管）
- 1 = 闭源 / 许可字段缺失或不确定
- **⚠ 一票否决**：D4=1 或 D4=2 且需商用自托管 → 直接淘汰，不参与加权

**D5 私有化可行性（10%）**
- 5 = 权重可下载、可离线部署、数据全程不出网
- 3 = 可私有化但依赖外部服务/镜像源
- 1 = 仅云端 API，文档必须出网

**D6 可维护性与社区活跃度（10%）**
- 5 = 主流框架原生支持（vLLM / TEI / Infinity / FlagEmbedding 均有官方路径）+ 近 6 个月有更新
- 4 = 至少一个主流框架原生支持
- 3 = 仅社区适配（如非官方 GGUF 转换）
- 1 = 无维护迹象或需自行适配

**加权总分**：`Σ(维度分 × 权重)`，满分 5.0。
**入选门槛**：总分 ≥ 3.5 且 D2 ≥ 3 且 D4 ≥ 3。

---

#### 3. 候选范围（不做最终绑定）

##### 3.1 A 类：开源、可私有化部署的交叉编码器

| 候选 | 体量 / 上下文 | 许可 | 硬件需求 | 适用场景 | 限制与风险 |
|---|---|---|---|---|---|
| **A1 `BAAI/bge-reranker-v2-m3`**（含 int8 现状版） | 568M / 512+ token | Apache-2.0 | CPU 可跑（现状 int8 ONNX，1082ms p95）；FP16 约 1.2GB 显存 | **兜底基线**；100+ 语言；生态最成熟（FlagEmbedding / sentence-transformers / 各框架内置） | 判别力已触及天花板（Recall 0.7909 距上限仅 12.5pp）；换它本身不产生增益，只能作为对照组 |
| **A2 `Qwen/Qwen3-Reranker-0.6B`** | 0.6B / 32K token | Apache-2.0 | FP16 约 1.2–1.3GB 显存；CPU 可回退但慢 | **首选主路线**：中文判别力强、长上下文、支持代码检索；vLLM / Xinference / TEI 有现成路径 | CPU 直跑大概率超 2.0× 安全线；官方无一手 GGUF（社区转换）；需重新标定 logits 阈值 |
| **A3 `Alibaba-NLP/gte-reranker-modernbert-base`** | 149M / 8192 token | 需核对官方 LICENSE 字段（见 R3） | 极小，CPU 友好 | **CPU 场景首选替代**：公开自部署 Hit@1 83%（优于 bge-m3 的 77.3%）；长上下文 | 较新，社区适配与量化版可能不足；许可待确认 |
| （备选观察）**A4 `jina-reranker-v3` / v3.5** | 约 0.6B / listwise 8K–32K | **许可存在冲突信息**：v2 权重为 CC-BY-NC-4.0，v3.5 有资料称 Apache-2.0 | 2–4GB | listwise 架构一次打分多文档，延迟优势明显（公开约 167ms） | **许可必须先核对官方仓库 LICENSE 字段**；若为非商用则排除商用自托管 |

##### 3.2 B 类：云端重排 API

| 候选 | 公开延迟 / 计价 | 适用场景 | 限制与风险 |
|---|---|---|---|
| **B1 Jina Reranker API** | 约 188ms；按 token 计费 | **延迟最优的 API 选项**；可作判别力上限探针 | 文档必须出网；网络依赖；免费额度有限 |
| **B2 Cohere Rerank 4 Fast / 4 Pro** | Fast 约 447ms / Pro 约 614ms；约 $0.05/1M tokens 或按次计费 | 判别力第一梯队（ELO 1510–1629）；Pro 在复杂语义上更强 | 约 600ms 对同步链路偏重；闭源、仅 API、锁定风险；出网合规 |
| **B3 Voyage AI rerank-2.5** | 约 613ms；$0.05/1M tokens（lite $0.02），首 200M tokens 免费 | 已有 Voyage 嵌入栈时最省事；单文档 16K token 上限 | 延迟与 Cohere 同档；出网合规 |
| **B4 国内托管（硅基流动 / 阿里云百炼 gte-rerank 等）** | 视厂商；国内网络可达 | 出网合规可接受时的**落地友好选项**（低延迟 + 人民币计价） | 厂商能力/稳定性需实测；模型版本不可自行锁定 |

> **B 类在本项目的定位**：只允许在 **P1 离线评测**中作为「判别力天花板探针」出现，用于回答「换模型究竟能涨多少」。除非 Q2（出网合规）明确放行，否则不进入 P2/P3。

---

#### 4. 评测方法

##### 4.1 评测集构建

**来源三层**（按优先级）：
1. **现有 71 题 gold**（`eval/` 默认评测集，含库外题 `oos`）— 必跑，保证与 B0/B1 可比。
2. **业务真实 query**：从课堂 `/chat` 日志抽样（去除 PII），按「高频问法 / 长尾问法 / 追问」分层抽样。
3. **难负例专项**：单独成组，用于拉开候选间差距。

**难负例四类（HN1–HN4）**，每条至少 15 例，合计 ≥ 60 条：

| 编号 | 类型 | 构造方式 | 为什么难 |
|---|---|---|---|
| HN1 | 同知识点异问法 | 同一 `##` 小节内不同 child 片互为负例 | 语义高度重叠，向量几乎无差别 |
| HN2 | 同文档相邻小节 | 目标节的 ±1 个 `##` 小节 | 主题一致但答案不在其中（用 `expand_adjacent_parents` 可批量生成） |
| HN3 | 同术语异义 | 如「时间片（RR）」vs「时间复杂度」；「TIME_WAIT 为什么等那么久」vs「SJF 为什么最优」 | 词面高度相似，BM25 高分但语义无关 |
| HN4 | 现有重排已挑错的样本 | 从 `scripts/diag_rerank_choice.py` 输出中提取「金标准未进 top3」的题 | 直接对标现状失败模式 |

**样本量要求**：
- 起步：**71 题**（现状 gold 全量），保证可比。
- 目标：**≥ 120 题**，其中 oos ≥ 15 条、难负例专项 ≥ 30 条。
- 统计灵敏度：71 题下 **1 题 ≈ 1.41pp**，故判定阈值取 **≥ 2 题 ≈ 2.8pp**（Recall@3）或 **nDCG@3 ≥ 0.02**；低于此视为噪声。
- 每题**重跑 3 次取中位数**（裁判/加载抖动），与现有 `--votes 3` 做法一致。

##### 4.2 指标定义（与项目现有口径对齐，不新造轮子）

| 指标 | 口径 | 现状值（B1） | 判定方向 |
|---|---|---|---|
| **Recall@K**（K=3） | 金标准切片被注入的比例 | 0.7909 | 越大越好 |
| **Precision@K**（K=3） | 注入条中金标准占比（分母恒为 inject_k=3） | 0.5634 | 越大越好 |
| **MRR** | 首条金标准名次倒数均值 | 0.9648 | 越大越好 |
| **nDCG@K**（K=3） | 折扣累计增益 | 0.8700 | 越大越好 |
| **首条命中率 Top1 Hit** | top1 即金标准的题目数 | 68/71 | 越大越好（**注意已近天花板，区分度低**） |
| **噪声率**（辅助） | 注入条中零增益条数 / K | 0.3052 | 越小越好 |
| **rerank p95** | L2 `rerank_p95_ms` | 1082.0 ms | ≤ 2160ms（安全线） |
| **L1 p95** | 检索端到端 | 1.1027 s | **≤ 3.0s 硬门禁** |
| **降级率** | `degraded_cases / total` | 0 | ≤ 2% |

##### 4.3 基线定义（必须同时打赢两个基线）

- **B0 无重排**（`RAG_RERANK=0`）：证明"重排这一步有价值" → 新候选 nDCG@3 必须 > 0.8161。
- **B1 现状**（bge-m3-int8）：证明"换得值" → 新候选 Recall@3 必须 ≥ 0.7909 + 1.5pp = **0.8059**。

##### 4.4 评测流程（8 步）

1. **锁指纹**：`eval/fingerprint.py` 记录索引指纹；任一候选跑之前校验指纹一致，不一致则本次结果作废（防"索引变了还对比"）。
2. **预热**：单跑一次候选模型，确认权重加载成功、无联网重试（`RAG_RERANK_ONLINE=0`）。
3. **单变量**：一次只改一个变量（模型 / 池大小 / 输入文本模式），其余钉死。
4. **分档独立进程**：沿用 `scripts/ab_recall_pool.py` 的做法，**每档开独立进程**跑，避免进程内缓存/显存串扰。
5. **重跑取中位**：每题 3 次，取中位数。
6. **与基线比**：同时输出与 B0、B1 的差值。
7. **门禁**：`eval/slo_check.py --p95 3.0 --degraded-rate 0.02`。
8. **落盘**：报告写入 `eval/reports/`，并在交付物中标注基线编号与指纹。

##### 4.5 待执行指令（**本轮不运行**）

> 前置：`cd Tutor-Chatbot-main/backend`，Python 用 `venv\Scripts\python.exe`（Windows）。
> 首次执行前先 `--help` 确认参数名（已知 `e2e_runner` 参数：`--cases --layers --sample --votes --base-url --pause --out`）。

```bash
# 步骤 0：确认基线可复现（B1），跑 L1+L2 全量 71 题
# 待执行
venv\Scripts\python.exe -m eval.e2e_runner --layers L1,L2 --out eval/reports/rerank_sel_B1.json

# 步骤 1：确认无重排基线（B0）
# 待执行（Windows 下用 set，Linux/macOS 用 export）
set RAG_RERANK=0
venv\Scripts\python.exe -m eval.e2e_runner --layers L1,L2 --out eval/reports/rerank_sel_B0.json
set RAG_RERANK=1

# 步骤 2：候选模型 A/B（需先实现 scripts/ab_rerank_model.py，见 §7 P1 交付物）
# 待执行 —— 每个候选独立进程、独立报告
set RAG_RERANK_MODEL=Qwen/Qwen3-Reranker-0.6B
venv\Scripts\python.exe -m scripts.ab_rerank_model --model Qwen/Qwen3-Reranker-0.6B --pool-k 6 --repeat 3 --out eval/reports/rerank_sel_A2.json

# 步骤 3：门禁判定
# 待执行
venv\Scripts\python.exe -m eval.slo_check --input eval/reports/rerank_sel_A2.json --p95 3.0 --degraded-rate 0.02
```

---

#### 5. 工程约束

##### 5.1 首阶段候选文档截断数量（top-K）

| 项 | 首阶段取值 | 理由 |
|---|---|---|
| 粗召回 `coarse_k` | **保持 6** | 实测 6/12/24 条 top1 完全一致，加大只涨延迟 |
| 重排池 `parent_pool_k` | **首阶段不限制（0）**，离线网格测 {4, 6, 8, 12} | 现状聚合后约 4.21 个 parent；池大小已被证伪为瓶颈，但换模型后需重扫一遍 |
| 注入条数 `parent_final_k` | **固定 3，不可改** | K=4 时精确率物理上限 0.5387 < 现状 0.5634 |
| 重排输入文本 | **`child_title`**（约 250 字） | `parent` 全文 → p95 3.65s **超 3.0s 门禁**；`child` 掉 1.67% nDCG；**`parent_head` 已于 2026-10-28 实测证伪**（Recall −3.34pp、nDCG −4.57pp、p95 +74%，见 §9.2） |
| 输入截断上限 | `rerank_head_chars=480` | 超过即截；**必须在 trace 里记截断事件**，避免"静默截断导致打分失真" |

**取舍建议**：换模型的第一轮**不要同时改池大小**（双变量无法归因）。池大小扫描放在确认模型之后再单独跑一轮。

##### 5.2 批处理与并发

| 部署形态 | batch | 并发 | 依据 |
|---|---|---|---|
| 本机 CPU（现状） | **1** | 串行 | 实测 6 条：b1 0.73s < b2 0.90s < b4 1.52s |
| 服务化 CPU（TEI/Infinity/ONNX Runtime） | 先测 1，再测 4/8 | 连接池 2–4 | 无 GPU 时批处理未必转为优势，必须重测 |
| GPU（如后续有卡） | **8–16** | 连接池 4–8 | 上 GPU 后批处理才转为优势（现状注释已预留） |
| 云端 API | 一次请求送**全部候选** | ≤ 4 并发 | listwise 模型一次打分多文档；拆开反而更慢更贵 |

队列等待超时：**300ms**，超时直接走降级路径。

##### 5.3 超时降级

| 层级 | 阈值 | 行为 |
|---|---|---|
| 软超时 | **800ms** | 记 warning，继续等待 |
| 硬超时 | **1500ms** | 中断重排 → **回落 RRF 原顺序** + `_mark_degraded(trace, "rerank_timeout")` |
| 加载失败 | — | `trace["rerank"]="unavailable"` + `_mark_degraded("rerank_unavailable")`（现有机制，直接复用） |
| 推理异常 | — | `trace["rerank"]="error"` + `_mark_degraded("rerank_error")`（现有机制） |
| 降级率预算 | **≤ 2%** | `eval/slo_check.py --degraded-rate 0.02` |

**关键纪律**：降级路径必须与现状一致（回落 RRF 顺序），保证「重排挂了 ≠ 检索挂了」。

##### 5.4 缓存策略

- **缓存粒度**：整池缓存（一次查询一条记录），而非 `(query, doc)` 对——候选池只有 4–6 条，逐对缓存收益低于开销。
- **缓存 key**：`hash(index_fingerprint + query_normalized + rerank_model + model_revision + pool_k + rerank_text + head_chars)`
  - 含 `index_fingerprint`：索引变更自动失效（复用 `eval/fingerprint.py`）
  - 含 `rerank_model`：换模型自动失效，**杜绝跨模型串分**
- **失效时机**：知识库热重载时清空（对齐现有 `clear_parent_index_cache()` 的清理时机）。
- **命中率目标**：≥ 30%。RAG 查询重复率远高于嵌入调用，命中后可把重排成本直接归零。
- **存储**：进程内 LRU（首阶段 2000 条即可，无需 Redis）。

##### 5.5 延迟预算内的取舍（决策树）

```
新模型单请求 p95（6 条池）
  ≤ 400ms   → 直接推进，可顺带把池扩到 8~12 换召回
  400~800ms → 推进，保持池 6
  800~1600ms→ 推进（等同现状区间），但必须证明 Recall +1.5pp 以上
  1600~2160ms→ 仅在 Recall 增益 ≥ 3.0pp 时推进，且需评估端到端 12s 门禁余量（仅剩 1.6s）
  > 2160ms  → 否决（触 3.0s 硬门禁安全线）
```

**补充取舍**：若某候选判别力明显更强但 CPU 超预算 → 走「**服务化 + GPU**」或「**云端 API**」，但两条路都要先过合规（Q2）与成本（Q4）。

---

#### 6. 落地路线（4 阶段）

##### P0 — 准备（1 个会话内完成）

| 项 | 内容 |
|---|---|
| **准入标准** | 基线 B0/B1 可复现（指纹一致）；评测集 v2 落盘（≥120 题，含 oos ≥15、难负例 ≥30） |
| **执行** | 跑 B0/B1 各一次；固化 `index_fingerprint`；导出难负例子集 |
| **交付物** | `eval/reports/rerank_sel_B0.json`、`rerank_sel_B1.json`、评测集 v2 JSON、指纹记录 |
| **回滚** | 无（只读阶段） |

##### P1 — 离线评测（**唯一决策阶段**）

| 项 | 内容 |
|---|---|
| **准入标准** | 至少一个候选同时满足：① Recall@3 ≥ **0.8059**（B1 +1.5pp）；② nDCG@3 > 0.8161（打赢 B0）；③ `rerank_p95_ms` ≤ **2160ms**；④ 降级率 ≤ 2% |
| **执行** | 候选 × 池大小网格（模型 {A2, A3, A4-若许可通过, B1-API} × pool {4,6,8,12}），每格独立进程、3 次取中位 |
| **交付物** | ① 新增 `scripts/ab_rerank_model.py`（按模型维度 A/B，输出 9 项指标 + 与 B0/B1 差值）；② 候选对比矩阵（含加权总分）；③ 阈值重标定报告（见 R2）；④ 选型结论（明确首选/备选） |
| **回滚条件** | 全部候选 Recall 增益 < 1.5pp → **不换模型**，转「备选：换运行时」（§0.3 第三条） |

##### P2 — 小流量灰度（影子模式）

| 项 | 内容 |
|---|---|
| **准入标准** | P1 通过；阈值（relevance_floor / per_doc_floor）已按新模型重标定并有 A/B 数据支撑 |
| **执行** | 5%–10% 流量（或仅内部课堂），**双跑影子模式**：新模型打分只记录不生效，与主链路结果对比 |
| **交付物** | 灰度报告（真实 query 下 Recall/Top1/p95/降级率，样本 ≥ 50 次真实问答） |
| **回滚条件**（任一即回滚） | ① 降级率 > 2%；② L1 p95 > 3.0s；③ 真实 query 下 Recall 未提升或下滑；④ 端到端 p95 > 12s；⑤ 出现静默截断未被记录 |
| **回滚动作** | `RAG_RERANK_MODEL` 改回 `BAAI/bge-reranker-v2-m3-int8` + 清缓存 + 重启（热重载已连清 parent 索引） |

##### P3 — 全量与 A/B 验证

| 项 | 内容 |
|---|---|
| **准入标准** | P2 灰度达标；A/B 达到 **≥ 200 次真实问答或 7 天**（先到为准），且 Recall@3 提升在置信区间内显著（≥ 2.8pp） |
| **执行** | 全量切流；A/B 分桶（课堂 ID 哈希），对照组沿用 B1 |
| **交付物** | ① 全量报告（21 项指标口径，走 `eval/metrics_report.py`）；② 参数固化（写回 `RetrievalConfig` 默认值 + 注释实测依据）；③ `docs/plans/PLANS.md` 增补章节；④ `docs/specs/EVAL_METRICS_API.md` 同步 |
| **回滚条件** | 同 P2 ①–⑤；另：用户侧满意度/拒答率异常上升 |
| **回滚动作** | 环境变量一键回退（所有新参数必须**可通过环境变量关闭**，不得硬编码） |

**贯穿三阶段的纪律**：
1. 一次只改一个变量；
2. 所有新参数默认关闭/等同旧行为，靠环境变量开启；
3. 每阶段结束必须更新 `docs/worklogs/B_WORKLOG.md` 与 `.workbuddy/memory/YYYY-MM-DD.md`；
4. 门禁不过 = 不推进，不做"先上线再优化"。

---

#### 7. 风险与待确认事项清单

##### 7.1 风险清单

| # | 风险 | 等级 | 影响 | 缓解 |
|---|---|---|---|---|
| **R1** | **本机无 GPU**，0.6B 以上模型 CPU 推理可能远超 2.0× 安全线 | 高 | 首选方案可能直接被延迟否决 | P1 先跑延迟闸门（只测 10 题 p95），不过立刻退到 A3（149M） |
| **R2** | **logits 量纲变化 → 两个 floor 阈值全部失效** | 高 | `-2.0` / `-8.0` 直接套用会大规模误杀或放过 | **换模型必须重标定**：用 `scripts/diag_perdoc_floor.py` 重扫 -4/-5/-6/-8/-10 档，重走性价比曲线 |
| **R3** | **许可信息冲突**（Jina v2 权重 CC-BY-NC vs v3.5 称 Apache-2.0；gte-reranker 许可未核） | 中 | 商用合规风险 | 选型前**逐候选核对官方仓库 LICENSE 字段**，不采信二手榜单 |
| **R4** | 云端 API 需文档出网 | 中 | 教学资料合规 | B 类只作离线探针，不进 P2/P3（除非 Q2 放行） |
| **R5** | 离线/内网环境无法下载权重（0.6B ≈ 1.2GB） | 中 | 候选无法部署 | 提前缓存到 `.model_cache`；`HF_ENDPOINT` 已设 `hf-mirror.com`；`RAG_RERANK_ONLINE=0` 防联网重试卡首请求 |
| **R6** | **Recall 物理上限 0.9159**，剩余空间仅 12.5pp，且部分源于金标准漏标 | 中 | 期望管理 | 方案 §0.4 已明确现实预期（+1.5～4.0pp 即成功） |
| **R7** | **Top1 已 68/71（95.8%）**，指标区分度低 | 中 | 无法区分候选优劣 | 判定主用 **Recall@3 + nDCG@3 + 难负例子集**，Top1 只作参考 |
| **R8** | 公开榜单 ≠ 本项目语料（中文八股、短查询、19 篇/366 片） | 中 | 榜单第一在本项目可能垫底 | 榜单只给先验分，D1 必须由本项目实测覆盖 |
| **R9** | 生成层裁判同源偏松（生成与裁判均 deepseek-flash） | 中 | 端到端收益无法由 L3/L4 佐证 | 换模型的收益判定**只用检索层指标**（L1/L2），不采信 L3 生成层分数 |
| **R10** | **混淆"模型判别力"与"运行时效率"** | 中 | 换模型白忙一场 | P1 增设对照组：同模型（bge-m3-int8）换运行时（TEI/Infinity），分离两者贡献 |
| **R11** | 服务化引入新故障域（进程/端口/超时） | 低 | 可用性下降 | 严格按 §5.3 降级路径；降级时行为必须与现状 RRF 完全一致 |

##### 7.2 待确认事项（需人工/环境答复，答复前不进入 P2）

| # | 待确认 | 影响 | 阻塞阶段 |
|---|---|---|---|
| **Q1** | 是否有可用 GPU（型号 / 显存 / 是否常驻）？ | 决定 A2（0.6B）是否可行，决定 batch 策略 | 阻塞 P1 结论 |
| **Q2** | 教学资料是否允许出网（云端重排 API 合规）？ | 决定 B 类能否从"探针"升级为"候选" | 阻塞 P2 |
| **Q3** | 延迟硬约束到底是 L1 3.0s，还是用户可感知阈值（如首字延迟）？ | 决定安全线取 2.0× 还是更宽 | 阻塞 P1 判定 |
| **Q4** | 云端 API 月度成本上限？ | 决定 B 类能否全量 | 阻塞 P3 |
| **Q5** | 语料是否需要多语言（英文术语 / 代码检索）？ | 决定 D1 中多语言的实际权重 | 影响打分，不阻塞 |
| **Q6** | 金标准能否扩充到 120 题（含难负例标注）？标注人力是否可投入？ | 决定评测灵敏度（71 题下 1 题 = 1.41pp） | 建议 P0 内解决 |
| **Q7** | 生产运行形态：单机进程内（现状）还是独立推理服务？ | 决定 §5.2 并发与超时设计 | 阻塞 P2 |

---

#### 8. 附：决策一页速查

```
换模型前先回答：
  1. 是"判别力不够"还是"跑得太慢"？ → 用同模型换运行时做对照组分离（R10）
  2. 延迟余量多少？ → 重排 p95 现状 1082ms，安全线 2160ms，硬门禁 3000ms
  3. 换模型 → 两个 floor 阈值必须重标定（R2），否则上线即事故
  4. 收益上限：Recall +12.5pp（物理），现实 +1.5~4.0pp
  5. 判定用 Recall@3 / nDCG@3 / 难负例子集，不要用 Top1（已 95.8%）

首选：Qwen3-Reranker-0.6B（需过延迟闸门）
CPU 兜底：gte-reranker-modernbert-base（149M）
成本兜底：不换模型，换运行时（TEI/Infinity）
探针：Jina Reranker API（判别力天花板，不进生产）
否决：4B/8B、Gemma2 系列（无 GPU 不可行）
```

---

#### 9. 实测结果（2026-10-28 执行 P0+P1 可执行部分）

> 完整报告见 `docs/reports/REPORTS.md`。本章只固化会影响选型决策的结论，并修正上文被实测推翻的假设。

##### 9.1 双基线复现

| 档位 | Recall@3 | Prec@3 | MRR | nDCG@3 | Top1 | rerank p95 | L1 p95 |
|---|---|---|---|---|---|---|---|
| B0 无重排 | 0.7539 | 0.5282 | 0.9272 | 0.8161 | 63/71 | 0 ms | 0.015 s |
| B1 现状 | 0.7909 | 0.5634 | 0.9648 | 0.8700 | 68/71 | 1077 ms | 1.111 s |

与历史基线（`e2e_20261028_130646.json`）完全一致，**可复现性成立**。
重排净贡献：Recall **+3.70pp**、nDCG **+5.39pp**、Top1 **+5 条**、噪声率 **−4.93pp**、硬负例率 **−0.58pp**，代价 **+1.09s**。

##### 9.2 配置矩阵：三档全平或劣化 ⇒ 配置已到局部最优

| 档位 | 变更 | Recall@3 | nDCG@3 | Top1 | rerank p95 | 判定 |
|---|---|---|---|---|---|---|
| B1 | 基线 | 0.7909 | 0.8700 | 68 | 1077 ms | — |
| C1 | `parent_head` 480 字 | **0.7575** | **0.8243** | **64** | **1873 ms** | ✗ 全面劣化 |
| C3 | `pool_k=6` | 0.7909 | 0.8700 | 68 | 1113 ms | = 无变化 |
| C4 | `pool_k=8` | 0.7909 | 0.8700 | 68 | 1076 ms | = 无变化 |
| C5 | `batch=4` | 0.7874 | 0.8603 | 67 | 1154 ms | ✗ 略劣化 |

**三条修正**：

1. **`parent_head` 不推荐**（原 §5.1 曾把它列为折中方案）。实测比 `child_title` 全面更差且慢 74%——标题信号已由 `child_title` 覆盖，截断正文只增噪声。
2. **`pool_k` 无效的根因是池子里只有 4.21 条**（`mean_parent_pool` 恒为 4.21，6/8 上限从未触发）。想动池维度必须先抬 `child_overfetch`，而后者已被证伪。
3. **CPU 上 `batch=1` 确认最优**（b4 使 nDCG −0.97pp、p95 +77ms）。

→ **R10「模型 vs 运行时」已有答案：运行时/配置侧没有剩余空间，瓶颈在模型判别力。**

##### 9.3 评测集结构缺陷（重要）

现有 gold 在 **parent 粒度上 79/79 条全相关**（金标准 parent 数 / 文档 parent 总数 = 1.000）。后果：

- 原 §4.1 的 **HN2「同文档相邻小节」构造法在本项目不成立**——相邻节也是 gold；
- Recall@3 上限 0.9159 里含约 8pp 的标注口径差（slice 级标注 vs parent 级全相关）。

已改用 **`gain=0` 的整节零增益 parent** 作为硬负例（58/83 题，平均 1.35 个/题），并新增指标 `hard_neg_rate`：B0 **9.55%** → B1 **8.97%**。即现状每 11 条注入仍有 1 条是整节零增益，这正是换更强重排器最该改善的点。

##### 9.4 候选可执行性（本轮结论）

| 候选 | 状态 | 原因 |
|---|---|---|
| A1 bge-reranker-v2-m3-int8 | ✅ 已测 | 本地缓存 |
| A2 Qwen3-Reranker-0.6B | ⛔ 阻塞 | 网络不通 + fastembed 不支持 + venv 无 torch |
| A3 gte-reranker-modernbert-base | ⛔ 阻塞 | 同上 |
| A4 jina-reranker-v2-base-multilingual | ⛔ 阻塞 | 网络不通（1.11GB 需下载） |
| B 类云端 API | ⛔ 未执行 | 无 key，Q2 未确认 |

**环境实测**：`hf-mirror.com` / `huggingface.co` 均 ReadTimeout；venv 无 torch / transformers / sentence-transformers；`.model_cache` 仅有 bge-reranker-v2-m3-ONNX(int8)。
fastembed 0.9.0 的 `TextCrossEncoder` **只支持 8 个模型**（上述 A1 + ms-marco 两个 + bge-base + jina tiny/turbo/v2-base），**不含 Qwen3 与 gte**。

→ 换模型必须先解 **Q1（GPU）/ Q5（网络）/ Q7（运行时）** 三选一以上。

##### 9.5 新增坑（务必记住）

**`eval/e2e_runner.py` 无法跑 B0**：其 `run_l1_l2()` 内部调用 `S.configure("full")`，会把 `R.CONFIG.enable_rerank` 重置为 True，**静默覆盖 `RAG_RERANK=0` 环境变量**。实测表现是 B0 与 B1 报告一模一样（top1 68/71、nDCG 0.87），极易误判为"重排没用"或"环境变量失效"。
→ 跑无重排基线请用 `scripts/ab_rerank_model.py --no-rerank`（直接改 CONFIG，不经过 configure）。

##### 9.6 P1 门禁判定

| 门禁 | 实际 | 结果 |
|---|---|---|
| nDCG@3 > B0 (0.8161) | 0.8700 | ✅ |
| Recall@3 ≥ 0.8059 | 0.7909 | ❌（待换模型） |
| rerank p95 ≤ 2160 ms | 1077 ms | ✅ |
| L1 p95 ≤ 3.0 s | 1.111 s | ✅ |
| 降级率 ≤ 2% | 0% | ✅ |

**P1 未通过，但结论明确：卡点不在配置，在模型；换模型被环境阻塞。**

##### 9.7 实测 jina-reranker-v2-base-multilingual（已测并否决）

**下载配方（VPN 下终于打通，记录可复现）**：
```
# fastembed 用 FASTEMBED_CACHE_PATH，不是 HF_HUB_CACHE；裸 python -c 不加载 .env，
# 必须显式导出 HF_ENDPOINT，否则它去连官方 huggingface.co（被代理 ProxyError/超时）。
export FASTEMBED_CACHE_PATH="<abs>/backend/.model_cache"
export HF_ENDPOINT=https://hf-mirror.com
export HF_HUB_DISABLE_XET=1          # 关 Xet，否则大文件走 cas-server.xethub-eu.hf.co → 401
export HF_HUB_DOWNLOAD_TIMEOUT=600
# 下载整库用 huggingface_hub.snapshot_download(cache_dir=.model_cache)（4.6GB，代理~2MB/s，约 1h）
# 加载用 fastembed TextCrossEncoder(model_name=...) + 上面四个环境变量
```
**四个坑**：① 裸脚本不加载 `.env` → `HF_ENDPOINT` 为 None → 连官方站超时；② fastembed 默认超时太短；③ Xet CAS 401；④ fastembed 缓存键是 `FASTEMBED_CACHE_PATH`，与 `HF_HUB_CACHE` 不是同一个变量。

**A/B 结论（71 题 vs B1）**：Recall@3 **0.7807**（−1.02pp）、Precision 0.5516（−1.18pp）、MRR 0.9577（−0.71pp）、nDCG 0.8600（−1.0pp）、Top1 **67**（−1）、噪声率 **0.3263**（+2.11pp 更差）、硬负例率 0.1019（更差）；仅 rerank p95 **982ms** 略优于 bge 1077ms。门禁 `passed=false`。
→ jina v2 **质量全面劣于 bge-m3-int8**，叠加其 `cc-by-nc-4.0` 非商用许可，**正式否决**。
→ 强化 R6/R10：fastembed 支持的交叉编码器中 bge-m3-int8 已近天花板；真正增益只能来自非 fastembed 的 Qwen3-0.6B（Apache-2.0），需 `pip install torch transformers` 并给 `ab_rerank_model.py` 加 `--backend hf` 分支。
