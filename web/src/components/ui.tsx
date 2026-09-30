"use client";

import { useEffect, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from "react";

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

type Variant = "primary" | "secondary" | "ghost" | "danger" | "ok";

export function Button({
  variant = "secondary",
  size = "md",
  className,
  loading,
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: "sm" | "md"; loading?: boolean }) {
  const styles: Record<Variant, string> = {
    primary: "bg-accent-2 hover:bg-accent text-white border-transparent",
    secondary: "bg-panel-2 hover:bg-line text-ink border-line-2",
    ghost: "bg-transparent hover:bg-panel-2 text-ink-2 hover:text-ink border-transparent",
    danger: "bg-bad/15 hover:bg-bad/25 text-bad border-bad/30",
    ok: "bg-ok/15 hover:bg-ok/25 text-ok border-ok/30",
  };
  return (
    <button
      {...rest}
      disabled={rest.disabled || loading}
      className={cx(
        "inline-flex items-center justify-center gap-1.5 rounded-md border font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer select-none",
        size === "sm" ? "h-7 px-2.5 text-xs" : "h-9 px-3.5 text-sm",
        styles[variant],
        className,
      )}
    >
      {loading && <Spinner className="size-3.5" />}
      {children}
    </button>
  );
}

export function Spinner({ className }: { className?: string }) {
  return (
    <svg className={cx("animate-spin", className ?? "size-4")} viewBox="0 0 24 24" fill="none">
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity="0.25" strokeWidth="3" />
      <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

/** Default to full width unless the caller sets a width. */
const widthOr = (className?: string) => (className && /(^|\s)(w-|flex-1)/.test(className) ? undefined : "w-full");

const field =
  "rounded-md border border-line-2 bg-bg px-3 text-sm text-ink placeholder:text-ink-3 focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/40";

export function Input({ className, ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...rest} className={cx(field, "h-9", widthOr(className), className)} />;
}

export function Textarea({ className, ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...rest} className={cx(field, "py-2 leading-relaxed", widthOr(className), className)} />;
}

export function Select({ className, children, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select {...rest} className={cx(field, "h-9 pr-8", widthOr(className), className)}>
      {children}
    </select>
  );
}

export function Label({ children, hint }: { children: ReactNode; hint?: ReactNode }) {
  return (
    <div className="mb-1.5 flex items-baseline justify-between gap-2">
      <span className="text-xs font-medium text-ink-2">{children}</span>
      {hint && <span className="text-[11px] text-ink-3">{hint}</span>}
    </div>
  );
}

export function Field({ label, hint, children }: { label: ReactNode; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="block">
      <Label hint={hint}>{label}</Label>
      {children}
    </label>
  );
}

export function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label?: ReactNode }) {
  return (
    <button
      type="button"
      onClick={() => onChange(!checked)}
      className="inline-flex items-center gap-2 text-sm text-ink-2 cursor-pointer"
    >
      <span className={cx("relative h-5 w-9 rounded-full transition-colors", checked ? "bg-accent-2" : "bg-line-2")}>
        <span className={cx("absolute top-0.5 size-4 rounded-full bg-white transition-all", checked ? "left-4.5" : "left-0.5")} />
      </span>
      {label}
    </button>
  );
}

export function Badge({ children, tone = "neutral", className }: { children: ReactNode; tone?: Tone; className?: string }) {
  return (
    <span className={cx("inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-medium leading-none", toneClass[tone], className)}>
      {children}
    </span>
  );
}

export type Tone = "neutral" | "accent" | "ok" | "warn" | "bad" | "info";
export const toneClass: Record<Tone, string> = {
  neutral: "bg-line text-ink-2",
  accent: "bg-accent/15 text-accent",
  ok: "bg-ok/15 text-ok",
  warn: "bg-warn/15 text-warn",
  bad: "bg-bad/15 text-bad",
  info: "bg-info/15 text-info",
};

export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cx("rounded-lg border border-line bg-panel", className)}>{children}</div>;
}

export function Empty({ title, children, icon }: { title: string; children?: ReactNode; icon?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-12 text-center">
      {icon && <div className="text-3xl opacity-60">{icon}</div>}
      <div className="text-sm font-medium text-ink">{title}</div>
      {children && <div className="max-w-sm text-sm text-ink-3">{children}</div>}
    </div>
  );
}

export function Modal({
  open,
  onClose,
  title,
  children,
  wide,
  footer,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  children: ReactNode;
  wide?: boolean;
  footer?: ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/60 p-2 pt-4 backdrop-blur-sm sm:p-4 sm:pt-[8vh]" onMouseDown={onClose}>
      <div
        className={cx("fade-in w-full rounded-xl border border-line-2 bg-panel shadow-2xl", wide ? "max-w-4xl" : "max-w-lg")}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-line px-5 py-3.5">
          <div className="text-sm font-semibold">{title}</div>
          <button onClick={onClose} className="text-ink-3 hover:text-ink cursor-pointer" aria-label="Close">
            ✕
          </button>
        </div>
        <div className="max-h-[75dvh] overflow-y-auto px-4 py-4 sm:px-5">{children}</div>
        {footer && <div className="flex justify-end gap-2 border-t border-line px-5 py-3">{footer}</div>}
      </div>
    </div>
  );
}

export function Drawer({ open, onClose, children, width = "w-[480px]" }: { open: boolean; onClose: () => void; children: ReactNode; width?: string }) {
  useEffect(() => {
    if (!open) return;
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/40" onMouseDown={onClose}>
      <div
        className={cx("fade-in h-full max-w-full overflow-y-auto border-l border-line-2 bg-panel shadow-2xl", width)}
        onMouseDown={(e) => e.stopPropagation()}
      >
        {children}
      </div>
    </div>
  );
}

export function Tabs<T extends string>({
  value,
  onChange,
  items,
  className,
}: {
  value: T;
  onChange: (v: T) => void;
  items: { value: T; label: ReactNode }[];
  className?: string;
}) {
  return (
    <div className={cx("flex gap-1 rounded-lg bg-bg p-1", className)}>
      {items.map((it) => (
        <button
          key={it.value}
          onClick={() => onChange(it.value)}
          className={cx(
            "rounded-md px-3 py-1.5 text-xs font-medium transition-colors cursor-pointer",
            value === it.value ? "bg-panel-2 text-ink shadow" : "text-ink-3 hover:text-ink",
          )}
        >
          {it.label}
        </button>
      ))}
    </div>
  );
}

export function ErrorNote({ error }: { error: string | null | undefined }) {
  if (!error) return null;
  return <div className="rounded-md border border-bad/30 bg-bad/10 px-3 py-2 text-sm text-bad">{error}</div>;
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="rounded border border-line-2 bg-panel-2 px-1.5 py-0.5 font-mono text-[10px] text-ink-2">{children}</kbd>;
}
