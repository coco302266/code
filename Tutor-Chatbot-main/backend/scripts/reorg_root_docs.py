"""一次性迁移：把项目根散落的 37 份 md/txt 收进 docs/，同类合并。

为什么要有这个脚本
------------------
根目录原有 37 份文档（另有 AGENTS/CLAUDE/README 三份必须留在根），
全部按文件名被引用（API_CONTRACT.md 47 次、RAG_HANDBOOK.md 40 次）。
手工搬会漏改引用，所以把「备份 -> 改写引用 -> 合并 -> 移动」写成脚本。

分类口径
--------
    specs/     规范与契约，被引用最密，保持独立文件（不合并）
    proposals/ 7 份 PROPOSAL_*    -> PROPOSALS.md
    reports/   9 份实验/评测报告  -> REPORTS.md
    plans/     4 份计划           -> PLANS.md
    handoff/   4 份交接类         -> HANDOFF.md（NEXT_SESSION_PROMPT 等 4 份保持独立）
    worklogs/  B/C_WORKLOG        追加型日志，不合并
    docs/tutorial.md              原样移动

引用改写规则
------------
统一改成「仓库根相对路径」（docs/specs/X.md），不用 ../ 相对路径：
文档在 docs/ 内部互相引用时 ../ 的层级各不相同，根相对路径是唯一不会算错的口径。
被合并掉的文件名统一指向合并后的文件，合并文件顶部保留「原名索引」，按旧名仍能搜到。

执行顺序不能换：先改写引用、再合并。
反过来的话，合并产物里的「原名索引」会被改写成指向自己，旧文件名就再也搜不到了。

注意：本文件自身必须排除在改写之外，否则常量会被改写成新路径，重跑即失效。

用法：
    venv/Scripts/python.exe scripts/reorg_root_docs.py            # 演练
    venv/Scripts/python.exe scripts/reorg_root_docs.py --apply    # 执行
"""

from __future__ import annotations

import argparse
import re
import shutil
from datetime import datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
# 文档在仓库根（AI-Classroom/），backend 在 Tutor-Chatbot-main/ 下，故取 parents[1]
ROOT = BACKEND.parents[1]
ARCHIVE = ROOT / "_docs_archive_20261030"

KEEP_AT_ROOT = {"AGENTS.md", "CLAUDE.md", "README.md"}

# 目录 -> 保持独立文件、仅移动的文档（裸文件名）
MOVES: dict[str, list[str]] = {
    "docs/specs": [
        "API_CONTRACT.md",
        "AGENT_PROTOCOL.md",
        "REQUIREMENTS.md",
        "EVAL_METRICS_API.md",
        "RAG_HANDBOOK.md",
        "DEVELOPMENT.md",
    ],
    "docs/handoff": [
        "NEXT_SESSION_PROMPT.md",
        "PROJECT_REPORT.md",
        "STAGE_REPORT_M1_M3.md",
        "STAGE_REPORT_T8_T9.md",
    ],
    "docs/worklogs": ["B_WORKLOG.md", "C_WORKLOG.md"],
    "docs": ["tutorial.md"],
}

