# RAG 资料库分层与规模演进 —— 交接文档

> **给谁看**：接手「AI-Classroom 资料库 / 检索」这条线的下一位（人或 AI 会话均可）。
> **怎么用**：新开对话先贴一句「读 `HANDOFF_RAG_LIBRARY.md`」，再照 §8 自查一遍再动手。
> **最后更新**：2026-10-26 12:30 ｜ **时效**：本文是快照，§0/§2/§3 的数字都现场复核过；
> 过两周以上请照 §8 重新量，不要直接引用。

---

## 0. 30 秒速览

| 项 | 当前值 | 怎么核的 |
|---|---|---|
| 知识库源文档 | **20 篇** md（`knowledge/cs/{os,network,db,dsa}/`） | `ls knowledge/cs/*/` |
| 生产向量库 | `backend/chroma_kb/`，collection **只有 `cs_basics`** 一个 | `chromadb.list_collections()` |
| 切片 | **248 child / 81 parent**（`chunk_mode=parent-child`） | 数 `metadatas` 与 `distinct(parent_id)` |
| 基线指纹 | **`sha256:bbe0cdc42635ac1c936d678ad22f3e8c`** | `eval/fingerprint.load_baseline_meta()` |
| 评测集 | 83 条（L1 金标准 79 条计分） | 同上 `eval_cases_count` |
| **元数据里有没有 `scope`** | **没有**（`set(m['scope']) == {None}`）→ **档① 未做** | 读全库 metadatas |
| 测试基线 | `run_tests.py` **529/529** | 2026-10-26 下午实跑（500 + XB-23 23 例 + XB-24 6 例） |
| 课堂资料层 | `classroom_{id}_docs` 落 `backend/chroma_classrooms/`，原件落 `backend/classroom_docs/`，登记表 `classroom_documents` | `POST /upload/teacher` + `persist=classroom` |
| 教师端入口 | `/teacher` →「备课」页签顶部「课堂资料」卡片（上传 / 清单 / 删除） | 打开 3000 的 `/teacher` 看第一张卡 |
| 服务 | 8000 **在跑**（后端）、3000 **在跑**（前端静态导出 `frontend/out`）；5173 无监听 | `curl --noproxy '*'` 三个端口 |
| 本轮改了产品代码吗 | 改了：XB-23（`app.py` 三处 + `db/*` + `indexer.py` + 新 `classroom_store.py`）、XB-24（`app.py` 三处 + `classroom_store.py` + 前端 `api/types/chat` + `teacher/page.tsx`） | `B_WORKLOG.md` 末尾 |

**一句话现状**：资料库"三层口径"已经讲清楚、教师侧上传入口已通、`scope` 方案的
设计与规模实测都做完了，**但生产库仍是单 collection、无 `scope` 字段**——
下一任要么做"档① 加元数据"（半天、零回归），要么等触发条件再做档③。

---

## 1. 这一轮定下的六条结论（决策台账）

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

## 2. 本轮交付物清单

| 文件 | 类型 | 状态 | 证据 / 备注 |
|---|---|---|---|
| `app.py` `/admin/reload-kb` | **产品代码**（越界，已登记 XB-20） | ✅ 已落地 | `_client_ip`(1093) / `_is_loopback_client`(1099) / `_admin_gate`(1114) / 路由(1133)；加 `10/minute` 限流。实测 `--host 0.0.0.0` 下局域网 IP → 403，本机 → 200；配 `ADMIN_RELOAD_TOKEN` 后无 token 401 |
| `app.py` `POST /upload/teacher` | **产品代码**（越界，已登记 XB-21 方案 A） | ✅ 已落地 | `_index_upload`(308) 抽出共用；`_require_teacher`(1969) 校验 role；学生入口(2187) / 教师入口(2214)；教师独立限流 10/h；`DocumentSession`(244) 扩 `audience/teacher_id/classroom_id` |
| `PROPOSAL_SCOPE_PREFILTER.md` | 设计提案（新增） | ✅ 设计完成，**未实施** | §一~§八 设计 + §九 规模实测与架构修正 |
| `scripts/update_kb.py` `infer_chunk_mode()` | 工具修复 | ✅ 已落地 | 修掉"不带 `--parent-child` 被静默判成全量重建"；+7 例单测 |
| `tests/test_app.py` `TeacherUploadTests` | 测试 | ✅ +4 例 | 含"非教师被拒时 `build_index` 一次都不许调用" |
| `tests/test_update_kb.py` / `test_kb_hot_reload.py` | 测试 | ✅ +7 / +17 例 | 合计到 500 |
| `RAG_HANDBOOK.md` | 文档 | ✅ 已同步 | §2 库结构表加教师列、§8 第六/七部分、§9 更正结课口径 |
| `B_WORKLOG.md` | 文档 | ✅ 已同步 | 文末 XB-20 / XB-21 / XB-22（含补记） |
| `env.example` | 配置样例 | ✅ 已加 `ADMIN_RELOAD_TOKEN` | |
| `scripts/backfill_scope.py` | 回填脚本（档① 需要） | ❌ **不存在，未写** | 全文骨架见 `PROPOSAL_SCOPE_PREFILTER.md` §4.2 |
| `PROPOSAL_TEACHER_MATERIALS.md` | 设计提案（新增） | ✅ 设计完成 | 教师上传三条语义 + 方案 B 全套设计 |
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

