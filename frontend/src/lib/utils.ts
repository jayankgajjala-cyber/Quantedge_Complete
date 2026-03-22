import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import { format, formatDistanceToNow } from "date-fns";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function fmt(n: number | null | undefined, decimals = 2): string {
  if (n == null) return "—";
  return n.toLocaleString("en-IN", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

export function fmtPct(n: number | null | undefined): string {
  if (n == null) return "—";
  return `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
}

export function fmtCurrency(n: number | null | undefined): string {
  if (n == null) return "—";
  return `₹${Math.abs(n).toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
}

export function timeAgo(iso: string): string {
  return formatDistanceToNow(new Date(iso), { addSuffix: true });
}

export function fmtDate(iso: string): string {
  return format(new Date(iso), "dd MMM yyyy, HH:mm");
}

export function signalColor(signal: string): string {
  const s = signal.toUpperCase();
  if (s.startsWith("BUY") || s.startsWith("WATCH")) return "text-bull";
  if (s.startsWith("SELL")) return "text-bear";
  if (s.startsWith("HOLD") || s.startsWith("CAUTION")) return "text-gold";
  return "text-muted-foreground";
}

export function signalBg(signal: string): string {
  const s = signal.toUpperCase();
  if (s.startsWith("BUY") || s.startsWith("WATCH")) return "bg-bull/10 text-bull border-bull/20";
  if (s.startsWith("SELL")) return "bg-bear/10 text-bear border-bear/20";
  if (s.startsWith("HOLD") || s.startsWith("CAUTION")) return "bg-gold/10 text-gold border-gold/20";
  return "bg-muted text-muted-foreground border-border";
}

export function regimeBadge(regime: string) {
  const map: Record<string, { color: string; label: string; icon: string }> = {
    STRONG_TREND:       { color: "text-bull bg-bull/10 border-bull/20",     label: "Strong Trend",    icon: "📈" },
    SIDEWAYS:           { color: "text-gold bg-gold/10 border-gold/20",     label: "Sideways",        icon: "↔️" },
    VOLATILE_HIGH_RISK: { color: "text-bear bg-bear/10 border-bear/20",     label: "Volatile",        icon: "⚡" },
    BEAR_CRASHING:      { color: "text-bear bg-bear/10 border-bear/20",     label: "Bear/Crashing",   icon: "📉" },
    UNKNOWN:            { color: "text-muted-foreground bg-muted border-border", label: "Unknown",    icon: "❓" },
  };
  return map[regime] || map.UNKNOWN;
}

export function sentimentColor(score: number | null): string {
  if (score == null) return "text-muted-foreground";
  if (score > 0.3) return "text-bull";
  if (score < -0.3) return "text-bear";
  return "text-gold";
}

export function confidenceBar(conf: number): string {
  if (conf >= 75) return "bg-bull";
  if (conf >= 50) return "bg-gold";
  return "bg-bear";
}
