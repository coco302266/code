# AI Classroom 大模型调用规范与智能体协作协议

> **owner**：A（后端与 AI 算法）
> **适用对象**：B（Prompt 工程与 RAG 检索）、前端同学、以及后续接手后端的任何人
> **代码版本**：M4 工作区（M1–M4-C 已落地，待提交）
> **配套文档**：接口交互看 `API_CONTRACT.md`，改造过程与踩坑看 `tutorial.md`，
> 阶段总结看 `PROJECT_REPORT.md` / `STAGE_REPORT_M1_M3.md`，**协作交接看 `HANDOFF_M4.md`**

这份文档回答三个问题：**模型怎么调**、**Agent 之间怎么分工**、**B 该怎么动手**。
规则用编号标记（如 `LLM-3`），review 和讨论时直接引用编号。

---

## 0. 当前是多智能体编排（M4 已落地）

**这一节在 M4 之后被重写过，旧内容（"当前不是多智能体"）已作废。**

现在一次 `/chat` 的形态是：**一条固定流水线 + 一段多角色编排**。

```
extract_topic → assess_understanding → END          （agent/graph.py，判定链路）
        ↓ 拿到 topic / hint_level / misconception
plan_agents(scenario) → [tutor, peer, ...]           （agent/orchestrator.py，编排链路）
        ↓ 按序逐个调 LLM 流式发言，前一个的发言注入后一个的提示词
```

关键性质：

1. **编排是确定性的，不调 LLM**（`plan_agents` 纯函数）。理由见 §3.4。
2. **角色序列由场景决定**，唯一事实源是 `agent/scenarios.py`：
   `v1`→[tutor]、`group`→[tutor, peer]、`debate`→[tutor, peer, examiner]。
3. **"协作"的实现方式是前文本注入**：第二个 Agent 的提示词里带着第一个 Agent 的原话
   （`app.py` 的 `prior_speeches`），而不是各答各的。这是它与"并联多次调用"的分界线。
4. **每个 Agent 的发言各占 `messages` 一行**，用 `agent_role` 区分身份。
   **没有** `agent_messages` 表——那等于把同一份文本存两遍（详见 §4 DATA-7）。

---

## 1. 大模型调用规范

### LLM-1 唯一入口：所有模型实例必须来自 `get_llm()`

```python
from agent.graph import get_llm
llm = get_llm()          # 读 LLM_PROVIDER，默认 groq
```

**禁止**在任何模块里直接 `ChatOpenAI(...)` / `ChatOllama(...)` / `ChatGroq(...)`。
`get_llm()` 内部按 provider 分支构造，只有它知道各家的参数差异。

**理由**：模型调用是全局性决策（成本、超时、降级）。散落在各处 new 实例后，换模型要改
N 个文件，且各家默认 temperature / 超时不一致，会出现"同一个问题在 A 模块和 B 模块
表现不同"却查不出原因。

### LLM-2 配置只走环境变量，禁止硬编码

| 变量 | 含义 | 当前值 |
|---|---|---|
| `LLM_PROVIDER` | 通道 | `openai`（= OpenAI 兼容通道） |
| `LLM_BASE_URL` | 端点 | `https://api.deepseek.com` |
| `LLM_MODEL` | 模型名 | `deepseek-flash` |
| `LLM_API_KEY` | 密钥 | 填在 `backend/.env`（已 gitignore） |

**换厂商只改这三行，不动代码**——这是选 OpenAI 兼容通道的全部意义。
其余 provider 走各自的 key（`GROQ_API_KEY` / `GOOGLE_API_KEY`）。

**`LLM-2a`**：`backend/.env` 已在 `.gitignore` 中。**任何情况下不要把 key 写进代码、
测试、文档或提交历史**。一旦推上去，删提交也没用，只能去厂商后台轮换密钥。

### LLM-3 模型名以 `deepseek-flash` 为准

DeepSeek 侧的 `deepseek-chat` / `deepseek-v4-flash` **已退役**，`deepseek-v4-pro` 正在
淘汰（请求会被路由走）。照抄旧教程会拿到报错或错误模型。默认模型表在
`agent/graph.py` 的 `DEFAULT_MODELS`。

### LLM-4 temperature 统一 0.4

三个 provider 分支都写死 `temperature=0.4`。辅导场景下要的是**稳定、可复现的讲解**，
不是创造性。要调必须改全部分支并在本文档登记，否则不同通道行为不一致。