## 3. 关键实测数字（引用前先看"适用边界"）

### 3.1 检索基线（L1，数据来自 `eval/rag_baseline.json`，父子 full 配置）

| 指标 | 值 | 口径 |
|---|---|---|
| top1 / top3 | **68/71** / **70/71** | 75 条评测集、71 条计分，**不是** 83 题全体 |
| nDCG@3 | 0.870 | 金标准 `topic-derived+llm-refined`，parent 粒度 79 条计分 |
| 提升度 | +0.060 | |
| p95 | 4.3 s | 父子预算 ≤5 s，**只算无降级子集** |
| 降级 | 0 | |

> 引用纪律：`68/71` 与 `7/7`、`35/36` **不可比**（不同题集）。任何检索分数报出来必须带题集。

### 3.2 规模实测（本轮新做，环境：本机 Chroma 1.5.9 / jieba 0.42.1 / 384 维）

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

### 3.3 六个已实测的 Chroma 语义（做档③ 前必须信任这六条）

| 验证点 | 结果 |
|---|---|
| `filter=` 直通 `query(where=...)` | 是（`langchain_chroma/vectorstores.py:780`）⇒ pre-filter 在 ANN 前 |
| 不过滤会不会跨课堂召回 | **会**（让某课堂片与查询向量完全一致，不过滤时它就是 top1） |
| `$in` / `$and` 组合 | 可用 |
| 无 `scope` 字段的旧片带过滤时 | **查不到** ⇒ 必须先回填再开过滤 |
| `collection.update(ids, metadatas)` 语义 | **合并**（`parent_text`/`parent_id` 保留）、不重算 embedding；换 Chroma 版本必须重测 |
| `eval/fingerprint.py` 哈希什么 | 只 `(source, start_index, page_content)`，**不含 metadata** ⇒ 只加 `scope` 不动指纹；给 shared 的 `source` 加前缀就会动指纹 |

---

## 4. 未决事项（按优先级，含卡点与归属）

| # | 事项 | 卡在哪 | 归属 | 建议 |
|---|---|---|---|---|
| U1 | **XB-15 / 18 / 19 / 20 四项待 A 复核**（都是 `app.py` 行为变更，已实测达标） | 等 A 签字 | A | 四项互相独立可一起过；U2 前先过 XB-20 |
| U2 | **XB-21 方案 B（课堂级 collection 长期生效）** | ~~待 A 定档~~ **已实施（XB-23，2026-10-26），待 A 复核** | A 复核 | 设计 `PROPOSAL_TEACHER_MATERIALS.md`；实现与实测见 `HANDOFF` §2 与 `B_WORKLOG.md` 末尾 XB-23 |
| U3 | **晋升审批人角色**（课堂资料 → 共享库，谁批） | 产品口径未定 | A / 产品 | 在此之前共享库只走"人工改 md + `update_kb.py`"，保持现状即可 |
| U4 | **反向代理部署必须配 `ADMIN_RELOAD_TOKEN`** | 已识别未落地 | 部署时处理 | 代理若在本机，`_is_loopback_client` 会判成本机而放行 → 必须开令牌模式 |
| U5 | `/upload`（学生入口）**无鉴权**，且 3/hour 是共享限流桶 | 同类问题，未登记 XB | A | 若要收，仿 XB-20 做法；教师桶已独立（10/h）不受影响 |
| U6 | `kind=oos` 库外题集滞后：4 条里 2 条已被扩库覆盖 | 改了要重算 L1 金标准与 L4 分母 | B | **单独一轮做**，别顺手改 |
| U7 | p95 压回 3s 内（重排改用 child 文本打分，命中后映射回 parent） | 属改动不是调参，需重建基线 | B | 可选 |
| U8 | `hetero_judge` 异源裁判（P4 唯一余项） | 需 A 提供异源 key/模型 | A | 阻塞在外部资源 |

---

## 5. 下一步动作（按触发条件分支，不要越级跳档）

