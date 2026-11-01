# AI-Classroom 交接说明合集

> 本文件由根目录 4 份交接类文档合并而成（2026-10-30）。合并只为止住根目录散落，正文一字未改。其中《打开前请阅读我》原是等宽排版，为保住 ASCII 分隔线的对齐，整篇放进 text 代码块。

## 原名索引（按旧文件名查）

- `HANDOFF_M4.md`（M4 交接说明（A → B / C）） —— 第 1 节，[跳转](#handoff-m4)
- `HANDOFF_RAG_LIBRARY.md`（RAG 资料库分层与规模演进 —— 交接文档） —— 第 2 节，[跳转](#handoff-rag-library)
- `项目交接说明.md`（AI Classroom 项目交接说明） —— 第 3 节，[跳转](#project-handoff)
- `打开前请阅读我.txt` —— 第 4 节，[跳转](#read-me-first)
- `双机联调`（两台电脑各有一套环境的联调步骤，2026-11-01） —— 第 5 节，[跳转](#lan-pair-testing)

---

<a id="handoff-m4"></a>

## 1、原名 HANDOFF_M4.md

### M4 交接说明（A → B / C）

> **交接人**：A（后端与 AI 算法）
> **日期**：2026-10-22
> **状态**：后端 M4 全部完成，**代码未提交**（由用户提交）。提交后 B / C 拉最新再动手。
> **配套**：接口细节看 `docs/specs/API_CONTRACT.md`，规则编号看 `docs/specs/AGENT_PROTOCOL.md`
> （本次同步更新：§0 / LLM-5 / DATA-6 / DATA-7 / §3.4 / §7 / §7.5 / Prompt-7）

---

#### 一、这次交付了什么

| 成果 | 状态 | 落点 |
|---|---|---|
| 成果 #2 多智能体原型 | ✅ **达成** | `agent/orchestrator.py` 重写 + `agent/scenarios.py` 新增 |
| 成果 #3 多场景 Demo | 🟡 **后端达成，前端待接** | `classrooms.scenario` + `/scenarios` + `/chat` 多角色流式 |
| 成果 #4 评估报告 | 🟡 **采集层达成，人工评分待填** | `agent/evaluation.py` + `/evaluation/*` + `eval_report.py` |
| 成果 #5 软著材料 | ⬜ **未做** | 源码已提交，**软著文本待写**（不属于后端任务，见 §五） |

**新增 / 改动文件**

| 文件 | 类型 | 说明 |
|---|---|---|
| `backend/agent/scenarios.py` | 新增 | 场景策略表，**角色序列的唯一事实源** |
| `backend/agent/orchestrator.py` | 重写 | 从"取角色提示词"升级为"产出编排计划" |
| `backend/agent/evaluation.py` | 新增 | 评估聚合层（纯函数，零 IO 零 LLM） |
| `backend/eval_report.py` | 新增 | 一键出评估报告 + 人工评分 CSV |
| `backend/tests/test_orchestration.py` | 新增 | 21 个测试，覆盖重点是降级路径 |
| `backend/tests/test_evaluation.py` | 新增 | 20 个测试，钉住指标口径与评分表对齐 |
| `backend/db/models.py` | 改 | `classrooms.scenario` + 新表 `agent_runs`（12 列） |
| `backend/db/repository.py` | 改 | `get_classroom_scenario` / `save_agent_run` / `list_agent_runs` / `get_messages_by_ids` |
| `backend/init_db.py` | 改 | 建 `agent_runs`、补 `scenario` / `rag_hits` 两列 |
| `backend/app.py` | 改 | 多 Agent 流式、SSE `agent` 事件、4 个新端点 |

**验证结果**：`run_tests.py` **189/189**（M4-A +21，M4-C +20）；`init_db.py` 对真实 MySQL
建出 `agent_runs` 12 列；真实 `group` 场景 16/16 通过（2 个 agent 事件、落库 2 条不同角色的
assistant 消息、peer 未复读 tutor）。

---

#### 二、给 C（前端）

##### 2.1 必须接的三件事

**（1）SSE 新增 `agent` 事件 —— 多 Agent 分气泡**

```
data: {"type":"sources","items":[...]}
data: {"type":"agent","role":"tutor","label":"主讲助教","stance":"立论","index":0,"total":2}
data: {"type":"token","content":"死锁"}
data: {"type":"agent","role":"peer","label":"学伴","stance":"质疑","index":1,"total":2}
data: {"type":"token","content":"补个口诀…"}
data: {"type":"state", ...}
data: [DONE]
```

收到 `agent` 就**新开一个气泡**，之后 `token` 归到当前气泡；`index`/`total` 可用于显示
"1/2"、"2/2" 进度。`stance` 是该角色在本场景里的身份（立论 / 质疑 / 追问），可作副标题。

> **向后兼容**：未知事件类型请**静默忽略**。忽略 `agent` 的表现是所有角色 token 连成一段
> ——文字不丢、不崩，只是不分气泡。所以**后端可以先上，前端后接**，不会打断现有联调。

**（2）建房时选场景**

```
GET  /scenarios     → { items: [{key,label,desc,steps:[...]}] }   // 下拉选项，别硬编码
POST /classrooms    { name, subject, scenario: "group" }          // 建房即定场景
GET  /classrooms/{id}、GET /classrooms  → 都返回 scenario
```

建了 `group` 房间后，`/chat` **不用传 `scenario`**，服务端按课堂走。传了也行（覆盖，方便调试）。

**（3）聊天记录按 `agent_role` 分气泡**

一轮 3 个 Agent 就是 3 条 `messages` 记录，`agent_role` 各不同。**不要按 `role=assistant`
合并**。增量轮询 `?after=` 照常，只补新增。

##### 2.2 验收清单

- [ ] `v1` 房间表现与现在**完全一致**（单气泡、单次回答）——这是回归底线
- [ ] `group` 房间一次提问出现 2 个气泡，头像/颜色按 `agent_role` 区分
- [ ] 刷新页面后气泡仍在且分得开（走 `GET /classrooms/{id}/messages`）
- [ ] 建房下拉选项来自 `GET /scenarios`，后端加场景前端不用改
- [ ] 非法 `scenario` 不报 422（后端回落 `v1`），界面不崩

##### 2.3 一个待拍板的事

**`debate` 场景要不要开放**。实测 `group`（2 Agent）一轮约 **9.3 秒**，`debate`（3 Agent）预估
约 **14 秒**。演示时这个等待是否可接受，请你和用户确认。
**在此之前先只接 `group`**——后端已经全支持，前端多做一个下拉项而已。

---

#### 三、给 B（Prompt 工程与 RAG）

##### 3.1 提示词归谁管（这条最容易改错地方）

| 想调的东西 | 改这里 | 不要改这里 |
|---|---|---|
| tutor / peer / examiner **怎么讲** | `agent/roles.py` 的 `build_role_system` | `scenarios.py` |
| 多 Agent 之间**怎么衔接**（别复读、要质疑） | `agent/scenarios.py` 的 `handoff` | `roles.py` |
| 该角色在本场景里的身份 | `scenarios.py` 的 `stance` | — |

理由（`docs/specs/AGENT_PROTOCOL.md` Prompt-7）：把"不要重复前面发言"写进角色提示词，
`v1` 单 Agent 场景也会被这句话污染——那里根本没有前面发言。

##### 3.2 你可以直接用的两个新工具

**`rag_hits` 已经自动采集了。** 一轮 `/chat` 只检索一次、所有 Agent 共用同一份上下文，
所以"命中几条"记在 `agent_runs.rag_hits`。改完检索策略后：

```
python eval_report.py --scenario v1     # 看检索覆盖率有没有退化
```

改检索的回归仍然跑 `python smoke_rag.py --compare rag_baseline.json`（准确率），
两者互补：**准确率看检索对不对，覆盖率看有没有回答是在"裸答"**。

**调提示词别烧额度**：`GET /students/{id}/prompt-preview` 仍然可用（不打 LLM）。
注意它返回的是单角色提示词，**不含 `handoff` 和前文本**——那两段在流式时才拼。

##### 3.3 评估报告的活在你这边（成果 #4 的最后一公里）

采集层我做完了，**四维人工评分要人填**：

```
python eval_report.py --classroom-id 3 --limit 50
→ eval_report.md    客观指标（轮数/每轮 Agent 数/降级率/延迟 p50·p95/检索覆盖率）
→ eval_scores.csv   人工评分表，准确/相关/完整/个性化 四列留空
```

口径（`docs/specs/AGENT_PROTOCOL.md` §7.5）：

- **评分粒度是"发言"不是"轮次"**：一轮 3 个 Agent = 3 行。按轮打分会把
  "tutor 讲得好、peer 在复读"折中成一个看不出问题的分——而"哪个角色在贡献价值"
  正是多智能体评估最该回答的问题。
- 建议每个场景各攒 **20 轮以上**再下结论，样本 <20 时 p95 会退化成最大值。
- **`rag_hits` 是本次才补的列**，更早的留痕记为 0。出正式报告请只用新跑的轮次。
- **跑出来是 0 先别急着重跑**，先看 `GET /evaluation/status`（或报告里的自检表）：
  留痕为空有四种成因（没配库 / 连不上 / 表缺失 / 被过滤掉），表现完全一样。
  其中"库里有数据但按课堂查不到"最常见——**不带 `classroom_id` 的 `/chat` 留下的痕
  `classroom_id` 是 NULL**，按课堂查必然漏掉。

##### 3.4 别动的三条红线

1. `orchestrator.py` 的**降级契约**（异常输入一律收敛成单 Agent + `degraded` + `reason`）。
   它保证了编排层出任何问题都不会让 `/chat` 失败。
2. `scenarios.MAX_AGENTS_PER_TURN`（单轮 Agent 上限 3）。这是成本红线不是技术上限。
3. 主管**不调 LLM**。要改成 LLM 主管，先来改 `docs/specs/AGENT_PROTOCOL.md` §3.4——一旦主管能
   自主派角色，LLM-5 的成本约定就失效了。

---

#### 四、环境：拉完代码必做一步

```
cd Tutor-Chatbot-main/backend
python init_db.py          # 建 agent_runs 表 + 补 classrooms.scenario / agent_runs.rag_hits
python run_tests.py        # 应见 RAN=210 FAILURES=0
```

**不加这一步的表现**：`agent_runs` 表缺失 → 编排留痕静默失败（回答正常，但
`/classrooms/{id}/runs` 和 `/evaluation/*` 都是空的）；`classrooms.scenario` 缺失 →
写入报 Unknown column。

服务启动时会自动建表 + 补列，所以**漏跑一般也能自愈**；但 MySQL 比后端晚启动时
那次自动建表会扑空——后端会在**第一次写留痕失败时补建一次并重试**，所以真正需要
手动跑的场景只剩"权限不足"。判断不了就直接问接口：

```
GET /evaluation/status     # 数据库不可用时也返回 200，不会 503
→ { ready, database:"up|down|disabled", table:{exists,missing_columns},
    total_runs, runs_without_classroom, hint }
```

启动服务前记得 **unset 代理变量**（`HTTP_PROXY` 等），否则 `/chat` 会 502。

应急开关：演示环境额度吃紧或延迟压不下来时，设 **`AGENT_COLLAB=0`**，
一切退回单 Agent 行为，**前端与数据库都不用改**。

---

#### 五、剩下的活

| 优先级 | 事项 | 谁 |
|---|---|---|
| P1 | 前端接 `agent` 事件 + 建房选场景（先只做 `group`） | **C** |
| P1 | `debate` 场景是否开放（延迟约 14s） | 用户拍板 → C 接 |
| P1 | 成果 #4：攒样本 + 填四维人工评分 + 写结论 | **B** |
| P2 | 成果 #5：**软著文本未写**（源码已提交） | 待定（非后端任务） |
| P2 | `LLM-8` 超时与重试仍未实现 | A |
| P2 | `LLM-6a` 评估节点解析失败仍无日志 | A |

**软著材料说明**：软著申请要的是「源代码文档 + 设计说明书/用户手册」这类文本材料，
现有报告（`docs/handoff/PROJECT_REPORT.md` / `docs/handoff/STAGE_REPORT_T8_T9.md` / `docs/handoff/STAGE_REPORT_M1_M3.md` /
`docs/reports/REPORTS.md`）是很好的素材来源，但需要按软著格式重写。这不是代码任务，
需要有人专门排时间。

---

<a id="handoff-rag-library"></a>

## 2、原名 HANDOFF_RAG_LIBRARY.md

### RAG 资料库分层与规模演进 —— 交接文档

> **给谁看**：接手「AI-Classroom 资料库 / 检索」这条线的下一位（人或 AI 会话均可）。
> **怎么用**：新开对话先贴一句「读 `docs/handoff/HANDOFF.md`」，再照 §8 自查一遍再动手。
> **最后更新**：2026-10-26 12:30 ｜ **时效**：本文是快照，§0/§2/§3 的数字都现场复核过；
> 过两周以上请照 §8 重新量，不要直接引用。

---

#### 0. 30 秒速览

| 项 | 当前值 | 怎么核的 |
|---|---|---|
| 知识库源文档 | **20 篇** md（`knowledge/cs/{os,network,db,dsa}/`） | `ls knowledge/cs/*/` |
| 生产向量库 | `backend/chroma_kb/`，collection **只有 `cs_basics`** 一个 | `chromadb.list_collections()` |
| 切片 | **248 child / 81 parent**（`chunk_mode=parent-child`） | 数 `metadatas` 与 `distinct(parent_id)` |
| 基线指纹 | **`sha256:bbe0cdc42635ac1c936d678ad22f3e8c`** | `eval/fingerprint.load_baseline_meta()` |
| 评测集 | 83 条（L1 金标准 79 条计分） | 同上 `eval_cases_count` |
| **元数据里有没有 `scope`** | **没有**（`set(m['scope']) == {None}`）→ **档① 未做** | 读全库 metadatas |
| 测试基线 | `run_tests.py` **550/550** | 2026-10-26 深夜实跑（500 + XB-23 23 + XB-24 6 + XB-26 路由 21） |
| 检索链路 | 最前面有**文档路由 L1**（`agent/rag/doc_router.py`，默认开，收窄到 top3 篇） | `/chat` 日志看 `路由=ok(...) N ms→M 篇`；`RAG_DOC_ROUTER=0` 可关 |
| 课堂资料层 | `classroom_{id}_docs` 落 `backend/chroma_classrooms/`，原件落 `backend/classroom_docs/`，登记表 `classroom_documents` | `POST /upload/teacher` + `persist=classroom` |
| 教师端入口 | `/teacher` →「备课」页签顶部「课堂资料」卡片（上传 / 清单 / 删除） | 打开 3000 的 `/teacher` 看第一张卡 |
| 服务 | 8000 **在跑**（后端）、3000 **在跑**（前端静态导出 `frontend/out`）；5173 无监听 | `curl --noproxy '*'` 三个端口 |
| 本轮改了产品代码吗 | 改了：XB-23（`app.py` 三处 + `db/*` + `indexer.py` + 新 `classroom_store.py`）、XB-24（`app.py` 三处 + `classroom_store.py` + 前端 `api/types/chat` + `teacher/page.tsx`）、XB-26（新 `agent/rag/doc_router.py` + `retriever.py` 四处 + `app.py` 日志） | `docs/worklogs/B_WORKLOG.md` 末尾 |

**一句话现状**：资料库"三层口径"已经讲清楚、教师侧上传入口已通、`scope` 方案的
设计与规模实测都做完了，**但生产库仍是单 collection、无 `scope` 字段**——
下一任要么做"档① 加元数据"（半天、零回归），要么等触发条件再做档③。

---

#### 1. 这一轮定下的六条结论（决策台账）

后面所有动作都建立在这六条上，改任何一条都要重看相关章节。

| # | 结论 | 依据（可复核处） |
|---|---|---|
| D1 | **不做"教师库 / 学生库"两个物理库**。分层依据不是"角色"，是**生命周期 + 可见范围** | 教师上传件与课堂资料同生命周期（跟随课堂），学生上传件是 1h 会话级；按角色分库会把"同一份课堂资料"劈成两半 |
| D2 | **写入分层、读取合并**。写入默认落课堂库，审核通过才晋升共享库；读取永远两库并行召回 | 只查课堂库会让教师资料顶掉教材、学生变盲；同构 Git 分支→PR→main |
| D3 | **共享库不允许"无审核上传即刻生效"**（不是技术上做不到） | 全局无归属、L1 金标准绑 `gold_index_fingerprint`（`bbe0cdc4`/248 片）一写就废、加容易删难 |
| D4 | **课堂库生命周期 = `Classroom` 行的生命周期**，只随课堂删除而清理 | ★ 早前"结课即 drop"是**错的**：`Classroom` 模型根本没有结课/归档字段，且删库违背"结课后回来复习"的正当需求。将来若加结课，应加 `status=archived`（禁写只读），不是删数据 |
| D5 | **当前 248 片规模走"一课一 collection"（方案 B）即可，不上 `scope`** | 方案 B 代码更小、撤销更干净、无泄漏面；`scope` 的收益（collection 数不随课堂涨）此刻为零 |
| D6 | **`scope` 方案也不该"全塞一个 collection"，而是两库分层**（`kb_shared` 不过滤 + `kb_classroom` 按 scope 过滤 + 会话内存库） | ★ 实测推翻：Chroma 带 `where` 时**不走 HNSW，改扫过滤子集**（20k 片：不过滤 3ms / 50% 子集 147ms / 1% 子集 29ms） |

---

#### 2. 本轮交付物清单

| 文件 | 类型 | 状态 | 证据 / 备注 |
|---|---|---|---|
| `app.py` `/admin/reload-kb` | **产品代码**（越界，已登记 XB-20） | ✅ 已落地 | `_client_ip`(1093) / `_is_loopback_client`(1099) / `_admin_gate`(1114) / 路由(1133)；加 `10/minute` 限流。实测 `--host 0.0.0.0` 下局域网 IP → 403，本机 → 200；配 `ADMIN_RELOAD_TOKEN` 后无 token 401 |
| `app.py` `POST /upload/teacher` | **产品代码**（越界，已登记 XB-21 方案 A） | ✅ 已落地 | `_index_upload`(308) 抽出共用；`_require_teacher`(1969) 校验 role；学生入口(2187) / 教师入口(2214)；教师独立限流 10/h；`DocumentSession`(244) 扩 `audience/teacher_id/classroom_id` |
| `docs/proposals/PROPOSALS.md` | 设计提案（新增） | ✅ 设计完成，**未实施** | §一~§八 设计 + §九 规模实测与架构修正 |
| `scripts/update_kb.py` `infer_chunk_mode()` | 工具修复 | ✅ 已落地 | 修掉"不带 `--parent-child` 被静默判成全量重建"；+7 例单测 |
| `tests/test_app.py` `TeacherUploadTests` | 测试 | ✅ +4 例 | 含"非教师被拒时 `build_index` 一次都不许调用" |
| `tests/test_update_kb.py` / `test_kb_hot_reload.py` | 测试 | ✅ +7 / +17 例 | 合计到 500 |
| `docs/specs/RAG_HANDBOOK.md` | 文档 | ✅ 已同步 | §2 库结构表加教师列、§8 第六/七部分、§9 更正结课口径 |
| `docs/worklogs/B_WORKLOG.md` | 文档 | ✅ 已同步 | 文末 XB-20 / XB-21 / XB-22（含补记） |
| `env.example` | 配置样例 | ✅ 已加 `ADMIN_RELOAD_TOKEN` | |
| `scripts/backfill_scope.py` | 回填脚本（档① 需要） | ❌ **不存在，未写** | 全文骨架见 `docs/proposals/PROPOSALS.md` §4.2 |
| `docs/proposals/PROPOSALS.md` | 设计提案（新增） | ✅ 设计完成 | 教师上传三条语义 + 方案 B 全套设计 |
| `agent/rag/classroom_store.py` | **新产品代码**（XB-23） | ✅ 已落地 | 原件落盘 / 派生索引 / 检索 / 清理；`retrieve_classroom_docs` |
| `db/models.py` `ClassroomDocument` + `db/repository.py` 六个函数 | **新产品代码**（XB-23） | ✅ 已落地 | 登记表 `classroom_documents`（11 列） |
| `app.py` `persist=classroom` / 三路注入 / 删除端点 | **产品代码**（越界，登记 XB-23） | ✅ 已落地 | 实测：同课堂命中 3 条、跨课堂 0 条、重启仍命中、删除即失效 |
| `tests/test_classroom_docs.py` | 测试 | ✅ +23 例 | 跨文档去重 / 权限 / 降级 / 副作用顺序 / 注入口径 |
| `scripts/purge_classroom_docs.py` | 运维脚本 | ✅ 已落地 | 孤儿课堂资料清理，默认 dry-run |

> ⚠ **注意两个已存在但容易看错的东西**：
> 1. `backend/backup_chroma_*` 是历次实测留下的备份目录（`chroma_del_*` / `chroma_s1` 等是隔离实验残留），
>    **不是**生产库，清理前先确认没有脚本引用。
> 2. `backend/backup_baseline_meta_20261026_0208.json` 是**旧基线**（235 片 / `2db8513a`），
>    现行基线是 `bbe0cdc4` / 248 片，别拿错。

---

#### 3. 关键实测数字（引用前先看"适用边界"）

##### 3.1 检索基线（L1，数据来自 `eval/rag_baseline.json`，父子 full 配置）

| 指标 | 值 | 口径 |
|---|---|---|
| top1 / top3 | **68/71** / **70/71** | 75 条评测集、71 条计分，**不是** 83 题全体 |
| nDCG@3 | 0.870 | 金标准 `topic-derived+llm-refined`，parent 粒度 79 条计分 |
| 提升度 | +0.060 | |
| p95 | 4.3 s | 父子预算 ≤5 s，**只算无降级子集** |
| 降级 | 0 | |

> 引用纪律：`68/71` 与 `7/7`、`35/36` **不可比**（不同题集）。任何检索分数报出来必须带题集。

##### 3.2 规模实测（本轮新做，环境：本机 Chroma 1.5.9 / jieba 0.42.1 / 384 维）

**BM25 冷启动 —— 瓶颈在分词，不在建索引**

| 切片数 | jieba 分词 | 建索引 | 一次打分 | 冷启动合计 | 常驻内存 |
|---|---|---|---|---|---|
| 5 000 | 7.79 s | 0.19 s | 17 ms | **8.0 s** | 20 MB |
| 20 000 | 19.35 s | 0.77 s | 49 ms | **20.1 s** | 81 MB |
| 50 000 | 46.85 s | 1.86 s | 144 ms | **48.7 s** | 203 MB |

分词占 96%。内存约 4.2 KB/片（仅 token 列表；线上 `_BM25_CACHE` 还另持 `docs`，每片 `parent_text` 约 3 KB）。

**Chroma 带 `where` 的检索路径 —— 走扫子集，不走图**

| 过滤条件 | 平均耗时（20k 片，10 次均值） | 返回 |
|---|---|---|
| 不过滤 | **3 ms** | 6 条 |
| `scope=shared`（约 50% 子集） | **147 ms** | 6 条 |
| `$in[shared, classroom:1]`（约 51%） | 171 ms | 6 条 |
| `scope=classroom:7`（约 1%） | **29 ms** | 6 条 |

耗时随**子集大小**线性增长、不随结果条数变化 ⇒ 带 filter 等于**放弃图索引改扫表**。

**适用边界（别外推过头）**：
- 以上是本机单机、单进程、384 维、无并发压测的数字；换机器/换 Chroma 版本要重量。
- 基准脚本是临时探针，**已删除**（沿用项目"临时脚本用完即删"的惯例）。
  要再测就在 `/tmp` 或隔离目录重新写，**不要往生产 `chroma_kb` 里灌测试数据**。

##### 3.3 六个已实测的 Chroma 语义（做档③ 前必须信任这六条）

| 验证点 | 结果 |
|---|---|
| `filter=` 直通 `query(where=...)` | 是（`langchain_chroma/vectorstores.py:780`）⇒ pre-filter 在 ANN 前 |
| 不过滤会不会跨课堂召回 | **会**（让某课堂片与查询向量完全一致，不过滤时它就是 top1） |
| `$in` / `$and` 组合 | 可用 |
| 无 `scope` 字段的旧片带过滤时 | **查不到** ⇒ 必须先回填再开过滤 |
| `collection.update(ids, metadatas)` 语义 | **合并**（`parent_text`/`parent_id` 保留）、不重算 embedding；换 Chroma 版本必须重测 |
| `eval/fingerprint.py` 哈希什么 | 只 `(source, start_index, page_content)`，**不含 metadata** ⇒ 只加 `scope` 不动指纹；给 shared 的 `source` 加前缀就会动指纹 |

---

#### 4. 未决事项（按优先级，含卡点与归属）

| # | 事项 | 卡在哪 | 归属 | 建议 |
|---|---|---|---|---|
| U1 | **XB-15 / 18 / 19 / 20 四项待 A 复核**（都是 `app.py` 行为变更，已实测达标） | 等 A 签字 | A | 四项互相独立可一起过；U2 前先过 XB-20 |
| U2 | **XB-21 方案 B（课堂级 collection 长期生效）** | ~~待 A 定档~~ **已实施（XB-23，2026-10-26），待 A 复核** | A 复核 | 设计 `docs/proposals/PROPOSALS.md`；实现与实测见 `HANDOFF` §2 与 `docs/worklogs/B_WORKLOG.md` 末尾 XB-23 |
| U3 | **晋升审批人角色**（课堂资料 → 共享库，谁批） | 产品口径未定 | A / 产品 | 在此之前共享库只走"人工改 md + `update_kb.py`"，保持现状即可 |
| U4 | **反向代理部署必须配 `ADMIN_RELOAD_TOKEN`** | 已识别未落地 | 部署时处理 | 代理若在本机，`_is_loopback_client` 会判成本机而放行 → 必须开令牌模式 |
| U5 | `/upload`（学生入口）**无鉴权**，且 3/hour 是共享限流桶 | 同类问题，未登记 XB | A | 若要收，仿 XB-20 做法；教师桶已独立（10/h）不受影响 |
| U6 | `kind=oos` 库外题集滞后：4 条里 2 条已被扩库覆盖 | 改了要重算 L1 金标准与 L4 分母 | B | **单独一轮做**，别顺手改 |
| U7 | ~~p95 压回 3s 内（重排改用短文本打分）~~ | 属改动不是调参，需重建基线 | B | **2026-10-27 已落地并转默认**：`RAG_RERANK_TEXT=child_title`（**命中片 + 小节标题**；纯 child 是次优）。L1 门禁口径三档独立进程对照（`scripts/l1_p95_by_mode.py`）：parent nDCG 0.8818 / p95 3.655s ✗；child 0.8549 / 1.103s ✓；**child_title 0.8651 / 1.134s ✓（默认）**。top1/top3 三档**完全不变**（68/71、70/71）。提升度 +0.0657→+0.0388→**+0.0490**。13 条单测（`tests/test_rerank_text.py`）锁定"注入文本永远是 parent"。回退：`parent`（⚠ 会让 p95 回到 ~3.65s 判红）。细节见 `docs/plans/PLANS.md` §7.5 |
| U12 | ~~代码 `GATE_L1_P95=3.0s` vs 手册「父子 ≤5s」预算冲突~~ | 分档的前提（重排打 parent 全文）已失效 | B | **2026-10-27 已闭环：统一回 3.0s，删除分档**。child_title 落地后父子 p95 = 1.134s，与扁平同量级，分档不再有存在理由。已改 `eval/e2e_runner.py`（常量注释写明由来与回退警示）、`docs/specs/RAG_HANDBOOK.md` §3、`docs/plans/PLANS.md` §7.6 |
| U8 | `hetero_judge` 异源裁判（P4 唯一余项） | 需 A 提供异源 key/模型 | A | 阻塞在外部资源 |
| U9 | ~~注入多样性缺失~~ **已实测否决，别拉多样性**：83 条里 67 条同源堆叠是**相关性的自然结果**，不是浪费 | 本库问题与文档基本一一对应 | B | 开关保留默认关（`RAG_SOURCE_QUOTA` / `RAG_MAX_PER_SOURCE`）。A/B：2:1 把堆叠 67→37，但**注入相关率 78.6%→56.2%**、p95 +0.53s、9 例少注入，top1 零提升。后续若再有「注入重复」的建议，先跑 `scripts/ab_source_quota.py` |
| U10 | 「快排的时间复杂度是多少」**不带 topic** 时 `top_score=-2.132` 被 `floor=-2.0` 拒成 0 注入 | 该问法不在 83 条评测集内；61 条库内题实测 floor 零误伤 | B | 先用 `scripts/diag_floor_margin.py` 常态化检查；这类「短属性问句」要不要进评测集属改金标准，见 U6 同一约束 |
| U11 | 融合后候选池偶尔只有 **3 条**（`fused_k=3`，向量与 BM25 高度重合） | 未定位到触发条件（路由收窄后两路命中同几片） | B | 记录观察；若将来看到重排输入过少导致排序不稳，从这里入手 |
| U13 | ~~评估链路两次串行 LLM 挡在检索之前（TTFT 第一杠杆）~~ | 理解评估只读 topic/hint_level/messages，**不读任何检索产物**，无数据依赖 | B | **2026-10-27 已落地并转默认（O3）**：`抽 topic → gather(理解评估, 三路检索)`，入口 `agent/graph.py:run_assessment_with_parallel`。A/B（`measure_ttft.py`，各 8 条、正反序各一轮）：前置段 **3126/3122ms（并行）vs 4061/4883ms（串行）**，快 0.94~1.76s；并行侧数值明显更稳（只由评估 LLM 决定）。⚠ 收益上限是 **min(理解评估, 检索)**，较长那侧省不掉。回退 `CHAT_PARALLEL_ASSESS=0`。9 条单测 `tests/test_assessment_parallel.py`（含"耗时接近 max 而非 sum"与"分步结果 ≡ assessment_graph.ainvoke"）。细节见 `docs/plans/PLANS.md` §7.7 |
| U14 | ~~抽 topic 节点（E）耗时 ~1.0s，纯串行挡在评估与检索之前~~ | `deepseek-flash` 是**推理模型**，回 "same" 也要先想 20~30 个 reasoning token（实测 completion 25 里 23 是 reasoning）；**不是** prompt 太长（砍长度只省 7ms） | B | **2026-10-27 已落地并转默认（O11）**：只给抽取节点 `bind(extra_body={"thinking": {"type": "disabled"}})`，理解评估不关。节点级 A/B（各 81 样本）：mean **1012→656ms（−35%）**、p95 **1509→878ms（−42%）**，判定准确率 **96.3%→97.5%**（唯一差异那条关掉后判得更对）。端到端（n=8）mean −44ms（噪声内）、中位 −222ms、p95 −518ms ⇒ **以节点级为准**。开关 `RAG_TOPIC_NO_THINK=0` 回退；厂商不认该字段时自动降级重试并打 WARNING。9 条单测 `tests/test_topic_no_think.py`。⚠ 三处测试 fake LLM 需补 `bind()`（已修）。细节见 `docs/plans/PLANS.md` §7.9 |
| U15 | ~~理解评估 A（~2.08s）是前置段更长的一极，关思考潜在收益 −1s 级~~ | 装了尺子后实测否决：延迟 −68% 但**误解检出 −10pp**，退化形态是**过度检出**（把“完全没思路”记成“误解 X”），而 misconception 会落进 `_update_profile` 的 mastery ⇒ 静默污染画像 | B | **2026-10-28 否决，不转默认**：正反序各 2 轮 × 每档 60 样本，两次独立运行高度复现（resolved 96.7% 两档持平；误解检出 83.3%→73.3%；strict 70.0%→63.3%；mean 2410~2674ms→764~772ms）。A 节点继续保留思考。想再试需先在 prompt 加显式约束：“学生只是表示没听过/不知道时，misconception 留空”。尺子：`eval/data/assess_gold.json` + `eval/assess_quality.py`；细节见 `docs/plans/PLANS.md` §7.10.1 / §7.10.2 |
| U16 | ~~追问时 topic 没变，却仍要等抽 topic（E ~0.66s）~~ | 盲缓存被 XB-31 否决（换题轮 93% 退化、topic 落库后跨轮累积）；改用**投机执行** gather(E, A(prev), R(prev))，E 仍是唯一裁决者 | B | **2026-10-28 已落地并转默认（O12）**：三层验证 —— 单测固定延迟 **250ms vs 507ms**（差 ≈ 注入的 E，干净因果）；节点级配对（间隔 9s 避开限流）**4097→3140ms（−23.3%）**，12/15 更快，与理论收益 E/max(A,R)≈24% 吻合；小间隔 0.8s 时命中组仅 −170ms ⇒ **限流会吃掉投机收益**，限流严格的部署应置 `CHAT_SPECULATE_TOPIC=0` 回退 O3。11 条单测 `tests/test_assessment_speculative.py`，含“未命中时投机段异常必须吞掉”（否则正常换题误报 502）与“不留孤儿任务”。细节见 `docs/plans/PLANS.md` §7.10.3 |

---

#### 5. 下一步动作（按触发条件分支，不要越级跳档）

##### 分支 A —— 现在就能做（半天，零回归）

**档① 只加 `scope` 元数据 + 回填**，检索链路一行不动：

1. `agent/rag/indexer.py` 的 `build_chunks` 注入 `scope="shared"` / `scope_kind` / `owner_id`；
2. 写 `scripts/backfill_scope.py`（骨架见提案 §4.2，含 `--dry-run` 与**末尾自校验**：
   `get(where={"scope":"shared"})` 片数必须等于回填前总数）；
3. 跑 `update_kb.py` 全量重建或直接回填，然后核 **指纹必须仍是 `bbe0cdc4`**；
4. 加单测守住"共享层 `source` 不带前缀""`parent_id` 唯一"。

**为什么值得先做**：它把不可逆的迁移成本前置，把有风险的检索改动后置；指纹不变、L1 基线可续用。

##### 分支 B —— 触发条件到了再做（6~7 天）

满足任一即启动档③「课堂池统管课堂库」（**共享库不进池**）：
1. 课堂数 **> 20~30**（collection 管理、BM25 冷启动、清理逻辑开始成负担）；
2. 需要"一次检索跨多个课堂"（教师看多班对比）——多 collection 要 N 次检索再合并；
3. 需要按 scope 做全局统计/巡检（`get(where=...)` 一次拿到）。

实施顺序（顺序错了会踩坑）：
**回填 → BM25 池化 → 课堂池写入 + `scope` 过滤 + 检索后断言 → 异步预热 → `app.py` 接线 → 单测 + L1 复跑**。
> 反了会怎样：先开过滤再回填 = 共享库自己查空（表现为"命中 0 但库里有 248 片"）。

##### 分支 C —— 远期（2~3 周 + 重做评测口径）

课堂池预计 **> 5 万片** → 先"按学期分池"再上线；
总量 **> 20 万片** → 换引擎（Qdrant / Milvus）+ BM25 外置（ES / Meilisearch）。

> ★ **汇报口径纪律**：`scope` 方案解决的是「**collection 数量**随租户增长」，
> **不解决「数据量」增长**。若"规模很大"指几十万片，做 `scope` 只完成约 40% 的功课，
> 别说成"规模问题已解决"。

---

#### 6. 硬约束（违反会返工，都是踩过的）

1. **共享库的 `source` 永远保持原样**（相对路径，不加 scope 前缀）。`source` 参与指纹，
   一改就必须重刷金标准；只有课堂/会话资料的 `source` 才前缀化（`classroom:7/讲义.md`）。
2. **回填必须先于开过滤**。顺序反了共享库会被自己查空，且**不报错**。
3. **`_require_teacher()` 要校验 role，不是只查 membership**（只查 membership 会放行学生）。
4. **上传件必须留在内存 Ephemeral**：`build_index()` 不传 `persist_directory` 是
   "上传件不污染评测基线"的**根因**，别"顺手"改成落盘。
5. **热重载要连 `_BM25_CACHE` 一起清**，否则混合召回拿旧语料匹配新切片。
6. **别用 `collection.count()` 判断热重载是否生效**（读 SQLite 元数据，永远返回最新值），要用真实检索。
7. **切片模式不用手动指定**（`infer_chunk_mode()` 会按库内快照推断）；
   手动漏加 `--parent-child` 会被判成参数变更而**静默退化全量重建**。
8. **降级请求的耗时不许混进 p95**（用 `eval/slo_check.py`，只判无降级子集）。
9. **没跑的评测层在报告里必须写 `None`**，不能填占位 dict。
10. **改 `app.py` / `agent/prompts.py` / 前端必须先在 `docs/worklogs/B_WORKLOG.md` 登记 XB-n**。
11. **改产品行为或提示词措辞必须同步评测判定端与门禁**（XB-10/13/14 三次验证过）。
12. 相关性过滤是**两层**：整题 `relevance_floor`（-2.0）挡库外题硬塞，逐条 `per_doc_floor`（-5.0）挡库内串主题。
13. **补库只由教学计划驱动，不由缺口清单驱动**：`kb_gap_events.jsonl` 里的提问是
    「学生问了什么」，不等于「课程该讲什么」。按 gap 逐条补库没有分母、永不收敛，
    且会让系统答出老师没讲的内容（教学不一致）。三条硬触发全满足才补，见
    `docs/specs/RAG_HANDBOOK.md` §1.2。同类缺口默认按「超纲」处理，动作是域外判别而非扩库。

---

#### 7. 环境与复现（照抄即可，**不要 `pip install`**，环境是整体搬运的）

```bash
cd <项目根>/Tutor-Chatbot-main/backend

# 起服务（必须 unset 代理 + 必须带 RAG_PARENT_CHILD=1）
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy RAG_PARENT_CHILD=1 \
  ./venv/Scripts/python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000

# 跑测试
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy ./venv/Scripts/python.exe run_tests.py

# 增量更新知识库（先 --dry-run）
./venv/Scripts/python.exe scripts/update_kb.py --dry-run
./venv/Scripts/python.exe scripts/update_kb.py

# 增量后热重载（免重启）
curl -X POST http://127.0.0.1:8000/admin/reload-kb
# 令牌模式下：curl -X POST -H "X-Admin-Token: <token>" http://127.0.0.1:8000/admin/reload-kb

# 验证「增量 ≡ 全量」（7 场景，改切片/索引逻辑后必跑）
./venv/Scripts/python.exe scripts/verify_incremental.py

# 检索评测（带指纹校验）
./venv/Scripts/python.exe scripts/smoke_rag.py --compare
```

**库现状自查一行**（不用起服务）：

```bash
./venv/Scripts/python.exe -c "
import chromadb; from eval import fingerprint as F
c=chromadb.PersistentClient(path='chroma_kb'); col=c.get_collection('cs_basics')
print(col.count(), F.load_baseline_meta()['index_fingerprint'])"
```

**注意事项**
- MySQL 3306 常驻（库 `ai_classroom`）；`.env` 里有 DeepSeek key —— **勿打印、勿提交**。
- 跑 `/chat` 或重排时别并发跑 CPU 密集任务，否则 p95 被污染。
- 重起后第一条 `/chat` 会触发重排模型懒加载（p95 虚高约 +10s），**读延迟前先跑一条预热**。
- 目录分工：backend 根只有 4 个入口 `app.py` / `run_tests.py` / `build_kb.py` / `init_db.py`；
  smoke/verify/一次性工具在 `scripts/`。

---

#### 8. 交接自查清单（**必做**，状态可能已变）

```bash
# 1) 库还是不是 248 片 / bbe0cdc4？（不是 → 基线已变，先读 B_WORKLOG 末尾找原因）
#    见 §7 的「库现状自查一行」

# 2) 元数据有没有 scope？（决定档① 做了没）
./venv/Scripts/python.exe -c "
import chromadb; col=chromadb.PersistentClient(path='chroma_kb').get_collection('cs_basics')
print(sorted(set(m.get('scope') for m in col.get(include=['metadatas'])['metadatas'])))"
#    期望：{None}  → 档① 未做
#          {'shared'} → 档① 已做

# 3) 测试基线
./venv/Scripts/python.exe run_tests.py        # 期望 529/529，不是就查 XB 记录

# 4) 服务端口（本轮结束时全关，可能是 000）
curl -s --noproxy '*' -o /dev/null -w "%{http_code}\n" --max-time 2 http://127.0.0.1:8000/
```

**然后回来改本文的 §0 和 §2** —— 不然后面的人会按旧状态做事。

---

#### 9. 文档地图（按"真相源"优先级）

| 想知道什么 | 读哪 | 优先级说明 |
|---|---|---|
| **代码到底怎么写的** | 代码本身 | **最高**，文档一律以代码为准 |
| 资料库三层口径 / 维护命令 / 硬约束 | `docs/specs/RAG_HANDBOOK.md`（§2 库结构、§8 增量更新、§9 方案 B 待办） | 运维主手册 |
| `scope` 方案的设计、实测、成本、触发条件 | `docs/proposals/PROPOSALS.md`（§一~§九） | **本次交接的核心**，含 9.1~9.6 规模修正 |
| 每个改动为什么改、改在哪、验证了什么 | `docs/worklogs/B_WORKLOG.md`（2026-10-24 ~ 10-26，重点 XB-13~22） | 决策过程与证据 |
| 下一轮开场指令与剩余事项 | `docs/handoff/NEXT_SESSION_PROMPT.md`（v6.6） | 会话入口 |
| 逐日踩坑流水（教训最全） | `.workbuddy/memory/2026-10-2{4,5,6}.md` | 排查时翻 |
| 全项目视角 / 前后端契约 | `docs/handoff/HANDOFF.md`、`docs/specs/API_CONTRACT.md` | 跨角色 |

---

#### 10. 给下一任的三句话

1. **别急着写代码。** 当前规模（248 片 / 20 篇）做 `scope` 收益是零，先确认触发条件。
2. **数字都要现场量。** 本文所有性能数字都是本机单进程实测，且基准脚本已删；换机器要重量。
3. **静默失败是这条线的主旋律。** 漏传 filter → 跨课堂泄漏不报错；回填前开过滤 → 查空不报错；
   `count()` 判热重载 → 永远是最新值；判错切片模式 → 悄悄全量重建。
   改完一律用**真实检索**验证，不要只看日志或计数。

---

<a id="project-handoff"></a>

## 3、原名 项目交接说明.md

### AI Classroom 项目交接说明

> **给谁看**：接手这个项目的 AI 助手（新对话）、以及新加入的同学。
> **怎么用**：新对话开头把它贴进去，或者说「先读 `docs/handoff/HANDOFF.md`」。
> 读完**不要直接开工** —— 先做第 8 节的自查清单，因为项目版本可能已经变了。

---

#### 1. 项目是什么

**大作业**：基于大模型的个性化资源生成与学习多智能体系统开发
**产品名**：AI Classroom —— 面向计算机八股（操作系统 + 计算机网络）学习的 AI 智能教室

三类角色：
- **学生**：在聊天室提问，拿到基于本人画像的个性化讲解
- **教师**：建课堂、看记录、输入主题让 AI 生成备课大纲和 PPT
- **AI**：回答学生问题（带知识库检索）、给教师生成教学资源

**最重要的一条**：这是**基于开源项目 Tutor-Chatbot 的二次开发**，
不是从零写。能复用的一律复用，只改与本需求相关的部分。

⚠️ 知识库实际范围是 **OS + 计网**，不是 `docs/specs/REQUIREMENTS.md` 里写的 Java
（初期契约没来得及同步，以 `knowledge/` 目录实际内容为准）。

---

#### 2. 角色分工

| 角色 | 负责 |
|---|---|
| **A**（吴甲月） | 后端与 AI 算法：大模型调用规范、多智能体协作协议、Prompt/RAG 指导 |
| **B**（温丽杏） | 算法优化与资源生成：Prompt 模板、Few-shot、难度可控、学习者画像与认知诊断 |
| **C**（**当前用户**） | 系统集成与前端交互：自适应界面、对话流实时展示、端到端集成测试 |

> 用户是 **C**。前端归他，后端一行不归他改。

---

#### 3. 技术栈（已定型，不要改）

- **前端**：Next.js 16 (App Router) + React 19 + TypeScript + Tailwind 4
- **后端**：FastAPI + LangGraph + LangChain + slowapi（限流）
- **数据**：MySQL（6 张表）+ ChromaDB（向量库）
- **模型**：DeepSeek `deepseek-flash`，走 OpenAI 兼容端点（`.env` 里 `LLM_PROVIDER=openai`）
- **客户端**：Electron 外壳 + Next.js 静态导出（`output: "export"` + `trailingSlash`）

禁止无必要引入 Redis / Kafka / 微服务 / Kubernetes 等。

---

#### 4. 目录结构（按项目根目录相对）

```
API_CONTRACT.md            ★ 前后端唯一契约，前端只读不改（16 个接口 + SSE 事件表）
AGENT_PROTOCOL.md          A 的交付物：模型调用规范 + Agent 协作协议
B_WORKLOG.md               B 的变更日记（含「越界改动」XB-n 登记）
C_WORKLOG.md               前端变更记录
FRONTEND_PLAN.md           前端设计说明（含 MVP 范围清单）
REQUIREMENTS / DEVELOPMENT / DEVELOPMENT_PLAN / PROJECT_REPORT
STAGE_REPORT_T8_T9 / TEST_REPORT / docs/tutorial.md / docs/handoff/HANDOFF.md
knowledge/cs/{os,network}/ 知识库源文档（8 篇，唯一参与检索）
Tutor-Chatbot-main/
    backend/               FastAPI 后端（A/B 的地盘）
    frontend/              Next.js 前端（C 的地盘）
```

---

#### 5. 铁律（违反会出问题）

1. **只改指定目录**。前端改动只落在 `Tutor-Chatbot-main/frontend/`。
2. **不动 B 的地盘**：`agent/prompts.py`、`agent/state.py`、`agent/personalize.py`、
   `agent/rag/`、`knowledge/`。要动必须在 `docs/worklogs/B_WORKLOG.md` 登记 XB-n。
3. **不动后端**。需要新能力先提需求，不要自己加接口。
4. **`docs/specs/API_CONTRACT.md` 只读**。契约里没写的字段/事件，先问再写。
5. **MVP 优先**：能运行 > 能演示 > 结构清晰 > 功能扩展。
   用户明确「只做最小可执行前端，鉴权等后面再加」，**不要主动扩功能**。
6. **契约里没有的能力先找 A 定接口**，不要前端自己造"影子协议"。
7. **后端 8000 / 前端 3000 常驻**，别重复起。后端无 `--reload`，改 `app.py` 必须重启。
8. **临时脚本用完即删**。工作记录只记重要改动，不写排查流水账。

---

#### 6. 接口契约的关键陷阱（最常翻车的几条）

1. **`provider` 不要传**。后端默认即 DeepSeek；传 `groq`/`gemini` 会 **503**。
   （开源原版就是传了 `groq`，所以它连不上本项目后端。）
2. **SSE 必须按 `\n\n` 分帧**，不是 `\n`。按单行切会跨分片丢字。
3. **回答中途的错误走事件体 `type=error`**，此时 HTTP 状态码已是 200。
   不处理会一直转圈。
4. **`classroom_id` + `user_id` 必须成对传**，缺一个就静默不落库（表现为刷新记录全没）。
5. **请求体 `extra="forbid"`**，多传一个未知字段直接 422。
6. **`sources` 是第一个 SSE 事件**：带本轮引用的资料切片，不带正文。
   三态必须区分：`items` 非空 / `items: []`（明确没用资料）/ 收不到事件（这条流没覆盖）。
7. **`state` 不是必然出现的事件**：纯寒暄或未识别知识点时只发 `token` + `[DONE]`。
   收不到就**保持上一轮状态，不能清空**。反过来，收到 `state` 一定是真的，
   且一定带 `personalized` 键。
8. **创建课堂不会自动把创建者加为成员**。教师必须显式
   `POST /classrooms/{id}/members` 且 `role="teacher"`，否则备课接口 403。
9. **`GET /students/{id}/profile`** 带 `classroom_id` 返回单个对象，不带返回列表包装。
10. **限流按客户端 IP**：`/chat` 10 次/分、备课 5 次/分、上传 3 次/时。
11. **AI 消息 `role="assistant"`、`username=null`**；`download_url` 是相对路径需拼 Base URL。
12. **权限看 `classroom_members.role`**，不是 `users.role`。
13. **后端无鉴权**，前端 localStorage 是身份的唯一真相源。

---

#### 7. 已知的项目边界（写报告时要说明）

> **2026-10-24 更新**：M4 落地后「多智能体 / 多场景」两条已不再是硬边界，
> 下面按当前实际状态重写。其余边界不变。

**已经不是边界的（M4 已解决）**

- 多智能体编排已落地：`plan_agents(scenario)` 产出角色序列，逐个调 LLM 流式发言，
  前一个的发言注入后一个的提示词。但**主管仍是规则，不调 LLM**（见 `docs/specs/AGENT_PROTOCOL.md` §3.4）
  —— 所以准确说法是「规则编排的多角色协作」，不是「自主决策的多智能体」。
- 多场景已支持：`v1` / `group` / `debate`，建房即定，`GET /scenarios` 拿选项。

**仍然是边界的**

- 主管不做 LLM 决策，场景与角色序列是固定的，没有运行时动态路由。
- 无鉴权、无部署方案、无性能测试数据。
- 评估报告（成果 #4）采集层就绪，**四维人工评分还没填**。
- `sources` 落库了，但后端把 `NULL`（老数据/未覆盖）和 `[]`（本轮明确没用资料）
  压成了同一个值返回 —— 刷新后一律显示「未引用资料」，区分度不如流式那一刻。
  前端三分支已按规范写好，后端改回返回原始值即可自动接住。

**2026-10-24 结清（原先记为待做，现已完成）**

- ✅ `sources` 刷新后保留：后端 2026-10-21 已加 `sources` 列，本次补上前端映射
  （`refreshMessages` 之前只映射了 `agent_role`，漏了 `sources`，导致刷新后依据消失）。
- ✅ 「我的课堂」改为后端优先：调 `GET /classrooms?user_id=N`，并用返回的房间内
  `role` 覆盖本地全局角色，处理「一人多房不同身份」。本机记录降级为兜底。
- ✅ `debate` 场景开放：三场景齐全，满足作业验收。

---

#### 8. 开工前自查清单（必做，别跳过）

**版本可能已经变了，不要假设。** 按顺序做：

```bash
cd <项目根目录>

# 1. 看队友最近改了什么
git log --oneline -10
git status --short

# 2. 契约有没有变？（前端一切以此为据）
#    重点看 SSE 事件表、/chat 请求体、错误码
grep -n "sources\|personalized\|事件" docs/specs/API_CONTRACT.md

# 3. 读两份 worklog
#    docs/worklogs/B_WORKLOG.md（后端/算法改了什么、有没有影响前端）
#    docs/worklogs/C_WORKLOG.md（前端上次做到哪、还有什么没做）

# 4. 前端能不能跑
cd Tutor-Chatbot-main/frontend
npm install          # 首次
npx tsc --noEmit     # 类型检查
npm run build        # 静态导出
```

**然后回来修正这份文档** —— 第 9 节的状态是上一任留下的，可能已经过时。

---

#### 9. 交接时的状态

> **最后更新 2026-10-24（本次会话核实 + 更新）** —— 仍然建议按第 8 节自查，
> 但如果自查结果与本节冲突，以自查为准并回来改本节。

##### 9.1 后端（A，M4 已完成）

| 成果 | 状态 |
|---|---|
| #2 多智能体原型 | ✅ 达成（`orchestrator.py` + `scenarios.py`） |
| #3 多场景 Demo | 🟡 后端达成，**前端本次已接上** |
| #4 评估报告 | 🟡 采集层达成，四维人工评分待填（归 B） |
| #5 软著材料 | ⬜ 源码已提交，文本未写（非后端任务） |

测试基线 `run_tests.py` **210 例**；`init_db.py` 会建 `agent_runs` 表并补
`classrooms.scenario` / `agent_runs.rag_hits` 两列（**拉完代码必跑，否则留痕静默失败**）。

##### 9.2 前端（C，MVP + M4 接入）

| 页面 | 路由 | 状态 |
|---|---|---|
| 入口（昵称/身份/课堂号/后端地址/健康检查） | `/` | ✅ |
| 我的课堂 | `/classrooms` | ✅ |
| 聊天室（流式 + 群聊轮询 + 画像侧栏 + 资料依据 + **多角色气泡**） | `/classroom` | ✅ |
| 教师工作台（课堂/备课/记录三页签 + **建房选场景**） | `/teacher` | ✅ |

- Electron 桌面客户端外壳已完成（开发态 / 静态态）
- `tsc --noEmit` 零报错；`npm run build` 4 路由全部静态导出（本次核实通过）
- SSE 解析有 20 条断言覆盖（含每 3 字节切片的极端分帧）

##### 9.3 还没解决的

| 事项 | 谁 |
|---|---|
| **真实 `/chat` 端到端未验证**（本机无后端环境，见 §10） | 需要 A/B 环境 |
| `debate` 场景是否开放（一轮约 14s） | 用户拍板，前端加一行即可 |
| 成果 #4 四维人工评分 + 结论 | B |
| 软著文本 | 待定 |
| `LLM-8` 超时重试 / `LLM-6a` 日志 | A |

##### 9.4 文档版本漂移（读的时候留个心眼）

- `README.md` 停留在 M1 前：「前八阶段完成 / 81 例测试」已过时（现 210 例），
  且知识库写的是 Java（实际是 OS + 计网）。归 A 维护，本次未改。
- 接口数量说法不一致：`README.md` / `docs/plans/PLANS.md` 写 16 个，
  `docs/specs/API_CONTRACT.md` 实际 27 个（M1–M4 增量）。以前者为准会漏掉 `/scenarios`、`/evaluation/*` 等。
- 本节第 7 条「不支持多场景」已按 M4 现状改写。

---

#### 10. 前端/客户端常见坑

| 现象 | 原因 / 解法 |
|---|---|
| 浏览器点进子页面 404 | 静态导出被挂在子路径下，而路由是绝对路径。**用 `npm run dev` 开 `http://localhost:3000`**，别直接开 `out/index.html` |
| 浏览器接后端被 CORS 拦 | 必须用 `localhost` 而不是 `127.0.0.1`（后端 `ALLOWED_ORIGINS` 只写了前者） |
| 客户端白屏、控制台 `_next/... 404` | `out/` 重建过但客户端还在用旧页面（资源哈希对不上）。**先关客户端再 build** |
| Electron 报 `registerSchemesAsPrivileged` undefined | 环境里有 `ELECTRON_RUN_AS_NODE=1`，Electron 被降级成纯 Node。用 `env -u ELECTRON_RUN_AS_NODE` 启动 |
| 客户端一闪就没 / `GPU process isn't usable` | 虚拟机/远程桌面里 GPU 起不来。`main.js` 已默认关硬件加速，真机可加 `--enable-gpu` |
| `npm run build` 报 `failed to read state` | 某类沙箱环境的删除守卫坏了。用 `NODE_OPTIONS= npm run build`。**普通机器不会遇到** |
| Electron 二进制没下载 | `ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/ node node_modules/electron/install.js` |

---

#### 11. 给新助手的一句话

这份文档是**上下文压缩包**，不是真理。项目真正的依据永远是：
**`docs/specs/API_CONTRACT.md`（接口）** 和 **代码本身**。

拿不准就 `git log` + 读文件，不要凭这份文档里的记忆下判断 ——
尤其第 9 节的状态，随时可能过时。

---

<a id="read-me-first"></a>

## 4、原名 打开前请阅读我.txt

> 说明（2026-10-30 整理时补）：本节写于根目录时代，里面的「根目录有什么」是按当时
> 的散落布局画的等宽清单，现已不成立 —— 文档已收进 `docs/` 分类子目录。清单保留原样
> 只为保留改稿时的对齐与语境，**当前真实文件位置以 `docs/README.md` 为准**。

```text
========================================================================
              AI Classroom —— 打开前请阅读我
              （解压后先看这份，3 分钟了解全貌）
========================================================================

打包日期：2026-10-22
当前版本：M4（多智能体编排 + 多场景 + 评估采集层完成）
交付范围：一个教室 + 一个学科（计算机八股：操作系统 + 计算机网络）


========================================================================
一、这是什么
========================================================================

面向计算机八股 / 面试知识的 AI 智能教室。三类角色：

  · 学生   在聊天室向 AI 提问，拿到基于本人画像的个性化讲解
  · 教师   建课堂、看课堂记录、让 AI 生成备课大纲、PPT 和 5 类教学资源
  · AI     回答学生问题（带知识库）、给教师生成教学资源

技术栈：FastAPI + LangGraph + ChromaDB + MySQL + Next.js，
       大模型走 DeepSeek API。

最重要的一点：这是基于开源项目 Tutor-Chatbot 的二次开发，
不是从零写的。能复用的一律复用，只改和本需求相关的部分。


========================================================================
二、现在做到哪一步了
========================================================================

十二个阶段，后端全部完成，前端待接入：

  T0  环境跑通            [完成]
  T1  提示基调改为讲解式  [完成]
  T2  常驻知识库          [完成]
  T3  MySQL 持久化        [完成]
  T4  成员校验+状态落库   [完成]
  T5  学生画像自动沉淀    [完成]
  T6  个性化提示词        [完成]
  T7  教师备课资源        [完成]
  T8  前后端联调          [后端已完成，前端待接入]
  T9  系统测试与验收      [完成]
  T10 部署与演示          [未做]
  T11 性能记录            [未做]
  T12 项目整理与文档      [未做]

在此之上又做了四个里程碑增量（M1–M4）：

  M1  聊天室 + 多角色 Agent      [完成]  群聊 / 私聊 / 角色发言 / 增量刷新
  M2  学习者画像 × 认知诊断      [完成]  IRT-lite 能力估计 + 知识图谱 + 标定题库
  M3  个性化资源生成算法库       [完成]  5 类资源 + 难度控制 + 零-LLM 出卷
  M4  多智能体编排 + 多场景      [后端完成，前端待接]
                                        评估采集层完成，人工评分待填

测试结论：单元测试 189/189 通过。详见 docs/reports/REPORTS.md 与
docs/handoff/STAGE_REPORT_M1_M3.md。


========================================================================
三、压缩包里有什么
========================================================================

根目录（文档 + 2 个目录）：

  README.md                项目总入口
  REQUIREMENTS.md          需求说明（做什么）
  DEVELOPMENT.md           技术方案（怎么设计）
  DEVELOPMENT_PLAN.md      开发计划（T0-T12 顺序与验收标准）
  AGENT_PROTOCOL.md        大模型调用规范 + 智能体协作协议      ★A 的交付物
  API_CONTRACT.md          前后端接口契约（26 个接口）          ★给前端
  HANDOFF_M4.md            M4 交接说明：谁该接什么、验收清单    ★先读这个
  PROJECT_REPORT.md        第一阶段总结（T0-T7）
  STAGE_REPORT_T8_T9.md    第二阶段总结（T8-T9）
  STAGE_REPORT_M1_M3.md    第三阶段总结（M1-M3）
  TEST_REPORT.md           系统测试报告
  tutorial.md              后端源码导学课程

  knowledge/               知识库源文档，8 篇 Markdown
      cs/os/               死锁、虚拟内存与页面置换、进程与线程、进程调度
      cs/network/          TCP 三次握手、HTTPS 与 TLS、TCP/IP 分层、TCP 与 UDP

  Tutor-Chatbot-main/      工程代码
      backend/             Python 后端（FastAPI + Agent + RAG + 数据库）
      frontend/            Next.js 前端

注意：包里没有 venv、没有 .env、没有向量库文件、没有 node_modules。
这些都是本地环境产物，需要你按第五节自己生成。


========================================================================
四、你该看哪几份文档
========================================================================

  不知道从哪下手 →
      docs/handoff/HANDOFF.md
      这次的交接说明，按人分工写好了：前端接什么、Prompt/RAG 接什么、
      各自的验收清单、三条不能碰的红线。先读这个，再决定读哪份。

  做前端的同学 →
      docs/specs/API_CONTRACT.md
      26 个接口的字段、错误码、限流、SSE 格式、接入流程图都在里面。
      里面有踩坑注意事项，写之前先扫一遍，能省掉来回问的时间。

  做 Prompt / RAG 的同学（B） →
      docs/specs/AGENT_PROTOCOL.md 第 5 节（Prompt 工程指南，Prompt-1~7）
      docs/specs/AGENT_PROTOCOL.md 第 6 节（RAG 检索模块指南，RAG-1~6）
      docs/specs/AGENT_PROTOCOL.md 第 7.5 节（效果评估口径，EVAL-1~6）
      docs/tutorial.md（源码导学）
      规则都编了号，讨论时直接说编号，不用翻代码。

  验收 / 汇报 →
      docs/handoff/PROJECT_REPORT.md + 两份 STAGE_REPORT（做了什么、遇到什么问题）
      docs/reports/REPORTS.md（测了什么、结果如何）


========================================================================
五、怎么跑起来
========================================================================

1) 建虚拟环境装依赖

     cd Tutor-Chatbot-main/backend
     python -m venv venv
     venv\Scripts\pip install -r requirements.txt

2) 配环境变量

     copy env.example .env

   然后编辑 .env，至少填 LLM_API_KEY（缺了 /chat 跑不起来）：

     LLM_PROVIDER=openai
     LLM_BASE_URL=https://api.deepseek.com
     LLM_MODEL=deepseek-flash
     LLM_API_KEY=你的 key

   MySQL 的 MYSQL_* 几项可以不填 —— 不填就不连数据库，
   服务照样能跑，只是不落库、聊天记录刷新后丢失。
   **要落库就必填，并且要跑第 3 步。**

3) 建数据库表（要落库才需要，M4 之后必做一次）

     python init_db.py

   会建 agent_runs 等七张表，并给已有表补列。
   服务启动时也会自动建一次，所以**忘了跑一般也没事**；但如果 MySQL 是
   在后端之后才启动的，那次自动建表会扑空，这时手动补跑一次。

   留痕真丢了的话，直接问接口为什么，别猜：

     http://127.0.0.1:8000/evaluation/status

   它会告诉你数据库通不通、agent_runs 表在不在、一共有多少条留痕，
   以及下一步该干什么（数据库不可用也返回 200，不会 503）。

4) 建知识库（第一次必做，约 1-2 分钟）

     python build_kb.py

   会下载中文 embedding 模型（约 100MB）。下载不动的话先把
   env.example 里 HF_ENDPOINT、HF_HUB_DISABLE_SYMLINKS 两行
   取消注释写进 .env，走 hf-mirror 镜像。

5) 起服务

     venv\Scripts\python -m uvicorn app:app --host 127.0.0.1 --port 8000

   打开 http://127.0.0.1:8000/health 看到 database 状态即正常。
   如果 /chat 返回 502，先检查命令行里有没有代理变量（HTTP_PROXY 等），
   有的话 unset 掉再起服务。

6) 自检

     python run_tests.py        210 条单元测试，零成本，改完代码必跑
     python smoke_rag.py        RAG 检索测试，不调模型，零成本
     python eval_report.py      出评估报告（不调模型，零成本，需数据库）


========================================================================
六、M4 新增了什么（和 10-05 那版最大的区别）
========================================================================

【真多智能体协作】

  不再是固定单链路。现在一次 /chat 会按场景派出多个角色依次发言：

    场景 v1      1v1 辅导   → [tutor]
    场景 group   小组讨论   → [tutor, peer]
    场景 debate  辩论对抗   → [tutor, peer, examiner]

  "协作"的实现方式是前文本注入——第二个 Agent 能看到第一个 Agent
  说了什么，所以是在补充而不是各答各的。实测效果：tutor 给标准答案，
  peer 补口诀和工程角度，不复读。

  场景在建课堂时选定（classrooms.scenario），建房即定，之后每轮自动按它走。

【前端要接的新东西】

  SSE 多了一个 agent 事件，收到就新开一个气泡：

    data: {"type":"agent","role":"peer","label":"学伴","stance":"质疑",
           "index":1,"total":2}

  一轮多个 Agent 就是多条 assistant 消息，用 agent_role 区分，
  不要按 role=assistant 合并。老前端忽略这个事件也能正常显示
  （文字连成一段），所以后端可以先上、前端后接。

【评估报告采集】

  python eval_report.py 直接出客观指标 + 人工评分表，零成本。
  四维人工评分（准确/相关/完整/个性化）还需要人填，见 docs/handoff/HANDOFF.md。


========================================================================
七、现在还没做 —— 别当成 bug 报
========================================================================

  · 前端还没接 M4（agent 分气泡、建房选场景），后端已全部就绪
  · 辩论场景因为延迟较长（约 14 秒一轮）暂未建议开放，待定
  · 软著申请材料里的「文本」部分还没写（源码已齐全）
  · 没有部署方案（T10）
  · 没有性能测试数据（T11）
  · 没有登录鉴权，接口不校验身份
  · LLM 调用没有超时与重试机制（协议里记为 LLM-8，已知缺口）
  · 不支持多学科（现在恒为 cs）

  另外两处和需求文档不一致，沿用旧说明：

  1) 知识库范围是【操作系统 + 计算机网络】，不是 docs/specs/REQUIREMENTS.md 第 6 节
     写的 Java（HashMap / JVM / 并发）。那两份初期文档里出现 Java 题目的
     地方，请自动替换成 OS / 计网题目，以 knowledge/ 下实际内容为准。

  2) docs/specs/REQUIREMENTS.md 第 11 节写"只生成 PPT 大纲"，实际已能生成真正的
     .pptx 文件，可以直接下载。


========================================================================
八、已知不足（已记录，不用重复报）
========================================================================

  · RAG 检索准确率：40 题集 top1 35/36、top3 36/36（已大幅改善），
    但"综合型 chunk 抢 top1"这个根因还没根治，靠混合召回 + 重排掩盖。
    根治方向是父子检索，未做。

  · 多 Agent 会变慢：v1 约 4.5s、group 约 9.3s、debate 约 14s。
    每个 Agent 一次完整 LLM 调用。演示前确认额度。

  · 限流按客户端 IP 计数：/chat 10 次每分钟，备课 5 次每分钟。
    连着跑脚本会撞 429，中间留一分钟。

  · 画像的薄弱点可能重复记录同一个误解。

  · 端到端测试依赖真实大模型，有随机性，不是严格可复现。

以上在各报告的「遗留 / 已知差距」章节有详细分析。


========================================================================
九、最后
========================================================================

有任何问题先看对应文档，文档里没写的再问。

改代码前请读 docs/specs/AGENT_PROTOCOL.md 第 8 节（变更流程），
里面列了改哪些东西必须同步改哪些文档 —— 不同步的话，
下一个人拿到的就是过时的说明。这份文件本身就因为没及时同步
而过期过一次（10-05 那版写着"不是真多智能体"，实际早已不是）。

========================================================================
```

---

<a id="lan-pair-testing"></a>

## 5、双机联调（两台电脑各有一套环境）

### 5.1 先定拓扑，这一步错了后面全白做

两台机器各连各的 MySQL 时，A 建的课堂 B 看不到、B 发的消息 A 也收不到 ——
因为「课堂 / 成员 / 消息 / 画像」全在那台机器的库里。联调要验证的恰恰是「同一群人的互动」，
所以数据必须先归一。

```
方案   谁跑什么                              数据                  适用
甲     A 跑 MySQL+后端+前端；B 只开浏览器      A 的一份             第一轮联调、演示
乙     A 跑 MySQL；两边各跑后端+前端           A 的一份（远程连）   两人都要改代码、各自重启
丙     各跑一套，各连各的库                    两份（互相看不见）   不叫联调，只能各自演示
```

方案乙有个容易忽略的代价：RAG 向量库、课堂讲义原件、上传件都落在「跑后端的那台」的本地目录。
乙方案下 B 传的讲义存在 B 的磁盘，A 的后端检索不到，回答依据会不一致。
所以第一轮建议走甲，接口都通了再拆成乙。

### 5.2 方案甲：A 当服务器，B 只开浏览器

以 A 的局域网 IP 是 192.168.1.23 为例。除最后一步外，其余都在 A 上做。

1. 查 A 的 IP：`ipconfig`，记下无线或有线网卡的 IPv4。

2. 后端改成监听所有网卡。双击 `backend/start_server_lan.bat`，等价命令：

```
cd Tutor-Chatbot-main/backend
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy ./venv/Scripts/python.exe -m uvicorn app:app --host 0.0.0.0 --port 8000
```

绑 `127.0.0.1` 时只有本机能连，B 一定连不上，这是最常见的第一个坑。
`0.0.0.0` 意味着同网段都能访问，联调结束换回 `start_server.bat`。

3. 前端不用改。本项目 Next 16.2 启动后实测已经是全网卡监听，直接就能被对方访问：

```
netstat 显示    TCP  0.0.0.0:3000  LISTENING
Next 启动日志   Network: http://10.34.36.74:3000
从局域网 IP 访问 http://10.34.36.74:3000/ -> 200
```

只有当你的 Next 版本或配置只监听 localhost 时，才需要显式指定：
`npm run dev -- -H 0.0.0.0`。判断方法就是看启动日志里有没有 Network 那一行。
另外 Next 不允许同一个目录同时跑两个 dev server，想换端口得先关掉原来那个。

4. 放行防火墙入站，管理员 PowerShell 执行一次：

```
netsh advfirewall firewall add rule name="AI-Classroom 3000" dir=in action=allow protocol=TCP localport=3000
netsh advfirewall firewall add rule name="AI-Classroom 8000" dir=in action=allow protocol=TCP localport=8000
```

5. 在 `backend/.env` 里加 CORS 来源（关键，见 5.4）：

```
ALLOWED_ORIGINS=["http://localhost:3000","http://192.168.1.23:3000"]
```

改完重启后端。

6. B 用浏览器打开 `http://192.168.1.23:3000`，在入口页点「后端地址」，填 `http://192.168.1.23:8000` 保存。

这一步不能省：前端默认后端地址是 `http://127.0.0.1:8000`，而 `127.0.0.1` 在 B 的浏览器里指 B 自己那台机器。

7. 双方各填不同昵称进入同一课堂号，就能互相看见消息。

### 5.3 方案乙：共享 A 的数据库，两边各跑后端

在甲的基础上再加三步，全部围绕 MySQL 远程访问。

1. A 的 MySQL 监听所有网卡：`my.ini` 的 `[mysqld]` 段加 `bind-address=0.0.0.0`，重启 MySQL 服务。

2. 建一个可远程登录的账号，不要直接开放 root：

```
CREATE USER 'aiclass'@'%' IDENTIFIED BY '换成你的密码';
GRANT ALL PRIVILEGES ON ai_classroom.* TO 'aiclass'@'%';
FLUSH PRIVILEGES;
```

3. B 改自己的 `backend/.env`：

```
MYSQL_HOST=192.168.1.23
MYSQL_USER=aiclass
MYSQL_PASSWORD=换成你的密码
```

4. B 起后端时同样绑 `0.0.0.0`，并且 A 的 `ALLOWED_ORIGINS` 要放行 B 的前端来源。

### 5.4 三个必踩的坑，都实测过

第一个，CORS 认的是「浏览器的来源」，不是后端地址。在 A 的本机实测（`curl -H "Origin: ..."` 看响应头）：

```
请求来源                      返回的 Access-Control-Allow-Origin
http://localhost:3000         http://localhost:3000
http://10.34.36.74:3000       无（浏览器会直接拦掉）
```

只把后端地址填对还不够，`.env` 里那个数组漏了对方的源，请求一样过不去。
症状是页面能打开、列表空白、控制台报 CORS，而后端日志显示请求正常。

第二个，前端的后端地址默认 `127.0.0.1`，在对方机器上指对方自己。
必须改成 A 的局域网 IP（入口页「后端地址」按钮），或构建时给 `NEXT_PUBLIC_BACKEND_URL`。

第三个，起后端前清代理。本机 shell 带代理时 `/chat` 会 502（`upstream connect failed`），
与联调无关但会让人误判成网络不通。

### 5.5 验收清单

按顺序做，每步都有明确的成功标志：

```
步骤          动作                                          成功标志
1  网络连通   在 B 上 ping 192.168.1.23                     有回包
2  后端可达   在 B 上访问 http://192.168.1.23:8000/health    返回 JSON，database 为 up
3  CORS 放行  在 B 的浏览器打开前端，F12 看 Network           无 CORS 报错
4  数据互通   两边各发一条消息，刷新对方页面                 都能看到
5  链路完整   问一个正经八股题，看是否多个 AI 角色回复       回复正常、依据可点开
```

第 2 步失败但第 1 步通过，通常是防火墙没放行 8000；
第 2 步通过、第 4 步空白，几乎一定是 CORS 或前端后端地址没改。

### 5.6 网络条件

两台机器要在同一个网段：同一个 Wi-Fi、同一个手机热点，或同一根交换机。
跨网段（一个连公司网、一个连热点）通常不通；学校网络还可能开了客户端隔离
（同一 Wi-Fi 下互相 ping 不通），这种情况换手机热点最省事。

### 5.7 三机拓扑：前端一台、后端一台、数据库公用

和 5.2 的区别只有三处：前端和后端不在同一台机器；数据库可能也在第三台；三台都要能互相访问。
配置的核心是两串地址，写错任何一串都会表现为「页面能打开、数据全空」。

先把角色固定下来，下面用占位名代替：

```
角色        跑什么                     示例地址
前端机      next dev                    http://192.168.1.10:3000
后端机      uvicorn + RAG 向量库        http://192.168.1.20:8000
数据库机    MySQL（也可能与后端机同机） 192.168.1.30:3306
```

前端机要做的只有一件事：把后端地址指向后端机。三种写法按优先级生效：

1. `frontend/.env.local` 里的 `NEXT_PUBLIC_BACKEND_URL=http://192.168.1.20:8000`（推荐，一次配好）；
2. 浏览器里点入口页的「后端地址」手动填（存 localStorage，优先级更高）；
3. 都不设时回落到 `http://127.0.0.1:8000`，在独立前端机上必然打不通。

```
# frontend/.env.local
NEXT_PUBLIC_BACKEND_URL=http://192.168.1.20:8000
```

这是启动期注入的，改完必须重启 `npm run dev`。实测：带这个变量构建出的产物里，
`http://127.0.0.1:8000` 出现 0 次，替换成目标地址 3 次。

后端机要做的两件事：

```
# backend/.env
MYSQL_HOST=192.168.1.30          # 数据库机的地址；同机就填 127.0.0.1
ALLOWED_ORIGINS=["http://localhost:3000","http://192.168.1.10:3000"]
```

第二行是三机拓扑里最容易漏的一行：浏览器跑在前端机上，它发请求时带的来源是
`http://192.168.1.10:3000`，后端不认这个来源就会回 CORS 错误。
本机实测对照：

```
Origin 头                      返回的 Access-Control-Allow-Origin
http://localhost:3000          有
http://10.141.46.147:3000      有（已加入白名单）
http://192.168.1.50:3000       无（未加入，浏览器会拦）
```

后端启动时要绑 `0.0.0.0`（双击 `start_server_lan.bat`），否则只有后端机自己能连。

数据库机要做的（与后端同机则跳过）：

1. MySQL 监听所有网卡。MySQL 8 默认就是 `::`（等价全网卡），可用
   `Get-NetTCPConnection -LocalPort 3306 -State Listen` 确认，看到 `::` 或 `0.0.0.0` 即可。
2. 建一个允许远程登录的账号，默认只有 `root@localhost`，远程连不上：

```
CREATE USER 'aiclass'@'%' IDENTIFIED BY '换成你的密码';
GRANT ALL PRIVILEGES ON ai_classroom.* TO 'aiclass'@'%';
FLUSH PRIVILEGES;
```

3. 放行 3306 入站。

三台都要做的：放行各自被访问的端口（前端机 3000、后端机 8000、数据库机 3306），
并且在同一网段。

多人课堂本身不需要额外配置：每台设备用不同昵称各自建身份，进同一个课堂号即可，
消息、画像、成员都是后端一处写库，所有人看到的是同一份。
唯一要注意的是「后端没有鉴权，身份存在各自浏览器里」——换浏览器等于换人。

排障顺序（哪一步断了一目了然）：

```
1  三台互 ping                    有回包        → 否则不同网段或客户端隔离
2  前端机能开 http://后端机IP:8000/health   返回 JSON → 否则防火墙或后端没绑 0.0.0.0
3  浏览器打开前端，F12 看 Network  无 CORS 报错  → 否则 ALLOWED_ORIGINS 漏了前端机的来源
4  浏览器看 localStorage 里的 backend-url  是后端机地址 → 否则 .env.local 没生效或被人手动改过
5  两个设备各发一条消息            互相可见      → 链路通
```

第 4 步单独说一句：入口页的「后端地址」一旦手填过，会以 localStorage 优先于环境变量，
之后改 `.env.local` 对这浏览器不再生效。清掉那条记录（或点面板恢复默认）才会重新读环境变量。

### 5.8 局域网 IP 会变，这是三机拓扑最隐蔽的坑

家里的路由器是 DHCP，换 Wi-Fi、重启路由器、休眠唤醒都可能换 IP。本机就实际遇到过：
上午是 `10.34.36.74`，下午变成 `10.141.46.147`。IP 一变，下面两处同时失效：

```
失效点                     现象
后端的 ALLOWED_ORIGINS    前端页面能开、数据全空、控制台报 CORS
前端的 BACKEND_URL        页面能开、连不上后端，请求指向一个不存在的地址
```

所以每次联调前先确认一次当前 IP（前端机的、后端机的各一次），再对一遍这两处配置。
想彻底避免：在路由器里给三台机器绑定固定 IP（IP 与 MAC 绑定），或者用主机名互相访问。

顺带一提，同一台机器上换 IP 后必须重启后端（`.env` 只在启动时读一次）
和前端（`NEXT_PUBLIC_` 是启动期注入的，热更新不会重新读取）。
