# NEXT_SESSION_PROMPT —— 新对话开场指令（v6.6，2026-10-26 02:55 更新，取代 v6.5）

> 用法：新开对话，把下面代码块里的内容整段粘贴发送即可。也可以只发一句  
> 「阅读 docs/handoff/NEXT_SESSION_PROMPT.md 并按其中的任务清单执行」。  
> **v6.5 增量**：知识库 **20 篇 / 248 child**（新增 `cs/db/sql-query-join.md`），
> 基线指纹 **`sha256:bbe0cdc42635ac1c936d678ad22f3e8c`**；增量更新已在**生产库**实测
> （新增 1 篇：235→248，库内 0.35s，与全量重建指纹逐位等价，L1 无退化）；
> 修掉「不带 `--parent-child` 会误判全量」缺陷（新增 `infer_chunk_mode()`）；
> **XB-20 管理端点鉴权**已落地：`/admin/reload-kb` 默认只放行本机来源，
> 配 `ADMIN_RELOAD_TOKEN` 后改校验 `X-Admin-Token`（实测 0.0.0.0 暴露下局域网访问 403）；
> XB-21 方案 A 已落地：新增 `POST /upload/teacher`（教师备课素材，独立限流 10/h +
> `_require_teacher()` 校验，会话级内存库，实测不污染共享库）；
> 测试 **529/529**。详见 `docs/specs/RAG_HANDBOOK.md` §2、§8 第六七部分、`docs/worklogs/B_WORKLOG.md` 末尾。  
> 本版对应状态：**知识库已扩到 20 篇（248 child / 77+ parent）、父子检索转生产默认、
> P0 度量失真已收尾**——L1 金标准重刷为 parent 粒度（79 条计分，带 gold_granularity +
> gold_index_fingerprint 同源校验）、chunk_quality 双粒度适配完成、L3/L4 已按新库重跑。
> 基线：top1 68/71、top3 70/71、nDCG@3 0.870、提升度 +0.060、p95 4.3s、0 降级；
> L3 利用率 0.8125、幻觉/矛盾 0；测试 **529/529**（增量更新 + XB-19 热重载 + XB-23 课堂资料 +
> XB-24 教师端实测修正 + 生产库实测 + 模式自动推断修复 + XB-20 鉴权 + XB-21 教师上传入口后）。  
> 上一版（v6.3）对应：XB-13/14 获批、XB-15/16/18 落地、per_doc_floor -5.0、
> 8000 服务已重启、smoke_chat 剧本固化为无额度单测（test_prompt_regression.py）。  
> **本轮新发现，需要拍板**：`kind=oos` 的 4 条库外题里 2 条已被扩库覆盖（MySQL 索引、
> Java HashMap），详见下文「已知失真」。

---

我的角色是 B，负责 AI-Classroom 项目的 RAG 知识库与检索。请先读下列文件建立上下文，然后继续执行剩余事项。

**必读（按序）**

1. `docs/handoff/HANDOFF.md` —— **资料库/检索这条线的交接总览**（现状数字、六条决策、
   未决事项、下一步分档、硬约束、自查清单）。先看它，能省一轮。
2. `AGENTS.md` —— 项目全局指令（只管分阶段总结纪律）
3. `docs/worklogs/B_WORKLOG.md` —— 只看 2026-10-24 ~ 2026-10-26 的记录（XB-13~22 与增量实测全在这里）
4. `.workbuddy/memory/2026-10-24.md` ~ `2026-10-26.md` —— 逐节流水账，**踩坑教训最全**
5. `docs/specs/RAG_HANDBOOK.md` —— RAG 现状 / 口径 / 硬约束 / 维护命令（§8 = 增量更新，必看）

**当前状态（截至 2026-10-26 02:20，全部实测验证过）**

- **知识库**：**20 篇 / 248 child**（新增 `cs/db/sql-query-join.md`），指纹
  `sha256:bbe0cdc42635ac1c936d678ad22f3e8c`。数据源 `knowledge/cs/`（**不是**
  `backend/knowledge/`，后者是 v0~v3 历史版本目录），向量库 `backend/chroma_kb/`。
- **增量更新（生产库实测，2026-10-26）**：新增/修改/删除文档都走
  `python scripts/update_kb.py`（库内 0.2~0.4s），与全量重建**指纹逐位等价**；
  更新后调 `curl -X POST http://127.0.0.1:8000/admin/reload-kb` 免重启生效。
  切片模式会按库内参数快照自动推断，**不用手动加 `--parent-child`**。

- **检索链路**：向量粗召回 + BM25(jieba) + RRF → （父子模式下按 `parent_id` 聚合）
  → bge-reranker-v2-m3 重排 → **整题级相关性下限** `relevance_floor`（默认 -2.0，
  2026-10-25 从 -1.0 下调；A 已批准保留该机制）→ 注入 top_k（父子 3 / 扁平 4）。  
  `chunk_mode=parent-child`，生产服务带 `RAG_PARENT_CHILD=1`。
