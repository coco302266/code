"""验证「增量更新」与「全量重建」的产物等价（RAG_HANDBOOK §8 实施步骤 ④）。

用法（在 backend/ 目录下，用 venv 的 python）：
    python scripts/verify_incremental.py

**为什么需要它**：增量更新的全部价值建立在「增量结果 == 全量结果」之上。
只要有一次不等价，库里就会留下与源文件对不上的切片，而 `index_fingerprint`
只在对比两端时才发现——那时已经分不清是哪次操作引入的。所以每做完一次
切片/索引相关的改动，都要跑一遍这个脚本。

**判据是 `index_fingerprint`**：它对排序后的 (source, start_index, page_content)
取 sha256，不含 embedding 与插入顺序。两个库指纹相等 ⟺ 切片集合与正文完全一致。

场景：
    S0 无变更      —— 跑两次增量，第二次应无变更、指纹不变
    S1 新增 1 篇   —— 增量 vs 全量重建，指纹应相等
    S2 修改 1 篇   —— 同上
    S3 删除 1 篇   —— 同上（LangChain Indexing API 的 incremental 模式做不到这条）
    S4 切片参数变更 —— 应自动回退全量重建，且库内 chunk_params_hash 更新

全程在临时目录里跑独立 collection，不碰生产库（chroma_kb 与 eval/baseline_meta.json）。
有任一场景失败则非 0 退出。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
KNOWLEDGE_DIR = BACKEND_DIR.parents[1] / "knowledge"
PY = sys.executable

NEW_DOC = """# 增量验证新增篇

## 一句话结论

这一篇是验证脚本临时造的文档，用来确认新增文档走增量更新与走全量重建
得到的切片集合完全一致。

## 原理展开

