"use client";
import { useState } from "react";
import {
  Trophy, TrendingUp, TrendingDown, BarChart3,
  RefreshCw, ChevronUp, ChevronDown, Minus,
} from "lucide-react";
import { useLeaderboard } from "@/hooks/useData";
import { cn, fmt, fmtPct } from "@/lib/utils";
import { Card, CardHeader, CardContent, Badge, Skeleton, Empty, ErrorBanner } from "@/components/ui";
import { getErrorMessage } from "@/lib/api";
import type { StrategyResult } from "@/types";

const QUALITY_BADGE: Record<string, { label: string; variant: "bull" | "gold" | "bear" | "neutral" }> = {
  "SUFFICIENT":         { label: "10yr+",    variant: "bull" },
  "INSUFFICIENT DATA":  { label: "< 10yr",   variant: "gold" },
  "LOW CONFIDENCE":     { label: "Low conf", variant: "bear" },
};

function SortIcon({ field, active, dir }: { field: string; active: string; dir: "asc" | "desc" }) {
  if (active !== field) return <Minus size={9} className="text-muted-foreground/40" />;
  return dir === "desc"
    ? <ChevronDown size={10} className="text-primary" />
    : <ChevronUp size={10} className="text-primary" />;
}

type SortField = "sharpe_ratio" | "cagr" | "win_rate" | "max_drawdown";

