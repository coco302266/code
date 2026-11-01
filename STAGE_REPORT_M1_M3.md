# AI Classroom 第三阶段性开发总结报告（M1 – M3）

> **覆盖范围**：M1（聊天室 + 多智能体角色雏形）、M2（学习者画像 × 认知诊断 × 知识图谱）、M3（个性化资源生成算法库）
> **报告日期**：2026-10-22
> **代码基线**：`f4244bc`（M1+M2，收口提交）、`6bf6cbe`（M3 资源生成）
> **前序报告**：
> - `PROJECT_REPORT.md`（T0–T7，问题 1–10 / Bug 1–11 / 六张表原始设计）
> - `STAGE_REPORT_T8_T9.md`（T8–T9，问题 11–19 / Bug A–E / 表设计二次校验）
> - `TEST_REPORT.md`（T0–T9 系统测试）
> **本文定位**：**增量报告，不重复前序内容**。问题编号从 20 起、Bug 用数字编号 `M1`–`M7` 以示区分；数据库一节只写「M1/M2/M3 阶段对数据结构的扩展与新增内存资产设计」，原始表设计理由见前序报告第 4 节。

---

## 一、当前进度总览

### 1.1 研究目标对照（4 方向 / 5 成果）

| 方向 / 成果 | 内容 | 本里程碑状态 |
|---|---|---|
| ① 学习者画像 / CDT / 知识图谱 | 画像 × 认知诊断 × 图谱拓扑 | ✅ **M2 已达标**（标定题库 + IRT-lite + Felder-Silverman + 8 节点图谱） |
| ② 个性化资源生成 | 按画像生成学习资源 | 🟡 **M3 算法库成型**（5 类资源 + 难度控制 + few-shot + 零-LLM 题库组卷），尚未接前端 |
| ③ 多智能体协作 | ≥4 类角色 Agent 协作 | 🟡 **仅角色定义**（M1 的 tutor/teacher/peer/examiner 提示词 + 解析），真协作（agent_runs 表）未做 |
| ④ 多场景自适应 | 1v1 / 小组 / 辩论 + 评估报告 | 🟡 **仅 1v1 聊天室**（M1），多场景与评估报告未做 |
| 成果 #1 算法库 | 画像/CDT/资源算法 | 🟡 半成（M2+M3 已沉淀 `pretest.py` / `knowledge_graph.py` / `resource_gen.py`） |
| 成果 #2 多智能体原型 | ≥4 角色 | 🟡 角色提示词层就绪，协作架构未实现 |
| 成果 #3 多场景 Demo | 可演示 | ⬜ 仅后端 1v1，无多场景 |
| 成果 #4 评估报告 | 效果评估 | ⬜ 未做 |
| 成果 #5 软著材料 | 源码 + 文档 | 🟡 源码已提交（`f4244bc`+`6bf6cbe`），软著文本未写 |

### 1.2 本里程碑实际交付

| 交付物 | 类型 | 说明 |
|---|---|---|
| `agent/roles.py` | 新增 | M1 四角色提示词库 + `build_role_system` |
| `agent/orchestrator.py` | 新增 | M1 langgraph 角色解析图 |
| `agent/knowledge_graph.py` | 新增 | M2 8 节点图谱 + 先修闭包 + 拓扑推荐 |
| `agent/pretest.py` | 新增 | M2 标定题库 + IRT-lite(1PL) + Felder-Silverman 认知风格 |
| `agent/resource_gen.py` | 新增 | M3 5 类资源算法库 + 难度控制 + 零-LLM 题库组卷 |
| `db/models.py` | 修改 | `classrooms.is_private`、`messages.agent_role`、`student_profiles` 三列扩展 |
| `db/init_db.py` | 修改 | `ensure_new_columns` 补 M1/M2 列（均 nullable 避开 MySQL 1138） |
| `db/repository.py` | 修改 | `create_classroom` 扩 is_private/members；`list_messages` 修 `.unique()` 挂错；`save_message` 透传 agent_role |
| `app.py` | 修改 | 新端点：`POST /classrooms/{id}/messages`、`GET /messages?after=`、`/pretest`、`/pretest/submit`、`/knowledge-graph`、`POST /resources/generate`；顺手补 `get_knowledge_context` 陈年缺失 import |
| `tests/test_cdt.py` | 新增 | 25 测试（IRT 单调性、组卷题量、先修拦截） |
| `tests/test_resource_gen.py` | 新增 | 30 测试（含 fake-LLM 编排 e2e） |
| `verify_live.py` / `verify_chat_live.py` | 新增（未提交） | 本机实跑冒烟脚本 |

