# 教师上传资料的存储与检索：综合设计方案

> 2026-10-26 ｜ 状态：**已实施（XB-23）+ 教师端实测修正（XB-24），待 A 复核**
> 实施结果：`run_tests.py` 523/523（+23 例）；真实链路实测——教师传 1 份 98 页 PDF
> → 187 片；同课堂学生 sources 命中 3 条 `origin=classroom`、同一学生换课堂 **0 条**
> （隔离）；重启后仍命中（持久化）；删除后立刻失效且空库被清；
> `chroma_kb` 仍只 `cs_basics` 248 片 / `bbe0cdc4`，L1 top1 **68/71** 无退化。
> 落地细节见 `RAG_HANDBOOK.md` §2「课堂资料长期化」、`B_WORKLOG.md` XB-23。
>
> **2026-10-26 下午补（XB-24，实测驱动）**：本文 §4 的设计有两处过于乐观，实测后改了：
> ①**界面**——按本文实施完，教师端 `/teacher` 其实一个上传入口都没有，功能对真实教师
> 不可达；已补「课堂资料」卡片（上传/清单/删除），并用 Chrome CDP 真点了一遍。
> ②**计量**——`chunk_count` 取的是整个课堂 collection 的总数（空白 PDF 报 188 片），
> 已改为按 `doc_id` 计数；同时把「抽不出正文的扫描件」从静默 200 改为 422 拒绝。
> ③**检索**——§4.3 说的「纯向量够用」漏了一层：没有相关性下限时离题问题也会硬塞资料，
> 已补 `CLASSROOM_MAX_DISTANCE`（默认 1.25）；交叉重排能分开但本机 3 片耗时 4.0s，
> 故不采用（数据见 `B_WORKLOG.md` XB-24 §四）。当前 `run_tests.py` **529/529**。
> 本文回答两个问题：**教师上传的内容应该放在哪里**（§一~§三）、**怎么设计才能落地**（§四~§八）。
> 所有代码事实均已现场核对（文件:行号见文中），所有性能数字来自
> `PROPOSAL_SCOPE_PREFILTER.md` §九 与 `.workbuddy/memory/2026-10-26.md` 的实测。

---

## 〇、结论先行（30 秒版）

1. **「教师上传」不是一个需求，是三个**——备课素材（用后即弃）、课堂资料（长期生效、
   本课堂学生可检索）、全局内容（所有课堂共用）。**放哪里取决于语义，不取决于角色**。
2. 三条路各就各位：
   - 备课素材 → **会话级内存库**（XB-21 方案 A，**已落地**）；
   - 课堂资料 → **课堂级 collection `classroom_{id}_docs` + 原件落盘 + 并行召回**
     （方案 B，本文核心设计，估 2~3 天 MVP）；
   - 全局内容 → **审核晋升**：`knowledge/inbox/` 暂存 → 人工审核 → 移入 `knowledge/cs/`
     → `update_kb.py` 增量 → 热重载（链路现成，成本 0.35s/篇）。
3. **绝不直接写 `cs_basics`**——四个硬伤：全局泄漏 / L1 金标准与指纹
   （`bbe0cdc4` / 248 片）立即失效 / 无归属无审核 / 加容易删难。
4. 检索链路 MVP **不动 `retrieve_knowledge_docs()`**：课堂库复用
   `retrieve_pdf_docs()` 的纯向量范式做第三路注入（`/chat` 已是
   `pdf_docs + kb_docs` 两路拼接，加一路是现成模式），避开重排成本×2、
   BM25 逐课堂冷启动、floor 口径不适配三个坑。
5. 当前规模（248 片 / 课堂数个位数）**不触发 scope 池化**；课堂数 >20~30 时
   按 `PROPOSAL_SCOPE_PREFILTER.md` §9.3 切「两库分层」。

---

## 一、需求分类：先问「这份资料给谁看、看多久」

`/upload/teacher`（app.py:2214）现在只有一种行为——建会话级内存库（TTL 1 小时、
进程重启即消失）。但教师上传的真实语义有三种，混在一个接口里迟早出事：

