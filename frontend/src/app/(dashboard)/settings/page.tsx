"use client";
import { useState } from "react";
import { Settings, Server, Zap, Database, Key, AlertCircle, CheckCircle2, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { runBacktest } from "@/hooks/useData";
import { useAuthStore } from "@/lib/store";
import { Card, CardHeader, CardContent, Badge } from "@/components/ui";
import { cn } from "@/lib/utils";

function SettingRow({ label, value, status }: { label: string; value: string; status?: "ok" | "warn" | "error" }) {
  return (
    <div className="flex items-center justify-between py-3 border-b border-border/40 last:border-0">
      <span className="text-xs text-muted-foreground">{label}</span>
      <div className="flex items-center gap-2">
        <span className="text-xs font-mono font-semibold">{value}</span>
        {status === "ok"    && <CheckCircle2 size={12} className="text-bull" />}
        {status === "warn"  && <AlertCircle  size={12} className="text-gold" />}
        {status === "error" && <AlertCircle  size={12} className="text-bear" />}
      </div>
    </div>
  );
}

export default function SettingsPage() {
  const username = useAuthStore((s) => s.username);
  const [backtesting, setBacktesting] = useState(false);
  const [detecting,   setDetecting]   = useState(false);
  const [refreshing,  setRefreshing]  = useState(false);
  const [symbols,     setSymbols]     = useState("");

  async function handleBacktest() {
    setBacktesting(true);
    try {
      const syms = symbols.trim()
        ? symbols.split(",").map((s) => s.trim().toUpperCase()).filter(Boolean)
        : [];

      if (syms.length > 0) {
        // runBacktest uses apiSlow (120s timeout) — backtest takes 15-45s per ticker
        const results = await Promise.allSettled(
          syms.map((ticker) => runBacktest(ticker))
        );
        const succeeded = results.filter((r) => r.status === "fulfilled").length;
        toast.success(`Backtest complete: ${succeeded}/${syms.length} tickers processed`);
      } else {
        // Run for all holdings using the slow client
        const { data: holdings } = await api.get("/trading/portfolio/holdings");
        if (!holdings?.length) {
          toast.error("No holdings found. Upload a portfolio CSV first.");
          return;
        }
        toast.info(`Starting backtest for ${holdings.length} holdings — this may take a few minutes`);
        // Fire-and-forget per ticker — each uses 120s timeout via runBacktest
        holdings.forEach((h: any) =>
          runBacktest(h.symbol).catch(() => null)
        );
        toast.success("Backtest jobs dispatched for all holdings");
      }
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Backtest failed to start");
    } finally {
      setBacktesting(false);
    }
  }

  async function handleDetectRegime() {
    setDetecting(true);
    try {
      // POST /api/dashboard/scan-now now:
      //   1. Calls detect_and_persist() first (fresh regime written to DB)
      //   2. Runs signal scan (reads fresh regime)
      //   3. Returns regime_label + regime_summary in the response body
      // No second GET needed — the regime is in the scan response.
      const { data } = await api.post("/dashboard/scan-now");
      toast.success(`Regime: ${data.regime_label ?? "Updated"}`, {
        description: data.regime_summary ?? `${data.signals_count ?? 0} signals generated`,
      });
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Detection failed");
    } finally {
      setDetecting(false);
    }
  }

  async function handleRefreshResearch() {
    setRefreshing(true);
    try {
      // FIXED: was POST /api/research/portfolio/refresh (did not exist)
      // Workaround: fetch holdings then trigger research for each
      const { data: holdings } = await api.get("/trading/portfolio/holdings");
      if (!holdings?.length) {
        toast.error("No holdings found. Upload a portfolio CSV first.");
        return;
      }
      // Trigger research refresh for first 5 holdings (cache invalidates on backend)
      const tickers = holdings.slice(0, 5).map((h: any) => h.symbol);
      await Promise.allSettled(
        tickers.map((t: string) => api.get(`/dashboard/research/${t}`))
      );
      toast.success(`Research refreshed for ${tickers.length} holdings`);
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Refresh failed");
    } finally {
      setRefreshing(false);
    }
  }

  const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

  return (
    <div className="space-y-5 animate-fade-in max-w-2xl">
      <div>
        <h1 className="font-display font-bold text-xl">Settings</h1>
        <p className="text-muted-foreground text-xs mt-0.5">System configuration and manual controls</p>
      </div>

      <Card>
        <CardHeader>
          <span className="text-xs font-semibold flex items-center gap-1.5">
            <Server size={13} className="text-muted-foreground" /> System Information
          </span>
        </CardHeader>
        <CardContent>
          <SettingRow label="Logged in as"   value={username || "—"}    status="ok" />
          <SettingRow label="Backend API"    value={API_URL}            status="ok" />
          <SettingRow label="Auth method"    value="bcrypt + OTP 2FA"   status="ok" />
          <SettingRow label="JWT algorithm"  value="HS256"              status="ok" />
          <SettingRow label="Regime refresh" value="Every 5 minutes"    status="ok" />
          <SettingRow label="News cache"     value="60 minutes"         status="ok" />
          <SettingRow label="Signal scan"    value="Every 5 minutes"    status="ok" />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <span className="text-xs font-semibold flex items-center gap-1.5">
            <Key size={13} className="text-muted-foreground" /> Environment Variables
          </span>
        </CardHeader>
        <CardContent>
          {[
            { label: "RESEND_API_KEY",     desc: "Email delivery for OTP",              url: "https://resend.com/api-keys" },
            { label: "NEWS_API_KEY",       desc: "NewsAPI.org free tier (100 req/day)", url: "https://newsapi.org" },
            { label: "HF_API_KEY",         desc: "Hugging Face Inference API",          url: "https://huggingface.co/settings/tokens" },
            { label: "GMAIL_APP_PASSWORD", desc: "Gmail SMTP for priority alerts",      url: "https://myaccount.google.com/apppasswords" },
            { label: "SCRAPERAPI_KEY",     desc: "Bypass Cloudflare for news scrapers", url: "https://scraperapi.com" },
          ].map(({ label, desc, url }) => (
            <div key={label} className="flex items-center justify-between py-3 border-b border-border/40 last:border-0">
              <div>
                <div className="text-xs font-mono font-semibold text-foreground">{label}</div>
                <div className="text-[10px] text-muted-foreground mt-0.5">{desc}</div>
              </div>
              <a href={url} target="_blank" rel="noopener noreferrer"
                className="text-[10px] text-primary hover:underline">Get key →</a>
            </div>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <span className="text-xs font-semibold flex items-center gap-1.5">
            <Zap size={13} className="text-muted-foreground" /> Manual Controls
          </span>
        </CardHeader>
        <CardContent className="space-y-5">
          <div className="flex items-start justify-between gap-4">
            <div>
              <div className="text-xs font-semibold">Run Regime Detection</div>
              <div className="text-[10px] text-muted-foreground mt-0.5">
                Force an immediate Nifty 50 regime classification + signal scan
              </div>
            </div>
            <button onClick={handleDetectRegime} disabled={detecting}
              className="shrink-0 px-4 py-2 rounded-xl border border-primary/30 bg-primary/10 text-primary text-xs font-semibold hover:bg-primary/20 transition-all disabled:opacity-60 flex items-center gap-1.5">
              {detecting ? <Loader2 size={11} className="animate-spin" /> : <Zap size={11} />}
              Detect Now
            </button>
          </div>

          <div className="h-px bg-border" />

          <div className="space-y-3">
            <div>
              <div className="text-xs font-semibold">Run 10-Year Backtest</div>
              <div className="text-[10px] text-muted-foreground mt-0.5">
                Runs all 8 strategies across your portfolio. Leave blank for all holdings.
              </div>
            </div>
            <input
              value={symbols}
              onChange={(e) => setSymbols(e.target.value)}
              placeholder="Comma-separated symbols (blank = entire portfolio)"
              className="w-full bg-muted/50 border border-border rounded-xl px-3 py-2 text-xs focus:outline-none focus:ring-1 focus:ring-primary/40 focus:border-primary/30 transition-all"
            />
            <button onClick={handleBacktest} disabled={backtesting}
              className="flex items-center gap-1.5 px-4 py-2 rounded-xl border border-border text-xs font-semibold text-muted-foreground hover:text-foreground hover:border-primary/30 transition-all disabled:opacity-60">
              {backtesting ? <Loader2 size={11} className="animate-spin" /> : <Database size={11} />}
              {backtesting ? "Running backtest…" : "Start Backtest"}
            </button>
          </div>

          <div className="h-px bg-border" />

          <div className="flex items-start justify-between gap-4">
            <div>
              <div className="text-xs font-semibold">Refresh Portfolio Research</div>
              <div className="text-[10px] text-muted-foreground mt-0.5">
                Fetches fresh news + sentiment for your top holdings
              </div>
            </div>
            <button onClick={handleRefreshResearch} disabled={refreshing}
              className="shrink-0 px-4 py-2 rounded-xl border border-border text-xs text-muted-foreground hover:text-foreground hover:border-primary/30 transition-all disabled:opacity-60 flex items-center gap-1.5">
              {refreshing ? <Loader2 size={11} className="animate-spin" /> : <Server size={11} />}
              Refresh
            </button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
