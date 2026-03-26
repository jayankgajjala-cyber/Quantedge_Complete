"""
10-Year Backtest Engine
========================
Orchestrates the full backtest pipeline:

  For every stock in the portfolio / Nifty 500:
    For every strategy in the library (8 strategies):
      1. Fetch 10+ years of daily data from yfinance
      2. Enrich with all technical indicators
      3. Generate strategy signals
      4. Calculate performance metrics
      5. Persist to `strategy_performance` table

Data quality rules (hard rules – not skipped):
  < 5  years → return DataQuality.LOW_CONFIDENCE  + note "INSUFFICIENT DATA"
  5-9  years → return DataQuality.INSUFFICIENT    + note "INSUFFICIENT DATA"
  ≥ 10 years → DataQuality.SUFFICIENT

If specific years within a 10-year window have missing data, the engine
skips those bars (dropna) and calculates metrics on available bars only.
"""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from typing import Optional

import pandas as pd
import yfinance as yf
from sqlalchemy.orm import Session

from backend.engine.indicators.technical import enrich_dataframe
from backend.engine.metrics import BacktestMetrics, calculate_metrics
from backend.engine.strategies.library import all_strategy_instances, BaseStrategy
from backend.models.portfolio import DataQuality
from backend.models.backtest import StrategyPerformance
from backend.core.database import SessionLocal
from backend.services.data_manager import fetch_ohlcv as _fetch_ohlcv_dm  # quality helper

logger = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────

INITIAL_CAPITAL    = 100_000.0
MIN_BARS_REQUIRED  = 60          # absolute minimum to generate any indicators
NSE_SUFFIX         = ".NS"


# ─── Data fetching ────────────────────────────────────────────────────────────

def _fetch_10yr_data(symbol: str, exchange: str = "NSE") -> Optional[pd.DataFrame]:
    """
    Download maximum available daily OHLCV history via yfinance.
    Applies NSE/BSE suffix conventions.
    Returns None on failure.
    """
    yf_sym = f"{symbol}{NSE_SUFFIX}" if exchange == "NSE" else symbol
    try:
        ticker = yf.Ticker(yf_sym)
        df     = ticker.history(period="max", interval="1d", auto_adjust=True)

        if df.empty:
            logger.warning("No data returned for %s", yf_sym)
            return None

        df = df[["Open", "High", "Low", "Close", "Volume"]].rename(columns=str.title)
        df.index = pd.to_datetime(df.index, utc=True).tz_localize(None)
        df = df.dropna(subset=["Open", "High", "Low", "Close"])

        if len(df) < MIN_BARS_REQUIRED:
            logger.warning("%s: only %d bars – too few to backtest", symbol, len(df))
            return None

        return df

    except Exception as exc:
        logger.error("yfinance fetch failed for %s: %s", yf_sym, exc, exc_info=True)
        return None


# ─── Single stock × single strategy runner ────────────────────────────────────

def run_single_backtest(
    df:       pd.DataFrame,
    symbol:   str,
    strategy: BaseStrategy,
) -> BacktestMetrics:
    """
    Enrich → signal → metrics for one (symbol, strategy) pair.
    Never raises; returns metrics with appropriate data_quality flag.
    """
    try:
        enriched = enrich_dataframe(df)

        # Drop rows where key indicators are still NaN (first 200 bars)
        enriched = enriched.dropna(subset=["EMA_200", "ADX_14"])

        if len(enriched) < MIN_BARS_REQUIRED:
            m = BacktestMetrics(
                data_quality = DataQuality.LOW_CONFIDENCE,
                notes        = f"INSUFFICIENT DATA: only {len(enriched)} usable bars after indicator warm-up",
            )
            return m

        signals = strategy.generate_signals(enriched)

        # ── Look-ahead bias prevention ────────────────────────────────────────
        # Signals are generated on bar-close data. A trade can only be entered
        # on the NEXT bar's open, so we shift the signal column forward by 1.
        # This ensures the backtest never "cheats" by acting on today's close
        # within the same bar that produced the signal.
        if "signal" in signals.columns:
            signals["signal"] = signals["signal"].shift(1)
        signals = signals.iloc[1:]   # drop the first NaN row introduced by shift

        metrics = calculate_metrics(signals, INITIAL_CAPITAL)

        return metrics

    except Exception as exc:
        logger.error(
            "Backtest failed for %s / %s: %s",
            symbol, strategy.name, exc, exc_info=True,
        )
        return BacktestMetrics(
            data_quality = DataQuality.LOW_CONFIDENCE,
            notes        = f"Runtime error: {exc}",
        )


