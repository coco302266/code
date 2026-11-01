# 修复提案：D2 评估节点无日志 / D3 hint_level 棘轮

目标文件：`Tutor-Chatbot-main/backend/agent/graph.py`（**属 A 的地盘，需 A 复核**）  
拟登记为 XB-3（D2）、XB-4（D3）。本次只交方案，未动代码。

现状前提（2026-10-15 实测）：整个 `backend/` **一行日志都没有**——无 `import logging`、  
无 `basicConfig`。所以方案里的 WARNING 级别是刻意选的：即使不配 logging，  
Python 的 `logging.lastResort` 也会把 WARNING+ 打到 stderr，uvicorn 控制台即可见。  
INFO/DEBUG 则需要额外配置才看得到。

---

## D2：评估节点解析失败无日志

### 问题

`assess_understanding_node` 的 except 分支返回  
`{resolved: False, hint_level: state["hint_level"], ...}`，  
与「模型正常判 false」**返回完全同形，且不打日志**。出问题时只能靠复现，  
换一个问题就得重新猜。

### 方案（改 3 处，纯增量，不改任何行为）

**1. 文件头加 logger**

```python
import json
import logging
import os
```

并在 `get_llm` 之前加：

```python
logger = logging.getLogger(__name__)
```

**2. 把 try 收窄到只包解析，失败记 WARNING**

现在 `try` 从 `parse_assessment_result` 一路包到三个 return，  
异常面太大。改成先解析、再分支：

```python
    try:
        assessment = parse_assessment_result(response.content)
    except (json.JSONDecodeError, TypeError, ValidationError, ValueError) as exc:
        # LLM-6a：解析失败与「模型判 resolved=false」返回完全同形，
        # 不记日志的话线上永远无法区分这两者，只能靠复现。
        logger.warning(
            "评估节点解析失败，按「未掌握」降级处理 topic=%r in_hint_level=%d "
            "exc=%s: %s raw=%r",
            state["topic"],
            state["hint_level"],
            type(exc).__name__,
            exc,
            response.content[:500],
        )
        return {
            "resolved": False,
            "hint_level": state["hint_level"],
            "misconception": state["misconception"],
        }
```

要点：

- 捕获类型**一个不动**（保持现有行为），只加日志。
- `raw` 截断 500 字符，避免模型返回一大坨把日志刷爆。
- 记 `in_hint_level`，这样事后能看出"降级时停在第几层"。

**3. 成功路径补一条 DEBUG（可选但强烈建议）**

```python
    logger.debug(
        "评估节点解析成功 topic=%r resolved=%s model_hint=%d cur_hint=%d misconception=%r raw=%r",
        state["topic"], assessment.resolved, assessment.hint_level,
        state["hint_level"], assessment.misconception, response.content[:500],
    )
```

这条是给「模型说了什么 vs 代码解析成什么」分层看的。之前排查时靠临时脚本打原始返回，  
有了它以后不用再写脚本。DEBUG 级别，生产默认不出，需要时 `LOG_LEVEL=DEBUG` 打开。

**4.（可选，需 A 同意）让解析失败对外可见**

在 `TutorState` 加 `assess_degraded: NotRequired[bool]`，  
`app.py:512` 的 state 事件里带上 `degraded` 字段。  
好处：假 state（D1）与"真 state 但评估降级"两条异常路径可以统一用一个字段表达。  
代价：动 `state.py` + `app.py` + 前端，跨边界。**建议先不做，日志够了再说。**

### 配套测试（tests/test_graph.py 新增 2 条）

```python
    def test_parse_failure_logs_warning(self):
        state = {..., "llm": _FakeLlm("not json at all")}
        with self.assertLogs("agent.graph", level="WARNING") as ctx:
            update = asyncio.run(assess_understanding_node(state))
        self.assertFalse(update["resolved"])
        self.assertIn("评估节点解析失败", ctx.output[0])
```

另一条断言：正常判 false 时**不**产生 WARNING（防止日志误报）——  
`assertNoLogs` 是 3.10+，本机 3.11 可用。

### 风险

无。不改返回值、不改捕获类型，83 条现有测试不受影响。

---

## D3：hint_level 棘轮效应

### 问题

`graph.py:201`：

```python
"hint_level": max(state["hint_level"], assessment.hint_level),
```

只增不减，`resolved=true` 或换话题才归零。而模型在 `ASSESS_UNDERSTANDING_PROMPT`  
里**是知道当前 hint_level 的**（`hint_level=state["hint_level"]` 传进去了），  
所以它返回更低的值就是"这个学生有进步、可以少讲点"的信号 —— `max()` 把这个信号  
整段吞掉了。后果：resolved 漏判一次，学生永久卡在第 3 层，前端和 HTTP 状态码都看不出来。

### 三个选项

