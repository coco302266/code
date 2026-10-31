# 背景图片来源与许可

所有图片来自 Unsplash，按 [Unsplash License](https://unsplash.com/license) 使用：
可免费用于商业与非商业用途，无需署名（此处署名仅作记录，不构成强制要求）。

## 页面底图

| 文件 | 用途 | 原始链接 |
|---|---|---|
| `home.jpg` | 入口 `/` | https://unsplash.com/photos/photo-1517694712202-14dd9538aa97 |
| `classrooms.jpg` | 我的课堂 `/classrooms/` | https://unsplash.com/photos/photo-1456513080510-7bf3a84b8-2f8 |
| `teacher.jpg` | 教师工作台 `/teacher/` | https://unsplash.com/photos/photo-1434030216411-0b793f4b4173 |

## 课堂场景图（聊天页按 scenario 自动切换）

| 文件 | 场景 | 原始链接 |
|---|---|---|
| `scene-v1.jpg` | 1v1 辅导（两人隔桌对谈） | https://unsplash.com/photos/photo-1556761175-b413da4baf72 |
| `scene-group.jpg` | 小组讨论（贴纸墙共创） | https://unsplash.com/photos/photo-1552664730-d307ca884978 |
| `scene-debate.jpg` | 辩论 / 发言（观众席侧影） | https://unsplash.com/photos/photo-1540575467063-178a50c2df87 |

下载参数：`w=1600–1920&q=52–62&fm=jpg&fit=crop`，单张 150–350 KB。

## 使用约束（改之前请读）

1. **聊天页的场景图是按 scenario 选的**，映射在 `app/classroom/page.tsx`：
   `debate → scene-debate`、`group → scene-group`、其余（含 v1）→ `scene-v1`。
   新增场景要同时补图、补映射、补 `PageBackdrop` 的变体。
2. 聊天页的图带 **4px 模糊 + 较轻的蒙版**（约 50/42/58），因为气泡是整块淡色卡片、
   正文不压在照片上。其余页面是清晰原图 + 较重蒙版（52%–84%）。
3. 选图三条硬标准：**主体要够大**（小物件裁切后就没影了 —— 换掉过一张麦克风特写，
   被右侧栏盖住）、**没有正脸特写**、**低饱和**。
   换掉过的还有：讲座现场（一堆人脸）、小学教室（一堆孩子的脸）。
4. 静态导出：`public/` 下的文件在 `next build` 时拷进 `out/bg/`，Electron 的
   `app://` 协议按 `out/` 映射，路径 `/bg/xxx.jpg` 在浏览器与桌面端都成立。
