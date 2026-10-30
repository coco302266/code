# RAG 知识库与检索手册（现状 + 口径 + 维护）

> **owner**：B（Prompt / RAG 方向）｜ **更新**：2026-10-26（新增 §1.2 知识库边界与补库纪律）
> 本文件合并自 5 份已落地的 RAG 提案，只保留**长期有效**的内容（现状、口径、约束、命令）。
> 历史过程与踩坑细节看 `docs/worklogs/B_WORKLOG.md`，本文不重复。

**来源映射**（原文件已删除，编号引用按此对照）：

| 原文件 | 状态 | 并入本文 |
|---|---|---|
| `docs/proposals/PROPOSALS.md` | 结论已全部落地（重排/混合/质量闭环） | §1 §2 |
| `docs/proposals/PROPOSALS.md` | 已落地（trace / sources 事件 / 指纹） | §6 |
| `docs/proposals/PROPOSALS.md` | 已落地（评测集 40→75→83 条） | §4 |
| `PROPOSAL_RAG_QUALITY_EVAL.md` | 七个 Wave 全部落地 | §3 §5 §7 §8 |
| `docs/proposals/PROPOSALS.md` | XB-3 / XB-4 已落地（详见 B_WORKLOG） | §6 注 |
| `docs/proposals/PROPOSALS.md` | **未触发，保留原件**（项目根） | §9 指针 |

---

## 1. 检索链路现状

```
query（/chat：评估节点抽 topic，追问自动拼接）
  → 文档路由 L1：BM25(每篇 top3 片求和) + 0.3×向量 RRF 按 source 聚合，取 top3 篇
  → 向量粗召回 k=6（bge-small-zh-v1.5，filter={"source": {"$in": [3 篇]}}）
  → BM25(jieba) k=4（**同一收窄范围**，否则会把排除掉的文档塞回来）
  → RRF 融合（k=60）截回 6
  → 父子模式：按 parent_id 聚合去重（child 召回 → parent 注入）
  → bge-reranker-v2-m3-int8 重排（batch=1，线性成本；**打 child 文本**，见 §1.1）
  → 相关性下限 relevance_floor（整题判定：最高分 < -2.0 → 全部不注入）
  → 逐条下限 per_doc_floor（单条 < -5.0 砍掉，-5.0~-2.0 的灰区保留）
  → top3 去重 → format 注入（父子模式 parent_final_k=3；扁平模式 final_k=4）
```

> 2026-10-26 更正：本节原写「最高分 < -1.0」，与实际配置不符。代码
> `RetrievalConfig.relevance_floor = -2.0`（2026-10-24 从 -1.0 放宽），
> 且流程图漏掉了 2026-10-25 新增的 `per_doc_floor`。照旧文档调参会调错档位。

> 2026-10-26 XB-26：链路最前面新增**文档路由（L1）**，见 §1.1。路由失败/关闭
> 时整条链路与原来逐字一致（`RAG_DOC_ROUTER=0` 可一键回退）。

### 1.1 文档路由（L1，2026-10-26 落地）

`agent/rag/doc_router.py`。**先选文档、再选切片**，解决「正确文档的切片必须先在
248 片里挤进前 6」这个赌召回的问题。

- **信号**：切片级证据按 `source` 聚合——每篇取 BM25 最高的 3 片求和（消除文档
  长度偏置）+ 0.3×向量 RRF（字面零命中时的语义兜底）。实测 route@1 **67/71**、
  route@3 **71/71**。
- **不建独立索引**：摘要从主库切片 metadata 现算，不存在「改库忘重建」。
- **为什么不按 category 过滤**：只有 4 个值、OS 一类占一半切片，粒度太粗。
- **回退**：过滤后召回为空 → 退回全库并记 `degraded=router_fallback`，不零资料。
- **trace**：`router` / `router_docs` / `router_scores` / `router_applied` / `router_ms`。

**收益要看清口径**：完整链路 A/B（71 题）top1 两边都是 68/71、注入总数都是 204、
逐例零变化；只有库外误注入 2→1（Java HashMap 那例收窄后最高分 -5.39 被 floor 拒掉）。
原因是交叉编码器逐对独立打分，换候选池不改单条分数。所以**当前库规模下这是质量
中性、结构更稳的一层**，收益随库变大才显现（候选池恒 6 条，不再随库稀释）。

**域外闸门 `RAG_ROUTER_GATE` 默认关闭**，实测无安全阈值：库外题「Python 里列表和
元组」的 `python`(df=2)/`列表`(df=1)/`元组`(df=1) 在库里都被顺带提到过，覆盖率
0.397；而带 topic 的口语追问只有 0.299，两者仅差 0.098。**这不是「库太小」，补库
解决不了、方向还是反的**（补一份 Python 文档会让 `列表/元组` 的 df 1→2、IDF 权重
下降，覆盖率更低，详见 §1.2）。真正能动的只有换判别力更强的重排/分类模型。
复核用 `scripts/calibrate_doc_router.py`。

参数集中在 `agent/rag/retriever.py:RetrievalConfig`（`RAG_RERANK` / `RAG_HYBRID` /
`RAG_RELEVANCE_FLOOR` 等环境变量可调）。扩库**不要**加大粗召回：实测 366 片模拟下
k=6/12/24 的 top1 完全相同、延迟从 2.0s 涨到 6.8s，正确做法是缩小候选池（文档路由）。

**重排打 child 文本（`RAG_RERANK_TEXT=child`，2026-10-27 起默认）**。父子模式聚合后
正文是整节（600~900 字），交叉编码器在本机是无 GPU 的 ONNX，成本随序列长度走——
实测重排占检索端到端 **97.7%**（`scripts/profile_rag_pipeline.py`）。改成用命中的那片
child（~200 字）打分后：重排 mean 9.22s→4.63s（−50%，反向顺序复测 −41.7%，
非顺序效应）、检索 p95 13.789s→6.180s、top1 59/61→60/61、库内零注入 0→0；
代价是 **nDCG@4 0.8818→0.8549（−3.0%）**、注入相关率 −1.7pp、重排提升度
+0.0657→+0.0388（短文本判别力本就弱些）。**注入给模型的始终是 parent 整节**，
改的只是喂给交叉编码器的输入。回退：`RAG_RERANK_TEXT=parent`。
A/B 用 `scripts/ab_rerank_text.py`，L1 口径用 `scripts/smoke_rag.py --uplift`。
完整调优方案见 `docs/plans/PLANS.md`。

**注入多样性：U9 已实测否决，别再拉多样性（2026-10-26）**。父子模式去重只按
`parent_id`，同一篇文档的好几个 `##` 小节可以占满全部 3 个注入位——83 条评测用例里
**67 条**如此。听起来像浪费注入预算，但 A/B 证明**强行摊平是净亏损**：
2:1 组合把同源堆叠从 67 压到 37、平均来源 1.458→2.072 篇，代价是
**注入相关率 78.6%→56.2%**（每注入 2 条里有近 1 条金标准认为不相关）、
p95 3.888s→4.423s、9 例从 3 条掉成 2 条；top1 一分未涨。
根因是**本库的问题与文档基本一一对应**：「B+ 树索引为什么快」就该给
`db/index-transaction.md` 的三个小节，拉来的 `dsa/tree-heap.md` /
`db/sql-query-join.md` 是噪音。相关率是对错的判据，多样性不是。
开关保留但**默认关闭**（`RAG_SOURCE_QUOTA` / `RAG_MAX_PER_SOURCE`），
A/B 用 `scripts/ab_source_quota.py` 可复现。

### 1.2 知识库边界与「补库」纪律（2026-10-26）

**边界由教学计划定义，不由「用户可能问什么」定义。** 当前边界 = `knowledge/cs/`
下 4 门课实际讲授的 **20 个小节**（db / dsa / network / os）。因为分母不存在，
「覆盖率」不能当 KPI 追——它只反映「老师讲了多少」，那是课程设计决策，不是检索指标。
按「检查发现没覆盖 → 补文档」做，每个新提问都会变成一条待办，永不收敛。

四类缺口，只有第一类该动文档：

| 类型 | 现象 | 正确动作 | 成本量级 |
|---|---|---|---|
| 漏采 | 大纲内小节确实没进库 | 补文档（走 `knowledge/cs/` → 增量入库） | 一次性 + 全量回归 |
| 超纲 | 学生在问课程没讲的内容（缺口日志里的 Redis 持久化、CAP 定理） | 不改库，靠域外判别挡掉误注入 | O(1)，**不随题库增长** |
| 召回失败 | 文档在库里，检索没命中 | 修召回/路由，**不碰文档** | 一次改动 |
| 排序被压 | 在候选池里但 top1 不是它 | 换重排模型 / 加训练 | 一次改动 |

