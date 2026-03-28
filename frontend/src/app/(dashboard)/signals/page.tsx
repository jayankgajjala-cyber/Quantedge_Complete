"use client";
import { useState } from "react";
import {
  Zap, BarChart3, TrendingUp, TrendingDown,
  Activity, RefreshCw, Loader2,
} from "lucide-react";
import { toast } from "sonner";
import { getErrorMessage } from "@/lib/api";
import { useDashboard, useLatestSignals, useLatestRegime, triggerScanNow } from "@/hooks/useData";
import { cn, regimeBadge } from "@/lib/utils";
import {
  Card, CardHeader, CardContent, StatCard, Skeleton, Badge, Tabs, Empty,
  ErrorBanner, LoadingOverlay, ActionButton,
} from "@/components/ui";
import SignalCard from "@/components/signals/SignalCard";
import CandlestickChart from "@/components/charts/CandlestickChart";
import type { FinalSignal } from "@/types";

const FILTER_TABS = [
  { key: "all",  label: "All",  icon: <Activity size={10} /> },
  { key: "BUY",  label: "Buy",  icon: <TrendingUp size={10} className="text-bull" /> },
  { key: "SELL", label: "Sell", icon: <TrendingDown size={10} className="text-bear" /> },
  { key: "HOLD", label: "Hold" },
];

