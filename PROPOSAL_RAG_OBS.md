# 提案：把「本轮是否注入资料」做成可观测字段

> 目标：解决 D11（检索静默失败不可观测）。轮 2 那种「检索四条全错、模型用自有知识补上、
> 回答看起来完全正常」的情况，现在只能靠开 DEBUG 日志复现，事后无法追溯。
>
> 状态：**待审核，未动任何业务代码**。本文所有行号基于当前工作区。

---

## 1. 现状：时序与观测盲区

`/chat` 主流程（`backend/app.py`）：

```
 327  assessment_graph.ainvoke()        → topic / hint_level / misconception / resolved
 337  get_relevant_context(PDF)         → doc_context   （字符串）
 352  get_knowledge_context(KB)         → kb_context    （字符串）
 358  rag_context = 两者拼接            → 注入 system prompt
      ├─ topic == "unknown" 且 有 PDF → doc_stream     （不发 state）
      ├─ topic == "unknown" 且 无 PDF → unknown_stream （不发 state）
      └─ 正常                          → event_stream
 506    llm.astream() 逐 token
 515    _persist_assistant()
 534    yield state 事件  ← 流末最后一条
 538    yield [DONE]
```

三个观测盲区，直接决定方案怎么选：

| 盲区 | 说明 |
|---|---|
| **降级流没有观测点** | `doc_stream` 恰恰是「有上传文档时依据文档回答」的路径，**它注入了资料但不发 state**。把字段塞进 state，这条路径依然看不见。 |
| **流中途失败没有观测点** | `event_stream` 一旦抛异常走 `type=error`，state 就不发了。而检索早在 358 行就算完了——信息存在但没出口。 |
| **来源信息被格式化掉了** | `get_knowledge_context` / `get_relevant_context` 都**只返回格式化后的字符串**，来源只在字符串里（`｜来源：cs/network/http-https.md`）。要结构化来源必须动 retriever。 |

---

## 2. 三个方案

### 方案 A：往现有 `state` 事件里加字段

payload 增加 `rag_used: bool` 和 `rag_sources: [...]`。

- ✅ 改动最小，前端不改也能拿到
- ❌ **降级流盲区依旧**（doc_stream 不发 state）
- ❌ **流中途失败时丢失**
- ❌ 语义混杂：`state` 是教学状态机（topic/hint/resolved），检索供给是另一类信息
- ⚠️ 隐性风险：`ChatRequest` 是 `extra="forbid"`。现在前端只挑 4 个字段回传所以安全，
  但**将来谁把整个 state 对象回传，多余字段会让请求直接 400**

### 方案 B：新增独立 SSE 事件 `sources`（推荐）

检索完成后、**token 流之前**发一条：

```json
{"type":"sources","items":[
  {"origin":"kb","source":"cs/network/http-https.md","topic":"HTTPS 与 TLS 握手","category":"计算机网络"},
  {"origin":"kb","source":"cs/network/http-https.md","topic":"HTTPS 与 TLS 握手","category":"计算机网络"},
  {"origin":"upload","page":3,"lines":"12-40"}
]}
```

- ✅ **时序正确**：观测点就是「检索完成」那一刻，中途 error 也已经发出去了
- ✅ **无盲区**：三条流都发（`unknown_stream` 发 `{"items":[]}`，即"明确没有资料"）
- ✅ **向后兼容**：现有前端解析只有 `token` / `state` / `error` 三个分支，
  未知 `type` **静默忽略**，不会崩。后端可以先上，前端后接
- ✅ 不动 `state` 契约，D1 建立的不变量（收到 state ⇒ 真 state ⇒ 含 `personalized`）不受影响
- ❌ 要动 `retriever.py`（返回结构化来源）
- ❌ 前端要显示的话需加一个分支（C 的地盘）

### 方案 C：B + 落库

在 B 的基础上把 sources 存进 `messages` 表，让老师事后能查历史课。

- ✅ 唯一能「事后追溯」的方案（B 只能看当场）
- ❌ 要改表结构 + `save_message` 签名（db 层是 T3/A 的地盘），涉及迁移
- 建议**拆成第二阶段**，不跟 B 混在一起做

---

## 3. 推荐方案 B：具体改动清单

### 3.1 `backend/agent/rag/retriever.py`（B 地盘，无需报备）

**新增两个函数，不改变现有函数对外行为：**