**为什么不做「覆盖率补库」**（三条实测依据）：

1. **边际收益已到顶**：库内 71 题 route@3 **71/71**、top1 **68/71**。剩 3 条不是
   缺知识，是重排顺序被干扰项压过（问「快排时间复杂度」，`dsa/sorting.md` 排第 3
   输给 `os/scheduling.md`）——文档全程在候选池里，**加文档修不了**。
2. **对域外误注入是反向的**：闸门判据是 IDF 加权覆盖率，补进含该词的文档会抬高
   df、压低该词权重，域外题反而更不像域外。
3. **每次补库都要付固定成本**：切片/嵌入/BM25 重建 → 指纹变化 → L1 金标准与 L4
   分母必须重刷。`kind=oos` 的 4 条已有 2 条因扩库过期（§3）。补一份的成本是
   「重建 + 重跑评测 + 重刷金标准」，收益可能是 0。

**补库三条硬触发（全满足才动，缺一不补）**：

1. 该知识点**在教学计划内**（教师/大纲确认要讲，不是「学生问过」就算）；
2. `backend/logs/kb_gap_events.jsonl` 里同类提问 **≥3 次**（单次是噪声）；
3. 现有 20 篇检索确认无内容（`top_score` 低于 floor，且人工复核确实没覆盖）。

**别忽略的反向代价**：把超纲内容补进共享库，会造成**教学不一致**——系统按教材口径
回答老师课上没讲的内容，学生拿去用。这与「教材注入排在教学讲义之前会覆盖教师口径」
是同一类问题、方向相反。

**下一步方向是域外判别，不是补库**：现状能动的只有重排/分类模型的判别力。注意
XB-14 已拍板——检索为空时**不对学生说「没有资料」**，只写缺口事件，所以域外判别的
落点是把误注入降到 0，不是加拒答话术。补库仅在三条硬触发全满足时执行。

## 2. 库结构（两套向量库，刻意隔离）

| | 常驻知识库 | 学生上传 PDF | 教师上传 PDF |
|---|---|---|---|
| 数据源 | 项目根 `knowledge/cs/` 下 **20 篇** Markdown（**248 child / 81 parent**） | `POST /upload` 临时文件，索引后即删 | `POST /upload/teacher`，同上 |
| Chroma | collection `cs_basics`，落盘 `backend/chroma_kb/` | `student_upload_{session_id}`，**纯内存** | `teacher_upload_{session_id}`，**纯内存** |
| 生命周期 | 常驻，build_kb.py 幂等重建 | 会话级，TTL 清理 + 容量淘汰 | 同左 |
| 切片 | 父子：child 200/40 检索 + parent=`##` 小节注入；`--parent-child` 建库 | 900/150 | 900/150 |
| 检索 | 完整链路（含父聚合 + 重排 + floor） | 纯向量 | 纯向量 |
| 权限 | — | 无（限流 3/小时） | `_require_teacher()`（限流 10/小时） |

不合并的理由：策展内容 vs 私有材料，隐私与生命周期都不同。
扩库触发条件（**>15 篇或 >150 片**）已于 2026-10-25 达到（19 篇 / 235 child），
父子检索因此从 opt-in 转为生产默认，见 §5。

### 教师上传备课素材（XB-21 方案 A，2026-10-26 落地）

`POST /upload/teacher` 与学生 `/upload` **共用 `_index_upload()`**，差别只有三处：
① 走 `_require_teacher()`（非本课堂教师 403、成员角色是 student 也 403）；
② **独立限流桶** 10/小时，不和学生共用 3/小时那个桶，否则教师会被学生流量挤掉；
③ 会话记 `teacher_id` / `classroom_id` 供审计。

**语义边界（务必看清）**：建的是**会话级内存库**，只给上传者这一次备课用——
**不进常驻知识库，也不会被学生问答检索到**。实测：上传前后 `chroma_kb` 里始终
只有 `cs_basics` 一个 collection，248 片 / 指纹 `bbe0cdc4` 不变。

若要做「教师资料长期生效、学生也能检索到」，那是**方案 B**（课堂级 collection
`classroom_{id}_docs` + 与共享库并行召回）——**已于 2026-10-26 落地为 XB-23**，
见下一节。为什么不直接写进共享库见 §9。

### 课堂资料长期化（XB-23 方案 B，2026-10-26 落地）

教师上传件按**语义**分三条路，不要再混成一个接口行为：

| 语义 | 落法 | 生效范围 | 生命周期 | 改基线 |
|---|---|---|---|---|
| 备课素材（用后即弃） | `POST /upload/teacher`，`persist=session`（**默认**） | 仅上传者本次 | 1h TTL / 进程重启消失 | 否 |
| **课程资料（长期）** | 同上，`persist=classroom` | **本课堂成员** | 跟随 `Classroom` 行 | 否 |
| 全局内容 | 落 md → 审核 → `update_kb.py` → 热重载 | 全平台 | 常驻 | **是** |

`persist=classroom` 的三层存储（缺一层就会踩坑）：

| 层 | 位置 | 作用 | 能否重建 |
|---|---|---|---|
| 原件（事实源） | `backend/classroom_docs/{classroom_id}/{doc_id}_{安全文件名}.pdf` | 换 embedding / 改切片参数 / 迁移时靠它重建 | 真相本身 |
| 登记 | MySQL `classroom_documents`（`init_db.py` 建表，服务启动时 `create_all` 兜底） | 去重（`doc_hash`）、配额统计、软删审计 | 可重建 |
| 派生索引 | `backend/chroma_classrooms/classroom_{id}_docs` | 检索用，**与 `chroma_kb` 物理分开** | **可以**（从原件重切重嵌） |

运维命令（`backend/` 下，venv）：

```bash
# 教师上传长期资料（persist=classroom；不传走会话级）
curl -X POST http://127.0.0.1:8000/upload/teacher \
  -F "file=@讲义.pdf" -F "teacher_id=1" -F "classroom_id=1" -F "persist=classroom"

# 清单 / 删除（都要 teacher_id，非本课堂教师 403）
curl "http://127.0.0.1:8000/classrooms/1/documents?teacher_id=1"
curl -X DELETE "http://127.0.0.1:8000/classrooms/1/documents/<doc_id>?teacher_id=1"

# 孤儿清理（课堂被删后磁盘/向量残留）；默认 dry-run
./venv/Scripts/python.exe scripts/purge_classroom_docs.py [--apply]
```

关键口径（都实测过）：

- **上传后学生立刻能检索到**：同进程内 chromadb 按路径共享 System，写入对已开句柄
  即时可见（探针实测）。**跨进程**写入（脚本直接改库）仍要重启服务或热重载。
- **`/chat` 三路注入顺序**：上传件 → 教材（kb）→ 课堂资料。教材在前，避免教师
  讲义覆盖教材造成教学不一致。
- **课堂资料走纯向量 + 距离门限**（`retrieve_classroom_docs`），不进共享库那条重排链路：
  单库签名要调两次重排（成本×2 击穿 p95）、`_BM25_CACHE` 按 collection 建键会
  逐课堂冷启动、`relevance_floor` 是按**重排 logits** 定的（-2.0），课堂库没有 logits，
  共用会张冠李戴。升级触发条件：单课堂 >2000 片或命中率不足。
- **★ 距离门限是必须的（XB-24，2026-10-26 实测补的）**：原先纯 top-k 无过滤，
  学生问「今天天气怎么样？」也会硬塞 3 片课文进上下文。现在按向量距离卡一道
  （`CLASSROOM_MAX_DISTANCE`，默认 **1.25**，设 `off` 关闭）：
  最高距离都不达标就**整题不注入**（与共享库「整题判定」同口径，不做逐片裁剪）。
  实测分布（Python 教材 187 片）：同类问句 0.72~0.98，离题问句 1.19~1.48 ——
  **有重叠**，「红烧肉」1.1887 比同类以外的「快速重传」1.2038 还近。
  所以这是**粗筛不是判据**：实测 5 个问句里挡掉天气类，红烧肉/电影类仍会进 2 片
  （模型按 grounding 规则忽略，回答正确）。真实课堂语料进来后要重标阈值。
  为什么不改用重排：本机 3 片重排实测 **4.0s**（同类 -0.86 / 离题 ≤ -7.0，能分开），
  每轮都跑直接击穿 p95，不划算。
