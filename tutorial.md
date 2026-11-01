# Tutor-Chatbot 后端源码课程

## 项目用途与本次学习目标

Tutor-Chatbot **原本**是一个苏格拉底式 CS 家教机器人：学生提问后，后端先判断"他想学什么、听懂了没有"，再按四级提示梯度（类比 → 收窄提示 → 引导提问 → 揭晓答案）用流式方式回答；学生上传 PDF 后，回答会基于 PDF 内容；提交代码时会调用 Judge0 真实执行。

> **阅读提示（2026-10-04 更新）**：源码已经动过两轮，下面各课的部分行号与描述已过时。请以本提示为准：
>
> | 已改动 | 原文描述 | 现在的事实 |
> |---|---|---|
> | Judge0 代码执行 | 第 1 课第 7 关"顺序是：代码执行（235-251）→…" | `agent/tools.py` 已删除，`event_stream()` 开头不再有执行分支；行号整体前移 |
> | 苏格拉底基调 | 第 1 课 `reveal_instruction`（281-285）"Do NOT give the direct answer." | 该变量已删除。`app.py` 的提示改为讲解式：允许直接给答案，规则里明确"不需要反问、不需要启发式铺垫" |
> | 四级策略语义 | 第 1/2 课"类比 → 收窄提示 → 引导提问 → 揭晓答案" | `HINT_STRATEGIES` 已改为**讲解深度**四层：0 一句话结论 / 1 面试标准答案 / 2 原理展开 / 3 举例+面试追问。提示词改为中文 |
> | RAG 引用规则 | 第 1 课"英文的 `RAG_GROUNDING_RULES`" | 已改为中文知识库规则，证据标签由 `PDF evidence:` 改为 `知识库资料：` |
> | Prompt 模板 | 第 2 课提到的 `prompts.py` 五个模板 | `RESPOND_PROMPT` / `CONGRATS_PROMPT` / `UNKNOWN_TOPIC_PROMPT` 三个死代码已删除；`EXTRACT_TOPIC_PROMPT`、`ASSESS_UNDERSTANDING_PROMPT` 已中文化 + 八股化 |
> | 模型通道 | `.env` 走智谱 GLM | 改为 DeepSeek：`LLM_BASE_URL=https://api.deepseek.com`、`LLM_MODEL=deepseek-flash` |
>
> 图（`graph.py`）本身**一行未改**——这正是第 2 课"分层是好事"的结论：换策略表和提示词不需要动状态机。

本次学习目标：把这套开源后端改造成 **AI Classroom**（一个教室 + 计算机八股学科 + 学生画像 + 教师资源生成）。因此课程按"哪些能直接复用、哪些必须改、哪些要删"来组织，读者定位是**后端与 AI 算法负责人**。

## 源码版本与本地改动

- 源码来自 GitHub 下载包，`Tutor-Chatbot-main/` 目录下**没有 git 仓库**，无法给出 commit 号 → **已在 `D:\STUDY\AI-Classroom`（项目根目录）建 git 仓库并做基线提交 `b41483c`，改造前务必先提交**。
- 本地已产生的改动：
  - 新建 `backend/venv/`，依赖按 `backend/requirements.txt` 安装完成（已追加 `langchain-openai`）。
  - 新建 `backend/.env`：走新增的通用 OpenAI 兼容通道，当前指向 DeepSeek（`LLM_PROVIDER=openai`、`LLM_BASE_URL=https://api.deepseek.com`、`LLM_MODEL=deepseek-flash`），`LLM_API_KEY` 留空待填。换厂商只改这三行，不用动代码。
  - 代码改动：`agent/graph.py` 新增 openai 分支（含 `DEFAULT_MODELS`）、`agent/state.py` 的 provider 白名单加入 `openai` 并设为默认值（详见第 3 课）。
  - **已完成五次改造**：① 删除 Judge0 代码执行链路（含 `tools.py`）；② 提示基调由苏格拉底式改为中文八股讲解式（策略表、提示词、引用规则全部中文化）；③ T2 八股知识库（8 篇文档 + 中文 embedding + 常驻 Chroma，详见下文 T2 小节）；④ T3 MySQL 持久化（四张核心表 + `/chat` 落库，详见下文 T3 小节）；⑤ T4 成员校验 + 教学状态落库（详见下文 T4 小节）。
  - 后端已在 `127.0.0.1:8000` 跑起来，`GET /health` 返回 200。
- **T0.2 已验收通过（2026-10-04）**：`LLM_API_KEY` 已填入 DeepSeek key，`/chat` 真实跑通。四轮实测结果见下表。
- **两个验收入口**（都在 `backend/` 下用 venv 的 python 跑）：
  - `python run_tests.py` → 纯逻辑测试基线 **16/16**，不花钱，每次改完必跑
  - `python smoke_chat.py` → 端到端真实调用（消耗额度），验证提示与状态机行为

## T0.2 实测基线（DeepSeek `deepseek-flash`，2026-10-04）

跑 `python smoke_chat.py` 的四轮剧本，状态机与提示改造全部生效：

| 轮 | 学生说 | hint_level | 回答长度 | resolved |
|---|---|---|---|---|
| 1 | 什么是死锁？产生的必要条件有哪些？ | 0 → 0 | 242 字 | false |
| 2 | 还是没太懂，能再讲清楚一点吗？ | 0 → **2** | 1240 字 | false |
| 3 | 那怎么预防死锁呢？举例说明一下 | 2 → **3** | 2186 字 | false |
| 4 | 明白了，谢谢！ | 3 → **0** | 156 字 | **true** |

- **中文 topic 生效**：抽取结果是「死锁」而不是 deadlock，讲解深度四层按预期影响篇幅（242 → 1240 → 2186 字）。
- **resolved 分支正确**：第 4 轮归零并输出「面试最值得记住的一句话」，随后主动引导下一个知识点。
- **耗时**：首 token 约 5.5s；整轮 4.4s / 17.2s / 15.0s / 6.8s（一次 `/chat` 内部打 3 次模型：抽主题、评估、流式回答）。

三个实测暴露的问题（后续改造要处理）：

1. **hint_level 是跳跃的**：第 2 轮从 0 直接跳到 2，不是逐层 +1。规则只说"提高"，没约束步长，模型自由裁量。
2. **misconception 四轮全为空**：它是 StudentProfile 薄弱点的原材料（见第 2 课），一直为空意味着 T5 画像拿不到数据。需要判断是评估 prompt 太宽松，还是这几轮确实没有误解——建议后续用明显错误说法（如"死锁就是进程死循环"）复测。
3. **策略约束是软的**：第 1 轮 hint_level=0 要求"一句话结论"，但学生直接问"有哪些"，模型还是列了完整四条。这是合理取舍，不是 bug——说明策略文案是风格约束而非硬限制。

## T2 八股知识库（2026-10-04 完成）

**内容**：`knowledge/cs/os/`（进程与线程、死锁、虚拟内存与页面置换、进程调度算法）+ `knowledge/cs/network/`（TCP 三次握手与四次挥手、TCP 与 UDP、HTTPS 与 TLS、TCP/IP 分层与 ARP/DNS），共 8 篇，每篇按「一句话结论 → 标准答案 → 原理展开 → 面试常问」组织，正好对应讲解深度四层。

**链路**：Markdown → 切分（500 字/重叠 80，按 `## ` 标题优先）→ 中文 embedding → Chroma 落盘 `backend/chroma_kb/`

- `indexer.py`：embedding 由 `bge-small-en-v1.5`（英文）换成 **`BAAI/bge-small-zh-v1.5`**；新增 `load_markdown_documents()` / `build_knowledge_index()` / `load_knowledge_index()`，metadata 为 `subject / category / topic / source`；PDF 版 `build_index()` 原样保留，两套 collection 互不干扰。
- `retriever.py`：新增 `get_knowledge_context()`，输出中文标注 `[知识点｜分类｜来源]`。
- `build_kb.py`：构建入口，带检索自检。
- `app.py`：`get_knowledge_store()`（lru_cache）常驻加载；**两套检索来源**——上传 PDF 的会话级上下文 `doc_context` + 常驻知识库 `kb_context`；`topic == "unknown"` 只在有上传文档时才走 `doc_stream`，否则打招呼（否则知识库常驻会让寒暄也去检索，永远不会走打招呼分支）。

