# 检索链路评测指标报告 —— 接口文档与前端集成

后端基地址（开发）：`http://127.0.0.1:8000`
实现：`backend/eval/metrics_report.py`（指标定义与报告卡）+ `backend/eval/report_api.py`（HTTP 接口）

---

## 1. 接口一览

| 方法 | 路径 | 说明 | 成功码 |
|---|---|---|---|
| GET | `/evaluation/metrics` | 指标报告卡（JSON） | 200 |
| GET | `/evaluation/metrics.csv` | 同一份表格的 CSV 文件下载 | 200 |
| POST | `/evaluation/metrics/refresh` | 触发重新评测（**异步**） | 202 |
| GET | `/evaluation/metrics/job/{job_id}` | 查询评测任务状态 | 200 |

### 为什么要拆成「读缓存 + 异步刷新」

一次 L1+L2 全量评测实测 **85 秒**，远超 HTTP 请求的合理等待时间。所以：

- `GET /evaluation/metrics` **默认返回最新一份已生成的报告**，毫秒级响应；
- 需要最新数据时，前端显式调 `POST /refresh` 拿到 `job_id`，轮询到完成后再取报告。

报告是否过期由**索引指纹**判定（不是时间）：`source.fresh=false` 表示知识库重建过，
这份报告不反映当前状态，`stale_reason` 会写明原因。

---

## 2. GET /evaluation/metrics

### 请求参数

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `report` | query | string | 否 | 指定报告文件名（`eval/reports/` 下）。**不接受路径分隔符或 `..`**。缺省取最新一份 |

### 返回结构

```jsonc
{
  "available": true,
  "generated_at": "2026-10-28T08:08:20",
  "source": {
    "report_file": "e2e_20261028_080820.json",
    "report_timestamp": "2026-10-28T08:08:20",
    "index_fingerprint": "sha256:bbe0cdc4…",
    "current_fingerprint": "sha256:bbe0cdc4…",
    "fresh": true,              // true=与当前索引一致 / false=已过期 / null=无法校验
    "stale_reason": null,
    "parent_child": "1"         // 父子检索档位，评测须与生产一致
  },
  "context": {
    "granularity": "parent", "inject_k": 3,
    "scored_cases": 71, "annotated_cases": 71, "ndcg_clamped_cases": 0,
    "index_fingerprint": "sha256:bbe0cdc4…",
    "caps": {                   // 物理上限，不是人为目标
      "cases": 71, "k": 3,
      "recall_cap_mean": 0.9159, "recall_cap_min": 0.4286,
      "precision_cap_mean": 0.6573, "precision_cap_min": 0.1667
    }
  },
  "summary": {
    "total": 21, "passed": 11, "failed": 2, "unknown": 8, "info": 0,
    "judged": 13,             // = passed + failed
    "pass_rate": 0.8462       // = passed / judged
  },
  "gate": { "passed": true, "failures": [] },
  "columns": ["group","name","unit","value","target","ceiling","direction","status","formula","advice","source"],
  "column_labels": { "group": "分组", "name": "指标", … },
  "rows": [
    {
      "key": "recall_at_k",
      "group": "上下文层",
      "name": "上下文召回率@K",
      "unit": "%",
      "value": 0.7909,             // 原始值；null 表示该层未运行
      "value_display": "79.09%",   // 已格式化，前端直接渲染
      "target": 0.85,
      "target_display": ">= 85.00%",
      "target_basis": "按物理上限折算：…",
      // 物理上限：只有「分母固定、分子有天花板」的指标才有，其余为 null。
      // 不看上限就无法判断 0.79 是好是坏（它是上限 0.9159 的 86.4%）。
      "ceiling": 0.9159,
      "ceiling_display": "91.59%",
      "ceiling_rate": 0.8637,      // = value / ceiling，null 表示无上限
      "direction": "越高越好",
      "status": "未达标",           // 达标 / 未达标 / 未运行 / 不设阈值
      "formula": "命中的金标准切片增益之和 / …",
      "advice": "先看清缺口在哪一段（2026-10-28 实测拆解）…",
      "source": "L1 检索层"
    }
  ]
}
```