| # | 语义 | 典型例子 | 生命周期 | 可见范围 | 对应落法 |
|---|---|---|---|---|---|
| S1 | **备课素材**（用后即弃） | 明天讲课要引用的论文 | 1 小时会话 | 仅上传者本次 | 现状（方案 A），已够用 |
| S2 | **课堂资料**（长期生效） | 本班讲义、错题集、实验指导 | 跟随 `Classroom` 行 | 本课堂成员 | **方案 B（本文）** |
| S3 | **全局内容**（所有课堂共用） | 优质总结、新知识点补充 | 常驻 | 全平台 | 审核晋升 → `knowledge/cs/` |

**判断依据是「生命周期 + 可见范围」，不是「上传者是不是教师」**（决策台账 D1）。
按角色分库会把同一份课堂资料劈成两半——比如教师传的讲义（S2）和助教整理的
错题集（S2）语义完全相同。

---

## 二、放哪里：四个选项的评估

### 选项 0：会话级内存库（现状，方案 A）——S1 的正解，S2 不够

- 已落地：`_index_upload()`（app.py:308）不传 `persist_directory`，纯内存 Ephemeral，
  不落盘、不进 `chroma_kb`，实测不污染基线（上传前后 `chroma_kb` 始终只有
  `cs_basics`，248 片 / `bbe0cdc4` 不变）。
- **对 S1 完全够用**；对 S2 致命缺陷：1 小时 TTL + 进程重启即消失 + 学生检索不到。
- **结论：保留现状服务 S1，不动。**

### 选项 1：写进共享库 `cs_basics` ——否决

四个硬伤（`RAG_HANDBOOK.md` §9 已登记，此处只列结论）：

1. **全局泄漏**：某教师传份错题集，所有课堂学生都能检索到；
2. **评测基线当场报废**：L1 金标准绑 `gold_index_fingerprint`（`bbe0cdc4` / 248 片），
   库一写就失效且无法复现，重刷金标准约 4 分钟一轮；
3. **无归属、无审核**：质量不可控；
4. **加容易删难**：`delete(where={"source": ...})` 删得掉，但源文件还在的话下次
   重建会「复活」。
- 共享库的正确定位：**数据源是仓库文件 `knowledge/cs/*.md`，Chroma 只是派生索引**。
  「上传到共享库」= 文件落 md → 审核 → `update_kb.py` 增量 → 热重载。这条链是通的，
  缺的只是审核闸门（待 A 定晋升审批人角色，U3）。

### 选项 2：课堂级 collection（方案 B）——S2 的正解，本文推荐

`classroom_{id}_docs`，与共享库**并行召回**后合并注入。优点：

- **权限边界是物理的**：学生查别的课堂的 collection 在机制上不可能（比
  scope 过滤「靠应用层每次都正确传参」硬得多——scope 方案的最大风险就是
  漏传 filter 造成静默跨课堂泄漏）；
- **不碰共享库**：L1 金标准与指纹零影响（金标准只测 `cs_basics`）；
- **撤销干净**：删课堂 → 删 collection + 原件目录，一个动作；
- 代码量小（对照 §六改动清单）。

代价：collection 数随课堂数线性增长。**当前课堂数个位数，此代价为零**
（规模切换点见 §七）。

### 选项 3：单库 + scope 元数据 + pre-filter——规模化后的解，现在不做

完整设计与实测见 `PROPOSAL_SCOPE_PREFILTER.md`（§九已按 2 万片规模实测修正为
「两库分层」）。两条硬实测否决了「现在就做」：

- Chroma 带 `where` 不走 HNSW 而是扫过滤子集（50% 选择性 147ms vs 不过滤 3ms）；
- BM25 冷启动瓶颈在 jieba 分词（20k 片 20.1s），教师上传会击穿 p95 ≤5s 门禁。

**结论：档①（只加 `scope` 元数据 + 回填，0.5 天、指纹不变、零回归）可作为
前置投资与方案 B 并行做；档③（课堂池）等触发条件。**

