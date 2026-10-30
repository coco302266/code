"""裁判 / 标注模型的统一入口（与生产回答通道解耦）。

为什么单独抽出来
----------------
生产回答走 LLM_*（DeepSeek），而评测里的「裁判」与「自动标注」是另一类负载：

1. 批量跑几百题，会把生产额度吃得很快；
2. 需要 temperature=0 保证可复现，而生产是 0.4；
3. 换厂商时希望只改环境变量，不动代码。

改造前，三处调用各自读 LLM_*，其中 scripts/judge_scores.py 连 base_url 的
默认值都是写死的 https://api.deepseek.com —— 想换模型必须改源码。
现在统一收敛到这里，四个环境变量决定一切：

    EVAL_JUDGE_MODEL       裁判模型名（未配则跟随 LLM_MODEL）
    EVAL_JUDGE_API_KEY     裁判专属 key（未配则跟随 LLM_API_KEY）
    EVAL_JUDGE_BASE_URL    裁判专属端点（未配则跟随 LLM_BASE_URL）
    EVAL_JUDGE_NO_THINK    是否关闭思考（默认 1 = 关，详见下）

回落约定（重要）
--------------
EVAL_JUDGE_* 全部未配置时，**逐项回落**到 LLM_*，旧行为零改动。

但「配了 MODEL 却没配 KEY」不再静默回落 —— 那会把 Qwen 的模型名打到
DeepSeek 的端点上，报出来的 404 会把人带偏方向。这种情况直接报错并指出
缺哪个变量。

关于 Qwen3 的思考（本项目默认关闭）
----------------------------------
Qwen3 系列默认开思考，而裁判任务是「按格式出一小段 JSON」：推理链既双倍
消耗 token，又会拖慢批量标注，还可能稀释格式遵循率。所以默认关掉。

麻烦在于各家字段名不统一：

    Qwen3 / DashScope        {"enable_thinking": false}
    DeepSeek 推理模型        {"thinking": {"type": "disabled"}}

由 STYLE 自动按「端点 + 模型名」判断，也可以用 EVAL_JUDGE_NO_THINK_STYLE
手动指定（auto / qwen / deepseek / none）。识别不出厂商时不发送该字段，
宁可多花点 token，也不塞一个会让对方 400 的未知参数。
"""

from __future__ import annotations

import os
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]

# 裁判脚本（scripts/*.py）是独立入口，不经过 app 启动流程，没有地方统一 load_dotenv。
# 在这里兜底加载一次，保证「同一个 .env，任何入口读到的都一样」。
# load_dotenv 不会覆盖已存在的进程环境变量 —— 命令行临时覆盖仍然有效。
try:
    from dotenv import load_dotenv

    load_dotenv(_BACKEND_DIR / ".env")
except Exception:  # pragma: no cover - dotenv 缺失时沿用进程环境变量
    pass

DEEPSEEK_DEFAULT_BASE = "https://api.deepseek.com"
DASHSCOPE_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"

_FALSY = {"0", "false", "False", "off", "OFF", "no", "NO", ""}


def _flag(name: str, default: str = "1") -> bool:
    """读布尔开关。约定：只有显式的 0/false/off/no/空 才算关。"""
    return os.getenv(name, default).strip() not in _FALSY


def _guess_base_url(model: str) -> str | None:
    """只按模型名猜端点，省得每个换模型的人都要背一遍厂商地址。

    猜不出来返回 None（交给 SDK 走 OpenAI 默认地址），不猜错地址。
    """
    m = (model or "").lower()
    if "qwen" in m or "dashscope" in m:
        return DASHSCOPE_BASE
    if "deepseek" in m:
        return DEEPSEEK_DEFAULT_BASE
    return None


def resolve_judge_config(*, require_key: bool = True) -> dict:
    """解析裁判配置，返回字典：model / api_key / base_url / temperature / extra_body。

    优先级：EVAL_JUDGE_* > LLM_* > 内置默认。
    """
    model = (os.getenv("EVAL_JUDGE_MODEL") or "").strip()
    api_key = (os.getenv("EVAL_JUDGE_API_KEY") or "").strip()
    base_url = (os.getenv("EVAL_JUDGE_BASE_URL") or "").strip()

    judge_explicit = bool(model or api_key or base_url)

    if judge_explicit:
        missing = [
            name
            for name, value in (
                ("EVAL_JUDGE_MODEL", model),
                ("EVAL_JUDGE_API_KEY", api_key),
            )
            if not value
        ]
        if missing:
            raise SystemExit(
                "裁判模型配置了 %s，但缺少 %s —— 半套配置不会回落到生产通道"
                "（那会把裁判模型名打到错误的端点上，报错会更难查）。\n"
                "请在 backend/.env 补齐 %s，或把三个 EVAL_JUDGE_* 全部删掉以跟随 LLM_*。"
                % ("EVAL_JUDGE_*", "、".join(missing), "、".join(missing))
            )
        if not base_url:
            base_url = _guess_base_url(model) or ""
    else:
        # 完全未配置：逐项回落，保持与改造前完全一致的行为
        model = (os.getenv("LLM_MODEL") or "deepseek-flash").strip()
        api_key = (os.getenv("LLM_API_KEY") or "").strip()
        base_url = (os.getenv("LLM_BASE_URL") or DEEPSEEK_DEFAULT_BASE).strip().rstrip("/")

    if require_key and not api_key:
        raise SystemExit(
            "裁判模型缺少 API Key：请配置 EVAL_JUDGE_API_KEY"
            "（或退回跟随生产时配置 LLM_API_KEY）。"
        )

    temperature = 0.0
    raw_temp = (os.getenv("EVAL_JUDGE_TEMPERATURE") or "").strip()
    if raw_temp:
        try:
            temperature = float(raw_temp)
        except ValueError:
            raise SystemExit("EVAL_JUDGE_TEMPERATURE 不是数字：%r" % raw_temp)

    return {
        "model": model,
        "api_key": api_key,
        "base_url": base_url.rstrip("/") or None,
        "temperature": temperature,
        "extra_body": build_no_think_body(base_url, model),
    }