### 1.3 测试与验证

| 测试 | 成本 | 结果 |
|---|---|---|
| `run_tests.py` 单元测试 | 零（不联网） | ✅ **118 → 148**（M2 +25、M3 +30） |
| `verify_live.py` 真实服务 e2e | 零（不调 LLM） | ✅ **16/16**（群聊/私聊/增量/agent_role/组卷/submit/图谱/题库组卷/门禁） |
| `verify_chat_live.py` 真实 LLM 链路 | 1 次 LLM | ✅ HTTP 200、162 SSE 事件、sources 正确上报 4 片资料 |

**关键变化**：10-21 旧记录写「后端无法运行、MySQL 在另一台机器、无 LLM」——本次核实**已不实**：本机 `.env` 已配齐 MySQL+DeepSeek+HF 镜像，`dotenv` 已装，`venv` 可用、外网通。M1/M2/M3 已在**真实 MySQL + 真实 LLM** 下端到端跑通（详见第六节）。

---

## 二、遇到的问题、解决方法与理由

> 前序报告已记录问题 1–19。本文从问题 20 起。

### 问题 20：聊天室形态下，群聊 / 私聊 / 角色发言 / 增量刷新如何落地

**问题**：研究目标要求多智能体 + 多场景，第一步 M1 先做「聊天室」原型。需要支持：多人群聊、两人私聊、AI 以不同角色（tutor/teacher/peer/examiner）发言、前端增量刷新（长轮询只拿新消息）。

**解决**：
- 私聊用 `classrooms.is_private`（BOOLEAN 位标记，2 人 DM），**不新建表**；创建课堂时一次性把参与者加为成员。
- AI 角色用 `messages.agent_role` 列（tutor/teacher/peer/examiner），人类消息该列为 NULL。
- 新增 `POST /classrooms/{id}/messages`（人类发言，不触发 AI）+ `GET /messages?after=`（增量轮询）。
- 角色提示词抽到 `agent/roles.py`，角色解析用 `agent/orchestrator.py`（langgraph StateGraph 取系统提示）。

**为什么这样**：
1. **私聊复用 classroom 表而非新建 dm 表**——DM 本质是「只有两人的课堂」，所有既有权限校验、消息查询逻辑都能复用；新建一张表就要双写两套查询与权限，且前端要区分两套数据模型。用一列布尔位零成本区分群聊/私聊。
2. **增量轮询用 `after=最后一条消息 id` 而非时间戳/游标**——消息 `id` 自增且单调，`WHERE id>after` 命中 `idx_message_classroom(classroom_id,id)` 复合索引，避免每次拉全量（群聊可能上百条），也天然支持断线重连只补增量。
3. **角色只存一个枚举列而非「角色对话表」**——M1 是雏形，角色只影响「AI 以什么身份说这句话」，和那条 assistant 消息同生共死，没有独立生命周期；真多智能体协作（M3+）才需要 `agent_runs`/`agent_messages` 表来记录多 Agent 间的交互。

### 问题 21：认知诊断（CDT）怎么做，才能既个性化又不烧额度、可复现