# ─── DB persistence ───────────────────────────────────────────────────────────

def _persist_result(
    db:       Session,
    symbol:   str,
    strategy: BaseStrategy,
    df:       pd.DataFrame,
    metrics:  BacktestMetrics,
) -> None:
    """Upsert a StrategyPerformance row (unique on ticker + strategy_name)."""
    try:
        start_date = df.index[0].to_pydatetime()  if not df.empty else None
        end_date   = df.index[-1].to_pydatetime() if not df.empty else None

        row = {
            "stock_ticker":    symbol,
            "strategy_name":   strategy.name,
            "sharpe_ratio":    round(metrics.sharpe_ratio,  4) if metrics.sharpe_ratio  is not None else None,
            "cagr":            round(metrics.cagr,           4) if metrics.cagr           is not None else None,
            "win_rate":        round(metrics.win_rate,       4) if metrics.win_rate       is not None else None,
            "max_drawdown":    round(metrics.max_drawdown,   4) if metrics.max_drawdown   is not None else None,
            "sortino_ratio":   round(metrics.sortino_ratio,  4) if metrics.sortino_ratio  is not None else None,
            "profit_factor":   round(metrics.profit_factor,  4) if metrics.profit_factor  is not None else None,
            "total_trades":    metrics.total_trades,
            "winning_trades":  metrics.winning_trades,
            "losing_trades":   metrics.losing_trades,
            "total_return_pct":round(metrics.total_return_pct, 4) if metrics.total_return_pct is not None else None,
            "annual_volatility":round(metrics.annual_volatility, 4) if metrics.annual_volatility is not None else None,
            "calmar_ratio":    round(metrics.calmar_ratio,   4) if metrics.calmar_ratio   is not None else None,
            "avg_trade_return":round(metrics.avg_trade_return, 4) if metrics.avg_trade_return is not None else None,
            "avg_win":         round(metrics.avg_win,        4) if metrics.avg_win        is not None else None,
            "avg_loss":        round(metrics.avg_loss,       4) if metrics.avg_loss       is not None else None,
            "backtest_start":  start_date,
            "backtest_end":    end_date,
            "years_of_data":   round(metrics.years_of_data, 2),
            "data_quality":    metrics.data_quality,
            "initial_capital": INITIAL_CAPITAL,
            "strategy_params": json.dumps({"strategy": strategy.name}),
            "ran_at":          datetime.utcnow(),
            "notes":           metrics.notes or "",
        }

        # Dialect-agnostic upsert — works on SQLite (dev) and PostgreSQL/Supabase (prod).
        existing = (
            db.query(StrategyPerformance)
            .filter_by(stock_ticker=symbol, strategy_name=strategy.name)
            .first()
        )
        if existing:
            for k, v in row.items():
                if k not in ("stock_ticker", "strategy_name"):
                    setattr(existing, k, v)
        else:
            db.add(StrategyPerformance(**row))
        db.commit()

    except Exception as exc:
        db.rollback()
        logger.error(
            "DB persist failed for %s / %s: %s",
            symbol, strategy.name, exc, exc_info=True,
        )
        raise


# ─── Main backtest runner ─────────────────────────────────────────────────────

