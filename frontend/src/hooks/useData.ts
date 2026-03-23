import useSWR from "swr";
import { fetcher, api, apiSlow } from "@/lib/api";
import type {
  BudgetStatus, DashboardPayload, FinalSignal, FullResearch,
  Holding, InceptionInfo, NewsArticle, Notification,
  RegimeSnapshot, StrategyResult, PaperTrade,
} from "@/types";

const REFRESH = 5 * 60 * 1000;

// ── Dashboard ─────────────────────────────────────────────────────────────────
export function useDashboard() {
  return useSWR<DashboardPayload>("/dashboard/", fetcher, {
    refreshInterval: REFRESH,
    revalidateOnFocus: true,
  });
}

// ── Regime ────────────────────────────────────────────────────────────────────
export function useLatestRegime() {
  return useSWR<RegimeSnapshot>("/dashboard/regime", fetcher, {
    refreshInterval: REFRESH,
  });
}

// ── Signals ───────────────────────────────────────────────────────────────────
export function useLatestSignals(signal?: string, minConf?: number) {
  const params = new URLSearchParams();
  if (signal)          params.set("signal",         signal);
  if (minConf != null) params.set("min_confidence", String(minConf));
  const qs  = params.toString();
  const url = `/dashboard/signals${qs ? "?" + qs : ""}`;
  return useSWR<FinalSignal[]>(url, fetcher, { refreshInterval: REFRESH });
}

export function useTickerSignals(ticker: string | null) {
  return useSWR<FinalSignal[]>(
    ticker ? `/dashboard/signals/${ticker}` : null,
    fetcher,
    { refreshInterval: REFRESH }
  );
}

// ── Holdings ──────────────────────────────────────────────────────────────────
export function useHoldings() {
  return useSWR<Holding[]>("/trading/portfolio/holdings", fetcher, {
    refreshInterval: 60_000,
  });
}

// ── Research ──────────────────────────────────────────────────────────────────
export function useResearch(ticker: string | null) {
  return useSWR<FullResearch>(
    ticker ? `/dashboard/research/${ticker}` : null,
    fetcher,
    { refreshInterval: 60 * 60 * 1000 }
  );
}

// ── News Articles ─────────────────────────────────────────────────────────────
// KEY FIX: Gate on `researchTicker === ticker`, NOT just `!!research`.
// When the user clicks Analyse with a new ticker, `research` still holds the
// previous ticker's data object (truthy) for one render cycle. If we gate only
// on `!!research`, useNews fires for the NEW ticker before NewsService.analyse()
// has run — articles don't exist yet → empty feed on every new search.
// By comparing `research.ticker === ticker` we ensure the research object
// actually belongs to the current ticker before fetching its articles.
export function useNews(ticker: string | null, researchTicker?: string | null) {
  const ready = ticker && researchTicker === ticker;
  return useSWR<NewsArticle[]>(
    ready ? `/dashboard/research/${ticker}/articles` : null,
    fetcher,
    { refreshInterval: 60 * 60 * 1000 }
  );
}

// ── Backtest leaderboard ──────────────────────────────────────────────────────
export function useLeaderboard(allQualities = true) {
  const url = `/dashboard/leaderboard?top_n=100${allQualities ? "&all_qualities=true" : ""}`;
  return useSWR<StrategyResult[]>(url, fetcher, {
    refreshInterval: 5 * 60 * 1000,
  });
}

// ── Paper Trades ──────────────────────────────────────────────────────────────
export function usePaperTrades(tradeStatus?: string) {
  const url = tradeStatus
    ? `/trading/paper/trades?status=${tradeStatus}`
    : "/trading/paper/trades";
  return useSWR<PaperTrade[]>(url, fetcher, { refreshInterval: 30_000 });
}

// ── Budget ────────────────────────────────────────────────────────────────────
export function useBudget() {
  return useSWR<BudgetStatus>("/trading/paper/budget", fetcher, {
    refreshInterval: 30_000,
  });
}

// ── OHLCV ─────────────────────────────────────────────────────────────────────
export function useOHLCV(ticker: string | null, interval = "daily", limit = 500) {
  return useSWR(
    ticker ? `/market/ohlcv/${ticker}?limit=${limit}` : null,
    (url: string) => api.get(url).then((r) => r.data.bars as import("@/types").OHLCVBar[]),
    { refreshInterval: REFRESH }
  );
}

// ── Inception / Data Quality ──────────────────────────────────────────────────
export function useInception(ticker: string | null) {
  return useSWR<InceptionInfo>(
    ticker ? `/market/inception/${ticker}` : null,
    fetcher,
    { refreshInterval: 60 * 60 * 1000 }
  );
}

// ── Notifications — real data from alert_dispatch_log ────────────────────────
// GET /api/dashboard/notifications → last 20 alerts fired by the scheduler
// Refreshes every 60s so new alerts surface without page reload.
export function useNotifications() {
  return useSWR<Notification[]>("/dashboard/notifications", fetcher, {
    refreshInterval: 60_000,
    revalidateOnFocus: true,
  });
}

// ── Manual triggers ───────────────────────────────────────────────────────────
export async function triggerScanNow() {
  const { data } = await api.post("/dashboard/scan-now");
  return data;
}

// Backtest is slow (10yr data fetch + 8 strategies) — use 120s timeout client
export async function runBacktest(ticker: string) {
  const { data } = await apiSlow.get(`/trading/backtest/run/${ticker}`);
  return data;
}

export async function fetchBestStrategy(ticker: string) {
  const { data } = await api.get(`/dashboard/signals/${ticker}`);
  return data;
}