---

## 三、三层资料库总览（方案 B 落地后的完整图景）

| 层 | collection | 事实源（真相在文件/DB，Chroma 是派生） | 可见范围 | 生命周期 | 影响 L1 基线 |
|---|---|---|---|---|---|
| 共享库 | `cs_basics` | `knowledge/cs/*.md`（20 篇） | 全部 | 常驻 | **是**（变更需重刷金标准） |
| **课堂库（新）** | `classroom_{id}_docs` | `backend/classroom_docs/{id}/` 原件 + `classroom_documents` 表 | 本课堂成员 | 跟随 `Classroom` 行 | 否 |
| 会话库 | `student_upload_/teacher_upload_{sid}` | 无（用后即弃，不留存） | 仅本人本次 | 1h TTL / LRU | 否 |

关键口径（沿用手册 §9 的已更正结论）：

- **结课 ≠ 删除**。`Classroom` 模型没有结课字段（`db/models.py:58`），课堂库
  生命周期 = `Classroom` 行的生命周期，只有课堂被真正删除才清理——学生结课后
  回来复习本课讲义是正当需求。将来加「结课」应是 `status=archived`（禁写只读）。
- **写入分层、读取合并**（D2）：写入默认落课堂库，审核通过才晋升共享库；
  读取永远「共享库 + 课堂库」并行召回——只查课堂库会让教师资料顶掉教材，
  学生反而变盲。

---

## 四、方案 B 详细设计

### 4.1 存储：原件落盘 + DB 登记 + 派生索引（三层各司其职）

**这是本设计新增的关键机制**——此前散落各处的讨论只讲了 Chroma collection，
没有回答「课堂库的事实源是什么」。共享库的事实源是 md 文件；课堂库若只有
Chroma 索引而没有原件，**换 embedding 模型、改切片参数、Chroma 升级迁移时
全部不可重建**，等于把派生物当成了真相。

```
backend/
├── classroom_docs/{classroom_id}/          # 原件（事实源）
│   └── {doc_id}_{原文件名}.pdf
├── chroma_classrooms/                      # 派生索引（落盘，与 chroma_kb 同级、物理分开）
│   └── classroom_{id}_docs/                # 一课一 collection
└── classroom_documents                     # MySQL 登记表（ai_classroom 库）
```

`classroom_documents` 表（沿用 `ensure_new_columns` 幂等迁移惯例）：

| 列 | 说明 |
|---|---|
| `id` (PK) | doc_id，兼作文件名前缀 |
| `classroom_id` (FK, CASCADE) | 归属课堂 |
| `teacher_id` | 上传者（审计） |
| `filename` / `stored_path` | 原始名 / 落盘路径 |
| `doc_hash` | 内容 sha256 前 16 位——**同课堂去重依据** |
| `chunk_count` / `file_bytes` | 容量统计 |
| `status` | `active` / `deleted`（软删，物理清理走后台） |
| `created_at` / `deleted_at` | 审计 |

设计要点：

1. **原件与索引分离**：PDF 原件可达 10MB，不进 DB；Chroma 目录
   `chroma_classrooms/` 与 `chroma_kb/` 分开，**运维上一眼可分**，备份/迁移/清理
   互不牵连，也杜绝任何脚本把课堂 collection 误算进共享库指纹。
2. **`doc_hash` 去重**：同课堂重复上传同内容 → 409 返回已有 doc_id（幂等）。
3. **切片策略**：PDF 走现有 `_index_upload` 的 900/150 扁平切片（父子模式是
   md 专属，靠 `##` 小节聚合，PDF 没有 markdown 结构，不要硬套）。
4. **source 前缀化**：课堂片的 `source` 记 `classroom:{id}/{doc_id}_{filename}#p{n}`
   ——即使将来并入 scope 课堂池，`parent_id` / 去重键也不会与共享库或其他课堂撞
   （`PROPOSAL_SCOPE_PREFILTER.md` §2.3 的教训）。共享库 `source` **永远不加前缀**。

