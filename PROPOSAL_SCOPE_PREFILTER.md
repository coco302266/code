# 单库 + `scope` 元数据 + 检索前 pre-filter：设计与改动评估

> 2026-10-26 ｜ 状态：**设计完成，未实施**（等触发条件）
> **修订（12:10）**：补入规模实测（§九）。结论由"全塞一个 collection"修正为
> **两库分层**——共享库仍独立且**不加过滤**，`scope` 过滤只用于课堂池。
> 依据：Chroma 带 `where` 的检索耗时随过滤子集大小线性增长（§9.3），全库方案会把共享库拖慢 50 倍。
> 结论先行：**改动属于"中等"，约 180 行改动 + 60 行新脚本 + 35~45 例单测；但当前规模不该做。**
> 该方案的真正成本不是"写多少代码"，而是引入一个**不报错的静默泄漏面**（漏传 filter 就跨课堂泄漏），
> 以及**课堂语料会通过 BM25 的 IDF 反过来影响共享库的排序**——这是单库方案的固有耦合，
> 多 collection 方案没有这个问题。

---

## 一、目标与边界

**要解决什么**：把「共享库 / 课堂库 / 会话库」三层资料放进**一个 Chroma collection**，
靠元数据 `scope` 区分归属，检索时用 `where` 做**检索前过滤（pre-filter）**，
从而避免 collection 数量随课堂数线性增长。

**不解决什么**：不改变"写入要分层、审核才晋升"的内容治理口径；
不改变 `/chat` 的请求协议；不改变金标准与评测口径。

---

## 二、数据模型设计

### 2.1 `scope` 取值

| scope 值 | 含义 | 写入方 | 可见范围 |
|---|---|---|---|
| `shared` | 共享教材库（`knowledge/cs/*.md`） | `build_kb.py` / `update_kb.py` | 所有人 |
| `classroom:<id>` | 课堂资料 | 教师上传（方案 B） | 该课堂成员 |
| `session:<sid>` | 会话级上传件 | `/upload`、`/upload/teacher` | 仅本次会话 |

**不要用 `classroom_1` 这种下划线拼接**：`classroom:1` 带冒号是有意的——
冒号在文件名/路径里不常见，后续若要把 `scope` 直接映射到目录名不会与 `_` 分隔的
复合名混淆；且解析 `scope.split(":", 1)` 比正则可靠。

### 2.2 元数据 schema（在现有字段上新增 3 个）

```python
{
    # —— 现有字段（不变）——
    "subject": "cs",
    "category": "os",
    "topic": "死锁的必要条件",
    "source": "os/deadlock.md",          # 相对 knowledge/cs 的路径
    "doc_hash": "a1b2c3d4e5f6a7b8",      # 源文件 sha256 前 16 位
    "chunk_params_hash": "…",            # 切片参数快照
    # 父子模式才有：
    "parent_id": "os/deadlock.md#2",
    "parent_text": "…",
    "parent_title": "…",
    "parent_start": 1200,

    # —— 新增（scoped 版）——
    "scope": "shared",                   # 检索过滤的唯一依据
    "scope_kind": "shared",              # shared | classroom | session（冗余但便于统计）
    "owner_id": 0,                       # 课堂/会话的归属 id；shared 记 0
}
```

`scope_kind` 是冗余字段，为的是让「按类型统计」（如"课堂资料一共多少片"）不必解析字符串。

### 2.3 唯一性：`source` 必须带 scope 前缀，否则 `parent_id` 会撞

现状 `parent_id = f"{source}#{idx}"`（`indexer.py:195`），`source` 是相对路径。
两个课堂各传一份 `讲义.md` → 两边 `source`、`parent_id` 完全相同 →
**父子聚合会把 A 课堂的 parent 当成 B 课堂的重复项丢掉**，且 `_dedupe(("source","start_index"))`
也会把它们当同一条。这是单库方案最隐蔽的正确性陷阱。

**做法**：课堂/会话资料的 `source` 前缀化（`classroom:7/讲义.md`），共享库 `source` **保持原样**
（这一点很关键，见 §五：`source` 参与指纹）。

---

## 三、检索设计

### 3.1 过滤点：必须在 ANN 阶段，不能检索后再筛

Chroma 1.5.9 实测（`langchain_chroma` 的 `filter=` 直通 `query(where=...)`，`vectorstores.py:780`）：