**三个环境坑（下次照做）**：

1. **下载模型要走国内镜像**：`HF_ENDPOINT=https://hf-mirror.com`，否则直连 HuggingFace 会被代理挡掉（ProxyError 502）。
2. **Windows 建不了符号链接**：huggingface_hub 默认用 symlink 组装 `snapshots/`，本机没权限会静默失败，报 `model_optimized.onnx doesn't exist`。加 `HF_HUB_DISABLE_SYMLINKS=1` 改用复制即可。
3. **Chroma 持久化是追加语义**：重复构建会翻倍（66 → 132）。`build_kb.py` 里已经加了 `drop_collection()` 保证幂等。

**验收**：`python build_kb.py` → 66 切片；三个测试问题（TCP 三次握手 / 死锁必要条件 / 进程与线程区别）回答均直接命中知识库内容，表格与措辞一致。

## T3 MySQL 持久化（2026-10-04 完成）

**本机环境**：MySQL 8.0.41（`C:\Program Files\MySQL\MySQL Server 8.0`），root 密码 `123456`，库名 `ai_classroom`，可用 Navicat 查看。

**四张核心表**（字段严格按 DEVELOPMENT.md 第 6 节，未提前加 StudentProfile / LearningRecord）：

| 表 | 关键设计 |
|---|---|
| `users` | `username` 唯一；`role` ∈ student/teacher |
| `classrooms` | `subject` 默认 `cs` |
| `classroom_members` | `(classroom_id, user_id)` 唯一约束，防止重复加入 |
| `messages` | `role` ∈ student/assistant/teacher；**`user_id` 可空**——assistant 的回复不属于任何用户，强行造「AI 用户」会污染 User 表和角色语义 |

**新增文件**：

- `db/session.py`：engine 与 `SessionLocal`。**数据库是可选的**——`MYSQL_*` 没配时 engine 为 None，服务以「无数据库」模式照常运行，避免本地没起 MySQL 就整个起不来。两个关键参数：`pool_pre_ping=True`（MySQL 默认 8 小时断连）、`expire_on_commit=False`（见下）。
- `db/models.py`：四张表的 ORM 模型。
- `db/repository.py`：数据访问函数。`load_history()` 先按 `id.desc()` 取最近 N 条再反转——直接 `order_by(id).limit(n)` 拿到的是**最早**的 n 条，会把最近的对话截掉。
- `init_db.py`：建库（utf8mb4）+ 建表 + 回读校验。
- `seed_demo.py`：T3.6 五项验收（纯数据库层，不启服务也能跑）。
- `smoke_db.py`：端到端验收（需服务在跑），与 `smoke_chat.py` 分工——前者带 `classroom_id` 验持久化，后者不带，验纯对话链路。

**`app.py` 的接入**：`ChatRequest` 新增可选 `classroom_id` / `user_id`。两者都留空时退化为原来的不落库行为，**现有前端（只传 `session_id`）完全不受影响**。落库顺序是「先读历史再存本条」，否则刚存的用户消息会被自己读回来、和新消息重复。

**两个真实踩到的坑**：

1. **`DetachedInstanceError`**：`with get_db() as db` 退出时会 commit + close，commit 默认把实例属性标记为失效，之后在 with 外面读 `user.id` 就崩。修法是 `sessionmaker(expire_on_commit=False)`，同时端点在 with 内就把字段取成 dict。
2. **追问被判成 `unknown`**（由持久化暴露）：`extract_topic_node` 原来**只把最后一条用户消息**喂给模型（`graph.py:105`），prompt 里虽带 `current_topic`，但一旦客户端没回传 topic（历史改成从库里读就是这种情况），「那怎么预防呢？」这种指代性追问就没有任何上下文，只能返回 unknown → 走去打招呼分支。修法是把最近 6 条对话一并给模型，并让 prompt 明确「当前知识点为 unknown 时，先从近期对话推断」。顺带修了第 2 课记下的隐患：`topic` 结果做 `.strip("。.！!？?；;，,、\"' ")`，模型多返回一个句号就会被当成换话题、把 hint_level 打回 0。

**验收**：`python init_db.py` → 四张表就绪；`python seed_demo.py` → 五项通过；`python smoke_db.py` → 两轮对话 4 条记录，第 2 轮追问 `topic=死锁` 且回答接上上下文（资源有序分配法）。`run_tests.py` 仍 16/16。

**留给 T4 的**：`topic` / `hint_level` 这些教学状态目前仍靠客户端回传，**没有落库**——所以刷新页面后虽然历史还在，但讲解深度会归零。T4 做「后端必须知道谁、在哪个课堂」时会一并解决（成员身份校验 + 教学状态持久化）。

## T4 角色、课堂与教学状态（2026-10-04 完成）

**两件事**：① 成员身份校验（T4.4「后端必须知道谁」）② 教学状态落库（T3 留下的缺口）。

**教学状态随消息落库**：`messages` 表新增 `topic`（VARCHAR 64，可空）与 `hint_level`（INT，默认 0）。每次回答把当时的知识点和讲解深度写进 assistant 那条消息，下次请求用 `load_last_state()` 从最后一条有 topic 的消息恢复。

**关键设计决策：服务端成为状态的唯一真相源。** 只要请求带了 `classroom_id`，就**不再相信客户端回传的 `topic` / `hint_level`**，一律从数据库恢复。理由：客户端传 0 既可能是「新话题」，也可能是「resolved 后重置」，服务端无法区分；与其猜，不如只认自己写进去的值。不带 `classroom_id` 的旧行为完全保留。

**成员校验**：`/chat` 带 `classroom_id` + `user_id` 时，先查 `classroom_members`；不是成员直接 403。AI 不作为成员记录——它以 `messages.role='assistant'` 参与对话，给它造一个 User 会污染 User 表和角色语义。

**新增端点**：`GET /classrooms/{id}`、`GET /classrooms/{id}/members`；`GET /classrooms/{id}/messages` 的返回带上 `topic` / `hint_level`。

**一个必须知道的机制**：`create_all()` 只对「不存在的表」生效，**表在而列缺失它不管**。所以后期每次给模型加字段，都要在 `init_db.py` 的 `ensure_new_columns()` 里同步登记一条 ALTER，否则表结构会和代码里的模型悄悄偏离——读出来是 None，写入直接报错。

**验收**（`python smoke_db.py`，三轮都**不传** topic / hint_level / history，全靠数据库恢复）：

| 轮 | 提问 | topic | hint_level | 回答长度 |
|---|---|---|---|---|
| 1 | 什么是死锁？ | 死锁 | 0 | 257 字 |
| 2 | 还是没太懂，能再讲细一点吗？ | 死锁 | **2** | 2587 字 |
| 3 | 举个例子 | 死锁 | **3** | 2304 字 |

`0 → 2 → 3` 就是状态恢复生效的硬证据：如果状态没恢复，每轮都会从 0 重新评估，讲解深度根本升不上去。另：非成员发消息返回 403。`run_tests.py` 仍 16/16。

**留给 T5 的**：`misconception` 每轮都算出来却用完即弃，它才是 StudentProfile 薄弱点的原材料——T5 做画像时按知识点累积落库。

## T5 学生画像（2026-10-04 完成）

**先修了两个挡路的 bug，否则画像拿不到原材料。**

**Bug 1：`misconception` 一直是空字符串，不是模型填不出来。** 排查方法：拿同一句「死锁不就是进程死循环卡住了嘛」做对照，A 组不回传 topic、B 组回传 `topic=死锁`：

| 组 | hint_level | misconception |
|---|---|---|
| 不回传 topic | 0 | `''` |
| 回传 topic=死锁 | 1 | `把死锁误解为进程死循环卡住，而忽略其本质是多个进程互相等待…` |