> **前端渲染约定**：表头用 `column_labels`，列顺序用 `columns`，单元格取值
> 用 `value_display` / `target_display` / `ceiling_display` / `status`。
> 不要在前端再写一份列名映射，也不要写死数字下标（2026-10-28 插入「物理上限」
> 列时写死下标的单测集体错位——错的是测试不是实现），按 `columns` 里的键取。
>
> 有上限的只有 `recall_at_k` 与 `precision_at_k` 两项（见 `CEILING_KEY_BY_METRIC`）。
> 其余指标渲染 `ceiling_display` 会得到「—」；**不要给它们补 1.0**——
> 首位命中率确实能到 100%，但延迟没有上界，补了就是误导。
>
> `rows` 共 **21 项**，按 6 个分组排序（`GROUP_ORDER`：链路稳定性 / 检索层 /
> 上下文层 / 生成层 / 响应延迟 / 重排增益）。加指标时**追加到 `METRIC_SPECS`
> 末尾即可**，展示位置由 `GROUP_ORDER` 决定，前端不需要改列顺序。
> 生成层 6 项与链路稳定性前 2 项分别依赖 L3 / L4，日常只跑 L1+L2 时它们
> 都是「未运行」（`value: null` + `status: "未运行"`），**不要渲染成 0 或 100%**。

### 错误码

| 状态码 | `detail.code` | 触发条件 |
|---|---|---|
| 404 | `NO_REPORT` | 尚无任何评测报告（提示先调 `/refresh`） |
| 404 | `REPORT_NOT_FOUND` | 指定的 `report` 文件不存在 |
| 422 | `BAD_REPORT_NAME` | `report` 含路径分隔符或 `..` |
| 500 | `REPORT_UNREADABLE` | 报告文件存在但解析失败（文件损坏） |

---

## 3. GET /evaluation/metrics.csv

### 请求参数

| 参数 | 位置 | 类型 | 默认 | 说明 |
|---|---|---|---|---|
| `report` | query | string | — | 同 `/evaluation/metrics` |
| `bom` | query | int (0/1) | **1** | 1=带 UTF-8 BOM（Excel 打开中文不乱码）；0=纯 UTF-8（给程序解析） |

### 响应

- `Content-Type: text/csv; charset=utf-8`
- `Content-Disposition: attachment; filename="rag_metrics_20261028080820.csv"`
- `Cache-Control: no-store`

**列结构与 `/evaluation/metrics` 的 `rows` 完全一致**（顺序、内容都对齐），
表头为中文：`分组,指标,单位,当前值,目标阈值,物理上限,判定方向,是否达标,计算说明,改进建议,数据来源`（11 列 × 13 行）

#### 关于 BOM（中文乱码的关键）

`utf-8-sig` 就是「UTF-8 + BOM」，编码本身仍是 UTF-8。中文 Windows 上的 Excel
在没有 BOM 时会按本地 ANSI 码页解析，**中文必然乱码**——这是「UTF-8 导出中文乱码」
最常见的原因。所以默认带 BOM；若前端拿到后要自己解析文本，用 `bom=0` 拿纯 UTF-8。

错误码同 `/evaluation/metrics`。

---

## 4. POST /evaluation/metrics/refresh

### 请求体

```jsonc
{
  "layers": "L1,L2",   // 可选 L1,L2,L3,L4；默认 "L1,L2"（零 LLM 成本，约 85s）
  "cases": null        // 可选，自定义评测集路径；缺省 rag_eval_cases.json
}
```

> L3 会调裁判模型、L4 会真实打 `/chat`（受 10/min 限流），**耗时和成本都高**，
> 日常只跑 L1+L2。

### 返回（202）

```jsonc
{
  "job_id": "c193dc940251",
  "status": "running",
  "layers": "L1,L2",
  "started_at": "2026-10-28T08:08:20",
  "poll": "/evaluation/metrics/job/c193dc940251",
  "hint": "轮询 poll 直到 status != running；完成后 GET /evaluation/metrics 取最新报告。"
}
```

### 错误码

| 状态码 | `detail.code` | 触发条件 |
|---|---|---|
| 409 | `JOB_RUNNING` | **已有任务在跑**（单飞：并发评测会互相抢 CPU，让延迟指标失真） |
| 422 | `BAD_LAYERS` | layers 含不支持的值 |
| 422 | `EMPTY_LAYERS` | layers 为空 |

---

## 5. GET /evaluation/metrics/job/{job_id}