| 探针 | 结果 |
|---|---|
| 无过滤，查询向量与 `classroom_2` 某片完全一致 | top1 = 那一片（**证明不过滤就跨课堂召回**） |
| `where={"scope":{"$in":["shared","classroom:1"]}}` | 返回 5 条，`classroom:2` **被排除** |
| `where={"scope":"classroom:1"}` | 只返回该课堂 2 条 |
| `$and`：`scope $in` + `source $in` | 正常（2 条） |
| 元数据**没有** `scope` 的旧切片 | 带过滤时**被排除**（故迁移必须回填） |

结论：`where` 是 ANN 前的子集过滤，**不会出现"先召回 6 条再筛掉一半"的信息损失**。

### 3.2 签名改动

```python
def retrieve_knowledge_docs(
    vectorstore: Chroma,
    query: str,
    k: int | None = None,
    topic: str = "",
    trace: dict | None = None,
    scopes: list[str] | None = None,      # 新增：None = 不过滤（兼容现状）
) -> list[Document]:
```

内部：

```python
def _scope_where(scopes: list[str] | None) -> dict | None:
    """纯函数：把 scope 列表翻成 Chroma where。空/None → None（不过滤）。"""
    if not scopes:
        return None
    if len(scopes) == 1:
        return {"scope": scopes[0]}
    return {"scope": {"$in": list(scopes)}}
```

然后 `vectorstore.similarity_search(search_query, k=coarse_k, filter=_scope_where(scopes))`。

`scopes=None` 时行为与现在**完全一致**——这是零回归的兼容开关，也是分阶段上线的抓手。

### 3.3 BM25 怎么办（这是全案最容易做错的一处）

`_bm25_search`（`retriever.py:422`）现在是**全局打分后取 top-k**，没有任何过滤。
两个错误做法：

- ❌ **按 scope 各建一份 BM25**：IDF 在不同语料里不可比，小课堂语料的 IDF 噪声极大；
  而且缓存键要从 1 个变成 N 个，内存与冷启动都翻倍。
- ❌ **先取全局 top-k 再按 scope 筛**：正确切片被其他课堂的片挤掉，**召回确定性下降且不报错**。

✅ **正确做法：全局只建一份 BM25（IDF 稳定、缓存只要一份），
打分后先在候选下标上按 scope 过滤，再取 top-k**：

```python
def _bm25_search(vectorstore, query, k, scopes=None):
    bm25, docs, doc_scopes = _get_bm25_store(vectorstore)   # 第三个返回值：与 docs 对齐的 scope 数组
    if bm25 is None or not docs:
        return []
    tokens = _tokenize(query)
    if not tokens:
        return []
    scores = bm25.get_scores(tokens)
    allowed = None if not scopes else set(scopes)
    order = sorted(
        (i for i in range(len(docs)) if allowed is None or doc_scopes[i] in allowed),
        key=lambda i: scores[i],
        reverse=True,
    )[:k]
    return [docs[i] for i in order if scores[i] > 0]
```

打分本身是 O(N) 的全量计算（不管过滤与否都要算），所以**多这一步过滤不增加计算量**，
只增加一个下标比较。缓存仍然是"collection 名 → 一份索引"，不需要按 scope 拆分。

> ⚠ **固有耦合**：全局 IDF 意味着某个课堂塞进几千片后，会**轻微改变共享库的 BM25 排序**。
> 这属于"单库方案的代价"，不是 bug。缓解是给教师上传设单片数上限并监控 `scope` 分布。
> 多 collection 方案不存在这个耦合——这是两者最实质的差别，也是我在
> `PROPOSAL_RAG_SCALE.md` §7 之外新增的取舍依据。

### 3.4 父聚合与去重

- `aggregate_parent_docs`（`retriever.py:509`）按 `parent_id` 去重 → §2.3 的 `source` 前缀已保证唯一，函数本身**零改动**。
- `_dedupe(docs, ("source","start_index"))` → 因 `source` 带前缀，**无需改键**。
  若走"source 不带前缀 + 只加元数据"的路线，这里必须改成 `("scope","source","start_index")`。

### 3.5 两层 floor 与重排