结论是 `assess_understanding_node` 开头的**两道短路**把评估整个跳过了：`topic_changed` 短路 + `len(messages) < 2` 短路。而学生带着错误理解来提问的**第一轮**，必然同时命中这两条——恰恰是最该记录的时刻。现在改成：换话题只把 `hint_level` 归零，误解照样记；也不再要求至少两轮消息才能评估。

**Bug 2：`.env` 靠工作目录加载，换目录启动就静默降级。** `load_dotenv()` 无参数时只找 cwd，用 `uvicorn --app-dir` 从别的目录启动时会读不到配置，`/health` 会退化成 `provider=groq / database=disabled`——**不报错、不落库，极难排查**。现在 `app.py`、`agent/graph.py`、`db/session.py` 三处都改成按 `__file__` 定位 `.env`。注意 `app.py` 必须在 import `db.session` **之前**加载：session 在模块级就建 engine。

**新增两张表**（都在「学生 × 课堂」粒度上，不跨课堂污染）：

- `student_profiles`：`overall_level`、`learning_preference`、`knowledge_mastery`(JSON: 知识点→0~1)、`weak_points`(JSON 数组)
- `learning_behaviors`：行为流水 `ask_question` / `repeat_question` / `request_explanation` / `mastered`

**画像怎么算出来的（`agent/profiler.py`，确定性规则，不再额外调 LLM）**：

| 信号 | 掌握度变化 |
|---|---|
| `resolved=true` | +0.3 |
| `hint_level=2` | −0.1 |
| `hint_level=3` | −0.2 |
| `hint_level=0/1` | 0（刚开口，不奖不罚） |

新知识点从 **0.5** 起步，代表「还没测出来」而不是「半懂」。定级规则：均值 ≥0.75 **且至少 2 个知识点**才给 advanced（只问一个、答得再好也不足以说明整体水平），≥0.4 为 intermediate，否则 beginner。

**topic 必须归一**：topic 是模型自由抽取的，「TCP 三次握手」和「tcp三次握手」不归一就会在画像里裂成两个键，学生问三遍还是 beginner。`normalize_topic()` 去掉全部空白并转小写。

**验收**（`python smoke_profile.py`，8 次真实对话）：

| 学生 | 对话 | 结果 |
|---|---|---|
| A | 带错误说法进场 → 连追三轮不懂 | `beginner`，`死锁=0.0`，3 条薄弱点，偏好「需要把原理展开」 |
| B | 两个知识点都当场答对 | `advanced`，`tcp三次握手=0.8`、`进程与线程的区别=0.8` |

行为流水 `['repeat_question', 'request_explanation', 'request_explanation', 'ask_question']` 四种齐了。`PUT /students/{id}/profile` 手工写入 `HashMap=0.3/JVM=0.2` 与 `0.8/0.9` 后自动定级 beginner / advanced。新增 `tests/test_profiler.py` 22 个用例，`run_tests.py` **38/38**。

**留给 T6 的**：画像现在只是「存下来了」，还没被喂回提示词。T6 要做的就是把 `overall_level` + 当前知识点的 `knowledge_mastery` + `weak_points` 拼进 `system_content`，让 beginner 和 advanced 拿到不同深度的讲解。

## T6 个性化提示词（2026-10-04 完成）

**新增 `agent/personalize.py`，与 `profiler.py` 分家**：profiler 负责「写画像」，personalize 负责「用画像」。它把画像翻译成一段可以直接拼进 `system_content` 的中文说明，是纯函数——同一份画像必须产出同一段话，这样才能单测、也能开一个不花钱的预览接口。

**两个维度的分工，避免打架**：

- `hint_level` 管「讲多少」（信息量 / 展开到第几层），由评估节点的现场证据决定
- 画像管「怎么讲」（术语门槛、要不要铺垫、该防哪些坑）

画像**不去覆盖** `hint_level`——后者是学生现场没听懂才往上涨的负反馈信号，拿统计猜测压过现场证据会出事。唯一例外是 `suggest_start_level()`：换知识点时，若**这个知识点**历史掌握度 ≥0.75，起步深度可以从「一句话结论」抬到「标准答案」。

> 为什么不用 `overall_level` 去抬：整体强 ≠ 这个知识点强。一个 advanced 学生第一次接触「MySQL 索引」，用整体水平抬起步深度会让他直接跳过结论层，而他对这个知识点一无所知。

**冷启动必须完全退化为 T5 之前的行为**。`get_or_create_profile` 会造出全空的 beginner 画像，那种不能当真人画像用——否则等于把每个新学生先入为主地当成基础薄弱。`has_signal()` 要求画像里至少有一条实质数据（mastery / weak_points / 非默认偏好）才注入。

---

**过程中挖出两个 T4 遗留的真 bug**，都是这次验收才暴露的：

**Bug 3：同一个课堂里，多个学生的对话和教学状态互相串。** `load_history()` 和 `load_last_state()` 都只按 `classroom_id` 取最后一条，不区分是谁的。表现是新学生第一次提问就拿到 `hint_level=2` 的 1994 字长篇——他其实什么都没问过，继承的是同课堂另一个学生的状态。

修法两处：① `load_history` / `load_last_state` 加 `user_id` 参数，按学生隔离；② **assistant 回复落库时也要写 `user_id`**（含义是「这条回复是给谁的」）。原来一律写 `NULL`，导致所有人的对话线混在一起分不开。老数据 `user_id IS NULL` 当作公共消息继续可见，避免升级后上下文凭空断掉。

> 副作用要心里有数：现在每个学生在课堂里有一条独立的对话线，`/classrooms/{id}/messages` 仍然是全课堂的（教师视角需要）。

**Bug 4：`api_list_members` 丢了路由装饰器**，`GET /classrooms/{id}/members` 一直返回 405。跑 `smoke_db.py` 才撞出来——之前的验收脚本没覆盖这个端点。

---

**验收**（`python smoke_personalize.py`，3 次真实对话 + 2 次免费预览）：

同一个知识点「死锁」，A 的画像 0.2、B 的画像 0.9：

| | 起步深度 | 实际深度 | 回答 |
|---|---|---|---|
| A（beginner，0.2） | 0 | 0 | 484 字，围绕一句话结论解释关键词 |
| B（advanced，0.9） | **1** | 1 | 316 字，直接给四个必要条件 + 预防 |
| C（新学生） | — | 0 | 403 字，`personalized=false` ✓ |

`/chat` 的 SSE state 事件新增 `personalized` 字段，前端能直接看到这一轮有没有吃到画像。另外开了 `GET /students/{id}/prompt-preview`：不打 LLM 就能看到「同一道题、两种画像」的提示词差在哪，调提示词时迭代快得多。

新增 `tests/test_personalize.py`（21 例）+ `tests/test_repository.py`（7 例，内存 SQLite，不依赖 MySQL），`run_tests.py` **66/66**。T4 持久化回归 `smoke_db.py` 复跑通过（`hint_level` 0→2→3）。

**留给 T7**：备课链路和学生链路目前是两条独立的路，教师备课还不看课堂里的学生画像。

## T7 教师备课资源（2026-10-04 完成）

**先落地一个产品决策：课堂是「共用聊天室」。**（用户拍板，形态类似 QQ/微信群，后续可能再加单独聊）

这里有个必须讲清楚的冲突：群聊的上下文要共享，个性化又要求按学生隔离。拆法如下：

| | 是否按学生隔离 | 理由 |
|---|---|---|
| 对话上下文 `load_history` | **不隔离**（群聊全量） | 同学之间才能接力追问——A 问了死锁，B 接着问「那怎么预防」才接得上 |
| 教学状态 `load_last_state` | **隔离** | 否则 B 会继承 A 讲到第 3 层的深度，个性化直接失效 |

`load_history` 的 `user_id` 参数就是「群聊 / 单独聊」开关：不传 = 群聊（当前），传了 = 只看这个人的对话线。将来做单独聊不用改结构，传个参数就行。

**回归验证**：改成群聊上下文后重跑 `smoke_personalize.py`，新学生 C 仍然是 `hint_level=0`、532 字，没有被同课堂 A/B 的详细讲解带偏。原因是状态仍按学生隔离，C 恢复出空状态，`topic_changed` 让深度归零。

