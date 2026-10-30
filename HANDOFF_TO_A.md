# 交接说明（B → A）

> 交接人：B（Prompt 工程与 RAG 检索）
> 接收人：A（后端与 AI 算法 owner）
> 日期：2026-10-30
> 范围：RAG 检索链路、评测体系与金标准、Docker 部署配置、裁判模型通道、文档目录整理
> 代码状态：全部落地并验证，`run_tests.py` 688/688 通过
> 变更（2026-10-30 晚）：第二节的 P0 已在容器里实测复现并修复，不再归你；P1 / P2 仍需你处理
> 说明位置：本文件放在项目根便于你第一眼看到，接收后建议并入 `docs/handoff/HANDOFF.md` 或直接删除

---

## 一、先看这段：你现在接手的是什么

三条线发生了变化，一条没变。

```text
变了   检索默认配置       rerank_text: child_title -> parent_head（有 391 题实测支撑）
变了   运行开关           RAG_PARENT_CHILD=1 写进 4 份配置，生产已生效并重建容器
变了   裁判模型通道       新增 EVAL_JUDGE_* 四个变量，与生产 LLM_* 物理隔离
没变   金标准与结论口径   cases_v3.json 冻结不动，历史数字仍可比
```

一句话结论：检索这一轮调优只有在父子模式下成立。之前生产一直跑扁平模式，等于 K=3、
parent_head/480、per_doc_floor=-8 这一整套都没生效。现在打开了，nDCG 从 0.7936 回到 0.8812。

---

## 二、A 需要处理的事（按优先级）

### P0　`app.py` 库外提问必 500（NameError）—— 已于 2026-10-30 修复，仅存档

```text
文件    Tutor-Chatbot-main/backend/app.py
行号    1102
旧代码      if doc_context:
新代码      if pdf_docs:
问题    doc_context 只在 _compose_rag_context()（第 826 行）内部定义，
        而 1102 行在 chat() 函数里（第 843 行起）。两个不同作用域。
触发    只要 assessment_state["topic"] == "unknown"，执行到 1102 行必然 NameError -> 500
```

更正上一版的一个判断：原文写「实测返回 HTTP 200，因为那次 topic 没判成 unknown」，
这个结论是错的。当晚在容器里用同一句话打真实接口，拿到的是：

```text
POST /api/chat  {"message": "帮我写一首关于秋天的诗"}
[HTTP 500 | 2.53s]
```

只要评估节点把 topic 判成 unknown 就必 500，与输入长短无关；之前那次 200 是我测的
输入恰好被判成了别的 topic，不是这条分支能扛住。

已按方案 1 修（采纳上文的推荐项，没有动 `_compose_rag_context` 的返回值）：

```python
if pdf_docs:      # pdf_docs 是 chat() 里第 1019 行解包出来的，作用域内真实存在
```

补的回归用例在 `Tutor-Chatbot-main/backend/tests/test_chat_unknown_topic.py`，4 条：
unknown 无上传件走寒暄降级（200 + 发空 sources 数组）、unknown 有上传件走文档降级
（200 + sources 带 upload origin）。先跑红（4 条全部崩在 NameError），改完转绿。
全量 `run_tests.py` 684 -> 688，全绿。

修完后在容器里复验两条链路：

```text
寒暄   POST /api/chat "帮我写一首关于秋天的诗"       500 -> 200（15.5s，sources 空数组，66 token）
八股   POST /api/chat "什么是死锁？四个必要条件"     200（6.8s，sources 命中 cs/os/deadlock.md x3）
```

八股那条顺带确认了父子模式没被重建打断：`parent_child=True / rerank_text=parent_head`。
这条你不用再动了，下面 P1 / P2 才是留给你的。

### P1　我改了检索默认值，连带改了守护测试的语义（请 review 这一处）