`relevance_floor` / `per_doc_floor` 作用在重排之后，**与过滤无关，零改动**。
但要注意口径：课堂资料与教材的分数分布不同，共用一套 floor 可能让课堂资料被整批滤掉。
上线后必须用真实问答核对"课堂资料能否稳定进入注入"，不能只看 L1（L1 只测共享库）。

---

## 四、写入与迁移设计

### 4.1 增量脚本（`scripts/update_kb.py`）

现有 `read_library()` 读**全表** metadatas 来比对源文件差异。单库后表里混着课堂资料，
不按 scope 过滤会**把课堂文件当成"已删除的共享文件"**，触发大批 `delete(where={"source": ...})`。
这是第二个静默破坏点。

```python
raw = store.get(include=["metadatas"], where={"scope": "shared"})   # 只读共享层
```

删除时说清 scope：

```python
store._collection.delete(where={"$and": [{"scope": "shared"},
                                        {"source": {"$in": sources}}]})
```

`needs_full_rebuild` 的判据不变（`chunk_params_hash` 比对面仍然只取 `scope=shared` 的片）。

### 4.2 迁移：给现有 248 片回填 `scope="shared"`

**已实测**：Chroma 1.5.9 的 `collection.update(ids=…, metadatas=…)` 是**合并**语义，
不会覆盖 `parent_text` / `parent_id`（旧版本是整体替换，会毁掉父子检索，务必按版本确认）：

```
update 后 keys = ['parent_id', 'parent_text', 'scope', 'source']
=> 结论：合并（安全）
```

且只传 metadatas → **不重算 embedding**，248 片耗时在 1 秒量级。

脚本骨架 `scripts/backfill_scope.py`：

```python
# 1) 取全库 ids + metadatas；2) 缺 scope 的批量（每批 200）update 成 shared
# 3) 打印回填前后 count、以及 get(where={"scope":"shared"}) 的片数，必须相等
# 4) --dry-run 只报数不写
```

**上线顺序**：先回填 → 再开过滤。反过来会在回填完成前把共享库自己查空。

---

## 五、与评测体系的关系（这是本方案能低成本落地的关键）

`eval/fingerprint.py` 的指纹是：

```python
sha256( 排序后的 (source, start_index, page_content) 三元组 )
```

**不含 metadata**。所以：

- ✅ 只加 `scope` 元数据 → **指纹不变、L1 基线可继续用**（`bbe0cdc4` / 248 片）
- ❌ 若给共享文的 `source` 加前缀 → 指纹会变（`source` 参与哈希）→ **必须重刷金标准**
- ⚠ 单库后 `collection_fingerprint()` 会把课堂资料也算进去 → **必须加 scope 过滤**，
  否则 L1 每次对比都被课堂内容搅乱：

```python
def collection_fingerprint(vectorstore, where=None):
    raw = vectorstore.get(where=where)     # 传 {"scope": "shared"}
    ...
```

结论：**共享库 `source` 保持不变**是本设计的硬约束；课堂资料的 `source` 才带前缀。

---

## 六、改动清单与工作量

| # | 文件 | 改动性质 | 估行数 | 风险 |
|---|---|---|---|---|
| 1 | `agent/rag/indexer.py` | `load_markdown_documents(scope="shared")` + `build_chunks` 注入 3 个字段 | ~12 | 低 |
| 2 | `agent/rag/retriever.py` | `_scope_where()` 纯函数 + 签名加 `scopes` + `similarity_search(filter=)` | ~25 | 低 |
| 3 | `agent/rag/retriever.py` | `_get_bm25_store` 返回 scope 数组 + `_bm25_search` 先滤后取 | ~15 | 中 |
| 4 | `eval/fingerprint.py` | `collection_fingerprint(where=None)` | ~6 | 低 |
| 5 | `scripts/update_kb.py` | 读/删按 `scope` 收窄，`$and` 删除 | ~35 | **中高** |
| 6 | `scripts/backfill_scope.py`（新） | 回填脚本 + dry-run | ~60 | 低 |
| 7 | `app.py` | `/chat` 传 `scopes=["shared","classroom:<id>"]`；教师上传改写单库 | ~30 | 中 |
| 8 | `tests/` | scope where 构造 / 过滤后排序 / 跨 scope 不泄漏 / 增量只删本层 / 指纹按层稳定 | 35~45 例 | — |

**合计：约 180 行改动 + 60 行新脚本 + 35~45 例单测。**
按本项目节奏（含回归 500 例 + L1 复跑）估 **3~5 个工作日量级**，不是"改个参数"。

