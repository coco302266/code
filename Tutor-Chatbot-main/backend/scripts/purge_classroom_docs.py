"""清理孤儿课堂资料（XB-23 方案 B 的运维配套）。

**什么时候需要它**：课堂被删（MySQL 里 `classroom_documents` 随 CASCADE 消失），
但磁盘上的原件目录 `classroom_docs/{id}/` 与向量 collection
`chroma_classrooms/classroom_{id}_docs` 不会自动消失——Chroma 和文件系统
都不归 MySQL 管。残留的后果不是「占点空间」这么轻：课堂 id 是自增复用的，
下一个同 id 的课堂会凭空继承上一届的讲义。

**默认只报告，加 `--apply` 才真删**。清理是不可逆操作，跟 `update_kb.py`
的 `--dry-run` 风格一致。

用法（backend/ 下）：
    ./venv/Scripts/python.exe scripts/purge_classroom_docs.py
    ./venv/Scripts/python.exe scripts/purge_classroom_docs.py --apply
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from agent.rag import classroom_store  # noqa: E402
from db.repository import list_classroom_ids_with_documents  # noqa: E402
from db.session import get_db  # noqa: E402


def find_orphans() -> dict[int, list[str]]:
    """返回 {课堂 id: [残留形态...]}，只含「Storage 有、登记表没有」的课堂。"""
    with get_db() as db:
        registered = list_classroom_ids_with_documents(db)
    storage = {
        "原件目录": classroom_store.list_storage_classroom_ids(),
        "向量 collection": classroom_store.list_storage_collection_ids(),
    }
    orphans: dict[int, list[str]] = {}
    for label, ids in storage.items():
        for classroom_id in ids - registered:
            orphans.setdefault(classroom_id, []).append(label)
    return orphans


def main() -> int:
    parser = argparse.ArgumentParser(description="清理孤儿课堂资料（默认 dry-run）")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="真的删除；不加只报告（默认行为）",
    )
    args = parser.parse_args()

    orphans = find_orphans()
    if not orphans:
        print("没有孤儿课堂资料，存储与登记表一致。")
        return 0

    print(f"发现 {len(orphans)} 个孤儿课堂：")
    for classroom_id, forms in sorted(orphans.items()):
        print(f"  - 课堂 {classroom_id}：{' / '.join(forms)}")

    if not args.apply:
        print("\n[dry-run] 未做任何删除。确认无误后加 --apply 执行。")
        return 0

    for classroom_id in sorted(orphans):
        removed = classroom_store.purge_classroom(classroom_id)
        print(
            f"  已清理课堂 {classroom_id}：collection={removed['collection']} "
            f"原件目录={removed['files_dir']}"
        )
    print("\n清理完成。若服务正在运行，被清课堂的检索缓存已失效（invalidate）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
