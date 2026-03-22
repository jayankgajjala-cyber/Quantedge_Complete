# Quantedge — Technical Architecture Reference

## Module Communication Map

```
┌─────────────────────────────────────────────────────────────────────┐
│  APScheduler (master_scheduler.py / inline in main.py)              │
│  Every 5 min (market hours):  heartbeat_job()                       │
│  Every 5 min (always):        regime_detector job                   │
│  Every 60 min (always):       research_refresh job                  │
│  Saturday 01:00 UTC:          weekly_backtest job                   │
│  Saturday 18:00 UTC:          weekly_report job                     │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
          ┌────────────────▼────────────────┐
          │        heartbeat_job()          │
          │   (skips if market is closed)   │
          └─────────────┬───────────────────┘
                        │
         ┌──────────────▼──────────────────────────────┐
         │           SignalEngine.run_scan()            │
         │                                              │
         │  For each holding ticker:                    │
         │  ┌─────────────────────────────────────┐    │
         │  │ 1. Read market_regime table          │    │
         │  │    └─ RegimeService writes this      │    │
         │  │                                      │    │
         │  │ 2. QuantService.get_best_strategy()  │    │
         │  │    └─ Queries strategy_performance   │    │
         │  │    └─ Returns highest Sharpe/WinRate │    │
         │  │                                      │    │
         │  │ 3. _generate_signals(df, strategy)   │    │
         │  │    └─ Pure NumPy/Pandas signals      │    │
         │  │                                      │    │
         │  │ 4. _validate_and_size()              │    │
         │  │    └─ Volume gate (1.5×)             │    │
         │  │    └─ R:R gate (≥1.5)               │    │
         │  │    └─ ATR-based SL/T1/T2             │    │
         │  │                                      │    │
         │  │ 5. Agreement Factor                  │    │
         │  │    └─ +20 if ≥3 strategies agree     │    │
         │  │    └─ -10 if ≥80% HOLD (bias)        │    │
         │  │                                      │    │
         │  │ 6. *** SENTIMENT GATE ***            │    │
         │  │    NewsService.analyse(ticker)       │    │
         │  │    └─ FinBERT per headline           │    │
         │  │    └─ If score < -0.6 AND BUY:       │    │
         │  │       signal = HOLD/CAUTION          │    │
         │  │    └─ If score > +0.6 AND HOLD:      │    │
         │  │       signal = WATCH                 │    │
         │  │                                      │    │
         │  │ 7. FinalSignal → DB → JSON           │    │
         │  └─────────────────────────────────────┘    │
         └──────────────────────────────────────────────┘
                        │
         ┌──────────────▼──────────────────┐
         │      Alert Dispatcher           │
         │  conf ≥ 85% + not HOLD/CASH     │
         │  + 3/day cap + 60-min dedup     │
         │  → Gmail SMTP HTML email        │
         └──────────────┬──────────────────┘
                        │
         ┌──────────────▼──────────────────┐
         │      SL / Target Monitor        │
         │  yfinance live price            │
         │  BUY: price ≤ SL → SL_HIT      │
         │  BUY: price ≥ target → TGT_HIT  │
         │  → Auto-close + VirtualLedger   │
         └─────────────────────────────────┘
```

## Database Schema Overview

```
holdings              historical_data         strategy_performance
  id                    id                      id
  symbol ────────┐      symbol                  stock_ticker
  quantity       │      interval                strategy_name
  average_price  │      timestamp               sharpe_ratio
  data_quality   │      open/high/low/close      cagr
                 │      volume                  win_rate
                 └──────holding_id              max_drawdown
                                                data_quality

market_regime          final_signals            news_analysis
  id                    id                       id
  timestamp             scan_id                  ticker
  regime_label          ticker ─────────────►   avg_sentiment_score
  adx_14                regime                   executive_summary
  atr_percentile        selected_strategy        conflict_detected
  confidence_score      signal                   forecast_outlook
                        confidence               cache_expires_at
                        sentiment_score  ◄────── is_cache_valid
                        sentiment_override

paper_trades           virtual_ledger           budget_cycles
  id                    id                       id
  symbol                trade_id ─────────────►  year / month
  direction             entry_type               total_budget
  quantity              price                    allocated
  entry_price           commission               remaining (property)
  stop_loss             realised_pnl             realised_pnl
  target
  status

alert_dispatch_log
  id
  ticker
  signal_type
  confidence
  sent_at          (used for 3/day cap + 60-min dedup)
```

## Parquet Cache Strategy

OHLCV data is cached as `.parquet` files to prevent API rate limiting:

```
data/cache/parquet/
├── IDX_NSEI_daily.parquet     ← Nifty 50 for regime detection
├── RELIANCE_10yr.parquet      ← 10yr daily bars for backtest
├── TCS_10yr.parquet
├── SBIN_10yr.parquet
└── ...
```

Cache freshness rules:
- `_IDX_NSEI_daily.parquet`: stale after 18 hours
- `*_10yr.parquet`: stale after 24 hours

On cache miss, yfinance is called and the result is immediately persisted.

## Confidence Score Formula

```
base_confidence     = f(sharpe_ratio, win_rate)  [20–85]
regime_fit_bonus    = 0–10  (strategy matches regime)
agreement_bonus     = +20   (if ≥3 strategies agree)
bias_penalty        = -10   (if ≥80% of scan is HOLD)

final = clamp(base + regime_fit + agreement - bias, 5, 100)
```

## Error Isolation

Every heartbeat step is independently wrapped in `try/except`:

```python
# Step 2 failure never prevents Step 3
try:
    scan_results = await run_signal_scan()
except Exception as exc:
    logger.error("Step 2 failed: %s", exc)

# Step 3 still runs
try:
    await dispatch_alerts(scan_results)
except Exception as exc:
    logger.error("Step 3 failed: %s", exc)
```

This ensures a NewsAPI outage never blocks signal generation,
and a yfinance timeout never stops the alert dispatcher.