```jsonc
{
  "job_id": "c193dc940251",
  "status": "done",            // running / done / failed
  "layers": "L1,L2",
  "started_at": "2026-10-28T08:08:20",
  "finished_at": "2026-10-28T08:09:46",
  "elapsed_secs": 85.7,
  "exit_code": 0,
  "report_file": "e2e_20261028_080820.json",   // 成功时有值
  "error": null,                                // 失败时写明原因
  "log_tail": "…"                               // 子进程输出尾部，排错用
}
```

| 状态码 | `detail.code` | 触发条件 |
|---|---|---|
| 404 | `JOB_NOT_FOUND` | job_id 不存在（或后端重启过，任务表在内存里） |

> 任务状态存在**进程内存**中，后端重启后会丢失。重启后重新调 `/refresh` 即可。

---

## 6. 前端调用示例

### 6.1 拉表格数据并渲染

框架无关，用 `fetch`（React / Vue / 原生均可直接抄）。

```js
// api/metrics.js
const BASE = process.env.NEXT_PUBLIC_API_BASE || 'http://127.0.0.1:8000';

export async function fetchMetrics(report) {
  const url = new URL('/evaluation/metrics', BASE);
  if (report) url.searchParams.set('report', report);

  const res = await fetch(url, { headers: { Accept: 'application/json' } });
  if (!res.ok) {
    // 后端错误体统一是 { detail: { code, message } }
    const { detail } = await res.json().catch(() => ({ detail: {} }));
    throw new Error(detail?.message || `HTTP ${res.status}`);
  }
  return res.json();
}

export async function refreshMetrics(layers = 'L1,L2') {
  const res = await fetch(`${BASE}/evaluation/metrics/refresh`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ layers }),
  });
  if (res.status === 409) throw new Error('已有评测任务在跑，请稍后再试');
  if (res.status === 422) {
    const { detail } = await res.json();
    throw new Error(detail.message);
  }
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();      // { job_id, poll, ... }
}

export async function fetchJob(jobId) {
  const res = await fetch(`${BASE}/evaluation/metrics/job/${jobId}`);
  if (res.status === 404) throw new Error('任务不存在（后端可能已重启）');
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

/** 轮询直到任务结束。intervalMs=3000，最长等 10 分钟。 */
export async function waitForJob(jobId, intervalMs = 3000, timeoutMs = 600000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const job = await fetchJob(jobId);
    if (job.status !== 'running') return job;
    await new Promise((r) => setTimeout(r, intervalMs));
  }
  throw new Error('评测超时');
}
```

### 6.2 React 组件（表格 + 下载 + 刷新）

```jsx
'use client';
import { useState, useEffect, useCallback } from 'react';
import { fetchMetrics, refreshMetrics, waitForJob } from '@/api/metrics';

const STATUS_CLASS = {
  达标: 'ok', 未达标: 'bad', 未运行: 'muted', 不设阈值: 'muted',
};

export default function MetricsReport() {
  const [card, setCard] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState('');

  const load = useCallback(async () => {
    setError('');
    try {
      setCard(await fetchMetrics());
    } catch (e) {
      setError(e.message);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  // 重新评测：拿到 job_id 后轮询，完成后自动刷新表格
  const onRefresh = async () => {
    setBusy(true); setError('');
    try {
      const { job_id } = await refreshMetrics('L1,L2');
      setProgress('评测中（约 85 秒）…');
      const job = await waitForJob(job_id);
      if (job.status === 'failed') throw new Error(job.error || '评测失败');
      setProgress('');
      await load();
    } catch (e) {
      setError(e.message);
      setProgress('');
    } finally {
      setBusy(false);
    }
  };

  if (error && !card) return <div className="err">{error}</div>;
  if (!card) return <div>加载中…</div>;

  return (
    <div>
      <header>
        <h2>检索链路指标报告</h2>
        <span>生成于 {card.generated_at}</span>
        {/* 索引指纹不一致时必须提示，否则用户会把旧数据当当前状态 */}
        {card.source.fresh === false && (
          <span className="warn">{card.source.stale_reason}</span>
        )}
        <button onClick={onRefresh} disabled={busy}>
          {busy ? progress || '评测中…' : '重新评测'}
        </button>
        {/* 下载直接走 URL，见 6.3 */}
        <a href={`${BASE}/evaluation/metrics.csv`} download>下载 CSV</a>
      </header>

      <p>
        共 {card.summary.total} 项：达标 {card.summary.passed}、
        未达标 {card.summary.failed}、未运行 {card.summary.unknown}
        （达标率 {(card.summary.pass_rate * 100).toFixed(1)}%，分母只算已判定项）
      </p>

      <table>
        <thead>
          <tr>
            {card.columns.map((c) => <th key={c}>{card.column_labels[c]}</th>)}
          </tr>
        </thead>
        <tbody>
          {card.rows.map((r) => (
            <tr key={r.key} className={STATUS_CLASS[r.status]}>
              <td>{r.group}</td>
              <td title={r.formula}>{r.name}</td>
              <td>{r.unit}</td>
              <td>{r.value_display}</td>
              <td title={r.target_basis}>{r.target_display}</td>
              <td>{r.direction}</td>
              {/* 「未运行」不等于达标，样式上要明显区别于「达标」 */}
              <td>{r.status}</td>
              <td className="formula">{r.formula}</td>
              <td className="advice">{r.advice}</td>
              <td>{r.source}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

### 6.3 触发文件下载（两种写法）

**写法 A：直接给链接（推荐，最简单）**

服务端已带 `Content-Disposition: attachment`，浏览器会直接下载，不会跳页。

```jsx
<a href={`${BASE}/evaluation/metrics.csv`} download>下载 CSV</a>
```

**写法 B：走 fetch（需要带鉴权 / 想自定义文件名时用）**

```js
export async function downloadMetricsCsv(filename) {
  const res = await fetch(`${BASE}/evaluation/metrics.csv`, {
    headers: { Accept: 'text/csv' },
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);

  // ⚠ 不要 res.text()：UTF-8 解码后再编码容易出问题。
  // 直接拿 Blob，字节层面原样保留（含 BOM），交给浏览器落盘。
  const blob = await res.blob();

  // 优先用服务端 Content-Disposition 里的文件名
  const cd = res.headers.get('Content-Disposition') || '';
  const m = /filename="([^"]+)"/.exec(cd);
  const name = filename || m?.[1] || 'rag_metrics.csv';

  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);   // 不回收会一直占内存
}
```

### 6.4 CORS

后端 `ALLOWED_ORIGINS` 默认放行 `http://localhost:3000`。若前端跑在其他端口，
启动后端时补上：

