/**
 * useData.ts — SWR hooks + imperative async helpers
 */
import useSWR from "swr";
import { fetcher, api, apiSlow } from "@/lib/api";
import type {
  BudgetStatus, DashboardPayload, FinalSignal, FullResearch,
  Holding, InceptionInfo, NewsArticle, Notification,
  OHLCVBar, RegimeSnapshot, StrategyResult, PaperTrade,
} from "@/types";

const REFRESH_5M  = 5 * 60 * 1000;
const REFRESH_1H  = 60 * 60 * 1000;
const REFRESH_30S = 30_000;
const REFRESH_1M  = 60_000;

// ── Dashboard ─────────────────────────────────────────────────────────────────
export function useDashboard() {
  return useSWR<DashboardPayload>("/dashboard/", fetcher, {
    refreshInterval: REFRESH_5M,
    revalidateOnFocus: true,
  });
}

// ── Regime ────────────────────────────────────────────────────────────────────
export function useLatestRegime() {
  return useSWR<RegimeSnapshot>("/dashboard/regime", fetcher, {
    refreshInterval: REFRESH_5M,
  });
}

// ── Signals ───────────────────────────────────────────────────────────────────
export function useLatestSignals(signal?: string, minConf?: number) {
  const params = new URLSearchParams();
  if (signal)          params.set("signal",         signal);
  if (minConf != null) params.set("min_confidence", String(minConf));
  const qs  = params.toString();
  const url = `/dashboard/signals${qs ? "?" + qs : ""}`;
  return useSWR<FinalSignal[]>(url, fetcher, { refreshInterval: REFRESH_5M });
}

export function useTickerSignals(ticker: string | null) {
  return useSWR<FinalSignal[]>(
    ticker ? `/dashboard/signals/${ticker}` : null,
    fetcher,
    { refreshInterval: REFRESH_5M }
  );
}

// ── Holdings ──────────────────────────────────────────────────────────────────
export function useHoldings() {
  return useSWR<Holding[]>("/trading/portfolio/holdings", fetcher, {
    refreshInterval: REFRESH_1M,
  });
}

// ── Research ──────────────────────────────────────────────────────────────────
export function useResearch(ticker: string | null) {
  return useSWR<FullResearch>(
    ticker ? `/dashboard/research/${ticker}` : null,
    fetcher,
    { refreshInterval: REFRESH_1H }
  );
}

// ── News Articles ─────────────────────────────────────────────────────────────
export function useNews(ticker: string | null, researchTicker?: string | null) {
  const ready = ticker && researchTicker === ticker;
  return useSWR<NewsArticle[]>(
    ready ? `/dashboard/research/${ticker}/articles` : null,
    fetcher,
    { refreshInterval: REFRESH_1H }
  );
}

// ── Leaderboard ───────────────────────────────────────────────────────────────
export function useLeaderboard(allQualities = true) {
  const url = `/dashboard/leaderboard?top_n=100${allQualities ? "&all_qualities=true" : ""}`;
  return useSWR<StrategyResult[]>(url, fetcher, {
    refreshInterval: REFRESH_5M,
  });
}

// ── Backtests ─────────────────────────────────────────────────────────────────
export function useBacktests(ticker?: string) {
  const url = ticker
    ? `/dashboard/backtests?ticker=${encodeURIComponent(ticker)}`
    : "/dashboard/backtests";
  return useSWR<any[]>(url, fetcher, { refreshInterval: REFRESH_1H });
}

// ── Paper Trades ──────────────────────────────────────────────────────────────
export function usePaperTrades(tradeStatus?: string) {
  const url = tradeStatus
    ? `/trading/paper/trades?status=${tradeStatus}`
    : "/trading/paper/trades";
  return useSWR<PaperTrade[]>(url, fetcher, { refreshInterval: REFRESH_30S });
}

// ── Budget ────────────────────────────────────────────────────────────────────
export function useBudget() {
  return useSWR<BudgetStatus>("/trading/paper/budget", fetcher, {
    refreshInterval: REFRESH_30S,
  });
}

// ── OHLCV ─────────────────────────────────────────────────────────────────────
export function useOHLCV(ticker: string | null, limit = 500) {
  return useSWR<OHLCVBar[]>(
    ticker ? `/market/ohlcv/${ticker}?limit=${limit}` : null,
    (url: string) => api.get(url).then((r) => r.data.bars),
    { refreshInterval: REFRESH_5M }
  );
}

// ── Inception / Data Quality ──────────────────────────────────────────────────
export function useInception(ticker: string | null) {
  return useSWR<InceptionInfo>(
    ticker ? `/market/inception/${ticker}` : null,
    fetcher,
    { refreshInterval: REFRESH_1H }
  );
}

// ── Notifications ─────────────────────────────────────────────────────────────
export function useNotifications() {
  return useSWR<Notification[]>("/dashboard/notifications", fetcher, {
    refreshInterval: REFRESH_1M,
    revalidateOnFocus: true,
  });
}

// ── Imperative helpers ────────────────────────────────────────────────────────

/**
 * POST /api/dashboard/scan-now  →  202 Accepted { scan_id }
 * Then polls GET /api/dashboard/scan-status/{scan_id} every 3s until done.
 * Resolves with the final status payload (signals_count, regime_label …).
 */
export async function triggerScanNow(
  onProgress?: (msg: string) => void,
  timeoutMs = 120_000,
): Promise<any> {
  const { data: accepted } = await api.post("/dashboard/scan-now");

  // Short-circuit: empty portfolio or immediate result
  if (accepted.status === "empty" || accepted.status === "success") {
    return accepted;
  }

  const { scan_id } = accepted;
  if (!scan_id) return accepted;

  onProgress?.("Scan queued — running regime detection…");

  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 3_000));
    try {
      const { data: job } = await api.get(`/dashboard/scan-status/${scan_id}`);
      if (job.status === "done" || job.status === "error") {
        if (job.status === "error") throw new Error(job.message || "Scan failed");
        return job;
      }
      if (job.status === "running") onProgress?.("Running signal scan across holdings…");
    } catch (err: any) {
      // ignore transient poll errors, keep polling
      if (err?.response?.status === 404) throw err;
    }
  }
  throw new Error("Scan timed out — check Railway backend logs.");
}

/**
 * GET /api/trading/backtest/run/{ticker}
 * Uses 120s timeout — backtest fetches 10yr data + runs 8 strategies.
 */
export async function runBacktest(ticker: string) {
  const { data } = await apiSlow.get(`/trading/backtest/run/${ticker}`);
  return data;
}

export async function fetchBestStrategy(ticker: string) {
  const { data } = await api.get(`/dashboard/signals/${ticker}`);
  return data;
}
