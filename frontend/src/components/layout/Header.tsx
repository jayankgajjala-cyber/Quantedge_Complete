"use client";
import { useState, useCallback, useRef, useEffect } from "react";
import { Search, Bell, RefreshCw, X, Loader2, CheckCircle2, AlertCircle, TrendingUp, Zap } from "lucide-react";
import { useRouter } from "next/navigation";
import { cn, regimeBadge, timeAgo } from "@/lib/utils";
import { useLatestRegime, useNotifications, triggerScanNow } from "@/hooks/useData";
import { toast } from "sonner";
import type { Notification } from "@/types";

const NIFTY_POPULAR = [
  "RELIANCE","TCS","HDFCBANK","INFY","ICICIBANK",
  "SBIN","WIPRO","AXISBANK","KOTAKBANK","LT",
  "BAJFINANCE","MARUTI","TATAMOTORS","HCLTECH","SUNPHARMA",
];

// Derive a display type from signal_type string coming from alert_dispatch_log
function notifMeta(n: Notification): {
  icon: React.ElementType;
  color: string;
  text: string;
} {
  const sig = (n.signal_type || "").toUpperCase();
  if (sig === "BUY" || sig === "SELL") {
    return {
      icon:  TrendingUp,
      color: sig === "BUY" ? "text-bull" : "text-bear",
      text:  n.subject || `${n.ticker} ${sig} — ${n.confidence ? n.confidence.toFixed(0) + "%" : ""}`,
    };
  }
  if (sig === "SL_HIT" || sig === "TARGET_HIT") {
    return {
      icon:  AlertCircle,
      color: sig === "SL_HIT" ? "text-bear" : "text-bull",
      text:  n.subject || `${n.ticker} ${sig.replace("_", " ")}`,
    };
  }
  return {
    icon:  CheckCircle2,
    color: "text-primary",
    text:  n.subject || `${n.ticker} ${n.signal_type}`,
  };
}