```bash
ALLOWED_ORIGINS='["http://localhost:3000","http://localhost:3001"]' \
  ./venv/Scripts/python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000
```

---

## 7. 指标口径速查

共 **21 项**，分 6 组。分组沿用 RAGAS 的三段式（检索层 / 上下文层 / 生成层），
另加本项目特有的「链路稳定性 / 响应延迟 / 重排增益」。

| 分组 | 指标 | 当前值 | 目标 | 判定 | 计算说明（摘） |
|---|---|---|---|---|---|
| 链路稳定性 | 链路成功率 | 100.00% | ≥ 99% | 达标 | L4：真实 POST /chat 返回 200 的占比（429 退避重试后仍失败才算） |
| 链路稳定性 | 端到端 p95 | 10.404s | ≤ 12s | 达标 | L4：含 LLM 生成的整轮耗时 p95（**九成是生成，不是检索**） |
| 链路稳定性 | 降级率 | 0.00% | ≤ 0% | 达标 | 降级用例数 / 计分题数（零容忍） |
| 检索层 | 首位命中率 | 95.77% | ≥ 90% | 达标 | top1 / 计分题数 |
| 检索层 | 检索命中率@K | 98.59% | ≥ 95% | 达标 | top3 / 计分题数（即 hit@K） |
| 检索层 | MRR | 0.9648 | ≥ 0.90 | 达标 | 1/rank（首个增益>0 的位置）的均值 |
| 检索层 | NDCG@K | 0.8700 | ≥ 0.80 | 达标 | DCG / IDCG，第 i 位折损 1/log2(i+2) |
| 上下文层 | 上下文召回率@K | 79.09% | ≥ 85% | **未达标** | 命中金标准增益和 / 金标准增益全集（**物理上限 91.59%**） |
| 上下文层 | 上下文精确率@K | 56.34% | ≥ 60% | **未达标** | 注入 K 条增益和 / K（**物理上限 65.73%**，上限本身不是 1.0） |
| 上下文层 | 注入噪声率 | 30.52% | ≤ 33.33% | 达标 | 注入窗口里**零增益**条数 / K（看条数，不看 0.5 档贡献） |
| 生成层 | 外幻觉率 | 0.00% | ≤ 15% | 达标 | L3 裁判：资料与通识之外的陈述占比 |
| 生成层 | 忠实度 | 1.0000 | ≥ 0.85 | 达标 | **派生值** = 1 − 外幻觉率（不新增裁判调用） |
| 生成层 | 事实矛盾率 | 0.00% | ≤ 5% | 达标 | L3 裁判判「矛盾」的用例数 / 有效判定数 |
| 生成层 | 答案相关性 | 1.0000 | ≥ 0.85 | 达标 | L3 裁判三档：1 紧扣 / 0.5 跑题 / 0 答非所问 |
| 生成层 | 答案正确率 | 0.9583 | ≥ 0.80 | 达标 | L3 裁判三档，参考答案用**金标准切片正文** |
| 生成层 | 资料利用率 | 66.67% | ≥ 60% | 达标 | 回答实际引用的条数 / 注入条数（多票取并集） |
| 响应延迟 | 检索 p95 | 1.108s | ≤ 3.0s | 达标 | **CI 硬门禁** `GATE_L1_P95` |
| 响应延迟 | 检索均值 | 0.968s | ≤ 1.5s | 达标 | 算术平均（软目标） |
| 重排增益 | NDCG 提升度 | 0.0539 | > 0 | 达标 | **CI 硬门禁**：≤0 即失败 |
| 重排增益 | 首位提升条数 | +5 | > 0 | 达标 | top1_on − top1_off |
| 重排增益 | 重排 p95 耗时 | 1079ms | ≤ 1500ms | 达标 | 重排阶段耗时 p95（占检索 97%） |