**问题**：T5 画像只有「知识点→分数」平表，缺两件事——知识点间依赖（没学进程线程不该考死锁）、冷启动施测顺序。且诊断若每轮调 LLM 会烧额度、不可复现。

**解决**：用**标定题库 + IRT-lite（1PL 逻辑斯蒂）**做能力估计，不调 LLM。`probability_correct(θ,b)=1/(1+e^{-D(θ-b)})`，`update_ability` 用残差（实际−期望）驱动 θ；`build_pretest` 按先修闭包组卷，`pick_topics` 决定考哪几个。认知风格用 **Felder-Silverman** 简化版（两维度），样本不足返回 `ready:false` 不注入提示词。全部**纯函数、零 IO、零 LLM**，可单测。

**为什么**：
1. **不调 LLM**——诊断要反复施测、随时可重跑，LLM 不可复现且贵；用确定性标定题库（答案与难度已知），给定同一份作答必然得到同一份能力估计，出问题能追到具体哪题。这与 T5 画像「用规则不用 LLM」同一理由（省额度/可复现/可归因）。
2. **用 1PL 而非 2PL/3PL**——本系统不是正式考试，8 个知识点的小题库上区分度差异不大，1PL 只估能力（θ）一个参数，足够驱动「该考哪个 / 该讲多难」，参数少更稳。
3. **标定题库而非动态生成题**——题库答案与难度是硬编码的「金标准」，零-LLM 路径可复现，也是 M3 quiz 零-LLM 出卷的共同数据源。

### 问题 22：知识点之间的依赖关系放哪、怎么表示

**问题**：要「没学进程线程就不考死锁」，必须知道知识点先修关系；还要冷启动推荐施测顺序、推荐下一个该学什么。

**解决**：`agent/knowledge_graph.py` 用**邻接字典 `_NODES`**（8 知识点）+ 两层边：`prereq`（先修，A→B 表示学 A 前必会 B）、`related`（易混淆，出对比题用）。提供 `prerequisites`（先修闭包，DFS）、`recommend_next`（拓扑序 + 掌握度加权）、`mastery_of`、`graph_payload`（叠加掌握度）。**不引 networkx**。

**为什么**：
1. **不引 networkx**——图只有 8 节点，邻接字典 + 一次 DFS 够用，多一个重依赖只会增加队友复现成本。
2. **知识点名与 `knowledge/cs/**/*.md` 一级标题同构**——这些文档是 RAG 唯一检索源、也是画像 `knowledge_mastery` 的键；另起一套命名会让画像和图谱对不上（症状是「明明学过死锁却说 unexplored」）。
3. **掌握度不存图里**——图是静态结构，掌握度按 (user, classroom) 动态变化；混在一起等于把静态数据写成动态数据。由调用方传 mastery 叠加（`graph_payload`）。
4. **纯函数零 IO**——图谱是资产不是运行时状态，能直接进单测（`test_cdt` 25 个测试零成本覆盖）。

### 问题 23：个性化资源生成要支持多类型，怎么做才不重复造轮子

**问题**：研究目标②要求按画像生成个性化资源，类型至少有课件/测验/导图/速记卡/学习路径。每类各写一套提示词+解析会重复且易裂。

**解决**：`agent/resource_gen.py` 让 5 类资源**共用一套流程**（检索依据 → 注入画像 → 难度调整 → few-shot → 调模型 → 解析校验），每类只换「指令 + JSON 结构 + 难度策略」。提供 `resolve_difficulty`（三级回退 + 钳制）、`parse_resource`（宽容解析 + 钳制）、`build_quiz_from_bank`（命中 M2 题库走零-LLM 出卷）。

