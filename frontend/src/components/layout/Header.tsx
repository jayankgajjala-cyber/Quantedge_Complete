"use client";
import { useState, useCallback, useRef, useEffect } from "react";
import { Search, Bell, RefreshCw, X, Loader2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { cn, regimeBadge, timeAgo } from "@/lib/utils";
import { useLatestRegime } from "@/hooks/useData";
import { triggerScanNow } from "@/hooks/useData";
import { toast } from "sonner";

const NIFTY_POPULAR = [
  "RELIANCE","TCS","HDFCBANK","INFY","ICICIBANK",
  "SBIN","WIPRO","AXISBANK","KOTAKBANK","LT",
  "BAJFINANCE","MARUTI","TATAMOTORS","HCLTECH","SUNPHARMA",
];

export default function Header() {
  const router = useRouter();
  const [query, setQuery]         = useState("");
  const [open, setOpen]           = useState(false);
  const [scanning, setScanning]   = useState(false);
  const inputRef                  = useRef<HTMLInputElement>(null);
  const { data: regime }          = useLatestRegime();

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
      toast.success(`Scan complete — ${res.signals_count} signals`);
    } catch { toast.error("Scan failed"); }
    finally { setScanning(false); }
  }

  // Close on outside click
  useEffect(() => {
    const h = (e: MouseEvent) => {
      if (!(e.target as HTMLElement).closest("[data-search]")) setOpen(false);
    };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, []);

  const badge = regime ? regimeBadge(regime.regime_label) : null;

  return (
    <header className="h-14 border-b border-border bg-card/80 backdrop-blur-sm flex items-center px-5 gap-4 shrink-0">
      {/* Quick Search */}
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
          <button onClick={() => { setQuery(""); setOpen(false); }}
            className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground">
            <X size={12} />
          </button>
        )}

        {/* Dropdown */}
        {open && (
          <div className="absolute top-full mt-2 left-0 w-full bg-card border border-border rounded-xl shadow-xl overflow-hidden z-50">
            <div className="px-3 py-2 border-b border-border">
              <span className="text-[10px] text-muted-foreground uppercase tracking-widest">
                {query ? "Search results" : "Popular Tickers"}
              </span>
            </div>
            <div className="max-h-56 overflow-y-auto">
              {(query ? filtered : NIFTY_POPULAR).map((ticker) => (
                <button key={ticker}
                  onClick={() => navigate(ticker)}
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

      {/* Regime badge */}
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

      {/* Scan now */}
      <button onClick={handleScan} disabled={scanning}
        className={cn(
          "flex items-center gap-1.5 px-3 py-1.5 rounded-xl border border-border text-xs text-muted-foreground hover:text-foreground hover:border-primary/30 transition-all",
          scanning && "opacity-60"
        )}>
        {scanning
          ? <Loader2 size={12} className="animate-spin" />
          : <RefreshCw size={12} />}
        <span className="hidden sm:block">{scanning ? "Scanning…" : "Scan Now"}</span>
      </button>

      {/* Notifications placeholder */}
      <button className="relative w-8 h-8 rounded-xl border border-border flex items-center justify-center text-muted-foreground hover:text-foreground hover:border-primary/30 transition-all">
        <Bell size={14} />
        <span className="absolute -top-1 -right-1 w-3.5 h-3.5 rounded-full bg-bear text-[8px] text-white flex items-center justify-center font-bold">3</span>
      </button>

      {/* Last scan time */}
      {regime && (
        <span className="hidden lg:block text-[10px] text-muted-foreground">
          {timeAgo(regime.timestamp)}
        </span>
      )}
    </header>
  );
}
