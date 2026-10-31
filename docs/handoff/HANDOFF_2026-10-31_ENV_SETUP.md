# HANDOFF 2026-10-31：本机环境搭建 + 六处改动（可搬运）

> 面向对象：以后在新工作空间接手本项目的 AI 助手，以及组员合并代码的人。
> 背景：2026-10-31 在本机 `E:\code` 上把项目从「跑不起来」修到「前后端 + 数据库 + 知识库全通」。
> 组员后来会给一份他们改过的项目副本，**那份副本没有这些改动**，需要按本文件补齐。

---

## 零、给接手 AI 的第一句话

如果你在新工作空间读到这份文件，按下面顺序执行，不要凭猜测改代码：

1. 先跑 `git log --oneline -3` 和 `git status --porcelain`，确认当前分支基点。
2. 执行 `git apply --check docs/handoff/2026-10-31_env_setup.patch`。
   - 通过 → `git apply docs/handoff/2026-10-31_env_setup.patch`
   - 失败 → 跳到本文第四节，按「手工改」逐条落地（patch 只是省事，四个文件都不大）。
3. 按第五节重建运行时产物（venv / node_modules / chroma_kb），**这些不能靠合并搬运**。
4. 按第六节逐条验证，每条都有期望输出。

---

## 一、这次改了什么（一句话版）

| 层次 | 内容 | 能否用合并搬运 |
|---|---|---|
| 代码 | 4 个文件的小修（见第三节） | 能，patch 直接应用 |
| 配置 | `backend/.env` 新建（含 MySQL 口令与 LLM key） | 不能，被 gitignore，需手工建 |
| 运行时 | `venv`、`node_modules`、`chroma_kb`、`.model_cache` | 不能，需按第五节重建 |
| 数据 | MySQL `ai_classroom` 库与 8 张表 | 机器级，同一台 MySQL 就还在 |

---

## 二、环境事实（换机器必看，都是踩过的坑）

1. MySQL 用的是本机 Windows 服务，服务名 `MySQL`，开机自启：
   - 可执行：`D:\mysql\MySQL Server 8.0\bin\mysqld`（8.0.41）
   - 数据目录：`D:\MySQL\MySQL Server 8.0\Data\`
   - 字符集：`utf8mb4 / utf8mb4_0900_ai_ci`
   - 账号：`root` / 口令 `123456`（**弱口令，且服务监听 0.0.0.0，未收紧**）
2. 机器上还有一套便携版 `D:/mysql/mysql-8.0.29-winx64`，其 `data` 里也有个同名的
   `ai_classroom`（空表），**已作废**。不要再启动它，会和服务的 3306 抢端口。
3. Python：项目专用 venv 在 `backend/venv`，基础解释器 Python 3.12.6
   （`C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe`）。
   venv 里放了 `pip.ini` 指向阿里云源。
4. pip 源：**清华源对 pip 26 返回 403**，报错文案却是
   `No matching distribution found`，极易误判成没网。排查必须用 `pip install -vvv`
   才看得到 `"GET /simple/xxx/ 403`。可用源：阿里云 / 腾讯云 / USTC / 华为云。
5. 模型下载：本机 `huggingface.co` 不通（curl 返回 000），**必须**
   `HF_ENDPOINT=https://hf-mirror.com HF_HUB_ENDPOINT=https://hf-mirror.com`，
   否则 `build_kb.py` 卡死在 `Fetching 5 files`。
6. Node：v22.22.2 / npm 10.9.7，`registry.npmjs.org` 可直连。
7. 端口：后端 8000（`127.0.0.1`），前端 3000（`localhost`）。
   前端 CORS 白名单只有 `http://localhost:3000`，用 `127.0.0.1:3000` 会被跨域拦掉。

---

## 三、代码改动清单（四个文件 + 一份工作记录）

1. `Tutor-Chatbot-main/backend/env.example`
   末尾新增 MYSQL 配置段。原文**完全没有这一节**，而 `db/session.py` 全靠这些变量名
   判断 `DB_ENABLED`，照示例配会配不上，后端静默退化成「无数据库模式」。

2. `Tutor-Chatbot-main/backend/init_db.py`
   修 `main()` 里的打码 bug：空口令时 `DATABASE_URL.replace('', '***')` 会在每个字符间
   插星号，把连接串打成 `***m***y***s***q***l...`。改为口令非空才打码。

3. `Tutor-Chatbot-main/backend/setup_backend.bat`
   三处：`BASE` 从写死的 `D:\STUDY\AI-Classroom\Tutor-Chatbot-main\backend` 改成 `%~dp0` 自定位；
   `PY` 从写死的 `C:\Users\24264\...\uv\python\cpython-3.12.14` 改成 `py -3.12`；
   镜像顺序改为阿里云前置、清华兜底（原因见第二节第 4 点）。

4. `Tutor-Chatbot-main/backend/start_server.bat`
   `BASE` 同样改成 `%~dp0` 自定位。

5. `docs/worklogs/B_WORKLOG.md`
   追加 XB-55 一节，记录本次环境迁移的全部事实与验证证据。

---

## 四、patch 应用失败时的手工改法

四个文件的改动都很小，直接用编辑器改更快。以下是要点（完整 diff 见 patch 文件）：

- `env.example`：文件末尾追加一节，变量名为
  `MYSQL_HOST` / `MYSQL_PORT` / `MYSQL_USER` / `MYSQL_PASSWORD` / `MYSQL_DATABASE`