`tests/test_recall_pool.py` 原本的设计是"负结论开关必须默认关闭，谁改默认值就让测试红"。
`rerank_text` 从 `child_title` 改成 `parent_head` 后这个测试红了，我没有直接把断言改成新值了事，
而是把这条从 `test_all_new_knobs_default_off` 里拆出来做成独立用例
`test_rerank_text_adopted_parent_head`，注释里写清依据和"要动这个值先看实测表"。

请重点看：这样拆分是否影响你对这个测试文件的意图（剩下的四个开关仍锁"默认关闭"）。
当前状态 688/688 全绿（684 是修 P0 之前的数，新增 4 条是 P0 的守护用例）。

### P2　父子模式开关只改了配置，没改代码（你的 .env 里现在多了一行）

```text
Tutor-Chatbot-main/backend/.env                     RAG_PARENT_CHILD=1
Tutor-Chatbot-main/backend/env.example              RAG_PARENT_CHILD=1
Tutor-Chatbot-main/deploy/.env                      RAG_PARENT_CHILD=1
Tutor-Chatbot-main/deploy/.env.docker.example       RAG_PARENT_CHILD=1
```

四处均带注释说明"为什么必须开"。依据：`chroma_kb` 是按父子模式建的
（248 条全带 `parent_id`，`chunk_params_hash=1af56b9448eed061` 就是 parent hash），
但 `CONFIG.parent_child` 默认 False，两边一直对不上。

同一套 `cases_v3` + 同一版重排配置，只切这个开关：

```text
RAG_PARENT_CHILD=1   recall 0.8736  nDCG 0.8812  precision 0.4872  noise 0.3909
不设（扁平）          recall 0.9091  nDCG 0.7936  precision 0.4795
```

注意 recall 在扁平下反而更高，别被这个数骗了：扁平模式把整个 parent 当成一条 child 召回，
分母口径不同，nDCG 才是可比的那一把。

运维要注意：env 变更不会触发 compose 自动重建，必须
`docker compose -f docker-compose.yml up -d --no-deps --force-recreate backend`
（在 `Tutor-Chatbot-main/deploy/` 下执行）。我已经重建并验证过，但下次你改 env 还得这样做。

### P3　容器镜像是旧的，在里面跑评测会找不到新文件

`eval/judge_llm.py`、`scripts/compare_annotators.py`、`scripts/simulate_qwen_relabel.py`
是这一轮新建的，当前运行的镜像里没有。`/chat` 生产不受影响（它不 import 这些），
但要在容器里跑评测必须先：

```bash
cd Tutor-Chatbot-main/deploy
docker compose -f docker-compose.yml build backend
```

### P4　我动了 C 的地盘一处（前端），你需要知道或转告

```text
文件    Tutor-Chatbot-main/frontend/lib/identity.ts（第 18 行）
改动    DEFAULT_BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL || "http://127.0.0.1:8000"
原因    原来硬编码指向 localhost，远程访问时前端会连访问者自己的本机，必失败
影响    仅默认值，用户手动设置过的后端地址（localStorage）优先，行为不变
```

这是为了 Docker 部署能通，属于应急处置。C 回来做前端时要确认这个默认值与他的一致性方案不冲突。

---

## 三、生产现在跑的是什么（配置快照）

```text
项                    值
容器                  ai-classroom-backend-1（healthy）、web-1、mysql-1
对外入口              8080（nginx 反代 /api 到 backend，backend 不映射宿主机端口）
MySQL                 仅 compose 内网可访问，宿主机 3306 未占用（本机原有 mysqld 仍在）
父子检索              parent_child=True
注入条数              parent_final_k=3；扁平 final_k=4
重排输入              rerank_text=parent_head，rerank_head_chars=480
重排融合              rerank_fusion_w=0.0（实测无效，保持纯重排）
整题相关性下限        relevance_floor=-2.0
单篇下限              per_doc_floor=-8.0（唯一转过默认的旧改动）
生产模型              LLM_MODEL=deepseek-flash（未改动）
裁判模型              EVAL_JUDGE_MODEL=qwen3.8-27b，地域端点 ws-6974ky9ynu0oayk4.cn-beijing
```