**为什么**：
1. **共用流程**——5 类的差异是「结构上」而非「流程上」的。把不变的抽成 `generate_resource` 编排、把变的拆成 per-type 的 prompt 模板 + Pydantic 模型 + few-shot 样例，新增一类只加一个模板；否则 5 套各写各的，解析/难度/个性化逻辑会各写一遍、各裂各的。
2. **难度三级回退夹到 [0.2,0.85]**——显式指定 > 整体水平 > 知识点掌握度，取更保守（更低）的一个，避免「整体 advanced 但单点没学过」被推到过难；极端值会让资源要么全是铺垫、要么全是边界情况，没法直接用。
3. **quiz 命中题库走零-LLM**——可复现、零额度、难度可控（按难度带出卷），与 M2 题库共用同一份「金标准」。

### 问题 24：本机能否真正端到端实跑（推翻旧判）

**问题**：10-21 旧记录写「后端无法运行、MySQL 在另一台机器、无 LLM」。用户指出本机有 MySQL，需要核实真实环境，否则 M1/M2/M3 只在 mock 层验证，风险未消除。

**解决**：探查 `.env` / `mysqld` / 外网，发现本机 `.env` 已配齐 MySQL+DeepSeek+HF 镜像、`dotenv` 已装、`venv` 可用、外网通。于是实跑：`init_db.py` 建表 → 起 uvicorn（DB 模式）→ `verify_live.py`（16/16）+ `verify_chat_live.py`（真实 LLM 链路）验证。

**为什么**：mock 层验证只能保证逻辑对，端到端要在真实 MySQL 事务 + 真实 LLM 流式下才暴露问题（角色判定 / 持久化 / 门禁 / sources 上报）。本机具备条件就实跑，而不是继续信任过时记录——这是前序报告「文档会过期、以代码为准」方法论在**环境层面**的延伸（详见第六节）。

---

## 三、遇到的 Bug、修复方式与理由

> 前序报告已记录 Bug 1–11（T0–T7）、Bug A–E（T8–T9）。本文 Bug 用 `M1`–`M7` 编号。
> **这些 bug 多数在「真实发请求 / 跑测试」时才暴露，静态看代码看不出来。**

### Bug M1（M1）：`GET /messages` 稳定 500 —— `.unique()` 挂错对象

| | |
|---|---|
| **现象** | M1 verify 首次跑，POST 发言正常但 GET 读消息 500 |
| **根因** | `list_messages` 写成 `query.unique()`，但 `.unique()` 是 **Result 的方法**（`db.execute` 的返回值），不是 `Select` 的；Select 没有这个方法 → `AttributeError` → 端点 500 |
| **修复** | `return list(db.execute(query).unique().scalars().all())`——把 `.unique()` 挂到 `db.execute(query)`（Result）上 |
| **为什么这样修** | 这是对象方法归属写错，不是设计问题，挂对位置即可。同时保留 `joinedload(Message.user)`（前序 Bug A 已建立），避免 N+1 与 `DetachedInstanceError`。**GET 受影响而 POST 不受影响**，正好解释了定位线索：POST 路径不碰 `.unique()` |

### Bug M2（M2）：组卷「要 8 题只出 4 题」+ 先修薄弱点被误放行

| | |
|---|---|
| **现象** | `build_pretest(limit=8)` 实际只返回 4 题；且已测过但不达标的先修知识点仍被放进卷子（明知他基础薄弱还往上考） |
| **根因** | ① `pick_topics` 按 `recommend_next` 推荐，被「先修未达标」挡掉后**不补题**，数量不足；② `pick_topics` 对已测过的薄弱先修**没有拦截**，允许直接考上层知识点 |
| **修复** | 在 `build_pretest` 里增加「补足」逻辑——按 `learning_order` 继续加候选，**但只加「先修要么已在卷子、要么已达标」的知识点**（`_ready_or_in_paper` 判定）；已测且低于 `PREREQ_READY_THRESHOLD`(0.4) 的先修一律不放行 |
| **为什么这样修** | ① **补足但不放宽先修约束**：只补「先修就绪」的点，避免「要 8 题凑不够就随便塞」——那会测到他根本啃不动的东西，诊断出的低分反映的是基础缺失而非该知识点掌握度；② **已测薄弱先修不放行**：与图谱 `recommend_next` 保持一致，既然已知他基础薄弱就不该顺杆往上考，否则诊断结论失真；③ 补了单测钉住（`count==8` 且不含薄弱点） |