- **空资料行为（XB-14，已批准）**：检索为空时不在回答里告知学生，写结构化缺口事件  
  `backend/logs/kb_gap_events.jsonl`；报表 `python -m eval.kb_gap_report`。  
  L3 空资料声明率是信息性指标；幻觉率门禁（≤0.15）保留。
- **XB-15（2026-10-25 落地，待 A 复核）**：`agent/prompts.py` 的 `RAG_GROUNDING_RULES`  
  （XB-18 已从 app.py 归口到此）新增「资料为主要依据 + 多条相关内容尽量组织进回答」  
  两条，原四条未动；  
  `backend/.gitignore` 追加 `logs/`（当前工作区没有 .git，建库时生效）。  
  **实测（L3 sample 10，报告 e2e_20261025_004723.json）：资料利用率 0.30 → 0.625**  
  （软门禁目标 0.6 达标）、幻觉率 0.0、矛盾率 0.0、门禁 EXIT=0。  
  ⚠️ `RAG_GROUNDING_RULES` 是模块常量：8000 常驻服务**重启后才吃到新措辞**  
  （下次重启自然生效，无需额外操作）；L3 评测走独立进程已经是新措辞。
- **fl-5 观察点已关闭（实测排除）**：`scripts/diag_floor_scores.py` 实跑证明  
  floor 无误伤——「死锁 性能开销体现在哪？」库内本无内容（最高 -2.674，正确过滤）；  
  正确 topic 下 +1.185 正常命中。L4 的 sources=0 是评测串联上下文错配 artifact。
- **目录结构**：backend 根目录 4 个核心入口 `app.py` / `run_tests.py` / `build_kb.py` /  
  `init_db.py`；smoke/verify/一次性工具（含 diag_floor_scores.py）在 `backend/scripts/`；  
  RAG 文档在项目根 `docs/specs/RAG_HANDBOOK.md` + `docs/proposals/PROPOSALS.md`（未触发提案）。
- **评测体系**：`backend/eval/` 12 个模块 + `agent/rag/kb_gap.py`；L1-L4 全落地；  
  统一入口 `python -m eval.e2e_runner --layers ...`。
- **检索基线**（75 条评测集、71 条计分，**父子 full 配置**，`rag_baseline.json`，指纹 2db8513a）：
  top1 **68/71**、top3 **70/71**、nDCG@3 **0.870**、提升度 **+0.060**、
  Recall@3 0.794 / Precision@3 0.566、p95 **4.3s**（父子预算 ≤5s）、0 降级。  
  金标准 = `topic-derived+llm-refined`（parent 粒度，79 条计分，gain 1.0/0.5/0.0 = 117/123/112），
  带 `gold_granularity` + `gold_index_fingerprint` 同源校验。
- **L3 生成层**（新库重测，报告 e2e_20261025_233508.json）：利用率 **0.8125**（判出 8 条）、  
  幻觉率 0.0、矛盾率 0、EXIT=0。  
  注意口径：空资料注入后「外幻觉率」裁判语义弱化，0.0 只说明无与资料矛盾的陈述。
- **L4 已补资料利用率（XB-16）**：per_case 增加 `material_utilization` / `used_indices`，  
  聚合增加 `material_utilization` + `utilization_judged`；**只对 sources>0 的条判**，  
  库外题记 None（不填 0）。判定函数 `eval.grounding_eval.judge_utilization` 与 L3 共用、口径唯一。  
  SSE 的 sources 事件不带正文，切片由本地用同一 query + state.topic 重跑检索取得（近似重建）。  
  最新实测（新库 10 条，e2e_20261025_233508.json）：成功 10/10、判出 8 条、利用率 **0.6459**、
  e2e p95 25.9s、avg sources 2.0；cascade top1/top3 **0.5/0.625**（旧库 0.625/0.875，
  库变大→干扰项变强，方向符合预期）。
- **已知失真（待拍板）**：`kind=oos` 4 条里 **2 条已被扩库覆盖**——MySQL 索引
  （`db/index-transaction.md`，sources=3）、Java HashMap（`dsa/hash-table.md`，sources=1），
  sources>0 是**命中不是误召回**。任何「库外 0 注入率」指标读到 2/4 失败都会误判。
  建议改判为库内并补 2~3 条真库外题（Redis 持久化 / CAP 仍真库外），
  但会重算 L1 金标准与 L4 分母，需单独一轮，别顺手改。