export default function LeaderboardPage() {
  const { data: rows, isLoading, error, mutate } = useLeaderboard();
  const [sortField, setSortField] = useState<SortField>("sharpe_ratio");
  const [sortDir,   setSortDir]   = useState<"asc" | "desc">("desc");
  const [filterQuality, setFilterQuality] = useState<string>("all");

  function handleSort(field: SortField) {
    if (sortField === field) {
      setSortDir((d) => (d === "desc" ? "asc" : "desc"));
    } else {
      setSortField(field);
      setSortDir("desc");
    }
  }

  const filtered = (rows ?? []).filter((r) =>
    filterQuality === "all" || r.data_quality === filterQuality
  );

  const sorted = [...filtered].sort((a, b) => {
    const av = a[sortField] ?? -Infinity;
    const bv = b[sortField] ?? -Infinity;
    // For drawdown, lower (more negative) is worse — invert display sort
    const mult = sortField === "max_drawdown"
      ? (sortDir === "desc" ? 1 : -1)   // desc = worst drawdown first
      : (sortDir === "desc" ? -1 : 1);
    return (av < bv ? 1 : -1) * mult;
  });

  // Summary stats
  const totalStrategies   = rows?.length ?? 0;
  const avgSharpe         = rows?.length
    ? (rows.reduce((s, r) => s + (r.sharpe_ratio ?? 0), 0) / rows.length).toFixed(2)
    : "—";
  const bestStrategy      = rows?.[0];
  const sufficientCount   = rows?.filter((r) => r.data_quality === "SUFFICIENT").length ?? 0;

  const COLS: { key: SortField; label: string; unit?: string }[] = [
    { key: "sharpe_ratio", label: "Sharpe",    unit: "" },
    { key: "cagr",         label: "CAGR",      unit: "%" },
    { key: "win_rate",     label: "Win Rate",  unit: "%" },
    { key: "max_drawdown", label: "Max DD",    unit: "%" },
  ];

  return (
    <div className="space-y-5 animate-fade-in">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-display font-bold text-xl">Strategy Leaderboard</h1>
          <p className="text-muted-foreground text-xs mt-0.5">
            10-year backtests · 8 strategies · ranked by Sharpe Ratio
          </p>
        </div>
        <button
          onClick={() => mutate()}
          className="flex items-center gap-1.5 px-3 py-2 rounded-xl border border-border text-xs text-muted-foreground hover:text-foreground hover:border-primary/30 transition-all"
        >
          <RefreshCw size={12} /> Refresh
        </button>
      </div>

      {/* Summary stat cards */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {[
          {
            label: "Total Backtests",
            value: String(totalStrategies),
            icon: <BarChart3 size={15} />,
            trend: "neutral" as const,
          },
          {
            label: "Avg Sharpe",
            value: avgSharpe,
            icon: <TrendingUp size={15} />,
            trend: parseFloat(String(avgSharpe)) >= 1 ? "up" as const : "neutral" as const,
          },
          {
            label: "10yr+ Quality",
            value: String(sufficientCount),
            icon: <Trophy size={15} />,
            trend: "up" as const,
          },
          {
            label: "Top Strategy",
            value: bestStrategy?.strategy_name?.replace(/_/g, " ") ?? "—",
            icon: <Trophy size={15} />,
            trend: "up" as const,
            glow: true,
          },
        ].map(({ label, value, icon, trend, glow }) => (
          <div
            key={label}
            className={cn(
              "bg-card border border-border rounded-2xl px-4 py-3 flex gap-3",
              glow && "glow-bull"
            )}
          >
            <div className="w-8 h-8 rounded-xl bg-muted/60 border border-border flex items-center justify-center shrink-0 text-muted-foreground">
              {icon}
            </div>
            <div className="min-w-0">
              <p className="text-[10px] uppercase tracking-widest text-muted-foreground font-medium mb-0.5">
                {label}
              </p>
              <p className={cn(
                "text-sm font-bold font-display truncate leading-tight",
                trend === "up" ? "text-bull" : trend === "down" ? "text-bear" : "text-foreground"
              )}>
                {value}
              </p>
            </div>
          </div>
        ))}
      </div>

      {/* Leaderboard fetch error */}
      {error && !isLoading && (
        <ErrorBanner
          title="Failed to load leaderboard"
          message={getErrorMessage(error)}
          onRetry={() => mutate()}
        />
      )}

      {/* Filter + Table */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between flex-wrap gap-3">
            <span className="text-xs font-semibold flex items-center gap-1.5">
              <Trophy size={13} className="text-gold" />
              Rankings
              <Badge variant="neutral">{sorted.length} results</Badge>
            </span>
            {/* Quality filter */}
            <div className="flex gap-1.5">
              {[
                { key: "all",              label: "All" },
                { key: "SUFFICIENT",       label: "10yr+" },
                { key: "INSUFFICIENT DATA",label: "< 10yr" },
              ].map(({ key, label }) => (
                <button
                  key={key}
                  onClick={() => setFilterQuality(key)}
                  className={cn(
                    "px-2.5 py-1 rounded-lg text-[10px] font-semibold border transition-all",
                    filterQuality === key
                      ? "bg-primary/15 border-primary/40 text-primary"
                      : "border-border text-muted-foreground hover:text-foreground bg-muted/30"
                  )}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
        </CardHeader>

        <CardContent className="p-0">
          {isLoading ? (
            <div className="p-5 space-y-2">
              {[...Array(8)].map((_, i) => (
                <Skeleton key={i} className="h-12 w-full" />
              ))}
            </div>
          ) : !sorted.length ? (
            <Empty
              icon={<Trophy size={32} />}
              title="No backtest results yet"
              description='Run a backtest from Settings → "Start Backtest" to populate the leaderboard'
            />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-border">
                    <th className="px-4 py-3 text-left text-[10px] uppercase tracking-widest text-muted-foreground font-medium w-10">
                      #
                    </th>
                    <th className="px-4 py-3 text-left text-[10px] uppercase tracking-widest text-muted-foreground font-medium">
                      Ticker
                    </th>
                    <th className="px-4 py-3 text-left text-[10px] uppercase tracking-widest text-muted-foreground font-medium">
                      Strategy
                    </th>
                    {COLS.map(({ key, label }) => (
                      <th
                        key={key}
                        onClick={() => handleSort(key)}
                        className="px-4 py-3 text-right text-[10px] uppercase tracking-widest text-muted-foreground font-medium cursor-pointer hover:text-foreground select-none whitespace-nowrap"
                      >
                        <span className="inline-flex items-center gap-1 justify-end">
                          {label}
                          <SortIcon field={key} active={sortField} dir={sortDir} />
                        </span>
                      </th>
                    ))}
                    <th className="px-4 py-3 text-left text-[10px] uppercase tracking-widest text-muted-foreground font-medium">
                      Quality
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {sorted.map((row: StrategyResult, idx: number) => {
                    const qBadge = QUALITY_BADGE[row.data_quality] ?? { label: row.data_quality, variant: "neutral" as const };
                    const sharpeColor =
                      (row.sharpe_ratio ?? 0) >= 1.5 ? "text-bull"
                      : (row.sharpe_ratio ?? 0) >= 0.8 ? "text-gold"
                      : "text-bear";
                    const isTopThree = idx < 3 && filterQuality !== "all" ? false : idx < 3;

                    return (
                      <tr
                        key={`${row.stock_ticker}-${row.strategy_name}`}
                        className={cn(
                          "border-b border-border/40 hover:bg-muted/30 transition-colors",
                          isTopThree && "bg-primary/[0.02]"
                        )}
                      >
                        {/* Rank */}
                        <td className="px-4 py-3">
                          {idx === 0 ? (
                            <span className="text-gold font-bold">🥇</span>
                          ) : idx === 1 ? (
                            <span className="text-muted-foreground font-bold">🥈</span>
                          ) : idx === 2 ? (
                            <span className="text-amber-600 font-bold">🥉</span>
                          ) : (
                            <span className="text-muted-foreground font-mono text-[11px]">
                              #{idx + 1}
                            </span>
                          )}
                        </td>

                        {/* Ticker */}
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-2">
                            <div className="w-7 h-7 rounded-lg bg-primary/10 border border-primary/20 flex items-center justify-center shrink-0">
                              <span className="text-primary text-[9px] font-bold">
                                {row.stock_ticker.slice(0, 2)}
                              </span>
                            </div>
                            <span className="font-bold text-foreground">{row.stock_ticker}</span>
                          </div>
                        </td>

                        {/* Strategy */}
                        <td className="px-4 py-3 text-muted-foreground max-w-[180px]">
                          <span className="truncate block">
                            {row.strategy_name.replace(/_/g, " ")}
                          </span>
                        </td>

                        {/* Sharpe */}
                        <td className={cn("px-4 py-3 font-mono font-bold text-right", sharpeColor)}>
                          {row.sharpe_ratio != null ? row.sharpe_ratio.toFixed(2) : "—"}
                        </td>

                        {/* CAGR */}
                        <td className={cn(
                          "px-4 py-3 font-mono font-semibold text-right",
                          (row.cagr ?? 0) >= 0 ? "text-bull" : "text-bear"
                        )}>
                          {row.cagr != null ? `${row.cagr >= 0 ? "+" : ""}${row.cagr.toFixed(1)}%` : "—"}
                        </td>

                        {/* Win rate */}
                        <td className={cn(
                          "px-4 py-3 font-mono font-semibold text-right",
                          (row.win_rate ?? 0) >= 55 ? "text-bull"
                          : (row.win_rate ?? 0) >= 45 ? "text-gold"
                          : "text-bear"
                        )}>
                          {row.win_rate != null ? `${row.win_rate.toFixed(1)}%` : "—"}
                        </td>

                        {/* Max drawdown */}
                        <td className={cn(
                          "px-4 py-3 font-mono font-semibold text-right",
                          (row.max_drawdown ?? 0) <= -30 ? "text-bear"
                          : (row.max_drawdown ?? 0) <= -15 ? "text-gold"
                          : "text-bull"
                        )}>
                          {row.max_drawdown != null
                            ? `${row.max_drawdown.toFixed(1)}%`
                            : "—"}
                        </td>

                        {/* Quality badge */}
                        <td className="px-4 py-3">
                          <Badge variant={qBadge.variant}>{qBadge.label}</Badge>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