- **`chunk_count` 必须按 doc_id 数（XB-24 修的）**：一个课堂一个 collection，
  用 `count()` 会把上一份文件的片一起数进去——实测 3 页空白 PDF 报「188 片」，
  教师以为传成功了。现在 `document_metrics(store, doc_id)` 返回 (本文件片数, 正文片数)。
- **抽不出正文的 PDF 一律 422 拒绝（XB-24 修的）**：扫描件/纯图片页只会产生一片
  空的 `front_matter`，入库「成功」但永远检索不到——教师看到 200 就走人，
  学生问什么都命不中。现在正文切片数 = 0 就回滚（删切片 + 删原件 + 一份不剩再清库）
  并返回「no extractable text」。
- **教师端有上传入口了（XB-24）**：`/teacher` 的「备课」页签顶部是「课堂资料」卡片
  （上传 / 清单 / 单个删除）。在此之前后端接口虽然存在，界面上**一个入口都没有**，
  功能对真实教师不可达。注意前端 `SourceItem.origin` 也已加 `classroom`——
  漏了会被 `normalizeSources` 归一成 `kb`，学生看到的依据徽章就标错来源。
- **检索失败一律降级为空**（不抛），课堂库是增强项，坏了不许让 /chat 500。
- **删到一份不剩会自动清空 collection**（避免每届留一个 0 切片空库）；登记行保留软删。
- **配额**：`CLASSROOM_MAX_FILES`（默认 20）/ `CLASSROOM_MAX_BYTES`（默认 200MB），
  超限 413；同课堂同内容重复上传 409 幂等返回已有 `doc_id`。
  ⚠ 上传限流 **10 次/小时**（按 IP），比 20 份配额更紧——教师一小时内填不满配额，
  要一次传满得等下一小时。限流桶在进程内存，重启服务即复位。
- **L1 基线零影响**：物理隔离，`chroma_kb` 始终只有 `cs_basics`（实测 248 片 /
  `bbe0cdc4` 不变）。L3/L4 评测每次新建课堂，天然没有课堂资料。

## 3. 评测体系（四层 + 门禁现状）

| 层 | 内容 | 工具 | 门禁 |
|---|---|---|---|
| L1 检索 | top1/top3、Recall@k、Precision@k、MRR、nDCG@k、p95（**k=实际注入条数**：扁平 4 / 父子 3） | `scripts/smoke_rag.py` | p95 **≤3s（统一，不按粒度分档）**；recall/precision 不设门禁（金标准是整节派生，0 分片不进分母，跨粒度不可直比，看 nDCG） |
| L2 重排 | 提升度（nDCG 有重排 − 无重排） | 同上 | **必须 > 0** |
| L3 生成 | 幻觉率、矛盾率、资料利用率、空资料声明率 | `eval/grounding_eval.py` | 幻觉率 ≤0.15；声明率已降级为信息性指标（XB-14，2026-10-24 用户决策：空资料不再告知学生，缺口走日志） |
| L4 端到端 | 真实 /chat SSE + 级联兜底 + 资料利用率 | `eval/e2e_runner.py` | 不进 CI，周跑 |

统一入口：`python -m eval.e2e_runner --layers ...`（没跑的层在报告里必须是 `None`，
不能填占位 dict）。`run_tests.py` 为全量单测回归（当前 **529**）。

**p95 预算：曾短暂分档，现已统一回 3s（2026-10-27 定稿）**

- **分档的由来（2026-10-25）**：父子模式注入整节 `##`（平均 1000+ 字，最长 1994），
  重排耗时随文本长度线性涨，实测 **p95 4.29~4.37s / 均值 2.58s**（`--only full`
  跑两次一致，非负载噪声）。原「≤3s」是扁平 200 字片时代的数字，照抄会把已上生产的
  父子模式判成永久不达标，故按粒度分档：**父子 ≤5s、扁平 ≤3s**。
- **分档失效的原因（2026-10-27）**：分档的前提是「重排打 parent 全文」。
  该前提已被 `RAG_RERANK_TEXT=child_title`（XB-29）消除——现在重排读的是
  「命中片 + 小节标题」（~250 字），父子与扁平的重排成本回到同量级，
  **分档不再有存在理由**，统一回 3.0s（代码 `eval/e2e_runner.py:GATE_L1_P95`）。
- **三档同机对照**（75 题 / 计分 71，各档独立进程串行，`scripts/l1_p95_by_mode.py`）：

  | 档位 | top1 | top3 | nDCG@4 | 重排提升度 | 平均 | p95 | 3.0s 门禁 |
  |---|---|---|---|---|---|---|---|
  | `parent`（整节全文） | 68/71 | 70/71 | 0.8818 | +0.0657 | 2.384s | 3.655s | ✗ 超 0.655s |
  | `child`（命中片） | 68/71 | 70/71 | 0.8549 | +0.0388 | 0.963s | 1.103s | ✓ |
  | `child_title`（片+标题，默认） | 68/71 | 70/71 | **0.8651** | **+0.0490** | 0.973s | **1.134s** | ✓ |

- ⚠ **回退 `RAG_RERANK_TEXT=parent` 会让 p95 回到约 3.65s 并判红**——那是预期内的：
  5s 分档本就是为 parent 全文打分才设的。回退前先看上面这张表。

**L3/L4 不依赖金标准坐标**：`grounding_eval.py` 不用 `relevant_chunks`、
L4 只读 SSE 的 sources，所以**换切片粒度**只需重刷 L1（含 L2），L3/L4 基线不必重跑。

**但换库或换 floor 必须重跑 L3/L4**：粒度只影响金标准坐标，而知识库篇数、切片内容、
`relevance_floor/-2.0` 直接改变「注入了哪些资料」，L3 的利用率 / 空资料声明率与
L4 的库外 0 注入率都会跟着变。判据看报告里的 `index_fingerprint` 是否等于
`eval/baseline_meta.json` 的当前指纹——不等就是旧库数据，不可与新库并列引用。

**L4 的资料利用率口径（XB-16）**：只对 `sources>0` 的条判（库外题 `sources=0`
记 `None`，**不填 0**）；SSE 的 sources 事件不带正文，切片由本地用同一 query +
`state.topic` 重跑一次检索取得（近似重建）；判定函数与 L3 共用
`eval.grounding_eval.judge_utilization`，**只有一份口径**，两边数值可比。
需答案正文，故 `gen_eval_samples.post_chat` 已返回 `text` 字段。

**⚠ 已知失真：`kind=oos` 题集会随扩库过期**（2026-10-25 发现）。库外题的定义是
「知识库里没有」，而库会变——`oos` 的 4 条里现在有 2 条已被扩库覆盖：
「MySQL 索引为什么用 B+ 树」（`db/index-transaction.md`）、「Java HashMap 线程安全吗」
（`dsa/hash-table.md`），实测 `sources=3 / 1`。这**不是误召回**，是命中新文档。
所以：**任何「库外 0 注入率」读数前，先确认 oos 题在当前库里真的还是库外**
（跑一次检索看有没有 sources）。已滞后未改判，改 `kind` 会重算 L1 金标准与 L4 分母，
须单独一轮。

## 4. 评测集与金标准

- `backend/rag_eval_cases.json` 共 **83 条**：in-kb 26 / kb-interview 35 / follow-up 8 /
  oos 4 / cross 2 / cascade 8；`cascade` 被 `load_cases` 过滤不进 L1 计分（75 条口径）。
  **加新 kind 必须同步过滤，否则口径悄悄变。**
- **为什么不用公开数据集**：公开数据集的 qrels 指向它自己的语料，对本库只有 query 侧
  可用；且本机 HF 全部超时。评测集只能自建（结论实测于 2026-10-19，仍有效）。
- 金标准演进：topic 派生（全集 gain=1）→ **LLM 三票细化**（gain 1/0.5/0，平票取保守
  低档，`eval/refine_gold.py`）→ **人工复核**（14 条低置信已复核，改 6 处）。
  `gold_source: topic-derived+llm-refined`；重跑 `--apply` 会跳过已升级用例，
  **人工复核结果不会被覆盖**；确实要重标时用 `--force`。
- **金标准粒度必须等于检索返回粒度**（2026-10-25，换 parent 粒度时踩到的静默坑）：
  `metrics._chunk_key` 是 `(source, start_index)`，而父子里聚合后的 parent 把
  `start_index` 换成了 `parent_start`——两套坐标。金标准按 child 派生的话，
  正确命中也匹配不上，Recall/Precision 会**静默归零且不报错**。
  `eval/build_gold.py` 现按库 metadata（有无 `parent_id`）自动判粒度，
  并把 `gold_granularity` + `gold_index_fingerprint` 写进评测集：
  库重建/换粒度后指纹对不上，就该重刷。显式指定与库不符的粒度会直接非 0 退出。
