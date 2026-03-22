// ── Auth ─────────────────────────────────────────────────────────────────────

export interface LoginResponse {
  message: string;
  otp_sent: boolean;
  email_hint: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_at: string;
  username: string;
}

// ── Holdings ─────────────────────────────────────────────────────────────────

export interface Holding {
  id: number;
  symbol: string;
  exchange: string;
  quantity: number;
  average_price: number;
  current_price: number | null;
  pnl: number | null;
  pnl_pct: number | null;
  sector: string | null;
  data_quality: string;
  uploaded_at: string;
}

// ── Market Regime ─────────────────────────────────────────────────────────────

export interface RegimeSnapshot {
  id: number;
  timestamp: string;
  index_symbol: string;
  regime_label: string;
  adx_14: number | null;
  atr_14: number | null;
  atr_percentile: number | null;
  ema_200: number | null;
  close_price: number | null;
  bb_upper: number | null;
  bb_lower: number | null;
  slope_20d: number | null;
  price_vs_ema: string | null;
  regime_summary: string | null;
  confidence_score: number | null;
}

// ── Signals ───────────────────────────────────────────────────────────────────

export interface FinalSignal {
  id: number;
  scan_id: string;
  ticker: string;
  regime: string;
  selected_strategy: string;
  signal: "BUY" | "SELL" | "HOLD" | "CASH";
  confidence: number;
  entry_price: number | null;
  stop_loss: number | null;
  target_1: number | null;
  target_2: number | null;
  risk_reward_ratio: number | null;
  adx: number | null;
  rsi: number | null;
  volume_ratio: number | null;
  agreeing_strategies: number | null;
  total_strategies_run: number | null;
  agreement_bonus: number | null;
  bias_warning: boolean | null;
  bias_message: string | null;
  reason: string;
  status: string;
  generated_at: string;
  expires_at: string | null;
}

export interface DashboardPayload {
  regime: string;
  regime_confidence: number;
  total_buy_signals: number;
  total_sell_signals: number;
  total_hold_signals: number;
  top_signals: FinalSignal[];
  bias_warning: boolean;
  bias_message: string;
  last_scan_at: string | null;
}

// ── Research ──────────────────────────────────────────────────────────────────

export interface NewsArticle {
  title: string;
  source_name: string;
  published_at: string;
  url: string | null;
  description: string | null;
  sentiment_score: number | null;
  sentiment_label: string | null;
}

export interface FullResearch {
  ticker: string;
  avg_sentiment_score: number | null;
  sentiment_label: string | null;
  sentiment_std_dev: number | null;
  conflict_detected: boolean;
  conflict_detail: string | null;
  executive_summary: string[];
  forecast_outlook: string | null;
  forecast_direction: string | null;
  forecast_confidence: number | null;
  price_slope_annual: number | null;
  revenue_cagr: number | null;
  articles_analysed: number;
  positive_count: number;
  neutral_count: number;
  negative_count: number;
  insufficient_coverage: boolean;
  coverage_message: string | null;
  analysed_at: string;
}
// ── Strategy Performance ──────────────────────────────────────────────────────

export interface StrategyResult {
  stock_ticker: string;
  strategy_name: string;
  sharpe_ratio: number | null;
  cagr: number | null;
  win_rate: number | null;
  max_drawdown: number | null;
  total_trades: number | null;
  years_of_data: number | null;
  data_quality: string;
}

// ── Paper Trades ──────────────────────────────────────────────────────────────

export interface PaperTrade {
  id: number;
  symbol: string;
  direction: "BUY" | "SELL";
  quantity: number;
  entry_price: number;
  exit_price: number | null;
  stop_loss: number | null;
  target: number | null;
  status: "OPEN" | "CLOSED";
  strategy_name: string | null;
  pnl: number | null;
  pnl_pct: number | null;
  entry_time: string;
  exit_time: string | null;
}

// ── OHLCV ────────────────────────────────────────────────────────────────────

export interface OHLCVBar {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}
