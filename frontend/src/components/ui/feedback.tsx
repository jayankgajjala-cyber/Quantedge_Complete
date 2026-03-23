/**
 * Global feedback primitives — used across every page.
 *
 * Components:
 *   Spinner          – inline animated spinner with optional label
 *   LoadingOverlay   – full-card loading state with ETA message
 *   ErrorBanner      – dismissible inline error block
 *   StatusBar        – thin progress/status strip (for long ops)
 *   ActionButton     – button that manages its own loading/disabled state
 */
"use client";
import { useState, useEffect } from "react";
import { Loader2, AlertCircle, X, CheckCircle2, Clock, RefreshCw } from "lucide-react";
import { cn } from "@/lib/utils";

// ── Spinner ───────────────────────────────────────────────────────────────────

interface SpinnerProps {
  size?: number;
  label?: string;
  className?: string;
}
export function Spinner({ size = 14, label, className }: SpinnerProps) {
  return (
    <span className={cn("inline-flex items-center gap-1.5", className)}>
      <Loader2 size={size} className="animate-spin shrink-0 text-primary" />
      {label && <span className="text-xs text-muted-foreground">{label}</span>}
    </span>
  );
}

// ── LoadingOverlay ────────────────────────────────────────────────────────────

interface LoadingOverlayProps {
  message: string;
  eta?: string;          // e.g. "~30 seconds"
  subMessage?: string;
  className?: string;
}
export function LoadingOverlay({ message, eta, subMessage, className }: LoadingOverlayProps) {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const t = setInterval(() => setElapsed((e) => e + 1), 1000);
    return () => clearInterval(t);
  }, []);

  return (
    <div className={cn(
      "flex flex-col items-center justify-center py-14 text-center gap-4",
      className
    )}>
      {/* Pulsing ring around spinner */}
      <div className="relative">
        <div className="absolute inset-0 rounded-full bg-primary/20 animate-ping" />
        <div className="relative w-12 h-12 rounded-full bg-primary/10 border border-primary/30 flex items-center justify-center">
          <Loader2 size={22} className="animate-spin text-primary" />
        </div>
      </div>

      <div className="space-y-1.5">
        <p className="text-sm font-semibold text-foreground">{message}</p>
        {eta && (
          <p className="text-xs text-muted-foreground flex items-center justify-center gap-1.5">
            <Clock size={11} />
            ETA: {eta}
            {elapsed > 0 && (
              <span className="ml-1 opacity-60">({elapsed}s elapsed)</span>
            )}
          </p>
        )}
        {subMessage && (
          <p className="text-[10px] text-muted-foreground/60 max-w-xs">{subMessage}</p>
        )}
      </div>
    </div>
  );
}

// ── ErrorBanner ───────────────────────────────────────────────────────────────

interface ErrorBannerProps {
  title?: string;
  message: string;
  onDismiss?: () => void;
  onRetry?: () => void;
  className?: string;
}
export function ErrorBanner({
  title = "Something went wrong",
  message,
  onDismiss,
  onRetry,
  className,
}: ErrorBannerProps) {
  return (
    <div className={cn(
      "bg-bear/5 border border-bear/25 rounded-xl p-4 flex items-start gap-3",
      className
    )}>
      <AlertCircle size={15} className="text-bear shrink-0 mt-0.5" />
      <div className="flex-1 min-w-0">
        <p className="text-xs font-bold text-bear">{title}</p>
        <p className="text-[11px] text-bear/80 mt-0.5 leading-relaxed break-words">{message}</p>
        {onRetry && (
          <button
            onClick={onRetry}
            className="mt-2 flex items-center gap-1 text-[10px] text-bear/70 hover:text-bear transition-colors"
          >
            <RefreshCw size={9} /> Try again
          </button>
        )}
      </div>
      {onDismiss && (
        <button
          onClick={onDismiss}
          className="text-bear/40 hover:text-bear transition-colors shrink-0"
        >
          <X size={12} />
        </button>
      )}
    </div>
  );
}

// ── StatusBar ─────────────────────────────────────────────────────────────────
// Thin strip shown under the page header during long-running operations.

interface StatusBarProps {
  message: string;
  eta?: string;
  variant?: "loading" | "success" | "error";
  onDismiss?: () => void;
}
export function StatusBar({ message, eta, variant = "loading", onDismiss }: StatusBarProps) {
  const colours = {
    loading: "bg-primary/10 border-primary/20 text-primary",
    success: "bg-bull/10  border-bull/20  text-bull",
    error:   "bg-bear/10  border-bear/20  text-bear",
  };
  const Icon =
    variant === "loading" ? Loader2 :
    variant === "success" ? CheckCircle2 : AlertCircle;

  return (
    <div className={cn(
      "flex items-center gap-2.5 px-4 py-2 border-b text-xs font-medium",
      colours[variant]
    )}>
      <Icon
        size={12}
        className={cn("shrink-0", variant === "loading" && "animate-spin")}
      />
      <span className="flex-1">{message}</span>
      {eta && (
        <span className="flex items-center gap-1 opacity-70 text-[10px]">
          <Clock size={9} /> {eta}
        </span>
      )}
      {onDismiss && (
        <button onClick={onDismiss} className="opacity-60 hover:opacity-100">
          <X size={11} />
        </button>
      )}
    </div>
  );
}

// ── ActionButton ──────────────────────────────────────────────────────────────
// Drop-in replacement for any <button> that manages spinner + disabled state.

interface ActionButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  loading?: boolean;
  loadingLabel?: string;
  icon?: React.ReactNode;
  variant?: "primary" | "secondary" | "danger" | "ghost";
  size?: "sm" | "md";
}
export function ActionButton({
  loading = false,
  loadingLabel,
  icon,
  variant = "primary",
  size = "md",
  children,
  disabled,
  className,
  ...props
}: ActionButtonProps) {
  const base =
    "inline-flex items-center justify-center gap-1.5 font-semibold rounded-xl transition-all disabled:opacity-60 disabled:cursor-not-allowed";

  const variants = {
    primary:   "bg-primary/10 border border-primary/30 text-primary hover:bg-primary/20",
    secondary: "bg-muted/50   border border-border      text-muted-foreground hover:text-foreground hover:border-primary/30",
    danger:    "bg-bear/10    border border-bear/25      text-bear  hover:bg-bear/20",
    ghost:     "text-muted-foreground hover:text-foreground",
  };
  const sizes = {
    sm: "px-3 py-1.5 text-[10px]",
    md: "px-4 py-2   text-xs",
  };

  return (
    <button
      disabled={disabled || loading}
      className={cn(base, variants[variant], sizes[size], className)}
      {...props}
    >
      {loading ? (
        <Loader2 size={size === "sm" ? 10 : 12} className="animate-spin" />
      ) : (
        icon
      )}
      {loading ? (loadingLabel ?? "Please wait…") : children}
    </button>
  );
}
