"use client";
import { useState, useRef } from "react";
import {
  Upload, TrendingUp, TrendingDown, Briefcase,
  RefreshCw, BarChart3, Loader2, CheckCircle2,
} from "lucide-react";
import { toast } from "sonner";
import { api, getErrorMessage } from "@/lib/api";
import { useHoldings } from "@/hooks/useData";
import { cn, fmt, fmtPct, fmtCurrency } from "@/lib/utils";
import {
  Card, CardHeader, CardContent, StatCard, Skeleton, Empty, Badge,
  ErrorBanner, LoadingOverlay,
} from "@/components/ui";
import type { Holding } from "@/types";

export default function PortfolioPage() {
  const { data: holdings, isLoading, error: holdingsError, mutate } = useHoldings();
  const [uploading, setUploading]   = useState(false);
  const [uploadMsg, setUploadMsg]   = useState("");
  const [uploadError, setUploadError] = useState("");
  const [refreshing, setRefreshing] = useState(false);
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

    // Client-side validation
    if (!file.name.endsWith(".csv")) {
      setUploadError("❌ Invalid file type. Please upload a .csv file (Zerodha holdings export).");
      return;
    }

    setUploading(true);
    setUploadError("");
    setUploadMsg("Uploading portfolio CSV…");

    const form = new FormData();
    form.append("file", file);

    try {
      setUploadMsg("Parsing holdings and fetching live prices… (ETA: ~10–30 seconds)");
      const { data } = await api.post("/trading/portfolio/upload", form);
      setUploadMsg("");
      toast.success(`✅ Portfolio uploaded — ${data.imported} holdings imported`, {
        description: data.skipped > 0 ? `${data.skipped} rows skipped (invalid data)` : "All rows imported successfully",
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
    try {
      await mutate();
      toast.success("✅ Holdings refreshed with live prices");
    } catch (err: any) {
      toast.error(`Refresh failed: ${getErrorMessage(err)}`);
    } finally {
      setRefreshing(false);
    }
  }

  return (
    <div className="space-y-5 animate-fade-in">

      {/* Upload status bar */}
      {uploading && uploadMsg && (
        <div className="bg-primary/8 border border-primary/20 rounded-xl px-4 py-2.5 flex items-center gap-2.5 text-xs">
          <Loader2 size={12} className="animate-spin text-primary shrink-0" />
          <span className="text-primary font-medium">{uploadMsg}</span>
        </div>
      )}

      {/* Upload error banner */}
      {uploadError && (
        <ErrorBanner
          title="Portfolio Upload Failed"
          message={uploadError}
          onDismiss={() => setUploadError("")}
          onRetry={() => fileRef.current?.click()}
        />
      )}

      {/* Holdings fetch error */}
      {holdingsError && !isLoading && (
        <ErrorBanner
          title="Failed to load holdings"
          message={getErrorMessage(holdingsError)}
          onRetry={() => mutate()}
        />
      )}

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-display font-bold text-xl">Portfolio</h1>
          <p className="text-muted-foreground text-xs mt-0.5">
            {holdings?.length ?? 0} holdings · Live prices · Updated every 60s
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={handleRefresh}
            disabled={refreshing || isLoading}
            className="flex items-center gap-1.5 px-3 py-2 rounded-xl border border-border text-xs text-muted-foreground hover:text-foreground hover:border-primary/30 transition-all disabled:opacity-50"
          >
            {refreshing ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
            Refresh
          </button>
          <label className={cn(
            "flex items-center gap-1.5 px-3 py-2 rounded-xl border text-xs font-medium transition-all cursor-pointer",
            uploading
              ? "border-border text-muted-foreground opacity-60 cursor-not-allowed"
              : "border-primary/40 bg-primary/10 text-primary hover:bg-primary/20"
          )}>
            {uploading ? <Loader2 size={12} className="animate-spin" /> : <Upload size={12} />}
            {uploading ? "Uploading…" : "Upload Zerodha CSV"}
            <input
              ref={fileRef}
              type="file"
              accept=".csv"
              className="hidden"
              onChange={handleUpload}
              disabled={uploading}
            />
          </label>
        </div>
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <StatCard
          label="Portfolio Value"
          value={fmtCurrency(totalValue)}
          sub={`Cost: ${fmtCurrency(totalCost)}`}
          icon={<Briefcase size={16} />}
          glow
          trend={totalPnl >= 0 ? "up" : "down"}
        />
        <StatCard
          label="Total P&L"
          value={fmtCurrency(totalPnl)}
          sub={fmtPct(totalPnlPct)}
          icon={totalPnl >= 0 ? <TrendingUp size={16} /> : <TrendingDown size={16} />}
          trend={totalPnl >= 0 ? "up" : "down"}
          glow
        />
        <StatCard
          label="Winners"
          value={String(winners)}
          sub={`${holdings?.length ? ((winners / holdings.length) * 100).toFixed(0) : 0}% win rate`}
          icon={<TrendingUp size={16} />}
          trend="up"
        />
        <StatCard
          label="Losers"
          value={String(losers)}
          sub={`${holdings?.length ? ((losers / holdings.length) * 100).toFixed(0) : 0}% of holdings`}
          icon={<TrendingDown size={16} />}
          trend="down"
        />
      </div>

      {/* Holdings table */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <BarChart3 size={14} className="text-muted-foreground" />
              <span className="text-sm font-semibold">Holdings</span>
            </div>
            <div className="flex items-center gap-2">
              {isLoading && <Loader2 size={11} className="animate-spin text-muted-foreground" />}
              <Badge variant="neutral">{holdings?.length ?? 0} stocks</Badge>
            </div>
          </div>
        </CardHeader>
        <CardContent className="p-0">
          {isLoading ? (
            <LoadingOverlay
              message="Loading holdings with live prices…"
              eta="~5 seconds"
              subMessage="Fetching LTP from NSE for all holdings in parallel"
            />
          ) : !holdings?.length ? (
            <div className="py-12 text-center space-y-3">
              <Upload size={32} className="text-muted-foreground/30 mx-auto" />
              <p className="text-sm font-semibold text-muted-foreground">No holdings yet</p>
              <p className="text-xs text-muted-foreground/60">
                Upload your Zerodha CSV to get started.<br />
                Download it from: Zerodha Console → Portfolio → Holdings → Export
              </p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-border">
                    {["Symbol", "Qty", "Avg Price", "LTP", "Current Value", "P&L", "P&L %", "Quality"].map((h) => (
                      <th key={h} className="px-4 py-3 text-left text-[10px] uppercase tracking-widest text-muted-foreground font-medium whitespace-nowrap">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {holdings.map((h: Holding, i: number) => {
                    const pnl    = h.pnl ?? 0;
                    const pnlPct = h.pnl_pct ?? 0;
                    const curVal = (h.current_price ?? h.average_price) * h.quantity;
                    return (
                      <tr
                        key={h.id}
                        className="border-b border-border/40 hover:bg-muted/30 transition-colors"
                        style={{ animationDelay: `${i * 20}ms` }}
                      >
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-2.5">
                            <div className="w-7 h-7 rounded-lg bg-primary/10 border border-primary/20 flex items-center justify-center">
                              <span className="text-primary text-[9px] font-bold">{h.symbol.slice(0, 2)}</span>
                            </div>
                            <div>
                              <div className="font-bold text-foreground">{h.symbol}</div>
                              <div className="text-[9px] text-muted-foreground">{h.exchange}</div>
                            </div>
                          </div>
                        </td>
                        <td className="px-4 py-3 font-mono">{fmt(h.quantity, 0)}</td>
                        <td className="px-4 py-3 font-mono">₹{fmt(h.average_price)}</td>
                        <td className="px-4 py-3 font-mono font-semibold">
                          {h.current_price ? `₹${fmt(h.current_price)}` : (
                            <span className="text-muted-foreground/50 text-[10px]">No feed</span>
                          )}
                        </td>
                        <td className="px-4 py-3 font-mono">₹{fmt(curVal)}</td>
                        <td className={cn("px-4 py-3 font-mono font-semibold", pnl >= 0 ? "text-bull" : "text-bear")}>
                          {pnl >= 0 ? "+" : ""}₹{fmt(Math.abs(pnl))}
                        </td>
                        <td className={cn("px-4 py-3 font-mono font-semibold", pnlPct >= 0 ? "text-bull" : "text-bear")}>
                          {fmtPct(pnlPct)}
                        </td>
                        <td className="px-4 py-3">
                          <Badge
                            variant={
                              h.data_quality === "SUFFICIENT" ? "bull"
                              : h.data_quality === "INSUFFICIENT DATA" ? "gold"
                              : "bear"
                            }
                          >
                            {h.data_quality === "SUFFICIENT" ? "10yr+" : h.data_quality}
                          </Badge>
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

      {/* Zerodha CSV format hint */}
      <p className="text-[10px] text-muted-foreground/50 text-center">
        CSV must have columns: <code className="font-mono">Instrument, Qty., Avg. cost</code> · Export from Zerodha Console → Portfolio → Holdings
      </p>
    </div>
  );
}