- **文档路由 L1 已落地（2026-10-26 晚，XB-26，默认开）**：`agent/rag/doc_router.py`，
  链路最前面加「先选文档、再选切片」，检索范围收窄到 top3 篇
  （`filter={"source": {"$in": [...]}}`，BM25 同步收窄）。
  信号是**切片级证据按 source 聚合**（每篇取 BM25 最高 3 片求和 + 0.3×向量 RRF），
  实测 route@3 **71/71**、route@1 67/71。
  **原 §4.3 的「每篇一条摘要向量」方案实测只有 60/71，已否决**（原因见
  `docs/specs/RAG_HANDBOOK.md` §1.1，别照着提案原文重做一遍）。
  收益口径：top1 **不变**（68/71，交叉编码器逐对独立打分，换池不改单条分数）、
  nDCG@4 **0.8621→0.8818**、库外误注入 2→1、热态耗时 21ms。
  回退：`RAG_DOC_ROUTER=0`。
- **域外闸门（`RAG_ROUTER_GATE`）默认关闭，别急着开**：IDF 加权覆盖率在库内题与
  库外题之间有 0.098 的重叠区（Python 题的 `python`/`列表`/`元组` 在库里 df=2/1/1），
  开闸会静默丢掉带 topic 的口语追问的资料。要开先跑
  `scripts/calibrate_doc_router.py`（含 23 条域外探针 + 阈值扫描）。
  **不要用「补库覆盖」来解决这条**（上一版这里写的是「正解是补库覆盖」，2026-10-26
  晚已推翻）：Redis / CAP / JVM GC / Docker 确实是真库外题，但把它们补进库既不能让
  闸门变安全（含该词的文档会让 df 升高、IDF 权重下降，判得更糊），也不解决库里被
  顺带提到的泛词（`python`/`列表`/`元组`）——那才是 0.098 重叠区的成因。
  补库另有纪律：只由教学计划驱动、不由缺口清单驱动，三条硬触发见 `docs/specs/RAG_HANDBOOK.md` §1.2。
  **这条的正解是换判别力更强的重排/分类模型**，或先接受「不开闸 + 靠 floor + 缺口日志」。
- **p95 门禁与本次改动无关但确实红着**：路由关 p95 3.94s、开 3.73s，都超
  `GATE_L1_P95 = 3.0`；而手册写的是「父子模式 ≤5s」。代码与规格不一致，
  **改预算需 A 拍板**，不要为了让门禁变绿而调。
  **→ 2026-10-27 XB-29 已闭环**：分档的前提（重排打 parent 全文）已被
  `RAG_RERANK_TEXT=child_title` 消除，父子 p95 实测 1.134s 与扁平同量级，
  故**统一回 3.0s、删除分档**（`HANDOFF` U12）。现在只有 3.0s 这一个预算。
- **测试基线已更新**：`run_tests.py` **604/604**（558 + XB-28 新增
  `tests/test_rerank_text.py` 9 例与 `test_source_cap.py` 12 例 + XB-29 的
  `test_rerank_text.py` 扩到 **13 例** + XB-30 新增
  `tests/test_assessment_parallel.py` **9 例** + XB-32 新增
  `tests/test_topic_no_think.py` **9 例** + XB-33 新增
  `tests/test_assessment_speculative.py` **11 例** + XB-34 新增
  `tests/test_metrics_report.py` **37 例** + XB-35 新增
  `tests/test_recall_pool.py` **16 例** 并改 2 例 + XB-36 指标扩展新增 22 例）
  ⇒ **当前 683/683**。