### Bug M3（M2）：IRT 区分度与更新规则 —— 「答对送分题涨太多」违背「意外才更新」

| | |
|---|---|
| **现象** | 初版能力估计对「高分学生答对偏易题」过度敏感（实测 θ 涨 0.073），且 naive 加分式会让学霸把能力刷到 1.0 |
| **根因** | 区分曲线过宽（过渡带偏宽）+ 更新未体现「信息量」——答对本就该答对的题不该动 θ |
| **修复** | ① 更新改为**残差驱动** `θ += 学习率·(实际−期望概率)`（答对 P≈1 的题残差≈0，几乎不动）；② 把区分曲线调陡（`DISCRIMINATION=5.0`），使「能力远高于题难度」时 `P≈1`、残差≈0，实现「答对送分题只微涨（+0.03）、答错才显著降（−0.37）」 |
| **为什么这样修** | **直接加分会让学霸把所有题刷到 1.0、诊断失真**。残差驱动 + 陡曲线才能体现「信息量」——只有「意外结果」（本该对却错）才是强信号。这是 CDT 的核心前提：测出来的低分要反映「这个知识点本身」，而不是「他基础缺失被顺杆往上考」 |

### Bug M4（M2）：知识图谱遍历漏 `walk` —— `prerequisites` 返回空

| | |
|---|---|
| **现象** | `prerequisites` 返回空列表，导致「先修未达标」判断全部失效（不拦任何先修） |
| **根因** | 修改遍历函数时**漏改**了递归调用 `walk(name)`（只改了入口、没改递归里的下一跳），DFS 走不出第一步 |
| **修复** | 补回 `walk(name)` 递归调用，使先修闭包能真正展开到所有祖先 |
| **为什么这样修** | 这是「改了一半」的低级错误。教训：结构改动（递归/循环）后必须跑相关单测——`test_cdt` 里断言先修关系的测试一跑就抓出。**对「返回空」这种静默失败要保持警惕**：空结果不会报错，只会让后续逻辑全部放行 |

### Bug M5（M2）：`personalize.py` 中文语境用英文直引号提前闭合字符串

| | |
|---|---|
| **现象** | 个性化提示词拼接时，某条认知风格文案在运行期抛语法 / 字符串截断类错误 |
| **根因** | 中文文案里用了**英文直引号 `"`** 当作文案内的「引用符号」，但那段在 Python 里用双引号包裹，于是第二个 `"` 提前闭合了字符串，后面内容变成裸代码 |
| **修复** | 把文案里的引号改成**中文引号 `""`**，与字符串界定符（英文 `"`）区分开 |
| **为什么这样修** | 中文语境里展示「引用」就该用中文引号；用英文引号包在英文双引号字符串里必然提前闭合。这是全仓一贯原则「模型/文案的自由文本当键/值用要归一/转义」的变体——只是这次是「我们自己写的中文文案里的引号」撞上了 Python 字符串界定符。**统一约定：代码里中文文案的引号一律用中文引号** |

### Bug M6（M3）：Pydantic 拒绝越界 `difficulty` —— 模型返回 0–1 外的值直接 422

| | |
|---|---|
| **现象** | `parse_resource` 用 `QuizQuestion.model_validate` 校验，模型偶尔返回 `difficulty=1.2` 之类越界值 → `ValidationError` → 整次资源生成失败、白烧额度 |
| **根因** | `QuizQuestion.difficulty` 一开始写成 `Field(ge=0.0, le=1.0)`，但**模型输出不可控**，越界值在校验最前、钳制在最后——还没到钳制就被拒 |
| **修复** | 去掉 `QuizQuestion.difficulty` 的 ge/le 约束（只留默认 0.5），把难度钳到 [0,1] 的活交给 `parse_resource` 在 `model_validate` **之后**统一做（`max(0.0, min(1.0, float(...)))`） |
| **为什么这样修** | 模型是外部不可信输入，不该用「硬校验」把越界值当错误拒绝（那会让整次生成失败），而该「宽容解析 + 事后归一」。这和大家一贯原则一致：**凡是模型自由输出的字段，先收进来再归一/钳制，不让模型的小偏差炸掉整个请求** |

