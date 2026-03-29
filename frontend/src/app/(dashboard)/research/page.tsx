"use client";
import { useState, useEffect, useRef } from "react";
import {
  Newspaper, Globe, Briefcase, BarChart3, TrendingUp, TrendingDown,
  Minus, Clock, ExternalLink, RefreshCw, Loader2, AlertTriangle,
  ChevronDown, ChevronUp, Activity,
} from "lucide-react";
import { api, getErrorMessage } from "@/lib/api";
import { useHoldings, useBacktests } from "@/hooks/useData";
import { cn, sentimentColor, timeAgo } from "@/lib/utils";
import {
  Card, CardHeader, CardContent, Badge, Empty, ErrorBanner,
} from "@/components/ui";
import type { NewsArticle, Holding } from "@/types";

// ── Constants ─────────────────────────────────────────────────────────────────

const NIFTY_50 = [
  "RELIANCE","TCS","HDFCBANK","INFY","ICICIBANK","SBIN","WIPRO","AXISBANK",
  "KOTAKBANK","LT","BAJFINANCE","MARUTI","TATAMOTORS","HCLTECH","SUNPHARMA",
  "NESTLEIND","HINDUNILVR","ASIANPAINT","BAJAJFINSV","ADANIPORTS",
  "TITAN","ULTRACEMCO","POWERGRID","NTPC","ONGC","COALINDIA","JSWSTEEL",
  "GRASIM","INDUSINDBK","HINDALCO",
];

const NIFTY_500_EXTRA = [
  "BANKBARODA","CANBK","UNIONBANK","IDFCFIRSTB","FEDERALBNK",
  "MUTHOOTFIN","CHOLAFIN","PNB","LICHSGFIN",
  "DRREDDY","CIPLA","DIVISLAB","BIOCON","AUROPHARMA",
  "TATAPOWER","ADANIGREEN","ADANIENT","ADANITRANS",
  "ZOMATO","NYKAA","PAYTM","POLICYBZR",
  "IRCTC","HAL","BEL","BHEL","CONCOR",
  "PIDILITIND","BERGEPAINT","KANSAINER",
  "VOLTAS","HAVELLS","CROMPTON","WHIRLPOOL",
  "TATACOMM","BHARTIARTL","IDEA","MTNL",
];

const GLOBAL_QUERIES = [
  "US Federal Reserve interest rates",
  "China economy slowdown impact India",
  "crude oil prices OPEC",
  "dollar index DXY emerging markets",
  "global recession risk 2025",
  "India GDP growth forecast",
  "FII DII flows Indian market",
];

const SECTOR_IMPACT_MAP: Record<string, string[]> = {
  "crude oil": ["Oil & Gas", "Paints", "Aviation", "Chemicals"],
  "interest rate": ["Banking", "NBFCs", "Real Estate", "Auto"],
  "dollar": ["IT", "Pharma", "Metals", "Oil & Gas"],
  "china": ["Metals", "Chemicals", "Electronics", "Auto"],
  "recession": ["IT", "Metals", "Auto", "Capital Goods"],
  "inflation": ["FMCG", "Banking", "Consumer Durables"],
  "fii": ["Banking", "IT", "Auto", "Financials"],
  "monsoon": ["FMCG", "Agro", "Rural Finance", "Fertilizers"],
  "gdp": ["Banking", "Infrastructure", "Capital Goods", "Auto"],
};

// ── Helpers ───────────────────────────────────────────────────────────────────

interface ArticleWithTicker extends NewsArticle {
  ticker?: string;
}

function getSectorImpact(title: string, description: string | null): string[] {
  const text = `${title} ${description ?? ""}`.toLowerCase();
  const sectors = new Set<string>();
  Object.entries(SECTOR_IMPACT_MAP).forEach(([keyword, sectorList]) => {
    if (text.includes(keyword)) sectorList.forEach((s) => sectors.add(s));
  });
  return Array.from(sectors).slice(0, 4);
}