- **2026-10-28 XB-34：检索链路评测指标报告功能已上线**（文档见
  `docs/specs/EVAL_METRICS_API.md`）。四个接口：`GET /evaluation/metrics`（报告卡 JSON）、
  `GET /evaluation/metrics.csv`（CSV 下载）、`POST /evaluation/metrics/refresh`
  （异步重跑，202 + job 轮询）、`GET /evaluation/metrics/job/{job_id}`。
  13 项指标含阈值/达标判定/计算说明/改进建议。
  **2026-10-28 XB-36 后扩到 21 项 / 6 组**（链路稳定性 / 检索层 / 上下文层 /
  生成层 / 响应延迟 / 重排增益，对齐 RAGAS 三段式）。新增：链路成功率(L4)、
  端到端 p95(L4)、注入噪声率(L1)、忠实度(=1−外幻觉率，派生)、事实矛盾率、
  答案相关性、答案正确率（后两个是**新裁判维度**，三档判分多票取中位数）、
  资料利用率。展示顺序由 `GROUP_ORDER` 决定，**加指标追加到 `METRIC_SPECS`
  末尾即可**，不用挪动已有 spec。
  ⚠ 读数三条：**① 忠实度与外幻觉率是一个数的两面**，别当两个问题；
  **② 答案相关性/正确率有同源偏松嫌疑**（生成与裁判都走 deepseek-flash，
  首次 8 条实测全 1.0），接入异源裁判前只能看趋势；
  **③ 生成层 6 项 + 链路稳定性前 2 项日常是「未运行」**，要出数得跑
  `--layers L1,L2,L3,L4 --sample 12`（约 19 分钟）。
  ⚠ 三个设计约束：**① 评测必须走子进程**（`smoke_rag.configure()` 改的是
  `retriever.CONFIG` 全局对象，与 `/chat` 共用，进程内跑会污染线上检索）；
  **② 单飞 409**（并发评测抢 CPU 会让延迟指标失真）；**③ 过期判定用索引指纹**
  而非时间。Recall 物理上限 **0.9159**、Precision **0.6573**（上限本身不是 1.0），
  换题库必须重算。**2026-10-28 XB-36 后基线**（`e2e_20261028_142722.json`，
  L1/L2 全量 71 题 + L3/L4 各 12 条）：**21 项 = 达标 19 / 未达标 2 / 未运行 0**。
  未达标仍是上下文召回率 **79.09%**（上限 91.59%，达成 86.4%）与上下文精确率
  **56.34%**（上限 65.73%，达成 85.7%）；p95 1.107s、nDCG 0.8700、MRR 0.9648、
  top1 68/71、top3 70/71；端到端 p95 **10.404s**（检索只占 1.1s，九成是生成）。
  报告卡 11 列（含「物理上限」）。
  ⚠ **别再试这四条，都已实测为负**（详见 `docs/plans/PLANS.md` §7.11.3）：
  盲目扩粗召回（池上限 +6.9pp 但重排挑错 +6.7pp，净≈0）、相邻小节扩展 ±1/±2、
  重排改读更长文本（parent_head 480 → Recall 掉到 0.731）、重排×RRF 名次融合
  （w=0.5/1.5 更差）。根因是 **bge-reranker-v2-m3-int8 只读 250 字的判别力**，
  要再往上只能换更强的重排模型（本机只有 int8 缓存，换模型需联网下载；
  当前重排 1.08s，p95 预算 3.0s 还有约 1.9s 余量）或人工复核金标准。
- **2026-10-27 XB-31/XB-32：追问缓存评估 + 抽 topic 关思考（U14）**：
  1. **盲缓存 topic 已否决**：换题轮上用旧 topic 检索，15 条里 1 无害 /
     **12 稀释**（注入 4 条里 2 条是旧知识点）/ **2 有害**（期望文档全丢）；
     叠加 p_switch≈43.6% ⇒ **约 41% 请求上下文被污染**，换期望延迟仅 0.61s。
     决定性理由不是检索，是**状态**：topic 落库 → 下轮 `load_last_state` 读回 →
     还喂给 `_update_profile` 记 mastery ⇒ 误判跨轮累积且静默。
  2. **「用本地 embedding 判沿用」已否决（O10）**：追问句 sim 中位 0.293 vs
     换题句 0.351，分布完全重叠，最佳阈值准确率仅 59.3%。根因：省略式追问
     本身不含主题词，向量上离任何主题都远。**别再试。**
  3. **O11 已落地并转默认**：只给抽取节点 `bind(extra_body={"thinking":
     {"type": "disabled"}})`。`deepseek-flash` 是推理模型，回 "same" 也要先
     想 20~30 个 reasoning token（实测 completion 25 里 23 是 reasoning）。
     节点级 A/B（各 81 样本）：mean **1012→656ms（−35%）**、p95 −42%，
     判定准确率 96.3%→97.5%。开关 `RAG_TOPIC_NO_THINK=0` 回退。
  4. **一个反直觉的坑**：砍 prompt 长度**完全没用**。prompt 从 6 字拉到
     4040 字耗时几乎不变（926→850ms），边际 prefill 仅 21.6ms/千字——
     砍一半现行 prompt 只省 7ms。三变体 A/B 里精简版**全都更慢**。
     量耗时前先做分解，别凭直觉归因。
- **2026-10-27 XB-28：全链路剖析 + 重排改打短文本（U7 已落地）**。
  先看 `docs/plans/PLANS.md`（本轮产出的完整调优方案），要点：
  1. 检索端到端 mean 2149ms 里**重排占 97.7%**，其余各段合计 50ms。
  2. **默认已是 `child_title`（命中片 + 小节标题）**，不是纯 `child`
     （XB-29 追加的第三档，严格优于 child，见 `docs/plans/PLANS.md` §7.5）。
     三档 L1 对照：parent 0.8818/p95 3.655s ✗ ｜ child 0.8549/1.103s ｜
     **child_title 0.8651/1.134s ✓（默认）**；top1/top3 三档**完全不变** 68/71、70/71。
     回退 `parent` 一行环境变量（⚠ 会让 p95 回到 ~3.65s 判红）。
  3. **O3（理解评估与检索并行）已落地并转默认**（XB-30，2026-10-27）。
     `抽 topic → gather(理解评估, 三路检索)`，入口
     `agent/graph.py:run_assessment_with_parallel`，开关 `CHAT_PARALLEL_ASSESS=0` 回退。