**新增两个模块，刻意分开**：

- `agent/resource_agent.py`：主题 → 检索知识库 → 结构化大纲。`PptOutline` 用 Pydantic 校验，**解析失败就抛 `OutlineError`**——半份大纲比报错更坑教师，他会拿着缺页的讲义去上课
- `agent/resource_pptx.py`：大纲 → .pptx。只做排版不碰内容，改大纲结构时不必动排版代码

**sections 带要点而不是纯标题**：计划里 `"sections": []` 看着像字符串数组，但纯标题渲染出来每页都是空的，教师还得自己填——那就没帮上忙。现在是 `{title, points}`，API 里另外给一份 `section_titles`（扁平字符串数组）给前端用。

**三个刻意加的护栏**：

1. **权限**：`_require_teacher()` 要求调用者是该课堂成员**且 `role='teacher'`**。学生不能拿它批量生成讲义
2. **空壳大纲报 502**：模型偶尔返回结构合法但内容全空的 JSON，渲染出来就是一张封面的 PPT。`is_shell()` 认出来直接报失败让教师重试
3. **文件名清洗**：主题是自由输入的，「进程/线程」这种带斜杠的名字直接当文件名会在 Windows 上写出奇怪的目录

**验收**（`python smoke_resource.py`，4 次调用）：主题「死锁」检索命中，产出 4 章节 × 4 要点 + 教学目标 4 条 + 常考要点 / 例子 / 提问 / 总结；PPT 41KB，下载字节数与本地一致且是 zip 结构（PK 开头）；学生调用 403、非成员教师 403；带斜杠的主题文件名合法。`run_tests.py` **81/81**（新增 `tests/test_resource.py` 13 例）。

**新增依赖**：`python-pptx` 1.0.2（顺带装了 lxml、XlsxWriter）。换机器部署要记得装，**第十二阶段整理项目时要补一份 requirements.txt**（目前仓库里没有）。

## 前置基础

- Python 异步（`async/await`、`asyncio.to_thread`）
- FastAPI 路由、请求模型校验、流式响应（SSE）
- Pydantic v2（`BaseModel`、`Field`、`model_config = ConfigDict(extra="forbid")`）
- LangChain 消息类型（HumanMessage / AIMessage / SystemMessage）与 `astream`
- 向量检索基本概念：切分、嵌入、相似度搜索

## 覆盖范围与未覆盖模块

- 覆盖：`backend/` 全部 Python 源码（app.py、agent/ 下全部模块、tests/、配置）。
- 未覆盖：`frontend/`（由其他成员负责，本套课程不涉及）；Judge0 外部服务内部实现；Ollama / LLM 服务端内部实现。
- 不确定边界：FastEmbed 首次运行会联网下载模型权重，离线环境下的行为未验证（第 4 课标出）。

---

## 第 1 课：一次聊天请求如何走完后端

**读完能回答**：从前端点发送，到屏幕上逐字出现回答，后端一共经历了哪些步骤？一次请求到底调用了几次大模型？在哪些地方会提前返回错误，错误是以什么形式给到前端的？

### 场景与目标

场景：学生在聊天框输入一句话点发送。目标不是"拿到答案"，而是后端先判断**他想学什么、有没有听懂**，再决定用哪一级教学策略回答，并且**边生成边推给前端**。理解本课之后，你应该能自己说清：请求在哪一步被校验、模型和提示词在哪一步拼装、状态（topic / hint_level）由谁保存。

两个必须先建立的前置概念：

- **SSE（Server-Sent Events）**：响应体不是一次性 JSON，而是 `Content-Type: text/event-stream` 的一条长流，服务端每生成一个片段就 `yield "data: {...}\n\n"`，前端边收边渲染。所以"逐字出现"是流的效果，不是前端在做打字动画。
- **状态放在客户端**：`topic`、`hint_level`、`misconception`、`resolved` 四件套和完整 `history` 都是**前端每次请求带过来**的（`ChatRequest`，`state.py:34-45`）。后端不存任何对话状态。这一点决定了后面 T3 接 MySQL 时要改什么。

### 主链与阅读顺序

```
前端 TutorChat.tsx:92 fetch('/chat')
  → app.py:129 @app.post + :130 限流 10/minute
  → app.py:133 get_llm(provider)              ← 出口①：503
  → app.py:137 清理过期会话（to_thread）
  → app.py:139 deserialize_history()
  → app.py:153 assessment_graph.ainvoke()     ← 出口②：502（第 2 课展开）
  → app.py:160-169 按 session_id 检索 PDF 证据
  → 分支：topic == "unknown"？（app.py:171）
      ├─ 有 PDF  → doc_stream()   app.py:174-203
      └─ 无 PDF  → unknown_stream() app.py:205-230
  → 正常路径 event_stream()        app.py:232-336
      contains_code → 取策略 → 拼 misconception_note → 拼 system → 注入证据
      → llm.astream(messages)  ← 出口③：流内 error 事件
  → token 事件… → state 事件 → [DONE]
```

必读（按顺序）：

1. `backend/app.py:129-131` — 路由与限流装饰器。
2. `backend/app.py:132-135` — 取模型，唯一的"配置类失败"出口。
3. `backend/app.py:99-115` — `deserialize_history()`，理解历史是谁给的。
4. `backend/app.py:142-158` — 组装 `TutorState` 并跑图。
5. `backend/app.py:160-171` — 取 PDF 证据与三岔路口。
6. `backend/app.py:232-336` — `event_stream()`，本课主体。
7. `backend/app.py:317-329` — 真正产出 SSE 的三段 `yield`。

选读：`backend/agent/state.py:34-45`（请求体长什么样、各字段上限）；`backend/app.py:299-315`（gemini 特判，见下文"坏味道"）。
暂缓：图内部两个节点（第 2 课）、证据怎么检索出来的（第 4 课）、代码执行细节（第 5 课）。

### 沿流程带读

**第 1 关：限流**（`app.py:130`，`@limiter.limit("10/minute")`）
按客户端 IP 计数（`app.py:43`，`key_func=get_remote_address`）。超限由全局异常处理器（`app.py:55`）返回 429，根本进不到函数体。**注意这个数字**：10 次/分钟在调试阶段很容易撞上——你连着试几轮 `/chat` 就会 429，不是代码坏了。

**第 2 关：拿模型**（`app.py:133-135`）
`get_llm(body.provider)` 去 `graph.py:34` 构造客户端。缺 key 时抛 `ValueError`，被原地转成 **HTTP 503**，且**不会**进入后续逻辑。这是配置问题和运行问题的分界线：503 = 你没配好，502 = 配好了但模型调用崩了。

**第 3 关：历史反序列化**（`app.py:139` → `app.py:99-115`）
`history` 里每项可能是 Pydantic 对象也可能是裸 dict（两条分支都做了兼容），只认 `role == "user"` / `"assistant"`，其他角色**静默丢弃**。所以 system 角色的消息在这里活不下来——后端自己拼 system（`app.py:313`）。

**第 4 关：跑评估图**（`app.py:142-158`）
组装出 `TutorState`（`state.py:17-24`），注意 `llm` 实例本身被塞进了 state（`app.py:148`）——这是为了让图内部节点能用**本次请求**的模型，而不是全局单例。
`await assessment_graph.ainvoke(initial_state)` 是异步的，且内部会**两次调用大模型**（`extract_topic_node` 一次、`assess_understanding_node` 一次，见第 2 课）。任何异常一律吞成 **502**，前端拿不到具体原因。

> **代价提醒（重要）**：一次 `/chat` 实际会打 **3 次**大模型——抽主题 1 次、评估理解 1 次、流式回答 1 次。前两次是完整 `ainvoke`（不流式、要等全部返回），用户感知的"首字延迟"里包含这两次往返。改造时把前两次合并成一次结构化调用，是最直接的降本提速点（属**分析建议**，本项目未实现，也未测量实际耗时）。

