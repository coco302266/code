"""端到端首字延迟（TTFT）测量（2026-10-27 系统性调优用）。

/chat 是 SSE 流，事件顺序天然把延迟切成两段：
   请求 ──(评估 LLM ×2 + 知识库检索)──▶ sources ──(LLM 首字)──▶ token ...
所以「到 sources 的时间」≈ 评估 + 检索，「sources 到首个 token」≈ 生成首字延迟。
这两段的优化手段完全不同（前者靠缓存/减检索，后者靠换模型/减上下文），
必须分开量，否则会把「评估慢」误判成「生成慢」。

用法（后端已在 8000 运行）：
    ./venv/Scripts/python.exe scripts/measure_ttft.py
    ./venv/Scripts/python.exe scripts/measure_ttft.py --n 3 --query "死锁怎么预防？"

注意：/chat 限流 10/minute，脚本默认只跑 4 条并串行执行。
"""

import argparse
import json
import statistics
import sys
import time
import urllib.request


BASE = "http://127.0.0.1:8000"
DEFAULT_QUERIES = [
    "B+ 树索引为什么快？",
    "TCP 三次握手为什么是三次？",
    "死锁怎么预防？",
    "进程和线程有什么区别？",
]


def sse_stream(
    query: str,
    provider: str = "openai",
    history: list | None = None,
    topic: str = "",
) -> dict:
    """发一轮 /chat 并拆分 SSE 时间线。

    `history` / `topic` 是后加的可选参数（O12 要测「同一课堂连续两轮」，
    第二轮必须带上第一轮抽出的 topic，否则前端等价于新话题，测不到投机命中）。
    老调用方不传这两个参数，行为与之前完全一致。
    """
    payload = json.dumps(
        {
            "message": query,
            "provider": provider,
            "history": history or [],
            "topic": topic,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        BASE + "/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    started = time.perf_counter()
    t_sources = None
    t_first_token = None
    tokens = 0
    chars = 0
    n_sources = None
    topic = ""
    chunks: list[str] = []

    with urllib.request.urlopen(req, timeout=120) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data: "):
                continue
            data = line[6:]
            if data == "[DONE]":
                break
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue
            kind = event.get("type")
            if kind == "sources":
                t_sources = time.perf_counter() - started
                n_sources = len(event.get("items") or [])
            elif kind == "token":
                if t_first_token is None:
                    t_first_token = time.perf_counter() - started
                tokens += 1
                chars += len(event.get("content") or "")
                chunks.append(event.get("content") or "")
            elif kind == "state":
                # 本轮抽出的 topic —— 下一轮要回传，否则服务端视作新话题，
                # 投机执行永远命中不了。
                topic = event.get("topic") or ""

    total = time.perf_counter() - started
    return {
        "query": query,
        "pre_ms": round((t_sources or 0) * 1000),
        "ttft_ms": round((t_first_token or 0) * 1000),
        "gen_ms": round(((t_first_token or 0) - (t_sources or 0)) * 1000),
        "total_ms": round(total * 1000),
        "tokens": tokens,
        "chars": chars,
        "n_sources": n_sources,
        "topic": topic,
        "answer": "".join(chunks),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=4, help="跑几条（限流 10/min，别超）")
    ap.add_argument("--query", default="", help="只跑这一条；空则用内置探针")
    ap.add_argument("--provider", default="openai")
    args = ap.parse_args()

    queries = [args.query] * args.n if args.query else DEFAULT_QUERIES[: args.n]
    rows = []
    for q in queries:
        try:
            row = sse_stream(q, args.provider)
        except Exception as exc:
            print(f"请求失败 {q!r}: {type(exc).__name__}: {exc}")
            continue
        rows.append(row)
        print(
            f"{row['query'][:20]:<22} 前置(评估+检索) {row['pre_ms']:>6}ms "
            f"首字 {row['ttft_ms']:>6}ms 生成段 {row['gen_ms']:>6}ms "
            f"总 {row['total_ms']:>6}ms 资料 {row['n_sources']} 条 "
            f"{row['chars']} 字/{row['tokens']} chunk"
        )

    if not rows:
        return 1
    if len(rows) > 1:
        print("\n均值：")
        for key in ("pre_ms", "ttft_ms", "gen_ms", "total_ms"):
            vals = [r[key] for r in rows]
            print(f"  {key:<10} mean {statistics.mean(vals):7.0f}  max {max(vals):7}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