- **换粒度后 gain 三档必须重标**（旧 gain 是按旧粒度的切片内容判的，留着等于错配），
  重标后 nDCG 与历史值不可直比；top1/top3 走 topic 匹配，跨粒度仍可比。
- 指标口径（改任何一处必须同步）：
  - 引用分数必须带题集：`7/7(9题)` < `35/36(40题)` < `68/71(75题)` 不可比；
  - topic 匹配归一化统一 `s.replace(" ", "").lower()`（build_gold / smoke_rag /
    metrics / chunk_quality / kb_gap 同一把尺子，**不自造第二套**）；
    `metrics._topic_match` 已改为归一化子串（原先精确匹配，`tcp 与 udp` 那批题
    永远拿不到 0.5 档，与 top1/top3 判定自相矛盾）；
  - nDCG ∈[0,1]，无标注时分母 =「k 位全命中」；降级请求耗时不进 p95（`eval/slo_check.py`）。

## 5. 切片策略与质量评估

- 策略：`RecursiveCharacterTextSplitter`，chunk_size **500** / overlap **80**，
  分隔符优先级 `## → ### → 空行 → 换行 → 。→ ；`；metadata 带 topic/category/source/start_index。
- **父子检索（已上生产，2026-10-25）**：`RAG_PARENT_CHILD=1` 时改用
  「child ≈200 字检索 + parent=完整 `##` 小节注入」，建库走
  `python build_kb.py --parent-child`，检索侧在 RRF 后按 `parent_id` 聚合去重、
  注入窗口 `parent_final_k`=3（`RAG_PARENT_FINAL_K`）。当前生产库即父子结构
  （19 篇 → **235 child / 77 parent**），8000 服务带该开关启动。
  代价：父块 600~900 字，同题重排约 ×1.7（1.33s→2.22s）。
  **切回扁平**：不带开关重启 + 用 `backup_chroma_flat_*` 的库与基线还原。
- 一致性约束（改检索必查）：`_rerank_docs` 里「候选 ≤ top_n 就跳过重排」只在
  **两级 floor 都关**时才允许——父子聚合常把 6 个 child 收成 3 个 parent，
  正好等于窗口，若仍跳过就等于没有评分、**两级 floor 双双失效** ⇒
  库外题重新被硬塞资料（2026-10-25 实测复现并修复）。
- 质量评估：`python -m eval.chunk_quality`（零 LLM，重建库后跑一次），三指标——
  **语义独立性**（跨/同主题距离比，>1 好）、**综合度**（≥3 个主题的 top3 = 万金油片）、
  **完整度**（标题悬空 / 句中截断 / 句读密度）。
  **诊断粒度跟随库形态**（`--granularity auto|child|parent`）：父子库默认诊断
  聚合后的 parent（生产注入单元），parent 向量为**重新 embed**（取 child 均值会
  系统性抬高独立性）；parent 下「标题悬空」结构上不适用，改看 `empty_body`。
  报告文件名带粒度，两种口径不会互相覆盖。
- 最新诊断（2026-10-25，指纹 `2db8513a…`，19 篇 / 235 child / 77 parent）：
  - **parent 粒度（生产注入单元）**：独立性中位 **1.747**（<1 的 **0** 片）、
    综合型 **10** 片、空正文 0、句中截断 0、偏移不可信 **0**。
  - child 粒度（对照）：独立性中位 1.295（<1 的 4 片）、综合型 5 片、偏移不可信 28
    ——那 28 是 `_merge_short_chunks` 合并块时补的人为换行导致源文件对不齐，
    **不是源文件偏移错了**，读 child 报告时别当成缺陷。
  - 为什么两个粒度都要看：parent 回答「注入的东西干净吗」，child 回答
    「检索粒度会不会把隔壁主题吸过来」，两者结论不能互相替代。

## 6. 可观测点位（改检索必看）

- **trace**：每轮 /chat 检索写 `kb_trace`（候选数、拼 topic 与否、重排分支、耗时、
  `relevance_top_score` / `relevance_filtered`）；
- **sources 事件**：SSE 流里 `{"type":"sources","items":[...]}` 上报本轮实际注入了哪几片
  ——检索静默失败从回答看不出来，只有这里能看到「系统喂了什么」；
- **降级 WARNING**：重排不可用/报错走向量排序时打 stderr（不依赖 logging 配置）；
- **缺口事件**：`kb_docs` 为空时写 `backend/logs/kb_gap_events.jsonl`
  （`RAG_KB_GAP_LOG` 可覆盖），报表 `python -m eval.kb_gap_report`；
- **索引指纹**：重建库后写 `eval/baseline_meta.json`，`smoke_rag --compare` 指纹不一致
  拒绝比对（防「库换了、基线没更新」的假阴性）。

## 7. 硬约束（违反会返工）

- 相关性过滤**只允许整题判定**——逐片裁剪实测误伤 follow-up（71 题退化 1 条）；
- 改产品行为 / 提示词措辞必须**同步评测判定端与门禁**（XB-10 / XB-13 / XB-14 三次验证）；
- 不改 `agent/prompts.py` 三个生产 prompt、不改 `agent/state.py:HINT_STRATEGIES`、不改 SSE 协议；
- **两层相关性下限，别混为一谈**（2026-10-25）：
  - `relevance_floor`（**-2.0**，2026-10-25 从 -1.0 下调）= **整题级**：最高分都不过线 → 整题不注入。
    挡的是「库外题被硬塞 4 条不相关资料」（D11）。
    下调依据：扩到 19 篇后 reranker 对「X 的时间复杂度是多少」这类具体事实问法
    即便命中正确文档也只给 **-1.4~-1.5**（快排 / 哈希表被 -1.0 误杀），
    而真负例最近是 git 回退 **-2.87**，-2.0 两侧各留余量。
  - `per_doc_floor`（-5.0）= **逐条级**：整题放行后再砍尾部明显不相关的片。
    挡的是「库内题被塞进别的主题的片」（问 TIME_WAIT 却注入 SJF/RR 时间片）。
    只有第一层时，高分 top1 会带着 -5 分的垃圾一起注入。两者可分别用
    `RAG_RELEVANCE_FLOOR=off` / `RAG_PER_DOC_FLOOR=off` 关闭。
  - 父子粒度下 `per_doc_floor` 几乎不触发（聚合后常 ≤3 条），实测 -5.0 只砍
    13 片真垃圾 / 误伤 1 片（`diag_perdoc_floor.py`），已成纯安全网。
- **调阈值必须拿切片级金标准量，不能拍**：`python scripts/diag_perdoc_floor.py`
  一次给出各阈值的 Recall/Precision/砍掉真垃圾/砍掉真误杀四列。
  该脚本已按 `CONFIG.parent_child` 先聚合再打分（否则单元与生产注入单元不同，
  parent 金标准全对不上，`hit_kept` 恒 0）。相对截断（top1 - margin）实测劣于
  绝对阈值（-4.0 起误杀陡增），别再试。
- chroma `get(include=["embeddings"])` 返回 ndarray，写 `or []` 抛歧义 ValueError；
- **BM25 分词必须大小写归一**（`retriever._tokenize` 已加 `.lower()`，2026-10-25 修）：
  文档写 `HTTPS`、评估节点吐 `https`，不归一则整路 BM25 一个 token 都匹配不上；
- **检索为空的第一怀疑对象应该是「库里有没有这个内容」，不是检索链路**：
  2026-10-25 追一条「HTTPS 性能开销」追了 5 个方案（拼接表达 / 扩大融合池 / topic 对齐 /
  query rewriting / 放宽 floor）全部无效，最后发现 `http-https.md` 根本没写这块内容——
  前面的功夫全是空转。**先 grep `knowledge/` 再动手调检索**（诊断工具：
  `scripts/diag_followup_topic.py` 真实 /chat、`scripts/diag_floor_scores.py` 看分数与候选池）；
- **省略式追问的失败要先查粗召回，别只盯重排/floor**：口语化 topic 拼接后若
  向量被稀释 + RRF 截断，正确片压根进不了候选池，重排再准也救不回来；
- `smoke_rag.configure()` 改模块级全局 CONFIG，跑完 hybrid 要恢复 full；
- 评测集 expected 写完整 topic 名；`cmd | tail` 后 `$?` 是 tail 的退出码；
- 起服务前 unset 代理（否则 /chat 502）；本机不要 pip install（必失败，环境整体搬运）。

## 8. 维护命令速查（backend/ 下，venv python）