# (目标文件, 标题, 导语, [源文件裸名])
MERGES: list[tuple[str, str, str, list[str]]] = [
    (
        "docs/proposals/PROPOSALS.md",
        "AI-Classroom 设计方案合集",
        "本文件由根目录 7 份独立的 PROPOSAL 合并而成（2026-10-30）。合并只为止住根目录散落，"
        "正文一字未改，仅把每份文档的标题层级整体下调两级以适配本文档的层级。"
        "按旧文件名检索时，用下面「原名索引」的锚点直达；原文件内部的小节编号（如 4.2）"
        "在合并后不再是唯一编号，请用标题文字搜索定位。",
        [
            "PROPOSAL_D2_D3.md",
            "PROPOSAL_RAG_ARCH.md",
            "PROPOSAL_RAG_EVAL_DATASET.md",
            "PROPOSAL_RAG_OBS.md",
            "PROPOSAL_RAG_SCALE.md",
            "PROPOSAL_SCOPE_PREFILTER.md",
            "PROPOSAL_TEACHER_MATERIALS.md",
        ],
    ),
    (
        "docs/reports/REPORTS.md",
        "AI-Classroom 实验与评测报告合集",
        "本文件由根目录 9 份独立报告合并而成（2026-10-30）。合并只为止住根目录散落，"
        "正文一字未改，仅把标题层级整体下调两级。口径提醒：这些报告横跨不同题集、"
        "不同配置、不同时间，数字之间不可直接相比，引用时必须带上报告名与题集规模。",
        [
            "RERANK_COST_QUALITY_COMPARISON.md",
            "RERANK_P1_EXEC_REPORT.md",
            "RERANK_STRATEGY_SUMMARY.md",
            "RERANK_TEXT_GRANULARITY_REPORT.md",
            "EVAL_SET_V3_BUILD_REPORT.md",
            "EMBEDDING_SWAP_VALIDATION.md",
            "RETRIEVAL_ACCURACY_CEILING.md",
            "V3_AB_SIGNIFICANCE_REPORT.md",
            "TEST_REPORT.md",
        ],
    ),
    (
        "docs/plans/PLANS.md",
        "AI-Classroom 开发与调优计划合集",
        "本文件由根目录 4 份计划合并而成（2026-10-30）。合并只为止住根目录散落，"
        "正文一字未改，仅把标题层级整体下调两级。计划类文档按「当时已知」写成，"
        "落地后的事实以 docs/specs/RAG_HANDBOOK.md 与 docs/worklogs/B_WORKLOG.md 为准。",
        [
            "DEVELOPMENT_PLAN.md",
            "FRONTEND_PLAN.md",
            "RAG_TUNING_PLAN.md",
            "RAG_RERANK_SELECTION_PLAN.md",
        ],
    ),
    (
        "docs/handoff/HANDOFF.md",
        "AI-Classroom 交接说明合集",
        "本文件由根目录 4 份交接类文档合并而成（2026-10-30）。合并只为止住根目录散落，"
        "正文一字未改。其中《打开前请阅读我》原是等宽排版，为保住 ASCII 分隔线的对齐，"
        "整篇放进 text 代码块。",
        [
            "HANDOFF_M4.md",
            "HANDOFF_RAG_LIBRARY.md",
            "项目交接说明.md",
            "打开前请阅读我.txt",
        ],
    ),
]

CODE_BLOCK_SUFFIX = {".txt"}

TEXT_SUFFIX = {".md", ".markdown", ".txt", ".py", ".ts", ".tsx", ".js", ".json", ".yml", ".yaml"}
SKIP_DIRS = {
    ".git", ".next", "node_modules", "venv", "__pycache__", ".model_cache",
    ".model_cache_local", "chroma_kb", "chroma_classrooms", "_chroma_archive",
    ".idea", ".workbuddy", "_docs_archive_20261030",
}
# 改写引用时必须跳过的文件：脚本自身、合并目标
SELF = Path(__file__).resolve()


def slug(name: str) -> str:
    stem = Path(name).stem
    s = re.sub(r"[^0-9a-zA-Z]+", "-", stem).strip("-").lower()
    return s or "doc"


# 纯中文文件名经 slug() 会把字符全部剥掉、统一退化成 doc，导致锚点重复。
# 这里给它们固定 ASCII 锚点，重建合并文件时锚点不会变。
NAME_ALIAS = {
    "项目交接说明.md": "project-handoff",
    "打开前请阅读我.txt": "read-me-first",
}


def doc_slug(name: str, used: set[str] | None = None) -> str:
    s = NAME_ALIAS.get(name) or slug(name)
    if used is not None:
        base, i = s, 2
        while s in used:
            s = "%s-%d" % (base, i)
            i += 1
        used.add(s)
    return s


def demote(text: str, levels: int = 2) -> str:
    """标题整体降 levels 级：合并文件自己是 H1，小节是 H2，原文 H1 变 H3。"""
    out = []
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        if not in_fence:
            m = re.match(r"^(#{1,%d})(\s+)" % (6 - levels), line)
            if m:
                line = "#" * levels + line
        out.append(line)
    return "\n".join(out)


def first_heading(text: str) -> str:
    for line in text.splitlines():
        m = re.match(r"^#{1,3}\s+(.+?)\s*$", line.strip())
        if m:
            return m.group(1)[:40]
    return ""


def collect_targets() -> list[Path]:
    out: list[Path] = []
    for _, names in MOVES.items():
        for n in names:
            p = ROOT / n
            if p.exists():
                out.append(p)
            else:
                print("  仍不存在，跳过：", n)
    for _, _, _, names in MERGES:
        for n in names:
            p = ROOT / n
            if p.exists():
                out.append(p)
            else:
                print("  仍不存在，跳过：", n)
    return out