| 选项             | 做法                                    | 优点                   | 缺点                                   |
| -------------- | ------------------------------------- | -------------------- | ------------------------------------ |
| **A（推荐）** 受控下调 | 模型值 < 当前值时，`next = max(cur-1, model)` | 响应速度信号，每轮最多降 1 级，不会抖 | 仍可能 3↔2 来回跳一次                        |
| B 完全信任模型       | 直接 `next = model`                     | 最简洁                  | temperature 0.4 下会明显抖动，学生看到的讲解深度忽深忽浅 |
| C 只观测不动        | 保持 `max()`，仅加日志告警                     | 零行为风险                | 不治本，学生照样卡死                           |

### 推荐方案：A + C 的告警

```python
        model_hint = assessment.hint_level
        cur_hint = state["hint_level"]

        if model_hint >= cur_hint:
            next_hint = min(model_hint, 3)
        else:
            # 模型主动调低 = 学生有进步的信号。原来用 max() 把这个信号整段吞掉，
            # 后果是 resolved 漏判一次，学生就永久卡在第 3 层（只有换话题才归零）。
            # 这里允许下调，但每轮最多降 1 级：temperature 0.4 下模型会抖，
            # 一步到位地跟会让讲解深度来回跳。
            next_hint = max(cur_hint - 1, model_hint, 0)
            logger.info(
                "讲解深度下调 topic=%r %d -> %d（模型给 %d）misconception=%r",
                state["topic"], cur_hint, next_hint, model_hint,
                assessment.misconception,
            )

        # 已经讲到最深一层、模型又说不出学生具体哪里不懂 —— 这是 resolved 漏判的
        # 典型特征。不改行为，只留证据。
        if next_hint >= 3 and not assessment.misconception:
            logger.warning(
                "已到最深讲解层且模型未给出具体误解 topic=%r resolved=false "
                "—— 疑似 resolved 漏判，需人工核看", state["topic"],
            )

        return {
            "resolved": False,
            "hint_level": next_hint,
            "misconception": assessment.misconception,
        }
```

`min(model_hint, 3)` 是补一道上限：`AssessmentResult` 的 `Field(ge=0, le=3)` 已经挡过，  
这里是双保险，`HINT_STRATEGIES` 只有 0-3 四层，越界会 KeyError。

### 为什么不需要改 prompt

模型已经拿到当前 hint_level 了，返回更低值本身就是判断结果。  
加 `progress: improved|same|worse` 字段能让信号更明确，但要改  
`AssessmentResult` + prompt + 所有 mock，收益不大。**留作后续。**

### 配套测试（tests/test_graph.py 新增 3 条）

| 用例         | 输入                            | 期望                      |
| ---------- | ----------------------------- | ----------------------- |
| 步进下调       | cur=3, 模型=1                   | `hint_level == 2`（不是 1） |
| 升级路径不变（回归） | cur=1, 模型=2                   | `hint_level == 2`       |
| 最深且无误解告警   | cur=3, 模型=3, misconception="" | 产生 WARNING              |

### 现有测试是否受影响

查过了：`tests/test_graph.py` 里**没有**"模型值低于当前值"的用例，  
现有 3 条相关断言（换话题归零 / resolved 归零 / 1→2 升级）在新逻辑下结果全部不变。  
`run_tests.py` 83 条应仍全绿。

### 风险

唯一实质风险是**行为变更**：学生可能看到讲解深度往回退一级。  
判断是值得的——卡在第 3 层出不来比回退一级更糟。若 A 认为风险仍高，  
可退到选项 C（只加告警、不动逻辑）。

---

## 落地顺序建议

1. **先做 D2**（纯增量、零风险，且它是所有"模型行为异常"排查的地基）
2. D3 的告警部分（`if next_hint >= 3 and not misconception`）可以和 D2 一起上，同样零风险
3. D3 的下调部分单独一笔提交，方便 `git revert` 整段回退

## 拍板结果（2026-10-15）

- [x] D2 方案照此执行，**含 DEBUG 那条**
- [x] D3 选 **A（受控下调）**
- [x] **由 B 改**（用户拍板），已登记为 XB-3 / XB-4，见 `B_WORKLOG.md`

### 已落地

`agent/graph.py`：WARNING 记解析失败、DEBUG 记原始返回、棘轮改为 `max(cur-1, model, 0)`、  
最深一层且无 misconception 时告警。  
`tests/test_graph.py`：新增 7 条（解析失败告警 / 正常判 false 不告警 / 步进下调 /  
升级不变 / 同级不变 / 最深告警 / 最深有误解不告警）。  
`run_tests.py`：**90/90 通过**（原 83 + 新增 7）。

### 落地时修掉的一处方案瑕疵

原方案写"开 `LOG_LEVEL=DEBUG` 就能看到原始返回"——**不成立**。项目没有 `basicConfig`，  
不配 logging 时只有 WARNING+ 走 `lastResort` 打到 stderr，DEBUG 设了环境变量也不会出现。  
已把代码注释改成准确说法（需要显式 `logging.basicConfig(level=logging.DEBUG)`），  
并在下方列为待办。

### 由此产生的新待办

- [x] `app.py` 加 `logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))`——  
  否则 D2 的 DEBUG 和 D3 下调的 INFO **默认都看不见**。  
  已做（XB-5，2026-10-15，**未提交**）。实测 `LOG_LEVEL=DEBUG` 下两条都能打出。