验证命令（我实际跑过的，可直接复用）：

```bash
cd Tutor-Chatbot-main/deploy
docker exec ai-classroom-backend-1 printenv RAG_PARENT_CHILD     # 期望输出 1

docker exec ai-classroom-backend-1 python -c "
import sys; sys.path.insert(0,'/app')
from agent.rag import retriever as R
from agent.rag.indexer import load_knowledge_index
print('parent_child=', R.CONFIG.parent_child, 'parent_final_k=', R.CONFIG.parent_final_k)
store = load_knowledge_index('cs_basics','chroma_kb')
docs = R.retrieve_knowledge_docs(store, '死锁产生的四个必要条件是什么？', topic='死锁', trace={})
print('注入条数=', len(docs))
for d in docs: print(' ', d.metadata.get('source'), 'start=', d.metadata.get('start_index'),
                     'parent_start=', d.metadata.get('parent_start'), 'len=', len(d.page_content))
"
# 期望：parent_child=True，注入 3 条，start_index == parent_start，正文 94/818/601 字

curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8080/health      # 期望 200
```

---

## 四、我改过你地盘的代码（逐文件，附回滚方式）

全部是"调参 + 补评测工具"，没有改业务语义。你可以按这个清单 review。

```text
文件                                  改了什么                                      回滚
agent/rag/retriever.py                默认 rerank_text child_title -> parent_head     RAG_RERANK_TEXT=child_title
                                      档位注释重写为 v3 实测表（8 档）                无需重建索引
agent/roles.py                        四个角色 + 回落分支统一追加 ANSWER_FORMAT_RULES   删掉该行
agent/prompts.py                      新增常量 ANSWER_FORMAT_RULES（禁星号/禁竖线表）   删除常量
eval/judge_llm.py                     新建：裁判模型唯一解析入口                       删除文件
eval/grounding_eval.py                make_judge_llm 改为委托工厂                     恢复原实现
scripts/judge_scores.py               删掉模块级硬编码三常量，改延迟解析 JUDGE_CFG      git 回退
scripts/build_eval_v3.py              走裁判通道；gold_note() 动态取实际模型名          git 回退
eval/e2e_runner.py                    门禁注释同步新默认值                            无需回滚
eval/metrics_report.py                3 处建议文案同步                                 无需回滚
tests/test_recall_pool.py             rerank_text 用例拆分（见 P1）                    恢复原断言
scripts/compare_annotators.py         新建：双模型标注一致性比对                       删除文件
scripts/simulate_qwen_relabel.py      新建：换金标准对检索指标的影响换算               删除文件
scripts/reorg_root_docs.py            新建：根目录文档迁移合并                         删除文件
Tutor-Chatbot-main/frontend/lib/identity.ts  见 P4                                git 回退
```

关于 `ANSWER_FORMAT_RULES`：这是你要求的"回答排版规范"落地（禁止星号强调、禁止 Markdown
竖线表格、表格改用 HTML 或等宽文本、层次与列表统一）。追加在系统提示词末尾，角色之间不重复。
副作用是每次请求多约 300 字符，没测过对延迟的可见影响。

---

## 五、写报告、软著必须带的口径

这一节是防误报的，写文档时照抄判断，别自己换算。

### 5.1 两套题集不可混

```text
题集                            规模              用途                         关键数字
旧集（smoke_rag 默认）          75 题 / 71 计分   早期基线、持续回归              top1 68/71、nDCG@3 0.870
新集 eval/cases_v3.json         395 题 / 391 计分  统计检验、A/B 显著性            nDCG 0.8812（父子）
```

引用分数必须带题集，`68/71(71题)` 和 `0.8812(391题)` 放在一句话里比就是错的。