- **2026-10-28 XB-33：给 A 装尺子 → A 关思考 A/B → 投机执行（U15 否决 / U16 落地）**：
  1. **A 有了质量判据**：`eval/data/assess_gold.json`（30 条人工标注，6 类难度）+
     `eval/assess_quality.py`（resolved 准确率 / 误解检出一致率 / hint 方向一致率）。
     查库时发现的尴尬：messages 表 162 条 student 话语**几乎全是提问**，
     没有一句是"学生陈述理解程度" ⇒ 判据集只能手写，
     它覆盖**难度分布而非频率分布**（已写进标注集 `limitations`）。
  2. **A 关思考实测否决（U15，不转默认）**：延迟 −68%（2410~2674ms → 764~772ms）
     很诱人，但**误解检出 −10pp**（83.3%→73.3%），strict −6.7pp。
     退化形态是**过度检出**——把"完全没思路，老师直接讲吧"记成
     "不了解 ReadView 可见性机制"。这正好撞 D11：misconception 喂给
     `_update_profile` 记 mastery，记错后**从回答上看不出来**。
     正反序各 2 轮 × 每档 60 样本，两次独立运行高度复现 ⇒ 结论可信。
  3. **投机执行已落地并转默认（U16 / O12）**：`gather(E, A(prev), R(prev))`，
     E 判沿用直接采用、判换题作废重跑；延迟收益与盲缓存相同但正确性零损失。
     三层验证：单测固定延迟 **250ms vs 507ms**（差 ≈ 注入的 E，干净因果）；
     节点级配对（间隔 9s 避开限流）**4097→3140ms（−23.3%）**，12/15 更快，
     与理论 E/max(A,R)≈24% 吻合。
  4. **⚠ 限流会吃掉投机收益**：同样代码，间隔 0.8s 时命中组只有 −170ms，
     拉到 9s 变成 +956ms。因为投机把 3 个请求同时打出去，限流下更易排队。
     ⇒ 限流宽松的部署收益明确；限流严格（如当前 10/min）的可能持平甚至倒退，
     此时置 `CHAT_SPECULATE_TOPIC=0` 回退 O3。
  5. **测量纪律（这轮又被同一个坑咬了一次）**：
     ① 一个进程里给两个档位各 `asyncio.run` 一次 ⇒ 第二个 loop 复用绑在第一个
        loop 上的 HTTP 连接池，报 `RuntimeError: Event loop is closed`。
        改成**每档一个独立进程**。
     ② 真实 LLM 延迟方差 ±1.5~4.4s，要分辨的效应只有 0.66s ⇒
        n=6 不够，n=24 时 mean 与中位都会给出相反结论。
     ③ 靠**固定延迟的 fake LLM** 才拿到干净因果证明，再用大间隔真实测量做方向确认。
     A/B（各 8 条、正反序各一轮）：前置段 **3126/3122ms（并行）vs 4061/4883ms（串行）**，
     快 0.94~1.76s。⚠ 两条读数纪律：① **前置段差值才是 O3 的直接证据**——
     TTFT 差值更大但生成段也差了 0.5~1.0s（O3 不碰生成），那部分是组间噪声；
     ② 收益上限是 **min(理解评估, 检索)**，较长那侧省不掉，再往下压得动
     评估 LLM 的调用次数或耗时。
  4. 三个新量具可复用：`scripts/profile_rag_pipeline.py`（分段计时）、
     `scripts/measure_ttft.py`（SSE 拆前置段/生成段）、`scripts/measure_assessment.py`。
  5. **别再踩**：后端无 `--reload`，改代码必须重启；重启要显式带 `RAG_PARENT_CHILD=1`
     （.env 里没写）；杀进程先 `netstat -ano | grep :8000` 拿真 PID。