```
./venv/Scripts/python.exe run_tests.py                        # 全量单测
./venv/Scripts/python.exe build_kb.py                         # 重建知识库（幂等）
./venv/Scripts/python.exe build_kb.py --parent-child          # 重建父子库（生产形态）
./venv/Scripts/python.exe scripts/update_kb.py                # 增量更新（日常首选，见 §8 方案）
./venv/Scripts/python.exe scripts/update_kb.py --dry-run       # 只看会改哪些文档，不动库
./venv/Scripts/python.exe scripts/verify_incremental.py       # 增量 ≡ 全量？改切片逻辑后必跑
./venv/Scripts/python.exe scripts/smoke_rag.py --compare      # 检索回归对比（带指纹）
./venv/Scripts/python.exe scripts/smoke_rag.py --save-base rag_baseline.json   # 刷新基线
./venv/Scripts/python.exe -m eval.e2e_runner --layers L1,L2   # L1/L2 评测 + 门禁
./venv/Scripts/python.exe -m eval.build_gold --dry-run        # 金标准派生（先看统计与粒度）
./venv/Scripts/python.exe -m eval.refine_gold --apply --force # gain 三档重标（换粒度后必跑）
./venv/Scripts/python.exe -m eval.chunk_quality               # 切片质量诊断（粒度自动）
./venv/Scripts/python.exe -m eval.chunk_quality --granularity child   # 对照：库内切片
./venv/Scripts/python.exe -m eval.refine_gold --report        # 低置信标注清单
./venv/Scripts/python.exe -m eval.kb_gap_report               # 知识库缺口报表
./venv/Scripts/python.exe scripts/diag_perdoc_floor.py        # 逐条下限校准（父子口径）
./venv/Scripts/python.exe scripts/diag_followup_topic.py      # 追问 topic 抽取诊断（真实 /chat）
./venv/Scripts/python.exe scripts/diag_floor_scores.py        # 重排分数 + 粗召回候选池诊断
./venv/Scripts/python.exe scripts/diag_floor_margin.py        # floor 边缘盘点 + 注入多样性（退出码 1 = 有库内题被拒成 0 注入）
./venv/Scripts/python.exe scripts/ab_source_quota.py          # per-source 配额 A/B（top1 / 相关率 / 多样性 / p95）
```

### 重建库的触发条件与「必须重启」的原因

**运行时只读 `backend/chroma_kb/`，从不读 `knowledge/`**——`load_knowledge_index()` 只接
`persist_directory`，md 是构建期输入。所以改了 md 不重建，新内容**一条都不会被检索到**，
而且不报错。也没有任何自动检测：`build_kb.py` 是手动脚本，服务启动时也不比对 md 与库。

**要重建**：`knowledge/` 增删改 md（改一个字也算）、改 `# 一级标题`（topic 变）、
移目录（category 变）、切 `RAG_PARENT_CHILD`、换 `EMBED_MODEL`、改切片参数。
**不用重建**：对话里上传 PDF（走 `/upload`，独立 `student_upload_<uuid>` collection，
当场索引、会话级、1h 过期）、改提示词/文案、改检索侧运行参数（k / floor / rerank 开关）。

**重建后必须重启服务，否则 `/chat` 直接 500**（2026-10-26 隔离实验实测）：
`get_knowledge_store()` 带 `@lru_cache(maxsize=1)`，进程内只 load 一次、永不刷新；
`build_kb.py` 的 `drop_collection` 删掉同名 collection 后，常驻进程手里的旧句柄变成悬空，
`retrieve_knowledge_docs` 的 `vectorstore.similarity_search()`（retriever.py:660）
**外层没有 try/except**，`NotFoundError: Collection [...] does not exist` 会一路冒到 `/chat`。
实验对照：不重启 → 抛 NotFoundError；`cache_clear()` 重新 load（等价重启）→ 计数 4→6 正常、
新文档可检索。所以「重建 + 重启」是一条龙动作，中间那段窗口期服务是坏的。

**自查库是否过期**（建库后有没有 md 被改过）：

```
./venv/Scripts/python.exe -c "import json,datetime as dt;from pathlib import Path;m=json.load(open('eval/baseline_meta.json',encoding='utf-8'));c=dt.datetime.strptime(m['created_at'],'%Y-%m-%d %H:%M:%S').timestamp();r=Path('../..')/'knowledge';md=list(r.rglob('*.md'));s=[p for p in md if p.stat().st_mtime>c];print('建库',m['created_at'],'| md',len(md),'篇 | 库里切片',m['chunks_count'],'| 建库后改过',len(s),'篇')"
```

### 为什么是「全量重建」而不是「增量 upsert」（设计决策，2026-10-26）

常见疑问：加一篇文档就要重建全库，是不是太笨？**不是，这是本场景的正确答案**，理由按重要性排：

1. **切片 ID 不稳定，增量会留孤儿切片**。`start_index` / `parent_start` 是全文位置偏移，
   改一句话后面所有切片偏移全变。按切片 upsert 会把「同一段内容」当成新切片插进去，
   旧切片仍留在库里 → 检索返回已被删改的内容。要增量可行，必须**按 `source` 整篇删再插**，
   不是按切片 upsert——这是两种完全不同的实现。
2. ~~**幂等与基线可比**：增量累积会因嵌入顺序/浮点差异让指纹漂移。~~
   **此条已实测证伪（2026-10-26）**，见下方「增量更新方案」P2：`index_fingerprint`
   只对 `(source, start_index, page_content)` 三元组排序后取 sha256，**不含 embedding**，
   所以**文件级增量后的指纹与全量重建完全相等**，门禁不会失效。理由 2 不成立，
   真正的理由只有 1 和 3。
3. **规模还小，成本可忽略**（实测 2026-10-26，隔离目录）：19 篇 → **全量 3.5s**（235 child 片）、
   **单篇 0.2s**、每篇均摊 0.18s，embedding 模型加载另计 1.3s（服务里常驻已付过）。
   全量/单篇相差 18.8 倍，但绝对差只有 **3.3 秒**。

**分水岭**：主流增量方案（LlamaIndex IngestionPipeline 的 document hash、
LangChain Indexing API 的 RecordManager）都是「按文件内容哈希判断变更、只重切重嵌变更文件」。
值得上的条件是**几百篇以上 / 单次构建 > 30s / 必须热更新不能停机**。按每篇 0.18s 线性外推，
100 篇约 18s、200 篇约 36s——**到 100 篇再立项**，现在上增量是拿 3.3 秒换一套孤儿切片
与哈希状态表的维护成本。

**另一条路径本来就存在**：学生上传 PDF 走 `/upload` → 独立 collection + 当场索引 +
会话级 TTL，**从不重建**。主流 RAG 也是这么分的：静态策展语料离线全量构建、
动态用户文档在线增量写入。两套路径互不干扰，这是现状而非缺口。

**真正贵的是重建的连带动作，不在重建本身**：3.5s 建库之后还要重启服务、重刷
`rag_baseline.json`、重跑 L1 金标准（`build_gold` + `refine_gold --force`，含 LLM 重标）。
若将来要减少这套摩擦，正解是**「热重载 + 蓝绿 collection」**而不是增量索引：新建
`cs_basics_v2` → 建完把服务指向新库 → 删旧库，全程无 500 窗口。注意 `get_knowledge_store()`
在 `app.py`（A 地盘），动它要按规矩先登记 XB。

### 删除语义：三种粒度分别能做什么（隔离实测，2026-10-26）

前提事实：`Chroma.from_documents()` **没有传 `ids=`**，切片 ID 是 Chroma 自动生成的
UUID（形如 `5546ce49-b53a-...`），**不是内容哈希**。所以「按 id 删」需要先查出来，
无法从内容反推 id。

| 删除粒度 | Chroma 能不能干净删 | 实际后果 | 推荐做法 |
|---|---|---|---|
| 整篇文档 | ✅ `delete(where={"source": ...})` | 实测 child 6→5、parent 3→2，无孤儿 | **删 md 文件 + 重建**（见下方「复活」现象） |
| 单个 child 片 | ⚠️ id 能删，但内容不变 | 父子模式下**无效**（见下） | 不要做，没有意义 |
| 小节里的一段话 | ❌ 无安全路径 | 会误删整节 / 造成同节两份正文 | **改 md + 重建** |

**① 整篇删除是干净的**，因为 `source` 是规范化相对路径（`cs/os/deadlock.md`），
child 与 parent 全带这个字段：
```
delete(where={"source": "db/bplus.md"})
删前 child=6 parent=3 → 删后 child=5 parent=2，剩余 source 只有 os/deadlock.md
```
但**不要用 API 删生产库文档**：「复活」现象——源 md 还在，下次 `build_kb.py` 重建会把
它整篇装回来，而你这期间对库做的其他修补会一起被抹掉。删除的正确入口永远是源文件。

