# M4 交接说明（A → B / C）

> **交接人**：A（后端与 AI 算法）
> **日期**：2026-10-22
> **状态**：后端 M4 全部完成，**代码未提交**（由用户提交）。提交后 B / C 拉最新再动手。
> **配套**：接口细节看 `API_CONTRACT.md`，规则编号看 `AGENT_PROTOCOL.md`
> （本次同步更新：§0 / LLM-5 / DATA-6 / DATA-7 / §3.4 / §7 / §7.5 / Prompt-7）

---

## 一、这次交付了什么

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

## 二、给 C（前端）

### 2.1 必须接的三件事

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

### 2.2 验收清单

- [ ] `v1` 房间表现与现在**完全一致**（单气泡、单次回答）——这是回归底线
- [ ] `group` 房间一次提问出现 2 个气泡，头像/颜色按 `agent_role` 区分
- [ ] 刷新页面后气泡仍在且分得开（走 `GET /classrooms/{id}/messages`）
- [ ] 建房下拉选项来自 `GET /scenarios`，后端加场景前端不用改
- [ ] 非法 `scenario` 不报 422（后端回落 `v1`），界面不崩

### 2.3 一个待拍板的事

**`debate` 场景要不要开放**。实测 `group`（2 Agent）一轮约 **9.3 秒**，`debate`（3 Agent）预估
约 **14 秒**。演示时这个等待是否可接受，请你和用户确认。
**在此之前先只接 `group`**——后端已经全支持，前端多做一个下拉项而已。

---

## 三、给 B（Prompt 工程与 RAG）

### 3.1 提示词归谁管（这条最容易改错地方）

| 想调的东西 | 改这里 | 不要改这里 |
|---|---|---|
| tutor / peer / examiner **怎么讲** | `agent/roles.py` 的 `build_role_system` | `scenarios.py` |
| 多 Agent 之间**怎么衔接**（别复读、要质疑） | `agent/scenarios.py` 的 `handoff` | `roles.py` |
| 该角色在本场景里的身份 | `scenarios.py` 的 `stance` | — |

理由（`AGENT_PROTOCOL.md` Prompt-7）：把"不要重复前面发言"写进角色提示词，
`v1` 单 Agent 场景也会被这句话污染——那里根本没有前面发言。

### 3.2 你可以直接用的两个新工具

**`rag_hits` 已经自动采集了。** 一轮 `/chat` 只检索一次、所有 Agent 共用同一份上下文，
所以"命中几条"记在 `agent_runs.rag_hits`。改完检索策略后：

```
python eval_report.py --scenario v1     # 看检索覆盖率有没有退化
```

改检索的回归仍然跑 `python smoke_rag.py --compare rag_baseline.json`（准确率），
两者互补：**准确率看检索对不对，覆盖率看有没有回答是在"裸答"**。

**调提示词别烧额度**：`GET /students/{id}/prompt-preview` 仍然可用（不打 LLM）。
注意它返回的是单角色提示词，**不含 `handoff` 和前文本**——那两段在流式时才拼。

### 3.3 评估报告的活在你这边（成果 #4 的最后一公里）

采集层我做完了，**四维人工评分要人填**：

```
python eval_report.py --classroom-id 3 --limit 50
→ eval_report.md    客观指标（轮数/每轮 Agent 数/降级率/延迟 p50·p95/检索覆盖率）
→ eval_scores.csv   人工评分表，准确/相关/完整/个性化 四列留空
```

口径（`AGENT_PROTOCOL.md` §7.5）：

- **评分粒度是"发言"不是"轮次"**：一轮 3 个 Agent = 3 行。按轮打分会把
  "tutor 讲得好、peer 在复读"折中成一个看不出问题的分——而"哪个角色在贡献价值"
  正是多智能体评估最该回答的问题。
- 建议每个场景各攒 **20 轮以上**再下结论，样本 <20 时 p95 会退化成最大值。
- **`rag_hits` 是本次才补的列**，更早的留痕记为 0。出正式报告请只用新跑的轮次。
- **跑出来是 0 先别急着重跑**，先看 `GET /evaluation/status`（或报告里的自检表）：
  留痕为空有四种成因（没配库 / 连不上 / 表缺失 / 被过滤掉），表现完全一样。
  其中"库里有数据但按课堂查不到"最常见——**不带 `classroom_id` 的 `/chat` 留下的痕
  `classroom_id` 是 NULL**，按课堂查必然漏掉。

### 3.4 别动的三条红线

1. `orchestrator.py` 的**降级契约**（异常输入一律收敛成单 Agent + `degraded` + `reason`）。
   它保证了编排层出任何问题都不会让 `/chat` 失败。
2. `scenarios.MAX_AGENTS_PER_TURN`（单轮 Agent 上限 3）。这是成本红线不是技术上限。
3. 主管**不调 LLM**。要改成 LLM 主管，先来改 `AGENT_PROTOCOL.md` §3.4——一旦主管能
   自主派角色，LLM-5 的成本约定就失效了。

---

## 四、环境：拉完代码必做一步

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

## 五、剩下的活

| 优先级 | 事项 | 谁 |
|---|---|---|
| P1 | 前端接 `agent` 事件 + 建房选场景（先只做 `group`） | **C** |
| P1 | `debate` 场景是否开放（延迟约 14s） | 用户拍板 → C 接 |
| P1 | 成果 #4：攒样本 + 填四维人工评分 + 写结论 | **B** |
| P2 | 成果 #5：**软著文本未写**（源码已提交） | 待定（非后端任务） |
| P2 | `LLM-8` 超时与重试仍未实现 | A |
| P2 | `LLM-6a` 评估节点解析失败仍无日志 | A |

**软著材料说明**：软著申请要的是「源代码文档 + 设计说明书/用户手册」这类文本材料，
现有报告（`PROJECT_REPORT.md` / `STAGE_REPORT_T8_T9.md` / `STAGE_REPORT_M1_M3.md` /
`TEST_REPORT.md`）是很好的素材来源，但需要按软著格式重写。这不是代码任务，
需要有人专门排时间。
