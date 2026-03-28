"use client";
import { useState } from "react";
import { BarChart3, RefreshCw, TrendingUp, TrendingDown, Minus, ChevronDown, ChevronUp } from "lucide-react";
import { useBacktests } from "@/hooks/useData";
import { cn } from "@/lib/utils";

interface BacktestRow {
  stock_ticker:      string;
  strategy_name:     string;
  sharpe_ratio:      number | null;
  cagr:              number | null;
  win_rate:          number | null;
  max_drawdown:      number | null;
  sortino_ratio:     number | null;
  profit_factor:     number | null;
  total_trades:      number | null;
  winning_trades:    number | null;
  losing_trades:     number | null;
  total_return_pct:  number | null;
  annual_volatility: number | null;
  years_of_data:     number | null;
  data_quality:      string;
  ran_at:            string | null;
}

function fmt(v: number | null, decimals = 2, suffix = "") {
  if (v == null) return "—";
  return `${v.toFixed(decimals)}${suffix}`;
}

function pctColor(v: number | null) {
  if (v == null) return "text-muted-foreground";
  return v > 0 ? "text-bull" : v < 0 ? "text-bear" : "text-muted-foreground";
}

function sharpeColor(v: number | null) {
  if (v == null) return "text-muted-foreground";
  return v >= 1 ? "text-bull" : v >= 0.5 ? "text-gold" : "text-bear";
}

function qualityBadge(q: string) {
  if (q === "SUFFICIENT")    return "bg-bull/10 text-bull border-bull/20";
  if (q === "INSUFFICIENT DATA") return "bg-gold/10 text-gold border-gold/20";
  return "bg-bear/10 text-bear border-bear/20";
}

