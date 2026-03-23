"use client";
import { useEffect, useState } from "react";
import { TrendingUp, TrendingDown, Minus, RefreshCw } from "lucide-react";
import { cn } from "@/lib/utils";

interface MacroData {
  us_10y_yield: number | string;
  dxy_index:    number | string;
  brent_crude:  number | string;
  fetched_at:   string;
  source_flags: Record<string, string>;
}

interface NewsData {
  market_mood:   string;
  headline_count?: number;
}

const DATA_UNAVAILABLE = "DATA_UNAVAILABLE";

function MacroItem({
  label, value, unit, warnThreshold, isHigh,
}: {
  label: string; value: number | string; unit?: string;
  warnThreshold?: number; isHigh?: boolean;
}) {
  const unavail = value === DATA_UNAVAILABLE || value === null || value === undefined;
  const numVal  = typeof value === "number" ? value : null;
  const warn    = warnThreshold != null && numVal != null && (isHigh ? numVal >= warnThreshold : numVal <= warnThreshold);

  return (
    <span className={cn(
      "inline-flex items-center gap-1.5 px-4 py-1 text-xs whitespace-nowrap",
      warn ? "text-bear" : "text-foreground/80"
    )}>
      <span className="text-muted-foreground uppercase tracking-widest text-[9px]">{label}</span>
      {unavail ? (
        <span className="text-muted-foreground/50 text-[10px] italic">N/A</span>
      ) : (
        <span className={cn("font-mono font-semibold", warn && "text-bear")}>
          {typeof numVal === "number" ? numVal.toFixed(2) : String(value)}{unit}
        </span>
      )}
      {warn && <span className="text-bear text-[9px]">▲</span>}
    </span>
  );
}

function MoodBadge({ mood }: { mood: string }) {
  if (!mood || mood === DATA_UNAVAILABLE) return null;
  const color = mood === "BULLISH" ? "text-bull" : mood === "BEARISH" ? "text-bear" : "text-gold";
  const Icon  = mood === "BULLISH" ? TrendingUp : mood === "BEARISH" ? TrendingDown : Minus;
  return (
    <span className={cn("inline-flex items-center gap-1 px-4 py-1 text-xs font-semibold", color)}>
      <Icon size={10} />MC {mood}
    </span>
  );
}

const SEPARATOR = <span className="text-muted-foreground/30 px-2">|</span>;

export default function IntelligenceMarquee() {
  const [macro,    setMacro]    = useState<MacroData | null>(null);
  const [news,     setNews]     = useState<NewsData  | null>(null);
  const [loading,  setLoading]  = useState(true);
  const [lastFetch, setLastFetch] = useState<Date | null>(null);

  const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

  async function fetchData() {
    const token = typeof window !== "undefined" ? JSON.parse(localStorage.getItem("trading-auth") || "{}").state?.token ?? null : null;
    if (!token) return;
    const headers = { Authorization: `Bearer ${token}` };
    try {
      const [macroRes, newsRes] = await Promise.allSettled([
        fetch(`${API}/api/market/macro`, { headers }),
        fetch(`${API}/api/market/news-context`, { headers }),
      ]);
      if (macroRes.status === "fulfilled" && macroRes.value.ok) {
        setMacro(await macroRes.value.json());
      }
      if (newsRes.status === "fulfilled" && newsRes.value.ok) {
        setNews(await newsRes.value.json());
      }
      setLastFetch(new Date());
    } catch (e) {
      // graceful — marquee stays with stale data
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    fetchData();
    const id = setInterval(fetchData, 5 * 60 * 1000); // refresh every 5 min
    return () => clearInterval(id);
  }, []);

  if (loading) {
    return (
      <div className="w-full h-7 bg-muted/30 border-b border-border flex items-center px-4">
        <span className="text-[10px] text-muted-foreground animate-pulse">
          Loading market intelligence…
        </span>
      </div>
    );
  }

  const items = (
    <>
      <span className="text-[9px] uppercase tracking-widest text-primary/70 px-3 font-bold">
        LIVE MACRO
      </span>
      {SEPARATOR}
      <MacroItem
        label="US 10Y" value={macro?.us_10y_yield ?? DATA_UNAVAILABLE}
        unit="%" warnThreshold={5.0} isHigh
      />
      {SEPARATOR}
      <MacroItem
        label="DXY" value={macro?.dxy_index ?? DATA_UNAVAILABLE}
        warnThreshold={106} isHigh
      />
      {SEPARATOR}
      <MacroItem
        label="Brent" value={macro?.brent_crude ?? DATA_UNAVAILABLE}
        unit=" USD" warnThreshold={95} isHigh
      />
      {SEPARATOR}
      <MoodBadge mood={news?.market_mood ?? DATA_UNAVAILABLE} />
      {news?.market_mood === "BEARISH" && (
        <>
          {SEPARATOR}
          <span className="text-bear text-[10px] font-semibold px-2">
            ⚠ MC BEARISH — BUY signals may be downgraded
          </span>
        </>
      )}
      {lastFetch && (
        <>
          {SEPARATOR}
          <span className="text-[9px] text-muted-foreground/50 px-3 flex items-center gap-1">
            <RefreshCw size={8} />
            {lastFetch.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" })}
          </span>
        </>
      )}
    </>
  );

  // Duplicate for seamless scroll
  return (
    <div className="w-full h-7 bg-card/60 border-b border-border overflow-hidden flex items-center">
      <div className="animate-ticker-scroll flex items-center whitespace-nowrap" style={{ width: "max-content" }}>
        {items}
        {/* Duplicate for seamless loop */}
        {items}
      </div>
    </div>
  );
}