function sentimentBadgeClass(label: string | null) {
  if (label === "POSITIVE") return "bg-bull/10 text-bull border-bull/20";
  if (label === "NEGATIVE") return "bg-bear/10 text-bear border-bear/20";
  return "bg-gold/10 text-gold border-gold/20";
}

function sentimentIcon(label: string | null, score: number | null) {
  if (label === "POSITIVE" || (score ?? 0) > 0.2) return <TrendingUp size={11} className="text-bull" />;
  if (label === "NEGATIVE" || (score ?? 0) < -0.2) return <TrendingDown size={11} className="text-bear" />;
  return <Minus size={11} className="text-gold" />;
}

function avgSentiment(articles: ArticleWithTicker[]) {
  const scored = articles.filter((a) => a.sentiment_score != null);
  if (!scored.length) return null;
  return scored.reduce((s, a) => s + (a.sentiment_score ?? 0), 0) / scored.length;
}

function SentimentMeter({ articles }: { articles: ArticleWithTicker[] }) {
  const avg = avgSentiment(articles);
  if (avg == null) return null;
  const pos = articles.filter((a) => (a.sentiment_score ?? 0) > 0.1).length;
  const neg = articles.filter((a) => (a.sentiment_score ?? 0) < -0.1).length;
  const neu = articles.length - pos - neg;
  const label = avg > 0.15 ? "Bullish" : avg < -0.15 ? "Bearish" : "Neutral";
  const color = avg > 0.15 ? "text-bull" : avg < -0.15 ? "text-bear" : "text-gold";
  return (
    <div className="flex items-center gap-4 flex-wrap">
      <div className="flex items-center gap-1.5">
        <span className="text-[10px] text-muted-foreground uppercase tracking-widest">Sentiment</span>
        <span className={cn("text-sm font-bold font-mono", color)}>
          {avg >= 0 ? "+" : ""}{avg.toFixed(3)}
        </span>
        <span className={cn("text-[10px] font-semibold", color)}>{label}</span>
      </div>
      <div className="flex items-center gap-2 text-[10px]">
        <span className="text-bull">▲ {pos} positive</span>
        <span className="text-muted-foreground">· {neu} neutral ·</span>
        <span className="text-bear">▼ {neg} negative</span>
      </div>
    </div>
  );
}