def run_full_backtest(
    symbols:          list[str],
    strategies:       Optional[list[BaseStrategy]] = None,
    exchange:         str                          = "NSE",
    max_workers:      int                          = 4,
    progress_callback = None,
) -> dict:
    """
    Run all strategies across all symbols. Persists results to DB.

    Parameters
    ----------
    symbols          : list of ticker strings (e.g. ["RELIANCE", "TCS"])
    strategies       : list of strategy instances; defaults to all 8
    exchange         : "NSE" or "BSE"
    max_workers      : parallel worker threads for data fetch
    progress_callback: optional callable(symbol, strategy_name, metrics)

    Returns
    -------
    summary dict with counts and timing
    """
    if strategies is None:
        strategies = all_strategy_instances()

    total_combinations  = len(symbols) * len(strategies)
    completed           = 0
    errors              = 0
    skipped_data        = 0
    start_time          = time.time()

    logger.info(
        "Starting backtest: %d symbols × %d strategies = %d runs",
        len(symbols), len(strategies), total_combinations,
    )

    for symbol in symbols:
        logger.info("Fetching data for %s ...", symbol)
        df = _fetch_10yr_data(symbol, exchange)

        if df is None:
            logger.warning("Skipping %s – no data available", symbol)
            skipped_data += len(strategies)
            continue

        # Data quality assessment (inlined — assess_data_quality was removed)
        days  = (df.index[-1] - df.index[0]).days if not df.empty else 0
        years = days / 365.25
        if years >= 10:
            quality, quality_msg = DataQuality.SUFFICIENT, ""
        elif years >= 5:
            quality, quality_msg = DataQuality.INSUFFICIENT, f"INSUFFICIENT DATA: {years:.1f} years"
        else:
            quality, quality_msg = DataQuality.LOW_CONFIDENCE, f"INSUFFICIENT DATA: {years:.1f} years"
        logger.info(
            "%s: %.1f years of data → %s", symbol, years, quality.value
        )

        db = SessionLocal()
        try:
            for strategy in strategies:
                try:
                    metrics = run_single_backtest(df.copy(), symbol, strategy)

                    # Override quality from global assessment if worse
                    if quality == DataQuality.LOW_CONFIDENCE:
                        metrics.data_quality = DataQuality.LOW_CONFIDENCE
                        if not metrics.notes:
                            metrics.notes = quality_msg

                    _persist_result(db, symbol, strategy, df, metrics)
                    completed += 1

                    if progress_callback:
                        progress_callback(symbol, strategy.name, metrics)

                    logger.info(
                        "✓ %s / %-35s CAGR=%s Sharpe=%s WinRate=%s [%s]",
                        symbol,
                        strategy.name,
                        f"{metrics.cagr:.1f}%" if metrics.cagr is not None else "N/A",
                        f"{metrics.sharpe_ratio:.2f}" if metrics.sharpe_ratio is not None else "N/A",
                        f"{metrics.win_rate:.1f}%" if metrics.win_rate is not None else "N/A",
                        metrics.data_quality.value,
                    )

                except Exception as exc:
                    errors += 1
                    logger.error(
                        "✗ Error %s / %s: %s", symbol, strategy.name, exc
                    )
        finally:
            db.close()

    elapsed = time.time() - start_time
    summary = {
        "total_combinations": total_combinations,
        "completed":          completed,
        "errors":             errors,
        "skipped_no_data":    skipped_data,
        "elapsed_seconds":    round(elapsed, 1),
        "symbols":            len(symbols),
        "strategies":         len(strategies),
    }
    logger.info("Backtest complete: %s", summary)
    return summary


# ─── Convenience: run for holdings portfolio ──────────────────────────────────

def run_portfolio_backtest(exchange: str = "NSE") -> dict:
    """
    Load all symbols from the Holdings table and run the full backtest.
    Intended to be called from a FastAPI background task.
    """
    from backend.models.portfolio import Holding

    db = SessionLocal()
    try:
        holdings = db.query(Holding).all()
        symbols  = [h.symbol for h in holdings]
    finally:
        db.close()

    if not symbols:
        logger.warning("No holdings in DB – backtest has nothing to run")
        return {"error": "No holdings found", "completed": 0}

    return run_full_backtest(symbols, exchange=exchange)