export default function Header() {
  const router = useRouter();
  const [query,      setQuery]      = useState("");
  const [open,       setOpen]       = useState(false);
  const [scanning,   setScanning]   = useState(false);
  const [notifOpen,  setNotifOpen]  = useState(false);
  const [readIds,    setReadIds]    = useState<Set<number>>(new Set());
  const inputRef = useRef<HTMLInputElement>(null);
  const notifRef = useRef<HTMLDivElement>(null);

  const { data: regime }        = useLatestRegime();
  const { data: notifications } = useNotifications();

  const unreadCount = (notifications ?? []).filter((n) => !readIds.has(n.id)).length;

  const filtered = NIFTY_POPULAR.filter((t) =>
    t.toLowerCase().includes(query.toLowerCase())
  );

  const navigate = useCallback((ticker: string) => {
    setQuery("");
    setOpen(false);
    router.push(`/research?ticker=${ticker.toUpperCase()}`);
  }, [router]);

  async function handleScan() {
    setScanning(true);
    try {
      const res = await triggerScanNow();
      toast.success(`Scan complete — ${res.signals_count} signals`, {
        description: res.regime_label ? `Regime: ${res.regime_label}` : undefined,
      });
    } catch {
      toast.error("Scan failed — check backend logs");
    } finally {
      setScanning(false);
    }
  }

  function markAllRead() {
    setReadIds(new Set((notifications ?? []).map((n) => n.id)));
  }

  // Close dropdowns on outside click
  useEffect(() => {
    const h = (e: MouseEvent) => {
      if (!(e.target as HTMLElement).closest("[data-search]")) setOpen(false);
      if (!(e.target as HTMLElement).closest("[data-notif]"))  setNotifOpen(false);
    };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, []);

  const badge = regime ? regimeBadge(regime.regime_label) : null;

  return (
    <header className="h-14 border-b border-border bg-card/80 backdrop-blur-sm flex items-center px-5 gap-4 shrink-0">

      {/* ── Quick Search ─────────────────────────────────────────────────── */}
      <div className="relative flex-1 max-w-xs" data-search>
        <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
        <input
          ref={inputRef}
          value={query}
          onChange={(e) => { setQuery(e.target.value); setOpen(true); }}
          onFocus={() => setOpen(true)}
          placeholder="Search Nifty 500 ticker…"
          className="w-full bg-muted/50 border border-border rounded-xl pl-8 pr-8 py-2 text-xs focus:outline-none focus:ring-1 focus:ring-primary/40 focus:border-primary/30 transition-all placeholder:text-muted-foreground"
        />
        {query && (
          <button
            onClick={() => { setQuery(""); setOpen(false); }}
            className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground">
            <X size={12} />
          </button>
        )}
        {open && (
          <div className="absolute top-full mt-2 left-0 w-full bg-card border border-border rounded-xl shadow-xl overflow-hidden z-50">
            <div className="px-3 py-2 border-b border-border">
              <span className="text-[10px] text-muted-foreground uppercase tracking-widest">
                {query ? "Search results" : "Popular Tickers"}
              </span>
            </div>
            <div className="max-h-56 overflow-y-auto">
              {(query ? filtered : NIFTY_POPULAR).map((ticker) => (
                <button key={ticker} onClick={() => navigate(ticker)}
                  className="w-full flex items-center gap-3 px-3 py-2.5 hover:bg-muted/50 transition-colors text-left">
                  <div className="w-7 h-7 rounded-lg bg-primary/10 border border-primary/20 flex items-center justify-center">
                    <span className="text-primary text-[9px] font-bold">{ticker.slice(0, 2)}</span>
                  </div>
                  <div>
                    <div className="text-xs font-semibold">{ticker}</div>
                    <div className="text-[10px] text-muted-foreground">NSE</div>
                  </div>
                </button>
              ))}
              {query && filtered.length === 0 && (
                <button onClick={() => navigate(query.toUpperCase())}
                  className="w-full flex items-center gap-2 px-3 py-2.5 hover:bg-muted/50 transition-colors text-xs text-muted-foreground">
                  <Search size={12} />
                  Analyse "{query.toUpperCase()}"
                </button>
              )}
            </div>
          </div>
        )}
      </div>

      {/* ── Regime badge ─────────────────────────────────────────────────── */}
      {badge && regime && (
        <div className={cn(
          "hidden md:flex items-center gap-1.5 px-3 py-1.5 rounded-xl border text-xs font-medium",
          badge.color
        )}>
          <span>{badge.icon}</span>
          <span>{badge.label}</span>
          {regime.confidence_score != null && (
            <span className="opacity-70">· {Math.round(regime.confidence_score * 100)}%</span>
          )}
        </div>
      )}

      <div className="flex-1" />

      {/* ── Scan now ─────────────────────────────────────────────────────── */}
      <button onClick={handleScan} disabled={scanning}
        className={cn(
          "flex items-center gap-1.5 px-3 py-1.5 rounded-xl border border-border text-xs text-muted-foreground hover:text-foreground hover:border-primary/30 transition-all",
          scanning && "opacity-60"
        )}>
        {scanning ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
        <span className="hidden sm:block">{scanning ? "Scanning…" : "Scan Now"}</span>
      </button>

      {/* ── Notifications ─────────────────────────────────────────────────── */}
      <div className="relative" data-notif ref={notifRef}>
        <button
          onClick={() => setNotifOpen((v) => !v)}
          className="relative w-8 h-8 rounded-xl border border-border flex items-center justify-center text-muted-foreground hover:text-foreground hover:border-primary/30 transition-all">
          <Bell size={14} />
          {unreadCount > 0 && (
            <span className="absolute -top-1 -right-1 w-4 h-4 rounded-full bg-bear text-[8px] text-white flex items-center justify-center font-bold">
              {unreadCount > 9 ? "9+" : unreadCount}
            </span>
          )}
        </button>

        {notifOpen && (
          <div className="absolute top-full right-0 mt-2 w-80 bg-card border border-border rounded-xl shadow-xl z-50 overflow-hidden">
            <div className="flex items-center justify-between px-3 py-2.5 border-b border-border">
              <span className="text-xs font-semibold">Notifications</span>
              {unreadCount > 0 && (
                <button onClick={markAllRead} className="text-[10px] text-primary hover:underline">
                  Mark all read
                </button>
              )}
            </div>

            <div className="max-h-72 overflow-y-auto">
              {!notifications || notifications.length === 0 ? (
                <div className="px-4 py-8 text-center">
                  <Zap size={20} className="text-muted-foreground/30 mx-auto mb-2" />
                  <p className="text-xs text-muted-foreground">No alerts yet</p>
                  <p className="text-[10px] text-muted-foreground/60 mt-1">
                    Alerts fire when signals hit ≥85% confidence
                  </p>
                </div>
              ) : (
                notifications.map((n: Notification) => {
                  const isRead = readIds.has(n.id);
                  const { icon: Icon, color, text } = notifMeta(n);
                  return (
                    <button
                      key={n.id}
                      onClick={() => setReadIds((prev) => new Set([...prev, n.id]))}
                      className={cn(
                        "w-full flex items-start gap-3 px-3 py-3 hover:bg-muted/50 transition-colors text-left border-b border-border/40 last:border-0",
                        !isRead && "bg-primary/5"
                      )}>
                      <Icon size={14} className={cn("shrink-0 mt-0.5", color)} />
                      <div className="flex-1 min-w-0">
                        <p className={cn("text-xs leading-relaxed line-clamp-2", !isRead && "font-medium")}>
                          {text}
                        </p>
                        <p className="text-[10px] text-muted-foreground mt-0.5">
                          {n.sent_at ? timeAgo(n.sent_at) : "—"}
                          {n.regime && (
                            <span className="ml-2 opacity-60">{n.regime}</span>
                          )}
                        </p>
                      </div>
                      {!isRead && (
                        <span className="w-1.5 h-1.5 rounded-full bg-primary shrink-0 mt-1.5" />
                      )}
                    </button>
                  );
                })
              )}
            </div>

            <div className="px-3 py-2 border-t border-border">
              <button
                onClick={() => { setNotifOpen(false); router.push("/signals"); }}
                className="text-[10px] text-primary hover:underline w-full text-center">
                View all signals →
              </button>
            </div>
          </div>
        )}
      </div>

      {/* ── Last scan time ────────────────────────────────────────────────── */}
      {regime && (
        <span className="hidden lg:block text-[10px] text-muted-foreground">
          {timeAgo(regime.timestamp)}
        </span>
      )}
    </header>
  );
}