### 分档评估（真正该看这张表）

| 档 | 范围 | 工作量 | 收益 | 建议 |
|---|---|---|---|---|
| **① 只加元数据** | `indexer` 写 `scope` + 回填脚本，**检索不动** | 0.5 天 | 为将来切换铺路；指纹不变、零回归 | **现在可做** |
| **② 共享层内部过滤** | 加 `scopes` 参数但只传 `["shared"]` | 1~1.5 天 | 语义显式化，防误查 | 随①一起做 |
| **③ 单库统管课堂库** | 全案（含课堂写入 + 并行召回） | 3~5 天 + 评测重跑 | 省掉 collection 爆炸 | **等触发条件** |

---

## 七、风险清单（按严重度）

1. **静默跨课堂泄漏（最高）**：忘传 `scopes` → 默认 `None` → 不过滤 → 学生看到别班资料，
   **不报错、测试也可能全绿**（因为返回的文档本身合法）。
   缓解：① 端点上强制传参，`None` 只在内部单测里用；② **检索后断言**——
   逐条校验 `doc.metadata["scope"] ∈ 允许集合`，不符直接 `raise`，
   把静默泄漏变成可见异常（这也是主流多租户方案的标准兜底）。
2. **增量脚本误删（高）**：`read_library` 不按 scope 收窄 → 把课堂文件当"已删除的共享文件"删掉。
   缓解：单测覆盖"库里有课堂资料时，共享层增量一个 delete 都不发"。
3. **回填前开过滤（中）**：共享库自己被查空，表现为"知识库命中 0 但库里有 248 片"。
   缓解：回填脚本末尾自校验（`get(where={"scope":"shared"})` 片数 == 回填前总数）。
4. **IDF 耦合（中，固有代价）**：课堂语料变多会轻微改变共享库 BM25 排序。
   缓解：设单片数上限 + 监控 scope 分布；若真成问题，退回多 collection。
5. **floor 口径不适配（中）**：课堂资料分数分布与教材不同，可能被整批滤掉。
   缓解：上线后用真实问答核对注入情况，别只看 L1。
6. **`parent_id` 撞车（中）**：`source` 不前缀化就撞；已由 §2.3 设计规避，但必须单测守住。
7. **旧版本 `update` 覆盖元数据（低但危险）**：本项目 1.5.9 是合并语义，
   换版本要重测，否则回填会毁掉 `parent_text`。

---

## 八、结论与建议

**改动是否很大？** 中等偏大：约 180 行改动 + 60 行脚本 + 35~45 例单测，3~5 天量级，
且必须重跑 L1（`gold_index_fingerprint` 需按 scope 收窄后确认仍为 `bbe0cdc4`）。

**现在该不该做？** 不该。当前 20 篇 / 248 片 / 课堂数量极少：
- 多 collection 方案（方案 B）**代码量更小、撤销更干净、无泄漏面**，完全够用；
- 单库方案的全部收益（省掉 collection 爆炸）**只有在课堂数上到几十~上百时才兑现**；
- 而它的成本（静默泄漏面 ×2 处、IDF 耦合、评测要按 scope 收窄）**从第一天就开始付**。

**该做的那一半现在就做**：只加 `scope` 元数据（档①）。
指纹不变、检索不变、零回归，成本半天；等触发条件到了，切档③只需加过滤，
不必重写切片与增量逻辑——**把不可逆的迁移成本前置，把有风险的检索改动后置**。

**触发条件（满足任一即切档③）**：
1. 课堂数 > 20~30（collection 管理、BM25 冷启动、清理逻辑开始成为负担）；
2. 需要"一次检索跨多个课堂"（如教师看多班对比）——多 collection 要 N 次检索再合并；
3. 需要按 scope 做全局统计/巡检（`get(where=...)` 一次拿到，多库要挨个查）。

---

## 附：本文结论的实测依据

全部在本机 Chroma 1.5.9 + `langchain_chroma` 1.1.0 上跑过（临时探针，已删除）：