**第 5 关：取证据**（`app.py:160-169`）
`body.session_id` 默认空串，`if body.session_id else None` 判 falsy → `None` → 走 `else` 分支得到空串。也就是说**没有上传过 PDF 时这一步直接跳过**。`session_id` 只关联"上传的那份 PDF 向量库"，跟对话历史无关，别混淆。

**第 6 关：三岔路**（`app.py:171`）
`topic == "unknown"` 时：有 PDF 证据 → `doc_stream()`（直接依据文档回答，不苏格拉底）；没证据 → `unknown_stream()`（反问学生想学什么）。两个分支都是**独立实现的 SSE 生成器**，跟主路径 `event_stream()` 各写一遍 `yield` 逻辑——三处重复，改造时要一起改，别只改一处。

**第 7 关：主路径提示组装**（`app.py:232-315`）
顺序是：代码执行（235-251）→ 取策略（253）→ 拼 `misconception_note`（254-271）→ 按 `resolved` 选提示（273-295）→ 注入证据（297）→ 组装消息（299-315）。

- `strategy = HINT_STRATEGIES[assessment_state["hint_level"]]`（`state.py:9-14`）：四级策略表，这是整套教学行为的"大脑"。
- `resolved` 为真时替换成祝贺提示（274-279），此时**完全没有 strategy**——一个已经"懂了"的学生会收到 2-3 句祝贺，而不是继续讲解。
- `reveal_instruction`（281-285）：只有 `hint_level == 3` 才允许直接给答案，其余一律 "Do NOT give the direct answer."。**这就是苏格拉底式最核心的一行，也是 AI Classroom 要最先改掉的一行**——八股学习要的是讲解，不是启发。
- `add_rag_grounding()`（297 → `app.py:118-126`）：把英文的 `RAG_GROUNDING_RULES`（`app.py:34-41`）和证据拼到 system 尾部；没证据时原样返回。

**第 8 关：gemini 特判（坏味道）**（`app.py:299-315`）
`provider == "gemini"` 时，system 内容被**拼进最后一条 HumanMessage**；其他 provider 用正常的 `[SystemMessage, ...]`。这是把 provider 差异泄漏进业务流程，改造时要么删掉这个特判（新增的 openai 通道走 else 分支，不受影响），要么抽成统一的消息组装函数。

**第 9 关：流式产出**（`app.py:317-329`）
三段 `yield`：`token` 事件（每个片段一条）、末尾 `state` 事件（把 topic/hint_level/misconception/resolved 回传前端）、`data: [DONE]`。
`state` 事件是关键——**后端把更新后的学习状态交回前端保存**，前端下次请求再带回来。这就是"状态在客户端"的闭环，也正是 AI Classroom 里学生学习行为最早的采集点。

### 重要分支与失败出口

| 出口 | 触发条件 | 位置 | 前端看到什么 |
|---|---|---|---|
| 422 | 请求体不合法（未知 provider、message 超 1000 字、history 超 50 条、多传字段） | Pydantic + `extra="forbid"`（`state.py:28,35`） | HTTP 422，函数体根本没执行 |
| 429 | 同一 IP 超过 10 次/分钟 | `app.py:130` + `app.py:55` | HTTP 429 |
| **503** | 缺 API Key / provider 未知 | `app.py:133-135` | HTTP 503，detail 是具体提示 |
| **502** | 评估图内异常（模型超时、鉴权失败、返回不可解析） | `app.py:154-158` | HTTP 502，detail 被写死成一句通用文案 |
| **流内 error** | 已经返回 200 之后，`astream` 中途失败 | `app.py:330-334` | HTTP **仍是 200**，流里追加一条 `type=error` |

两条必须记住的非对称行为：

1. **502 会丢掉真实错误信息**——`app.py:157` 把 detail 写死，真正的异常只进服务端日志。调试期建议先临时把 `str(exc)` 打进日志或直接返回，定位完再改回去。
2. **流式一旦开始就没有回头路**。`event_stream()` 是生成器，`StreamingResponse` 在第一次 `yield` 之前就已经把 200 响应头发给前端了，所以后面的失败**只能**在流里补 error 事件，无法再改成 4xx/5xx。前端必须解析流里的 `type`，否则会把半截回答当成完整答案。三条路径（`doc_stream` / `unknown_stream` / `event_stream`）都实现了这个兜底，但**没有重试、没有回滚、没有清理**。

还有一个未验证的风险点：代码只处理 `chunk.content` 为字符串的情况（`app.py:185-191` 等处）。部分模型或网关返回的 `AIMessageChunk.content` 可能是**列表**（多模态块格式），届时 `json.dumps` 会序列化出一个数组而不是文本。原项目只跑过 ollama/groq/gemini，**新接的 openai 兼容通道尚未实跑通过**，标为 `待确认`，接上 key 冒烟时重点看这一点。

### 完整流程串联

用一句话走到底：前端首次发送 `{"message": "什么是死锁？"}`，无 `session_id`，`provider="openai"`，key 已配好。

1. 限流通过 → `get_llm("openai")` 读 `LLM_API_KEY` 构造 `ChatOpenAI`（`graph.py:62-78`）。
2. `history=[]` → `deserialize_history([])` 返回空列表 → `messages = [HumanMessage("什么是死锁？")]`。
3. `ainvoke` 进图：`extract_topic_node` 抽到主题（如 "deadlock"），与当前 topic（空）不同 → 返回 `topic` + 重置 `hint_level=0` + `topic_changed=True`（`graph.py:130-136`）；`assess_understanding_node` 见 `topic_changed` 直接短路返回全零（`graph.py:140-141`）。**第二次模型调用被省掉了**。
4. `session_id` 为空 → `rag_context = ""`。
5. `topic != "unknown"` → 进 `event_stream()`。
6. `contains_code("什么是死锁？")` → 不含特征词 → `code_output = ""`，跳过 Judge0。
7. `strategy = HINT_STRATEGIES[0]`（类比）；`misconception_note` = "You don't yet know their specific misconception."；`resolved=False` → `reveal_instruction = "Do NOT give the direct answer."`。
8. 拼出英文 system 提示 → 无证据，`add_rag_grounding` 原样返回 → `messages = [SystemMessage(提示), HumanMessage("什么是死锁？")]`。
9. `astream` 逐段 `yield` token → 前端逐字渲染 → 末尾 `state` 事件 `{topic:"deadlock", hint_level:0, misconception:"", resolved:false}` → `[DONE]`。
10. 前端把这个 state 存起来，下一轮连同 history 一起发回来。**服务端至此什么都不记得。**

失败变体：如果 `LLM_API_KEY` 是空的，流程停在第 1 步，返回 503（**当前 `.env` 就是这个状态**）；如果 key 填错了，第 3 步的 `ainvoke` 抛鉴权异常，返回 502；如果前两步都过了、`astream` 中途断流，前端收到半截回答 + 一条 error 事件，HTTP 状态却是 200。

### 改造落点（AI Classroom）

按改动代价从小到大排：

1. **`app.py:281-295` 的提示基调**：从"苏格拉底不给答案"改成"八股讲解 + 分层展开"。这是产品定位差异，必改。
2. **`app.py:34-41` 的证据规则**：现在是英文、且针对 PDF 页码行号。改成中文知识库规则，引用形式换成"知识点出处"。
3. **`app.py:232-251` 删掉代码执行分支**：八股问答用不上，且 `contains_code()` 会把带 `{`、`;` 的中文提问误判成代码（第 5 课详述）。删掉能显著降低 `event_stream` 复杂度。
4. **`app.py:299-315` 去掉 provider 特判**：新通道走 else，保留即可，但建议抽成统一的消息组装函数。
5. **`app.py:325-328` 的 state 事件扩展**：这是把"学生学习状态"从客户端搬到服务端的天然入口。T5 阶段在这里顺手把 topic / hint_level / 提问次数写进 `StudentProfile`，比另起一套埋点更自然。
6. **history 来源改造（T3/T5）**：现在靠前端回传，改成后端按 `user_id + classroom_id` 从 MySQL 读最近 N 条。届时 `deserialize_history` 要改成从库里取，且要处理 token 预算（不能无限塞历史）。
7. **三次模型调用合并（分析建议，非必需）**：把抽主题与评估合并成一次 JSON 输出，能省一次完整往返。收益需要实测，未测量前不要当结论讲。

