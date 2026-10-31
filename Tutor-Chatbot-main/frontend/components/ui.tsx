"use client";

import type {
  ButtonHTMLAttributes,
  InputHTMLAttributes,
  ReactNode,
  Ref,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from "react";

type Variant = "primary" | "ghost" | "subtle" | "danger";

const BTN_STYLE: Record<Variant, string> = {
  primary: "bg-brand text-white hover:bg-brand-strong",
  ghost: "border border-line bg-panel text-ink hover:bg-soft",
  subtle: "bg-brand-soft text-brand hover:bg-brand-soft-strong",
  danger: "bg-danger text-white hover:bg-danger-strong",
};

export function Btn({
  variant = "ghost",
  className = "",
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }) {
  return (
    <button
      {...rest}
      className={`inline-flex shrink-0 items-center justify-center gap-1.5 rounded-lg px-3.5 py-2 text-[14px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-55 ${BTN_STYLE[variant]} ${className}`}
    />
  );
}

export function Card({
  className = "",
  glass = false,
  children,
}: {
  className?: string;
  /**
   * 玻璃卡片：浮在整屏背景图上时用。
   *
   * 82% 白 + 背景模糊是实测出来的下限：再透，深色正文压在照片暗部就开始费眼；
   * 再实，就退回普通白卡，图和卡片的层次感没了。
   * 只给「浮在照片上的大容器」用，卡片内部的小条目保持实底 —— 全玻璃会糊成一团。
   */
  glass?: boolean;
  children: ReactNode;
}) {
  const skin = glass
    ? "border-white/60 bg-panel/64 backdrop-blur-2xl shadow-[0_16px_40px_-20px_rgba(15,23,42,0.32)]"
    : "border-line bg-panel";
  return (
    <div className={`rounded-xl border ${skin} ${className}`}>{children}</div>
  );
}

type Tone = "neutral" | "brand" | "teacher" | "ai" | "danger" | "warn" | "ok";

const TAG_STYLE: Record<Tone, string> = {
  neutral: "bg-soft text-sub border-line",
  brand: "bg-brand-soft text-brand border-transparent",
  teacher: "bg-teacher-soft text-teacher border-transparent",
  ai: "bg-ai-soft text-ai border-transparent",
  danger: "bg-danger-soft text-danger border-transparent",
  warn: "bg-warn-soft text-warn-ink border-transparent",
  ok: "bg-ai-soft text-ai border-transparent",
};

export function Tag({
  tone = "neutral",
  className = "",
  children,
}: {
  tone?: Tone;
  className?: string;
  children: ReactNode;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[12px] leading-4 ${TAG_STYLE[tone]} ${className}`}
    >
      {children}
    </span>
  );
}

const BANNER_STYLE: Record<string, string> = {
  info: "bg-brand-soft text-brand border-brand-line",
  ok: "bg-ai-soft text-ai border-ok-line",
  warn: "bg-warn-soft text-warn-ink border-warn-line",
  error: "bg-danger-soft text-danger border-danger-line",
};

export function Banner({
  tone = "info",
  children,
  className = "",
}: {
  tone?: "info" | "ok" | "warn" | "error";
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`rounded-lg border px-3 py-2 text-[13px] leading-relaxed ${BANNER_STYLE[tone]} ${className}`}
    >
      {children}
    </div>
  );
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1.5 flex items-baseline gap-2">
        <span className="text-[13px] font-medium text-ink">{label}</span>
        {hint && <span className="text-[12px] text-hint">{hint}</span>}
      </span>
      {children}
    </label>
  );
}

const INPUT_BASE =
  "w-full rounded-lg border border-line bg-panel px-3 py-2 text-[14px] text-ink outline-none transition-colors placeholder:text-hint focus:border-brand focus:ring-2 focus:ring-brand-soft";

export function Input({
  className = "",
  ref,
  ...rest
}: InputHTMLAttributes<HTMLInputElement> & { ref?: Ref<HTMLInputElement> }) {
  return <input ref={ref} {...rest} className={`${INPUT_BASE} ${className}`} />;
}

export function Textarea({
  className = "",
  ref,
  ...rest
}: TextareaHTMLAttributes<HTMLTextAreaElement> & {
  ref?: Ref<HTMLTextAreaElement>;
}) {
  return (
    <textarea
      ref={ref}
      {...rest}
      className={`${INPUT_BASE} resize-none ${className}`}
    />
  );
}

export function Select({
  className = "",
  children,
  ...rest
}: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select {...rest} className={`${INPUT_BASE} cursor-pointer ${className}`}>
      {children}
    </select>
  );
}

export function Spinner({ className = "" }: { className?: string }) {
  return (
    <span
      className={`inline-block h-3.5 w-3.5 shrink-0 animate-spin rounded-full border-2 border-current border-t-transparent ${className}`}
    />
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-full items-center justify-center px-6 text-center text-[14px] text-hint">
      {children}
    </div>
  );
}

/** 把 ISO 时间显示成 HH:MM；解析不了就原样返回。 */
export function shortTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  });
}