### 4.2 上传接口：`/upload/teacher` 扩一个参数，不开新端点

```python
@app.post("/upload/teacher")
async def upload_teacher_document(
    ...,
    teacher_id: int = Form(...),
    classroom_id: int = Form(...),
    persist: str = Form("session"),   # 新增："session"(默认,现状) | "classroom"(方案B)
):
```

`persist="classroom"` 时的行为（在现有 `_require_teacher` 校验之后）：

1. 校验容量配额（§4.5），超限 413；
2. `doc_hash` 查重，重复 409 幂等返回；
3. 原件落盘 `classroom_docs/{id}/`（沿用现有边读边判大小的临时文件机制，
   校验通过后从临时文件拷入，失败不留垃圾）；
4. `Chroma.from_documents(..., persist_directory="chroma_classrooms")` 建索引
   ——**与 `_index_upload` 的差别只有 persist_directory 一个参数**，抽出共用；
5. DB 登记 `classroom_documents` 行；
6. 失效课堂库句柄缓存（§4.4）。

**默认值 `session` 保证存量调用方行为零变化**——这是零回归的兼容开关。

### 4.3 检索：第三路注入，MVP 不动 `retrieve_knowledge_docs()`

`/chat` 现状已是两路并行注入（app.py:694~727）：

```python
pdf_docs  = retrieve_pdf_docs(...)    # 学生会话库，纯向量
kb_docs   = retrieve_knowledge_docs(...)  # 共享库，完整链路(向量+BM25+RRF+重排+floor)
rag_context = format_pdf_docs(pdf_docs) + format_knowledge_docs(kb_docs)
source_items = summarize_sources(pdf_docs, "upload") + summarize_sources(kb_docs, "kb")
```

MVP 加第三路：

```python
classroom_docs = retrieve_classroom_docs(classroom_id, query, k=3)  # 新函数，纯向量范式
```

**为什么 MVP 走纯向量而不是完整链路**（这是本设计最重要的取舍）：

| 做法 | 代价 | 收益 |
|---|---|---|
| 完整链路（向量+BM25+RRF+重排+floor） | `retrieve_knowledge_docs` 是单库签名（retriever.py:622），要么调两次各自重排（**重排成本×2**，p95 预算 4.3s/5s 直接击穿），要么改成多库联合检索（大改）；`_BM25_CACHE` 按 collection 名做键，**每课堂首次检索冷启动**；课堂资料分数分布与教材不同，**共用 floor 可能整批滤掉** | 召回质量略高 |
| **纯向量（`retrieve_pdf_docs` 范式，MVP）** | 复用现成函数模式，检索耗时 ms 级 | 课堂资料量小（单课堂通常 <几百片）时纯向量完全够用 |

`retrieve_classroom_docs` 照抄 `retrieve_pdf_docs`（retriever.py:739，含 front_matter
特判与去重），加 `classroom_id` 定位 collection。注入上限 `k=3`，防上下文膨胀。
`summarize_sources(docs, "classroom")`——origin 标签机制现成（retriever.py:544），
SSE 协议零改动（只是 sources 数组多一类 origin 值，前端向后兼容）。

**升级触发条件**（写下来，免得将来拍脑袋）：单课堂片数 > 2000，或实测发现
课堂资料命中率不足，再评估上 BM25 + 重排——届时优先「课后台预建 BM25」而不是
请求时冷启动。

### 4.4 课堂库句柄管理：吃透「P5 教训」

实测教训（memory 2026-10-26）：Chroma `PersistentClient` 打开过的进程持有内存快照，
**写入后已持有句柄的进程看不到新数据，且不报错**。课堂库写入（教师上传）与
读取（学生 /chat）在同一个服务进程里，必须处理：

- **MVP 方案**：不缓存课堂库句柄，每次 `/chat` 现场
  `PersistentClient(path="chroma_classrooms").get_collection(f"classroom_{id}_docs")`。
  Chroma client 创建是轻操作（实测共享库场景无感知），单课堂 collection 小，
  换来「写入即时可见、零缓存一致性问题」。