### 流程回顾与自测

一次 `/chat` = 限流 → 取模型 → 还原历史 → 跑图（1-2 次模型调用）→ 取证据 → 三选一流式分支 → 拼提示 → 流式生成 → 回传 state。三个失败出口分别是 503（配置）、502（图内异常，信息被吞）、流内 error（HTTP 已 200）。

**自测任务（不要求运行项目）**：合上代码，口述一遍——① 前端每次请求带回来哪些字段，服务端自己保存了什么；② 一次请求最多调用几次大模型，分别在哪个函数里；③ 什么样的失败会让前端收到"HTTP 200 但答案不完整"。答不上来就回到"沿流程带读"重看第 4、9 关和失败出口那张表。

**状态**：已展开

---

## 第 2 课：LangGraph 如何判断学生"懂没懂"

**读完能回答**：hint_level 是怎么升降的？换话题、答对、模型返回一堆废话分别会怎样？图到底改了状态的哪几个字段，哪些字段它根本不碰？

### 场景与目标

场景就是第 1 课第 4 关那一行 `await assessment_graph.ainvoke(initial_state)`（`app.py:153`）的内部。学完你应该能自己说清：一次请求里 topic 什么时候变、hint_level 靠什么升上去、模型说胡话时系统怎么兜底。

三个必须先建立的前置概念，不懂就看不懂后面：

- **节点返回的是"增量"，不是"全量"**：节点返回 dict，LangGraph 把它**合并**进状态；返回 `{}` 表示"我什么都不改"。
- **`add_messages` reducer**（`state.py:18`）：`messages` 字段带这个注解，语义是**追加**而非覆盖。本项目两个节点都不返回 messages，所以它实际没被触发——但你要知道它的存在，否则改图时容易误判。
- **图不生成任何回答**：它只更新 `topic` / `hint_level` / `misconception` / `resolved` 四个字段，`messages` 原封不动传出去，最后由 `app.py:317` 拿去喂给 `astream`。这个"评估与生成分离"的分层是对的，改造时两边可以独立换。

### 主链与阅读顺序

```
app.py:153  assessment_graph.ainvoke(initial_state)
  → graph.py:195  assessment_graph（模块级单例，导入时编译一次）
  → extract_topic_node           graph.py:101-136   第 1 次 LLM
  → assess_understanding_node    graph.py:139-181   第 2 次 LLM（三种情况可短路）
  → 返回合并后的状态 → 回到 app.py:171 起的分支判断
```

必读（按顺序）：

1. `backend/agent/graph.py:183-195` — `build_assessment_graph()`：两个节点、一条线、没有条件边。
2. `backend/agent/state.py:17-24` — `TutorState`，看清哪些字段是图负责的。
3. `backend/agent/graph.py:101-136` — `extract_topic_node`。
4. `backend/agent/graph.py:139-181` — `assess_understanding_node`，本课重点。
5. `backend/agent/graph.py:83-98` — `AssessmentResult` 与 `parse_assessment_result`，模型的"防乱说闸门"。
6. `backend/agent/prompts.py:23-46` — `ASSESS_UNDERSTANDING_PROMPT`，规则写在这里，实现在节点里，两边要对照看。

选读：`backend/agent/state.py:9-14`（`HINT_STRATEGIES`，注意它**在图里没被用到**）；`backend/tests/test_graph.py:75-160`（四个测试正好覆盖本课四个分支）。
暂缓：证据怎么检索（第 4 课）；app.py 拿这四个字段做了什么（第 1 课已讲）。

### 沿流程带读

**图的结构：一条两节点流水线，不是循环**

`graph.py:183-195`：`add_node` 两个 → `set_entry_point` → 线性 `add_edge` → `compile()`。**没有条件边、没有回边**——"状态机"这个名字有点误导：它每次请求从头跑一遍，图自己不保留上一轮的任何东西（上一轮状态靠前端回传，见第 1 课）。

`assessment_graph` 在模块导入时编译一次，是全局单例；但每次用的 **LLM 来自 `state["llm"]`**（`app.py:148` 把实例塞进状态），所以不同请求可以走不同模型。`tests/test_graph.py:137-160` 用一个 `_SequenceLlm` 按顺序返回两次不同结果，专门验证"图确实用的是请求级的那个模型"。

**节点 1：`extract_topic_node`（graph.py:101-136）**

- 102-103：从 `state["messages"]` 里筛出 `HumanMessage`，取**最后一条**作为"学生最新说了什么"。
- 105-109：`current_topic` 为空时填 `"unknown"`，再和最新消息一起填进 `EXTRACT_TOPIC_PROMPT`。
- 113-114：模型返回 `"same"` → `return {}`，沿用旧 topic，其它字段一概不动。
- 117-125：模型返回 `unknown / none / no topic / not mentioned` 之一时：若当前 topic **不是** unknown → `return {}`（**保住旧主题**，防止学生一句"嗯"把话题清空）；否则返回 `{topic:"unknown", hint_level:0, misconception:"", resolved:False}`。
- 127-128：抽出的新主题名与当前同名（都已 lower）→ `return {}`。
- 130-136：确认是新话题 → 小写 topic + `hint_level` 归零 + `misconception` 清空 + `resolved=False` + **`topic_changed=True`**。

`topic_changed` 是本课唯一"跨节点通信"的字段：它不在 `ChatRequest` 里，只在图内部产生、由下一个节点消费（`state.py:24` 声明为 `NotRequired`，`app.py:149` 在初始状态里显式给了 `False`，所以节点用 `state.get("topic_changed", False)` 读它）。

两个健壮性缺口，是读代码读出来的，不是猜的：

- 127 行的同名判断**没有 `strip()`**。模型多返回一个句号或尾随空格（`"recursion."`）就会被当成新话题，把 hint_level 打回 0，学生的进度白攒。
- 102-103 若 messages 里一条 `HumanMessage` 都没有，`user_messages[-1]` 会 `IndexError`。`app.py:140,143` 总会追加新消息，线上不会触发；但单独调这个节点时会。四个测试都带了 HumanMessage，等于绕开了这个边界。

**节点 2：`assess_understanding_node`（graph.py:139-181）**

三道短路闸门，按顺序判断：

1. 140-141：`topic_changed` 为真 → 返回全零。**刚换过话题就不评估**，因为新一轮还没有可判断的迹象。
2. 143-144：`topic == "unknown"` → 全零。
3. 146-147：`len(state["messages"]) < 2` → 全零。**第一轮提问（只有一条 HumanMessage）不评估**，直接 hint_level=0。注意这里数的是**全部**消息，`AIMessage` 也算在内。

过了闸门才真正调模型（149-159）：把整个 messages 拼成 `User:` / `Tutor:` 开头的文本（150-152，只判断是不是 `HumanMessage`，其余一律算 Tutor），填进 `ASSESS_UNDERSTANDING_PROMPT`，`ainvoke`。

结果处理（161-181）：