| 验证点 | 方式 | 结果 |
|---|---|---|
| pre-filter 在 ANN 前生效 | 让某课堂片与查询向量完全一致，带 `$in` 过滤 | 该片被排除 → 过滤在召回前 |
| `$in` / `$and` 可用 | 组合 scope + source | 正常返回交集 |
| 无 `scope` 的旧片会被过滤掉 | 混入一条无 scope 的片 | 带过滤时查不到 → 必须回填 |
| `filter=` 直通 `where=` | 读 `langchain_chroma/vectorstores.py:780` | `where=filter` 在 `query` 调用里 |
| `update` 是合并语义 | 只传 `{scope}`，回读 keys | `parent_text`/`parent_id` 保留 |
| 指纹不含 metadata | 读 `eval/fingerprint.py` | 只哈希 `(source, start_index, page_content)` |

---

## 九、规模实测与架构修正（2026-10-26 12:10 补）

> 上下文：A 明确「预期后面规模会很大」。前八节都是在 20 篇 / 248 片的数据上做的定性设计，
> 本节补上用**目标规模**跑出来的数字，并据此修正架构结论。
> 环境：本机 Chroma 1.5.9 / jieba 0.42.1 / rank_bm25 / 384 维向量，临时基准脚本已删除。

### 9.1 BM25 的成本随规模线性恶化（这是全案的真正瓶颈）

用与线上同一份 `_tokenize`（jieba + 小写归一）和 `BM25Okapi`，构造每片约 100 token 的语料：

| 切片数 | jieba 分词 | BM25 建索引 | 一次打分 | **冷启动合计** | 常驻内存 |
|---|---|---|---|---|---|
| 5 000 | 7.79 s | 0.19 s | 17 ms | **8.0 s** | 20 MB |
| 20 000 | 19.35 s | 0.77 s | 49 ms | **20.1 s** | 81 MB |
| 50 000 | 46.85 s | 1.86 s | 144 ms | **48.7 s** | 203 MB |

（内存为含语料 token 列表的上界，约 4.2 KB/片；线上 `_BM25_CACHE` 还额外持有 `docs`，
其中每片的 `parent_text` 约 3 KB，两万片再加约 60 MB。）

**读法**：BM25 建索引本身不慢（50k 片只要 1.86 s），**慢的是分词**（46.85 s，占 96%）。
而 `_BM25_CACHE` 是「collection 名 → 一份索引」的整份重建机制 ——
**只要课堂池里任何一篇文档被写入，整份索引就要重建**。

于是纯单库方案在 2 万片时会出现一个致命现象：
**教师上传成功 → 缓存失效 → 下一个学生提问要多等 20 秒**（5 万片时 49 秒）。
本项目 p95 门禁是父子 ≤5 s，这会直接击穿，而且表现为「偶发一次极慢」，
排查时极难归因到「半小时前有位老师传了份讲义」。

### 9.2 Chroma 带 `where` 的检索路径：不是走图，是扫子集

20 000 片 / 384 维，同一个查询向量，只改过滤条件，各跑 10 次取均值：

| 过滤条件 | 平均耗时 | 返回 |
|---|---|---|
| 不过滤（全库） | **3 ms** | 6 条 |
| `scope=shared`（约 50% 子集） | **147 ms** | 6 条 |
| `$in[shared, classroom:1]`（约 51%） | **171 ms** | 6 条 |
| `$in[shared, 5 个课堂]`（约 55%） | **173 ms** | 6 条 |
| `scope=classroom:7`（约 1%） | **29 ms** | 6 条 |

**关键现象**：不带过滤恒为 3 ms，带过滤后耗时随**过滤子集大小**线性增长
（1% → 29 ms，50% → 147 ms），而不是随结果条数变化。
说明带 `where` 时走的不是 HNSW 图搜索，而是**对满足条件的子集做扫描**。

这条实测推翻了一个常见的想当然：「加了 filter 只是多一层 SQL 条件，检索还是走索引」。
不是。**过滤的代价 = 子集大小 × 单条距离计算**，与库总量成正比。

### 9.3 架构修正：两库分层，`scope` 只用于课堂池

把 9.2 的结论代进来，纯单库（共享 10 万 + 课堂 10 万塞一个 collection）会在**每次提问**时
扫 20 万条中的 10 万条 —— 按 147 ms / 1 万条外推约 **1.5 s 起步**，且随规模继续恶化。
这等于为了省掉「collection 数量」而牺牲「每一次检索」。

修正为**三个物理集合，职责不同**：

