# docs 文档索引

2026-10-30 整理：项目根原有的 37 份文档按类型收进本目录，同类合并成一份，
根目录只保留 `AGENTS.md`、`CLAUDE.md`、`README.md` 三份。

整理前的原件全量备份在 `_docs_archive_20261030/`（含 `MANIFEST.md` 清单），
确认无误后可整目录删除；需要回滚直接把文件拷回项目根即可。

## 一、目录结构

```text
docs/
  specs/      规范、契约、手册 —— 引用最密，保持独立文件，不合并
  proposals/  PROPOSALS.md   7 份方案提案合并
  reports/    REPORTS.md     9 份实验/评测报告合并
  plans/      PLANS.md       4 份开发/调优计划合并
  handoff/    HANDOFF.md     4 份交接类文档合并
              + NEXT_SESSION_PROMPT / PROJECT_REPORT / STAGE_REPORT_* 独立保留
  worklogs/   B_WORKLOG.md、C_WORKLOG.md  追加型日志，不合并
  tutorial.md 后端源码课程
```

## 二、按用途找文件

```text
specs/API_CONTRACT.md         前后端接口契约（前端按此对接）
specs/AGENT_PROTOCOL.md       三人分工与接口协议，§7.5 是评测口径
specs/RAG_HANDBOOK.md         RAG 现状 / 口径 / 硬约束 / 维护命令（合并自 5 份已落地提案）
specs/EVAL_METRICS_API.md     评测指标的计算口径与调用方式
specs/REQUIREMENTS.md         需求说明
specs/DEVELOPMENT.md          开发说明（环境、命令、目录约定）

reports/REPORTS.md            9 份报告：RERANK 四份、EVAL_SET_V3 构建、Embedding 替换、
                              Retrieval Ceiling、V3 A/B 显著性、系统测试报告
proposals/PROPOSALS.md        7 份提案：RAG 架构 / 可观测 / 评测集 / 规模化 /
                              范围预筛 / 教师资料 / D2-D3
plans/PLANS.md                4 份计划：开发计划、前端计划、RAG 调优、重排选型
handoff/HANDOFF.md            4 份交接：HANDOFF_M4、HANDOFF_RAG_LIBRARY、项目交接说明、
                              打开前请阅读我（等宽排版已整篇放进 text 代码块）
handoff/NEXT_SESSION_PROMPT.md 新会话启动提示，每次开新会话先读它
handoff/PROJECT_REPORT.md     T0–T7 开发总结：10 个设计问题、11 个 bug、6 张表取舍
handoff/STAGE_REPORT_M1_M3.md 第一阶段~第三阶段总结
handoff/STAGE_REPORT_T8_T9.md 第二阶段总结（T8–T9）
worklogs/B_WORKLOG.md         B（RAG / Prompt 方向）变更日记，含 XB-n 越界登记
worklogs/C_WORKLOG.md         C（前端方向）变更日记
tutorial.md                   后端源码课程与改造记录
```

## 三、按旧文件名查（合并前的位置）

合并文件顶部都有一节「原名索引（按旧文件名查）」，带锚点跳转；
下面这张表用于从旧名字直接定位到合并后的文件。

```text
旧文件（项目根）                            现在的位置
PROPOSAL_D2_D3.md                          docs/proposals/PROPOSALS.md
PROPOSAL_RAG_ARCH.md                       docs/proposals/PROPOSALS.md
PROPOSAL_RAG_EVAL_DATASET.md               docs/proposals/PROPOSALS.md
PROPOSAL_RAG_OBS.md                        docs/proposals/PROPOSALS.md
PROPOSAL_RAG_SCALE.md                      docs/proposals/PROPOSALS.md
PROPOSAL_SCOPE_PREFILTER.md                docs/proposals/PROPOSALS.md
PROPOSAL_TEACHER_MATERIALS.md              docs/proposals/PROPOSALS.md

RERANK_COST_QUALITY_COMPARISON.md          docs/reports/REPORTS.md
RERANK_P1_EXEC_REPORT.md                   docs/reports/REPORTS.md
RERANK_STRATEGY_SUMMARY.md                 docs/reports/REPORTS.md
RERANK_TEXT_GRANULARITY_REPORT.md          docs/reports/REPORTS.md
EVAL_SET_V3_BUILD_REPORT.md                docs/reports/REPORTS.md
EMBEDDING_SWAP_VALIDATION.md               docs/reports/REPORTS.md
RETRIEVAL_ACCURACY_CEILING.md              docs/reports/REPORTS.md
V3_AB_SIGNIFICANCE_REPORT.md               docs/reports/REPORTS.md
TEST_REPORT.md                             docs/reports/REPORTS.md

DEVELOPMENT_PLAN.md                        docs/plans/PLANS.md
FRONTEND_PLAN.md                           docs/plans/PLANS.md
RAG_TUNING_PLAN.md                         docs/plans/PLANS.md
RAG_RERANK_SELECTION_PLAN.md               docs/plans/PLANS.md

HANDOFF_M4.md                              docs/handoff/HANDOFF.md
HANDOFF_RAG_LIBRARY.md                     docs/handoff/HANDOFF.md
项目交接说明.md                             docs/handoff/HANDOFF.md
打开前请阅读我.txt                          docs/handoff/HANDOFF.md

API_CONTRACT.md                            docs/specs/API_CONTRACT.md
AGENT_PROTOCOL.md                          docs/specs/AGENT_PROTOCOL.md
REQUIREMENTS.md                            docs/specs/REQUIREMENTS.md
EVAL_METRICS_API.md                        docs/specs/EVAL_METRICS_API.md
RAG_HANDBOOK.md                            docs/specs/RAG_HANDBOOK.md
DEVELOPMENT.md                             docs/specs/DEVELOPMENT.md
NEXT_SESSION_PROMPT.md                     docs/handoff/NEXT_SESSION_PROMPT.md
PROJECT_REPORT.md                          docs/handoff/PROJECT_REPORT.md
STAGE_REPORT_M1_M3.md                      docs/handoff/STAGE_REPORT_M1_M3.md
STAGE_REPORT_T8_T9.md                      docs/handoff/STAGE_REPORT_T8_T9.md
B_WORKLOG.md                               docs/worklogs/B_WORKLOG.md
C_WORKLOG.md                               docs/worklogs/C_WORKLOG.md
tutorial.md                                docs/tutorial.md
```

## 四、合并后需要知道的三个口径变化

1. 标题层级整体下调两级：合并文件自己是 H1，每份原稿是 H2「第 N、原名 xxx」，
   原稿的 H1 变 H3。原稿内部的小节编号（如 4.2）在合并后不再唯一。
2. 引用统一改成仓库根相对路径（`docs/specs/X.md`），不再使用裸文件名或 `../`。
   全仓 245 处引用已在迁移时批量改写，旧文件名的独立出现形式基本清零。
3. 报告合集的数字不可跨报告直接比较：那 9 份横跨不同题集、不同配置、不同时间，
   引用时必须带上报告名与题集规模（这条也写进了 `AGENTS.md`）。
