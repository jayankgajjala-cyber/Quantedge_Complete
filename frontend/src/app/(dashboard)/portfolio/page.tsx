"use client";
import { useState, useRef } from "react";
import {
  Upload, TrendingUp, TrendingDown, Briefcase,
  RefreshCw, BarChart3, Loader2, Activity, Info,
} from "lucide-react";
import { toast } from "sonner";
import { api, apiSlow, getErrorMessage } from "@/lib/api";
import { useHoldings, useBacktests } from "@/hooks/useData";
import { cn, fmt, fmtPct, fmtCurrency } from "@/lib/utils";
import {
  Card, CardHeader, CardContent, StatCard, Skeleton, Empty, Badge,
  ErrorBanner, LoadingOverlay,
} from "@/components/ui";
import useSWR from "swr";
import type { Holding, NewsArticle } from "@/types";

// ── Recommendation engine ─────────────────────────────────────────────────────

interface Reco {
  action: "BUY" | "ACCUMULATE" | "HOLD" | "SELL";
  color: string;
  bgColor: string;
  reason: string;
  confidence: number;
}

function computeReco(
  holding: Holding,
  sentimentAvg: number | null,
  backtests: any[] | undefined,
): Reco {
  const best = backtests?.length
    ? [...backtests].sort((a, b) => (b.sharpe_ratio ?? -99) - (a.sharpe_ratio ?? -99))[0]
    : null;

  const sharpe = best?.sharpe_ratio ?? 0;
  const cagr   = best?.cagr ?? 0;
  const sent   = sentimentAvg ?? 0;
  const pnlPct = holding.pnl_pct ?? 0;

  let score = 0;
  const reasons: string[] = [];

  if (sharpe >= 1.5)  { score += 3; reasons.push(`Strong backtest (Sharpe ${sharpe.toFixed(1)})`); }
  else if (sharpe >= 1.0) { score += 2; reasons.push(`Good backtest (Sharpe ${sharpe.toFixed(1)})`); }
  else if (sharpe >= 0.5) { score += 1; reasons.push(`Moderate backtest (Sharpe ${sharpe.toFixed(1)})`); }
  else if (sharpe > 0)    { score -= 1; reasons.push(`Weak backtest (Sharpe ${sharpe.toFixed(1)})`); }

  if (sent > 0.3)       { score += 2; reasons.push("Strong positive sentiment"); }
  else if (sent > 0.1)  { score += 1; reasons.push("Mild positive sentiment"); }
  else if (sent < -0.3) { score -= 2; reasons.push("Strong negative sentiment"); }
  else if (sent < -0.1) { score -= 1; reasons.push("Mild negative sentiment"); }

  if (cagr > 15)  { score += 1; reasons.push(`High CAGR ${cagr.toFixed(0)}%`); }
  if (cagr < 0)   { score -= 1; reasons.push(`Negative CAGR`); }

  if (pnlPct > 20)  { score += 1; reasons.push("Already profitable"); }
  if (pnlPct < -15) { score -= 1; reasons.push("Significant loss position"); }

  const confidence = Math.min(100, Math.max(10, 50 + score * 12));

  if (score >= 4)  return { action: "BUY",        color: "text-bull",           bgColor: "bg-bull/10 border-bull/20",           reason: reasons.slice(0,2).join(" · "), confidence };
  if (score >= 2)  return { action: "ACCUMULATE", color: "text-emerald-400",    bgColor: "bg-emerald-400/10 border-emerald-400/20", reason: reasons.slice(0,2).join(" · "), confidence };
  if (score >= 0)  return { action: "HOLD",       color: "text-gold",           bgColor: "bg-gold/10 border-gold/20",           reason: reasons.slice(0,2).join(" · ") || "Neutral signals", confidence };
  return               { action: "SELL",       color: "text-bear",           bgColor: "bg-bear/10 border-bear/20",           reason: reasons.slice(0,2).join(" · "), confidence };
}

// ── Per-holding data hooks ────────────────────────────────────────────────────

const slowFetcher = (url: string) => apiSlow.get(url).then((r) => r.data);

function useHoldingIntel(symbol: string) {
  const { data: backtests } = useBacktests(symbol);
  const { data: articles }  = useSWR<NewsArticle[]>(
    `/dashboard/research/${symbol}/articles?limit=10`,
    (url) => api.get(url).then((r) => r.data),
    { refreshInterval: 60 * 60 * 1000, revalidateOnFocus: false }
  );
  const sentimentAvg = (() => {
    if (!articles?.length) return null;
    const scored = articles.filter((a) => a.sentiment_score != null);
    if (!scored.length) return null;
    return scored.reduce((s, a) => s + (a.sentiment_score ?? 0), 0) / scored.length;
  })();
  return { backtests, sentimentAvg };
}

