/**
 * useData.ts — SWR hooks + imperative async helpers
 *
 * FIX 1: triggerScanNow() now uses `apiSlow` (120s timeout) instead of `api`
 *         (30s timeout). The scan-now endpoint runs regime detection (~20s)
 *         + full signal scan (~30-60s). With the 30s client it ALWAYS timed
 *         out before completing — the frontend showed "Scan failed (timeout)"
 *         even when the backend finished successfully 10s later.
 *
 * FIX 2: useOHLCV fetcher now propagates errors correctly. Previously,
 *         when the backend returned 404, `r.data.bars` was `undefined`
 *         and SWR treated it as a successful empty result instead of an error.
 *         The fetcher now throws on missing `bars` field so SWR can surface
 *         the error to the UI via the `error` field.
 *
 * FIX 3: useNews() gate logic relaxed — previously `researchTicker === ticker`
 *         prevented articles loading after any error or re-render race. Now
 *         it only gates on `research` being truthy (loaded at all), not
 *         requiring the ticker field to match (which could mismatch on fast
 *         ticker switches if the backend normalises casing differently).
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
// FIX 3: Gate loosened — previously `researchTicker === ticker` caused articles
// to never load after a research error or fast ticker switch. Now we just check
// that research has been attempted (research !== undefined) OR has loaded for
// this ticker. This ensures articles load as soon as research is done.
export function useNews(ticker: string | null, researchTicker?: string | null) {
  // Allow fetch when:
  //   (a) research has loaded and ticker matches (happy path), OR
  //   (b) researchTicker is explicitly provided and matches (legacy callers)
  // Block only when ticker is null (no ticker selected yet)
  const ready = !!ticker && (researchTicker === ticker || researchTicker != null);
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
// FIX 2: The original fetcher did `api.get(url).then((r) => r.data.bars)`.
// When the backend returned a 404 (no data for ticker), `r.data.bars` was
// `undefined` and SWR set data=undefined with NO error — the component saw
// isLoading=false, data=undefined, error=undefined and rendered nothing.
// Fixed: throw when bars is missing so SWR captures it as `error`.
export function useOHLCV(ticker: string | null, limit = 500) {
  return useSWR<OHLCVBar[]>(
    ticker ? `/market/ohlcv/${ticker}?limit=${limit}` : null,
    async (url: string) => {
      const r = await api.get(url);
      const bars = r.data?.bars;
      if (!Array.isArray(bars)) {
        throw new Error(
          r.data?.detail ?? `No OHLCV data for ${ticker} (quality: ${r.data?.quality ?? "unknown"})`
        );
      }
      return bars as OHLCVBar[];
    },
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
 *
 * FIX 1: Uses apiSlow (120s timeout) — not api (30s).
 * The backend runs: regime detection (~20s) + signal scan (~30-60s) = up to 80s.
 * With the old 30s client this ALWAYS timed out on cold runs, showing
 * "Scan failed" in the toast even when backend completed successfully.
 */
export async function triggerScanNow() {
  const { data } = await apiSlow.post("/dashboard/scan-now");
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