- **解析**：`parse_assessment_result()`（89-98）先剥掉 ```json 围栏，再用 `AssessmentResult`（83-86）严格校验——`StrictBool` 拒绝 `"false"` 这种字符串布尔，`hint_level` 必须 0-3，`misconception` 最长 500 字。测试 `test_graph.py:53-61` 专门验这两条。
- **`resolved=True`**（164-169）→ 返回 `{resolved:True, hint_level:0, misconception:""}`：一轮结束，等级归零，下一题重新开始。
- **未 resolved**（171-175）→ `hint_level = max(state["hint_level"], assessment.hint_level)`（173）。**只升不降**——即使模型这次给了更低的级别也不会退回去。`prompts.py:44` 里写了同一条规则，实现和提示双重保险。
- **解析失败**（176-181，捕获 `JSONDecodeError` / `TypeError` / `ValidationError` / `ValueError`）→ **沿用旧值**，不抛异常、不重试、不打日志。

**这四行静默降级是本课最值得警惕的设计。** 模型返回一句人话（而不是 JSON）时，学生完全无感，但 hint_level 卡住不动，外在表现是"怎么问都不给答案"。从前端看不出来，从 HTTP 状态码也看不出来（它不是异常，不会变 502），只能看服务端日志——而这里恰恰没有日志。

一个未覆盖的失败路径（**分析，未实测**）：176 行的 except 不含 `AttributeError`。若某些网关让 `response.content` 不是字符串，`raw.strip()`（第 90 行）会抛 `AttributeError` 并冒泡到 `app.py:154`，最终吃掉成 502。原项目三家 provider 都返回 str，新增的 openai 兼容通道尚未实跑，标为 `待确认`。

**`HINT_STRATEGIES` 不在这个图里**（`state.py:9-14`）：四级策略表只在 `app.py:253` 被消费一次。图只产出一个 0-3 的数字，"怎么表达"完全是回答阶段的事。这个分层是好的——你改造时可以只换策略表，一行都不动图。

### 重要分支一览

| 情形 | 位置 | 结果 |
|---|---|---|
| 模型返回 `same` | `graph.py:113` | `{}`，一切不变 |
| 返回 unknown 且当前已有主题 | 117-119 | `{}`，保住旧主题 |
| 返回 unknown 且当前无主题 | 120-125 | `topic=unknown` + 全零 → 第 1 课的 `unknown_stream` |
| 确认是新话题 | 130-136 | 重置四件套 + `topic_changed=True` |
| `topic_changed=True` | 140-141 | assess 短路，返回全零 |
| `topic == "unknown"` | 143-144 | assess 短路，返回全零 |
| 消息少于 2 条 | 146-147 | assess 短路，返回全零 |
| 合法 JSON 且 resolved | 164-169 | `resolved=True`，hint_level 归零 |
| 合法 JSON 未 resolved | 171-175 | `hint_level = max(旧, 新)`，只升不降 |
| 非法 JSON / 校验失败 | 176-181 | **沿用旧值，静默降级** |

### 完整流程串联

用四轮对话走一遍，`topic` 与 `hint_level` 的实际变化：

1. **"什么是死锁？"**（history 空）→ messages 只有 1 条 → extract 得 `deadlock`，与当前 `""` 不同 → 重置 + `topic_changed=True` → assess 撞短路 1 → 结果 `topic=deadlock, hint_level=0`。回到 app.py 走 `HINT_STRATEGIES[0]`（类比）。
2. **"不太懂"** → extract 返回 `same` → `{}` → assess：不是新话题、不是 unknown、messages 已 3 条 → 调模型 → `{"resolved":false,"hint_level":1,...}` → `max(0,1)=1` → 走 `HINT_STRATEGIES[1]`（收窄提示）。
3. **"懂了，就是两个进程互相等对方释放锁"** → extract `same` → assess → 模型给 `{"resolved":true,...}` → 返回 `{resolved:True, hint_level:0, misconception:""}` → `app.py:273` 的分支切成祝贺提示（`app.py:274-279`），学生收到 2-3 句祝贺，**本轮不再讲解**。
4. **"那活锁呢？"** → extract 返回 `livelock` ≠ `deadlock` → 重置四件套 + `topic_changed=True` → assess 撞短路 1 → 新话题从头开始。

失败变体：第 2 轮时模型返回 `"很抱歉，我无法回答这个问题"` → `json.loads` 抛 `JSONDecodeError` → 176 行捕获 → hint_level 仍是 0，misconception 仍是**上一轮的旧值**。注意这个细节：旧 misconception 会被带进下一轮提示，`app.py:254-259` 会把它当作"学生当前的误解"讲给模型听。没有重试、没有日志、没有告警。

### 改造落点（AI Classroom）

按依赖关系排：

1. **重新定义 hint_level 的语义**（最优先）：现在是"启发到第几级"，八股场景应改成"讲解深度"——0 一句话结论 / 1 标准答案 / 2 原理展开 / 3 举例 + 面试追问。改 `state.py:9-14` 的表 + `prompts.py:23-46` 的规则即可，**图一行都不用动**。
2. **misconception 是画像原材料，现在用完即弃**：它每轮都被算出来，却只活在当轮提示里。T5 阶段把它落库、按知识点累积，就是 StudentProfile 的薄弱点——这是"个性化回答"最直接的证据来源，不用另做一套分析。
3. **extract_topic 改成"知识点识别"**：八股有固定知识点表，第 4 课建库后可以先拿知识点名做匹配，匹配不到再让模型抽。更稳，还可能省掉一次模型调用。
4. **静默降级必须补观测**：176-181 至少加一条日志（记录模型的原始返回），否则"学生卡住"这类问题无从定位。
5. **prompt 的 few-shot 是英文的**（`prompts.py:6-13`，其中 `"explaint recursion"` 还拼错了），中文八股要换成中文示例。
6. **短路 3（首轮不评估）要改**：教学 Agent 的首轮应该依据学生画像做难度定级，而不是一律 hint_level=0。这是注入 StudentProfile 的入口。
7. **两次模型调用可合并**（呼应第 1 课）：抽主题 + 评估合成一次结构化输出；若第 3 条落地，这里能降到只剩一次。收益需实测，未测量前别当结论讲。

### 流程回顾与自测

图 = `extract_topic` → `assess_understanding` 两节点线性流水线，每次请求从头跑，上一轮状态靠前端回传。它只更新四个字段（topic / hint_level / misconception / resolved），不碰 messages，也不生成任何回答。hint_level 只升不降（`graph.py:173`），resolved 时归零（167），模型说胡话时静默沿用旧值（176-181）。

**自测任务（不用跑代码）**：① 说出 assess 节点的三道短路分别是什么、判断顺序如何；② 学生连问三轮都没懂，hint_level 会变成几，哪一行的代码决定了它绝不会退回；③ 模型返回非 JSON 时，前端和学生会观察到什么现象，你作为开发者要怎样才能发现。答不上来，回到"沿流程带读"的节点 2 和"完整流程串联"的失败变体重看。

**状态**：已展开

---

## 第 3 课：模型通道是怎么切换的（对接 DeepSeek 的落点）

**读完能回答**：provider 是怎么路由到具体模型的？我要新增一个 OpenAI 兼容通道，该改哪几处？

**主链**

触发（请求里的 `provider` 字段） → 入口 `graph.py:33 get_llm()` → `get_model()` 解析模型名 → 按 provider 分支构造客户端 → 结果：可 `ainvoke` / `astream` 的 LLM 实例

**必读路径与符号**

1. `backend/agent/state.py:44` — `provider: Literal["ollama", "groq", "gemini"]`。输入：请求字段。输出：白名单校验（不在白名单内直接 422）。下一步：`get_llm`。
2. `backend/agent/graph.py:16-30` — `DEFAULT_MODELS` 与 `get_model()`：优先级为 `<PROVIDER>_MODEL` 环境变量 > 与 `LLM_PROVIDER` 匹配时的 `LLM_MODEL` > 内置默认。输出：模型名字符串。下一步：分支构造。
3. `backend/agent/graph.py:39-61` — 三个分支：ollama 走 `ChatOllama`（无 key 要求）；groq 走 `ChatGroq`（缺 `GROQ_API_KEY` 抛 `ValueError`）；gemini 走 `ChatGoogleGenerativeAI`（缺 `GOOGLE_API_KEY` 抛错）。输出：LLM 实例或异常。下一步：回到第 1 课第 3 步。
4. `backend/env.example` — 环境变量全貌：`LLM_PROVIDER`、`LLM_MODEL`、各 provider key 与 per-provider 覆盖项、上传与会话限制、`ALLOWED_ORIGINS`。

**选读证据**：`backend/tests/test_graph.py:64-73` 验证"groq 默认用 gpt-oss-120b""缺 key 时报错"——新增通道后应照此补两个测试。

**改造落点提示**：新增 OpenAI 兼容通道需要改三处——`state.py:44` 的 Literal、`graph.py` 的分支（用 `ChatOpenAI` + `base_url`）、`.env` 增加三个变量；`app.py:299` 的 gemini 特判不需要动。

**暂缓阅读**：嵌入模型的选型与加载（第 4 课，那是另一个模型，与对话模型独立）。

**状态**：大纲

---

## 第 4 课：RAG 从上传到检索（改造重点）

**读完能回答**：PDF 是怎么变成可检索片段的？检索结果怎样进入提示词？要换成常驻的中文八股知识库，需要动哪些？

**主链**

触发（`POST /upload`） → 入口 `app.py:349 upload_document()` → 落临时文件并校验 PDF 头 → `build_index()` 切分+嵌入+入库 → 结果：`session_id`；随后每次 `/chat` 由 `get_relevant_context()` 检索并注入提示

**必读路径与符号**

1. `backend/app.py:349-415` — `upload_document()`：只接受 `.pdf`；边写临时文件边累加字节数，超限返回 413；校验文件头 `%PDF-`；`build_index()` 失败返回 422；最后入 `sessions` 字典。输入：上传文件。输出：`{"session_id", "message"}`。下一步：索引。
2. `backend/agent/rag/indexer.py:15-65` — `build_index()`：`PyPDFLoader` 载入 → `RecursiveCharacterTextSplitter(chunk_size=900, chunk_overlap=150, add_start_index=True)` 切分 → 为每个 chunk 补 `page_number` / `line_start` / `line_end` 元数据 → 额外追加一页 `front_matter`（首屏内容，供标题作者类问题使用）→ `Chroma.from_documents()`。输出：内存向量库。下一步：检索。
3. `backend/agent/rag/indexer.py:10-12` — `get_embeddings()`：`FastEmbedEmbeddings(model_name="BAAI/bge-small-en-v1.5")`，带 `lru_cache`。**这是英文模型**，中文八股知识库必须换成中文嵌入（本地 `bge-small-zh-v1.5` 或云端 `bge-m3`）。首次运行会下载权重，离线行为待确认。
4. `backend/agent/rag/retriever.py:12-51` — `get_relevant_context()`：`similarity_search(query, k=5)`；命中 `METADATA_QUERY` 正则（title/author/abstract 等）时额外检索一页 front_matter；按 `(page, line_start, content)` 去重；拼接成 `[PDF page X, lines Y-Z]` 格式。输出：证据字符串。下一步：注入提示。
5. `backend/app.py:34-41` 与 `118-126` — `RAG_GROUNDING_RULES` 与 `add_rag_grounding()`：把"只依据证据、必须引用页码行号、证据不足要说没有"的规则拼进 system 提示。输入：提示 + 证据。输出：加固后的提示。

**选读证据**：`backend/tests/test_app.py:41-56` 验证"有证据时拼入规则与证据、无证据时原样返回"；`:80` 验证"每次上传用独立 collection 且清理临时文件"。

**改造落点提示**：AI Classroom 需要的是**服务启动时就常驻**的知识库，而不是"每次上传、TTL 一小时"的会话库。改造方向是让 `build_index()` 支持目录批量导入 + 持久化目录，并去掉 `sessions` 的 TTL 语义。

**暂缓阅读**：会话过期与淘汰策略（第 6 课）。

**状态**：大纲

---

## 第 5 课：代码执行链路（AI Classroom 用不上，可跳过）

**读完能回答**：学生贴代码时会发生什么？为什么这条链路在八股学习场景里应该删掉？

**主链**

触发（用户消息含代码特征） → 入口 `app.py:235 contains_code()` → `extract_code()` + `detect_language()` → `execute_code()` 调用 Judge0 → 结果：真实输出被写进 `misconception_note`

**必读路径与符号**

1. `backend/agent/tools.py:117-120` — `contains_code()`：字符串特征匹配（`def `、`class `、```、`print(` 等），命中就走代码路径。
2. `backend/agent/tools.py:96-115` — `extract_code()`：优先取 ``` 围栏内容，否则从第一个像代码的行起全取。
3. `backend/agent/tools.py:81-94` — `detect_language()`：关键字猜语言，猜不出默认 python。
4. `backend/agent/tools.py:21-79` — `execute_code()`：base64 编码后 POST 到 Judge0（有 `JUDGE0_API_KEY` 走 RapidAPI，否则走 `JUDGE0_URL` 公共实例），解析 stdout/stderr/compile_output，超时 30 秒。
5. `backend/app.py:261-271` — 结果拼进提示：学生直接问输出就直给，否则让模型据此反馈。

**建议**：这条链路与"计算机八股问答"无关，且 `contains_code()` 的特征匹配会把含 `{`、`;` 的普通提问误判为代码。改造时建议移除该分支，可显著降低提示组装复杂度。

**状态**：大纲

---

## 第 6 课：限流、会话与清理（服务边界）

**读完能回答**：上传的 PDF 存在哪、什么时候被删？限流和 CORS 在哪配置？

**必读路径与符号**

1. `backend/app.py:27-32` — `MAX_UPLOAD_BYTES`（默认 10MB）、`MAX_DOCUMENT_SESSIONS`（默认 100）、`SESSION_TTL_SECONDS`（默认 3600）。输入：环境变量。输出：全局上限。
2. `backend/app.py:46-53` — `DocumentSession` 数据类与全局 `sessions: dict`。向量库存**内存**，进程重启即失效。
3. `backend/app.py:78-96` — `cleanup_expired_sessions()`（按 TTL 清理）与 `evict_oldest_session_if_full()`（超上限淘汰最旧）。调用点：`/chat` 开头（app.py:137）与 `/upload` 前后（app.py:405-406）。
4. `backend/app.py:43-67` — slowapi 限流（`/chat` 10 次/分钟、`/upload` 3 次/小时）与 CORS（白名单来自 `ALLOWED_ORIGINS`）。
5. `backend/app.py:418-423` — `DELETE /sessions/{session_id}` 主动删除。

**注意**：内存态会话是本项目最大的架构约束——它决定了"知识库"目前不能跨重启存在，也不能多实例共享。MySQL（T3 阶段）进来后，聊天记录与画像应落库，向量库则要考虑持久化目录。

**状态**：大纲

---

## 第 7 课：测试怎么跑，改动怎么验证

**读完能回答**：我改完代码后，怎么确认没把原功能改坏？

**必读路径与符号**

1. `backend/tests/test_state.py:12-27` — `ChatRequestTests`：拒绝未知 history 角色、拒绝超长内容、拒绝未知 provider。这是请求模型的第一道防线。
2. `backend/tests/test_graph.py:44-72` — `AssessmentResultTests` 与 `ProviderConfigurationTests`：JSON 严格校验（拒绝字符串布尔、越界 hint）、provider 默认模型与缺 key 报错。
3. `backend/tests/test_graph.py:75-137` — `TopicTrackingTests`：主题切换与 hint 重置的三种情形，外加"异步图使用请求级 LLM"。
4. `backend/tests/test_app.py:23-80` — history 反序列化、RAG 加固拼接、过期会话清理、上传用独立 collection 并清理临时文件。

**验证建议**：每改一个模块就重跑测试，与基线对比。

**基线（2026-10-03 实测，未改动任何源码）**：

- 命令：在 `backend/` 下用 venv 的 python 执行 `python run_tests.py`
- 结果：**Ran 16 tests，FAILURES=0，ERRORS=0，全部通过**，耗时 0.137s
- 分布：test_app.py 5 个、test_graph.py 8 个、test_state.py 3 个

**为什么需要 `run_tests.py`**：`tests/` 目录没有 `__init__.py`，`python -m unittest discover -s tests` 会报 `Start directory is not importable`。`backend/run_tests.py` 用文件路径逐个加载测试模块绕开这个限制，并把 `backend/` 加入 `sys.path`、把工作目录切到 `backend/`（保证 `.env` 能被加载）。这是新增文件，不是源码改动。

**状态**：大纲（基线已实测）

---

## 自学提醒

若某文件或原理看不懂，请继续追问 AI；本套课程负责给出学习路径与题目，不提供逐行讲解。展开任意一课时，请指定课程编号，只更新该课内容。