export default function SignalsPage() {
  const [filter, setFilter]           = useState("all");
  const [chartTicker, setChartTicker] = useState<string | null>(null);
  const [scanning, setScanning]       = useState(false);
  const [scanMsg, setScanMsg]         = useState("");
  const [scanError, setScanError]     = useState("");

  const { data: dashboard, isLoading: dashLoading, error: dashError, mutate: mutateDash } = useDashboard();
  const { data: regime } = useLatestRegime();
  const signalFilter = filter === "all" ? undefined : filter;
  const { data: signals, isLoading: sigsLoading, error: sigsError, mutate: mutateSigs } = useLatestSignals(signalFilter);

  const badge = regime ? regimeBadge(regime.regime_label) : null;

  async function handleScan() {
    setScanning(true);
    setScanError("");
    setScanMsg("Queuing scan…");
    try {
      const res = await triggerScanNow((msg) => setScanMsg(msg));
      setScanMsg("");
      toast.success(`✅ Scan complete — ${res.signals_count ?? 0} signals generated`, {
        description: res.regime_label ? `Current regime: ${res.regime_label}` : undefined,
        duration: 5000,
      });
      mutateDash();
      mutateSigs();
    } catch (err: any) {
      setScanMsg("");
      const msg = getErrorMessage(err);
      setScanError(`❌ Scan failed: ${msg}`);
      toast.error(`Scan failed: ${msg}`);
    } finally {
      setScanning(false);
    }
  }

  return (
    <div className="space-y-5 animate-fade-in">

      {scanning && scanMsg && (
        <div className="bg-primary/8 border border-primary/20 rounded-xl px-4 py-2.5 flex items-center gap-2.5 text-xs">
          <Loader2 size={12} className="animate-spin text-primary shrink-0" />
          <span className="text-primary font-medium">{scanMsg}</span>
        </div>
      )}

      {scanError && (
        <ErrorBanner
          title="Signal scan failed"
          message={scanError}
          onDismiss={() => setScanError("")}
          onRetry={handleScan}
        />
      )}

      {dashError && !dashLoading && (
        <ErrorBanner
          title="Failed to load dashboard data"
          message={getErrorMessage(dashError)}
          onRetry={() => mutateDash()}
        />
      )}

      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-display font-bold text-xl">Market Signals</h1>
          <p className="text-muted-foreground text-xs mt-0.5">
            Regime-aware · 5-min auto-refresh · {signals?.length ?? 0} active signals
          </p>
        </div>
        <ActionButton
          onClick={handleScan}
          loading={scanning}
          loadingLabel="Scanning…"
          icon={<Zap size={12} />}
          variant="primary"
        >
          Force Scan
        </ActionButton>
      </div>

      {regime && badge && (
        <div className={cn(
          "rounded-2xl border p-4 flex gap-5 items-center flex-wrap",
          badge.color.includes("bull") ? "bg-bull/5 border-bull/20"
          : badge.color.includes("bear") ? "bg-bear/5 border-bear/20"
          : "bg-gold/5 border-gold/20"
        )}>
          <div className="flex items-center gap-3">
            <div className="text-3xl">{badge.icon}</div>
            <div>
              <div className="text-[10px] uppercase tracking-widest text-muted-foreground">Current Regime</div>
              <div className="font-display font-bold text-lg leading-none mt-0.5">{badge.label}</div>
            </div>
          </div>
          <div className="flex gap-6 flex-wrap">
            {[
              { label: "ADX",          value: regime.adx_14 ? regime.adx_14.toFixed(1) : "—" },
              { label: "ATR %ile",     value: regime.atr_percentile ? `${regime.atr_percentile.toFixed(0)}th` : "—" },
              { label: "Price vs EMA", value: regime.price_vs_ema || "—" },
              { label: "Confidence",   value: regime.confidence_score ? `${(regime.confidence_score * 100).toFixed(0)}%` : "—" },
            ].map(({ label, value }) => (
              <div key={label}>
                <div className="text-[9px] uppercase tracking-widest text-muted-foreground">{label}</div>
                <div className="text-sm font-mono font-bold">{value}</div>
              </div>
            ))}
          </div>
          {regime.regime_summary && (
            <p className="text-[10px] text-muted-foreground max-w-lg leading-relaxed flex-1">
              {regime.regime_summary}
            </p>
          )}
        </div>
      )}

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {dashLoading ? (
          [...Array(4)].map((_, i) => <Skeleton key={i} className="h-20 rounded-2xl" />)
        ) : (
          <>
            <StatCard label="Buy Signals"  value={String(dashboard?.total_buy_signals  ?? 0)} trend="up"      icon={<TrendingUp size={15} />} glow />
            <StatCard label="Sell Signals" value={String(dashboard?.total_sell_signals ?? 0)} trend="down"    icon={<TrendingDown size={15} />} />
            <StatCard label="Hold Signals" value={String(dashboard?.total_hold_signals ?? 0)} trend="neutral" icon={<Activity size={15} />} />
            <StatCard label="Regime Conf"  value={dashboard ? `${((dashboard.regime_confidence ?? 0) * 100).toFixed(0)}%` : "—"} trend="neutral" icon={<BarChart3 size={15} />} />
          </>
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[1fr_340px] gap-4">
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <Tabs
              tabs={FILTER_TABS.map((t) => ({
                key: t.key,
                label: (
                  <span className="flex items-center gap-1">
                    {t.icon}
                    {t.label}
                  </span>
                ),
              }))}
              active={filter}
              onChange={setFilter}
            />
            <button
              onClick={() => { mutateDash(); mutateSigs(); }}
              className="ml-auto text-muted-foreground hover:text-foreground transition-colors p-1.5 rounded-lg hover:bg-muted/50"
            >
              <RefreshCw size={12} />
            </button>
          </div>

          {sigsError && (
            <ErrorBanner
              title="Failed to load signals"
              message={getErrorMessage(sigsError)}
              onRetry={() => mutateSigs()}
            />
          )}

          {sigsLoading ? (
            <div className="space-y-2">
              {[...Array(3)].map((_, i) => <Skeleton key={i} className="h-28 rounded-2xl" />)}
            </div>
          ) : !signals?.length ? (
            <Empty
              icon={<Zap size={20} />}
              title="No signals yet"
              description={
                filter === "all"
                  ? "Upload your portfolio CSV then click Force Scan to generate signals."
                  : `No ${filter} signals found. Try changing the filter.`
              }
            />
          ) : (
            <div className="space-y-2">
              {signals.map((sig) => (
                <SignalCard
                  key={sig.id}
                  signal={sig}
                  onClick={() => setChartTicker(sig.ticker === chartTicker ? null : sig.ticker)}
                  active={chartTicker === sig.ticker}
                />
              ))}
            </div>
          )}
        </div>

        <div className="hidden lg:block">
          {chartTicker ? (
            <div className="sticky top-4">
              <CandlestickChart ticker={chartTicker} />
            </div>
          ) : (
            <div className="h-64 rounded-2xl border border-border/40 flex items-center justify-center text-muted-foreground text-xs">
              Click any ticker to view chart
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
