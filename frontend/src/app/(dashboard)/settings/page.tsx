"use client";
import { useState } from "react";
import {
  Settings, Server, Zap, Database, Key,
  AlertCircle, CheckCircle2, Loader2, Clock,
} from "lucide-react";
import { toast } from "sonner";
import { api, apiSlow, getErrorMessage } from "@/lib/api";
import { runBacktest } from "@/hooks/useData";
import { useAuthStore } from "@/lib/store";
import {
  Card, CardHeader, CardContent,
  ErrorBanner, ActionButton,
} from "@/components/ui";
import { cn } from "@/lib/utils";

function SettingRow({
  label, value, status,
}: { label: string; value: string; status?: "ok" | "warn" | "error" }) {
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
  const [btProgress,  setBtProgress]  = useState("");
  const [btError,     setBtError]     = useState("");

  const [detecting,   setDetecting]   = useState(false);
  const [detectMsg,   setDetectMsg]   = useState("");
  const [detectError, setDetectError] = useState("");

  const [refreshing,  setRefreshing]  = useState(false);
  const [refreshError,setRefreshError]= useState("");
  const [symbols,     setSymbols]     = useState("");

  // ── Run Backtest ─────────────────────────────────────────────────────────────
  // FIX: list_holdings now always returns a plain array. Previously when
  // no holdings existed the response was {"status":"success","data":[],"message":"..."}
  // so `holdings` was that wrapper object, not an array. `.length` was undefined,
  // the `!holdings?.length` check passed as truthy, and the error toast fired
  // even when holdings existed. Now we get `[]` and the guard works correctly.
  async function handleBacktest() {
    setBacktesting(true);
    setBtError("");
    setBtProgress("");

    try {
      const syms = symbols.trim()
        ? symbols.split(",").map((s) => s.trim().toUpperCase()).filter(Boolean)
        : [];

      if (syms.length > 0) {
        // Explicit tickers provided — run for those
        setBtProgress(`Running backtest for ${syms.length} ticker${syms.length > 1 ? "s" : ""}… (ETA: ~${syms.length * 30}s)`);
        const results = await Promise.allSettled(
          syms.map((ticker) => runBacktest(ticker))
        );
        const succeeded = results.filter((r) => r.status === "fulfilled").length;
        const failed    = results.filter((r) => r.status === "rejected").length;
        setBtProgress("");
        if (succeeded > 0) {
          toast.success(`✅ Backtest complete: ${succeeded}/${syms.length} tickers processed`, {
            description: failed > 0 ? `${failed} tickers failed — check ticker symbols` : undefined,
            duration: 6000,
          });
        }
        if (failed > 0 && succeeded === 0) {
          setBtError(`All ${failed} backtests failed. Check ticker symbols and backend health.`);
        }
      } else {
        // No tickers — run for entire portfolio
        // FIX: list_holdings returns plain array (not wrapped). No need to
        // unpack .data — the array IS the response body.
        const { data: holdings } = await api.get("/trading/portfolio/holdings");

        // holdings is now always an array ([] when empty, [{...},...] otherwise)
        if (!Array.isArray(holdings) || holdings.length === 0) {
          toast.error("No holdings found — upload a portfolio CSV first");
          setBacktesting(false);
          return;
        }

        setBtProgress(`Dispatching backtest for ${holdings.length} holdings… (ETA: ~${Math.ceil(holdings.length * 0.5)} minutes)`);
        toast.info(`⏳ Backtest started for ${holdings.length} holdings`, {
          description: "Each ticker takes 15–45s. Results appear in the Leaderboard when done.",
          duration: 8000,
        });

        let completed = 0;
        const total = holdings.length;
        await Promise.allSettled(
          holdings.map(async (h: any) => {
            try {
              await runBacktest(h.symbol);
              completed++;
              setBtProgress(`Completed ${completed}/${total} — ${h.symbol} done`);
            } catch {
              completed++;
            }
          })
        );
        setBtProgress("");
        toast.success(`✅ Backtest complete for all ${total} holdings`, {
          description: "Check the Leaderboard page for results",
          duration: 6000,
        });
      }
    } catch (err: any) {
      setBtProgress("");
      const msg = getErrorMessage(err);
      setBtError(`Backtest failed: ${msg}`);
      toast.error(`Backtest failed: ${msg}`);
    } finally {
      setBacktesting(false);
    }
  }

  // ── Detect Regime ─────────────────────────────────────────────────────────
  // FIX: Now uses `apiSlow` (120s timeout) instead of `api` (30s timeout).
  // The scan-now endpoint runs:
  //   Phase 1 — Regime detection:  ~20–40s
  //   Phase 2 — Full signal scan:  ~30–60s
  //   Total:                       up to ~80s
  // With the 30s `api` client, the request ALWAYS timed out before completing.
  // The frontend would show "Detection failed (timeout)" while the backend
  // actually finished successfully 10–20 seconds later. Now using apiSlow.
  async function handleDetectRegime() {
    setDetecting(true);
    setDetectError("");
    setDetectMsg("Detecting market regime… (ETA: ~30–80 seconds)");

    try {
      const { data } = await apiSlow.post("/dashboard/scan-now");
      setDetectMsg("");
      toast.success(`✅ Regime: ${data.regime_label ?? "Updated"}`, {
        description: data.regime_summary ?? `${data.signals_count ?? 0} signals generated`,
        duration: 6000,
      });
    } catch (err: any) {
      setDetectMsg("");
      const msg = getErrorMessage(err);
      setDetectError(`Detection failed: ${msg}`);
      toast.error(`Regime detection failed: ${msg}`);
    } finally {
      setDetecting(false);
    }
  }

  // ── Refresh Research ──────────────────────────────────────────────────────
  // FIX: Same holdings response fix as handleBacktest above.
  async function handleRefreshResearch() {
    setRefreshing(true);
    setRefreshError("");

    try {
      const { data: holdings } = await api.get("/trading/portfolio/holdings");

      // FIX: holdings is now always an array
      if (!Array.isArray(holdings) || holdings.length === 0) {
        toast.error("No holdings found — upload a portfolio CSV first");
        setRefreshing(false);
        return;
      }

      const tickers = holdings.slice(0, 5).map((h: any) => h.symbol);
      toast.info(`⏳ Refreshing research for: ${tickers.join(", ")}`, {
        description: "ETA: ~20–40 seconds per ticker",
        duration: 5000,
      });
      await Promise.allSettled(
        tickers.map((t: string) => api.get(`/dashboard/research/${t}`))
      );
      toast.success(`✅ Research refreshed for ${tickers.length} holdings`, {
        description: "News, sentiment, and forecasts are up to date",
        duration: 5000,
      });
    } catch (err: any) {
      const msg = getErrorMessage(err);
      setRefreshError(`Research refresh failed: ${msg}`);
      toast.error(`Refresh failed: ${msg}`);
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

      {/* System info */}
      <Card>
        <CardHeader>
          <span className="text-xs font-semibold flex items-center gap-1.5">
            <Server size={13} className="text-muted-foreground" /> System Information
          </span>
        </CardHeader>
        <CardContent>
          <SettingRow label="Logged in as"   value={username || "—"}  status="ok" />
          <SettingRow label="Backend API"    value={API_URL}           status="ok" />
          <SettingRow label="Auth method"    value="bcrypt + OTP 2FA"  status="ok" />
          <SettingRow label="Regime refresh" value="Every 5 minutes"   status="ok" />
          <SettingRow label="News cache"     value="60 minutes"        status="ok" />
          <SettingRow label="Signal scan"    value="Every 5 minutes"   status="ok" />
        </CardContent>
      </Card>

      {/* Env vars */}
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
              <a href={url} target="_blank" rel="noopener noreferrer" className="text-[10px] text-primary hover:underline">
                Get key →
              </a>
            </div>
          ))}
        </CardContent>
      </Card>

      {/* Manual controls */}
      <Card>
        <CardHeader>
          <span className="text-xs font-semibold flex items-center gap-1.5">
            <Zap size={13} className="text-muted-foreground" /> Manual Controls
          </span>
        </CardHeader>
        <CardContent className="space-y-5">

          {/* Detect regime — FIX: now uses apiSlow via handleDetectRegime */}
          <div className="space-y-2">
            <div className="flex items-start justify-between gap-4">
              <div>
                <div className="text-xs font-semibold">Run Regime Detection + Signal Scan</div>
                <div className="text-[10px] text-muted-foreground mt-0.5">
                  Force immediate Nifty 50 regime classification + full signal scan across all holdings.{" "}
                  <span className="text-gold">ETA: ~30–80 seconds.</span>
                </div>
              </div>
              <ActionButton
                onClick={handleDetectRegime}
                loading={detecting}
                loadingLabel="Detecting…"
                icon={<Zap size={11} />}
                variant="primary"
                size="sm"
              >
                Detect Now
              </ActionButton>
            </div>
            {detectMsg && (
              <div className="flex items-center gap-2 text-[10px] text-primary/80 bg-primary/5 rounded-lg px-3 py-2">
                <Clock size={10} />
                <span>{detectMsg}</span>
              </div>
            )}
            {detectError && (
              <ErrorBanner
                message={detectError}
                onDismiss={() => setDetectError("")}
                onRetry={handleDetectRegime}
              />
            )}
          </div>

          <div className="h-px bg-border" />

          {/* Backtest */}
          <div className="space-y-3">
            <div>
              <div className="text-xs font-semibold">Run 10-Year Backtest</div>
              <div className="text-[10px] text-muted-foreground mt-0.5">
                Fetches 10yr OHLCV data and runs 8 strategies per ticker.
                Leave blank to run for all holdings.{" "}
                <span className="text-gold">ETA: ~30–45s per ticker.</span>
              </div>
            </div>
            <input
              value={symbols}
              onChange={(e) => setSymbols(e.target.value)}
              placeholder="RELIANCE, TCS, INFY  (blank = entire portfolio)"
              className="w-full bg-muted/50 border border-border rounded-xl px-3 py-2 text-xs focus:outline-none focus:ring-1 focus:ring-primary/40 focus:border-primary/30 transition-all"
              disabled={backtesting}
            />
            <ActionButton
              onClick={handleBacktest}
              loading={backtesting}
              loadingLabel={btProgress || "Running backtest…"}
              icon={<Database size={11} />}
              variant="secondary"
              size="sm"
            >
              Start Backtest
            </ActionButton>
            {btProgress && !backtesting && (
              <p className="text-[10px] text-muted-foreground">{btProgress}</p>
            )}
            {btError && (
              <ErrorBanner
                message={btError}
                onDismiss={() => setBtError("")}
                onRetry={handleBacktest}
              />
            )}
          </div>

          <div className="h-px bg-border" />

          {/* Refresh research */}
          <div className="space-y-2">
            <div className="flex items-start justify-between gap-4">
              <div>
                <div className="text-xs font-semibold">Refresh Portfolio Research</div>
                <div className="text-[10px] text-muted-foreground mt-0.5">
                  Fetches fresh news + FinBERT sentiment for top 5 holdings.{" "}
                  <span className="text-gold">ETA: ~20–40s per ticker.</span>
                </div>
              </div>
              <ActionButton
                onClick={handleRefreshResearch}
                loading={refreshing}
                loadingLabel="Refreshing…"
                icon={<Server size={11} />}
                variant="secondary"
                size="sm"
              >
                Refresh
              </ActionButton>
            </div>
            {refreshError && (
              <ErrorBanner
                message={refreshError}
                onDismiss={() => setRefreshError("")}
                onRetry={handleRefreshResearch}
              />
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
