# NEXT_SESSION_PROMPT —— 新对话开场指令（v6.6，2026-10-26 02:55 更新，取代 v6.5）

> 用法：新开对话，把下面代码块里的内容整段粘贴发送即可。也可以只发一句  
> 「阅读 NEXT_SESSION_PROMPT.md 并按其中的任务清单执行」。  
> **v6.5 增量**：知识库 **20 篇 / 248 child**（新增 `cs/db/sql-query-join.md`），
> 基线指纹 **`sha256:bbe0cdc42635ac1c936d678ad22f3e8c`**；增量更新已在**生产库**实测
> （新增 1 篇：235→248，库内 0.35s，与全量重建指纹逐位等价，L1 无退化）；
> 修掉「不带 `--parent-child` 会误判全量」缺陷（新增 `infer_chunk_mode()`）；
> **XB-20 管理端点鉴权**已落地：`/admin/reload-kb` 默认只放行本机来源，
> 配 `ADMIN_RELOAD_TOKEN` 后改校验 `X-Admin-Token`（实测 0.0.0.0 暴露下局域网访问 403）；
> XB-21 方案 A 已落地：新增 `POST /upload/teacher`（教师备课素材，独立限流 10/h +
> `_require_teacher()` 校验，会话级内存库，实测不污染共享库）；
> 测试 **529/529**。详见 `RAG_HANDBOOK.md` §2、§8 第六七部分、`B_WORKLOG.md` 末尾。  
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

1. `HANDOFF_RAG_LIBRARY.md` —— **资料库/检索这条线的交接总览**（现状数字、六条决策、
   未决事项、下一步分档、硬约束、自查清单）。先看它，能省一轮。
2. `AGENTS.md` —— 项目全局指令（只管分阶段总结纪律）
3. `B_WORKLOG.md` —— 只看 2026-10-24 ~ 2026-10-26 的记录（XB-13~22 与增量实测全在这里）
4. `.workbuddy/memory/2026-10-24.md` ~ `2026-10-26.md` —— 逐节流水账，**踩坑教训最全**
5. `RAG_HANDBOOK.md` —— RAG 现状 / 口径 / 硬约束 / 维护命令（§8 = 增量更新，必看）

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
  RAG 文档在项目根 `RAG_HANDBOOK.md` + `PROPOSAL_RAG_SCALE.md`（未触发提案）。
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
- **测试基线已更新**：`run_tests.py` **523/523**（500 的基础上，2026-10-26 下午
  XB-23 课堂资料新增 `tests/test_classroom_docs.py` **23 例**）。
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
- **提示词工程已盘点并收尾（2026-10-25）**：`AGENT_PROTOCOL.md` §5 Prompt-1~7
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
  设计见 `PROPOSAL_TEACHER_MATERIALS.md`。
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
  `build_kb.py`、`agent/rag/`、`knowledge/`、`rag_eval_cases.json`、`RAG_HANDBOOK.md`；  
  改 `app.py` / `agent/prompts.py` / 前端**必须先在 `B_WORKLOG.md` 登记 XB-n**
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

完成后更新 `B_WORKLOG.md` 与 `.workbuddy/memory/当日.md`；若本轮改了东西，同步重写本文件的  
「当前状态」段（本文件是下一个会话的唯一入口，状态写旧了会直接带偏一整轮）。  
按 `AGENTS.md` 的分阶段纪律主动汇报（改了什么 / 验证证据 / 未完成项 / 下一步）。
