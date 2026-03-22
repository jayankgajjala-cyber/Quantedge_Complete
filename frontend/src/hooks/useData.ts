import useSWR from "swr";
import { fetcher, api } from "@/lib/api";
import type {
  DashboardPayload, FinalSignal, FullResearch,
  Holding, NewsArticle, RegimeSnapshot, StrategyResult, PaperTrade
} from "@/types";

const REFRESH = 5 * 60 * 1000; // 5 minutes — matches backend scan cadence

// ── Dashboard ─────────────────────────────────────────────────────────────────
export function useDashboard() {
  return useSWR<DashboardPayload>("/signals/dashboard", fetcher, {
    refreshInterval: REFRESH,
    revalidateOnFocus: true,
  });
}

// ── Regime ────────────────────────────────────────────────────────────────────
export function useLatestRegime() {
  return useSWR<RegimeSnapshot>("/quant/regime/latest", fetcher, {
    refreshInterval: REFRESH,
  });
}

// ── Signals ───────────────────────────────────────────────────────────────────
export function useLatestSignals(signal?: string, minConf?: number) {
  const params = new URLSearchParams();
  if (signal) params.set("signal_type", signal);
  if (minConf != null) params.set("min_confidence", String(minConf));
  const url = `/signals/latest?${params.toString()}`;
  return useSWR<FinalSignal[]>(url, fetcher, { refreshInterval: REFRESH });
}

export function useTickerSignals(ticker: string | null) {
  return useSWR<FinalSignal[]>(
    ticker ? `/signals/ticker/${ticker}` : null,
    fetcher,
    { refreshInterval: REFRESH }
  );
}

// ── Holdings ──────────────────────────────────────────────────────────────────
export function useHoldings() {
  return useSWR<Holding[]>("/portfolio/holdings", fetcher, {
    refreshInterval: 60_000,
  });
}

// ── Research ──────────────────────────────────────────────────────────────────
export function useResearch(ticker: string | null) {
  return useSWR<FullResearch>(
    ticker ? `/research/${ticker}` : null,
    fetcher,
    { refreshInterval: 60 * 60 * 1000 }  // 60 min cache matches backend
  );
}

export function useNews(ticker: string | null) {
  return useSWR<NewsArticle[]>(
    ticker ? `/research/${ticker}/news?limit=30` : null,
    fetcher,
    { refreshInterval: 60 * 60 * 1000 }
  );
}

// ── Backtest leaderboard ──────────────────────────────────────────────────────
export function useLeaderboard() {
  return useSWR<StrategyResult[]>("/quant/backtest/leaderboard", fetcher, {
    refreshInterval: 5 * 60 * 1000,
  });
}

// ── Paper Trades ──────────────────────────────────────────────────────────────
export function usePaperTrades(status?: string) {
  const url = status ? `/paper-trades/?status=${status}` : "/paper-trades/";
  return useSWR<PaperTrade[]>(url, fetcher, { refreshInterval: 30_000 });
}

// ── OHLCV ─────────────────────────────────────────────────────────────────────
export function useOHLCV(ticker: string | null, interval = "daily", limit = 500) {
  return useSWR(
    ticker ? `/market-data/${ticker}?interval=${interval}&limit=${limit}` : null,
    fetcher,
    { refreshInterval: REFRESH }
  );
}

// ── Manual trigger: on-demand scan ───────────────────────────────────────────
export async function triggerScanNow() {
  const { data } = await api.post("/signals/scan-now");
  return data;
}

export async function fetchBestStrategy(ticker: string) {
  const { data } = await api.get(`/signals/best-strategy/${ticker}`);
  return data;
}
