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
  primary: "bg-brand text-white hover:bg-[#2560d4]",
  ghost: "border border-line bg-panel text-ink hover:bg-soft",
  subtle: "bg-brand-soft text-brand hover:bg-[#dbe7fb]",
  danger: "bg-danger text-white hover:bg-[#981d13]",
};

export function Btn({
  variant = "ghost",
  className = "",
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }) {
  return (
    <button
      {...rest}
      className={`inline-flex shrink-0 items-center justify-center gap-1.5 rounded-lg px-3.5 py-2 text-[13px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-55 ${BTN_STYLE[variant]} ${className}`}
    />
  );
}

export function Card({
  className = "",
  children,
}: {
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className={`rounded-xl border border-line bg-panel ${className}`}>
      {children}
    </div>
  );
}

type Tone = "neutral" | "brand" | "teacher" | "ai" | "danger" | "warn" | "ok";

const TAG_STYLE: Record<Tone, string> = {
  neutral: "bg-soft text-sub border-line",
  brand: "bg-brand-soft text-brand border-transparent",
  teacher: "bg-teacher-soft text-teacher border-transparent",
  ai: "bg-ai-soft text-ai border-transparent",
  danger: "bg-danger-soft text-danger border-transparent",
  warn: "bg-[#fef6e7] text-[#8a5a00] border-transparent",
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
      className={`inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] leading-4 ${TAG_STYLE[tone]} ${className}`}
    >
      {children}
    </span>
  );
}

const BANNER_STYLE: Record<string, string> = {
  info: "bg-brand-soft text-brand border-[#cfe0fb]",
  ok: "bg-ai-soft text-ai border-[#c6e9df]",
  warn: "bg-[#fef6e7] text-[#8a5a00] border-[#f6e2b8]",
  error: "bg-danger-soft text-danger border-[#f7cdc9]",
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
      className={`rounded-lg border px-3 py-2 text-[12.5px] leading-relaxed ${BANNER_STYLE[tone]} ${className}`}
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
        <span className="text-[12.5px] font-medium text-ink">{label}</span>
        {hint && <span className="text-[11.5px] text-hint">{hint}</span>}
      </span>
      {children}
    </label>
  );
}

const INPUT_BASE =
  "w-full rounded-lg border border-line bg-panel px-3 py-2 text-[13px] text-ink outline-none transition-colors placeholder:text-hint focus:border-brand focus:ring-2 focus:ring-brand-soft";

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
    <div className="flex h-full items-center justify-center px-6 text-center text-[13px] text-hint">
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