// ── Holding row with recommendation ──────────────────────────────────────────

function HoldingRow({ h, index }: { h: Holding; index: number }) {
  const { backtests, sentimentAvg } = useHoldingIntel(h.symbol);
  const [showDetail, setShowDetail] = useState(false);

  const reco = computeReco(h, sentimentAvg, backtests);
  const pnl    = h.pnl ?? 0;
  const pnlPct = h.pnl_pct ?? 0;
  const curVal = (h.current_price ?? h.average_price) * h.quantity;
  const bestBacktest = backtests?.length
    ? [...backtests].sort((a, b) => (b.sharpe_ratio ?? -99) - (a.sharpe_ratio ?? -99))[0]
    : null;

  return (
    <>
      <tr
        className="border-b border-border/40 hover:bg-muted/20 transition-colors cursor-pointer"
        style={{ animationDelay: `${index * 20}ms` }}
        onClick={() => setShowDetail(!showDetail)}
      >
        <td className="px-4 py-3">
          <div className="flex items-center gap-2.5">
            <div className="w-7 h-7 rounded-lg bg-primary/10 border border-primary/20 flex items-center justify-center">
              <span className="text-primary text-[9px] font-bold">{h.symbol.slice(0, 2)}</span>
            </div>
            <div>
              <div className="font-bold text-foreground text-xs">{h.symbol}</div>
              <div className="text-[9px] text-muted-foreground">{h.exchange}</div>
            </div>
          </div>
        </td>
        <td className="px-4 py-3 font-mono text-xs">{fmt(h.quantity, 0)}</td>
        <td className="px-4 py-3 font-mono text-xs">₹{fmt(h.average_price)}</td>
        <td className="px-4 py-3 font-mono text-xs font-semibold">
          {h.current_price ? `₹${fmt(h.current_price)}` : (
            <span className="text-muted-foreground/50 text-[10px]">No feed</span>
          )}
        </td>
        <td className="px-4 py-3 font-mono text-xs">₹{fmt(curVal)}</td>
        <td className={cn("px-4 py-3 font-mono text-xs font-semibold", pnl >= 0 ? "text-bull" : "text-bear")}>
          {pnl >= 0 ? "+" : ""}₹{fmt(Math.abs(pnl))}
        </td>
        <td className={cn("px-4 py-3 font-mono text-xs font-semibold", pnlPct >= 0 ? "text-bull" : "text-bear")}>
          {fmtPct(pnlPct)}
        </td>
        {/* Sentiment */}
        <td className="px-4 py-3 text-xs">
          {sentimentAvg != null ? (
            <span className={cn("font-mono font-bold",
              sentimentAvg > 0.1 ? "text-bull" : sentimentAvg < -0.1 ? "text-bear" : "text-gold"
            )}>
              {sentimentAvg >= 0 ? "+" : ""}{sentimentAvg.toFixed(2)}
            </span>
          ) : <span className="text-muted-foreground/40 text-[10px]">—</span>}
        </td>
        {/* Backtest */}
        <td className="px-4 py-3 text-xs">
          {bestBacktest ? (
            <span className={cn("font-mono font-bold",
              (bestBacktest.sharpe_ratio ?? 0) >= 1 ? "text-bull" :
              (bestBacktest.sharpe_ratio ?? 0) >= 0.5 ? "text-gold" : "text-bear"
            )}>
              {bestBacktest.sharpe_ratio?.toFixed(2) ?? "—"}
            </span>
          ) : <span className="text-muted-foreground/40 text-[10px]">—</span>}
        </td>
        {/* Recommendation */}
        <td className="px-4 py-3">
          <span className={cn("text-[10px] font-bold border px-2 py-0.5 rounded-full", reco.bgColor, reco.color)}>
            {reco.action}
          </span>
        </td>
      </tr>
      {/* Expandable detail row */}
      {showDetail && (
        <tr className="bg-muted/10 border-b border-border/40">
          <td colSpan={10} className="px-6 py-3">
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 text-xs">
              <div>
                <p className="text-[10px] uppercase tracking-widest text-muted-foreground mb-1.5">Recommendation Basis</p>
                <p className="text-foreground/80 leading-relaxed">{reco.reason || "Insufficient data"}</p>
                <div className="flex items-center gap-2 mt-2">
                  <div className="h-1.5 flex-1 bg-muted rounded-full overflow-hidden">
                    <div className={cn("h-full rounded-full", reco.action === "BUY" ? "bg-bull" : reco.action === "SELL" ? "bg-bear" : "bg-gold")}
                      style={{ width: `${reco.confidence}%` }} />
                  </div>
                  <span className="text-[10px] font-mono text-muted-foreground">{reco.confidence}% conf</span>
                </div>
              </div>
              <div>
                <p className="text-[10px] uppercase tracking-widest text-muted-foreground mb-1.5">Best Strategy</p>
                {bestBacktest ? (
                  <div className="space-y-1">
                    <p className="font-semibold">{bestBacktest.strategy_name}</p>
                    <p className="text-muted-foreground">Sharpe: <span className="font-mono">{bestBacktest.sharpe_ratio?.toFixed(2)}</span></p>
                    <p className="text-muted-foreground">CAGR: <span className="font-mono">{bestBacktest.cagr ? `${(bestBacktest.cagr * 100).toFixed(1)}%` : "—"}</span></p>
                    <p className="text-muted-foreground">Win Rate: <span className="font-mono">{bestBacktest.win_rate ? `${(bestBacktest.win_rate * 100).toFixed(0)}%` : "—"}</span></p>
                  </div>
                ) : <p className="text-muted-foreground">No backtest data — run from Backtest Results tab</p>}
              </div>
              <div>
                <p className="text-[10px] uppercase tracking-widest text-muted-foreground mb-1.5">News Sentiment</p>
                {sentimentAvg != null ? (
                  <div className="space-y-1">
                    <p className={cn("font-bold", sentimentAvg > 0.1 ? "text-bull" : sentimentAvg < -0.1 ? "text-bear" : "text-gold")}>
                      {sentimentAvg > 0.1 ? "Positive" : sentimentAvg < -0.1 ? "Negative" : "Neutral"}
                      {" "}({sentimentAvg >= 0 ? "+" : ""}{sentimentAvg.toFixed(3)})
                    </p>
                    <p className="text-muted-foreground">Based on recent news articles from News Research tab</p>
                  </div>
                ) : <p className="text-muted-foreground">No news data — visit News Research tab to load articles</p>}
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function PortfolioPage() {
  const { data: holdings, isLoading, error: holdingsError, mutate } = useHoldings();
  const [uploading,     setUploading]     = useState(false);
  const [uploadMsg,     setUploadMsg]     = useState("");
  const [uploadError,   setUploadError]   = useState("");
  const [refreshing,    setRefreshing]    = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const totalValue  = holdings?.reduce((s, h) => s + (h.current_price ?? h.average_price) * h.quantity, 0) ?? 0;
  const totalCost   = holdings?.reduce((s, h) => s + h.average_price * h.quantity, 0) ?? 0;
  const totalPnl    = totalValue - totalCost;
  const totalPnlPct = totalCost > 0 ? (totalPnl / totalCost) * 100 : 0;
  const winners     = holdings?.filter((h) => (h.pnl ?? 0) > 0).length ?? 0;
  const losers      = (holdings?.length ?? 0) - winners;

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    if (!file.name.endsWith(".csv")) {
      setUploadError("❌ Invalid file type. Please upload a .csv file.");
      return;
    }
    setUploading(true);
    setUploadError("");
    setUploadMsg("Parsing CSV and saving holdings…");
    const form = new FormData();
    form.append("file", file);
    try {
      const { data } = await apiSlow.post("/trading/portfolio/upload", form);
      setUploadMsg("");
      toast.success(`✅ Portfolio uploaded — ${data.imported} holdings imported`, {
        description: data.skipped > 0 ? `${data.skipped} rows skipped` : "All rows imported",
        duration: 5000,
      });
      mutate();
    } catch (err: any) {
      setUploadMsg("");
      const msg = getErrorMessage(err);
      setUploadError(`❌ Upload failed: ${msg}`);
      toast.error(`Upload failed: ${msg}`);
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function handleRefresh() {
    setRefreshing(true);
    try { await mutate(); toast.success("✅ Holdings refreshed"); }
    catch (err: any) { toast.error(`Refresh failed: ${getErrorMessage(err)}`); }
    finally { setRefreshing(false); }
  }

  return (
    <div className="space-y-5 animate-fade-in">

      {uploading && uploadMsg && (
        <div className="bg-primary/8 border border-primary/20 rounded-xl px-4 py-2.5 flex items-center gap-2.5 text-xs">
          <Loader2 size={12} className="animate-spin text-primary shrink-0" />
          <span className="text-primary font-medium">{uploadMsg}</span>
        </div>
      )}
      {uploadError && (
        <ErrorBanner title="Portfolio Upload Failed" message={uploadError}
          onDismiss={() => setUploadError("")} onRetry={() => fileRef.current?.click()} />
      )}
      {holdingsError && !isLoading && (
        <ErrorBanner title="Failed to load holdings" message={getErrorMessage(holdingsError)}
          onRetry={() => mutate()} />
      )}

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-display font-bold text-xl">Portfolio</h1>
          <p className="text-muted-foreground text-xs mt-0.5">
            {holdings?.length ?? 0} holdings · Recommendations from Backtest + News Sentiment
          </p>
        </div>
        <div className="flex gap-2">
          <button onClick={handleRefresh} disabled={refreshing || isLoading}
            className="flex items-center gap-1.5 px-3 py-2 rounded-xl border border-border text-xs text-muted-foreground hover:text-foreground hover:border-primary/30 transition-all disabled:opacity-50">
            {refreshing ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
            Refresh
          </button>
          <label className={cn(
            "flex items-center gap-1.5 px-3 py-2 rounded-xl border text-xs font-medium transition-all cursor-pointer",
            uploading ? "border-border text-muted-foreground opacity-60 cursor-not-allowed"
              : "border-primary/40 bg-primary/10 text-primary hover:bg-primary/20"
          )}>
            {uploading ? <Loader2 size={12} className="animate-spin" /> : <Upload size={12} />}
            {uploading ? "Uploading…" : "Upload Zerodha CSV"}
            <input ref={fileRef} type="file" accept=".csv" className="hidden"
              onChange={handleUpload} disabled={uploading} />
          </label>
        </div>
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <StatCard label="Portfolio Value" value={fmtCurrency(totalValue)} sub={`Cost: ${fmtCurrency(totalCost)}`}
          icon={<Briefcase size={16} />} glow trend={totalPnl >= 0 ? "up" : "down"} />
        <StatCard label="Total P&L" value={fmtCurrency(totalPnl)} sub={fmtPct(totalPnlPct)}
          icon={totalPnl >= 0 ? <TrendingUp size={16} /> : <TrendingDown size={16} />}
          trend={totalPnl >= 0 ? "up" : "down"} glow />
        <StatCard label="Winners" value={String(winners)}
          sub={`${holdings?.length ? ((winners / holdings.length) * 100).toFixed(0) : 0}% win rate`}
          icon={<TrendingUp size={16} />} trend="up" />
        <StatCard label="Losers" value={String(losers)}
          sub={`${holdings?.length ? ((losers / holdings.length) * 100).toFixed(0) : 0}% of holdings`}
          icon={<TrendingDown size={16} />} trend="down" />
      </div>

      {/* Intelligence hint */}
      {holdings?.length ? (
        <div className="bg-primary/5 border border-primary/15 rounded-xl px-4 py-2.5 flex items-center gap-2.5 text-xs text-primary/70">
          <Activity size={12} className="shrink-0" />
          Click any row to expand Backtest + Sentiment details. Recommendations update as new data arrives.
          Run backtests from <strong className="font-semibold">Backtest Results</strong> and fetch news from <strong className="font-semibold">News Research</strong>.
        </div>
      ) : null}

      {/* Holdings table */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <BarChart3 size={14} className="text-muted-foreground" />
              <span className="text-sm font-semibold">Holdings</span>
              <span className="text-[10px] text-muted-foreground">· Click row to expand intelligence</span>
            </div>
            <div className="flex items-center gap-2">
              {isLoading && <Loader2 size={11} className="animate-spin text-muted-foreground" />}
              <Badge variant="neutral">{holdings?.length ?? 0} stocks</Badge>
            </div>
          </div>
        </CardHeader>
        <CardContent className="p-0">
          {isLoading ? (
            <LoadingOverlay message="Loading holdings with live prices…" eta="~5 seconds"
              subMessage="Fetching LTP from NSE for all holdings in parallel" />
          ) : !holdings?.length ? (
            <div className="py-12 text-center space-y-3">
              <Upload size={32} className="text-muted-foreground/30 mx-auto" />
              <p className="text-sm font-semibold text-muted-foreground">No holdings yet</p>
              <p className="text-xs text-muted-foreground/60">
                Upload your Zerodha CSV to get started.<br />
                Export from: Zerodha Console → Portfolio → Holdings
              </p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-border">
                    {["Symbol", "Qty", "Avg Price", "LTP", "Value", "P&L", "P&L %", "News Sent", "Sharpe", "Action"].map((h) => (
                      <th key={h} className="px-4 py-3 text-left text-[10px] uppercase tracking-widest text-muted-foreground font-medium whitespace-nowrap">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {holdings.map((h: Holding, i: number) => (
                    <HoldingRow key={h.id} h={h} index={i} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      <p className="text-[10px] text-muted-foreground/50 text-center">
        CSV must have columns: <code className="font-mono">Instrument, Qty., Avg. cost</code> · Export from Zerodha Console → Portfolio → Holdings
      </p>
    </div>
  );
}