- 课堂不存在（新课堂无资料）→ `get_collection` 抛 `NotFoundError` → **静默返回
  空列表**（知识库是增强项，失败必须降级，不许 500——XB-19 同款教训）。
- **验证生效用真实检索，不用 `collection.count()`**（count 读 SQLite 元数据
  永远返回最新值，是已知坑，硬约束 6）。

### 4.5 生命周期与容量治理

| 事件 | 动作 |
|---|---|
| 课堂被删除 | 删 collection（`delete_collection` 或整目录）+ 删 `classroom_docs/{id}/` + DB 行 CASCADE（沿用 `ClassroomMember`/`Message` 的 `ondelete="CASCADE"` 惯例，`db/models.py:102`） |
| 教师删自己的资料 | 软删 DB 行 + `delete(where={"source": {"$in": [...]}})` 按篇删片（同库批量删用 `$in`，避免 N 次全表扫描）+ 原件留待后台清理 |
| 容量配额（MVP 建议值） | 每课堂 ≤ 20 个文件 / ≤ 200MB；超限 413。**限流已有 10/hour 不变** |
| 结课（将来若做） | `status=archived` → 禁上传、检索保留。**绝不删数据** |

### 4.6 权限

- **上传**：`_require_teacher()`（app.py:1969，已核实校验 role 而非只查 membership）——现成，复用。
- **检索**：零新增逻辑。`/chat` 的 `ChatRequest` 本来就带 `classroom_id`
  （app.py:616 `_classroom_of()`），且已有 `is_member` 校验（app.py:633）——
  课堂成员问答自动落在本课堂库，非成员连 /chat 都进不来。
  **请求协议零改动**（这是方案 B 相比其他方案最省的一点）。

### 4.7 评测口径影响（必须写清楚，否则上线后指标会「莫名」漂移）

| 层 | 影响 | 对策 |
|---|---|---|
| L1 检索 | **零**（金标准只测 `cs_basics`，物理隔离） | 上线后复跑一次 `smoke_rag.py --compare` 确认指纹仍 `bbe0cdc4` |
| L3/L4 生成与 e2e | **有**：评测对话若带 classroom_id 且该课堂有资料，会注入课堂内容，sources 口径变化 | 评测脚本固定用「无课堂资料的课堂」（或 classroom_id=None 路径），**把课堂库从评测口径里显式隔离**；另设独立的「课堂资料命中」验收场景 |
| 新增验收场景 | 教师传讲义 → 学生提问 → 断言 sources 含 `origin=classroom` 且正文来自该篇 | 写成单测（mock LLM）+ 一条真实冒烟 |

---

## 五、晋升通道：课堂库 → 共享库（S3 的路）

```
教师上传(S2, 课堂库) ──教师点「申请全局收录」──▶ knowledge/inbox/{slug}.md 暂存
                                                    │
                                          人工审核（审批人角色待 A 定, U3）
                                                    ▼
                                    移入 knowledge/cs/{category}/ → update_kb.py 增量
                                                    ▼
                                          POST /admin/reload-kb（热重载,现成）
```

- 转入库时必须**转成 md 并人工整理**（PDF → md 的自动转换质量不可控，不自动化）；
- 审核通过后走的是现成增量链路（0.35s/篇 + 热重载），**这一段不需要写新代码**；
- MVP 可以先只做「下载原件 + 手工入库」，UI/流程后补——**不要为一个还没定审批人
  的流程先写代码**。

---

## 六、改动清单与工作量（MVP）

