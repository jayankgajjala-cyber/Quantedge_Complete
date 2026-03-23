/**
 * useData.ts — SWR hooks + imperative async helpers
 *
 * SWR hooks surface { data, error, isLoading, mutate } for every endpoint.
 * The `error` field lets pages render inline ErrorBanner instead of silent empties.
 * Imperative helpers (triggerScanNow, runBacktest) are used by buttons.
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
// ETA note: first-time analysis triggers FinBERT + BART (~20–40s).
// Cache is 60 min so subsequent loads are instant.
export function useResearch(ticker: string | null) {
  return useSWR<FullResearch>(
    ticker ? `/dashboard/research/${ticker}` : null,
    fetcher,
    { refreshInterval: REFRESH_1H }
  );
}

// ── News Articles ─────────────────────────────────────────────────────────────
// Gated on researchTicker === ticker so articles aren't fetched before
// NewsService.analyse() has run for the current ticker.
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
 * POST /api/dashboard/scan-now
 * Phase 1: detect_and_persist() fresh regime
 * Phase 2: run full signal scan
 * Returns { signals_count, regime_label, regime_summary }
 */
export async function triggerScanNow() {
  const { data } = await api.post("/dashboard/scan-now");
  return data;
}

/**
 * GET /api/trading/backtest/run/{ticker}
 * Uses 120s timeout — backtest fetches 10yr data + runs 8 strategies.
 * ETA: ~15–45s per ticker on cold cache, ~5s on warm cache.
 */
export async function runBacktest(ticker: string) {
  const { data } = await apiSlow.get(`/trading/backtest/run/${ticker}`);
  return data;
}

export async function fetchBestStrategy(ticker: string) {
  const { data } = await api.get(`/dashboard/signals/${ticker}`);
  return data;
}