### Bug M7（M3 / 顺手修陈年隐患）：`api_ppt_outline` 调用 `get_knowledge_context` 却从未 import

| | |
|---|---|
| **现象** | `api_ppt_outline` 在请求时调用 `get_knowledge_context`，但该符号从未在 `app.py` 导入；一旦走到那条路径就 `NameError` |
| **根因** | 历史代码里 `get_knowledge_context` 的 import 缺失（某次重构/拆分时漏掉） |
| **修复** | 在 `app.py` 的 retriever 导入块补上 `get_knowledge_context`（M3 的 `/resources/generate` 也要用它） |
| **为什么这样修** | 这是「运行时才暴露」的隐患——静态 import 检查不会报（函数定义里只是个名字）。补在 retriever 导入块最自然（它本就来自 retriever 模块）。顺手修掉，既修复了原本就坏的 `api_ppt_outline`，也避免 M3 端点同样踩坑 |

### 附：环境类干扰（非代码 Bug）

连跑多个涉及 LLM 的验证脚本时撞 **`429` 限流**（`/chat` 10 次/分等）中断过开发节奏。属产品防刷设计，不该为开发便利性改代码；靠错峰 + 用户「继续」指令恢复（前序问题 19 已记录同类）。

---

## 四、数据库与关键数据结构设计（M1–M3 增量）

> 前序报告第 4 节已记录 T3/T5 的 6 张表原始设计。**本里程碑（M1/M2/M3）没有新增物理表**，而是做了一件更克制的事——**在既有 6 张表上做最小列扩展 + 把「图谱 / 题库 / 资源」设计成内存资产而非表**。这一节专门讲清楚「设计了什么、怎么设计、为什么这样设计」，并诚实说明「为什么没建新表」。

### 4.1 既有表的列扩展（M1/M2）

| 表 | 新增列 | 类型 | 设计理由 |
|---|---|---|---|
| `classrooms` | `is_private` | BOOLEAN NOT NULL DEFAULT 0 | M1 私聊 DM 标记。不新建 dm 表——DM 本质是「只有两人的课堂」，复用全部既有权限/消息查询；用一列布尔位零成本区分群聊/私聊 |
| `messages` | `agent_role` | VARCHAR(16) NULL | M1 多角色 Agent：这条 AI 回复由哪个角色产出（tutor/teacher/peer/examiner）；人类消息为 NULL。和 assistant 消息同生共死，无需独立实体 |
| `student_profiles` | `ability_vector` | JSON NULL | M2 CDT：存「凭什么给这个分」的原始施测证据 `{"死锁":{"theta":0.62,"attempts":2,"last_difficulty":0.7}}`。与 `knowledge_mastery`（分数）分开——留原始作答计数才能换估计模型重算 |
| `student_profiles` | `cognitive_style` | JSON NULL | M2 认知风格 `{"ready":true,"confidence":0.75,"dimensions":[...]}`。NULL 表示尚未达可判定样本量，不是「没风格」 |
| `student_profiles` | `pretested_at` | DATETIME NULL | M2 施测时间戳，区分「从未施测」与「施测过」 |

**为什么是列扩展而不是新表（关键设计决策）**：