- **2026-10-26 晚体检新发现（未修复，只留了开关与量具）**：
  1. **注入多样性 U9 已实测否决（别再做第 2 次）**：83 条里 67 条出现「同一文档占 ≥2 位」，
     但 A/B（配额前置 + 扩召回）证明这是**相关性的自然结果**：
     | 组合 | top1 | 平均来源 | **注入相关率** | 同源堆叠 | p95 |
     |---|---|---|---|---|---|
     | 现状 | 59/61 | 1.458 | **78.6%** | 67 | 3.888s |
     | 配额2+末端1 | 59/61 | 2.072 | **56.2%** | 37 | 4.423s |
     | 配额2+末端2 | 59/61 | 1.759 | 66.8% | 63 | 4.306s |
     | 配额3+末端1 | 59/61 | 1.952 | 60.8% | 46 | 4.113s |
     多样性涨了，但**每 2 条里近 1 条变成不相关资料**、p95 +0.53s、9 例从 3 条掉成 2 条，
     top1 零收益。判据是**注入相关率，不是多样性**。开关保留默认关，A/B 可复现：
     `scripts/ab_source_quota.py`。
  1b. 附带发现：融合后候选池有时只有 **3 条**（`fused_k=3`，两路命中同几片）→ 登记 U11。
  2. **`relevance_floor` 边缘**：61 条库内题实测 **0 条被拒成 0 注入**（fl-5 的结论有了全量口径）；
     但手写用例「快排的时间复杂度是多少」**不带 topic** 时 `top_score=-2.132`，被 `-2.0`
     拒掉（余量仅 0.132），带 topic 才勉强 -1.751。这类「短属性问句」不在 83 条评测集内 →
     要不要补进评测集属改金标准，参照 U6 单独一轮。
  3. 量具：`scripts/diag_floor_margin.py`（退出码 1 = 出现库内题 0 注入）。
- **XB-23 课堂资料长期化已落地（2026-10-26，待 A 复核）**：`POST /upload/teacher`
  加 `persist=session|classroom`（**默认 session，存量行为零变化**）。`classroom`
  走三层存储：原件 `backend/classroom_docs/{id}/` + 登记表 `classroom_documents`
  （`doc_hash` 去重 / 配额 / 软删）+ 派生索引 `backend/chroma_classrooms/
  classroom_{id}_docs`。`/chat` 加第三路纯向量注入（`origin=classroom`，k=3），
  失败一律降级为空。新增 `GET /classrooms/{id}/documents`、
  `DELETE /classrooms/{id}/documents/{doc_id}`、`scripts/purge_classroom_docs.py`。
  **实测**：上传 187 片子；同课堂学生 sources 命中 3 条、同一学生换课堂 0 条
  （隔离）；重启后仍命中（持久化）；删除后立刻失效且空库被清；
  `chroma_kb` 仍只有 `cs_basics` 248 片 / `bbe0cdc4`，L1 top1 68/71 无退化。
- **两个上传入口（别搞混）**：`POST /upload` 学生用、`POST /upload/teacher` 教师用。
  前者恒为**会话级内存库**（不落盘、不进 `chroma_kb`，故不影响 L1 基线）；
  后者按 `persist` 分两条：`session`（默认）= 会话级内存库，
  `classroom` = 课堂级持久库（XB-23，见上一条）。
  教师入口走 `_require_teacher()` + 独立限流桶 10/h。
  **任何情况下都不要写进 `cs_basics`**（会让 83 题金标准与指纹失效）。
- **知识库增量更新已落地（2026-10-26）**：日常更新走
  `python scripts/update_kb.py`（文件级「按 source 整篇删+插」，非切片级 upsert），
  `--dry-run` 只看不动、`--full` 强制全量；`build_kb.py` 保留为兜底。
  切片 metadata 带 `doc_hash` + `chunk_params_hash`，状态存库内、不引 docstore。
  `scripts/verify_incremental.py` 验证「增量 ≡ 全量」，7 场景全 PASS，
  **改切片或索引逻辑后必跑**。生效方式：更新后调一次 `POST /admin/reload-kb`
  （XB-19），**不必重启**；不调也不报错，只是服务继续用旧数据。
  已知限制：换 embedding 模型必须手动 `build_kb.py`；判断热重载是否生效别用
  `collection.count()`（读 SQLite 元数据，永远返回最新值），要用真实检索。
  ⚠️ 切片模式**不用手动指定**：脚本按库内 `chunk_params_hash` 自动推断
  （`infer_chunk_mode()`）。此前不带 `--parent-child` 会被误判成「切片参数已变更」
  而悄悄退化成全量重建，2026-10-26 已修并加了 7 例单测守着。
  `/admin/reload-kb` 的鉴权（XB-20）：默认只放行本机来源；配了
  `ADMIN_RELOAD_TOKEN` 就必须带 `X-Admin-Token`（本机也一样）。反向代理后面部署时
  若代理在本机会被判成「本机」而放行，**那种部署必须配令牌**。
- **提示词工程已盘点并收尾（2026-10-25）**：`docs/specs/AGENT_PROTOCOL.md` §5 Prompt-1~7
  逐条核对——6 条合规、1 条偏离（`RAG_GROUNDING_RULES` 长在 app.py）。
  偏离项已修：**XB-18** 把该常量文本一字未动搬入 `agent/prompts.py`，app.py 改
  import（命名空间仍可访问，向后兼容），`tests/test_app.py` 的 import 改指事实源。
  纯搬迁行为零变更，无需重跑 L3/L4。smoke_chat 四轮剧本已固化为
  `tests/test_prompt_regression.py`（mock LLM 回放，无额度，变异验证过）。
  回答阶段的 RESPOND/CONGRATS 仍内联 app.py——历史原因（Prompt-1 认可），
  要彻底归口另开 XB-n。
