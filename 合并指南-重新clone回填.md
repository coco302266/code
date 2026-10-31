# 队友代码合并指南（重新 clone + 回填）

> 适用场景：你本地 `D:/STUDY/AI-Classroom` 的 `.git` 已损坏（`bad object HEAD`），且队友 GitHub 仓库 `coco302266/code` 的版本**不含你的 A 角色后端提交**。本指南教你在**能联网的机器**上拿到一份干净仓库，再把你自己的改动精准补回去。

---

## 0. 先搞清楚现状（为什么不能直接 merge）

| 事实 | 说明 |
|---|---|
| 本地 `.git` 损坏 | `git status` 报 `fatal: bad object HEAD`，无法提交/真正合并 |
| 队友 GitHub 不含你的提交 | 已证实 `graph.py` 里没有你加的 `LLM_TIMEOUT_SECONDS`，`test_graph.py` 里没有你的 `LLMResilienceTests` |
| 本次已做的事 | 用镜像把队友文件拉到 `D:\STUDY\_teammate_pull\staging\`，并把队友**新增**的 162 个文件已落盘到你的项目（docs/、deploy/、scripts/、新测试、knowledge v0~v3 等） |

**关键省事点**：那 162 个"新增"文件**本来就是队友仓库的一部分**。所以只要重新 clone 一份干净的队友仓库，它们**自动就有了**，不用手动搬。你真正要补的，只是**你自己的 A 角色改动 + 几个本地独有文件**。

---

## 1. 在能联网的机器上，重新 clone（拿到干净仓库）

```powershell
# 建议在 D:/STUDY 下新建目录，别覆盖你现在的本地目录
cd D:/STUDY
git clone https://github.com/coco302266/code AI-Classroom-fresh
cd AI-Classroom-fresh
```

> 若 clone 报 `Connection was reset` / 超时：在命令前加代理，例如
> `git -c http.proxy=http://127.0.0.1:7890 clone https://github.com/coco302266/code AI-Classroom-fresh`
> （把 `7890` 换成你本机代理软件的监听端口；或用 GitHub 镜像前缀 `https://ghproxy.net/https://github.com/...`）。

clone 完成后，`AI-Classroom-fresh` 就是队友的**完整最新版**（含那 162 个新增文件 + 前端 + 知识库新版本）。

---

## 2. 补回你自己的 A 角色改动（核心步骤）

你的 A 角色独有改动（队友版不含），来自备份 `D:\STUDY\_backup_merge_20261031_142456`（和当前本地一致）。**只动下面这几个文件，其余一律用 clone 来的队友版**：

| 文件 | 操作 | 说明 |
|---|---|---|
| `Tutor-Chatbot-main/backend/agent/graph.py` | **合并** | 把本地 `get_llm()` 里的 `LLM_TIMEOUT_SECONDS=60` / `LLM_MAX_RETRIES=1` 两行补回队友版（队友版没有，直接加） |
| `Tutor-Chatbot-main/backend/db/repository.py` | **合并** | 把 `load_history` 的群聊共享语义 docstring 补回（队友也改了 11 行，注意别覆盖他们的） |
| `Tutor-Chatbot-main/backend/tests/test_graph.py` | **追加** | 把本地 `LLMResilienceTests` 类追加到队友版末尾（队友版另有 26 行新测试，保留） |
| `Tutor-Chatbot-main/backend/tests/test_app.py` / `test_state.py` | 无需改 | 与队友版一致（IDENTICAL） |
| `Tutor-Chatbot-main/backend/app.py` | **谨慎合并** | 双方都大改（本地增 203 / 队友增 95）。先用 diff 看清楚再合，别整体覆盖 |
| `Tutor-Chatbot-main/backend/run_server.py` | **整文件复制** | 本地独有（队友无此文件），直接拷过去 |

具体做法（任选其一）：
- **手动**：用 VS Code 打开 `AI-Classroom-fresh` 和备份目录，把上面几处差异对进去。
- **用我生成的 diff 参考**：`D:\STUDY\_teammate_pull\backend_refill_diff.txt` 里有 graph/repository/test_graph/app 等全部 DIVERGED 文件的 unified diff，照着补。
- **核对清单**：`D:\STUDY\_teammate_pull\backend_analysis.txt` 列出了 28 个核心文件逐状态（19 一致 / 10 分歧），★ 标记的就是你必须补的。

---

## 3. 补回本地独有文件（非代码，可选）

这些文件队友仓库没有，但你有，按需拷贝到 clone 目录：
- `运行教程-新手版.md`（你写的新手教程）
- `项目使用方法.txt`（使用说明，含 CORS 3001 改动）
- 若你改过根层文档（`AGENT_PROTOCOL.md` 等），队友已把它们挪到 `docs/`，以 `docs/` 为准即可，根层旧文件可删。

---

## 4. 那些"双方都改过"的文件怎么处理（B 负责的）

以下 6 个文件本地与队友都改过（DIVERGED），**负责人是 B（RAG/提示词）**，不是你：
- `agent/prompts.py`、`agent/rag/classroom_store.py`、`agent/rag/retriever.py`、`agent/roles.py`、`agent/state.py`

建议：**优先采用队友新版**（他们大概率在 GitHub 上是 canonical）。如果你确知本地有必须保留的 B 改动，再用 `backend_refill_diff.txt` 里对应的 diff 段手动合并。这类文件不要盲目用本地覆盖队友。

---

## 5. 重新建 venv（Python 3.11）

clone 不含 `backend/venv`（被 gitignore）。按之前验证可用的方式重建：
```powershell
# 用 uv 装 3.11（本机无 3.11 时）
uv python install 3.11
cd D:/STUDY/AI-Classroom-fresh/Tutor-Chatbot-main/backend
python -m venv venv
# 改 venv/pyvenv.cfg 的 home/executable/version 指向 uv 装的 3.11（如 C:\Users\24264\AppData\Roaming\uv\python\cpython-3.11.16-windows-x86_64-none）
venv\Scripts\python.exe -m pip install -r requirements.txt
```

---

## 6. 提交

```powershell
cd D:/STUDY/AI-Classroom-fresh
git add -A
git commit -m "refill: 补回 A 角色后端改动 (graph/repository/test_graph/run_server) + 本地文档"
git push origin main   # 或新开分支 PR
```

---

## 7. 你现在已有的产物（都在 `D:\STUDY\_teammate_pull\`）

- `backend_analysis.txt` —— 28 个核心文件逐状态 + ★必须回填清单
- `backend_refill_diff.txt` —— 全部 DIVERGED 文件的 unified diff（回填时照抄）
- `staging/` —— 队友完整文件快照（已落盘，可直接对照）
- `D:\STUDY\_backup_merge_20261031_142456\` —— 你本地改动完整备份（回填源）

> 当前本地目录 `D:/STUDY/AI-Classroom` 已是"保守合并结果"（162 新增已落盘、179 未覆盖），但 `.git` 损坏无法提交。等 GitHub 能连时，按本指南在 `AI-Classroom-fresh` 上操作即可。