- `init_db.py` 的 `main()`：

```python
# 改前
print(f"连接串：{DATABASE_URL.replace(os.getenv('MYSQL_PASSWORD', ''), '***')}")
# 改后
_pwd = os.getenv("MYSQL_PASSWORD", "")
shown = DATABASE_URL.replace(_pwd, "***") if _pwd else DATABASE_URL
print(f"连接串：{shown}")
```

- 两个 `.bat`：开头三行改为

```bat
set "BASE=%~dp0"
cd /d "%BASE%"
set "PY=py -3.12"
```

  并且 `setup_backend.bat` 里两处 `"%PY%" -m venv venv` 要去掉引号写成 `%PY% -m venv venv`
  （否则 `py -3.12` 会被当成一个整体路径）。

应用 patch 时的三种情况：

```bat
git apply --check docs/handoff/2026-10-31_env_setup.patch     :: 先测
git apply docs/handoff/2026-10-31_env_setup.patch             :: 干净就应用
git apply --3way docs/handoff/2026-10-31_env_setup.patch      :: 有偏移时三方合并
git apply --reject docs/handoff/2026-10-31_env_setup.patch    :: 冲突时落 .rej，逐个人工改
```

---

## 五、运行时产物重建（合并搬不过来，必须重跑）

在**新工作空间**的 `Tutor-Chatbot-main/backend` 下：

```bat
py -3.12 -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com
venv\Scripts\python.exe init_db.py
```

建知识库（注意必须带镜像环境变量）：

```bat
set HF_ENDPOINT=https://hf-mirror.com
set HF_HUB_ENDPOINT=https://hf-mirror.com
venv\Scripts\python.exe build_kb.py
```

前端：

```bat
cd Tutor-Chatbot-main\frontend
npm ci
```

`backend/.env` 需要手工创建（不在 git 里），最小内容：

```ini
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=123456
MYSQL_DATABASE=ai_classroom

LLM_PROVIDER=openai
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-flash
LLM_API_KEY=<找 key 的所有者要，不要写进仓库>
```

---

## 六、验证清单（每条都有期望输出）

1. `venv\Scripts\python.exe run_tests.py`
   → `RAN=714 FAILURES=0 ERRORS=0 SKIPPED=0`
   （`AGENTS.md` 里写的 282 是旧数字，别拿它当基准）
2. `venv\Scripts\python.exe init_db.py`
   → `8 张表全部就绪 OK`
3. 起后端后 `curl http://127.0.0.1:8000/health`
   → `{"status":"ok","provider":"openai","model":"deepseek-flash","database":"up"}`
4. `curl http://127.0.0.1:8000/evaluation/status`
   → `ready:true, database:up, table.exists:true, missing_columns:[]`
5. 提一句「TCP 为什么需要三次握手？」
   → SSE 里 `sources` 非空且 `origin=kb`，`agent_runs` 最新一行 `rag_hits > 0`
   → 若日志出现 `degraded='no_store'`，说明知识库没建好，回第五节
6. 前端 `npm run dev` 后访问 `http://localhost:3000/`
   → HTTP 200，页面渲染出「智能教室」

---

## 七、几个会误判成 bug 的现象

1. **`agent_runs` 没有新行**：多半是调用方中途断开（`curl | head -N`、前端 abort、
   关页面）。SSE 流没跑完，写留痕那步不会执行。要用完整收流再查。
2. **`/chat` 308 跳 `/chat/`**：`next.config.ts` 是 `output:"export"` + `trailingSlash:true`，
   正常行为。`curl -L /chat` 会报 `too many redirects`（exit 47），直接访问 `/chat/` 是 200。
3. **口播说「父子切片必须开」**：`env.example` 里 `RAG_PARENT_CHILD=1` 那段注释是针对
   别处构建过的库（`chunk_params_hash=1af56b9448eed061`）。本机新建的库是 flat 单层
   （500/80），开着反而对不上，**故意没开**。
4. **跑 `run_tests.py` 时反复打印 `agent_runs 表放宽约束`**：startup 与测试各自执行一次
   幂等 ALTER，属正常噪音。

---

## 八、合并组员版本时的建议顺序

1. 先确认他们的改动提交在哪个 commit 上，`git log` 对比本仓库的 `4e2d15c`。
2. 在他们那份代码上应用 patch（第四节）。冲突最可能出现在两个 `.bat` 和工作记录文件。
3. 应用完立刻跑第六节 1、2 两条，先保证后端能起、表能建。
4. 最后再重建 `venv` / `node_modules` / `chroma_kb`，不要反过来。
5. 若他们的版本改过 `requirements.txt`，以他们的为准，再补 `sqlalchemy`/`pymysql`/
   `python-dotenv` 三件（本项目原本已有）。

---

## 附：本机遗留未做的两件事

1. MySQL 服务 `bind_address=*`（监听 `0.0.0.0:3306`）且 root 口令是 `123456`，
   未收紧。要收就改 `D:\mysql\MySQL Server 8.0\my.ini` 的 `[mysqld]` 加
   `bind-address=127.0.0.1` 再重启服务——注意同机另一个项目 `lostandfound` 可能依赖局域网访问。
2. 桌面客户端形态（`npm run build` 出 `out/` + `npm run client` 起 Electron）未验证，
   Electron 窗口需人工确认。