export default function BacktestsPage() {
  const { data: rows, isLoading, error, mutate } = useBacktests();
  const [expandedTicker, setExpandedTicker] = useState<string | null>(null);
  const [filterTicker, setFilterTicker]     = useState("");

  // Group rows by ticker
  const grouped: Record<string, BacktestRow[]> = {};
  (rows ?? []).forEach((r) => {
    if (!grouped[r.stock_ticker]) grouped[r.stock_ticker] = [];
    grouped[r.stock_ticker].push(r);
  });

  const tickers = Object.keys(grouped).filter((t) =>
    !filterTicker || t.toLowerCase().includes(filterTicker.toLowerCase())
  );

  const totalTickers   = Object.keys(grouped).length;
  const totalStrategies = (rows ?? []).length;
  const avgSharpe = rows?.length
    ? (rows.reduce((s, r) => s + (r.sharpe_ratio ?? 0), 0) / rows.length).toFixed(2)
    : "—";

  return (
    <div className="max-w-5xl space-y-5 animate-fade-in">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-display font-bold text-xl">Backtest Results</h1>
          <p className="text-muted-foreground text-xs mt-1">
            Strategy performance across all portfolio holdings · weekly refresh
          </p>
        </div>
        <button
          onClick={() => mutate()}
          className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors px-3 py-1.5 rounded-lg border border-border hover:bg-muted/50"
        >
          <RefreshCw size={11} />
          Refresh
        </button>
      </div>

      {/* Summary stats */}
      <div className="grid grid-cols-3 gap-3">
        {[
          { label: "Stocks Backtested", value: isLoading ? "…" : String(totalTickers) },
          { label: "Total Strategies",  value: isLoading ? "…" : String(totalStrategies) },
          { label: "Avg Sharpe Ratio",  value: isLoading ? "…" : avgSharpe },
        ].map(({ label, value }) => (
          <div key={label} className="bg-card border border-border rounded-2xl px-4 py-3">
            <div className="text-[10px] uppercase tracking-widest text-muted-foreground">{label}</div>
            <div className="font-display font-bold text-lg mt-0.5">{value}</div>
          </div>
        ))}
      </div>

      {/* Filter */}
      {!isLoading && tickers.length > 0 && (
        <input
          type="text"
          value={filterTicker}
          onChange={(e) => setFilterTicker(e.target.value)}
          placeholder="Filter by ticker…"
          className="w-full max-w-xs bg-muted/30 border border-border rounded-xl px-3 py-2 text-xs outline-none focus:border-primary/50"
        />
      )}

      {/* Error */}
      {error && (
        <div className="bg-bear/10 border border-bear/20 rounded-2xl px-5 py-4 text-xs text-bear">
          ❌ Failed to load backtest results — {error.message ?? String(error)}
        </div>
      )}

      {/* Loading */}
      {isLoading && (
        <div className="space-y-2">
          {[...Array(4)].map((_, i) => (
            <div key={i} className="h-14 rounded-2xl bg-muted/30 animate-pulse" />
          ))}
        </div>
      )}

      {/* Empty */}
      {!isLoading && !error && tickers.length === 0 && (
        <div className="bg-card border border-border rounded-2xl overflow-hidden">
          <div className="flex items-start gap-4 px-5 py-6">
            <span className="text-base shrink-0 mt-0.5">ℹ️</span>
            <div>
              <div className="text-xs font-semibold">No backtest results yet</div>
              <div className="text-xs text-muted-foreground mt-0.5 leading-relaxed">
                Backtests run every Saturday at 06:30 IST automatically, or you can
                trigger one manually via the Portfolio page for any holding.
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Results accordion grouped by ticker */}
      {!isLoading && tickers.length > 0 && (
        <div className="bg-card border border-border rounded-2xl overflow-hidden divide-y divide-border/40">
          {tickers.map((ticker) => {
            const strategies = grouped[ticker];
            const best = [...strategies].sort(
              (a, b) => (b.sharpe_ratio ?? -99) - (a.sharpe_ratio ?? -99)
            )[0];
            const isOpen = expandedTicker === ticker;

            return (
              <div key={ticker}>
                {/* Ticker row */}
                <button
                  onClick={() => setExpandedTicker(isOpen ? null : ticker)}
                  className="w-full flex items-center gap-4 px-5 py-4 hover:bg-muted/20 transition-colors text-left"
                >
                  <span className="text-xs font-bold font-mono w-24 shrink-0">{ticker}</span>

                  <div className="flex-1 flex items-center gap-6 flex-wrap text-xs">
                    <span>
                      <span className="text-muted-foreground">Best: </span>
                      <span className="font-medium">{best.strategy_name}</span>
                    </span>
                    <span>
                      <span className="text-muted-foreground">Sharpe </span>
                      <span className={cn("font-mono font-bold", sharpeColor(best.sharpe_ratio))}>
                        {fmt(best.sharpe_ratio)}
                      </span>
                    </span>
                    <span>
                      <span className="text-muted-foreground">CAGR </span>
                      <span className={cn("font-mono font-bold", pctColor(best.cagr))}>
                        {fmt(best.cagr, 1, "%")}
                      </span>
                    </span>
                    <span>
                      <span className="text-muted-foreground">Win </span>
                      <span className="font-mono font-bold text-foreground">
                        {fmt(best.win_rate ? best.win_rate * 100 : null, 0, "%")}
                      </span>
                    </span>
                    <span className={cn(
                      "text-[9px] border rounded px-1.5 py-0.5 font-semibold uppercase tracking-wide",
                      qualityBadge(best.data_quality)
                    )}>
                      {best.data_quality === "SUFFICIENT" ? "✓ Sufficient" :
                       best.data_quality === "INSUFFICIENT DATA" ? "⚠ Partial" : "⚠ Low Conf"}
                    </span>
                    <span className="text-muted-foreground text-[10px]">
                      {strategies.length} strategies
                    </span>
                  </div>

                  {isOpen ? <ChevronUp size={13} className="shrink-0 text-muted-foreground" /> : <ChevronDown size={13} className="shrink-0 text-muted-foreground" />}
                </button>

                {/* Expanded strategy table */}
                {isOpen && (
                  <div className="px-5 pb-4 overflow-x-auto">
                    <table className="w-full text-[11px] border-collapse">
                      <thead>
                        <tr className="text-muted-foreground text-[9px] uppercase tracking-widest">
                          <th className="text-left pb-2 pr-4 font-medium">Strategy</th>
                          <th className="text-right pb-2 pr-4 font-medium">Sharpe</th>
                          <th className="text-right pb-2 pr-4 font-medium">Sortino</th>
                          <th className="text-right pb-2 pr-4 font-medium">CAGR</th>
                          <th className="text-right pb-2 pr-4 font-medium">Return</th>
                          <th className="text-right pb-2 pr-4 font-medium">Win%</th>
                          <th className="text-right pb-2 pr-4 font-medium">MaxDD</th>
                          <th className="text-right pb-2 pr-4 font-medium">Trades</th>
                          <th className="text-right pb-2 pr-4 font-medium">PF</th>
                          <th className="text-right pb-2 font-medium">Years</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-border/20">
                        {strategies
                          .sort((a, b) => (b.sharpe_ratio ?? -99) - (a.sharpe_ratio ?? -99))
                          .map((s) => (
                          <tr key={s.strategy_name} className="hover:bg-muted/10">
                            <td className="py-2 pr-4 font-medium">{s.strategy_name}</td>
                            <td className={cn("py-2 pr-4 text-right font-mono font-bold", sharpeColor(s.sharpe_ratio))}>
                              {fmt(s.sharpe_ratio)}
                            </td>
                            <td className={cn("py-2 pr-4 text-right font-mono", sharpeColor(s.sortino_ratio))}>
                              {fmt(s.sortino_ratio)}
                            </td>
                            <td className={cn("py-2 pr-4 text-right font-mono", pctColor(s.cagr))}>
                              {fmt(s.cagr, 1, "%")}
                            </td>
                            <td className={cn("py-2 pr-4 text-right font-mono", pctColor(s.total_return_pct))}>
                              {fmt(s.total_return_pct, 1, "%")}
                            </td>
                            <td className="py-2 pr-4 text-right font-mono">
                              {fmt(s.win_rate ? s.win_rate * 100 : null, 0, "%")}
                            </td>
                            <td className="py-2 pr-4 text-right font-mono text-bear">
                              {s.max_drawdown != null ? `-${Math.abs(s.max_drawdown * 100).toFixed(1)}%` : "—"}
                            </td>
                            <td className="py-2 pr-4 text-right font-mono text-muted-foreground">
                              {s.total_trades ?? "—"}
                            </td>
                            <td className="py-2 pr-4 text-right font-mono">
                              {fmt(s.profit_factor)}
                            </td>
                            <td className="py-2 text-right font-mono text-muted-foreground">
                              {fmt(s.years_of_data, 1)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    {strategies[0]?.ran_at && (
                      <p className="text-[10px] text-muted-foreground mt-2">
                        Last run: {new Date(strategies[0].ran_at).toLocaleDateString("en-IN", {
                          day: "numeric", month: "short", year: "numeric",
                          hour: "2-digit", minute: "2-digit",
                        })}
                      </p>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Legend */}
      {!isLoading && tickers.length > 0 && (
        <div className="bg-muted/30 border border-border rounded-2xl p-5 space-y-2 text-xs leading-relaxed">
          <p className="font-bold text-sm">Reading the results</p>
          <p><span className="text-bull font-mono">Sharpe ≥ 1.0</span> — Strong risk-adjusted returns. Preferred for live trading.</p>
          <p><span className="text-gold font-mono">Sharpe 0.5–1.0</span> — Moderate. Use with caution or in trending regimes only.</p>
          <p><span className="text-bear font-mono">Sharpe &lt; 0.5</span> — Weak. Avoid until market conditions improve.</p>
          <p><span className="font-semibold">CAGR</span> — Compound Annual Growth Rate over the backtest period.</p>
          <p><span className="font-semibold">MaxDD</span> — Largest peak-to-trough drawdown. Lower is better.</p>
          <p><span className="font-semibold">PF</span> — Profit Factor (gross profit / gross loss). &gt;1.5 is healthy.</p>
        </div>
      )}
    </div>
  );
}