**② 删 child 在父子模式下等于无效操作**（最反直觉的一点）。`parent_text`（小节全文）
被**复制内嵌到该小节每一个 child 的 metadata**（`indexer.py:197-201`），聚合时按
`parent_id` 去重取一份。所以只要还剩 1 片 sibling，整节照样完整注入：
```
小节有 4 片 child，删掉其中 1 片
该小节注入正文：删前 464 字 → 删后 464 字，内容完全一致
```
删 child 只是把该小节被检索到的概率从 4 份降到 3 份，**注入给 LLM 的一个字都没少**。

**③ 「删片段里的一段话」三条路都不通**：
- `delete(where_document={"$contains": "那句话"})` —— 删的是**整片 child**，不是句中一句；
  且 child 切片 `chunk_overlap=40`（`indexer.py:73`）让一句话天然横跨相邻 2 片，实测一句话
  命中 4 片、一次删光该小节全部 child，**小节彻底从检索中消失**。删除范围不可控。
- `update(metadatas=[{"parent_text": 新文本}])` —— 只能**整块替换**字符串，不能局部删句；
  且必须同步改该小节下**每一片** child。实测只改 1/4 片时，同一 `parent_id` 下出现两种正文
  （1 片是新文本、3 片是旧文本），**检索命中哪片就注入哪版，正文不确定**——这是真缺陷。
- 两者叠加是最坏情况。

**④ 正解：改源 md + 重建**，实测对照组：
```
改源 md 删掉那一句 → build_kb.py
重建后：那句话消失、小节正文 404 字完整（仅少了那一句）、该小节 child 2 片
```

**为什么这套「麻烦」不落在日常运维上**：`knowledge/*.md` 是唯一事实来源，Chroma 只是
它的派生索引。所有增删改的正确入口都是源文件，流程恒为「改 md → `build_kb.py`（3.5s）
→ 重启服务」，不存在「删除某个片段」这个动作——你只是在编辑器里删几行字。
**真正会踩上面三个坑的场景，恰恰是上了增量更新之后**：因为增量的动机就是「不重建」，
操作直接打到向量库上、绕开了源文件。

**删除的真正成本在评测侧，不在建库**。金标准引用 11 篇文档，其中
`cs/network/http-https.md` 被 60 道题引用、`cs/os/deadlock.md` 与 `tcp-handshake.md`
各 44 道、`scheduling.md` 与 `tcp-vs-udp.md` 各 40 道。删一篇 → 那些 `relevant_chunks`
条目全部指向不存在的切片，**L1 指标会静默归零且不报错**，必须重跑
`build_gold` + `refine_gold --force`（重标 gain 要调 LLM，约 4 分钟）。

### 知识库增量更新方案（为规模化准备，2026-10-26 实测后定稿）

**结论先行**：规模上来后**应当改文件级增量**，不是二选一——全量重建要**保留为 `--full` 兜底路径**。

> **状态：已实施（2026-10-26）**。下面「一、实测事实」是本方案的设计依据，
> 「五、实施状态与用法」是落地后的实际状态与验证结果。日常更新走
> `scripts/update_kb.py`，`build_kb.py` 保留为全量兜底。

#### 一、实测事实（隔离环境，全部有输出可复现）

| 编号 | 命题 | 实测结果 |
|---|---|---|
| P1 | 构建耗时是否随规模线性增长 | **完全线性**（线性度 1.00）：19 篇 3.98s / 38 篇 6.51s / 76 篇 15.95s，每篇 ~0.21s。外推 100 篇 ≈ 21s、300 篇 ≈ 63s、**1000 篇 ≈ 210s** |
| P2 | 增量后 `index_fingerprint` 是否与全量相等 | **相等**（两边同为 `sha256:6c14da72...`）。指纹只对排序后的 `(source, start_index, page_content)` 取 sha256 且**不含 embedding**，与插入顺序、嵌入批次无关 → **门禁不失效** |
| P3 | 改 1 篇能省多少 | 76 篇库：全量 **16.41s** → 增量 **0.86s**（切片 0.04s + 删 0.589s + 插 0.23s），**19.1 倍，省 94.8%** |
| P4 | 删 1 篇的指纹是否等价 | **等价**（`sha256:9e094b1f...`），耗时 0.927s |
| P5 | 增量写入后，服务进程能否检索到 | **全新进程能**（count=2、命中=True）；**已打开过该库的进程不能**（持有旧内存快照）→ 需重启生效 |
| P6 | 不 drop 会不会 500 | **不会**。500 的根因是 `drop_collection` 让旧句柄悬空；增量不 drop，服务继续用旧数据正常响应 |

#### 二、方案：文件级增量（不是切片级）

**核心：按 `source` 整篇「删 + 插」，绝不按切片 upsert。** 这样天然规避了上一节
列的所有问题——无孤儿切片（`source` 是稳定主键，整篇删净）、幂等、指纹等价（P2/P4 已证）。

**状态存库内，不需要额外的状态表**（这是本方案相对主流框架的简化点）：切片时把源文件
内容哈希写进 metadata，判断「哪篇变了」只需比对库与磁盘两侧，无需外挂 JSON / SQLite：

```python
# agent/rag/indexer.py —— build_parent_child_documents 内，每个 child 的 metadata 追加
doc_hash=hashlib.sha256(Path(abs_path).read_bytes()).hexdigest()[:16],
```

`compute_fingerprint` 只用 `(source, start_index, page_content)`，**不含 metadata**，
所以新增 `doc_hash` **不会改变指纹**，也不会影响 `gold_index_fingerprint` 校验。

**新增 `scripts/update_kb.py`（骨架）**：

```python
# 1. 扫描磁盘：source -> 文件内容 sha256
disk = {rel: sha256(bytes) for md in knowledge.rglob("*.md")}
# 2. 读库内状态：source -> doc_hash（只取这两列，不取向量，很轻）
meta = store.get(include=["metadatas"])["metadatas"]
lib  = {m["source"]: m.get("doc_hash") for m in meta if m.get("source")}

to_add   = [s for s in disk if s not in lib or lib[s] != disk[s]]  # 新增 + 变更
to_del   = [s for s in lib  if s not in disk]                      # 源文件已删除

# 3. 变更/新增：整篇删 + 整篇插（先删后插，故无需 deterministic ID）
for s in to_add:
    store._collection.delete(where={"source": s})
store.add_documents(chunks_of(to_add))     # 只重切重嵌这几篇
# 4. 删除
for s in to_del:
    store._collection.delete(where={"source": s})
# 5. 刷新指纹与基线元信息（与 build_kb.py 同一套）
write_baseline_meta(store, chunk_mode=...)

# 全程不调 drop_collection → 不会让服务进程句柄悬空 → 无 500 窗口
```

**规模化优化**：批量删除用 `delete(where={"source": {"$in": to_del}})` 一次完成，
避免 N 次全表扫描（实测单次 `delete(where=)` 在 940 片库上约 0.6s，是增量的主要成本）。

#### 三、与主流方案的对比

| 方案 | 机制 | 本项目是否采用 |
|---|---|---|
| LlamaIndex `IngestionPipeline` + docstore | 存 `doc_id → document_hash`，hash 变则重处理 + upsert | 机制采纳，**但不引入 docstore**：md 目录遍历即得全量清单，状态存库内 metadata 即可 |
| LangChain Indexing API + RecordManager | 记录 `(hash, write_time, source_id)`，三种 cleanup 模式 | **不引入**：其 `incremental` 模式**不处理源文档删除**（只有 `full` 处理）；且 RecordManager 与向量库分离，存在「记账成功、写库失败」的一致性问题 |
| 自研文件级删+插（本方案） | `source` 为键、内容哈希为判据、先删后插 | **采用** |

两点值得记的兼容性事实：
- 主流方案都要求 **deterministic ID** 才能 upsert（LlamaIndex 明确警告：自动 ID 会导致同一文件
  两次运行生成不同 ID 而重复嵌入）。本方案**先删后插、不使用 upsert**，因此**不需要改造 ID 生成**，
  省掉了这一整项改造。
- 本项目 `source` 是规范化相对路径，天然就是稳定 `doc_id`，不必额外设计。

#### 四、已知限制（实施前必须知道）