### 5.2 当前基线（v3，391 计分题，父子模式，rerank_text=parent_head）

```text
指标            值        说明
Recall@3        0.8736
nDCG@3          0.8812    这是主指标
Precision@3     0.4872
噪声率          0.3909    越低越好
p95 延迟        1.97s     串行测；父子预算 5s，并发下会虚报，别用并发数
降级            0
```

### 5.3 K=3 的依据

三段损失分解里 `window_loss` 仅 0.81pp，小于 MDE 1.34pp（395 题、α=.05、power=.8）
⇒ 把 K 从 4 降到 3 造成的窗口损失在统计上不可分辨，而注入 token 与延迟实打实下降。
这条是"K=3"唯一站得住的理由，别写成"经验值"。

### 5.4 重排的两条结论（容易记反）

```text
换 checkpoint（BGE int8 -> jina-reranker-v2）   nDCG +0.60pp  CI[-0.56,+1.81]  不显著
换输入粒度（child_title -> parent_head/480）    nDCG +2.62pp  CI[+0.81,+4.41]  显著
```

结论是"重排值得投入，但杠杆不在换模型，在喂给它什么文本"。此前的判断是反的：
旧粗金标准下重排只剩 4.27pp 空间（占 20%），收紧金标准后变成 5.97pp（占 42%），
重排才是主要瓶颈。

### 5.5 Qwen 裁判的定位（重要，别被"1M 免费额度"带偏）

```text
对照组                              一致率      kappa
Qwen vs 现存金标准                  73.2%       0.560
DeepSeek 重跑 vs 现存金标准（自复现） 83.8%       0.741
Qwen vs DeepSeek（同时刻同 prompt）  82.6%       0.694
```

连 DeepSeek 自己都复现不出当年的标签（滚动在线版本漂移），所以换模型只会得到"另一把尺"。

换算到指标：被重标的 78 题 recall +6.04pp、nDCG +2.02pp、precision -8.12pp。
若全量重标，nDCG 的偏移超过 MDE，检索一行代码没动，报告上会显示"检索变好了"。

决定：`cases_v3.json` 冻结，不用 Qwen 重标。Qwen 只用于同时刻的相对比较
（`judge_scores.py`、`grounding_eval.py`）。真要第二把尺子，另存 `cases_v4_qwen.json`
并附上面三个 pp 的换算表，绝不与 v3 出现在同一张趋势图里。

裁判通道的额外收益（实测）：关思考后四维评分 282 tokens / 3.63s，开思考 716 / 13.71s，
打分完全一致（5/5/4/2）⇒ 推理链对"套格式"任务零贡献。100 万额度约可跑 3500 次评分。

---

## 六、怎么验证我交的东西是真的

四条命令，从快到慢，都能在本地跑：

```bash
cd Tutor-Chatbot-main/backend

# 1. 单测（约 5 秒，不联网不花钱）
venv/Scripts/python.exe run_tests.py
# 期望：RAN=688 FAILURES=0 ERRORS=0（684 + P0 守护用例 4 条）

# 2. 裁判通道自检（不发请求，只看解析结果）
venv/Scripts/python.exe -m eval.judge_llm
# 期望：裁判模型=qwen3.8-27b  端点=ws-6974ky9ynu0oayk4.cn-beijing...  key=sk-ws-...（脱敏）
#       temperature=0.0  关闭思考=是   扩展请求体：{'enable_thinking': False}

# 3. 裁判通道真实连通（花 1 次额度，我这次 4.30s / 19 tokens；热连接约 0.5s）
venv/Scripts/python.exe -m eval.judge_llm --ping
# 期望：调用成功 + 模型回复「正常」+ reasoning_tokens 为 None（说明思考确实关了）

# 4. 检索基线复现（我这次跑了 15 分钟，395 题全量重排；父子模式由 .env 自动带出，不用加前缀）
venv/Scripts/python.exe scripts/diag_acc_ceiling.py --tag CHECK --cases eval/cases_v3.json \
    --overfetch 1 --inject-k 3 --rerank-text parent_head
# 期望：recall 0.8736 / ndcg 0.8812 / precision 0.4872 / noise 0.3909，与第五节表格逐位一致
```