| # | 文件 | 改动 | 估行数 | 风险 |
|---|---|---|---|---|
| 1 | `db/models.py` + `init_db.py` | `classroom_documents` 表 + 幂等迁移 | ~40 | 低 |
| 2 | `app.py` | `/upload/teacher` 加 `persist` 参数、原件落盘、DB 登记 | ~60 | 中（越界，登记 XB） |
| 3 | `agent/rag/retriever.py` | `retrieve_classroom_docs()`（照抄 pdf 范式 + NotFound 降级） | ~40 | 低 |
| 4 | `app.py` `/chat` | 第三路注入 + `summarize_sources(..., "classroom")` | ~20 | 中（越界，登记 XB） |
| 5 | `app.py` | 删除课堂级联清理（课堂删除钩子）+ 教师删资料端点 | ~50 | 中 |
| 6 | `tests/` | 权限/去重/配额/NotFound 降级/三路注入/评测隔离，约 20~25 例 | ~250 | — |
| 7 | `RAG_HANDBOOK.md` §2/§9 | 三层库表更新 | 文档 | — |

**估 2~3 个工作日**（含 500 例回归 + L1 复跑确认指纹不变 + 一条真实冒烟）。
其中 #2/#4/#5 动 `app.py`，**开工前必须在 `B_WORKLOG.md` 登记 XB-n**（硬约束 10）。

---

## 七、规模演进路线（不要越级跳档）

| 阶段 | 触发条件 | 架构 |
|---|---|---|
| 现在 | 248 片 / 课堂数个位数 | 一课一 collection（方案 B） |
| 档①（可与 B 并行） | 想前置迁移成本 | 只加 `scope` 元数据 + 回填（0.5 天、指纹不变、零回归） |
| 档③ | 课堂数 >20~30 / 需跨课堂检索 / 需全局统计 | 两库分层：`kb_shared`(不过滤,HNSW) + `kb_classroom`(scope 过滤,课堂池) + BM25 池化 + 异步预热，**6~7 天** |
| 远期 | 课堂池 >5 万片 | 按学期分池；>20 万片换 Qdrant/Milvus + BM25 外置 ES |

**汇报纪律**（沿用手册结论）：scope 解决「collection 数量」增长，**不解决「数据量」
增长**；几十万片的解法是换引擎，别把档③说成「规模问题已解决」。

---

## 八、风险清单与验证方案

| # | 风险 | 缓解 |
|---|---|---|
| 1 | **课堂库检索失败拖垮 /chat** | `NotFoundError`/任何异常 → 静默空列表（增强项降级纪律，XB-19 同款）；单测覆盖 |
| 2 | **写入后读不到（句柄旧快照）** | MVP 不缓存句柄，每请求 `get_collection`；验收用「上传后立刻 /chat 能命中」实测，不信 `count()` |
| 3 | **上下文膨胀（三路拼接）** | 课堂路注入上限 k=3；监控 avg sources（L4 已有该口径） |
| 4 | **课堂资料顶掉教材** | 并行召回不是替换（D2）；教材路 final_k 不减 |
| 5 | **磁盘无界增长** | 配额 20 文件/200MB 每课堂 + 软删后台清理原件 |
| 6 | **评测口径漂移** | §4.7 对策；上线后 L3/L4 复跑一轮并核对 |
| 7 | **越界改动** | app.py 三处全部先登记 XB，等 A 复核（连同 XB-15/18/19/20 一起过） |

**验收清单（做对了的判据）**：

1. `run_tests.py` 全绿（500 + 新增 20~25 例）；
2. `smoke_rag.py --compare` 指纹仍为 `bbe0cdc4`（共享库零污染）；
3. 真实链路冒烟：教师 `persist=classroom` 上传 → **不重启服务** → 学生同课堂 /chat
   提问 → sources 出现 `origin=classroom` 且正文来自该篇；**换个课堂的学生提问
   检索不到**（隔离的反向验证）；
4. 会话级上传（默认 persist=session）行为与现状逐字节一致（兼容性回归）。

---

## 九、待 A 拍板的三个点

1. **是否按本方案启动方案 B MVP**（2~3 天，含 app.py 越界改动 XB 登记）；
2. **晋升审批人角色**（U3）：共享库审核闸门由谁把——在此之前共享库保持
   「人工改 md + update_kb.py」现状；
3. **配额数值**（20 文件 / 200MB 是 MVP 建议值，可调）。