### LLM-5 成本约定：一次 `/chat` = 2 次判定 + N 次发言（N = 本轮 Agent 数）

| 调用 | 落点 | 用途 | 次数 |
|---|---|---|---|
| 1 | `extract_topic_node` | 抽取当前知识点 | 1 |
| 2 | `assess_understanding_node` | 评估听懂没有、讲解深度、误解 | 1 |
| 3 | `app.py` 按编排计划逐个角色流式生成 | 真正输出讲解 | **N**（v1=1 / group=2 / debate=3） |

`POST /resources/ppt-outline` 是 1 次。画像推导**不调模型**（确定性规则，见 `LLM-7`）。
**编排本身不调模型**（见 §3.4）。

**`LLM-5a` 单轮 Agent 数上限 3**（`scenarios.MAX_AGENTS_PER_TURN`）。
这是成本红线不是技术上限：额度是这个项目唯一的真实成本，限流 10 次/分钟，
N 翻倍意味着可用轮次减半。实测 `v1`≈4.5s、`group`≈9.3s、`debate`≈14s。

**`LLM-5b` 应急开关 `AGENT_COLLAB=0`**：关掉后一切退回 M1 行为（单 Agent、单次调用），
前端与数据库都不用改。演示环境额度吃紧 / 延迟压不下来时先动这个，不要改代码。

**新增任何模型调用必须先在这里登记并说明理由**。

### LLM-6 需要结构化输出时的约定

评估节点要求模型返回 JSON，规则如下：