def backup(files: list[Path], apply: bool) -> None:
    if not apply:
        return
    ARCHIVE.mkdir(exist_ok=True)
    lines = [
        "# 根目录文档归档备份（2026-10-30）",
        "",
        "迁移到 docs/ 之前的原文件全量副本，用于回滚。确认 docs/ 结构无误后可整体删除。",
        "对应迁移脚本：Tutor-Chatbot-main/backend/scripts/reorg_root_docs.py",
        "",
        "注意：备份是「改写引用之前」的版本，里面的交叉引用仍是旧裸文件名。",
        "",
        "```text",
        "%-42s %10s  %s" % ("文件名", "字节", "原始修改时间"),
    ]
    for f in files:
        shutil.copy2(f, ARCHIVE / f.name)
        st = f.stat()
        lines.append(
            "%-42s %10d  %s"
            % (f.name, st.st_size, datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"))
        )
    lines += ["```", ""]
    (ARCHIVE / "MANIFEST.md").write_text("\n".join(lines), encoding="utf-8")


def build_mapping() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for rel_dir, names in MOVES.items():
        for n in names:
            mapping[n] = rel_dir + "/" + n
    for rel_target, _, _, names in MERGES:
        for n in names:
            mapping[n] = rel_target
    return mapping


def rewrite_text(text: str, mapping: dict[str, str], force_fence: bool = False) -> tuple[str, int]:
    """把裸文件名改写为仓库根相对路径，返回（新文本, 改写次数）。

    代码块内有两类内容必须区别对待：
    - 命令/示例：路径应该跟着改，否则命令失效；
    - 等宽对齐的目录清单：加长路径会把右侧列挤歪，这类行跳过不改。
    判据是「文件名后面跟着 2 个以上空格」，对齐表格才长这样。

    force_fence=True 用于整篇都是等宽排版的 .txt：它合并后会被包进代码块，
    所以按「全部处于代码块内」处理。
    """
    total = 0
    out_lines = []
    in_fence = force_fence
    for line in text.splitlines():
        if not force_fence and line.lstrip().startswith("```"):
            in_fence = not in_fence
            out_lines.append(line)
            continue
        new = line
        for name, dest in mapping.items():
            if name not in new:
                continue
            pattern = re.compile(r"(?<![\w/.\-])" + re.escape(name))
            if in_fence:
                # 逐处判断：命中位置后面若是 2 个以上空格，视为对齐列，保留原样
                def _sub(m, dest=dest):
                    tail = m.string[m.end():]
                    if re.match(r"\s{2,}\S", tail):
                        return m.group(0)
                    return dest

                new, n = pattern.subn(_sub, new)
            else:
                new, n = pattern.subn(dest, new)
            total += n
        out_lines.append(new)
    return "\n".join(out_lines), total


def rewrite_refs(mapping: dict[str, str], apply: bool) -> dict[str, int]:
    """把全文里的裸文件名改写成仓库根相对路径（必须先于合并执行）。"""
    hits: dict[str, int] = {}
    skip_files = {SELF} | {(ROOT / t).resolve() for t, _, _, _ in MERGES}
    for p in ROOT.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in TEXT_SUFFIX:
            continue
        if any(part in SKIP_DIRS for part in p.relative_to(ROOT).parts):
            continue
        if p.resolve() in skip_files:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        new, _ = rewrite_text(text, mapping)
        if new != text and apply:
            p.write_text(new, encoding="utf-8")
        for name in mapping:
            # 只统计「裸」出现次数（前面不是路径分隔符或字母数字）
            pat = re.compile(r"(?<![\w/.\-])" + re.escape(name))
            n = len(pat.findall(text)) - len(pat.findall(new))
            if n > 0:
                hits[name] = hits.get(name, 0) + n
    return hits


def build_merge(
    target: Path,
    title: str,
    intro: str,
    sources: list[Path],
    apply: bool,
    mapping: dict[str, str] | None = None,
) -> str:
    used: set[str] = set()
    anchors = [doc_slug(s.name, used) for s in sources]
    chunks = ["# %s" % title, "", "> " + intro, "", "## 原名索引（按旧文件名查）", ""]
    for i, src in enumerate(sources, 1):
        head = src.read_text(encoding="utf-8", errors="ignore").lstrip("\ufeff")
        hint = first_heading(head)
        suffix = ("（%s）" % hint) if hint and src.suffix == ".md" else ""
        chunks.append("- `%s`%s —— 第 %d 节，[跳转](#%s)" % (src.name, suffix, i, anchors[i - 1]))
    chunks.append("")
    for i, src in enumerate(sources, 1):
        text = src.read_text(encoding="utf-8", errors="ignore").lstrip("\ufeff")
        if mapping:
            text, _ = rewrite_text(text, mapping, force_fence=src.suffix in CODE_BLOCK_SUFFIX)
        chunks += ["---", "", '<a id="%s"></a>' % anchors[i - 1], ""]
        chunks += ["## %d、原名 %s" % (i, src.name), ""]
        if src.suffix in CODE_BLOCK_SUFFIX:
            chunks += ["```text", text.rstrip(), "```", ""]
        else:
            chunks += [demote(text).replace("\r\n", "\n").rstrip(), ""]
    out = "\n".join(chunks)
    if apply:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(out, encoding="utf-8")
    return str(target.relative_to(ROOT)).replace("\\", "/")


def move_one(src: Path, target_dir: Path, apply: bool) -> str:
    dst = target_dir / src.name
    if dst.exists():
        raise SystemExit("目标已存在，拒绝覆盖：%s" % dst)
    if apply:
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
    return str(dst.relative_to(ROOT)).replace("\\", "/")


def rebuild_from_archive(apply: bool) -> int:
    """从 _docs_archive_*/ 的原件重生成全部 docs/ 产物。

    用途：改写规则变了（比如代码块里的等宽清单不该动）需要重出结果时，
    不必回滚再迁一遍 —— 原件都在备份里，直接重新生成即可，结果可复现。
    """
    if not ARCHIVE.is_dir():
        raise SystemExit("找不到备份目录：%s" % ARCHIVE)
    mapping = build_mapping()
    missing = [n for n in mapping if not (ARCHIVE / n).exists()]
    if missing:
        print("备份里缺这些文件，无法重建：", missing)
        return 1

    print("=== 重建合并文件 ===")
    for rel_target, title, intro, names in MERGES:
        sources = [ARCHIVE / n for n in names]
        dest = build_merge(ROOT / rel_target, title, intro, sources, apply, mapping=mapping)
        print("  %-34s <- %2d 份" % (dest, len(sources)))

    print("\n=== 重建独立文件 ===")
    for rel_dir, names in MOVES.items():
        for n in names:
            text = (ARCHIVE / n).read_text(encoding="utf-8", errors="ignore")
            new, hits = rewrite_text(text, mapping)
            dst = ROOT / rel_dir / n
            if apply:
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_text(new, encoding="utf-8")
            print("  %-34s 改写 %d 处" % (rel_dir + "/" + n, hits))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正执行（默认只演练）")
    ap.add_argument("--rebuild", action="store_true", help="从备份重新生成 docs/ 产物")
    args = ap.parse_args(argv)
    apply = args.apply

    if args.rebuild:
        return rebuild_from_archive(apply)

    targets = collect_targets()
    print("=== 待处理 %d 份（%s）===" % (len(targets), "执行" if apply else "演练"))
    for p in sorted(targets):
        print("  %-42s %8d 字节" % (p.name, p.stat().st_size))
    if not targets:
        print("没有可处理的文件，可能已经迁移过。")
        return 0

    backup(targets, apply)
    print("备份目录：", ARCHIVE.name)

    mapping = build_mapping()

    print("\n=== 改写引用（先于合并，避免改名索引变成自引用）===")
    hits = rewrite_refs(mapping, apply)
    for name, n in sorted(hits.items(), key=lambda kv: -kv[1]):
        print("  %-42s %3d 处 -> %s" % (name, n, mapping[name]))
    print("  合计改写 %d 处" % sum(hits.values()))

    print("\n=== 合并 ===")
    for rel_target, title, intro, names in MERGES:
        sources = [ROOT / n for n in names if (ROOT / n).exists()]
        dest = build_merge(ROOT / rel_target, title, intro, sources, apply)
        print("  %-34s <- %2d 份" % (dest, len(sources)))
        if apply:
            for src in sources:
                src.unlink()
            print("  %-34s    已删除 %d 份原件（备份可回滚）" % ("", len(sources)))

    print("\n=== 独立移动 ===")
    for rel_dir, names in MOVES.items():
        for n in names:
            src = ROOT / n
            if not src.exists():
                continue
            dest = move_one(src, ROOT / rel_dir, apply)
            print("  %-46s -> %s" % (n, dest))

    if apply:
        left = sorted(p.name for p in ROOT.iterdir() if p.is_file() and p.suffix in (".md", ".txt"))
        print("\n=== 根目录剩余文档 ===")
        for n in left:
            print("  ", n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