- **省略式追问功能已实测验证**（`scripts/diag_followup_hit.py`）：换库内确有内容的
  知识点（TCP 三次握手 / 死锁）跑真实 /chat，**5/5 PASS**（判据三重：注入了资料 +
  来自期望文档 + 注入正文确有目标知识点）。
- **逐条相关性下限已落地**（B 地盘 retriever.py）：`per_doc_floor` 默认 **-5.0**，
  `RAG_PER_DOC_FLOOR=off` 可关。与整题 `relevance_floor`（**-2.0**）是两层：
  整题挡「库外题硬塞资料」，逐条挡「库内题被塞别的主题的片」。
  阈值由切片级金标准量化选定（`scripts/diag_perdoc_floor.py`），不是拍的。
- **8000 服务（2026-10-26 01:30 重启）**：已吃 XB-15 新措辞与逐条下限，当前库带
  `doc_hash`。是**会话后台任务**起的，会话结束可能退出，重起照抄「环境」段的
  uvicorn 命令，**必须带 `RAG_PARENT_CHILD=1`**——服务侧 `CONFIG.parent_child`
  默认 False，漏了会拿扁平口径（final_k=4）读父子库，不报错但行为不一致。
  重起后第一条 /chat 会触发重排模型懒加载（p95 虚高约 +10s，实测 27.0s vs 稳态
  16.1s），**读延迟前先跑一条预热**。

**剩余事项**

- **（新，2026-10-26 下午）XB-23 方案 B 已实施完毕，待 A 复核**：改动落在
  `db/models.py`（`classroom_documents`）、`db/repository.py`、`init_db.py`、
  `agent/rag/indexer.py`（`build_index` 加 `persist_directory`/`metadata_extra`）、
  `agent/rag/classroom_store.py`（新）、`agent/rag/retriever.py`
  （`summarize_sources` 加 `classroom` 分支）、`app.py`（三处：上传/注入/删除）、
  `tests/test_classroom_docs.py`（23 例）、`scripts/purge_classroom_docs.py`。
  设计见 `docs/proposals/PROPOSALS.md`。
- **（新，最值得做）`kind=oos` 库外题集滞后**：4 条里 2 条已被扩库覆盖——
  「MySQL 索引为什么用 B+ 树」（`db/index-transaction.md`，sources=3）、
  「Java HashMap 线程安全吗」（`dsa/hash-table.md`，sources=1）。sources>0 是
  **命中不是误召回**，但任何「库外 0 注入率」口径读到 2/4 会恒假阴。
  做法：这 2 条 `kind` 改 `kb-interview` + 补 `expected` + 重刷 gains（`build_gold` →
  `refine_gold --force`），再补 2~3 条**真库外**题（Redis 持久化、CAP 定理仍真库外；
  可选 Kafka / Docker 镜像分层 / JVM GC）。**会重算 L1 金标准与 L4 分母，单独一轮做。**
- **（新，可选）把 p95 压回 3s 内**：重排**用 child 文本打分**、命中后再映射回 parent
  注入——打分成本按 200 字算，注入粒度不变。属改动不是调参，需重建基线。
- ~~（A 地盘）增量更新后免重启~~ **已落地为 XB-19（2026-10-26）**：`app.py` 新增
  `reload_knowledge_store()` + `POST /admin/reload-kb`。增量更新后调一次即可，
  不必重启。生产库上真验过：加一篇文档 → 增量 → 服务不重启 → 热重载前检索不到、
  热重载后注入来源正是该篇。**待 A 复核**（新增管理端点，越界 app.py）。
  注意两点：① 必须连 `_BM25_CACHE` 一起清，否则混合召回拿旧语料匹配新切片；
  ② ~~该端点无鉴权~~ **已由 XB-20 解决**：默认只放行本机来源，配置了
  `ADMIN_RELOAD_TOKEN` 后改为校验 `X-Admin-Token`。
- **hetero_judge 异源裁判**（P4 唯一余项）：裁判与生成同为 deepseek-flash，  
  有自评偏置。切 `EVAL_JUDGE_MODEL` 前需 A 指定可用的异源 key/模型。
- **待 A 复核（四项，都是 app.py 行为变更，已实测达标）**：XB-15
  （`RAG_GROUNDING_RULES` 提利用率措辞）、XB-18
  （该常量归口 prompts.py，纯搬迁）、XB-19
  （知识库热重载 + 新增 `POST /admin/reload-kb`）、XB-20
  （该端点加鉴权：默认只放行本机来源，`ADMIN_RELOAD_TOKEN` 可开令牌模式；
  实测 `--host 0.0.0.0` 暴露下用局域网 IP 访问返回 403）。
  复核互相独立，可一起过。