### 分支 A —— 现在就能做（半天，零回归）

**档① 只加 `scope` 元数据 + 回填**，检索链路一行不动：

1. `agent/rag/indexer.py` 的 `build_chunks` 注入 `scope="shared"` / `scope_kind` / `owner_id`；
2. 写 `scripts/backfill_scope.py`（骨架见提案 §4.2，含 `--dry-run` 与**末尾自校验**：
   `get(where={"scope":"shared"})` 片数必须等于回填前总数）；
3. 跑 `update_kb.py` 全量重建或直接回填，然后核 **指纹必须仍是 `bbe0cdc4`**；
4. 加单测守住"共享层 `source` 不带前缀""`parent_id` 唯一"。

**为什么值得先做**：它把不可逆的迁移成本前置，把有风险的检索改动后置；指纹不变、L1 基线可续用。

### 分支 B —— 触发条件到了再做（6~7 天）

满足任一即启动档③「课堂池统管课堂库」（**共享库不进池**）：
1. 课堂数 **> 20~30**（collection 管理、BM25 冷启动、清理逻辑开始成负担）；
2. 需要"一次检索跨多个课堂"（教师看多班对比）——多 collection 要 N 次检索再合并；
3. 需要按 scope 做全局统计/巡检（`get(where=...)` 一次拿到）。

实施顺序（顺序错了会踩坑）：
**回填 → BM25 池化 → 课堂池写入 + `scope` 过滤 + 检索后断言 → 异步预热 → `app.py` 接线 → 单测 + L1 复跑**。
> 反了会怎样：先开过滤再回填 = 共享库自己查空（表现为"命中 0 但库里有 248 片"）。

### 分支 C —— 远期（2~3 周 + 重做评测口径）

课堂池预计 **> 5 万片** → 先"按学期分池"再上线；
总量 **> 20 万片** → 换引擎（Qdrant / Milvus）+ BM25 外置（ES / Meilisearch）。

> ★ **汇报口径纪律**：`scope` 方案解决的是「**collection 数量**随租户增长」，
> **不解决「数据量」增长**。若"规模很大"指几十万片，做 `scope` 只完成约 40% 的功课，
> 别说成"规模问题已解决"。

---

## 6. 硬约束（违反会返工，都是踩过的）

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
10. **改 `app.py` / `agent/prompts.py` / 前端必须先在 `B_WORKLOG.md` 登记 XB-n**。
11. **改产品行为或提示词措辞必须同步评测判定端与门禁**（XB-10/13/14 三次验证过）。
12. 相关性过滤是**两层**：整题 `relevance_floor`（-2.0）挡库外题硬塞，逐条 `per_doc_floor`（-5.0）挡库内串主题。

---

## 7. 环境与复现（照抄即可，**不要 `pip install`**，环境是整体搬运的）

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

## 8. 交接自查清单（**必做**，状态可能已变）

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

## 9. 文档地图（按"真相源"优先级）

| 想知道什么 | 读哪 | 优先级说明 |
|---|---|---|
| **代码到底怎么写的** | 代码本身 | **最高**，文档一律以代码为准 |
| 资料库三层口径 / 维护命令 / 硬约束 | `RAG_HANDBOOK.md`（§2 库结构、§8 增量更新、§9 方案 B 待办） | 运维主手册 |
| `scope` 方案的设计、实测、成本、触发条件 | `PROPOSAL_SCOPE_PREFILTER.md`（§一~§九） | **本次交接的核心**，含 9.1~9.6 规模修正 |
| 每个改动为什么改、改在哪、验证了什么 | `B_WORKLOG.md`（2026-10-24 ~ 10-26，重点 XB-13~22） | 决策过程与证据 |
| 下一轮开场指令与剩余事项 | `NEXT_SESSION_PROMPT.md`（v6.6） | 会话入口 |
| 逐日踩坑流水（教训最全） | `.workbuddy/memory/2026-10-2{4,5,6}.md` | 排查时翻 |
| 全项目视角 / 前后端契约 | `项目交接说明.md`、`API_CONTRACT.md` | 跨角色 |

---

## 10. 给下一任的三句话

1. **别急着写代码。** 当前规模（248 片 / 20 篇）做 `scope` 收益是零，先确认触发条件。
2. **数字都要现场量。** 本文所有性能数字都是本机单进程实测，且基准脚本已删；换机器要重量。
3. **静默失败是这条线的主旋律。** 漏传 filter → 跨课堂泄漏不报错；回填前开过滤 → 查空不报错；
   `count()` 判热重载 → 永远是最新值；判错切片模式 → 悄悄全量重建。
   改完一律用**真实检索**验证，不要只看日志或计数。