它的内容刻意写得足够长，以便被切成多个 child 切片，从而覆盖「一个 parent
小节对应多个 child」的情形——这是父子切片下最容易出错的地方。
"""


def run(cmd, env_extra=None, timeout=900):
    env = dict(os.environ)
    env["RAG_PARENT_CHILD"] = "1"
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        cmd,
        cwd=str(BACKEND_DIR),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def copy_corpus(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def read_fp(meta_path: Path) -> str:
    if not meta_path.exists():
        return ""
    try:
        return json.loads(meta_path.read_text(encoding="utf-8")).get(
            "index_fingerprint", ""
        )
    except (OSError, json.JSONDecodeError):
        return ""


def build_full(kb: Path, db: Path, meta: Path, env_extra=None):
    return run(
        [
            PY, "build_kb.py",
            "--knowledge-dir", str(kb),
            "--persist-dir", str(db),
            "--meta-path", str(meta),
        ],
        env_extra,
    )


def update_inc(kb: Path, db: Path, meta: Path, env_extra=None, extra_args=()):
    return run(
        [
            PY, "scripts/update_kb.py",
            "--knowledge-dir", str(kb),
            "--persist-dir", str(db),
            "--meta-path", str(meta),
            *extra_args,
        ],
        env_extra,
    )


def main() -> int:
    if not KNOWLEDGE_DIR.is_dir():
        print("知识目录不存在：%s" % KNOWLEDGE_DIR)
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="kbverify_"))
    results = []
    try:
        base = tmp / "base"
        work = tmp / "work"
        inc_kb, inc_db, inc_meta = tmp / "inkb", tmp / "indb", tmp / "inc_meta.json"
        full_kb, full_db, full_meta = tmp / "fullkb", tmp / "fulldb", tmp / "full_meta.json"

        copy_corpus(KNOWLEDGE_DIR, base)
        copy_corpus(base, work)

        print("=" * 68)
        print("增量更新等价性验证（临时目录：%s）" % tmp.name)
        print("语料：%s（%d 篇）" % (KNOWLEDGE_DIR, len(list(base.rglob("*.md")))))
        print("=" * 68)

        # ---- 起点：增量库先全量建一次，让库带上 doc_hash ----
        copy_corpus(work, inc_kb)
        rc, out = build_full(inc_kb, inc_db, inc_meta)
        print("\n[起点] 增量库全量构建 rc=%d 指纹=%s" % (rc, read_fp(inc_meta)))
        if rc != 0:
            print(out[-2000:])
            return 1
        fp_start = read_fp(inc_meta)

        def scenario(name, mutate, expect_change=True):
            """施加变更 → 增量更新 vs 全量重建 → 断言指纹相等。"""
            print("\n" + "-" * 68)
            print("[%s]" % name)
            mutate(work)

            copy_corpus(work, inc_kb)
            rc_inc, out_inc = update_inc(inc_kb, inc_db, inc_meta)
            fp_inc = read_fp(inc_meta)

            copy_corpus(work, full_kb)
            rc_full, out_full = build_full(full_kb, full_db, full_meta)
            fp_full = read_fp(full_meta)

            ok = rc_inc == 0 and rc_full == 0 and fp_inc == fp_full
            if expect_change:
                ok = ok and fp_inc != fp_start and fp_inc != ""

            print("  增量：rc=%d 指纹=%s" % (rc_inc, fp_inc))
            print("  全量：rc=%d 指纹=%s" % (rc_full, fp_full))
            print("  结论：%s" % ("PASS（两者等价）" if ok else "FAIL"))
            if not ok:
                print("  --- 增量输出尾部 ---")
                print("\n".join(out_inc.strip().splitlines()[-15:]))
                print("  --- 全量输出尾部 ---")
                print("\n".join(out_full.strip().splitlines()[-15:]))
            results.append((name, ok))
            return ok

        # S0 无变更
        print("\n" + "-" * 68)
        print("[S0 无变更]")
        copy_corpus(work, inc_kb)
        rc0, out0 = update_inc(inc_kb, inc_db, inc_meta)
        fp0 = read_fp(inc_meta)
        ok0 = rc0 == 0 and fp0 == fp_start and "无变更" in out0
        print("  第二次增量：rc=%d 指纹=%s（起点 %s）" % (rc0, fp0, fp_start))
        print("  结论：%s" % ("PASS（未改动库）" if ok0 else "FAIL"))
        if not ok0:
            print("\n".join(out0.strip().splitlines()[-15:]))
        results.append(("S0 无变更", ok0))

        # S1 新增
        def add_doc(root: Path):
            target = root / "cs" / "os" / "verify-incremental-new.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(NEW_DOC, encoding="utf-8")

        scenario("S1 新增 1 篇", add_doc)

        # S2 修改
        def modify_doc(root: Path):
            target = root / "cs" / "os" / "deadlock.md"
            if not target.exists():
                candidates = sorted(root.rglob("*.md"))
                target = candidates[0]
            text = target.read_text(encoding="utf-8")
            target.write_text(
                text + "\n\n## 增量验证追加小节\n\n这一节是验证脚本追加的，用来触发"
                "「文件内容变了」的判定：doc_hash 变化 → 该篇整篇删+插。\n",
                encoding="utf-8",
            )

        scenario("S2 修改 1 篇", modify_doc)

        # S3 删除
        def del_doc(root: Path):
            target = root / "cs" / "os" / "verify-incremental-new.md"
            if target.exists():
                target.unlink()
            else:
                sorted(root.rglob("*.md"))[0].unlink()

        scenario("S3 删除 1 篇", del_doc)

        # S3b 一次删除多篇 —— 专门覆盖 delete_sources 的 `$in` 批量分支
        # （只删 1 篇时走的是单值 where，覆盖不到 $in；批量删除在大规模库上
        #  是主要成本项，必须单独验过）
        def del_many(root: Path):
            for target in sorted(root.rglob("*.md"))[:2]:
                target.unlink()

        scenario("S3b 一次删除 2 篇（$in 分支）", del_many)

        # S4 切片参数变更 → 应自动回退全量
        print("\n" + "-" * 68)
        print("[S4 切片参数变更（RAG_CHILD_CHUNK_SIZE 200→300）]")
        copy_corpus(work, inc_kb)
        rc4, out4 = update_inc(inc_kb, inc_db, inc_meta, {"RAG_CHILD_CHUNK_SIZE": "300"})
        fp4 = read_fp(inc_meta)
        ok4 = (
            rc4 == 0
            and "需要全量重建" in out4
            and "自动回退为全量重建" in out4
            and fp4 != fp_start
        )
        print("  rc=%d 指纹=%s" % (rc4, fp4))
        print("  结论：%s" % ("PASS（自动回退全量）" if ok4 else "FAIL"))
        if not ok4:
            print("\n".join(out4.strip().splitlines()[-20:]))
        results.append(("S4 切片参数变更", ok4))

        # S5 扁平模式（RAG_PARENT_CHILD=0）—— 父子路径验过不代表扁平也对，
        # 两条切片路径产出不同的 metadata，各自都要能增量
        print("\n" + "-" * 68)
        print("[S5 扁平模式新增 1 篇（RAG_PARENT_CHILD=0）]")
        flat_env = {"RAG_PARENT_CHILD": "0"}
        f_work, f_inc_kb, f_inc_db = tmp / "fwork", tmp / "finkb", tmp / "findb"
        f_inc_meta = tmp / "finc_meta.json"
        f_full_kb, f_full_db, f_full_meta = (
            tmp / "ffullkb",
            tmp / "ffulldb",
            tmp / "ffull_meta.json",
        )
        copy_corpus(base, f_work)
        copy_corpus(f_work, f_inc_kb)
        rc_a, out_a = build_full(f_inc_kb, f_inc_db, f_inc_meta, flat_env)
        fp_a = read_fp(f_inc_meta)
        add_doc(f_work)
        copy_corpus(f_work, f_inc_kb)
        rc_b, out_b = update_inc(f_inc_kb, f_inc_db, f_inc_meta, flat_env)
        fp_b = read_fp(f_inc_meta)
        copy_corpus(f_work, f_full_kb)
        rc_c, out_c = build_full(f_full_kb, f_full_db, f_full_meta, flat_env)
        fp_c = read_fp(f_full_meta)

        ok5 = rc_a == 0 and rc_b == 0 and rc_c == 0 and fp_b == fp_c and fp_b != fp_a
        print("  起点：rc=%d 指纹=%s" % (rc_a, fp_a))
        print("  增量：rc=%d 指纹=%s" % (rc_b, fp_b))
        print("  全量：rc=%d 指纹=%s" % (rc_c, fp_c))
        print("  结论：%s" % ("PASS（两者等价）" if ok5 else "FAIL"))
        if not ok5:
            for tag, out in (("起点", out_a), ("增量", out_b), ("全量", out_c)):
                print("  --- %s 输出尾部 ---" % tag)
                print("\n".join(out.strip().splitlines()[-10:]))
        results.append(("S5 扁平模式新增", ok5))

        # 汇总
        print("\n" + "=" * 68)
        print("汇总")
        for name, ok in results:
            print("  %-24s %s" % (name, "PASS" if ok else "FAIL"))
        failed = [n for n, ok in results if not ok]
        print("=" * 68)
        if failed:
            print("失败场景：%s" % "、".join(failed))
            return 1
        print("全部通过：增量更新在全场景下与全量重建等价。")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
