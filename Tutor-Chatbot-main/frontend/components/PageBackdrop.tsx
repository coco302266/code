"use client";

/**
 * 整屏背景图（页面底板）。
 *
 * 设计规则（改动前先读）：
 *   1. 图是**整屏铺满且清晰**的，不做模糊、不做低透明度。
 *      之前试过 10%–24% 半透明铺底：照片有明确物体轮廓，压淡后是一层位置随机的
 *      灰斑，既不可读也不好看。照片要么清晰出现，要么不出现。
 *   2. 图上必须有一层 canvas 色蒙版。没有它，深色标题压在照片暗部会掉到 AA 以下，
 *      卡片与背景的边界也会糊在一起。蒙版是渐变的：上部略轻（照片透气），
 *      下部略重（输入区、列表底部落在实底上）。
 *   3. 浮在照片上的容器用 `glass`（见 components/ui.tsx 的 Card）：
 *      白 85% + 背景模糊。正文仍然在浅色实底上，对比度不受照片影响。
 *
 * 为什么用 public/bg 下的本地文件：项目要 `next build` 出静态产物给 Electron
 * （`app://` 协议映射 out/），外链在离线桌面端直接空白。图片来源见 public/bg/CREDITS.md。
 */

export type BackdropVariant =
  | "home"
  | "classrooms"
  | "teacher"
  | "v1"
  | "group"
  | "debate";

const IMAGES: Record<
  BackdropVariant,
  { src: string; scrim: string; blur?: number; position?: string }
> = {
  home: {
    src: "/bg/home.jpg",
    // 白桌面 + 笔记本，本身很亮，蒙版可以轻一些仍然保得住深色标题
    scrim: "from-canvas/55 via-canvas/35 to-canvas/65",
  },
  classrooms: {
    src: "/bg/classrooms.jpg",
    scrim: "from-canvas/78 via-canvas/62 to-canvas/84",
  },
  // 三个课堂场景各一张：聊天页按当前课堂的 scenario 选图（见 app/classroom/page.tsx）。
  // 统一 14px 模糊 —— 气泡是主体，图只负责说明「这是哪种课堂」。
  v1: {
    src: "/bg/scene-v1.jpg",
    scrim: "from-canvas/50 via-canvas/42 to-canvas/58",
    blur: 4,
  },
  group: {
    src: "/bg/scene-group.jpg",
    scrim: "from-canvas/50 via-canvas/42 to-canvas/58",
    blur: 4,
  },
  debate: {
    // 观众席侧影：整幅都是主体，裁切不丢信息，语义上就是「台上在辩」
    src: "/bg/scene-debate.jpg",
    scrim: "from-canvas/46 via-canvas/38 to-canvas/56",
    blur: 4,
    position: "50% 45%",
  },
  teacher: {
    src: "/bg/teacher.jpg",
    scrim: "from-canvas/78 via-canvas/64 to-canvas/84",
  },
};

export function PageBackdrop({ variant }: { variant: BackdropVariant }) {
  const cfg = IMAGES[variant];

  return (
    <div
      aria-hidden="true"
      className="pointer-events-none absolute inset-0 z-0 overflow-hidden"
    >
      <img
        src={cfg.src}
        alt=""
        decoding="async"
        className="h-full w-full object-cover"
        style={{
          filter: cfg.blur
            ? `saturate(0.9) blur(${cfg.blur}px)`
            : "saturate(0.95)",
          transform: cfg.blur ? "scale(1.03)" : undefined,
          objectPosition: cfg.position,
        }}
      />
      <div className={`absolute inset-0 bg-gradient-to-b ${cfg.scrim}`} />
    </div>
  );
}