def build_no_think_body(base_url: str | None, model: str) -> dict | None:
    """构造「关闭思考」的扩展请求体；识别不出厂商或不该关时返回 None。"""
    if not _flag("EVAL_JUDGE_NO_THINK", "1"):
        return None

    style = (os.getenv("EVAL_JUDGE_NO_THINK_STYLE") or "auto").strip().lower()
    if style == "none":
        return None

    if style == "auto":
        haystack = "%s %s" % (base_url or "", model or "")
        haystack = haystack.lower()
        if "dashscope" in haystack or "qwen" in haystack:
            style = "qwen"
        elif "deepseek" in haystack:
            style = "deepseek"
        else:
            return None  # 不认识的厂商，不乱塞字段

    if style == "qwen":
        return {"enable_thinking": False}
    if style == "deepseek":
        return {"thinking": {"type": "disabled"}}
    return None


def describe_judge(cfg: dict | None = None) -> str:
    """一行人类可读的裁判配置（自动遮蔽 key），打进日志用。

    存在的意义：批量评测跑几百题，结果只产出一张表。如果裁判实际用的还是
    DeepSeek 而不是以为的 Qwen，光看指标分辨不出来 —— 必须打出来。
    """
    cfg = cfg or resolve_judge_config(require_key=False)
    key = cfg.get("api_key") or ""
    masked = ("%s...%s" % (key[:6], key[-4:])) if len(key) > 12 else ("已配置" if key else "未配置")
    return "裁判模型=%s  端点=%s  key=%s  temperature=%s  关闭思考=%s" % (
        cfg.get("model"),
        cfg.get("base_url") or "(SDK 默认)",
        masked,
        cfg.get("temperature"),
        "是" if cfg.get("extra_body") else "否",
    )


def make_judge_llm(temperature: float | None = None):
    """构造裁判 LLM（langchain ChatOpenAI）。

    厂商不认 extra_body 时降级为不带该字段重试，并记录 —— 静默回退会让
    「为什么这么慢/这么贵」变成悬案。
    """
    cfg = resolve_judge_config()

    from langchain_openai import ChatOpenAI

    kwargs = {
        "model": cfg["model"],
        "temperature": cfg["temperature"] if temperature is None else temperature,
        "api_key": cfg["api_key"],
        "base_url": cfg["base_url"],
    }
    extra = cfg.get("extra_body")
    if extra:
        kwargs["extra_body"] = extra

    try:
        return ChatOpenAI(**kwargs)
    except Exception as exc:
        if not extra:
            raise
        print(
            "  [warn] 厂商不接受「关闭思考」字段（%s），降级为默认参数重试：%s"
            % (extra, exc),
            flush=True,
        )
        kwargs.pop("extra_body")
        return ChatOpenAI(**kwargs)


def judge_payload_extras(cfg: dict | None = None) -> dict:
    """给 urllib / requests 直调的调用方用：要合并进请求体的字段。

    scripts/judge_scores.py 是裸 HTTP 调用，不走 langchain，需要这份。
    """
    cfg = cfg or resolve_judge_config()
    return dict(cfg.get("extra_body") or {})


if __name__ == "__main__":
    # 自检入口：python -m eval.judge_llm [--ping]
    # 用户填完 .env 之后先跑这个，确认「以为的配置」和「实际生效的配置」一致。
    import argparse
    import time

    ap = argparse.ArgumentParser(description="裁判模型配置自检")
    ap.add_argument(
        "--ping",
        action="store_true",
        help="额外发一条真实请求，验证 key / 模型 ID / 端点三者确实可用",
    )
    args = ap.parse_args()

    cfg = resolve_judge_config(require_key=False)
    print(describe_judge(cfg))
    if not cfg["api_key"]:
        print("\n[!] API Key 尚未配置：请在 backend/.env 填写 EVAL_JUDGE_API_KEY。")
        print("    在当前状态下跑评测会直接报错（这是刻意的：半套配置不静默回落）。")
        raise SystemExit(1)
    print("扩展请求体：", cfg["extra_body"] or "（无）")

    if args.ping:
        print("\n--- 连通性实测 ---")
        started = time.perf_counter()
        try:
            reply = make_judge_llm().invoke("只回复两个字：正常")
            elapsed = time.perf_counter() - started
            text = str(getattr(reply, "content", "") or "").strip()
            print("调用成功，耗时 %.2fs" % elapsed)
            print("模型回复：%s" % text[:120])
            usage = getattr(reply, "response_metadata", {}).get("token_usage", {})
            if usage:
                print("token 用量：%s" % usage)
        except Exception as exc:
            print("调用失败：%s: %s" % (type(exc).__name__, exc))
            print(
                "\n排查顺序：① EVAL_JUDGE_API_KEY 是否填对（百炼 key 以 sk- 开头）；"
                "② 模型 ID 是否存在（百炼控制台模型列表核对，版本号最易写错）；"
                "③ 端点是否属可用地域（免费额度仅限华北2/新加坡）。"
            )
            raise SystemExit(2)