（基线：`e2e_20261028_142722.json`，L1/L2 全量 71 题 + L3/L4 各 12 条抽样，
`RAG_PARENT_CHILD=1` + `RAG_RERANK_TEXT=child_title`）

> **日常只跑 L1+L2 时**，生成层 6 项与链路稳定性前 2 项会回到「未运行」——
> L3 要花裁判模型的钱、L4 要起后端，都不进 CI。要这 8 项有数就跑：
> `python -m eval.e2e_runner --layers L1,L2,L3,L4 --sample 12`（约 19 分钟）。

### 三条读数纪律

1. **「未运行」≠ 达标**。L3 要花裁判模型的钱，日常不跑。它单独计数，
   不计入达标率分母。把「没跑」显示成「达标」是这类报告最常见的骗人方式。
2. **先看降级率再看延迟**。链路降级走的是快路径，耗时反而更短——
   一堆失败请求会让 p95「变好看」。降级率不为 0 时，延迟数字不可信。
3. **阈值有出处，别照抄**。recall 的分母是金标准全集而注入窗口只有 3 条，
   **物理上限 0.9159**；换题库后必须重算上限再谈阈值。
4. **忠实度与外幻觉率是一个数的两面**，不要当成两个独立问题处理——
   忠实度是 `1 − 外幻觉率` 的派生值，不新增任何裁判调用。
   另外它与 RAGAS 的 faithfulness 口径不同（本项目按「资料 + 计算机通识」判定，
   教科书级常识不算幻觉），**跨项目比较时不可直接套用**。
5. **答案正确率的数要连着 `correctness_ref_gold` 一起看**。参考锚点优先用
   金标准切片正文；只有知识点标签（如"死锁"两个字）时裁判锚点太粗，
   实测 4 条全判 1.0——那种情况下这个数**不能当真**。

### 生成层 / 链路稳定性这两组怎么跑出来

```bash
# L3 生成层（裁判模型，花钱）：全量约 71 题 × 15 次调用
RAG_PARENT_CHILD=1 python -m eval.e2e_runner --layers L3

# 只想验证链路通不通：小样本 + 1 票（注意 sample 要大于 oos 条数才抽得到库内题）
RAG_PARENT_CHILD=1 python -m eval.e2e_runner --layers L3 --sample 8 --votes 1

# L4 端到端（需先起后端）：真实 POST /chat
python -m eval.e2e_runner --layers L4 --sample 10
```

---

## 8. 命令行用法（不想起后端时）

```bash
cd backend

# 打印表格（读最新一份报告）
python -m eval.metrics_report

# 导出 CSV
python -m eval.metrics_report --csv reports/metrics.csv

# 纯 UTF-8（无 BOM，给程序解析）
python -m eval.metrics_report --csv reports/metrics.csv --no-bom

# JSON
python -m eval.metrics_report --json

# 先跑一次全量评测再出报告（约 85 秒）
RAG_PARENT_CHILD=1 python -m eval.e2e_runner --layers L1,L2
```