- ~~HTTPS 性能开销内容缺口~~ 已于 2026-10-25 补章节并重建库，本条关闭。

**环境（照抄即可用，不要 pip install——必失败，环境是整体搬运的）**

- 后端目录：`Tutor-Chatbot-main/backend/`，venv 可用
- 起服务（**必须 unset 代理**，否则 /chat 502；**必须带 RAG_PARENT_CHILD=1**，
  否则拿扁平口径读父子库）：  
  `env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy RAG_PARENT_CHILD=1 ./venv/Scripts/python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000`
- 跑测试：同前缀 + `./venv/Scripts/python.exe run_tests.py`
- 增量更新知识库：`./venv/Scripts/python.exe scripts/update_kb.py --dry-run`
- 增量后热重载（免重启）：`curl -X POST http://127.0.0.1:8000/admin/reload-kb`
- 验证增量 ≡ 全量：`./venv/Scripts/python.exe scripts/verify_incremental.py`
- 跑检索评测：`./venv/Scripts/python.exe scripts/smoke_rag.py --compare`（带指纹校验）
- 缺口报表：`./venv/Scripts/python.exe -m eval.kb_gap_report`
- fl-5 复现：`./venv/Scripts/python.exe scripts/diag_floor_scores.py`
- MySQL 3306 常驻（库 `ai_classroom`）；`.env` 里有 DeepSeek key，**勿打印、勿提交**
- 跑 /chat 或重排时不要并发跑别的 CPU 密集任务，否则 p95 被污染

**地盘与硬约束（违反会返工）**

- 只动 B 地盘：`backend/eval/`、`backend/tests/`、`backend/scripts/`（smoke/verify/一次性工具）、  
  `build_kb.py`、`agent/rag/`、`knowledge/`、`rag_eval_cases.json`、`docs/specs/RAG_HANDBOOK.md`；  
  改 `app.py` / `agent/prompts.py` / 前端**必须先在 `docs/worklogs/B_WORKLOG.md` 登记 XB-n**
- 引用检索分数必须带题集（68/71 指 75 条评测集，与 7/7、35/36 不可比）
- 降级请求的耗时不许混进 p95（用 `eval/slo_check.py`，只判无降级子集）
- nDCG 必须 ∈[0,1]；**没跑的评测层在报告里必须是 `None`，不能填占位 dict**
- `smoke_rag.configure()` 改的是模块级全局 CONFIG，**跑完 hybrid 要恢复 full**
- 评测集 expected 必须写完整 topic 名；topic 匹配归一化统一 `s.replace(" ", "").lower()`  
  （build_gold / smoke_rag / chunk_quality / kb_gap 同一把尺子，**不自造第二套**）
- **改产品行为 / 提示词措辞必须同步评测判定端与门禁**（XB-10 / XB-13 / XB-14 三次验证；  
  XB-15 自查结论：MATERIAL_UTILIZATION_PROMPT 无关键词耦合，无需改词表）
- 相关性过滤是**两层**：整题 `relevance_floor`（**-2.0**）挡「库外题硬塞资料」，
  逐条 `per_doc_floor`（-5.0，`RAG_PER_DOC_FLOOR=off` 可关）挡「库内题被塞别的主题的片」。
  旧结论「只允许整题判定、逐片裁剪有退化」是阈值 -1.0 时代的实测，**已被 -5.0 重测推翻**
  （-1.3pp Recall@4 换 +6.2pp Precision@4，量化见 `scripts/diag_perdoc_floor.py`）；
  该脚本已适配父子单元（先聚合再打分），父子口径下 -5.0 砍 13 片真垃圾 / 误伤 1 片。
- 不改 `agent/prompts.py` 三个生产 prompt、不改 `agent/state.py:HINT_STRATEGIES`、不改 SSE 协议
- chroma `get(include=["embeddings"])` 返回 ndarray，写 `or []` 会抛歧义 ValueError
- `cmd | tail` 后取 `$?` 量到的是 tail 的退出码 —— 测脚本退出码要直连或用 PIPESTATUS
- 移动脚本文件的完整检查单：① sys.path 引导层级；② `__file__` 相对路径推导；  
  ③ 模块级 import 方的搜索路径；④ 用户可见报错消息里的路径（py_compile 抓不到这些，必须实跑）
- 调试「文件没写出来」先核对文件真实落点（BACKEND_DIR 类相对推导少一层就错位）

**收尾动作**

完成后更新 `docs/worklogs/B_WORKLOG.md` 与 `.workbuddy/memory/当日.md`；若本轮改了东西，同步重写本文件的  
「当前状态」段（本文件是下一个会话的唯一入口，状态写旧了会直接带偏一整轮）。  
按 `AGENTS.md` 的分阶段纪律主动汇报（改了什么 / 验证证据 / 未完成项 / 下一步）。