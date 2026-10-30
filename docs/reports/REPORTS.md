# AI-Classroom 实验与评测报告合集

> 本文件由根目录 9 份独立报告合并而成（2026-10-30）。合并只为止住根目录散落，正文一字未改，仅把标题层级整体下调两级。口径提醒：这些报告横跨不同题集、不同配置、不同时间，数字之间不可直接相比，引用时必须带上报告名与题集规模。

## 原名索引（按旧文件名查）

- `RERANK_COST_QUALITY_COMPARISON.md`（重排模型质量与成本对比报告（JINA v2 vs 现状 BGE int8）） —— 第 1 节，[跳转](#rerank-cost-quality-comparison)
- `RERANK_P1_EXEC_REPORT.md`（重排选型 P1 执行报告（2026-10-28）） —— 第 2 节，[跳转](#rerank-p1-exec-report)
- `RERANK_STRATEGY_SUMMARY.md`（重排模型选型：总体论证与决策建议） —— 第 3 节，[跳转](#rerank-strategy-summary)
- `RERANK_TEXT_GRANULARITY_REPORT.md`（重排输入粒度 与 RRF 融合权重：实测与选型结论） —— 第 4 节，[跳转](#rerank-text-granularity-report)
- `EVAL_SET_V3_BUILD_REPORT.md`（评测集扩容至 395 题 + 金标准收紧：构建报告） —— 第 5 节，[跳转](#eval-set-v3-build-report)
- `EMBEDDING_SWAP_VALIDATION.md`（换粗排 embedding 的离线验证结论：**不建议落地**） —— 第 6 节，[跳转](#embedding-swap-validation)
- `RETRIEVAL_ACCURACY_CEILING.md`（检索准确率天花板归因（重排模型不变的前提下）） —— 第 7 节，[跳转](#retrieval-accuracy-ceiling)
- `V3_AB_SIGNIFICANCE_REPORT.md`（395 题新集上的 A/B 显著性检验报告） —— 第 8 节，[跳转](#v3-ab-significance-report)
- `TEST_REPORT.md`（AI Classroom 系统测试报告） —— 第 9 节，[跳转](#test-report)

---

<a id="rerank-cost-quality-comparison"></a>

## 1、原名 RERANK_COST_QUALITY_COMPARISON.md

### 重排模型质量与成本对比报告（JINA v2 vs 现状 BGE int8）

> 环境：无 GPU，纯 CPU；评测集 71 题（L1 检索层）；成本基准各 200 样本（40 问 × 5 轮，每问 4 条 ~250 字真实切片）。
> 报告日期：2026-10-28。数据文件：`eval/reports/rerank_ab_{B0,B1,JINA}.json`、`eval/reports/bench_{BGE,JINA}.json`。

---

#### 0. 结论先行

**新下载的 jina-reranker-v2-base-multilingual（下称 JINA）相比现状 bge-reranker-v2-m3-int8（下称 BGE）：质量全面下降、内存磁盘翻倍，唯一收益是重排推理快约 20%，而这一收益在全链路 10.4s 里仅省 ~85ms（可忽略）。结论：不换，维持 BGE。**

一句话取舍：**JINA 用「1.63× 内存 + 2× 磁盘 + 48% 更慢的冷启动 + 质量全面变差」换「重排快 20%」——在教学产品里不划算。**

---

#### 1. 评测口径

| 维度 | 方法 |
|---|---|
| 质量 | 71 题 L1 全量，Recall@3 / Precision@3 / MRR / nDCG@3 / Top1 / 噪声率 / 硬负例率 |
| 成本 | 独立进程各跑一次（内存干净）；冷加载耗时、进程 RSS 增量、重排延迟分位、吞吐 |
| 延迟负载 | 真实 query 40 条 × 每问 4 条真实知识库切片（约 250 字，贴合线上 `child_title`） |
| 稳定性 | 200 样本（repeat 5），前有 warmup |
| 注意 | 机器有背景负载抖动，延迟绝对值仅供参考，**相对差异已在 3 轮中复现** |

---

#### 2. 质量对比（71 题，L1 检索层）

| 指标 | B0 无重排 | **B1 现状 BGE int8** | JINA v2 | JINA − B1 |
|---|---|---|---|---|
| Recall@3 | 0.7539 | **0.7909** | 0.7807 | **−1.02pp** ❌ |
| Precision@3 | 0.5282 | **0.5634** | 0.5516 | **−1.18pp** ❌ |
| MRR | 0.9272 | **0.9648** | 0.9577 | −0.71pp ❌ |
| nDCG@3 | 0.8161 | **0.8700** | 0.8600 | **−1.00pp** ❌ |
| Top1 命中 | 63/71 | **68/71** | 67/71 | −1 条 ❌ |
| Top3 命中 | 70/71 | 70/71 | 70/71 | 0 |
| 注入噪声率（越低越好） | 0.3545 | **0.3052** | 0.3263 | **+2.11pp** ❌ |
| 硬负例误注入率（越低越好） | 0.0955 | **0.0897** | 0.1019 | **+1.22pp** ❌ |

**读法**：JINA 打赢了「无重排」B0（说明重排本身有价值），但**在每一项上都不如现状 BGE**。尤其在两个「越低越好」的指标上更差——每 3 条注入就有约 1 条是零增益噪声（32.6% vs 30.5%），每 10 条注入有 1 条是整节零增益硬负例。

→ **增益为负，无可采纳的正向收益。**

---

#### 3. 成本对比（CPU，各 200 样本）

| 成本项 | **BGE int8** | JINA v2 | JINA 相对 BGE |
|---|---|---|---|
| 实际加载的模型文件 | 570.7 MB | 1114.0 MB | **+95%（≈2×）** |
| 进程内存增量 RSS | 826.0 MB | 1348.0 MB | **+63%** |
| 冷加载耗时 | 1635 ms | 2418 ms | **+48%（更慢）** |
| 重排延迟 mean | 363.6 ms | **286.0 ms** | −21%（更快）✅ |
| 重排延迟 p50 | 368.0 ms | **284.6 ms** | −23% ✅ |
| 重排延迟 p95 | 409.7 ms | **330.5 ms** | −19% ✅ |
| 重排延迟 p99 | 425.7 ms | **350.6 ms** | −18% ✅ |
| 吞吐（docs/s） | 11.0 | **14.0** | +27% ✅ |
| 许可证 | Apache-2.0（可商用） | **CC-BY-NC-4.0（禁商用）** | **不可用于生产** ❌ |

> 注：JINA 在 HF 仓库里还带了 fp16/q4/bnb4 等多个变体（全量快照约 4.6GB），但 fastembed **只加载 `onnx/model.onnx`（1114MB）**。生产若只下该文件，磁盘按 1114MB 计。

**为什么更大的 JINA 反而更快？**（2026-10-28 晚已修正，原推断有误）

初版推断是「INT8 反量化/低精度算子开销抵消了量化收益」。**该推断已被同权重精度对照实验推翻**（见 §7）：同一套 bge-m3 权重，FP32 版重排 p95 **1716ms**，比 INT8 的 **1054ms** 慢 **63%**。所以 INT8 在这台 CPU 上不慢、反而最快，JINA 更快的真实原因是**它本身更小**（base 级 278M 主干，对 568M 的 bge-m3），而不是精度格式。

---

#### 4. 端到端影响（A/B，71 题真实链路）

| 指标 | BGE | JINA | 差异 |
|---|---|---|---|
| 重排 mean | 947.0 ms | 823.2 ms | −13% |
| 重排 p95 | 1077 ms | 982 ms | −9% |
| 单题检索总耗时（avg） | 0.9915 s | 0.8624 s | −13% |
| 检索层 p95 | 1.1111 s | 1.0262 s | **−7.6%（省 ~85ms）** |
| 全链路 p95（含 LLM 生成） | ~10.4 s | ~10.3 s（推算，未跑 L4） | **≈−0.8%，可忽略** |

**关键判断**：重排虽占检索 98%，但检索只占全链路 ~10%。换 JINA 省下的 85ms 放在 10.4s 的端到端里 **不到 1%**，用户完全无感；却要多付 1.6× 内存和 2× 磁盘。

---

#### 5. 最终结论

| 结论 | 说明 |
|---|---|
| **不更换生产重排模型** | 维持 `bge-reranker-v2-m3-int8` |
| **JINA 否决理由** | ① 质量全面劣于 BGE；② 内存 1.63×、磁盘 2×、冷启动 +48%；③ `CC-BY-NC-4.0` 禁商用；④ 延迟收益在全链路中 <1% |
| **JINA 的唯一用途** | 留档为「更小主干换更低延迟」的样本（不是精度格式的功劳，见 §7） |
| **想真正提升质量的方向** | 只剩方案首选 **Qwen3-Reranker-0.6B（Apache-2.0）**，但它不在 fastembed 支持列表，需 `torch+transformers` + `--backend hf` 分支；且 JINA 这种强多语言重排器都打不过 BGE，对 Qwen3 在**纯 CPU** 上既赢质量又过延迟闸门不宜乐观 |

---

#### 6. 复现命令

```bash
cd backend
export FASTEMBED_CACHE_PATH="$(pwd)/.model_cache"
export HF_ENDPOINT=https://hf-mirror.com
export HF_HUB_DISABLE_XET=1

# 质量 A/B（71 题）
venv/Scripts/python.exe scripts/ab_rerank_model.py \
  --tag JINA --model jinaai/jina-reranker-v2-base-multilingual \
  --repeat 1 --out eval/reports/rerank_ab_JINA.json

# 成本基准（各模型独立进程跑）
venv/Scripts/python.exe scripts/bench_rerank_cost.py \
  --model BAAI/bge-reranker-v2-m3-int8 --tag BGE --repeat 5 --out eval/reports/bench_BGE.json
venv/Scripts/python.exe scripts/bench_rerank_cost.py \
  --model jinaai/jina-reranker-v2-base-multilingual --tag JINA --repeat 5 --out eval/reports/bench_JINA.json
```

---

#### 7. 补齐剩余候选后的终局对比（2026-10-28 晚）

##### 7.1 为什么会有这一轮

上文只测了 2 个候选（bge-m3-int8、jina-v2-multilingual），不足以回答「BGE 是不是最合适」。
fastembed 支持的重排模型一共 **8 个**，逐一核对后：

| 候选 | 许可 | 是否可测 | 处置 |
|---|---|---|---|
| Xenova/ms-marco-MiniLM-L-6-v2（80MB） | Apache-2.0 | 纯英文（MS MARCO） | 按语言不适用排除 |
| Xenova/ms-marco-MiniLM-L-12-v2（120MB） | Apache-2.0 | 纯英文 | 按语言不适用排除 |
| jinaai/jina-reranker-v1-tiny-en / turbo-en | Apache-2.0 | 纯英文 | 按语言不适用排除 |
| BAAI/bge-reranker-base（278M） | **MIT** | ✅ 中文可用 | **本轮实测** |
| BAAI/bge-reranker-v2-m3（FP32，568M） | Apache-2.0 | ✅ 与现状同权重、仅精度不同 | **本轮实测** |
| BAAI/bge-reranker-v2-m3-int8（568M） | Apache-2.0 | ✅ 现状 | 已有基线 |
| jinaai/jina-reranker-v2-base-multilingual | **CC-BY-NC-4.0** | ✅ | 已测并否决 |

即：**许可证干净、且语言上适用的候选已全部测完**，只剩 ms-marco / jina-en 这类纯英文模型（中文八股语料上无意义）未跑。

##### 7.2 新增的离线加载通道（本轮工程产出）

镜像 429/502 期间 fastembed 完全无法加载模型——它每次实例化都会先打 Hub 取 revision
（`fastembed/common/model_management.py:385`）。为此新增：

- `scripts/onnx_xenc.py`：直接从本地目录读 ONNX 图 + `tokenizer.json`，暴露与 fastembed
  一致但**返回 float 迭代器**的 `rerank()`（fastembed 也是 yield float，返回对象会让
  `retriever._rerank_docs` 的排序抛 `TypeError`，本轮踩过）。
- `scripts/ab_rerank_model.py` 新增 `--onnx-dir / --threads`：覆盖 `get_reranker()` 单例，
  不经 fastembed、不查 Hub。
- 数据来源改用**魔搭 ModelScope**（HF 镜像挂掉时仍可达，实测 4–5 MB/s）；魔搭上有
  `onnx-community/bge-reranker-v2-m3-ONNX` 与 `BAAI/bge-reranker-base` 的完整 ONNX 文件。

**加载器自检（关键前提）**：用新加载器跑**现状 int8 模型**，71 题指标与 fastembed 逐项一致——
Recall 0.7909 / Precision 0.5634 / MRR 0.9648 / nDCG 0.8700 / Top1 68 / 噪声率 0.3052 /
硬负例率 0.0897。**因此下面跨加载器的比较成立。**

##### 7.3 全候选质量对照（71 题，K=3，同一批题、同一 harness）

| 候选 | Recall@3 | Prec@3 | MRR | nDCG@3 | Top1 | 噪声率↓ | 硬负例率↓ | rerank p95 |
|---|---|---|---|---|---|---|---|---|
| B0 无重排 | 0.7539 | 0.5282 | 0.9272 | 0.8161 | 63/71 | 0.3545 | 0.0955 | 0 ms |
| **B1 bge-m3-int8（现状）** | **0.7909** | **0.5634** | **0.9648** | **0.8700** | **68/71** | **0.3052** | **0.0897** | 1054 ms |
| bge-m3 **FP32**（同权重） | 0.7862 | 0.5587 | 0.9577 | 0.8619 | 67/71 | 0.3052 | 0.0903 | **1716 ms** |
| bge-reranker-**base**（278M） | 0.7782 | 0.5493 | 0.9648 | 0.8532 | 68/71 | 0.3169 | **0.1218** | 907 ms |
| jina-reranker-v2-multilingual | 0.7807 | 0.5516 | 0.9577 | 0.8600 | 67/71 | 0.3263 | 0.1019 | 982 ms |

##### 7.4 三条新结论

1. **精度格式维度：INT8 是「质量与速度双赢」，不是权宜之计。**
   同一套权重，FP32 换来的是 **nDCG −0.81pp、Top1 −1 条、延迟 +63%（1054→1716ms）**，
   没有任何一项变好。这说明本项目选 int8 不是「为了省磁盘的妥协」，而是当前 CPU 上的最优档。
   同时它**推翻了上文 §3「INT8 反量化拖慢 CPU」的推断**（已就地修正）。

2. **更小主干（base，278M）确实更快（907ms，−14%），但质量掉得更多：**
   nDCG −1.68pp、Recall −1.27pp、**硬负例误注入率 0.0897→0.1218（+3.2pp，最差档）**。
   即「每 8 条注入就有 1 条是整节零增益」，对小模型来说这个代价不可接受。

3. **排序稳定性上 bge-m3-int8 也是最好的**：MRR 0.9648 与 base 并列最高，但它的
   噪声率（0.3052）与硬负例率（0.0897）都是全场最低——**质量优势集中在「少注入垃圾」，
   而不是「把正确答案排更前」**（Top1 已 68/71 接近饱和，区分度本就低）。

##### 7.5 结论：BGE 是不是「最合适」——按可测范围分成两句话

- **在「可离线部署 + 许可可商用 + 中文适用」这个交集里：是。**
  `bge-reranker-v2-m3-int8` 在 6 项质量指标上全部第一（并列第一的 MRR 除外），
  延迟仅比最快的 base 慢 147ms（相对 3.0s 门禁占 5%），磁盘 571MB 是全场最小，
  许可 Apache-2.0 可商用。**没有第二个候选能在任何一项上同时不落后。**
- **但它不是「全局最优」的证明**：还有三条路没走——① Qwen3-Reranker-0.6B（Apache-2.0，
  不在 fastembed 列表，需 `torch+transformers`，本机无 GPU，纯 CPU 延迟风险高）；
  ② gte-reranker-modernbert-base（149M，同上）；③ 云端重排 API（Cohere/Voyage/Jina，
  需出网合规确认，且延迟 188–614ms 与本地差不了多少）。
  这三条**都不在当前运行时可评测的范围内**，因此结论的准确表述是
  **「当前可离线部署候选中的最优」，而非「所有模型中的最优」**。

---

<a id="rerank-p1-exec-report"></a>

## 2、原名 RERANK_P1_EXEC_REPORT.md

### 重排选型 P1 执行报告（2026-10-28）

> 对应方案：`docs/plans/PLANS.md`
> 执行范围：P0 准备 + P1 离线评测（可执行部分）
> 原则：一次只变一个变量、每档独立进程、与 B0/B1 双基线对比

---

#### 1. 执行摘要

**做了三件事**：① 复现 B0/B1 双基线（精确复现）；② 新增 P1 评测脚本与难负例标注，跑完 5 档配置矩阵；③ 排查候选模型的**可执行性**。

**三个结论**：

1. **配置维度已无剩余空间**——`child_title` 已是最优输入模式，`pool_k` 加大完全无效，`batch=1` 已是最优。**瓶颈确实在模型判别力，不在配置**（R10 得到实测支撑）。
2. **首选候选本轮无法执行**——本机网络不通（hf-mirror / huggingface 均 ReadTimeout），且 fastembed 0.9.0 的 `TextCrossEncoder` **不支持 Qwen3-Reranker 与 gte-reranker**，只支持 8 个模型（见 §5）。因此 A2/A3 必须走「联网下载 + 换运行时」路线，属于部署动作，未执行。
3. **发现评测集一个结构缺陷**——现有 gold 在 **parent 粒度上 79/79 条全相关**（覆盖率 1.0），导致"相邻小节"天然无法作为难负例；已改用 `gain=0` 的整节零增益 parent 作为硬负例（58/83 条覆盖）。

---

#### 2. 基线复现（P0 交付）

| 档位 | 命令 | Recall@3 | Prec@3 | MRR | nDCG@3 | Top1 | rerank p95 | L1 p95 |
|---|---|---|---|---|---|---|---|---|
| **B0** 无重排 | `--no-rerank` | **0.7539** | 0.5282 | 0.9272 | **0.8161** | **63/71** | 0 ms | 0.015 s |
| **B1** 现状 | `--model bge-reranker-v2-m3-int8 --rerank-text child_title` | **0.7909** | **0.5634** | **0.9648** | **0.8700** | **68/71** | **1077 ms** | **1.111 s** |

**复现结论**：B1 与历史基线 `e2e_20261028_130646.json` 完全一致（0.7909 / 0.5634 / 0.9648 / 0.8700 / 68-71），rerank p95 1077ms vs 1082ms（±0.5%），可复现性成立。B0 的 nDCG 0.8161、top1 63/71 也与 L2 报告里的 `ndcg_rerank_off` / `top1_off` 吻合。

**重排的真实贡献（B0→B1）**：Recall **+3.70pp**、Prec **+3.52pp**、MRR **+3.76pp**、nDCG **+5.39pp**、Top1 **+5 条**、噪声率 **−4.93pp**、硬负例率 **−0.58pp**，代价是 **+1.09s 延迟**。以当前链路看这笔交易是划算的，但延迟已吃满预算的 98%。

---

#### 3. 配置矩阵（R10：分离模型与配置）

同一模型 `bge-reranker-v2-m3-int8`，逐档独立进程：

| 档位 | 变更 | Recall@3 | Prec@3 | nDCG@3 | Top1 | 噪声率 | rerank p95 | L1 p95 | 判定 |
|---|---|---|---|---|---|---|---|---|---|
| B1 | 基线 | 0.7909 | 0.5634 | 0.8700 | 68 | 0.3052 | 1077 ms | 1.111 s | — |
| **C1** | `rerank_text=parent_head`(480字) | **0.7575** | 0.5540 | **0.8243** | **64** | 0.2746 | **1873 ms** | 1.920 s | ✗ 全面劣化 |
| **C3** | `pool_k=6` | 0.7909 | 0.5634 | 0.8700 | 68 | 0.3052 | 1113 ms | 1.158 s | = 无变化 |
| **C4** | `pool_k=8` | 0.7909 | 0.5634 | 0.8700 | 68 | 0.3052 | 1076 ms | 1.120 s | = 无变化 |
| **C5** | `batch=4` | 0.7874 | 0.5587 | 0.8603 | 67 | 0.3099 | 1154 ms | 1.217 s | ✗ 略劣化 |

##### 3.1 三条可落地的结论

**（1）`parent_head` 被证伪，方案原假设需修正**
方案 §5.1 把 `parent_head`(480 字) 当作 `child_title` 与 `parent` 之间的折中，实测**全面更差**：Recall −3.34pp、nDCG −4.57pp、Top1 −4 条，而延迟反涨 **74%**（1873ms）。
原因：480 字截断把整节砍掉后 2/3，而标题信号已经由 `child_title` 提供，截断正文只增加噪声与序列长度。
→ **`child_title` 不是折中，是当前最优**。方案 §5.1 的 `parent_head` 选项应标为「不推荐」。

**（2）`pool_k` 无效，不是"没调好"，是"池子只有 4.21 条"**
C3/C4 的 `mean_parent_pool` 恒为 **4.21**，与 B1 完全相同——意味着 `pool_k=6/8` 根本没触发上限。这从另一个角度再次证实「扩大候选池无收益」（此前 A/B 已证 0.7827→0.7712 劣化）。
→ 想在池维度做文章，必须**先提高召回条数**（`child_overfetch`），而不是抬 `pool_k` 上限；而 `child_overfetch` 已在 O13 被证伪到只剩噪声。

**（3）`batch=4` 在 CPU 上确认劣化**
Recall −0.35pp、nDCG −0.97pp、p95 +77ms。与 `RetrievalConfig` 注释里的实测（6 条：b1 0.73s / b4 1.52s）方向一致。
→ 无 GPU 时 `batch=1` 就是正确的；批处理只有在 GPU 上才转为优势。

**综合判断**：三档配置全部"无变化或劣化"，说明**当前配置已经处在该模型的局部最优**。要继续涨分，只能换模型——这条结论现在有了实测支撑，不再是推断。

---

#### 4. 难负例评测集（P0 交付）

##### 4.1 关键发现：现有 gold 在 parent 粒度是全相关的

对 79 条有切片标注的 case 统计「金标准 parent 数 / 所在文档 parent 总数」：

- **均值 = 1.000**
- **79/79 条 = 100%**

也就是说，每条 case 的名字都覆盖了它所在文档的**全部** `##` 小节。后果有两个：

1. **「同文档相邻小节」永远不可能是负例**——它和你标成答案的节一样是 gold。方案 §4.1 的 HN2 构造法在本项目**不成立**。
2. 这解释了 Recall@3 上限 0.9159 的成因之一：金标准是 **slice 级、每题只标 4 条**，而 parent 级全相关；两个粒度之间的差就是那 8.4pp 的"上限缺口"。

##### 4.2 改用「整节零增益」作为硬负例

`relevant_chunks` 自带三档 gain（1.0×117 / 0.5×123 / 0.0×112）。判据：

> 若某个 parent 底下被标注的切片**全部 gain≤0**，该 parent 整体判为硬负例。

保守判据的理由：节里只要有一条 gain>0 的切片，它就可能是答案的一部分，标成负例会让指标**奖励错误行为**。

产出 `eval/cases_rerank_v2.json`（原 83 题 + 新字段）：

| 项 | 值 |
|---|---|
| 有硬负例的题 | **58 / 83** |
| 平均硬负例数/题 | **1.35** |
| 覆盖 parent | 81 个（20 篇文档） |
| 新增字段 | `hard_negatives`（parent_id 列表）、`hard_neg_source`、`hard_neg_slices` |

##### 4.3 新指标：硬负例误注入率

`hard_neg_rate` = 注入条中属于硬负例的条数 / 注入总条数（只在有标注的 53 题上算）。

| 档位 | hard_neg_rate |
|---|---|
| B0 无重排 | **9.55%** |
| B1 现状 | **8.97%** |

重排把"整节零增益"的误注入压低了 **0.58pp（相对 −6%）**。绝对值仍接近 9%，说明**每 11 条注入里就有 1 条是明知不相关的整节**——这正是换更强重排器最该改善的地方。

---

#### 5. 候选模型的可执行性排查

##### 5.1 fastembed 0.9.0 实际支持的 rerank 模型（全量 8 个）

```
Xenova/ms-marco-MiniLM-L-6-v2      0.08 GB
Xenova/ms-marco-MiniLM-L-12-v2     0.12 GB
BAAI/bge-reranker-base             1.04 GB
BAAI/bge-reranker-v2-m3            2.27 GB
BAAI/bge-reranker-v2-m3-int8       0.57 GB   ← 现状
jinaai/jina-reranker-v1-tiny-en    0.13 GB
jinaai/jina-reranker-v1-turbo-en   0.15 GB
jinaai/jina-reranker-v2-base-multilingual 1.11 GB
```

**方案里的 A2（Qwen3-Reranker-0.6B）与 A3（gte-reranker-modernbert-base）不在列表里** → 无法用现有运行时加载，必须另外引入 transformers/torch 或 vLLM/TEI 服务。当前 venv **没有 torch / transformers / sentence-transformers**（已确认）。

##### 5.2 网络与本地缓存

| 项 | 状态 |
|---|---|
| `hf-mirror.com` | **ReadTimeout（16.95s）** |
| `huggingface.co` | **ReadTimeout（9.12s）** |
| 本地 `.model_cache` | 只有 `bge-reranker-v2-m3-ONNX`（int8）与 `bge-small-zh-v1.5` |

**结论**：本机当前**无法下载任何新模型权重**。唯一"新候选"是 `jina-reranker-v2-base-multilingual`（1.11GB），同样需要下载 → 同样阻塞。

##### 5.3 因此本轮实际可测的候选只有

| 候选 | 状态 | 原因 |
|---|---|---|
| A1 `bge-reranker-v2-m3-int8` | ✅ 已测（B1/C1–C5） | 本地已有 |
| A2 `Qwen3-Reranker-0.6B` | ⛔ 阻塞 | 网络不通 + fastembed 不支持 + 无 torch |
| A3 `gte-reranker-modernbert-base` | ⛔ 阻塞 | 同上 |
| A4 `jina-reranker-v2-base-multilingual` | ⛔ 阻塞 | 网络不通（1.11GB 需下载） |
| B 类云端 API | ⛔ 未执行 | 无 API key，且 Q2（出网合规）未确认 |

---

#### 6. 门禁判定

按方案 §6 P1 的五条准入：

| 门禁 | B1 现状 | 结果 |
|---|---|---|
| nDCG@3 > B0（0.8161） | 0.8700 | ✅ |
| Recall@3 ≥ 0.8059（B1+1.5pp） | 0.7909 | ❌（正是要换模型解决的问题） |
| rerank p95 ≤ 2160 ms | 1077 ms | ✅ |
| L1 p95 ≤ 3.0 s | 1.111 s | ✅ |
| 降级率 ≤ 2% | 0% | ✅ |

**P1 整体判定：未通过**——不是执行失败，而是"当前模型拿不到第 2 条"，与方案预期一致：**换模型是唯一出路，而换模型被网络/依赖阻塞**。

---

#### 7. 交付物清单

| 文件 | 说明 |
|---|---|
| `scripts/ab_rerank_model.py` | P1 主脚本：单档单进程、支持 model/text/pool/batch 覆盖、9 项指标 + 双基线差值 + 5 条门禁、可选 `hard_neg_rate` |
| `scripts/build_rerank_hardneg.py` | 难负例标注生成（整节零增益判据） |
| `eval/cases_rerank_v2.json` | 原 83 题 + `hard_negatives` 标注（58 题覆盖） |
| `eval/reports/rerank_ab_B0.json` / `B0_recheck.json` | B0 基线 |
| `eval/reports/rerank_ab_B1.json` / `B1_recheck.json` | B1 基线（含 hard_neg_rate） |
| `eval/reports/rerank_ab_C1_parent_head.json` | parent_head 档（被证伪） |
| `eval/reports/rerank_ab_C3_pool6.json` / `C4_pool8.json` | 池大小档（无变化） |
| `eval/reports/rerank_ab_C5_batch4.json` | 批处理档（劣化） |
| `eval/reports/rerank_sel_B0.json` / `B1.json` | e2e_runner 口径基线（用于交叉验证） |

---

#### 8. 下一步（需决策才能继续）

##### 8.1 阻塞项（按优先级）

| # | 阻塞 | 解除方式 | 解锁什么 |
|---|---|---|---|
| **Q1** | 无 GPU | 提供 ≥4GB 显存的卡，或确认可申请 | A2（Qwen3-0.6B）可测 |
| **Q5** | 网络不通 | 打通 hf-mirror，或手工把权重放进 `.model_cache`，或用内网镜像 | 任意新模型可下载 |
| **Q7** | 运行时缺失 | `pip install torch transformers`（约 2GB）或部署 TEI/Infinity 容器 | fastembed 之外的模型可加载 |
| **Q2** | 出网合规未定 | 明确教学资料能否送第三方 API | 云端探针（Jina/Cohere）可跑 |

##### 8.2 解除后的最短路径

```bash
# ① 装运行时（约 2GB，需网络）
venv\Scripts\pip.exe install torch --index-url https://download.pytorch.org/whl/cpu
venv\Scripts\pip.exe install transformers sentence-transformers

# ② 下模型（需网络；RAG_RERANK_ONLINE=1 允许联网）
set RAG_RERANK_ONLINE=1

# ③ 给 ab_rerank_model.py 加 --backend hf 分支（复用 build_cross_encoder 适配层）
#    然后用同一套 71 题跑 A2 / A3，与 B1 对比
RAG_PARENT_CHILD=1 venv\Scripts\python.exe scripts\ab_rerank_model.py ^
    --tag A2_qwen06 --backend hf --model Qwen/Qwen3-Reranker-0.6B --repeat 3
```

##### 8.3 可以立刻做、不依赖网络的两件事

1. **补 `--backend http`**：把云端 API 接成 `POST /rerank` 适配层，Q2 一放行就能跑探针（半天工作量）。
2. **金标准修正**：现有 79 条 case 的 gold 在 parent 级全相关，建议重标为「只保留 gain>0 的 slice 所属 parent」——这会同时抬高 Recall 上限的**可信度**（现在的 0.9159 上限里含约 8pp 的标注噪声）。

---

#### 9. 一句话总结

**配置已经调到顶了（三档全平或劣化），重排本身贡献 +5.39pp nDCG、−4.93pp 噪声；下一步换模型被"网络不通 + 无 torch + fastembed 不支持"三重阻塞，需先解 Q1/Q5/Q7 才能继续。**

---

<a id="rerank-strategy-summary"></a>

## 3、原名 RERANK_STRATEGY_SUMMARY.md

### 重排模型选型：总体论证与决策建议

> 目的：围绕「提高检索/问答准确率」这一最终目标，综合此前历次实验与结论，
> 论证应当**继续沿用并优化现有重排模型方案**，还是**转向其他技术方向**。
> 所有数据来自本机实测，详见文末引用文件。
> 口径：71 道 baseline 题（oos 不计）、父子检索模式、注入 K=3、重排模型
> `BAAI/bge-reranker-v2-m3-int8`（INT8 ONNX）。

---

#### §0 当前状态确认（先回答最核心的问题）

**当前使用的重排模型，仍是当初选定的 `BAAI/bge-reranker-v2-m3-int8`，且现已真实生效。**

三点独立证据：

1. **配置未变**（`agent/rag/retriever.py:80`）：
   `rerank_model="BAAI/bge-reranker-v2-m3-int8"`、`enable_rerank=True`、
   `rerank_online=False`；环境变量 `RAG_RERANK_MODEL` 默认值也指向同一模型。
   整段对话过程中，**生产代码里这个模型名从未被替换过**。
2. **权重缓存已恢复**：`.model_cache/bge-reranker-v2-m3-int8/onnx/model_int8.onnx`
   （570MB，硬链接自旧快照 `930e3860…`）。
3. **端到端真生效**：默认路径（不绕任何参数）下单次检索
   `rerank=ok`、`encoder is None = False`、`degraded`（空）——**不是之前撞到的静默降级**。

> 补充说明（此前已澄清）：上一轮曾出现「`RAG_RERANK=1` 但重排没真跑」的现象，
> 根因是 fastembed 缓存目录缺失后加载失败被吞、退化为 `docs[:top_n]`。
> 现已修复；且 `retriever.py:401` 本就会把 `rerank_unavailable` 计入
> `trace.degraded`（评测门禁 `degraded_rate≤0.02` 会拦截），缺口只在 `/chat`
> 链路缺少主动告警监控，而非代码未记录。

---

#### §1 此前历次回答与关键结论（按时间线梳理）

| # | 用户问题 | 关键结论 |
|---|---|---|
| 1 | 生成层 LLM 裁判是什么模型？ | 默认 `deepseek-flash`（=生产 `LLM_MODEL`），可用 `EVAL_JUDGE_MODEL` 换异源；同源裁判偏松，生成层指标只能看趋势。 |
| 2 | 输出可落地的重排选型方案 | 形成 `docs/plans/PLANS.md`：选型维度 / 候选范围 / 评测方法 / 工程约束 / 落地路线 / 风险清单。 |
| 3 | 按方案执行 | 写 `ab_rerank_model.py` 做 A/B；构造难负例；从 HF 镜像拉到 `jina-v2-multilingual`。 |
| 4 | 「打通镜像/手动拷权重」是何意？模型换了吗？ | 解释两种离线获取方式；**明确生产重排模型从未更换**。 |
| 5 | 连 VPN 重下 HF 模型 | 踩 5 个下载坑后从 HF 镜像拉到 jina v2；jina 许可 `cc-by-nc-4.0` 禁商用 → 一票否决。 |
| 6 | 新下载模型 vs 原模型：增益与成本 | `docs/reports/REPORTS.md`：JINA 质量全劣于 BGE 且禁商用，否决。 |
| 7 | 所以 BGE 是最合适的选择吗？ | **是「当前可离线+可商用+中文适用」交集内最优**；**非全局最优**（未测 Qwen3/gte/云端 API）。 |
| 8 | 准确率瓶颈在哪？不换模型还能提高吗？ | `docs/reports/REPORTS.md`：瓶颈在「召回预算+注入预算」，**不在重排模型**。 |
| 9 | 重排缓存丢了，重下 BGE | 权重本体在本机旧快照；用硬链接组装回缓存目录；已验证 `NATIVE_RESTORED` 与原生逐项一致。 |
| 10 | 换粗排 embedding 验证（jina-base-zh） | `docs/reports/REPORTS.md`：+0.78pp Recall，**统计不显著**，且有确定代价 → 不推荐切换。 |

**贯穿始终的一条主线**：重排模型本身从一开始（第 2 步方案）就被锁定为
`bge-reranker-v2-m3-int8`，后续所有工作都是在「这个锁定」前提下的验证与归因，
从未发生过模型替换。

---

#### §2 核心数据与事实提炼

##### 2.1 五候选重排模型对比（71 题，K=3，越优越好）

| 模型 | Recall@3 | Prec@3 | MRR | nDCG@3 | Top1 | 噪声率 | 硬负例率 | rerank p95 |
|---|---|---|---|---|---|---|---|---|
| 无重排基线 B0 | 0.7539 | 0.5282 | 0.9272 | 0.8161 | 63 | 0.3545 | 0.0955 | 0ms |
| **BGE-m3-int8（现状）** | **0.7909** | **0.5634** | **0.9648** | **0.8700** | **68** | **0.3052** | **0.0897** | 1054ms |
| BGE-m3-FP32 | 0.7862 | 0.5587 | 0.9577 | 0.8619 | 67 | 0.3052 | 0.0903 | 1716ms |
| bge-reranker-base(278M) | 0.7782 | 0.5493 | 0.9648 | 0.8532 | 68 | 0.3169 | **0.1218（最差）** | 907ms |
| jina-v2-multilingual | 0.7807 | 0.5516 | 0.9577 | 0.8600 | 67 | 0.3263 | 0.1019 | 982ms |

→ **现状 int8 在 7 项指标里 6 项最优或并列最优**，且 INT8 比同权重 FP32 又快又好
（反证「INT8 反量化拖慢」的旧假设错误——主干更小才是关键）。

##### 2.2 准确率损失三段归因（现状 K=3，总损失 20.91pp）

| 损失段 | 占比 | 负责方 | 含义 |
|---|---|---|---|
| **召回损失** | **15.02pp / 72%** | 粗召回（向量+BM25+RRF） | gold 没进候选池，重排看不见 |
| 窗口损失 | 1.62pp / 8% | 注入预算 K=3 | gold 在池里但 3 个坑装不下（gold 均值 4.49 条/题） |
| **排序损失** | **4.27pp / 20%** | 重排模型 | gold 在池里也装得下，却排到窗口外 |

→ **重排模型的全部剩余空间 = 4.27pp Recall / 5.96pp nDCG**，且 5 个候选实测极差仅 1.7pp。

##### 2.3 换粗排 embedding 验证（jina-base-zh）

- Recall@3 +0.78pp、nDCG@3 +0.42pp、候选池召回 +1.37pp；
- 配对 bootstrap（2 万次重采样）置信区间**跨 0**（9 题变好 / 6 题变差 / 56 题不变）→ **不可判定有效**；
- 确定代价：query 编码 2.2→13.9ms（×6.3）、端到端检索 p95 +163ms（+15%）、+641MB 磁盘、维度 512→768（HNSW 距离计算更贵）、需改 `indexer.py` 落库。

---

#### §3 围绕「提高准确率」的论证

##### 3.1 为什么不应继续在「重排模型」上投入

1. **天花板已锁死**：重排能影响的只有排序损失 4.27pp（占总量 20%），
   而这段空间在当前模型代际内几乎拿不到（5 候选极差仅 1.7pp）。
   继续搜 Qwen3-Reranker / gte / 云端 API，期望收益 ≤1.7pp，且要付出
   出网合规审查、本机无 GPU 跑不动大模型、CPU 上延迟崩坏等成本。
2. **收益已被证明「此消彼长」而非「净增」**：扩召回池能把召回漏从 15pp
   压到 0.4pp，但 Recall@3 纹丝不动（0.7909→0.7874）——重排在大杂池里把
   新增干扰排到了前面。说明**单纯给重排更多/更杂的输入救不了准确率**。
3. **放宽注入 K 能涨召回，但亏的是上下文质量**：K=5+扩池 3× 的 nDCG 略过线
   (+1.43pp)，代价是噪声率 0.3052→0.4925（一半内容是垃圾）、硬负例率 +47%，
   而这部分损失**不体现在检索指标里**，会在生成层忠实度上反向受损。

##### 3.2 真正的杠杆在哪里

- **72% 的损失在召回层**，但正确的解法不是「硬扩池」，而是「让 gold 排进 top-6 窗口」。
  旁路证据：top-30 child 已覆盖 99.6% 的 gold（pool_all 0.996），71 题仅 1 题整池找不到
  gold → **问题从来不是召不到，是排不进窗口**。
- 这条路径目前在小库（248 chunk）上实测**增益不显著**（jina +0.78pp 跨 0），
  但在**千片以上大库**上粗排判别力的杠杆会显著上升——小库结论不能直接外推。
- **更前置的杠杆**：评测本身。71 题的最小可检出效应约 ±2pp，
  此前任何「±2pp 以内的微调收益」都只是噪声排名。把金标准从
  「整篇 parent 全相关」收紧、评测集扩到 300+ 题，才能让现有优化变得可判定。

---

#### §4 各路径优劣对比

| 路径 | 理论增益 | 实测增益 | 成本 / 风险 | 是否推荐 |
|---|---|---|---|---|
| **沿用+守住 BGE-m3-int8** | 排序损失已近榨干 | 7/7 项最优/并列 | 零新增成本（已离线+可商用） | ✅ **推荐（维持现状）** |
| 继续换重排模型（Qwen3/gte/云端） | ≤4.27pp | 候选极差仅 1.7pp | 出网合规、无 GPU、CPU 慢、许可风险 | ❌ 不推荐 |
| 放宽注入 K（K=4/5） | +4.3~+8.6pp | 实测可达 | 噪声崩、硬负例 +47%、上下文劣化 | ❌ 不推荐（除非先改评测口径） |
| 换粗排 embedding（jina-base-zh） | 0~12pp（随库规模） | +0.78pp（不显著） | +641MB/+15% 延迟/维度变更/改代码 | ⚠️ 当前库规模不推荐，大库可重评 |
| 扩评测集 + 收紧金标准 | 让优化可判定 | — | 标注人力 | ✅ 推荐（前提条件） |
| 扩知识库到千片量级 | 提升粗排杠杆 | — | 工程 + 重索引 | ✅ 大库后重评 embedding 路径 |

---

#### §5 明确建议

##### 是否继续研究「重排模型选择」？—— **否（在当前约束与库规模下）。**

**理由：**
1. **目标锁定后它已是最优解**：在「离线部署 + 许可可商用 + 中文适用」这三重硬约束的
   交集内，`bge-reranker-v2-m3-int8` 是 7 项指标 6 项最优的模型，INT8 还顺带比 FP32
   又快又好。继续在约束内找更优模型，边际收益 ≤1.7pp，投入产出比极低。
2. **它的天花板就那么高**：只负责 20% 的损失（4.27pp），且代际内拿不到。
   把研究资源压在这 4.27pp 上，是在「最不值得的地方」用力。
3. **已验证的替代方向同样不划算**：换 embedding 当前不显著、有确定代价；
   扩池是零和；放宽 K 伤上下文。
4. **真正卡准确率的是评测与规模**，不是模型选型。

**建议的下一步投入优先级：**
1. **守住并重监控现状**：把 `rerank_unavailable` 接到运行时告警（别只留评测门禁），
   固化 `restore_rerank_cache.py` 以防缓存再丢。
2. **扩评测集到 300+ 题并收紧金标准**：这是让任何后续优化「可判定」的前提，
   优先级高于一切模型层面的微调。
3. **知识库扩到千片量级后，再回头重评换 embedding 路径**：届时粗排杠杆上升，
   当前不显著的结论可能翻转。
4. **如确有高准确率诉求**：评估放宽注入 K（4~5 条）对生成层忠实度的影响，
   这是唯一能显著涨 Recall 的旋钮——但必须先有可靠的生成层指标才能判断亏不亏。

**一句话总结**：重排模型选型这件事**已经结束，结论有效**；继续研究的 ROI 远低于
「先把评测做对、把知识库做厚」。无需在重排模型上再投入。

---

#### 引用文件

- `docs/plans/PLANS.md` — 选型方案
- `docs/reports/REPORTS.md` / `docs/reports/REPORTS.md` — 执行与对比
- `docs/reports/REPORTS.md` — 准确率天花板三段归因
- `docs/reports/REPORTS.md` — 换粗排 embedding 验证
- `scripts/restore_rerank_cache.py`、`scripts/diag_acc_ceiling.py`、`scripts/ab_rerank_model.py`、`scripts/fetch_modelscope_fastembed.py` — 实验与恢复脚本
- `eval/reports/rerank_ab_*.json`、`eval/reports/ceiling_*.json` — 全部原始数据

---

<a id="rerank-text-granularity-report"></a>

## 4、原名 RERANK_TEXT_GRANULARITY_REPORT.md

### 重排输入粒度 与 RRF 融合权重：实测与选型结论

> 评测集：`eval/cases_v3.json`（395 题，其中 391 题计分）
> 重排模型：`BAAI/bge-reranker-v2-m3-int8`（未更换）
> 日期：2026-10-29　｜　对比基线：`rerank_text=child_title`、`inject_k=3`、`child_overfetch=1`

---

#### §0 结论先行

**1. 输入粒度是真实有效杠杆，且首次拿到统计显著的质量提升。**

`parent_head`（小节全文截断 480 字）替换现状 `child_title`（命中片 + 小节标题，约 250 字）：

| 指标 | 现状 | parent_head/480 | 变化 | 95% CI | 判定 |
|---|---|---|---|---|---|
| nDCG@3 | 0.8550 | **0.8812** | **+2.62pp** | [+0.81, +4.41] | **显著** |
| Precision@3 | 0.4761 | 0.4872 | +1.11pp | [+0.21, +2.00] | **显著** |
| 噪声率（越低越好） | 0.4118 | **0.3909** | **−2.09pp** | [−3.45, −0.72] | **显著** |
| Recall@3 | 0.8586 | 0.8736 | +1.49pp | [−0.18, +3.12] | 不显著（边缘） |

延迟：p95 **1203ms → 1967ms（1.64×）**，仍在 2.0× 安全线（2406ms）内。

**2. 全文档（`parent`）质量更好，但延迟过不了门禁。**
nDCG +3.31pp / Recall +2.12pp 四项全显著，但 p95 **3538ms = 2.94×**，已超 2.77× 硬门禁（3331ms）。**不可上线**，仅作为粒度上界的标尺。

**3. RRF 融合权重无效，可以关掉这个念想。**
w=0.2/0.4/0.6 三档全部不显著（nDCG −0.02 / −0.76 / −1.77pp），w 越大越差。此前在旧集上「0.5/1.5 更差」的结论在新集上被复现并扩展到 0.2 档。

**4. 这两条结论合起来，修正了此前对瓶颈的判断。**
过去两轮都在「换 checkpoint / 换 embedding」上打转，全部不显著（+0.60pp、+0.71pp）。本轮只改**喂给重排模型什么文本**（零模型成本、零新增依赖），就拿到 +2.62pp 显著收益。**瓶颈不在模型能力，而在输入信息与标注粒度错配。**

**5. 已落地。** `retriever.py` 的 `rerank_text` 默认值已从 `child_title` 切到 `parent_head`，默认配置全量复跑**逐位复现**实验档指标（0.8736 / 0.8812 / 0.4872 / 0.3909）。回滚只需 `RAG_RERANK_TEXT=child_title`。

---

#### §1 为什么之前换模型全失败，改粒度却成了

金标准是 **parent（小节）粒度**：一题标注「哪些小节回答了这个问题」。但 `child_title` 只让交叉编码器看到该小节里命中的 **~200 字碎片 + 标题**。

后果是结构性的：同一个 `## 死锁的预防` 小节里，前半段讲「资源有序分配法」、后半段讲「银行家算法」，而 query 问的是后者。`child_title` 只递进去前者，模型怎么打分都是错的——**它没有看到答案**。

这正好解释了此前反复出现的两个反常：

- **排序损失（重排的真实空间）一直占大头**：收紧金标准后从 4.27pp(20%) 升到 5.97pp(42%)。
- **换任何 checkpoint 都没用**：输入是错的，换谁来读都读不出正确结论。JINA、base278M、FP32、m3 五候选极差仅 1.7pp，原因不是它们能力接近，而是**它们被同一个错误输入卡住了上限**。

粒度一改，排序损失从 5.97pp 掉到 4.47pp（parent_head）／3.85pp（parent）——重排的实际失职被收回了一大半。

---

#### §2 八档实测全景

评测条件统一：391 计分题、`inject_k=3`、池不做扩增（`child_overfetch=1`、`parent_pool_k=0`）。
质量指标在同一环境（ONNX 直载 + threads 4 并发）下取得，延迟单独串行复测（见 §4）。

| 档位 | rerank_text | Recall@3 | Δ | nDCG@3 | Δ | Prec@3 | 噪声率 | 硬负例率 | Top1 | 排序损失 |
|---|---|---|---|---|---|---|---|---|---|---|
| **BASE** | child_title（现状） | 0.8586 | — | 0.8550 | — | 0.4761 | 0.4118 | 0.0897 | 372 | 5.97pp |
| A | child | 0.8567 | −0.19 | 0.8523 | −0.27 | 0.4744 | 0.4135 | 0.0850 | 374 | 6.16pp |
| B | parent_head 240 | 0.8210 | **−3.76** | 0.8237 | **−3.13** | 0.4569 | 0.4007 | 0.1092 | 363 | 9.73pp |
| **C** | **parent_head 480** | **0.8736** | **+1.50** | **0.8812** | **+2.62** | **0.4872** | **0.3909** | 0.0805 | 374 | **4.47pp** |
| **D** | **parent（全文）** | **0.8798** | **+2.12** | **0.8882** | **+3.32** | **0.4902** | **0.3870** | **0.0800** | **377** | **3.85pp** |
| E | child_title + fusion 0.2 | 0.8619 | +0.33 | 0.8549 | −0.01 | 0.4783 | 0.4071 | 0.0903 | 368 | 5.64pp |
| F | child_title + fusion 0.4 | 0.8578 | −0.08 | 0.8474 | −0.76 | 0.4753 | 0.4122 | 0.0968 | 365 | 6.05pp |
| G | child_title + fusion 0.6 | 0.8557 | −0.29 | 0.8373 | −1.77 | 0.4744 | 0.4109 | 0.0903 | 364 | 6.26pp |

三个读得出的规律：

1. **粒度不是「越大越好」，是「必须覆盖答案所在位置」**。`parent_head 240` 比现状还差 3.13pp——见 §2.1 的长度分布：240 字会截断 79% 的小节，把答案和上下文一起砍掉。480 才越过临界点。
2. **`child` ≈ `child_title`**（差 0.27pp）。标题这一层信号几乎白给——真正缺的不是标题，是**正文上下文**。这与当初加 `child_title` 的设计假设相反。
3. **噪声率与召回同向改善**。parent_head/480 在 Recall +1.50pp 的同时噪声 −2.09pp、硬负例率 −0.92pp。这不是「多塞几条换召回」的零和路线（那条路此前实测 Precision −17pp），而是判别力真实变强。

##### §2.1 为什么 480 是甜蜜点：小节长度分布

从 60 条查询实际聚合出的 171 个 parent 统计（`doc.page_content` 长度）：

| 分位 | min | p25 | 中位 | p75 | p90 | max | 均值 |
|---|---|---|---|---|---|---|---|
| 字符数 | 88 | 267 | **485** | 813 | 1711 | 1994 | 598 |

| 截断档 | 不被截断（等同全文）的占比 | 被截断条数 |
|---|---|---|
| 240 字 | 21.1%（36/171） | **135** |
| **480 字** | **47.4%（81/171）** | **90** |
| 640 字 | 71.3%（122/171） | 49 |

这张表把三档的差异解释干净了：

- **240 档砍掉 79% 的小节**，其中位数只有 485 字的一半，答案常在后半段 → 质量崩到 0.8237，比现状还差。
- **480 档正好卡在中位数上**：近一半小节完整送出，另一半只砍掉尾部；而小节开头通常是定义/结论，信息密度最高 → 用 53% 的截断率换回 2.62pp。
- **全文档不再截断**，再拿 0.70pp。但代价是 p95 从 1967ms 冲到 3538ms。

也就是说：**480 这个默认值是代码里早就写好、但从未启用的**（`rerank_head_chars: int = 480`）。它恰好吃在小节长度的中位线上——不是巧合，是当初按库内文档形态定的参数，只是当时因为 `parent` 全文过不了门禁，连 `parent_head` 一起被搁置了。

---

#### §3 配对显著性检验

方法：逐题差值配对 bootstrap（20,000 次重采样，α=0.05），基线为 `child_title`。
同一批 391 题、同一金标准，只比逐题差值，避免题目间差异混入。

##### parent_head 480 vs child_title

| 指标 | 差值 | 95% CI | 胜负（胜/负/平） | 判定 | 判定所需样本 |
|---|---|---|---|---|---|
| Recall@3 | +1.49pp | [−0.18, +3.12] | 37/15/339 | 不显著 | 966 |
| **nDCG@3** | **+2.62pp** | **[+0.81, +4.41]** | 80/48/263 | **显著** | 370 |
| **Precision@3** | **+1.11pp** | **[+0.21, +2.00]** | 37/15/339 | **显著** | 527 |
| **噪声率** | **−2.09pp** | **[−3.45, −0.72]** | 15/40/336 | **显著** | 343 |

##### parent 全文 vs child_title

| 指标 | 差值 | 95% CI | 胜负 | 判定 |
|---|---|---|---|---|
| Recall@3 | +2.12pp | [+0.61, +3.62] | 38/11/342 | **显著** |
| nDCG@3 | +3.31pp | [+1.65, +4.95] | 85/38/268 | **显著** |
| Precision@3 | +1.41pp | [+0.55, +2.22] | 38/11/342 | **显著** |
| 噪声率 | −2.47pp | [−3.79, −1.15] | 11/40/340 | **显著** |

##### 融合 w=0.2 vs child_title

| 指标 | 差值 | 95% CI | 判定 |
|---|---|---|---|
| Recall@3 | +0.33pp | [−0.27, +0.96] | 不显著 |
| nDCG@3 | −0.02pp | [−0.67, +0.65] | 不显著 |
| Precision@3 | +0.21pp | [−0.21, +0.64] | 不显著 |
| 噪声率 | −0.47pp | [−1.11, +0.13] | 不显著 |

**注意 Recall 的边缘性**：parent_head/480 的 Recall 差 CI 下界 −0.18pp，刚好跨 0。这不是"没效果"，而是 391 题的分辨率边界——nDCG 已显著，Recall 要 966 题才能判定。落地判断应以 nDCG（排序质量）+ 噪声率为准，这两项都显著。

---

#### §4 延迟与门禁（串行实测）

并发会让 p95 严重失真（并行时 parent 档曾报 10405ms），故三档全部串行重测，走**生产同款 fastembed 原生路径**、不限线程。

| 档位 | rerank p95 | retrieval p95 | 相对基线 | L1 门禁 3.0s | 安全线 2.0×（2406ms） | 判定 |
|---|---|---|---|---|---|---|
| child_title（现状） | 1203ms | 1256ms | 1.00× | ✓ | ✓ | 当前生产 |
| **parent_head 480** | **1967ms** | **2009ms** | **1.64×** | ✓ | ✓ | **可上线** |
| parent 全文 | 3538ms | 3583ms | 2.94× | — | — | **超 2.77× 硬门禁，不可上线** |

口径说明（沿用既有推导）：重排占检索耗时 ~98%，L1 硬门禁 3.0s ⇒ 新档耗时上限 2.77×（3331ms）；安全线取 2.0×（2406ms）留一半余量给抖动与长尾。

**parent_head/480 用 +764ms 换来 +2.62pp nDCG 与 −2.09pp 噪声**，且余量还有 439ms。这是当前唯一同时满足"质量显著提升"与"延迟过门禁"的档位。

---

#### §5 一个必须记住的数值抖动

`parent` 全文档在两套环境下质量不一致：

| 环境 | Recall@3 | nDCG@3 | Top1 |
|---|---|---|---|
| ONNX 直载 + threads 4（并发） | 0.8798 | 0.8882 | 377 |
| fastembed 原生、不限线程（串行） | 0.8855 | 0.8925 | 379 |

而 `child_title`（0.8586/0.8550）与 `parent_head 480`（0.8812）在两套环境下**逐位一致**。

推断：`parent` 输入序列最长，logits 分布被压缩、近分候选多，浮点归约顺序（线程数）差异足以在边缘处翻转排序。**这本身是反对 `parent` 的又一条理由**——它除了慢，还不稳。`parent_head 480` 没这个问题。

---

#### §6 落地实施（已完成）

##### 改动清单

| 文件 | 改动 | 性质 |
|---|---|---|
| `agent/rag/retriever.py` | `rerank_text` 默认值 `child_title` → `parent_head`（字段默认值 + `from_env()` 默认值共 2 处）；档位注释重写为 v3 实测表 | **行为变更** |
| `eval/e2e_runner.py` | 门禁注释更新：重排当前打 480 字、p95 1.97s；`parent` 全文仍会判红 | 仅注释 |
| `eval/metrics_report.py` | 3 处给使用者的建议文案（nDCG 提升度、p95 延迟、重排提升度）同步为 parent_head | 仅文案 |

```diff
# agent/rag/retriever.py
-    rerank_text: str = "child_title"
+    rerank_text: str = "parent_head"

-            rerank_text=os.getenv("RAG_RERANK_TEXT", "child_title").strip().lower(),
+            rerank_text=os.getenv("RAG_RERANK_TEXT", "parent_head").strip().lower(),
```

`rerank_head_chars` **无需改动**——480 本来就是代码默认值，只是此前 `child_title` 档根本不走这个分支。

##### 切换后验证

默认配置、不加任何参数、全量 391 题：

| 指标 | 实验档 | 默认档复跑 | 一致性 |
|---|---|---|---|
| Recall@3 | 0.8736 | **0.8736** | 逐位一致 |
| nDCG@3 | 0.8812 | **0.8812** | 逐位一致 |
| Precision@3 | 0.4872 | **0.4872** | 逐位一致 |
| 噪声率 | 0.3909 | **0.3909** | 逐位一致 |
| config.rerank_text | — | `parent_head` | ✓ 读取正确 |

另做了纯函数级验证：`rerank_input()` 四档（child 57 字 / child_title 62 字 / parent_head 337 字 / parent 337 字）行为符合预期，且 `parent_head` 能覆盖小节中后段内容（`child_title` 看不到的那部分），机制成立。

三个改动文件均通过语法检查与导入检查（该 venv 未安装 pytest，无法跑 `tests/` 下的单测，以端到端全量评测替代）。

##### 灰度与回滚

| 项 | 做法 |
|---|---|
| 灰度 | 已直接改默认值；如需灰度，可用 `RAG_RERANK_TEXT=child_title` 把旧行为固定回去 |
| 观测 | `/chat` 的 `trace.rerank_ms` p95 应落在 1.9~2.1s；持续 >2.4s 即回滚 |
| 回滚 | `RAG_RERANK_TEXT=child_title`，**无需重建索引**（只影响重排输入，不动向量库） |

##### 不建议做的事

- **不要上 `parent` 全文**：延迟超硬门禁 2.94×，且存在跨环境数值抖动。
- **不要开融合权重**：三档全部不显著，w≥0.4 明确劣化，等于白付一次排序。
- **不要因为「child ≈ child_title」就把标题去掉**：差异虽小但方向为正，且它只在 `child` 档起作用，切到 `parent_head` 后该逻辑本来就不走。

---

#### §7 局限与后续

1. **金标准为 LLM 标注、待人工复核**。标注噪声会同时抬方差、压显著性；因此本报告的"显著"是**保守**结论，未显著的项也不能断言无效。
2. **结论仅对 248 child / 81 parent 的小库成立**。库扩到千片量级后，重排输入变长会让延迟进一步上升，`parent_head 480` 的余量（439ms）可能不够，需重测。
3. **480 未必是全局最优**。按 §2.1 的长度分布，480 档仍有 53% 的小节被截断；640 档能把截断率降到 28.7%。若延迟余量允许（640 估算 p95 ≈2.4~2.6s，**已逼近 2.406s 安全线**），可补测 640 再定参——成本约 20 分钟。当前先用 480 保延迟确定性。
4. **本轮已改动生产代码**（`retriever.py` 默认值 + 两处门禁注释/文案），其余为实验产物。改动清单与验证见 §6。
5. **小节长度分布基于 60 条查询实际聚合出的 171 个 parent**，不是全库 81 个 parent 的穷举——但覆盖了主要命中路径，量级可信。

---

#### 附录：复现命令

```bash
cd Tutor-Chatbot-main/backend
ONNX=".model_cache/models--onnx-community--bge-reranker-v2-m3-ONNX/snapshots/930e38609a0e848e391a2bba0bf52317850e7b25"

# 质量（任一档）
RAG_PARENT_CHILD=1 venv/Scripts/python.exe scripts/diag_acc_ceiling.py \
    --cases eval/cases_v3.json --onnx-dir "$ONNX" --threads 4 \
    --rerank-text parent_head --head-chars 480 \
    --tag V3_TXT_PHEAD480 --dump-per-case

# 延迟（必须串行、不带 onnx-dir，走生产同款 fastembed 路径）
RAG_PARENT_CHILD=1 venv/Scripts/python.exe scripts/diag_acc_ceiling.py \
    --cases eval/cases_v3.json --rerank-text parent_head --head-chars 480 \
    --tag V3_LAT_PHEAD480

# 显著性
venv/Scripts/python.exe scripts/ab_significance.py \
    --base eval/reports/ceiling_V3_BASE_20261029_085720.json \
    --alt  eval/reports/ceiling_V3_TXT_PHEAD480_20261029_135432.json
```

数据文件：`eval/reports/ceiling_V3_{TXT_PARENT,TXT_CHILD,TXT_PHEAD240,TXT_PHEAD480,FUS02,FUS04,FUS06,LAT_CHILD_TITLE,LAT_PHEAD480,LAT_PARENT}_*.json`

---

<a id="eval-set-v3-build-report"></a>

## 5、原名 EVAL_SET_V3_BUILD_REPORT.md

### 评测集扩容至 395 题 + 金标准收紧：构建报告

> 目标：把评测集从 71 题扩到 300+，并「收紧金标准，让任何优化可判定」。
> 产物：`eval/cases_v3.json`（395 题）、`scripts/build_eval_v3.py`、`scripts/validate_eval_v3.py`。
> 全程**未改动生产检索代码与指标代码**，旧集 `rag_eval_cases.json` 保持原样。

---

#### §0 先修正一个前提（重要）

动手前我核对了旧集，发现 **「收紧金标准」这步在旧集上其实已经做过**：

- 71 条计分题的 `gold_source` 全部是 `topic-derived+llm-refined`；
- 每题平均 4.49 个 parent 标注，其中**只有 1.46 个是 1.0 档**、1.56 个 0.5 档、1.46 个 0.0 档；
- 70/71 题的 1.0 档 < 4（即已从「整篇 topic 全相关」收紧到「具体小节」）。

所以 `gold_granularity: parent` 里的 "parent" 不是「松」，而是**父子检索模式下可观测的最细单位**——再细到 child 就无法与返回结果的身份对齐了。

**真正的缺口是样本量**：71 题时最小可检出效应（MDE）约 **3.17pp**，任何小于 3pp 的优化都测不出来。

因此本次执行的实际内容是：
1. 把同一套「逐题分档」的严格标准**扩到 300+ 题**；
2. 并在平均意义上**比旧集更紧**（见 §2 对比表）；
3. 补齐旧集只覆盖 11 篇文档的短板，扩展到全部 **20 篇**。

---

#### §1 方法与关键机制

##### 1.1 流水线（`scripts/build_eval_v3.py`）

1. 从 chroma 索引读出全部 parent 切片（20 篇文档 → **81 个 parent**），按 topic 归组；
2. 每篇文档调用一次 LLM（deepseek-flash，temp=0.7）生成 16 个互不重复、且**仅凭该文档可作答**的问句，覆盖 定义/原理/步骤/对比/场景/排错/面试 七类 aspect；
3. 再调用一次 LLM（temp=0）对「每个问句 × 该文档全部 parent」打增益分：1.0 直接作答 / 0.5 部分支撑 / 0.0 同主题但不作答；强制每题**至少一个 1.0**；
4. 校验（每题至少一个 1.0、长度对齐、括号配对解析 JSON）、跨题去重、写盘。

##### 1.2 收紧的关键：显式 0.0 覆盖 0.5 兜底

`eval/metrics.py::chunk_gains` 有一条兜底规则：**同 topic 但不在金标准里的切片，自动给 0.5**。若只把「相关的那一节」写成 1.0，同主题其余小节仍会白拿 0.5，收紧就被抵消。

解法：**把同主题的全部 parent 显式写进 `relevant_chunks`（含 0.0 档）**。`chunk_gains` 的逻辑是「显式条目优先」——只要 parent 出现在金标准里，就按它的分档取值，兜底不再生效。因此**无需改任何指标代码**，收紧即可生效。

这一点已用实测确认：金标准的 `(source, parent_start)` 能命中检索实返切片（`chunk_rel` 的 recall/nDCG 非零），坐标没有错位。

##### 1.3 身份坐标

父子模式下检索返回的 parent 把 `start_index` 换成了 `parent_start`，所以金标准必须写 `[source, parent_start, gain]`。用 child 偏移会静默归零——本脚本从索引元信息直接取 `parent_start`，已校验一致。

---

#### §2 结果：紧致度对比（v3 比旧集更紧）

| 指标 | 旧集（71 题） | v3（395 题） | 说明 |
|---|---|---|---|
| 平均每题标注条数 | 4.49 | 4.09 | |
| **平均 1.0 档（正解）条数** | 1.46 | **1.21** | 越少越紧 |
| 平均 0.5 档条数 | 1.56 | 1.02 | |
| **平均 0.0 档（同主题负例）条数** | 1.46 | **1.86** | 越多越有区分度 |
| 唯一正解的题占比 | 36/71 = 51% | **309/391 = 79%** | |
| 含同主题 0 档负例的题 | — | **351/391** | 共 734 条难负例 |

增益总分布：1.0 档 478、0.5 档 403、0.0 档 734。

---

#### §3 可检出效应（MDE）的改善

用既有 A/B 两档（BASE vs EMB_JINA）的**逐题 nDCG 差值标准差** σ=0.0955 反推，
α=0.05 双侧、power=0.8（系数 2.80）：

| 样本量 | MDE（nDCG） |
|---|---|
| 71（旧集） | **3.17pp** |
| 300 | 1.54pp |
| **395（v3）** | **1.34pp** |

也就是说，**原本要 >3.2pp 的收益才显著，现在 >1.34pp 就能判定**。之前「换 embedding +0.78pp 不显著」的结论，在 395 题下会有更明确的判定力。

> 口径修正：此前口头说的「71 题 MDE ≈ ±2pp」是粗略估计；按真实差值方差算是 **3.17pp**，本次以数据为准。

---

#### §4 校验结果

- **结构**：395 题，`id` 无重复、`query` 无重复、无空 query；`load_cases` 成功返回 395 个 5 元组。
- **边界**：无「有 expected 但无正例」的题；4 条 oos 无金标准（不计分，符合旧口径）。
- **难度可比**：抽样 40 题实测（含重排）→ Recall@3 0.7668 / nDCG 0.8459 / 噪声 0.30 / nDCG 无截断，
  与旧集 71 题（0.79 / 0.87）同量级，说明新题难度可比、不是虚高或过难。
- **难负例**：351/391 题含同主题 0 档负例，共 734 条——这正是旧集缺的「同主题排错」信号。

---

#### §5 局限与待办（必须知道）

1. **AI 标注，需人工抽检**：`gold_source=llm-chunk-annotated-v3`，`_note` 已标注「待人工复核」。建议按 10% 随机抽检，重点看 0.5/0.0 边界题。
2. **跨文档假阴性**：新题要求「仅凭本文档作答」，但个别问句可能也能被其他文档部分回答（如「数据库里怎么避免死锁」）。当前把跨主题 parent 一律计 0.0，可能轻微低估召回。若要严谨，可对高相似度跨主题 parent 补标 0.5。
3. **旧集 75 题原样保留**：与 v3 合并后共 395，便于与历史报告对齐；但旧集金标准口径（4.49 条/题）与新集（4.09）略有差异，**分组看**更稳妥。
4. **默认入口未改**：`smoke_rag.CASES_FILE` 仍指向旧集，保证历史分数口径不断。用 v3 需显式传参。

---

#### §6 使用方式

```bash
# 单档评测（含重排）
RAG_PARENT_CHILD=1 venv/Scripts/python.exe scripts/ab_rerank_model.py \
    --cases eval/cases_v3.json --tag V3_B1

# 天花板归因
RAG_PARENT_CHILD=1 venv/Scripts/python.exe scripts/diag_acc_ceiling.py \
    --cases eval/cases_v3.json --onnx-dir <本地ONNX> --tag V3_CEIL

# 重新生成 / 扩容（例：每篇 20 题）
RAG_PARENT_CHILD=1 venv/Scripts/python.exe scripts/build_eval_v3.py \
    --per-doc 20 --include-existing --out eval/cases_v3.json

# 校验
RAG_PARENT_CHILD=1 venv/Scripts/python.exe scripts/validate_eval_v3.py \
    --cases eval/cases_v3.json --run-sample 40
```

复现提示：生成需 `LLM_MODEL/LLM_API_KEY/LLM_BASE_URL`（当前 deepseek-flash），共 40 次调用、约 24 分钟。

---

#### §7 结论

- **300+ 题已交付**：395 题（320 新 + 75 旧），覆盖全部 20 篇文档。
- **金标准更紧**：1.0 档 1.21/题（旧 1.46），79% 的题只有唯一正解，734 条同主题难负例。
- **可判定性提升**：MDE 3.17pp → **1.34pp**。
- **未动生产代码**：只新增两个脚本和一个数据文件。

下一步建议：用 `cases_v3.json` 重跑一次「换 embedding / 换重排模型」的 A/B——此前 ±1pp 级结论在旧集上不可判定，在新集上应能给出明确结论。

---

<a id="embedding-swap-validation"></a>

## 6、原名 EMBEDDING_SWAP_VALIDATION.md

### 换粗排 embedding 的离线验证结论：**不建议落地**

> 2026-10-29 实测。71 道 baseline 题（oos 不计），父子模式，重排模型恒为
> `bge-reranker-v2-m3-int8`（INT8），注入 K=3。
> 数据：`eval/reports/ceiling_BASE_K3_pc.json` vs `eval/reports/ceiling_EMB_JINA_pc.json`，
> 另有 `ceiling_EMB_JINA_*.json`（含重建索引的那一轮）。

---

#### §1 结论先行

把粗排 embedding 从 `BAAI/bge-small-zh-v1.5`（512 维 / 33M）换成
`jinaai/jina-embeddings-v2-base-zh`（768 维 / 约 110M），**方向是对的，但幅度太小，
统计上无法判定有效，而代价是实打实的**：

| | 现状 bge-small-zh | jina-base-zh | 差 |
|---|---|---|---|
| Recall@3 | 0.7909 | 0.7987 | +0.78pp |
| nDCG@3 | 0.8700 | 0.8742 | +0.42pp |
| Precision@3 | 0.5634 | 0.5751 | +1.17pp |
| **候选池召回 `recall_pool_all`** | 0.8498 | **0.8635** | **+1.37pp** |
| Top1 | 68/71 | 67/71 | **−1** |
| 噪声率 | 0.3052 | 0.2981 | −0.71pp |
| query 编码延迟 p50 | 2.2ms | **13.9ms** | **6.3 倍** |
| 端到端检索 p95 | 1093ms | **1256ms** | **+163ms（+15%）** |

**配对 bootstrap（20k 次重采样，同一批 71 题）**：

```
Recall@3 差值 +0.0078   95% CI [-0.0179, +0.0339]   ← 跨 0，不显著
nDCG@3   差值 +0.0042   95% CI [-0.0182, +0.0261]   ← 跨 0，不显著
逐题变化：9 题变好 / 6 题变差 / 56 题不变
```

71 题里只有 15 题发生了变化，还近乎正负抵消。**这就是噪声。**

---

#### §2 一个必须先说的前提：候选集里没有 bge-base-zh / bge-m3

上一版评估报告建议「换 bge-base-zh 或 bge-m3」，那是**基于通用认知写的，没有核对
本机实际能加载什么**。核对之后发现，fastembed 0.9.0 的 `TextEmbedding` 支持列表里，
**中文相关的一共只有两个**：

| 模型 | 维度 | 大小 | 备注 |
|---|---|---|---|
| `BAAI/bge-small-zh-v1.5` | 512 | 0.09GB | **现状** |
| `jinaai/jina-embeddings-v2-base-zh` | 768 | 0.64GB | Apache-2.0，本次唯一的替换候选 |

`BAAI/bge-base-zh-v1.5` 与 `BAAI/bge-m3` **不在 fastembed 的内置列表里**
（`bge-m3` 在 ModelScope 上只有 FP32 ONNX，`model.onnx_data` 就 2.27GB）。
要用它们得自己写加载器 + 改 `indexer.get_embeddings`，属于另一档工作量。

**所以本次只验证了 jina-base-zh**，它已经是「零代码改动路径」下能换到的最强选择。

---

#### §3 实验方法（关键：完全没动现库）

1. **独立索引**：新建 `chroma_kb_jina/`（collection 名相同但 persist 目录不同，完全隔离）。
   现网 `chroma_kb/` **一次都没被写过**。
2. **本地直载**：langchain 的 `FastEmbedEmbeddings` 不传 `local_files_only`（默认 False），
   断网必失败。所以实验期用一个三行的 `Embeddings` 薄封装直接持有
   `fastembed.TextEmbedding(..., local_files_only=True)` 并替换 `indexer.get_embeddings`
   —— 生产代码未改。
3. **同口径**：两套都跑同一批 71 题、同一个重排模型、同一套 `eval/metrics.chunk_rel` 口径；
   `--dump-per-case` 输出逐题明细用于配对检验。

新增 `diag_acc_ceiling.py` 的三个参数即可复用：`--embed-model` / `--persist-dir` / `--build`。

---

#### §4 为什么收益这么小：不是模型不准，是标注结构与 K=3 的矛盾

这是本次最有价值的副产品。原本的假设是「top-6 child 覆盖不到 gold，是因为向量不够准」，
实测数据不支持这个假设：

| 检索深度 | 候选池 | pool_all | 说明 |
|---|---|---|---|
| top-6 child（现状） | 4.21 parent | 0.8498 | 现状 |
| top-30 child（OF=5） | 11.45 parent | **0.9960** | **gold 几乎全在库里，也没多深** |

即：**gold 早就召得回来**（99.6% 覆盖率、71 题里只有 1 题整池无 gold），问题是
**每条 query 平均有 4.49 个 gold 小节**（金标准定义），而注入窗口只有 3 个坑。
向量再准，"更像"的那节也挤不掉别的 gold —— **它们本来就都是正确答案**。

这条正好接得上因果类归因的结论（见 `docs/reports/REPORTS.md` §1）：

```
召回漏 15.02pp（池里没有）→ 换 embedding 能救的部分，实测只有 1.37pp
窗口漏  1.62pp（3 个坑装不下 4.49 条）→ 换 embedding 一点用没有，只跟 K 有关
排序漏  4.27pp（排错顺序）→ 归重排模型管
```

而且**这条路的理论上限是可以算出来的**：K=3 的 Recall@3 物理上限 0.914（POOL5 的 oracle
实测 0.9139，交叉验证通过）。从 0.7909 到 0.9139 一共 12.30pp 的空间，换 embedding
理论上最多把人push到那里。**jina 实测只走了其中的 6%（0.78 / 12.30）**，却付出了
6.3 倍的编码延迟和 15% 的端到端延迟。

补充一个反直觉的点：**延迟的大头不是模型本身**。query 编码只涨了 11.7ms，但端到端
检索 p95 涨了 163ms —— 剩下 150ms 来自向量库侧的距离计算（512 维 → 768 维，
HNSW 每条候选的距离算得更多）。所以"embedding 很便宜，换了不心疼"这个判断是错的。

---

#### §5 建议：不落地，改投评测集

**不切换粗排 embedding。** 三条理由，任一条单独成立：

1. **收益不可判定**：+0.78pp Recall，95% CI 跨 0，15/71 题发生变化且近乎正负抵消。
2. **代价确定**：query 编码 ×6.3、端到端 p95 +163ms（占当前检索耗时的 15%，而 L1 硬门禁
   是 3.0s，这条缓冲不该花在一个测不出来的收益上）、磁盘 +641MB、维度 512→768。
3. **落地要改代码**：`FastEmbedEmbeddings` 不支持离线加载，现状之所以能离线跑，
   是因为旧模型的 GCS 扁平缓存恰好命中。换成 HF 结构的新模型就得改 `indexer.py`。

**杠杆更高的是评测本身。** 用本次数据反推：要在这份评测上判定 1pp 的改进，
需要的样本量是几百量级（当前逐题差值的标准差下，71 题的最小可检出效应约 ±2pp）。
在此之前，任何 ±2pp 以内的"微调收益"都只是在给噪声排名。**扩到 300+ 题、
并把金标准从"整篇 parent 全相关"细化之后再来评这一项，才有决策价值。**

**什么时候值得重做这个实验**（满足任一即可）：

- 评测集扩到 300 题以上，且单题 gold 条数口径收紧之后（当前 4.49 条/题偏松）；
- 知识库从 248 片扩到千片量级——**slice 变多后，粗排判别力的重要性会上升**，
  在小库上测出来的结论不能直接外推到扩库之后（这条最有可能翻盘）；
- 出现权限 tens 可商用的中文 stronger contest embedding 且量化版可用
  （本机的 CPU 场景下量化版是关键，FP32 的代价太高）。

---

#### §6 关于 bge-m3：本次没测，理由与成本已估

**没测**，理由三条，不是偷懒：

1. 它不在 fastembed 内置列表里，得先写加载器（约等于重做一遍 `onnx_xenc.py` 的
   embedding 版本）；
2. 权重只有 FP32 ONNX，`model.onnx_data` 就 **2.27GB**，CPU 上单次编码大概率在
   百毫秒量级（jina 已经 13.9ms 了），端到端检索会明显超出缓冲；
3. 已有证据表明边际收益很低：参数量比 bge-small 大 3~4 倍的 jina 只带来统计上
   看不出来的 +0.78pp。参数再涨 5 倍到 568M，按这条曲线也不该期待跳变。

如果仍要实测，成本约：下载 2.3GB（ModelScope 7.6MB/s 约 5 分钟）+ 写加载器 +
重建索引 + 跑评测，合计 1 小时上下。要不要做，等你决定。

---

#### §7 复现 / 清理

```bash
cd Tutor-Chatbot-main/backend
ONNX=".model_cache/models--onnx-community--bge-reranker-v2-m3-ONNX/snapshots/930e38609a0e848e391a2bba0bf52317850e7b25"

# 拉权重（ModelScope 可达，HF / hf-mirror 现在都是 ProxyError）
venv/Scripts/python.exe scripts/fetch_modelscope_fastembed.py \
    --repo jinaai/jina-embeddings-v2-base-zh \
    --files onnx/model.onnx tokenizer.json tokenizer_config.json \
            config.json special_tokens_map.json

# 建对照索引 + 归因（--persist-dir 独立，不动现库）
RAG_PARENT_CHILD=1 venv/Scripts/python.exe scripts/diag_acc_ceiling.py \
    --tag EMB_JINA --onnx-dir "$ONNX" \
    --embed-model jinaai/jina-embeddings-v2-base-zh \
    --persist-dir chroma_kb_jina --build --dump-per-case
```

**清理**：对照库 `chroma_kb_jina/`、`eval/baseline_meta_jina.json`、缓存
`.model_cache/jina-embeddings-v2-base-zh/`（641MB）均为本次实验产物，确认不继续时可删除；
现网 `chroma_kb/` 未被改动，无需回滚。

---

<a id="retrieval-accuracy-ceiling"></a>

## 7、原名 RETRIEVAL_ACCURACY_CEILING.md

### 检索准确率天花板归因（重排模型不变的前提下）

> 结论口径：71 道 baseline 题（oos 不计），父子模式，注入 K 条，重排模型恒为
> `bge-reranker-v2-m3-int8`（INT8 ONNX）。数据全部来自本次实测，见
> `eval/reports/ceiling_*.json`。
>
> 生成方式：`scripts/diag_acc_ceiling.py --tag <档位> [--overfetch N] [--pool-k N]
> [--inject-k N] [--adjacent-radius N] [--fusion-w W]`（单档单进程）。

---

#### §1 结论先行

**瓶颈不在重排模型，而在「召回预算 + 注入预算」这两个量。** 证据是三段损失分解：

现状（候选池 4.21 个 parent，注入 3 条，Recall@3 = 0.7909）的 20.91pp 总损失里：

| 损失段 | 占比 | 含义 | 谁负责 |
|---|---|---|---|
| **召回损失** | **15.02pp（72%）** | gold 小节压根没进候选池，重排根本看不见 | **粗召回（向量 + BM25 + RRF）** |
| 窗口损失 | 1.62pp（8%） | gold 在池里，但 3 个坑装不下（gold 均值 4.49 条/题） | 注入预算 K |
| 排序损失 | 4.27pp（20%） | gold 在池里也装得下，但被排到窗口外 | **重排模型** |

所以：**继续换重排模型，理论上最多只能再拿 4.27pp Recall / 5.96pp nDCG——
这是它的全部剩余空间**，而且五个候选模型的实测极差只有 1.7pp，说明
「拿走这 4.27pp」在当前模型代际内做不到。真正的 72% 在召回层。

**但它也不该直接从召回层入手硬扩。** 实测扩池的结果是「此消彼长」：

| 候选池 | Recall@3 | 召回漏 | 窗口漏 | 排序漏 | rerank p95 |
|---|---|---|---|---|---|
| **4.21（现状）** | **0.7909** | 15.02 | 1.62 | 4.27 | 1014ms |
| 6.00 | 0.7745 | 11.41 | 3.27 | 7.87 | 1147ms |
| 7.37（相邻扩展） | 0.7806 | 6.27 | 4.89 | 10.78 | 1430ms |
| 9.24（overfetch×3） | 0.7883 | 2.98 | 6.81 | 11.39 | 1519ms |
| 11.45（overfetch×5） | 0.7874 | 0.40 | 8.21 | 12.65 | 1812ms |

把召回漏从 15.02pp 压到 0.40pp（几乎全捞回来），**Recall@3 纹丝不动**
（0.7909 → 0.7874，还掉了 0.35pp）。原因：每救回 1pp 召回漏，就转化成
约 0.5pp 窗口漏 + 0.5pp 排序漏——**重排在更大的杂池里把新增的干扰排到了前面**。

---

#### §2 归因方法

在 `_rerank_docs` 上挂一层钩子，把送进重排的候选池原样捞出来，然后算三层阶梯：

```
recall_pool_all  整个候选池全部注入（解除窗口约束，但仍在池的约束内）
recall_oracle    候选池内按真实增益贪心取最优 K 条（解除排序约束）
recall_actual    当前实际结果
```

三段损失（相加恒等于总损失）：

```
召回损失 = 1 − recall_pool_all     候选池外，任何排序都救不回来
窗口损失 = recall_pool_all − recall_oracle    K 个坑装不下
排序损失 = recall_oracle − recall_actual      重排的真实剩余空间
```

两点必须说清，否则会误读：

1. **oracle 不是全局上限**。它假定池的外延固定。换成更大的池，oracle 会跟着涨
   （0.8336 → 0.9139），那部分增量属于召回层，不属于排序层。
2. **POOL5 的 oracle = 0.9139，与先前独立推导的 K=3 物理上限 0.9159 吻合**，
   交叉验算通过——说明这套归因是自洽的，不是自洽地编造数字。

---

#### §3 关键旁路证据

- **金标准均值 4.49 条/题，池里平均只有 2.31 条 gold**：候选池里一半以上是噪声，
  重排是在一个 55% 都是垃圾的池子里挑。
- **池平均只有 4.21 条，却要从中选 3 条**：去重后实际选择空间只有 1.2 条的余量。
  这解释了为什么 C3_pool6 / C4_pool8 之前「测了没反应」——`parent_pool_k` 试图
  截断一个本来就只有 4.21 条的池，参数根本没生效。
- **top-30 child 里能覆盖 99.6% 的 gold**（overfetch×5 时 pool_all = 0.9960）：
  gold 全都在库里、也不算深，只是**排不进 top-6**。这是典型的「粗排模型判别力不足」，
  而不是「库里没有」。
- **hard_miss 只有 1 题**：整池一条 gold 都找不到的题，71 题里只有 1 道。
  进一步佐证：问题从来不是「召不到」，是「排不进前面的窗口」。

---

#### §4 重排模型不变，还能提高吗（7 种改法实测）

全部保持 `bge-reranker-v2-m3-int8` 不动：

| # | 改法 | Recall | nDCG | 噪声率 | 硬负例 | 检索 p95 | 判定 |
|---|---|---|---|---|---|---|---|
| 0 | **现状**（OF=1, K=3） | 0.7909 | **0.8700** | 0.3052 | 0.0897 | 1093ms | 基准 |
| 1 | 扩池 3× | 0.7883 | 0.8713 | — | — | 1577ms | 持平，+48% 延迟 |
| 2 | 扩池 5× | 0.7874 | 0.8701 | — | — | 1838ms | 持平，+79% 延迟 |
| 3 | 扩池 3× + 截断 6 条 | 0.7745 | 0.8578 | — | — | 1228ms | **劣化** |
| 4 | 相邻小节扩展 r=1 | 0.7806 | 0.8641 | 0.3216 | 0.1266 | 1503ms | **劣化** |
| 5 | 相邻扩展 r=1 + OF=2 | 0.7787 | 0.8664 | 0.3239 | 0.1132 | 1736ms | **劣化** |
| 6 | 名次融合先验 w=0.5 | 0.778 *（小池口径） | 更差 | — | — | — | **劣化**（代码注释已记录 0.5/1.5 均劣） |
| 7 | 注入 K=4（现状池） | 0.8340 | 0.8624 | 0.3885 | 0.1152 | 1134ms | Recall +4.3pp，nDCG **−0.76pp**，硬负例 +28% |
| 8 | 注入 K=5（现状池） | 0.8383 | 0.8582 | 0.4075 | 0.1393 | 1113ms | Recall +4.7pp，nDCG **−1.18pp**，硬负例 **+55%** |
| 9 | 注入 K=5 + 扩池 3× | **0.8772** | 0.8843 | **0.4925** | 0.1318 | 1594ms | Recall +8.6pp，nDCG +1.43pp，**噪声 +18.7pp** |
| 10 | K=5 + 扩池 3× + 融合 0.5 | **0.8860** | 0.8820 | 0.4791 | 0.1502 | 1592ms | Recall 全场最高，噪声仍崩 |

补充：给重排喂更多信息（`rerank_text=parent_head`）在大池下反而更差
（nDCG 0.848 vs 0.8843）且延迟翻倍到 2984ms，否决。
`head_chars` 只对 `parent_head` 生效，对现状的 `child_title` 无作用
（实测两档逐项一致，不是 bug）。

**读法**：所有「不动注入预算」的改法（#1–#6）都拿不到 NPV 为正的收益。
剩下的路只有放宽注入条数，而它有个很干净的代价梯度：

| K（现状池） | Recall | Precision | nDCG | 噪声率 | 硬负例率 |
|---|---|---|---|---|---|
| **3（现状）** | 0.7909 | **0.5634** | **0.8700** | **0.3052** | **0.0897** |
| 4 | 0.8340 | 0.4525 | 0.8624 | 0.3885 | 0.1152 |
| 5 | 0.8383 | 0.3648 | 0.8582 | 0.4075 | 0.1393 |

**Recall 每涨 1pp，噪声率涨 2.2pp、硬负例涨 1.1pp**——多带的那一两条，
几乎全是垃圾。这就是上个周期把 K 锁在 3 的原因：注入给 LLM 的不是 3 条答案，
而是 3 条里 ~0.9 条噪声 + 2.1 条答案；放到 K=5 就变成 2 条噪声 + 3 条答案。
若同时扩池（#9）能让 nDCG 略过线（+1.43pp），但噪声要到 0.49——一半是垃圾，
生成层的忠实度大概率反向受损，而这个损失不在检索层指标里体现。

**所以：在当前架构下，不换任何模型，检索层的 nDCG 已经贴着自己的天花板了。**

---

#### §5 唯一未测且有理论空间的路：换粗排（embedding）模型

这是「重排模型不变」约束下唯一还有 ~12pp 空间的方向，依据是上面 §3 的旁路证据：

```
现状：top-6 child    → parent 池 4.21 条 → pool_all = 0.8498
扩池：top-30 child   → parent 池 11.45 条 → pool_all = 0.9960
                                            潜在增量 +14.6pp
```

gold 全在 top-30 里，只是进不了 top-6。**把 gold 推进 top-6** 的办法不是扩大
预算然后再想办法压回来（已证明无效），而是**提高粗排本身的判别力**：

- 现状向量侧是 `bge-small-zh-v1.5`（约 33M），候选可用 `bge-base-zh-v1.5` /
  `bge-m3`（需重新建库，成本是一次性的，不增加在线延迟）
- 预期收益路径：pool_all 0.85 → 0.93 左右 ⇒ oracle 0.8336 → ~0.89 ⇒
  若排序损失仍维持 4pp，Recall@3 可达 ~0.85（现状 0.7909，+6pp）
- **零在线延迟代价**（embedding 计算量与延迟远低于交叉编码器），这是它相对
  扩池路线的最大优势：扩池是拿 CPU 换召回，换 embedding 是白拿。

配套要做的两件事（缺一不可）：

1. **扩评测集**。71 题 / 4.49 条 gold 的样本量撑不起 ±5pp 的判定，且生成层的
   judge 与上线模型同源（deepseek-flash），只能看趋势不能看绝对值。
2. **先做离线增益验证**：在当前 chroma 库上重建索引，跑同一批 71 题，
   只看 `recall_pool_all` 是否上涨——这一步不需要动重排、不需要重测全流程。

---

#### §6 缓存缺失问题（**已修复**，2026-10-29 01:2x）

**现象**：`.model_cache/bge-reranker-v2-m3-int8` 目录不存在，加载报
`Could not find the model tar.gz file`，随即静默降级为纯向量排序（指标退回 B0 的 0.7539）。

**根因链**（fastembed 0.9.0）：

1. `retriever.get_reranker()` 传 `local_files_only=not CONFIG.rerank_online`，
   而 `rerank_online` 默认 False ⇒ fastembed **只查本地，不联网**；
2. HF 路：模型源是 `onnx-community/bge-reranker-v2-m3-ONNX`，
   `model_file=onnx/model_int8.onnx`，但该仓库**当前 main revision 已不含 int8 文件**
   （只剩 FP32 的 model.onnx），按 main 解析必然失败；
3. 退回 GCS 路：找扁平目录 `<cache_dir>/bge-reranker-v2-m3-int8`，不存在 ⇒
   抛 `ValueError` ⇒ `get_reranker()` 吞异常返回 `None`。

**修复**：权重本体并未丢失——它在本机同仓库的**旧 revision 快照**（`930e3860…`）里，
本轮全部评测正是用它跑的。把它组装回扁平目录即可，用 NTFS 硬链接避免多占 570MB：

```bash
venv/Scripts/python.exe scripts/restore_rerank_cache.py     # 组装 + 加载自检
```

**修复后验证**（`ab_rerank_model.py --tag NATIVE_RESTORED`，默认路径、不带任何绕过参数）：

| 指标 | 退化时 | 修复后 | 与 ONNX 直载档差值 |
|---|---|---|---|
| Recall@3 | 0.7539 | **0.7909** | 0.0 |
| nDCG@3 | 0.8161 | **0.8700** | 0.0 |
| degraded_rate | 1.0 | **0.0** | — |

逐项与 `--onnx-dir` 直载档完全一致（`delta_vs.B1` 全为 0.0）。

**更正上一版的一处不准确表述**：本节原先写「建议把 `rerank=unavailable` 计入 degraded
硬门禁」——这是错的。`retriever.py:401` **早就在做了**：`_mark_degraded(trace,
"rerank_unavailable")` 会写进 `trace["degraded"]` 与 `degraded_components`，
并被 `eval/slo_check.py` 分类统计。已做对照实验验证它确实触发（把缓存目录改名后，
单次检索的 trace 即为 `degraded=rerank_unavailable`、`rerank=unavailable`，
`get_reranker()` 返回 `None`）。

真正缺口不在「有没有记录」，而在**生产运行时没人盯这一个字段**：
评测入口（`ab_rerank_model.py`）有 `degraded_rate_ok ≤ 0.02` 的硬门禁，一跑就能发现；
但 `/chat` 请求链路只写了一行 WARNING 日志。建议把它接到监控告警。

---

#### §7 复现指令

```bash
cd Tutor-Chatbot-main/backend
ONNX=".model_cache/models--onnx-community--bge-reranker-v2-m3-ONNX/snapshots/930e38609a0e848e391a2bba0bf52317850e7b25"

# 现状归因
RAG_PARENT_CHILD=1 venv/Scripts/python.exe scripts/diag_acc_ceiling.py \
    --tag BASE_K3 --onnx-dir "$ONNX" --dump-per-case

# 扩池
RAG_PARENT_CHILD=1 venv/Scripts/python.exe scripts/diag_acc_ceiling.py \
    --tag POOL3 --onnx-dir "$ONNX" --overfetch 3

# 扩注入
RAG_PARENT_CHILD=1 venv/Scripts/python.exe scripts/diag_acc_ceiling.py \
    --tag FIN_K5_OF3 --onnx-dir "$ONNX" --overfetch 3 --inject-k 5
```

报告落 `eval/reports/ceiling_<tag>_<时间戳>.json`；`--dump-per-case` 会附每题明细。

---

<a id="v3-ab-significance-report"></a>

## 8、原名 V3_AB_SIGNIFICANCE_REPORT.md

### 395 题新集上的 A/B 显著性检验报告

> 目的：用扩容+收紧后的评测集（395 题，计分 391）重跑此前「不可判定」的两项对比——
> 换重排模型、换粗排 embedding——看能否给出明确结论。
> 基线：`bge-small-zh-v1.5` 粗排 + `bge-reranker-v2-m3-int8` 重排（= 生产现状）。
> 方法：同一批题、同金标准、配对 bootstrap（2 万次重采样），95% CI。
> 数据：`eval/reports/ceiling_V3_{BASE,RR_JINA,EMB_JINA}_*.json`。

---

#### §1 结论先行

| 对比 | nDCG 变化 | 95% CI | 判定 | 检出该效应所需样本 |
|---|---|---|---|---|
| **换重排模型**（→ jina-reranker-v2） | **+0.60pp** | [−0.56, +1.81] | **不显著** | 3,053 题 |
| **换粗排 embedding**（→ jina-base-zh） | **+0.71pp** | [−0.38, +1.77] | **不显著** | 1,807 题 |

**两项都没测出显著差异**，但原因不是"没差别"，而是**效应量（0.6~0.7pp）小于当前评测集的分辨率（MDE 1.34pp）**。
要判定一个 0.7pp 的真效应，需要 ~1,800~3,000 题；395 题仍然不够。

---

#### §2 三档完整指标（391 题计分）

| 档位 | Recall@3 | nDCG@3 | Prec@3 | 噪声率 | Top1 | Top3 | 池召回 | Oracle | 重排空间(nDCG) | 重排p95 |
|---|---|---|---|---|---|---|---|---|---|---|
| **BASE**（现状） | 0.8586 | 0.8550 | 0.4761 | 0.4118 | 372/391 | 387/391 | 0.9264 | 0.9183 | **10.13pp** | 1175ms |
| 换 JINA 重排 | 0.8620 | 0.8610 | 0.4765 | 0.4228 | 373 | 389 | 0.9264 | 0.9183 | 9.53pp | 1029ms |
| 换 JINA embedding | **0.8653** | **0.8621** | **0.4829** | **0.4062** | 371 | 386 | **0.9414** | **0.9344** | 10.75pp | 1170ms |

配对逐题胜负（对照 vs 基线）：

- **换重排**：nDCG 59 胜 / 55 负 / 277 平——几乎五五开，纯噪声级差异。
- **换 embedding**：nDCG 43 胜 / 29 负 / 319 平——**方向一致偏正**（四个指标全为正），但幅度不够。

---

#### §3 最重要的发现：收紧金标准改变了归因，重排的真实空间比之前大一倍

同一套链路、只换评测集，三段损失归因发生结构性变化：

| 损失段 | 旧集 71 题 | **新集 391 题** | 变化 |
|---|---|---|---|
| 召回损失（gold 没进池） | 15.02pp（72%） | **7.36pp（52%）** | ↓ 7.66pp |
| 窗口损失（K 装不下） | 1.62pp（8%） | **0.81pp（6%）** | ↓ |
| **排序损失（重排没排进窗口）** | 4.27pp（20%） | **5.97pp（42%）** | **↑ 1.70pp** |
| **重排 nDCG 空间** | 5.96pp | **10.13pp** | **↑ 4.17pp** |

**为什么**：旧金标准把「整个 topic 的 parent 全标 1.0」，所以只要检索到**同主题任一 parent** 就算命中，「排错小节」不可见——重排的失职被掩盖了。收紧到「只有真正回答该问句的那一节 = 1.0」后，同主题的错误小节降为 0.0，重排「选错小节」第一次被计入损失。

⇒ **我此前『重排只剩 4.27pp、只占 20%，不值得再投入』的结论，是旧金标准造成的低估，现予更正。**
在新集上，重排是**占比 42%、nDCG 空间 10.13pp 的主要瓶颈**。

---

#### §4 机制证据：换 embedding 会把瓶颈进一步推给重排

换 jina embedding 后：

- 池召回 0.9264 → **0.9414（+1.5pp）**，召回损失 7.36 → 5.86pp —— 粗排确实变好了；
- 但排序损失反而 5.97 → **6.91pp（+0.94pp）** —— 池里多了正确答案，重排却没把它们排进前 3；
- 净效果 Recall +0.67pp、nDCG +0.71pp —— **收益被重排的失职吃掉大半**。

这解释了为什么换 embedding 总是"看着有改善、统计上不显著"：**瓶颈不在能不能召到，而在召到之后排不对。**

---

#### §5 对既有结论的修订

| 此前的说法 | 本次修正 |
|---|---|
| 「重排只剩 4.27pp，占 20%，别再研究重排」 | **部分推翻**：收紧后重排 5.97pp / 占 42% / nDCG 空间 10.13pp，是主要瓶颈 |
| 「换重排模型候选极差仅 1.7pp，没意义」 | **仍成立**：JINA 换上去只 +0.6pp 且不显著，噪声还变差 |
| 「换 embedding +0.78pp 不显著」 | **仍成立**，且给出定量边界：要判定 0.7pp 需 ~1,800 题 |
| 「评测集扩容后能判定 ±1pp」 | **修正**：395 题 MDE≈1.34pp，仍判不了 0.7pp；要 1,800+ 题 |

**综合判断**：
- **"在现有几个重排模型之间挑一个更好的"这条路，已被 391 题证实无效**——不要继续做模型选型。
- **但"重排"作为技术方向值得继续投入**，因为它是当前最大瓶颈（42%、10pp）；只是杠杆不在换 checkpoint，而在**重排的用法**：输入文本粒度、候选池构造、与 RRF 的名次融合、或换架构（更大的/LLM-based reranker）。

---

#### §6 建议

1. **停止重排模型选型**（JINA/base278M/FP32 均无显著优势，JINA 噪声更差）。
2. **把"重排"作为主攻方向，但不是换模型**：优先测「重排输入文本」（`child_title` vs `parent_head` vs 摘要拼接）与「RRF 名次融合权重」——这两项零模型成本，且直接针对 10pp 的排序损失。
3. **换 embedding 暂缓**：方向为正但需 ~1,800 题才能判定；当前 641MB + 15% 延迟的代价换一个不可判定的 0.7pp，不划算。
4. **评测集若要继续扩**：目标不是 300，而是 **1,500~2,000**——才够判定 0.7pp 级效应。或者，若只关心 ≥1.5pp 的改进，395 题已够用。

---

#### §7 局限

- 金标准为 LLM 标注（`llm-chunk-annotated-v3`），需人工抽检；标注噪声会同时抬高方差、压低显著性。
- 换 JINA embedding 臂用的是独立索引 `chroma_kb_jina`，与 `chroma_kb` 的 chunk 切分一致但**未做二次核对**。
- "重排空间 10.13pp"是 **oracle 上界**（假定池内 gold 能被完美排序），不等于可达收益。
- 结论仅对当前小库（248 child / 81 parent）成立，库扩到千片后需重评。

---

#### §8 产物

- `eval/reports/ceiling_V3_BASE_20261029_085720.json`
- `eval/reports/ceiling_V3_RR_JINA_20261029_090321.json`
- `eval/reports/ceiling_V3_EMB_JINA_20261029_091105.json`
- `scripts/ab_significance.py`（配对 bootstrap + 需样本量估算）

---

<a id="test-report"></a>

## 9、原名 TEST_REPORT.md

### AI Classroom 系统测试报告

> **测试范围**：T0–T7 全部已交付功能 + T9 要求的专项测试
> **报告日期**：2026-10-05
> **代码基线**：`e9c2555`（含 Bug A/B 修复）**+ 本轮 Bug C/D 未提交改动**（见第五节）
> **执行环境**：Windows / Python 3.13.12（venv）/ MySQL 8.0.41 / ChromaDB 落盘 / DeepSeek `deepseek-flash`

---

#### 一、测试结果总览

| # | 测试项 | 类型 | 成本 | 结果 |
|---|---|---|---|---|
| 1 | 单元测试 `run_tests.py` | 纯逻辑 | 零（不联网） | ✅ **83 / 83** |
| 2 | API 测试 `smoke_db.py` | 端到端 | 3 次 LLM | ✅ 通过 |
| 3 | Profile 测试 `smoke_profile.py` | 端到端 | 8 次 LLM | ✅ 通过 |
| 4 | 个性化测试 `smoke_personalize.py` | 端到端 | 3 次 LLM | ✅ 通过 |
| 5 | Agent 测试 `smoke_resource.py` | 端到端 | 2 次 LLM | ✅ 通过 |
| 6 | RAG 检索专项 `smoke_rag.py` | 端到端 | **零**（不调 LLM） | ⚠️ top3 6/7，top1 4/7 |

**总判定：通过。** 唯一有瑕疵的是 RAG 检索准确率（第六节），不影响 MVP 验收，但已记录为已知不足。

---

#### 二、关于测试用例的一处替换（必须说明）

`docs/plans/PLANS.md` 的 **T9.2 RAG 测试** 原文要求测试 `HashMap` / `JVM` / `ConcurrentHashMap`，**T6.4 个性化测试**原文要求用「HashMap 为什么线程不安全？」。

**这三项全部改成了操作系统 / 计算机网络题目**，原因是知识库范围在项目执行中做了收敛：范围确定为「操作系统 + 计算机网络」，明确不做 Java 八股。`knowledge/` 下现有 8 篇文档：

```
cs/os/      死锁 / 虚拟内存与页面置换 / 进程与线程 / 进程调度算法
cs/network/ TCP 三次握手与四次挥手 / HTTPS 与 TLS 握手 / TCP/IP 分层模型与 ARPDNS / TCP 与 UDP
```

计划文档里的 Java 用例是选题时的占位符，若沿用会因知识库根本没这些内容而得到一个「全部未命中」的无意义结论。**替换后的用例在语义位置上等价**：同样是「库内知识点 / 跨知识点 / 库外」三类。

对应到本次执行：

| 计划原用例 | 本次实际用例 |
|---|---|
| HashMap 为什么线程不安全？（T6.4） | 讲讲死锁（T6.4） |
| HashMap / JVM / ConcurrentHashMap（T9.2） | 死锁 / TCP 三次握手 / HTTPS / TCP vs UDP / 页面置换 LRU（T9.2） |

---

#### 三、单元测试（`run_tests.py`）— 83 / 83

7 个测试文件，0.24 秒跑完，不联网不花钱：

| 文件 | 覆盖内容 | 条数 |
|---|---|---|
| `test_app` | 应用装配 | 基线 |
| `test_graph` | Agent 图调用链 | 基线 |
| `test_state` | `ChatRequest` 字段校验 | 基线 |
| `test_profiler` | 画像沉淀规则（漂移、定级、行为分类） | 22 |
| `test_personalize` | 个性化提示词组装 | 21 |
| `test_repository` | 状态隔离 + **本轮新增 2 条** detach 回归 | 8 + 2 |
| `test_resource` | 备课大纲解析与空壳判定 | 13 |

本轮新增的两条（`DetachedSessionTests`）见第五节 Bug A。

---

#### 四、端到端验收结果

##### 4.1 API 测试（T9.1）— `smoke_db.py` ✅

覆盖 T3.6 的五项能力 + T4 的成员校验与状态持久化。

| 步骤 | 结果 |
|---|---|
| 创建用户 / 课堂 / 加入课堂 | 课堂 20，成员 `e2e_student` + `e2e_teacher` |
| 多轮对话（不回传任何状态） | hint_level **0 → 2 → 3**，回答长度 **196 → 2459 → 1374 字** |
| 每次都能从库里读回状态 | topic=死锁，三级 hint_level 全部与返回值一致 |
| 记录累计 | 6 条（3 问 3 答） |
| 非成员发消息 | **403** ✓ |

要点：这里刻意**不传 `topic` / `hint_level` / `history`**，全部从数据库恢复，多轮递进依然正确——验证了「服务端是状态唯一真相源」。

##### 4.2 Profile 测试（T9.3）— `smoke_profile.py` ✅

两个学生各跑一段真实对话，画像自动沉淀：

| 学生 | 对话 | 最终画像 |
|---|---|---|
| A | 连续 4 轮「死锁」都没懂 | `beginner`，`{死锁: 0.0}`，3 条薄弱点，偏好「需要把原理展开才听得懂，别只给结论」 |
| B | TCP 握手 / 进程线程都答对了 | `advanced`，`{tcp三次握手: 0.8, 进程与线程的区别: 0.8}`，均值 0.8，偏好「吸收快，可以直接给标准答案和追问」 |

行为流水（A）：`ask_question → request_explanation → request_explanation → repeat_question`，四种 `action` 全部出现。

`misconception` 抓取正常：A 第一轮说「死锁是不是就是进程死循环卡住」，被准确记为「把死锁误解为单个进程死循环卡住，未理解是多进程互相等待资源」。这条正是当初 bug #5 的验证点，现已稳定。

##### 4.3 个性化测试（T6.4 / T9.3）— `smoke_personalize.py` ✅

同一道题「讲讲死锁」，三种画像：

| 学生 | 画像 | 起步深度 | 实际 hint_level | 回答长度 | 是否注入画像 |
|---|---|---|---|---|---|
| A | beginner（死锁 0.2） | 0 | **0** | 216 字 | 是 |
| B | advanced（死锁 0.9） | **1** | **1** | 323 字 | 是 |
| C | 无（新学生） | 0 | 0 | 431 字 | **否** ✓ |

关键证据：

- **B 被画像把起步深度从 0 抬到 1**——正是 `suggest_start_level()` 的设计意图：这个知识点历史掌握度 0.9 ≥ 0.75，不必再从一句话结论讲起。
- **C 完全没有被注入任何画像段落**（`personalized=false`），冷启动退化正确。否则等于给每个新学生凭空贴 beginner 标签。

两份注入段落的实际差异（节选）：

```
A：先给结论再解释为什么；术语顺手用一句话说明；一次只讲一个要点；
   「死锁」他此前一直掌握得不好（0.2），当作完全没学过来讲。
B：不用铺垫基础概念，直接进入要点和易错点；多给面试追问的边界情况；
   「死锁」他此前掌握得不错（0.9），重点放在面试追问上。
```

##### 4.4 Agent 测试（T9.4）— `smoke_resource.py` ✅

Student → Teaching Agent 见 4.1–4.3；Teacher → Resource Agent：

| 步骤 | 结果 |
|---|---|
| 主题「死锁」检索 | 命中 ✓ |
| 大纲结构 | 4 章节 × 4 要点，另有目标 3 条、常考要点 3 条、例子 2 个、提问 2 个、总结 2 条 |
| 生成 .pptx | `generated/死锁：从四个必要条件到工程排查_20261005_162550.pptx`，**40035 字节** |
| 下载 | 字节数一致，Content-Type 正确 |
| 学生调用 | **403** ✓ |
| 非成员「教师」调用 | **403** ✓ |
| 主题含斜杠（「进程/线程 的区别」） | 文件名正确清洗，未生成奇怪目录 ✓ |

##### 4.5 RAG 检索专项（T9.2）— `smoke_rag.py` ⚠️

见第六节。

---

#### 五、本轮发现并修复的缺陷

这一节值得单独讲：**这 5 个缺陷全部是在「跑测试」这件事本身的过程中发现的，静态看代码一个都没看出来。** 尤其前两个，在写 `docs/specs/API_CONTRACT.md` 通读 `app.py` 时也没发现——必须真实发请求才暴露。

##### Bug A：`GET /classrooms/{id}/messages` 稳定返回 500

| | |
|---|---|
| 现象 | `smoke_db.py` 在第 4.1 轮之后崩溃，`GET /classrooms/17/messages` → 500 |
| 根因 | 序列化写在 `with get_db()` 块**外面**，此时 session 已关闭；列表推导里访问 `m.user.username` 触发 relationship 延迟加载 → `DetachedInstanceError` |
| 为什么以前没暴露 | 这是聊天室主数据源，但之前的验收可能都在 `username` 字段加入之前，或数据恰好没触发 relationship 访问 |
| 修复 | ① `list_messages` 加 `joinedload(Message.user)`（顺带把 100 条消息 101 次 SQL 降到 1 次）；② 序列化移进 session 生命周期内 |
| 为什么这样修 | 只加 `joinedload` 能修这次，但只要有人再往 with 块外挪一行就会复发。**查询层预加载 + 序列化位置约束**，两层一起才堵得住 |
| 回归测试 | 新增 `DetachedSessionTests`（2 条） |

> 注：当初 bug #2 修过一次 `expire_on_commit=False`，但那个只解决「已加载的标量属性不再过期」，管不到「还没加载的 relationship」。

##### Bug B：AI 的回复会被渲染成学生的发言

| | |
|---|---|
| 现象 | 修完 Bug A 后请求通了，但 assistant 消息的 `username` 是 `e2e_student` |
| 根因 | T6 之后 assistant 回复也记 `user_id`（含义是「这条回复是给谁的」，用于在多人课堂里区分对话线）。列表直接用 `m.user.username` 就把它当成了发言人 |
| 影响 | 前端照 `username` 渲染，AI 的回答会顶着学生的名字出现——这是给前端的可用性事故 |
| 修复 | assistant 消息的 `username` 一律返回 `null`，前端按 `role='assistant'` 显示「AI」；`user_id` 保留原语义不动 |
| 为什么分开处理 | 这两个字段用途不同：`user_id` 给服务端做对话线隔离，`username` 给前端做发言人展示。合成一个字段就会在某一端出错 |

##### Bug C：embedding 模型加载失败导致整个 `/chat` 500

| | |
|---|---|
| 现象 | 重启服务后 `/chat` 全部 500，`httpx.ConnectError: getaddrinfo failed` / `ProxyError: 502` |
| 根因 | `huggingface.co` 在本机网络下不可达（直连超时、代理返回 502）。而 `docs/handoff/PROJECT_REPORT.md` 声称「这两个变量写在 `build_kb.py` 里」——**实际代码里一行都没有**。构建时的环境是一次性的 shell export，运行时路径完全裸奔 |
| 修复 | 把 `HF_ENDPOINT`（hf-mirror 镜像）、`HF_HUB_DISABLE_SYMLINKS`、`FASTEMBED_CACHE_PATH` 三个变量注入到 `agent/rag/indexer.py` **模块顶部、import 之前**（huggingface_hub 在 import 时就把这些变量求值成常量，晚一步设无效） |
| 为什么放在这里 | 放在 `build_kb.py` 只管「构建索引」，但**服务运行时加载模型是另一条路径**，两头都要走 `get_embeddings()`。放错位置等于没修 |
| 顺带修复 | 缓存目录从系统 Temp 挪到 `backend/.model_cache/`——Temp 会被磁盘清理扫掉，100MB 模型没了下次又得重下 |

##### Bug D：RAG 失败会把主功能拖垮

| | |
|---|---|
| 现象 | 模型一下载不到，`/chat` 就整体 500，连「不用知识库也能回答」都做不到 |
| 修复 | `get_knowledge_store()` 加 try/except，失败返回 `None` 并打印告警，检索层兜底返回空串 |
| 为什么这样修 | 知识库是回答质量的**增强手段**，模型自带知识仍能给合格答案。**辅助功能的故障不该升级成主功能的故障。** `lru_cache` 保证这个代价每个进程只付一次 |

##### Bug E：`db/repository.py` 的过时注释（未改，已标注）

`load_history` 的 docstring 写着「同样按学生隔离（T6 修）」，但 `app.py` 调用时**刻意不传 `user_id`**（群聊共享上下文）。这是 T6 隔离改动后注释没同步。**没有擅自修改**——群聊共享上下文是明确的产品决策，改注释前需确认。已在 `docs/specs/API_CONTRACT.md` 第 6 节标注「以契约文档为准」。

---

#### 六、RAG 检索专项：结果与分析（T9.2）

##### 6.1 测试设计

新建 `smoke_rag.py`，**不调 LLM、不占限流、不花额度**——只验「问题 → 检索 → 是否命中正确知识点」这一层。检索不准的话后面接什么模型都白搭，所以这一层要能单独测。

9 道题分三类：`in-kb`（库内，5 题）/ `cross`（跨知识点，2 题）/ `oos`（库外，2 题）。

##### 6.2 结果

```
库内题 top3 命中：6 / 7
库内题 top1 命中：4 / 7
```

| 问题 | top1 | top3 | 判定 |
|---|---|---|---|
| 死锁产生的四个必要条件 | 死锁 | 死锁×3 | ✓ 完美 |
| TCP 为什么需要三次握手 | 三次握手 | 三次握手×3 | ✓ 完美 |
| 进程和线程有什么区别 | 进程与线程 | 进程与线程×3 | ✓ 完美 |
| 死锁和进程调度算法有关系吗 | 进程调度算法 | 死锁×2 + 调度 | ✓ 跨知识点正确 |
| HTTPS 和 HTTP 有什么不同 | TCP/IP 分层 | 未召回 HTTPS | ✗ **失败** |
| TCP 和 UDP 该怎么选 | HTTPS 与 TLS | TCP 与 UDP（第 3 位） | △ top1 不准 |
| 页面置换 LRU 怎么工作 | TCP/IP 分层 | 虚拟内存（第 2、3 位） | △ top1 不准 |
| Redis 持久化有几种（库外） | — | — | 按预期召回无关内容 |
| MySQL 索引为什么用 B+ 树（库外） | — | — | 按预期召回无关内容 |

##### 6.3 失败项分析：HTTPS 题为什么完全没召回

「HTTPS 和 HTTP 有什么不同？」三条召回是 TCP/IP 分层、TCP/IP 分层、TCP 与 UDP，**全部跑偏**。

推断原因：《TCP/IP 分层模型与 ARP / DNS》里有一段「从浏览器输入 URL 到页面显示发生了什么」，其中出现 HTTP/TLS/HTTPS 相关表述，在向量空间里和这个问题靠得很近；而《HTTPS 与 TLS 握手》这篇的一级标题把重心放在 **TLS 握手流程**上，「HTTPS 与 HTTP 的差异」不是它的开篇语义。两边一挤，正确答案就掉出了 top3。

值得注意的是，**同一份知识库里「TCP 和 UDP 该怎么选」反而把 HTTPS 文档排到了第一**——两篇语义相邻的文档在 top1 位置上互相错位，是同一个根因的两种表现。

##### 6.4 影响与处置

**不阻塞 MVP 验收**，理由：

1. top3 层面仍有 6/7，多数问题能拿到正确资料；
2. 检索失败时 LLM 会用自己的知识作答（`add_rag_grounding` 只在有 context 时才加「以资料为准」约束），回答质量下降但不失效；
3. Bug D 修完后，检索层故障不会再升级成接口级故障。

**但这是有代价的**：原本要求「涉及资料内容的结论以资料为准」，检索不到这条约束就不成立，答案可靠性退回到「模型自由发挥」。

**建议的改进方向**（未实施，需权衡）：① 给切片加 `topic` 前缀再 embedding，让知识点名在向量里占更大权重；② 引入 rerank；③ 对 top1 相似度设阈值，低于阈值就不注入上下文。三项都要改检索层，建议在 T11 之后评估。

---

#### 七、已知不足与限制

| 项 | 说明 |
|---|---|
| **RAG top1 准确率 4/7** | 见 6.3。语义相邻文档互相错位 |
| **embedding 模型 512 维 small 版** | 知识库只有 8 篇 66 个切片，瓶颈在切片粒度不在模型容量；扩到几百篇后需重新评估是否换大模型 |
| **`weak_points` 可能重复** | 同一误解在多轮被反复记录成多条（画像测试里 A 就有 3 条含义相近的死锁误解） |
| **性能数据尚未采集** | T11 的内容，本次未做，缺少响应时间基线 |
| **前端未参与测试** | 本报告只覆盖后端 / AI 部分 |
| **测试依赖真实 LLM** | 端到端结果含模型随机性，非严格可复现；单元测试（83 条）才是确定性基线 |

---

#### 八、如何复跑

```bash
cd Tutor-Chatbot-main/backend

python run_tests.py          # 83/83，零成本，每次改动后必跑
python smoke_rag.py          # RAG 检索专项，零成本，不占限流
python smoke_db.py           # 需服务在跑，消耗 3 次 LLM
python smoke_profile.py      # 需服务在跑，消耗 8 次 LLM
python smoke_personalize.py  # 需服务在跑，消耗 3 次 LLM
python smoke_resource.py     # 需服务在跑，消耗 2 次 LLM
```

**限流是各端点独立计数**：`/chat` 10 次/分钟、`/resources/ppt-outline` 5 次/分钟、`/upload` 3 次/小时；且**计数键是客户端 IP 不是 user_id**。连着跑两个脚本很容易撞 429，中间留一分钟。

所有 `*_out.txt` 是脚本输出快照，已 gitignore。Windows 控制台看中文会乱码，**读文件而不是看控制台**。

---

#### 九、测试中发现的流程问题（写给自己）

1. **接口契约文档应该在联调前写**。这次是写完 `docs/specs/API_CONTRACT.md` 通读全文之后才开始跑端到端，结果一次就撞出两个真实缺陷。反过来（先联调、边跑边定契约）会把发现推迟到联调现场。
2. **端到端验收脚本不能被跳过**。这次 5 个缺陷里前两个单测测不到：路由注册、session 生命周期这类问题只有真实请求才暴露。之前的 bug #8、#9 也是同一类，验证了报告里那条结论。
3. **测试脚本自身的判定口径也要校验**。写 `smoke_rag.py` 时先因为 `expected` 用了简化别名，把 3/3 全中的题判成失败；改成子串匹配后又因为没对期望值做同样的归一化，把 `TCP 与 UDP` 判成未命中。**口径不对的测试比没有测试更糟**——它会给你假警报，也会让你放过真问题。
4. **报告里写的环境细节要跟代码核对**。`docs/handoff/PROJECT_REPORT.md` 说 HF 变量在 `build_kb.py` 里，实际一行没有。文档会过期，代码不会，排查时以代码为准。
