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
    setScanMsg("Detecting market regime…");
    try {
      setScanMsg("Running signal scan across all holdings… (ETA: ~30–60 seconds)");
      const res = await triggerScanNow();
      setScanMsg("");
      toast.success(`✅ Scan complete — ${res.signals_count} signals generated`, {
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

      {/* Scan status bar */}
      {scanning && scanMsg && (
        <div className="bg-primary/8 border border-primary/20 rounded-xl px-4 py-2.5 flex items-center gap-2.5 text-xs">
          <Loader2 size={12} className="animate-spin text-primary shrink-0" />
          <span className="text-primary font-medium">{scanMsg}</span>
        </div>
      )}

      {/* Scan error */}
      {scanError && (
        <ErrorBanner
          title="Signal scan failed"
          message={scanError}
          onDismiss={() => setScanError("")}
          onRetry={handleScan}
        />
      )}

      {/* Dashboard fetch error */}
      {dashError && !dashLoading && (
        <ErrorBanner
          title="Failed to load dashboard data"
          message={getErrorMessage(dashError)}
          onRetry={() => mutateDash()}
        />
      )}

      {/* Header */}
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

      {/* Regime panel */}
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

      {/* Stats */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {dashLoading ? (
          [...Array(4)].map((_, i) => <Skeleton key={i} className="h-20 rounded-2xl" />)
        ) : (
          <>
            <StatCard label="Buy Signals"  value={String(dashboard?.total_buy_signals  ?? 0)} trend="up"      icon={<TrendingUp size={15} />} glow />
            <StatCard label="Sell Signals" value={String(dashboard?.total_sell_signals ?? 0)} trend="down"    icon={<TrendingDown size={15} />} />
            <StatCard label="Hold Signals" value={String(dashboard?.total_hold_signals ?? 0)} trend="neutral" icon={<Activity size={15} />} />
            <StatCard label="Regime Conf"  value={dashboard ? `${(dashboard.regime_confidence * 100).toFixed(0)}%` : "—"} trend="neutral" icon={<BarChart3 size={15} />} />
          </>
        )}
      </div>

      {/* Bias warning */}
      {dashboard?.bias_warning && (
        <div className="bg-gold/5 border border-gold/30 rounded-2xl p-4 flex gap-3">
          <Zap size={14} className="text-gold shrink-0 mt-0.5" />
          <p className="text-xs text-gold/80">{dashboard.bias_message}</p>
        </div>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-[1fr_400px] gap-5">
        {/* Signals list */}
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <Tabs tabs={FILTER_TABS} active={filter} onChange={setFilter} />
            <button
              onClick={() => { mutateSigs(); mutateDash(); }}
              className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
            >
              <RefreshCw size={11} />
            </button>
          </div>

          {/* Signals fetch error */}
          {sigsError && !sigsLoading && (
            <ErrorBanner
              title="Failed to load signals"
              message={getErrorMessage(sigsError)}
              onRetry={() => mutateSigs()}
            />
          )}

          {sigsLoading ? (
            <LoadingOverlay
              message="Loading latest signals…"
              eta="~3 seconds"
            />
          ) : !signals?.length ? (
            <Empty
              icon={<Zap size={32} />}
              title="No signals yet"
              description='Click "Force Scan" to run a fresh signal scan, or wait for the 5-minute auto-scheduler'
            />
          ) : (
            <div className="space-y-3">
              {signals.map((sig: FinalSignal) => (
                <SignalCard key={sig.id} signal={sig} onSelectTicker={setChartTicker} />
              ))}
            </div>
          )}
        </div>

        {/* Chart panel */}
        <div className="space-y-3">
          {chartTicker ? (
            <>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <BarChart3 size={13} className="text-muted-foreground" />
                  <span className="text-sm font-semibold">{chartTicker}</span>
                </div>
                <button
                  onClick={() => setChartTicker(null)}
                  className="text-[10px] text-muted-foreground hover:text-foreground transition-colors"
                >
                  Clear ×
                </button>
              </div>
              <CandlestickChart ticker={chartTicker} showEMA showBB showVolume height={420} />
            </>
          ) : (
            <div className="h-64 rounded-2xl border border-border border-dashed flex items-center justify-center">
              <div className="text-center">
                <BarChart3 size={24} className="text-muted-foreground/30 mx-auto mb-2" />
                <p className="text-xs text-muted-foreground">Click any ticker to view chart</p>
              </div>
            </div>
          )}

          {dashboard?.top_signals?.length ? (
            <Card>
              <CardHeader>
                <span className="text-xs font-semibold flex items-center gap-1.5">
                  <TrendingUp size={12} className="text-bull" /> Top Buy Signals
                </span>
              </CardHeader>
              <CardContent className="p-0">
                {dashboard.top_signals.slice(0, 5).map((s: any, i: number) => (
                  <button
                    key={s.ticker}
                    onClick={() => setChartTicker(s.ticker)}
                    className="w-full flex items-center gap-3 px-4 py-3 hover:bg-muted/30 transition-colors border-b border-border/40 last:border-0 text-left"
                  >
                    <span className="text-[10px] text-muted-foreground w-4">#{i + 1}</span>
                    <div className="w-7 h-7 rounded-lg bg-bull/10 border border-bull/20 flex items-center justify-center">
                      <span className="text-bull text-[9px] font-bold">{s.ticker.slice(0, 2)}</span>
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="text-xs font-semibold">{s.ticker}</div>
                      <div className="text-[9px] text-muted-foreground truncate">{s.selected_strategy}</div>
                    </div>
                    <div className="text-right">
                      <div className="text-xs font-bold text-bull">{s.confidence.toFixed(0)}%</div>
                      <div className="text-[9px] text-muted-foreground">conf</div>
                    </div>
                  </button>
                ))}
              </CardContent>
            </Card>
          ) : null}
        </div>
      </div>
    </div>
  );
}
