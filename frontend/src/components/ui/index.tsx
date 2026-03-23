"use client";
import { cn } from "@/lib/utils";

// ── Card ─────────────────────────────────────────────────────────────────────
export function Card({ className, children, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn("bg-card border border-border rounded-2xl", className)} {...props}>
      {children}
    </div>
  );
}

export function CardHeader({ className, children, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn("px-5 pt-4 pb-3 border-b border-border/60", className)} {...props}>
      {children}
    </div>
  );
}

export function CardContent({ className, children, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn("px-5 py-4", className)} {...props}>
      {children}
    </div>
  );
}

// ── Badge ─────────────────────────────────────────────────────────────────────
interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  variant?: "bull" | "bear" | "gold" | "neutral" | "cyan";
  size?: "sm" | "md";
}
export function Badge({
  className,
  variant = "neutral",
  size = "sm",
  children,
  ...props
}: BadgeProps) {
  const variants = {
    bull:    "bg-bull/10 text-bull border-bull/20",
    bear:    "bg-bear/10 text-bear border-bear/20",
    gold:    "bg-gold/10 text-gold border-gold/20",
    neutral: "bg-muted text-muted-foreground border-border",
    cyan:    "bg-cyan/10 text-cyan border-cyan/20",
  };
  const sizes = { sm: "text-[10px] px-2 py-0.5", md: "text-xs px-2.5 py-1" };
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 border rounded-full font-semibold",
        variants[variant],
        sizes[size],
        className
      )}
      {...props}
    >
      {children}
    </span>
  );
}

// ── Skeleton ──────────────────────────────────────────────────────────────────
export function Skeleton({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn("animate-pulse rounded-lg bg-muted/60", className)} {...props} />
  );
}

// ── StatCard ──────────────────────────────────────────────────────────────────
interface StatCardProps {
  label: string;
  value: string | React.ReactNode;
  sub?: string;
  trend?: "up" | "down" | "neutral";
  icon?: React.ReactNode;
  className?: string;
  glow?: boolean;
}
export function StatCard({ label, value, sub, trend, icon, className, glow }: StatCardProps) {
  const glowClass = glow
    ? trend === "up"   ? "glow-bull"
    : trend === "down" ? "glow-bear"
    : ""
    : "";
  return (
    <div className={cn("bg-card border border-border rounded-2xl px-5 py-4 flex gap-4", glowClass, className)}>
      {icon && (
        <div className="w-9 h-9 rounded-xl bg-muted/60 border border-border flex items-center justify-center shrink-0 text-muted-foreground">
          {icon}
        </div>
      )}
      <div className="min-w-0">
        <p className="text-[10px] uppercase tracking-widest text-muted-foreground font-medium mb-1">
          {label}
        </p>
        <p className={cn(
          "text-lg font-bold font-display leading-none",
          trend === "up"   ? "text-bull"
          : trend === "down" ? "text-bear"
          : "text-foreground"
        )}>
          {value}
        </p>
        {sub && <p className="text-[10px] text-muted-foreground mt-1">{sub}</p>}
      </div>
    </div>
  );
}

// ── ConfidenceBar ─────────────────────────────────────────────────────────────
export function ConfidenceBar({ value, className }: { value: number; className?: string }) {
  const color = value >= 75 ? "bg-bull" : value >= 50 ? "bg-gold" : "bg-bear";
  return (
    <div className={cn("flex items-center gap-2", className)}>
      <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden">
        <div
          className={cn("h-full rounded-full transition-all", color)}
          style={{ width: `${value}%` }}
        />
      </div>
      <span className="text-[10px] font-mono text-muted-foreground w-8 text-right">
        {value.toFixed(0)}%
      </span>
    </div>
  );
}

// ── Divider ───────────────────────────────────────────────────────────────────
export function Divider({ className }: { className?: string }) {
  return <div className={cn("h-px bg-border", className)} />;
}

// ── Empty state ───────────────────────────────────────────────────────────────
export function Empty({
  icon,
  title,
  description,
}: {
  icon?: React.ReactNode;
  title: string;
  description?: string;
}) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center gap-3">
      {icon && <div className="text-muted-foreground/40 mb-1">{icon}</div>}
      <p className="text-sm font-semibold text-muted-foreground">{title}</p>
      {description && (
        <p className="text-xs text-muted-foreground/60 max-w-xs">{description}</p>
      )}
    </div>
  );
}

// ── Tabs ──────────────────────────────────────────────────────────────────────
interface TabsProps {
  tabs: { key: string; label: string; icon?: React.ReactNode }[];
  active: string;
  onChange: (k: string) => void;
  className?: string;
}
export function Tabs({ tabs, active, onChange, className }: TabsProps) {
  return (
    <div className={cn("flex gap-1 bg-muted/40 p-1 rounded-xl border border-border", className)}>
      {tabs.map(({ key, label, icon }) => (
        <button
          key={key}
          onClick={() => onChange(key)}
          className={cn(
            "flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all",
            active === key
              ? "bg-card border border-border text-foreground shadow-sm"
              : "text-muted-foreground hover:text-foreground"
          )}
        >
          {icon}
          {label}
        </button>
      ))}
    </div>
  );
}

// ── Re-export feedback primitives so all pages import from one place ──────────
export {
  Spinner,
  LoadingOverlay,
  ErrorBanner,
  StatusBar,
  ActionButton,
} from "@/components/ui/feedback";
