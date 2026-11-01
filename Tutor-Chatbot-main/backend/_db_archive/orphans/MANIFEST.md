# 清理时归档的孤儿资料

2026-11-01 清理测试课堂（id 3、4、6、7、8、9、10），这些课堂的原件目录随之下线。
没有直接删除，挪到这里留档；对应的向量集合 `classroom_3_docs` / `classroom_7_docs`
已从 `chroma_classrooms` 删除（可由这里的原件重建）。

| 目录 | 原位置 | 说明 |
|---|---|---|
| `classroom_docs_3/` | `backend/classroom_docs/3/` | 课堂 3 的教师上传件 |
| `classroom_docs_7/` | `backend/classroom_docs/7/` | 课堂 7 的教师上传件 |

确认不再需要后，整个 `orphans/` 目录可以直接删。