1. prompt 里写明"只返回 JSON 对象，不要任何其他文字"
2. 解析前剥离 ```` ```json ```` 围栏（`parse_assessment_result`）
3. 用 Pydantic 模型校验（`AssessmentResult`：resolved / hint_level 0–3 / misconception ≤500 字）
4. **解析失败沿用旧值，不抛异常**——这是刻意的：一次评估失败不该让学生的提问 502

**`LLM-6a`（已实现，XB-3 / 832aff2）**：第 4 条的代价是"静默降级"——模型连续抽风时表现为学生卡在同一
讲解深度，前端和 HTTP 状态码都看不出来。现已在 `agent/graph.py` 的 `assess_understanding_node`
解析失败分支补 `logger.warning("评估节点解析失败…")`（含 topic / in_hint_level，便于还原现场），
且 `tests/test_graph.py` 的 `AssessmentLoggingTests` 守住「解析失败必出 WARNING、正常判 false 不出」。

### LLM-7 能用确定性规则解决的，不要调模型

画像推导（`agent/profiler.py`）全部是规则：resolved +0.3、讲到第 3 层 −0.2、新知识点
从 0.5 起步……理由：① 省额度 ② 可复现 ③ 出问题能追到具体哪条规则。模型输出是黑盒，
不利于调试。**B 在做 RAG 重排、Prompt 分支判断时也请遵守这条。**

### LLM-8 超时与重试（**现状：已实现**，2026-10-30 A 落地）

规范：显式 timeout（60s）+ 最多 1 次重试，重试针对连接类/超时/限流等瞬时错误。
实现位置：`agent/graph.py` 的 `get_llm()`——`LLM_TIMEOUT_SECONDS=60` 与
`LLM_MAX_RETRIES=1` 两个模块级常量，应用到 `ChatGroq` 与 `ChatOpenAI` 两路构造。
效果：单轮 LLM 调用超过 60s 即超时降级（评估节点已有 502 兜底、回答节点由
SSE 错误事件兜住），不会无限挂；瞬时错误自愈 1 次，且不会重试「明显会失败的请求」（如 401 缺 key）。
所有调用方（/chat 的评估与回答节点）经 `get_llm()` 统一受益。

---

## 2. Agent 角色定义

四个角色，职责互斥，边界如下。**跨过边界的改动必须先改本文档。**

其中 **TopicExtractor / Assessor / Responder 三个属于学生对话链路**（串行跑完一次
`/chat`），**ResourceAgent 是教师备课链路的独立角色**，与学生链路不共享状态。

| 角色 | 职责 | 输入 | 输出 | 代码落点 | 调 LLM |
|---|---|---|---|---|---|
| **TopicExtractor** | 判断学生现在在问哪个知识点 | 最近 6 条对话 + 当前 topic | `topic`（"unknown" 表示没问知识点） | `graph.py: extract_topic_node` | 是（1 次） |
| **Assessor** | 判断听没听懂、该讲到第几层、误解在哪 | 全部对话 + topic + 当前 hint_level | `resolved` / `hint_level` / `misconception` | `graph.py: assess_understanding_node` | 是（1 次） |
| **Responder** | 生成面向学生的讲解 | 策略 + 知识库上下文 + 画像 + 历史 | 流式文本 | `app.py: event_stream` | 是（1 次，流式） |
| **ResourceAgent** | **教师**备课：主题 → PPT 大纲 | 主题 + 检索到的知识库上下文 | `PptOutline`（Pydantic 校验） | `agent/resource_agent.py` | 是（1 次） |

**不是 Agent 但会被误认为是的两个模块**：

- `agent/profiler.py` —— 写画像，纯规则，不调模型
- `agent/personalize.py` —— 画像翻译成提示词片段，纯函数，不调模型

之所以把它们和 agent 分开放，是为了**改提示词时不会误伤推导规则**。

### 2.1 一条重要的边界：Assessor 不生成讲解，Responder 不做判断

`HINT_STRATEGIES`（讲解深度四层）只在 `app.py` 消费，图里不用。这个分层的收益已经兑现过：
T0 改造时把"苏格拉底反问"换成"八股讲解"，**只改了 `state.py` 的策略表和 `prompts.py`，
图一行没动**。

---

## 3. 消息路由与上下文管理

### 3.1 路由现状：固定流水线，没有 router

```
entry → extract_topic → assess_understanding → END
```

两条无条件边。**没有条件分支、没有回边**。每次请求从头跑一遍，跨轮状态靠数据库恢复
（见 §3.3），不靠图内部循环。

### 3.2 "路由"实际体现在请求入口层

真正的分发发生在 `app.py`，依据是请求带了什么参数：

| 请求形态 | 走向 |
|---|---|
| 带 `classroom_id` + `user_id` | 校验成员 → 恢复历史与状态 → 落库 → 沉淀画像 |
| 只带 `classroom_id` | 不落库、不校验（兼容旧前端） |
| 都不带 | 纯内存对话 |
| `topic == "unknown"` 且有上传文档 | 走文档问答流 |
| 教师调 `/resources/ppt-outline` | 走备课链路（与学生链路完全独立） |

### 3.3 上下文管理：四条硬规则

**CTX-1 历史默认取最近 20 条**（`load_history(limit=20)`），且**先取末尾再反转**——
直接 `order_by(id).limit(n)` 拿到的是最老的 n 条，会把最近的对话截掉。

**CTX-2 上下文与教学状态刻意拆开**（这是最容易改错的地方）：

| | 是否按学生隔离 | 理由 |
|---|---|---|
| 对话上下文 `load_history` | **不隔离**（群聊全量） | 同学之间才能接力追问：A 问死锁、B 接"那怎么预防"才接得上 |
| 教学状态 `load_last_state` | **必须隔离** | 否则 B 继承 A 讲到第 3 层的深度，个性化失效 |

`load_history` 的 `user_id` 参数就是"群聊 / 单独聊"开关：不传 = 群聊，传了 = 单聊。
将来做单聊不用改结构。

**CTX-3 服务端是状态的唯一真相源。** 只要带了 `classroom_id`，就**不再信任客户端回传的
`topic` / `hint_level`**，一律从库恢复。理由：客户端传 0 既可能是"新话题"也可能是
"resolved 后重置"，无法区分。

**CTX-4 topic 必须归一。** 模型自由输出的知识点名要 `normalize_topic()`（去空白 + 转小写）
后才能当字典键用。否则"TCP 三次握手"和"tcp三次握手"在画像里裂成两个知识点，学生问三遍
还是 beginner。

### 3.4 多智能体编排：已落地，以及"为什么主管不调 LLM"

编排入口是 `agent/orchestrator.py` 的 `plan_agents()`，唯一事实源是 `agent/scenarios.py`。

**`ORCH-1` 主管（supervisor）用规则，不调 LLM。** 三个理由：
① 多 Agent 本身已经把单轮调用从 1 次变成 N 次，再叠一层主管决策会直接撞限流（本仓已有
429 打断整轮开发的前科）；② 模型决策不可复现，同一句话两次可能派出不同角色；
③「这轮为什么是 3 个 Agent」必须能事后回答，规则决策天然自带解释，模型决策只能靠日志猜。

**`ORCH-2` 降级契约：任何异常输入都收敛成「单 Agent + `degraded=True` + `reason`」。**
绝不因为编排层的问题让整次 `/chat` 失败。已知会被降级的输入：未知场景、`AGENT_COLLAB=0`、
角色数超上限。**降级是静默的**——回答完全正常，唯一症状是多场景没生效，
所以必须落 `agent_runs` 并能在 `GET /classrooms/{id}/runs` 里查到。

**`ORCH-3` 多步场景忽略客户端传的 `agent_role`。** 小组讨论里客户端传个 examiner
就把讨论变成考试了。`agent_role` 只在单步场景（`v1`）下生效。

**将来要升级成真 LLM 主管**：替换 `orchestrator._plan_agents_node` 的实现即可，
`/chat` 侧的 `agent_graph.ainvoke(...)` 调用方式不变。**改动前先来改这一节**——
主管一旦能自主决定派谁，LLM-5 的成本约定就失效了。

---

## 4. 数据兼容约定（A 的交付责任）

| 规则 | 内容 |
|---|---|
| **DATA-1** | 对外字段一律 **snake_case**，与 `API_CONTRACT.md` 一致；改字段必须同步改那份文档 |
| **DATA-2** | 模型返回的结构化数据**必须过 Pydantic**（`AssessmentResult` / `PptOutline`），不能直接 `json.loads` 就用 |
| **DATA-3** | 画像的两个 JSON 列结构固定：`knowledge_mastery` 是 `{知识点: 0~1}`，`weak_points` 是字符串数组。**键必须归一**（CTX-4） |
| **DATA-4** | `messages.user_id` 对 assistant 消息的含义是"**这条回复是给谁的**"，不是"谁说的"。前端显示发言人要用 `role` 判断：assistant 一律显示"AI" |
| **DATA-5** | 错误码语义固定：非成员 403、需要数据库但没配 503、模型侧失败 502、限流 429 |
| **DATA-6** | SSE 共五类事件，顺序固定：`sources`（最前，本轮检索依据）→ `agent`（**每个角色发言前一次**，标记切换）→ `token`（增量文本，可多次）→ `state`（最后，本轮最终状态）/ `error`（失败）。前端按类型分发；**未知类型必须静默忽略**——这是老前端不吃 M4 变更也不崩的原因 |
| **DATA-7** | 多 Agent 发言**各占 `messages` 一行**，用 `agent_role` 区分；**不建 `agent_messages` 表**。轮次级元数据（参与角色 / 顺序 / 耗时 / 是否降级 / 检索命中数）记在 `agent_runs` 一张表里 |

**兼容性检查清单**（每次改动后跑）：`run_tests.py`（83 例）+ 四个 smoke 脚本。
其中 `smoke_db.py` 和 `smoke_personalize.py` 是跨模块的，最容易撞出兼容问题。

---

## 5. 给 B：Prompt 工程实现指南

### Prompt-1 提示词只写在两个地方

| 类型 | 位置 |
|---|---|
| 图内用的模板 | `agent/prompts.py`（`EXTRACT_TOPIC_PROMPT` / `ASSESS_UNDERSTANDING_PROMPT`） |
| 讲解深度策略 | `agent/state.py` 的 `HINT_STRATEGIES` |

回答阶段的提示在 `app.py` 内联拼接（历史原因）。**新增提示词请归入 `prompts.py`**，
不要把长文本塞进 `app.py`——那会让路由逻辑被提示词淹没。

### Prompt-2 讲解深度四层是产品语义，不要当成"难度"

| level | 讲法 |
|---|---|
| 0 | 一句话结论 |
| 1 | 面试标准答案（分点、可背诵、200 字内） |
| 2 | 展开原理：为什么是这样、底层机制与设计取舍 |
| 3 | 原理 + 具体例子 + 一个面试追问方向 |

**每一层都允许直接给答案**。这是从开源原版的"苏格拉底式反问"改过来的——八股学习场景下
学生要的是答案，不是被反问。调 prompt 时不要把"不要直接给答案"这类句子加回去。

### Prompt-3 画像管"怎么讲"，`hint_level` 管"讲多少"，不要互相覆盖

`agent/personalize.py` 的 `build_profile_brief()` 只输出**讲法**（术语门槛、要不要铺垫、
防哪些历史坑）。画像唯一能抬 `hint_level` 的地方是 `suggest_start_level()`，且**只看当前
这个知识点**的掌握度（≥0.75 才从 0 抬到 1）。**不要用 `overall_level` 去抬**——整体强
不等于这个知识点强。

### Prompt-4 冷启动必须完全退化

`get_or_create_profile` 会造一个全空的 beginner 画像，那**不算数据**。
`has_signal()` 把关：没有实质信号时不注入任何画像段落。
否则等于把每个新学生先入为主当基础薄弱来教。

### Prompt-5 迭代别烧额度：用 `/students/{id}/prompt-preview`

```
GET /students/{id}/prompt-preview?classroom_id=1&topic=死锁&hint_level=0
```

**不打 LLM**，直接返回拼好的提示词。调提示词时用它对比"同一道题、两种画像"的差异。
真实调用一次要花额度且受限流，别用来做文案迭代。

### Prompt-6 改完提示词必须回归

至少跑 `smoke_chat.py`（四轮剧本，验证 hint_level 递进与 resolved 归零）。
改动 `assess` 相关的还要跑 `smoke_profile.py`。

### Prompt-7 多 Agent 的衔接指令在 `scenarios.py`，不在角色提示词里

每个场景步骤带两个字段：`stance`（这个角色在本场景里的身份，如"立论"/"质疑"/"追问"）
和 `handoff`（衔接指令，由 `app.py` 拼到系统提示词末尾）。

**要调"第二个 Agent 别复读"这类协作行为，改 `handoff`；要调"tutor 讲成什么样"，
改 `roles.py`。** 两者不要混：把协作要求写进角色提示词，`v1` 单 Agent 场景也会被
"不要重复前面发言"这句话污染（那里根本没有前面发言）。

发言角色提示词的唯一落点仍是 `agent/roles.py` 的 `build_role_system`。

---

## 6. 给 B：RAG 检索模块实现指南

### RAG-1 文档结构与讲解深度一一对应

每篇知识文档固定四段，**顺序不能变**：

```
# 标题
## 一句话结论      ← 对应 hint_level 0
## 标准答案        ← 对应 hint_level 1
## 原理展开        ← 对应 hint_level 2
## 面试常问        ← 对应 hint_level 3
```

这不是排版偏好，是**检索质量的保证**：四段各自成块，模型按当前讲解深度取用对应段落，
不会把"举例"内容塞进"一句话结论"。

### RAG-2 加新文档的三步

1. 放到 `knowledge/cs/os/` 或 `knowledge/cs/network/`（目录名就是 metadata 的 `category`）
2. 按 RAG-1 的结构写
3. `python build_kb.py`（会先 `drop_collection` 再重建，**幂等**）

**只做操作系统 + 计算机网络，明确不做 Java 八股。** 当前 8 篇（各 4 篇）。

### RAG-3 构建必须带两个环境变量（Windows 必踩）

```
HF_ENDPOINT=https://hf-mirror.com     # 直连会被代理挡，报 502
HF_HUB_DISABLE_SYMLINKS=1             # 否则 snapshots 目录是空的，加载时找不到权重
```

- **RAG-3a 注入时机必须在 import 之前**：`huggingface_hub` 在 import 时就把这些变量求值成
  常量，写在 import 之后**完全无效**。三个变量（`HF_ENDPOINT` / `HF_HUB_DISABLE_SYMLINKS` /
  `FASTEMBED_CACHE_PATH`）用 `os.environ.setdefault` 写在 `agent/rag/indexer.py` 顶部。
  第三个是把模型缓存从系统 Temp 挪到 `backend/.model_cache/`——Temp 会被磁盘清理扫掉，
  表现为"跑过一次好好的，过几天突然要重新下载"。

> 注意：`build_kb.py` 里**没有**这些设置，它依赖 import `indexer` 时顺带生效。
> 手动跑任何独立脚本前确认 `indexer` 已被导入。

### RAG-4 检索接口

```python
from agent.rag.retriever import get_knowledge_context
ctx = get_knowledge_context(vectorstore, query, k=4)   # 没有库时返回空串
```

- 默认 `k=4`，输出的每段带 `[知识点｜分类｜来源]` 标记，方便模型引用和人工核查
- **不要**用 `get_relevant_context`——那是给上传 PDF 的会话级检索用的，坐标体系是页码行号
- 知识库实例由 `app.py` 的 `get_knowledge_store()` 缓存常驻，**不要每次请求重建索引**

### RAG-5 中文场景别换回英文 embedding

现在是 `BAAI/bge-small-zh-v1.5`。选 small 是因为总量只有 8 篇、66 个切片，瓶颈在切片
粒度不在模型容量。**知识库扩到几百篇再考虑换 base/large。**

### RAG-6 自检方式

`build_kb.py` 内置检索自检。加完文档跑一次，确认目标问题命中的是**新加的那篇**。
判断检索是否真的生效，用文档里**独有的论点**去问——比如问 TCP 握手能答出"避免历史
连接重复初始化"，说明确实命中了知识库而不是模型在背通用答案。

---

## 7. 已知差距（不粉饰，便于后续排期）

| 项 | 现状 | 影响 |
|---|---|---|
| 多智能体 | ✅ **M4 已落地**（规则编排 + 前文本注入），主管仍非 LLM | 见 §3.4；升级落点已写明 |
| 多场景 | ✅ **M4 已落地**（`v1`/`group`/`debate`，建房即定） | 前端待接（见 `HANDOFF_M4.md`） |
| 评估报告 | 🟡 **M4-C 采集层已就绪**，人工评分待填 | `python eval_report.py` 出客观指标，四维打分要人填 |
| 软著材料 | ⬜ 源码已提交，**文本未写** | 不属于后端任务，见 `HANDOFF_M4.md` |
| 超时与重试 | ✅ **已实现（LLM-8，graph.py get_llm，timeout=60s/max_retries=1）** | 模型侧卡住 60s 即超时降级，不再无限挂 |
| 评估失败的日志 | 静默降级，无日志（LLM-6a） | 线上难排查 |
| `weak_points` 语义重复 | ✅ **已解决**（`profiler.merge_weak_points` 去重保序 + 注入时只取最近 3 条） | — |
| `requirements.txt` | T7 依赖已补 | ✅ 已解决 |

---

## 7.5 效果评估口径（成果 #4，M4-C）

采集层在 `agent/evaluation.py`（纯函数）+ `db/repository.list_agent_runs`，
产物由 `python eval_report.py` 生成。**跑评估不需要起服务、不调 LLM、零额度。**

| 规则 | 内容 |
|---|---|
| **EVAL-1** | **客观指标与主观评分严格分开**。机器只算：轮数、每轮 Agent 数、降级率、延迟分位、检索覆盖率。质量靠人评四维度（准确 / 相关 / 完整 / 个性化，各 1–5 分）。别把两者混成一张表 |
| **EVAL-2** | **评分粒度是"发言"不是"轮次"**。一轮 3 个 Agent = 3 行。按轮打分会把"tutor 讲得好、peer 在复读"折中成一个看不出问题的分——而"哪个角色在贡献价值"正是多智能体评估最该回答的 |
| **EVAL-3** | **延迟看 p95 不看均值**。均值会被一次冷启动（首次加载重排模型）拉偏。样本 <20 时 p95 退化成最大值，这是诚实的，报告同时给样本量 |
| **EVAL-4** | **降级必须单列**。编排降级是静默的，混在"平均 1.0 个 Agent"里看不出问题 |
| **EVAL-5** | **`rag_hits` 是 M4-C 才补的列**，更早的留痕记为 0。出正式报告只用新跑的轮次，否则覆盖率被低估 |
| **EVAL-6** | 指标口径改了必须同步改 `tests/test_evaluation.py`。口径是会反复改的，纯函数才能改一次测一次 |
| **EVAL-7** | **留痕读不出来和确实没有是两回事**。`db.diagnostics.count_runs` 查不出返回 `None` 而不是 `0`；「表不存在」退化成 0 会让人以为只是还没跑对话。改动诊断层前先看 `tests/test_diagnostics.py` 里钉住这条的用例 |
| **EVAL-8** | **`/evaluation/*` 为空必须先读 `diagnosis`**。四种成因（没配库 / 连不上 / 表缺失 / 被过滤掉）表现完全一样，凭 0 判断不了。新增评估接口必须带 `diagnosis` 字段 |
| **EVAL-9** | **不带 `classroom_id` 的 `/chat` 也要留痕**（`agent_runs.classroom_id` 为 NULL）。早先直接丢弃，症状是「回答正常、评估全空、零报错」——这类留痕没有发言正文，评不了分，`runs_without_classroom` 就是它们的数量 |

---

## 8. 变更流程

1. 改协议 → **同步改本文档的对应编号**
2. 改对外字段 → **同步改 `API_CONTRACT.md`**（前端按它对接，改了不说就是坑人）
3. 改数据库字段 → **必须在 `init_db.py` 的 `ensure_new_columns()` 登记 ALTER**
   （`create_all()` 不给已存在的表补列，会静默失败）
4. 提交前跑 `run_tests.py`，涉及端到端的补跑对应 smoke 脚本