第 4 条我 2026-10-30 09:15 实跑过一次，关键输出（可直接对照）：

```text
cases 391   top1 374   top3 384
recall_actual 0.8736   ndcg_actual 0.8812   precision_actual 0.4872   noise_rate 0.3909
loss_breakdown: recall 7.36pp / window 0.81pp / ranking 4.47pp
config.rerank_text = parent_head, inject_k = 3
```

两点说明：

其一，这条命令**没有**加 `RAG_PARENT_CHILD=1` 前缀，父子模式是从 `backend/.env` 读到的，
说明配置链路是通的；如果谁把这一行删了，nDCG 会掉到 0.7936 左右 —— 那是模式问题，不是检索退化。

其二，延迟别拿这次输出当基线。这次同机并跑了别的任务，`rerank_p95_ms` 报 7166ms；
空闲机器串行测的历史值是 1.97s。延迟一定要独占机器串行测，并发下数值没有意义（见第七节第 4 条）。

---

## 七、七个坑，别重复踩

1. 本机不要 pip install。`pip.ini` 指向清华源 + shell 有沙箱代理，装包必失败；环境是整体搬运来的。
2. 起本地服务前必须 unset 代理，否则 `/chat` 502：
   `env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy ./venv/Scripts/python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000`
3. env 改了不等于容器吃了。compose 不会因 `.env` 变化重建，必须 `--force-recreate`。
4. 延迟只能串行测。并发下 parent 注入曾虚报 10405ms，报告直接作废。
5. 金标准里 0.0 和 0.5 不能互换。同主题但答不对的 parent 要显式标 0.0 覆盖兜底，
   标成 0.5 会放松标尺，制造"检索变好"的假象（Precision 曾从 56% 掉到 48.7% 就是这个原因）。
6. `RAG_GROUNDING_RULES`、`ANSWER_FORMAT_RULES` 是模块常量，常驻服务要重启才吃到新措辞。
7. 改文档路径前先读 `AGENTS.md` 2.4：根目录只允许 `AGENTS.md`、`CLAUDE.md`、`README.md`
   三份（本文件是例外）。文档都在 `docs/` 下分类合并，新内容能并入现有合集就追加一节，别再开新文件。

---

## 八、文档在哪、还剩什么

根目录现在只有三份文档加一个 `docs/`。要看全景先读 `docs/README.md`（含旧文件名对照表）。

```text
你要找的                              去哪
检索现状 / 参数口径 / 维护命令          docs/specs/RAG_HANDBOOK.md
接口契约                               docs/specs/API_CONTRACT.md
模型调用规范 / Agent 分工（你写的）      docs/specs/AGENT_PROTOCOL.md
这一轮所有实测报告（13 档 + 显著性）     docs/reports/REPORTS.md
变更日记（XB-1 ~ XB-46，含越界登记）     docs/worklogs/B_WORKLOG.md
交接总览                               docs/handoff/HANDOFF.md
```

剩余待办（不属于我这条线，列出来免得丢）：

```text
①  app.py:1102 的 500（第二节 P0）                        归你，建议本轮修掉
②  容器镜像重建（第二节 P3）                              归运维，跑评测前必做
③  _docs_archive_20261030/ 备份目录                       你确认 docs 结构后可整目录删除
④  软著文本                                               一直没写，需要你或用户推进
⑤  debate 场景是否对前端开放                              3 Agent 一轮约 14 秒，待用户拍板
```

最后一句提醒：这一轮所有结论都建立在 `cases_v3.json` 这一把尺子上。换尺子之前先看第五节的
换算表，别把"标注变了"读成"系统变好了"。