1. **`ability_vector` 沿用 T5 的 JSON 列原则**。知识点是开放集合、且我们**没有「反查哪些学生某个知识点能力低」这类需求**（只整体读写、不做内部查询）。继续用 JSON 列比拆成 `ability(user_id, topic, theta, attempts)` 子表更简洁——避免每次读画像都 join、避免换估计模型时大量 UPDATE。一旦出现按能力反查的需求就该拆表（T5 已写明这个前提）。
2. **三列都允许 NULL，不强行 NOT NULL**。`init_db.py` 的 `ensure_new_columns()` 给**已存在数据的表**补 JSON/NOT NULL 列会直接报 MySQL **1138**（JSON 不能有 DEFAULT）。所以让它可空，由调用方统一 `or {}` / `or []`。这是 T3 以来 `ensure_new_columns` 幂等 ALTER 纪律的延续（前序报告 4.5 已记）。
3. **`is_private` / `agent_role` 用布尔位 / 枚举列而非状态子表**——M1 是雏形，这两个字段都「和载体同生共死」（私聊身份随课堂、角色随消息），没有独立生命周期，不值得单独立表。

### 4.2 知识图谱（设计成内存资产，不是表）

`_NODES` 邻接字典 + 两层边（`prereq` / `related`），8 个知识点：进程与线程、进程调度算法、死锁、虚拟内存与页面置换（操作系统）；TCP/IP 分层模型与 ARP/DNS、TCP 与 UDP、TCP 三次握手与四次挥手、HTTPS 与 TLS 握手（计算机网络）。

**为什么落内存、不落库**：
- 图是**静态学科结构**，8 节点、手工维护、零运行时写入；落库要建 `knowledge_nodes` / `knowledge_edges` 两张表 + 迁移 + 读取逻辑，收益为零。
- 落库会把「图谱」变成「运行时状态」，但它在 CDT 里是**常量资产**（与 RAG 检索源、画像键同源）。纯函数零 IO 才能直接单测、才能被 `test_cdt` 25 个测试零成本覆盖。

### 4.3 标定题库（设计成内存资产，不是表）

`_ITEM_BANK` 是硬编码 Python 列表，每题含 `topic / stem / options / answer / explanation / difficulty / item_type`。

**为什么落内存、不落库**：
- 题库是 CDT 的「金标准」（答案与难度已知），必须**确定、可复现、零额度**——落库徒增 DB 读写与迁移成本，且题目极少变动。
- 它是 M2 施测与 M3 quiz 零-LLM 出卷的**共同数据源**，内存共享零延迟。

### 4.4 资源类型与结构（设计成「流程共用 + per-type 模板」）

`RESOURCE_TYPES = {slides, quiz, mindmap, cards, guide}`，每类一个 Pydantic 模型（都带 `max_length` 防模型灌爆，如 `questions` 上限 30、`sections` 上限 6）+ 一个 prompt 模板 + 一个 few-shot 样例。

**设计要点**：
- **统一 `resolve_difficulty`**：三级回退（显式 > 整体水平 > 知识点掌握度）夹到 [0.2,0.85]，避免「整体 advanced 但单点没学过」被推到过难。
- **`parse_resource` 宽容解析**：吃代码围栏、结构非法即抛 `ResourceGenError`、quiz 难度事后钳制——不让模型小偏差炸掉整次生成（呼应 Bug M6）。
- **`build_quiz_from_bank` 零-LLM 出卷**：quiz 主题命中 M2 题库时按难度带直接出卷，可复现、零额度、难度可控。

---

## 五、验证体系

| 脚本 / 手段 | 用途 | 成本 | 结果 |
|---|---|---|---|
| `run_tests.py` | 纯逻辑单元测试（含 `test_cdt` 25、`test_resource_gen` 30） | 零 | ✅ **148/148** |
| `verify_live.py` | 真实服务 e2e（M1/M2/M3 零-LLM 路径） | 零 | ✅ **16/16** |
| `verify_chat_live.py` | 真实 LLM 链路（`/chat` + SSE sources） | 1 次 LLM | ✅ HTTP 200、162 SSE 事件、sources 上报 4 片资料 |