1. 增量后进程不会立刻看到新内容（P5：已打开过该库的进程持有旧内存快照）。
   运行时行为是「旧数据继续服务」而非「报错」，所以是**数据滞后**不是故障。
   **已解决（XB-19，2026-10-26 落地，待 A 复核）**：调一次热重载即可，不必重启
   ```
   curl -X POST http://127.0.0.1:8000/admin/reload-kb
   # 配置了 ADMIN_RELOAD_TOKEN 之后（详见本节第七部分）：
   curl -X POST http://127.0.0.1:8000/admin/reload-kb -H "X-Admin-Token: $ADMIN_RELOAD_TOKEN"
   ```
   实测（生产库上真跑）：加一篇文档 → 增量 → 服务不重启 → 热重载前检索不到该篇、
   热重载后注入来源正是该篇。删掉探针后指纹回到 `sha256:2db8513a...`。
2. **★ 判断热重载是否生效，不要用 `collection.count()`**：它读 SQLite 元数据，
   永远返回最新值。实测「进程持旧 HNSW 索引」时 count 已经是 237、检索却搜不到——
   这解释了更早那次「count 变了但检索不到」的矛盾现象。判据必须是真实检索。
3. **不要为热重载去降级 chromadb**：`.persist()` 只在 **0.3.x 及更早**（最高 0.3.29）存在，
   Chroma 官方在 **0.4.0（2023-07-17）移除**，理由原文是「0.4.0 即时保存所有写入，
   故不再需要 persist」。降级有三重障碍：① 0.4.0 把存储从 duckdb+parquet 换成 sqlite，
   官方只提供**升级**用的 `chroma-migrate`，**没有降级路径**，现有 1.5.9 库 0.3.x 读不了；
   ② `langchain_chroma 1.1.0` 走 `chromadb/api/rust.py`（1.x 才有），0.3.x 无此模块；
   ③ 真要降级得连带把 langchain_chroma 降到 2023 年的 0.0.x，会级联破坏整个 langchain 栈。
   何况 0.3.x 的语义是「写入只在强制 flush 时落盘」，忘了调 `persist()` 就丢数据，
   **比现在更危险**。
4. **切片参数变更（`RAG_CHILD_CHUNK_SIZE` 等）必须走全量**，因为增量按「文件内容哈希」判断，
   切片参数变了但文件内容没变时会被判为「无变更」而漏掉。`update_kb.py` 需要把切片参数
   一并计入判据（例如存一份 `chunk_params_hash`）。
5. 换 embedding 模型仍必须全量重算（向量空间整体变了）。

#### 五、实施状态与用法（2026-10-26 已落地）

**改动清单**

| 文件 | 改动 |
|---|---|
| `agent/rag/indexer.py` | 切片 metadata 新增 `doc_hash`（源文件内容 sha256 前 16 位，由 `load_markdown_documents` 写入，扁平/父子两种模式都有）与 `chunk_params_hash`（切片参数快照）；新增 `build_chunks()`，**全量构建与增量更新共用同一套切片逻辑**，否则两条路径稍有差异就很难查 |
| `scripts/update_kb.py` | 新增。增量入口，带 `--dry-run` / `--full` / `--no-fallback` / `--meta-path` |
| `scripts/verify_incremental.py` | 新增。等价性验证，**改切片或索引逻辑后必跑** |
| `build_kb.py` | `write_baseline_meta` 与 CLI 新增 `--meta-path`，隔离验证不再覆盖生产基线 |
| `tests/test_update_kb.py` | 新增 29 例，只覆盖纯函数（不跑 embedding） |

**日常用法**（backend/ 下，venv python）

```
python scripts/update_kb.py                 # 按源文件差异增量更新
python scripts/update_kb.py --dry-run       # 只看会改哪些文档，不动库
python scripts/update_kb.py --full          # 强制全量（换 embedding 模型时用）
python scripts/update_kb.py --query "死锁的必要条件"
python scripts/verify_incremental.py        # 改切片/索引逻辑后验证等价性
curl -X POST http://127.0.0.1:8000/admin/reload-kb   # 更新后热重载，免重启
```

切片模式**不用手动指定**：脚本按库内 `chunk_params_hash` 自动对齐成建库时的模式
（忘记加 `--parent-child` 不会再退化成全量，见本节第六部分）。

**等价性验证结果**（`verify_incremental.py`，7 场景全 PASS，EXIT=0）

| 场景 | 增量指纹 | 全量重建指纹 | 结论 |
|---|---|---|---|
| S0 无变更 | `2db8513a`（不变） | — | PASS，且不刷新基线元信息 |
| S1 新增 1 篇 | `0bf91da1` | `0bf91da1` | PASS |
| S2 修改 1 篇 | `53e95473` | `53e95473` | PASS |
| S3 删除 1 篇 | `48cf58c7` | `48cf58c7` | PASS |
| S3b 一次删 2 篇（`$in` 批量分支） | `8be1c6e5` | `8be1c6e5` | PASS |
| S4 切片参数变更 | 自动回退全量 | — | PASS |
| S5 扁平模式新增（`RAG_PARENT_CHILD=0`） | `b27639a2` | `b27639a2` | PASS |

**回归结果**

- 单测 **466/466**（原 437 + 新增 29）。
- L1：`top1 68/71`、`top3 70/71`、`nDCG@3 0.870`、`MRR 0.972`、`Recall@3 0.794`，
  与重建前基线**逐项相同**；指纹 `sha256:2db8513a...` 重建前后一致 →
  实证「新增 metadata 既不改指纹、也不改检索结果」。
- 单测抓到并修掉一个真缺陷：库内**混着多种** `chunk_params_hash`（半更新状态）时，
  原判据「当前参数在库里即放过」会漏判，那批旧参数文档内容没变、永远不会被重切，
  库会一直不一致。已改为 `len(lib_params) > 1` 即触发全量（`needs_full_rebuild`）。

**自动回退全量的三种情况**（`--no-fallback` 可改成报错退出，CI 里用来暴露意外变更）：
① 旧库没有 `doc_hash`；② 库内切片参数混着多种；③ 当前切片参数不在库里。

#### 六、生产库实测：新增一篇真实文档（2026-10-26）

在**生产库**（不是隔离环境）上完整跑一遍「新增 → 检索 → 删除 → 再新增」：
新增 `knowledge/cs/db/sql-query-join.md`（SQL 查询与多表连接，3.7KB）。

| 步骤 | 命令 / 动作 | 结果 |
|---|---|---|
| 基线 | — | 19 篇 / **235 切片**，指纹 `2db8513a` |
| ① dry-run | `update_kb.py --dry-run` | 报「新增+变更 1 篇」，**未判全量**，库未改动 |
| ② 增量 | `update_kb.py` | **235 → 248 切片（+13）**，库内操作 **0.35s**（切片 8ms + 删 1ms + 插 346ms）；进程墙钟 6.8s，其中绝大部分是加载 embedding 模型 |
| ③ 等价性 | 隔离目录跑全量重建对照 | 全量指纹 `bbe0cdc4` ≡ 增量指纹 `bbe0cdc4` |
| ④ 检索 | 3 个只有新文档能答的问题 | 全部命中 `cs/db/sql-query-join.md` |
| ⑤ 删除 | 移走文件后增量 | 248 → **235**，指纹精确回到 `2db8513a`（幂等，来去可逆） |
| ⑥ L1 | `smoke_rag.py --save-base` | **top1 68/71、top3 70/71** 与旧基线相同，MRR 0.970（旧 0.972，微差来自新切片入候选池），**无退化** |

热重载闭环（同一次实测，服务在 8000 端口运行，全程未重启）：

- 删除文档后**不重载**就提问 → 服务仍返回**已删除文档**的 sources（数据滞后，符合预期）；
- 调 `POST /admin/reload-kb`（返回 `bm25_cleared:1`）→ 同样问题 `sources: []`，新数据立即生效。

**结论**：新增/修改/删除文档都走增量，耗时从「全量重建」降到 0.2–0.4s，
且与全量重建结果逐位等价；唯一要注意的是更新后调一次热重载。

**本次实测抓到并修掉的缺陷**：`update_kb.py` 不带 `--parent-child` 时会误判全量。

父子模式是 opt-in（`RAG_PARENT_CHILD=1` / `--parent-child`），库却是按父子建的。
本次命令没带开关时 `current_params` 算成 flat，与库内父子参数不符 →
`needs_full_rebuild` 判「切片参数已变更」→ **每次都回退全量重建，增量形同虚设**
（实测现象：明明只加了 1 篇，却打印"※ 需要全量重建：切片参数已变更"）。
修复：新增纯函数 `infer_chunk_mode()`，拿库内 `chunk_params_hash` 与两种模式
各算一次哈希比对，反推建库模式并自动对齐。优先级
**显式开关 > 环境变量 > 自动推断**；库内混参数或空库时不推断。
单测新增 7 例（含端到端验收：推断后 `needs_full_rebuild` 必须判 False），
全项目 **479/479**。