| 集合 | 内容 | 是否加过滤 | 检索路径 | 写入频率 |
|---|---|---|---|---|
| `kb_shared` | 教材（`knowledge/cs/`） | **不加** | HNSW 图搜索，3 ms 量级 | 极低（人工审核后才动） |
| `kb_classroom` | **所有课堂**资料 | 加 `scope` 过滤 | 子集扫描，1% 选择性下 29 ms | 高（教师自由上传） |
| 会话级（内存） | 单次上传件 | 不加（collection 本身隔离） | 纯向量 | 每次上传 |

这么分的收益是**三条同时成立**，而纯单库只能拿到第一条：

1. **collection 数量不随课堂增长**（1 个池装 N 个课堂）——这是 A 想解决的问题，做到了；
2. **共享库检索不被过滤拖慢**（不加 filter，永远走 HNSW）——纯单库做不到；
3. **共享库的 BM25 与评测基线物理隔离**（课堂写入不触发共享索引重建、不改指纹）——
   纯单库做不到，这也顺带消掉了 §3.3 说的 IDF 耦合。

BM25 也随之分成两份：`kb_shared` 一份（随教材变更重建，5 万片 49 s 但一年没几次），
`kb_classroom` 一份（随课堂写入重建，靠**异步预热**吸收，见 9.5）。

### 9.4 规模档位与推荐架构

| 总切片量 | 推荐架构 | 关键依据 |
|---|---|---|
| < 1 万（当前 248） | 一课一 collection 即可，不必上 scope | 方案 B 代码更少、撤销更干净；本方案收益为零 |
| 1 万 ~ 5 万 | **两库分层**（本章 9.3） | 课堂池过滤扫描 <100 ms；BM25 池化后冷启动 20 s 可异步吸收 |
| 5 万 ~ 20 万 | 两库分层 + **课堂池按学期分池** + BM25 异步预热 | BM25 冷启动 49 s 必须切成「每池 <10 s」 |
| > 20 万 | 向量库换 Qdrant / Milvus；**BM25 外置** ES / Meilisearch | Chroma 过滤走子集扫描、单机内存与单进程重建都不再适用 |

**这张表是本节最重要的一行**：`scope` 方案解决的是「**collection 数量**随租户增长」，
不解决「**数据量**增长」。后者要换引擎，是另一个量级的改造（估 2~3 周，且要重做评测口径）。
所以如果「规模很大」指的是几十万片，现在做 scope 只完成了 40% 的功课。

### 9.5 成本重估（一次性 + 运行期）

**一次性改动**（相对 §六 上调，因为要包含 BM25 池化与异步预热）：

| 项 | 估时 |
|---|---|
| 元数据 + 回填脚本（§四） | 0.5 天 |
| 课堂池写入 + `scope` 过滤 + 检索后断言 | 1.5 天 |
| BM25 池化（shared / classroom 两份缓存，键带池名） | 1 天 |
| 写入后**异步预热**（复用现有 `bm25_unavailable` 降级路径） | 1 天 |
| `app.py` 接线（`/chat` 传 scopes、教师上传写池） | 1 天 |
| 单测 + L1 复跑 + 回归 | 1.5 天 |
| **合计** | **约 6~7 天** |

**运行期成本**（这部分才是「规模大了会不会疼」的答案）：

| 项 | 1 万片 | 5 万片 | 20 万片 |
|---|---|---|---|
| 课堂池过滤检索 | ~30 ms | ~150 ms | ~600 ms |
| 课堂池 BM25 冷启动（未预热时首查） | 8 s | 49 s | 不可用 |
| BM25 + docs 常驻内存 | ~120 MB | ~400 MB | ~1.6 GB |
| 需人工运维的动作 | 无 | 按学期分池 | 换引擎 |

### 9.6 对前八节结论的修订

- §3.3 说「全局一份 BM25 保 IDF 稳定」——**在老师只关心课堂池内部排序时仍成立**，
  但不再跨库共用；共享库与课堂池各一份，互不影响。
- §八 的「③ 单库统管课堂库」应读作「**课堂池**统管课堂库」，共享库不进池。
- 分档建议不变：当前只做**档①（加 `scope` 元数据 + 回填）**，成本 0.5 天、指纹不变、零回归；
  档③ 等触发条件。**新增一个触发条件**：课堂池预计超过 5 万片时，先做「按学期分池」再上线。