**为什么状态/诊断测试用纯函数 + 内存 SQLite 而非真库**：IRT 单调性、组卷题量、先修拦截都是**算法语义层面**的规则，换方言不影响结论；依赖真库的测试在别人机器上跑不起来。`verify_live.py` 则是反过来——专门在真库上验证持久化、门禁、角色判定这些**只有真实 MySQL 事务才能暴露**的行为。两者互补。

---

## 六、本机环境实跑复盘（推翻 10-21 旧判）

10-21 旧记录称「后端无法运行 / MySQL 在另一台机器 / 无 LLM」。本次核实全部不实：

| 条件 | 当时记录 | 本次核实 |
|---|---|---|
| `dotenv` | 未提 / 视为不可用 | ✅ 已装，`app.py` 可正常 `import app` |
| MySQL | 在另一台机器 | ✅ 本机 `mysqld` 在跑，`db_available()=True`（root@localhost:3306/ai_classroom） |
| DeepSeek | 无 | ✅ `.env` 已配 key，端点可达 |
| HF 镜像（embedding） | 无 | ✅ `hf-mirror.com` 200 |
| 代理变量 | 会导致 /chat 502 | ✅ 当前 shell 无代理，无需 unset |
| venv | 残缺（指向不存在的 E:\Python） | ✅ 实际是可用 uv 管理的 Python 3.12.14，依赖齐全 |

**已落地的实跑动作**：`init_db.py` 在 `ai_classroom` 库真实建了 6 张表（含 M1/M2 新增列）→ 起 uvicorn（DB 模式，无「无数据库」退化告警）→ `verify_live.py` 16/16 + `verify_chat_live.py` 真实 LLM 链路通。

**结论**：M1/M2/M3 的逻辑此前只在 mock 层验证，本次在**真实 MySQL + 真实 LLM** 下端到端跑通，风险已实质消除。`memory/MEMORY.md` 与今日日志已更正旧判。

---

## 七、遗留事项与下一步

| 优先级 | 事项 | 说明 |
|---|---|---|
| **P0** | 提交 M1/M2/M3 | ✅ 已完成（`f4244bc` + `6bf6cbe`） |
| **P0** | 本机实跑验收 | ✅ 已完成（真实 MySQL + LLM） |
| **P1** | M3+ 真多智能体协作 | 需 `agent_runs` / `agent_messages` 表记录多 Agent 交互；目前仅角色提示词层 |
| **P1** | M4 多场景 | 1v1 之外的小组 / 辩论场景 + 自适应切换 |
| **P2** | 成果 #4 评估报告 | 需新写计时 + 四项人工评分（准确/相关/完整/个性化），复用 `smoke_rag.py` 零-LLM 计时思路 |
| **P2** | 成果 #5 软著材料 | 源码已提交，需补软著文本 |
| **P2** | 前端联调 | M1/M2/M3 后端接口已就绪，等前端接入（契约见 `API_CONTRACT.md`） |
| 注 | `verify_live.py` / `verify_chat_live.py` | 依赖 8000 端口服务在跑，可复现实跑；如不需要可删，未提交 |

**一个方法论小结（本里程碑新增）**：
1. **环境记录会过期，动手前先核实**。10-21「无 MySQL/LLM」的判断耽误了一轮验证，本次一行命令（读 `.env` + `db_available()` + curl 外网）就推翻了它。
2. **列扩展优先于新表**。M2 的 CDT 数据落到 `student_profiles` 的 JSON 列而非新表，延续 T5 的「开放集合用 JSON」原则，避免 join 与迁移成本。
3. **模型输出一律宽容解析 + 事后归一**。Bug M6 再次印证：硬校验（ge/le）会把模型小偏差变成整次失败，钳制应放在解析之后。
4. **改动递归/循环后必须跑相关单测**。Bug M4 的「返回空」静默失败靠 `test_cdt` 抓出。
