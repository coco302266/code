# 背景图片来源与许可

三张图均来自 Unsplash，按 [Unsplash License](https://unsplash.com/license) 使用：
可免费用于商业与非商业用途，无需署名（此处署名仅作记录，不构成强制要求）。

| 文件 | 用途页面 | 原始链接 |
|---|---|---|
| `home.jpg` | 入口 `/` | https://unsplash.com/photos/photo-1517694712202-14dd9538aa97 |
| `classrooms.jpg` | 我的课堂 `/classrooms/` | https://unsplash.com/photos/photo-1456513080510-7bf3a84b82f8 |
| `teacher.jpg` | 教师工作台 `/teacher/` | https://unsplash.com/photos/photo-1434030216411-0b793f4b4173 |

下载参数：`w=1920&q=62&fm=jpg&fit=crop`，单张 190–260 KB，合计约 610 KB。

## 使用约束（改之前请读）

1. 三张图都是**整屏铺满**的，靠一层 canvas 色渐变蒙版（见 `components/PageBackdrop.tsx`）
   把照片压到能读的程度。聊天室 `/classroom/` **不放图** —— 那页是长时间读密集文字的地方。
2. 浮在照片上的容器用 `Card glass`（白 82% + 背景模糊）。**不要**把卡片改成更透的比例，
   深色正文压在照片暗部会掉到 AA 以下；也别把玻璃用在卡片内部的小条目上，全玻璃会糊成一团。
3. 不在卡片里的标题要加一块无边框柔光板（`bg-canvas/72 backdrop-blur-md`），
   否则 12–13px 的副标题直接压在照片上会看不清楚。`app/classrooms/page.tsx` 有现成写法。
4. 选图标准：没有正脸特写、没有密集小物体、低饱和。换图前先确认这三点 ——
   试过讲座现场（一堆人脸）和小学教室（一堆孩子的脸），都被换掉了。
5. 静态导出会带上这些文件：`public/` 下的内容在 `next build` 时拷进 `out/bg/`，
   Electron 的 `app://` 协议按 `out/` 映射，路径 `/bg/xxx.jpg` 在浏览器与桌面端都成立。