function ArticleCard({ article, showTicker, showSectors }: {
  article: ArticleWithTicker;
  showTicker?: boolean;
  showSectors?: boolean;
}) {
  const sectors = showSectors ? getSectorImpact(article.title, article.description) : [];
  return (
    <div className="bg-card border border-border/60 rounded-xl p-3.5 hover:border-primary/20 transition-all group">
      <div className="flex items-start gap-3">
        <div className="mt-0.5 shrink-0">{sentimentIcon(article.sentiment_label, article.sentiment_score)}</div>
        <div className="flex-1 min-w-0">
          <div className="flex items-start gap-2 justify-between">
            <p className="text-xs font-semibold text-foreground leading-snug line-clamp-2 group-hover:text-primary transition-colors">
              {article.title}
            </p>
            {article.url && (
              <a href={article.url} target="_blank" rel="noopener noreferrer"
                className="shrink-0 text-muted-foreground hover:text-primary transition-colors">
                <ExternalLink size={10} />
              </a>
            )}
          </div>
          {article.description && (
            <p className="text-[10px] text-muted-foreground mt-1 line-clamp-2">{article.description}</p>
          )}
          <div className="flex items-center gap-2 mt-2 flex-wrap">
            {showTicker && article.ticker && (
              <span className="text-[9px] font-bold text-primary bg-primary/10 px-1.5 py-0.5 rounded">
                {article.ticker}
              </span>
            )}
            <span className="text-[9px] text-muted-foreground uppercase tracking-wide">
              {article.source_name?.replace("_", " ")}
            </span>
            <span className="text-[9px] text-muted-foreground flex items-center gap-1">
              <Clock size={8} />{timeAgo(article.published_at)}
            </span>
            {article.sentiment_label && (
              <span className={cn("text-[9px] font-semibold border px-1.5 py-0.5 rounded", sentimentBadgeClass(article.sentiment_label))}>
                {article.sentiment_label}
              </span>
            )}
            {sectors.length > 0 && (
              <div className="flex gap-1 flex-wrap ml-1">
                {sectors.map((s) => (
                  <span key={s} className="text-[9px] bg-muted/60 text-muted-foreground px-1.5 py-0.5 rounded border border-border/40">
                    {s}
                  </span>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function SectionHeader({
  icon, title, subtitle, count, loading, onRefresh, expanded, onToggle,
}: {
  icon: React.ReactNode; title: string; subtitle: string;
  count: number; loading?: boolean;
  onRefresh: () => void; expanded: boolean; onToggle: () => void;
}) {
  return (
    <div className="flex items-center gap-3 cursor-pointer" onClick={onToggle}>
      <div className="w-8 h-8 rounded-xl bg-primary/10 border border-primary/20 flex items-center justify-center shrink-0">
        {icon}
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <h2 className="font-display font-bold text-sm">{title}</h2>
          <Badge variant="neutral">{count} articles</Badge>
          {loading && <Loader2 size={11} className="animate-spin text-muted-foreground" />}
        </div>
        <p className="text-[10px] text-muted-foreground mt-0.5">{subtitle}</p>
      </div>
      <button
        onClick={(e) => { e.stopPropagation(); onRefresh(); }}
        className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-all"
      >
        <RefreshCw size={11} />
      </button>
      {expanded ? <ChevronUp size={14} className="text-muted-foreground shrink-0" /> : <ChevronDown size={14} className="text-muted-foreground shrink-0" />}
    </div>
  );
}

// ── Backtest-driven recommendation ───────────────────────────────────────────

function useBacktestReco(ticker: string, sentimentAvg: number | null) {
  const { data: backtests } = useBacktests(ticker);
  if (!backtests?.length) return null;
  const best = [...backtests].sort((a, b) => (b.sharpe_ratio ?? -99) - (a.sharpe_ratio ?? -99))[0];
  const sharpe = best?.sharpe_ratio ?? 0;
  const cagr   = best?.cagr ?? 0;
  const sent   = sentimentAvg ?? 0;

  if (sharpe >= 1.0 && sent > 0.1 && cagr > 5)  return { action: "BUY",        color: "text-bull",           reason: `Sharpe ${sharpe.toFixed(2)} + positive sentiment` };
  if (sharpe >= 0.5 && sent >= -0.1)             return { action: "ACCUMULATE", color: "text-emerald-400",    reason: `Moderate Sharpe ${sharpe.toFixed(2)}, neutral-positive news` };
  if (sharpe >= 0.5 && sent < -0.1)              return { action: "HOLD",       color: "text-gold",           reason: `Good strategy but negative news sentiment` };
  if (sharpe < 0.5  && sent < -0.1)              return { action: "SELL",       color: "text-bear",           reason: `Weak Sharpe ${sharpe.toFixed(2)} + negative sentiment` };
  return { action: "HOLD", color: "text-gold", reason: `Insufficient signal strength` };
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function NewsResearchPage() {
  const { data: holdings } = useHoldings();
  const holdingTickers = (holdings ?? []).map((h: Holding) => h.symbol);

  // Section expand state
  const [secA, setSecA] = useState(true);
  const [secB, setSecB] = useState(true);
  const [secC, setSecC] = useState(true);

  // Articles state
  const [indexArticles,   setIndexArticles]   = useState<ArticleWithTicker[]>([]);
  const [holdingArticles, setHoldingArticles] = useState<ArticleWithTicker[]>([]);
  const [globalArticles,  setGlobalArticles]  = useState<ArticleWithTicker[]>([]);

  const [loadA, setLoadA] = useState(false);
  const [loadB, setLoadB] = useState(false);
  const [loadC, setLoadC] = useState(false);
  const [errA,  setErrA]  = useState("");
  const [errB,  setErrB]  = useState("");
  const [errC,  setErrC]  = useState("");

  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);

  // ── Fetch helpers ──────────────────────────────────────────────────────────

  async function fetchArticlesForTickers(tickers: string[]): Promise<ArticleWithTicker[]> {
    const results: ArticleWithTicker[] = [];
    // Fetch in batches of 5 to avoid hammering backend
    const batch = tickers.slice(0, 15);
    await Promise.allSettled(
      batch.map(async (ticker) => {
        try {
          const { data } = await api.get(`/dashboard/research/${ticker}/articles?limit=5`);
          if (Array.isArray(data)) {
            data.forEach((a: NewsArticle) => results.push({ ...a, ticker }));
          }
        } catch {}
      })
    );
    return results.sort((a, b) =>
      new Date(b.published_at).getTime() - new Date(a.published_at).getTime()
    );
  }

  async function fetchGlobalNews(): Promise<ArticleWithTicker[]> {
    // Use a broad market ticker to get macro news
    const macroTickers = ["NIFTY50", "SENSEX", "USDINR"];
    const results: ArticleWithTicker[] = [];
    await Promise.allSettled(
      macroTickers.map(async (t) => {
        try {
          const { data } = await api.get(`/dashboard/research/${t}/articles?limit=8`);
          if (Array.isArray(data)) data.forEach((a: NewsArticle) => results.push({ ...a, ticker: t }));
        } catch {}
      })
    );
    // Also try fetching from regime/macro endpoint
    try {
      const { data } = await api.get("/dashboard/research/GOLD/articles?limit=5");
      if (Array.isArray(data)) data.forEach((a: NewsArticle) => globalArticles.push({ ...a, ticker: "MACRO" }));
    } catch {}
    return results.sort((a, b) =>
      new Date(b.published_at).getTime() - new Date(a.published_at).getTime()
    );
  }

  async function loadSectionA() {
    setLoadA(true); setErrA("");
    try {
      const tickers = [...new Set([...NIFTY_50, ...NIFTY_500_EXTRA])].slice(0, 20);
      const articles = await fetchArticlesForTickers(tickers);
      setIndexArticles(articles);
    } catch (e: any) { setErrA(getErrorMessage(e)); }
    finally { setLoadA(false); }
  }

  async function loadSectionB() {
    if (!holdingTickers.length) { setHoldingArticles([]); return; }
    setLoadB(true); setErrB("");
    try {
      const articles = await fetchArticlesForTickers(holdingTickers);
      setHoldingArticles(articles);
    } catch (e: any) { setErrB(getErrorMessage(e)); }
    finally { setLoadB(false); }
  }

  async function loadSectionC() {
    setLoadC(true); setErrC("");
    try {
      const articles = await fetchGlobalNews();
      setGlobalArticles(articles);
    } catch (e: any) { setErrC(getErrorMessage(e)); }
    finally { setLoadC(false); }
  }

  async function refreshAll() {
    await Promise.all([loadSectionA(), loadSectionB(), loadSectionC()]);
    setLastRefresh(new Date());
  }

  // Load on mount + when holdings change
  useEffect(() => {
    refreshAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [holdingTickers.join(",")]);

  // ── Holding recommendations panel ─────────────────────────────────────────

  function HoldingRow({ holding }: { holding: Holding }) {
    const myArticles = holdingArticles.filter((a) => a.ticker === holding.symbol);
    const avg = avgSentiment(myArticles);
    const reco = useBacktestReco(holding.symbol, avg);
    return (
      <div className="flex items-center gap-3 px-4 py-3 border-b border-border/40 last:border-0 hover:bg-muted/20 transition-colors">
        <div className="w-8 h-8 rounded-lg bg-primary/10 border border-primary/20 flex items-center justify-center shrink-0">
          <span className="text-primary text-[9px] font-bold">{holding.symbol.slice(0,2)}</span>
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-xs font-bold">{holding.symbol}</div>
          <div className="text-[10px] text-muted-foreground">{myArticles.length} articles today</div>
        </div>
        {avg != null && (
          <span className={cn("text-[10px] font-mono font-bold", avg > 0.1 ? "text-bull" : avg < -0.1 ? "text-bear" : "text-gold")}>
            {avg >= 0 ? "+" : ""}{avg.toFixed(3)}
          </span>
        )}
        {reco && (
          <span className={cn("text-[10px] font-bold border px-2 py-0.5 rounded-full",
            reco.action === "BUY"        ? "text-bull bg-bull/10 border-bull/20" :
            reco.action === "ACCUMULATE" ? "text-emerald-400 bg-emerald-400/10 border-emerald-400/20" :
            reco.action === "SELL"       ? "text-bear bg-bear/10 border-bear/20" :
            "text-gold bg-gold/10 border-gold/20"
          )}>
            {reco.action}
          </span>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-6 animate-fade-in">

      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="font-display font-bold text-xl">News Research</h1>
          <p className="text-muted-foreground text-xs mt-0.5">
            Daily market intelligence · Auto-refreshes every morning · Sentiment scored by FinBERT
          </p>
        </div>
        <div className="flex items-center gap-3">
          {lastRefresh && (
            <span className="text-[10px] text-muted-foreground">
              Updated {timeAgo(lastRefresh.toISOString())}
            </span>
          )}
          <button
            onClick={refreshAll}
            disabled={loadA || loadB || loadC}
            className="flex items-center gap-1.5 px-3 py-2 rounded-xl border border-border text-xs text-muted-foreground hover:text-foreground hover:border-primary/30 transition-all disabled:opacity-50"
          >
            {(loadA || loadB || loadC) ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
            Refresh All
          </button>
        </div>
      </div>

      {/* ── Section A: Nifty 50 + 500 ─────────────────────────────────────── */}
      <div className="bg-card border border-border rounded-2xl overflow-hidden">
        <div className="px-5 py-4 border-b border-border/60">
          <SectionHeader
            icon={<BarChart3 size={14} className="text-primary" />}
            title="Nifty 50 & 500 — Index News"
            subtitle="Macro & micro sentiment across benchmark stocks"
            count={indexArticles.length}
            loading={loadA}
            onRefresh={loadSectionA}
            expanded={secA}
            onToggle={() => setSecA(!secA)}
          />
          {secA && indexArticles.length > 0 && (
            <div className="mt-3">
              <SentimentMeter articles={indexArticles} />
            </div>
          )}
        </div>

        {secA && (
          <div className="p-4">
            {errA && <ErrorBanner title="Failed to load index news" message={errA} onRetry={loadSectionA} />}
            {loadA ? (
              <div className="flex items-center gap-2 py-8 justify-center text-xs text-muted-foreground">
                <Loader2 size={14} className="animate-spin" />
                Fetching news for Nifty 50 & 500 stocks…
              </div>
            ) : indexArticles.length === 0 ? (
              <Empty icon={<Newspaper size={24} />} title="No articles loaded" description="Click refresh to fetch today's news" />
            ) : (
              <div className="space-y-2">
                {indexArticles.slice(0, 30).map((a, i) => (
                  <ArticleCard key={i} article={a} showTicker />
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* ── Section B: Holdings ───────────────────────────────────────────── */}
      <div className="bg-card border border-border rounded-2xl overflow-hidden">
        <div className="px-5 py-4 border-b border-border/60">
          <SectionHeader
            icon={<Briefcase size={14} className="text-primary" />}
            title="My Holdings — Portfolio News"
            subtitle="News & sentiment for stocks you own · Backtest-driven recommendations"
            count={holdingArticles.length}
            loading={loadB}
            onRefresh={loadSectionB}
            expanded={secB}
            onToggle={() => setSecB(!secB)}
          />
          {secB && holdingArticles.length > 0 && (
            <div className="mt-3">
              <SentimentMeter articles={holdingArticles} />
            </div>
          )}
        </div>

        {secB && (
          <div>
            {errB && (
              <div className="p-4">
                <ErrorBanner title="Failed to load holdings news" message={errB} onRetry={loadSectionB} />
              </div>
            )}

            {!holdingTickers.length ? (
              <div className="p-6 text-center">
                <Empty icon={<Briefcase size={24} />} title="No holdings uploaded" description="Upload your Zerodha CSV in Portfolio to see holding-specific news here" />
              </div>
            ) : loadB ? (
              <div className="flex items-center gap-2 py-8 justify-center text-xs text-muted-foreground">
                <Loader2 size={14} className="animate-spin" />
                Fetching news for your {holdingTickers.length} holdings…
              </div>
            ) : (
              <div className="grid grid-cols-1 xl:grid-cols-[280px_1fr] divide-y xl:divide-y-0 xl:divide-x divide-border/40">

                {/* Holdings recommendation panel */}
                <div>
                  <div className="px-4 py-2.5 border-b border-border/40 bg-muted/20">
                    <p className="text-[10px] uppercase tracking-widest text-muted-foreground font-semibold">
                      Recommendation (Sentiment + Backtest)
                    </p>
                  </div>
                  <div className="divide-y divide-border/40 max-h-[500px] overflow-y-auto">
                    {(holdings ?? []).map((h: Holding) => (
                      <HoldingRow key={h.symbol} holding={h} />
                    ))}
                  </div>
                </div>

                {/* Articles */}
                <div className="p-4">
                  {holdingArticles.length === 0 ? (
                    <Empty icon={<Newspaper size={24} />} title="No articles yet" description="Articles load from the news database. Run a Force Scan or wait for the scheduler." />
                  ) : (
                    <div className="space-y-2">
                      {holdingArticles.slice(0, 40).map((a, i) => (
                        <ArticleCard key={i} article={a} showTicker />
                      ))}
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* ── Section C: Global News ────────────────────────────────────────── */}
      <div className="bg-card border border-border rounded-2xl overflow-hidden">
        <div className="px-5 py-4 border-b border-border/60">
          <SectionHeader
            icon={<Globe size={14} className="text-primary" />}
            title="Global News & Macro"
            subtitle="International events, macro indicators, and Indian market sector impact"
            count={globalArticles.length}
            loading={loadC}
            onRefresh={loadSectionC}
            expanded={secC}
            onToggle={() => setSecC(!secC)}
          />
          {secC && globalArticles.length > 0 && (
            <div className="mt-3">
              <SentimentMeter articles={globalArticles} />
            </div>
          )}
        </div>

        {secC && (
          <div className="p-4">
            {errC && <ErrorBanner title="Failed to load global news" message={errC} onRetry={loadSectionC} />}
            {loadC ? (
              <div className="flex items-center gap-2 py-8 justify-center text-xs text-muted-foreground">
                <Loader2 size={14} className="animate-spin" />
                Fetching global market news…
              </div>
            ) : globalArticles.length === 0 ? (
              <div className="space-y-4">
                <Empty icon={<Globe size={24} />} title="No global articles loaded" description="Click refresh to fetch macro and global news" />
                <div className="bg-muted/20 border border-border/40 rounded-xl p-4">
                  <p className="text-[10px] uppercase tracking-widest text-muted-foreground font-semibold mb-3">
                    Tracked Global Themes
                  </p>
                  <div className="flex flex-wrap gap-2">
                    {GLOBAL_QUERIES.map((q) => (
                      <span key={q} className="text-[10px] bg-muted/60 border border-border/40 text-muted-foreground px-2 py-1 rounded-lg">
                        {q}
                      </span>
                    ))}
                  </div>
                </div>
              </div>
            ) : (
              <div className="space-y-2">
                {globalArticles.slice(0, 25).map((a, i) => (
                  <ArticleCard key={i} article={a} showSectors />
                ))}
              </div>
            )}
          </div>
        )}
      </div>

    </div>
  );
}