**仍存在的限制**

1. ~~更新后需重启服务才生效~~ **已解决（XB-19）**：`POST /admin/reload-kb`
   清 `lru_cache` + BM25 缓存并重新加载，免重启生效，实测通过。
   不调也不报错——服务继续用旧数据正常响应，只是看不到新内容（数据滞后）。
   ~~⚠️ 该端点无鉴权~~ **也已解决（XB-20）**：默认只放行本机来源，见本节第七部分。
2. 换 embedding 模型必须手动 `build_kb.py`：向量空间整体变了，增量判断不出来。
3. `read_library()` 要全表读 metadatas，父子模式下每片都内嵌 `parent_text`；
   按 1000 篇 × 12 片 × 约 3KB 估算约 36MB 常驻，可接受但值得知道。
4. 切片模式自动推断依赖库内 `chunk_params_hash`；旧版本建的库（没有该字段）
   仍会走「无 doc_hash → 全量重建」分支，属预期行为。

#### 七、管理端点鉴权（XB-20，2026-10-26 落地，待 A 复核）

热重载会触发一次**全库重新加载**，属重操作。此前该端点完全没鉴权，只靠
「服务监听 127.0.0.1」这道网络位置防线——一旦 `--host` 改成 0.0.0.0 就裸奔。

**两层闸门（默认即安全，不用记得配什么）**

| 配置 | 放行条件 | 不满足时 |
|---|---|---|
| 未配置 `ADMIN_RELOAD_TOKEN`（默认） | 只放行 **loopback（本机）** 来源 | `403` |
| 配置了 `ADMIN_RELOAD_TOKEN` | 必须带 `X-Admin-Token` 头且比对通过（`secrets.compare_digest`，防时序侧信道）；**不看来源**，便于远程运维 | `401` |

- 配了令牌后**本机调用同样要带**（行为一致才好排查，不能本机悄悄放行）。
- 令牌比对用 `compare_digest`；拒绝原因只说「缺什么」，不回显令牌。
- 被拒绝时记 WARNING 日志（含来源 IP，不含令牌）。
- 端点另加 `@limiter.limit("10/minute")`，防止被反复触发重载。

**⚠️ 不信任 `X-Forwarded-For`**：该头可任意伪造，用它判来源等于没鉴权。
注意反向代理场景——服务端看到的来源 IP 是代理地址，**若代理跑在本机会被判成
loopback 而放行**，那种部署必须配置 `ADMIN_RELOAD_TOKEN`。

**实测（临时起 `--host 0.0.0.0` 实例，用局域网 IP `192.168.2.23` 访问，测完即关）**

| 场景 | 结果 |
|---|---|
| 0.0.0.0 实例 + 局域网 IP + 无令牌配置 | **403**（即使完全暴露也打不到） |
| 同一实例 + loopback | **200**（本机用法不变） |
| 带令牌实例 + 局域网 IP + 无头 | **401** |
| 带令牌实例 + 局域网 IP + 错令牌 | **401** |
| 带令牌实例 + 局域网 IP + 正确令牌 | **200** |

**结论**：原先「改 `--host 0.0.0.0` 前必须先加鉴权」这条限制**已被消除**——
默认行为本身就是安全的，暴露出去也打不到；想远程运维再显式配令牌。

**改动**：`app.py`（`_client_ip` / `_is_loopback_client` / `_admin_gate` 三个纯函数
+ 端点接闸门 + 限流 + 拒绝留痕）、`tests/test_kb_hot_reload.py`（新增 17 例：
loopback 判定 6 例 + 闸门矩阵 8 例 + 端到端 3 例）、`env.example`。
单测 **496/496**（479 + 17）。

**回退**：删掉端点里的 `_admin_gate` 调用即恢复无鉴权（不建议）；
`ADMIN_RELOAD_TOKEN` 留空就是默认行为，不影响任何现有用法。

## 9. 待办与触发条件

- **教师资料长期进检索（XB-21 方案 B → 已落地为 XB-23，2026-10-26）**：
  `/upload/teacher` 加 `persist=classroom` 即走**课堂级 collection
  `classroom_{id}_docs` + 原件落盘 + 与共享库并行召回**（详见 §2「课堂资料长期化」）。
  不传 `persist` 仍是会话级内存库（方案 A，1h TTL）。
  **仍然不要写进 `cs_basics`**。
  直接写共享库的四个硬伤：① 一个教师传份错题集，全局所有课堂都能检索到；
  ② L1 83 题金标准与指纹（现 248 片 / `bbe0cdc4`）立即失效且无法复现；
  ③ 无归属无审核；④ 加容易删难。
  方案 B 不污染全局库、不影响 L1 基线、**生命周期跟随课堂行**、天生带课堂权限边界。
- **★ 课堂库的生命周期（此处曾写错，2026-10-26 更正）**：
  **结课 ≠ 删除**。`Classroom` 模型只有 `created_at`，**没有结课/归档状态字段**，
  系统里并不存在「结课」这个动作。课堂库的生命周期 = `Classroom` 行的生命周期：
  **只有课堂被删除时才清理**（沿用 `ClassroomMember` / `Message` 的 cascade 惯例）。
  课堂一直存在，库就一直保留，学生随时回来复习都能检索到本课讲义。
  「结课」若将来要做，应加 `status`（active/archived），archived 只是**禁写、只读**，
  绝不是删数据。
- **共享库不是「不能上传」（此处曾表述含糊，2026-10-26 更正）**：准确说法是
  **不能「无审核、上传即刻生效」地写入**。共享库的数据源是仓库文件
  `knowledge/cs/*.md`，Chroma 只是它的派生索引——所以「上传到共享库」= 文件先落成 md
  → 审核 → `update_kb.py` 增量入库 → 热重载。这条链是通的，成本 0.35s/篇。
  三种需求对应三条路，别混：
  | 需求 | 落法 | 生效范围 | 是否改基线 |
  |---|---|---|---|
  | 我课上要用的讲义 | 课堂库 `classroom_{id}_docs` | 本课堂 | 否 |
  | 希望所有课堂都能用 | `knowledge/inbox/` 暂存 → 审核 → 移入 `knowledge/cs/` → 增量 | 全局 | **是**（需重刷金标准指纹）|
  | 学生自己带资料提问 | `/upload` 会话级 | 仅该会话 | 否 |
- `docs/proposals/PROPOSALS.md`（项目根）：L1 文档路由 / L2 父子检索。
  **L2 已落地并在生产启用**（2026-10-25 扩库到 19 篇 / 77 parent，触发条件已满足）；
  **L1 文档路由已落地**（2026-10-26，XB-26，`agent/rag/doc_router.py`，默认开，见 §1.1）。
  L1 的**域外闸门**（`RAG_ROUTER_GATE`）保持默认关闭：实测无安全阈值，见 §1.1。
- 扩库后仍未解决、且**不是路由层能解决**的两件事（2026-10-26 实测记录）：
  1. **域外误注入**（如「Python 列表和元组」→ 注入 `db/sql-query-join.md`）：
     根因是库里被顺带提到 `python`/`列表`/`元组`（df 分别 2/1/1），任何「词是否
     出现在库」的判据都判不出域外。解在**换判别力更强的重排/分类模型**——
     **不是补库覆盖**（补库会让这些词的 df 升高、权重更低，判得更糊，见 §1.2）。
  2. **重排排序被干扰项压过**：问「快排时间复杂度」时正确的 `dsa/sorting.md`
     排第 3，输给 `os/scheduling.md`。路由收窄了候选池但没改单条分数
     （交叉编码器逐对独立），所以这条要换模型或加训练，不是调参能解。

- 已拍板（2026-10-25）：XB-13 relevance_floor **批准保留**；XB-14 空资料静默 + 缺口日志批准；
  fl-5「省略式追问被 floor 误伤」**实测排除**——L4 实例是评测串联的上下文错配
  （接在死锁对话后跑），「死锁 性能开销」库内本无内容，floor 正确过滤；
  正确 topic 下重排最高分 +1.185 远高于 floor（diag_floor_scores.py 可复现）；
- 软门禁：资料利用率 0.30（目标 0.6）——XB-15 已改 `RAG_GROUNDING_RULES`
  措辞（回答应以资料为主要依据 + 多条相关内容尽量组织进回答），L3 重测确认；
  hetero_judge 异源裁判（`EVAL_JUDGE_MODEL`，改前先问 A）。