```python
def retrieve_knowledge_docs(vectorstore, query, k=None, topic="") -> list[Document]:
    """把 get_knowledge_context 的检索主体抽出来，返回 Document 列表。
    现有函数改为调用它 + format_knowledge_docs，对外返回值一字不变。"""
    ...  # 现 290-317 行内容，去掉最后的 format

def get_knowledge_context(...) -> str:
    return format_knowledge_docs(retrieve_knowledge_docs(...))   # 行为不变

def retrieve_pdf_docs(vectorstore, query, k=5) -> list[Document]:
    """get_relevant_context 的检索主体。注意：去重逻辑要跟着搬过来，
    否则格式化后的条数会和现在不一样。"""
```

再加一个小工具：

```python
def summarize_sources(docs, origin: str) -> list[dict]:
    """Document metadata → 可 JSON 序列化的精简字典。
    只挑对外有意义的字段，不要把整个 metadata 泄出去。"""
```

**为什么不用「从格式化字符串里正则提取来源」的省事做法**：那等于把给模型看的文本
当接口用，格式一改就静默失效——正是 D8 讨论过的 hack 判据第 2 条（静默失效）。不推荐。

### 3.2 `backend/app.py`（A 地盘，**需报备**）

| 位置 | 改动 |
|---|---|
| 336-358 | 改成先拿 `kb_docs` / `pdf_docs` 列表，再各自格式化；`rag_context` 拼法不变 |
| 新增模块级函数 | `_source_items(kb_docs, pdf_docs) -> list[dict]`（约 12 行） |
| `event_stream` 开头（506 行前） | 加一行 `yield` sources 事件 |
| `doc_stream`（376 行前） | 加一行 yield（只有 upload 来源） |
| `unknown_stream`（413 行前） | 加一行 yield `{"items":[]}` |

净增约 20 行，**不改动任何现有逻辑分支**。

### 3.3 `API_CONTRACT.md`（A 地盘）

事件表加一行，并把向后兼容性写清楚：

| 事件 | 时机 | 载荷 |
|---|---|---|
| `sources` | **token 之前**（检索完成后立刻） | `{"type":"sources","items":[...]}` |

> 老前端不认识 `sources` 会静默忽略，不会报错。
> `items: []` 表示「本轮明确没有注入资料」，与「没收到事件」含义不同——
> 只有 `unknown_stream` 之外的异常路径才会完全没有这个事件。

### 3.4 前端 `frontend/app/components/TutorChat.tsx`（C 地盘，**可选**）

在 152 行 `state` 分支旁加一个分支（约 10 行），把 sources 挂到当前 assistant 消息上，
可选展示「依据 N 篇资料」角标。**不改也能正常跑**。

---

## 4. 要不要在 `state` 里也放一个布尔？

方案 B 之外可以追加：`state` 事件里加 `rag_used: bool`。

- 用途：让「教学状态」知道这轮有没有资料支撑（将来可用于策略，例如没资料时提示
  「以下为模型自有知识，建议核对」）
- 代价：state 契约变宽，且降级流仍覆盖不到（doc_stream 不发 state），会形成一个
  **时有时无的字段**，反而比不加更容易误用

**建议：先只做 B，不加这个布尔。** 等真有策略要用时再补，那时连同降级流一起设计。

---

## 5. 风险与回退

| 风险 | 应对 |
|---|---|
| 重构 retriever 引入回归 | `get_knowledge_context` / `get_relevant_context` 对外返回值**必须一字不变**；验收靠 `smoke_rag.py --compare rag_baseline.json`（top1 7/7）+ `run_tests.py`（90/90） |
| 新事件破坏老前端 | 已确认解析逻辑是三个独立 `if`，未知类型静默忽略 |
| 增加每轮 SSE 一条消息 | 可忽略（几十字节） |
| 回退 | 删掉 3 处 `yield` 即可，retriever 的重构可保留（无害） |

---

## 6. 待审核决策点

- [x] **选方案**：**B（独立 sources 事件）** —— 已于 2026-10-19 落地，登记 XB-11
- [ ] **是否带重排分数**：`items` 里加 `score` 字段便于排查质量，但要改 `_rerank_docs`
      让它连分数一起返回。注意 D10 结论：这个分数**只能看，不能当相关性门限**
- [ ] **前端是否同步展示**（需要 C 配合；不同步也能跑）
- [ ] **是否落库**（方案 C，需要 A 配合改表）

> **执行结果（2026-10-19）**：方案 B 已落地并实测，见 `B_WORKLOG.md` XB-11。
> 可观测性**只走事件层**。曾试过的提示词层 inline citation（XB-10）已按用户要求
> **从代码里整体删除**——它要求模型在回答里标 `[K1]`，软约束且前端不可解析，
> 而 sources 事件是结构化的，两者功能重叠。留档见 XB-10。

审核通过后，我按 3.1 → 3.2 → 3.3 顺序做，其中 3.2/3.3 会先在 `B_WORKLOG.md` 登记 XB-n 报备 A。
