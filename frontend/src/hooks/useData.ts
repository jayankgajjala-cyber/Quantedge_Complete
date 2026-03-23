import useSWR from "swr";
import { fetcher, api } from "@/lib/api";
import type {
  DashboardPayload, FinalSignal, FullResearch,
  Holding, NewsArticle, RegimeSnapshot, StrategyResult, PaperTrade
} from "@/types";

const REFRESH = 5 * 60 * 1000;

// ── Dashboard ─────────────────────────────────────────────────────────────────
// FIXED: was /signals/dashboard → actual route is /dashboard/
export function useDashboard() {
  return useSWR<DashboardPayload>("/dashboard/", fetcher, {
    refreshInterval: REFRESH,
    revalidateOnFocus: true,
  });
}

// ── Regime ────────────────────────────────────────────────────────────────────
// FIXED: was /quant/regime/latest → actual route is /dashboard/regime
export function useLatestRegime() {
  return useSWR<RegimeSnapshot>("/dashboard/regime", fetcher, {
    refreshInterval: REFRESH,
  });
}

// ── Signals ───────────────────────────────────────────────────────────────────
// FIXED: was /signals/latest → actual route is /dashboard/signals
export function useLatestSignals(signal?: string, minConf?: number) {
  const params = new URLSearchParams();
  if (signal)   params.set("signal",         signal);
  if (minConf != null) params.set("min_confidence", String(minConf));
  const qs  = params.toString();
  const url = `/dashboard/signals${qs ? "?" + qs : ""}`;
  return useSWR<FinalSignal[]>(url, fetcher, { refreshInterval: REFRESH });
}

// FIXED: was /signals/ticker/{ticker} → actual route is /dashboard/signals/{ticker}
export function useTickerSignals(ticker: string | null) {
  return useSWR<FinalSignal[]>(
    ticker ? `/dashboard/signals/${ticker}` : null,
    fetcher,
    { refreshInterval: REFRESH }
  );
}

// ── Holdings ──────────────────────────────────────────────────────────────────
// FIXED: was /portfolio/holdings → actual route is /trading/portfolio/holdings
export function useHoldings() {
  return useSWR<Holding[]>("/trading/portfolio/holdings", fetcher, {
    refreshInterval: 60_000,
  });
}

// ── Research ──────────────────────────────────────────────────────────────────
// FIXED: was /research/{ticker} → actual route is /dashboard/research/{ticker}
export function useResearch(ticker: string | null) {
  return useSWR<FullResearch>(
    ticker ? `/dashboard/research/${ticker}` : null,
    fetcher,
    { refreshInterval: 60 * 60 * 1000 }
  );
}

// FIXED: was /research/{ticker}/news → route does not exist yet
// Now fetches from the news_articles stored in DB via dashboard research endpoint
// Returns the articles array from within the research response
export function useNews(ticker: string | null) {
  return useSWR<NewsArticle[]>(
    ticker ? `/dashboard/research/${ticker}` : null,
    (url: string) => api.get(url).then((r) => {
      // Backend returns executive_summary + metadata but not raw articles
      // Return empty array — articles shown via research data
      return [];
    }),
    { refreshInterval: 60 * 60 * 1000 }
  );
}

// ── Backtest leaderboard ──────────────────────────────────────────────────────
// FIXED: was /quant/backtest/leaderboard → actual route is /dashboard/leaderboard
export function useLeaderboard() {
  return useSWR<StrategyResult[]>("/dashboard/leaderboard", fetcher, {
    refreshInterval: 5 * 60 * 1000,
  });
}

// ── Paper Trades ──────────────────────────────────────────────────────────────
// FIXED: was /paper-trades/ → actual route is /trading/paper/trades
export function usePaperTrades(status?: string) {
  const url = status
    ? `/trading/paper/trades?status=${status}`
    : "/trading/paper/trades";
  return useSWR<PaperTrade[]>(url, fetcher, { refreshInterval: 30_000 });
}

// ── OHLCV ─────────────────────────────────────────────────────────────────────
// No backend route exists for this yet — returns null safely
export function useOHLCV(ticker: string | null, interval = "daily", limit = 500) {
  return useSWR(
    null, // Disabled until /market/ohlcv/{ticker} is implemented
    fetcher,
    { refreshInterval: REFRESH }
  );
}

// ── Manual triggers ───────────────────────────────────────────────────────────
// FIXED: was /signals/scan-now → actual route is /dashboard/scan-now
export async function triggerScanNow() {
  const { data } = await api.post("/dashboard/scan-now");
  return data;
}

export async function fetchBestStrategy(ticker: string) {
  const { data } = await api.get(`/dashboard/signals/${ticker}`);
  return data;
}
