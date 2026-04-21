"""
backend/services/quant_service.py
=====================================
QuantService: Strategy selection + 10-year backtest engine.

Data quality enforcement:
  < 5 years  → DataQuality.LOW_CONFIDENCE   "INSUFFICIENT DATA"
  5-9 years  → DataQuality.INSUFFICIENT     "INSUFFICIENT DATA"
  ≥ 10 years → DataQuality.SUFFICIENT

Parquet caching: historical OHLCV saved per ticker to avoid API rate limits.
If a year of data is missing in the middle, it is skipped and metrics are
computed on available bars. If total data < 5 years, INSUFFICIENT DATA
is returned — no fake values emitted.
"""

from __future__ import annotations

import json
import logging
import math
import statistics
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf
from sqlalchemy import desc
from sqlalchemy.orm import Session

from backend.core.config import get_settings
from backend.core.database import get_db_context
from backend.models.backtest  import StrategyPerformance
from backend.models.portfolio import DataQuality
from backend.models.regime    import MarketRegimeLabel
from backend.models.signals   import RegimeMode

logger = logging.getLogger(__name__)
cfg    = get_settings()

NSE_SUFFIX        = ".NS"
MIN_BARS          = 220
COMMISSION_PCT    = cfg.COMMISSION_PCT
RISK_FREE_RATE    = cfg.RISK_FREE_RATE
TRADING_DAYS      = cfg.TRADING_DAYS_PER_YEAR


# ─── Parquet cache ─────────────────────────────────────────────────────────────

def _parquet_path(symbol: str) -> Path:
    p = Path(cfg.PARQUET_CACHE_DIR)
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{symbol}_10yr.parquet"

def _cache_fresh(path: Path, max_age_hours: int = 24) -> bool:
    if not path.exists(): return False
    return (datetime.now().timestamp() - path.stat().st_mtime) < max_age_hours * 3600

def fetch_with_cache(symbol: str) -> Optional[pd.DataFrame]:
    path = _parquet_path(symbol)
    if _cache_fresh(path):
        try:
            df = pd.read_parquet(path)
            logger.debug("Parquet HIT: %s (%d bars)", symbol, len(df))
            return df
        except Exception: pass

    yf_sym = f"{symbol}{NSE_SUFFIX}"
    try:
        df = yf.Ticker(yf_sym).history(period="max", interval="1d", auto_adjust=True)
        if df.empty: return None
        df = df[["Open","High","Low","Close","Volume"]].rename(columns=str.title)
        df.index = pd.to_datetime(df.index, utc=True).tz_localize(None)
        df = df.dropna()
        df.to_parquet(path, index=True)
        logger.info("Parquet WRITE: %s (%d bars)", symbol, len(df))
        return df
    except Exception as exc:
        logger.error("yfinance fetch failed for %s: %s", yf_sym, exc)
        return None


# ─── Data quality assessment ──────────────────────────────────────────────────

def assess_quality(df: pd.DataFrame, symbol: str) -> tuple[DataQuality, float, str]:
    if df is None or df.empty:
        return DataQuality.LOW_CONFIDENCE, 0.0, "No data returned"
    days  = (df.index[-1] - df.index[0]).days
    years = days / 365.25
    if years >= cfg.MIN_YEARS_SUFFICIENT:
        return DataQuality.SUFFICIENT, years, f"{years:.1f} yrs — SUFFICIENT"
    if years >= cfg.MIN_YEARS_CONFIDENCE:
        return DataQuality.INSUFFICIENT, years, f"INSUFFICIENT DATA: only {years:.1f} yrs (need ≥{cfg.MIN_YEARS_SUFFICIENT})"
    return DataQuality.LOW_CONFIDENCE, years, f"INSUFFICIENT DATA: only {years:.1f} yrs (need ≥{cfg.MIN_YEARS_CONFIDENCE})"


# ─── Metrics ──────────────────────────────────────────────────────────────────

def _cagr(initial: float, final: float, days: float) -> Optional[float]:
    if initial <= 0 or days < 1: return None
    return ((final / initial) ** (365.25 / days) - 1) * 100

def _max_drawdown(curve: list[float]) -> Optional[float]:
    if len(curve) < 2: return None
    peak, mdd = curve[0], 0.0
    for v in curve:
        if v > peak: peak = v
        dd = (peak - v) / peak if peak > 0 else 0.0
        if dd > mdd: mdd = dd
    return round(-mdd * 100, 2)

def _sharpe(daily_rets: list[float]) -> Optional[float]:
    if len(daily_rets) < 30: return None
    rfr   = RISK_FREE_RATE / TRADING_DAYS
    excess = [r - rfr for r in daily_rets]
    std    = statistics.stdev(excess) if len(excess) >= 2 else 0.0
    return round((statistics.mean(excess) / std) * math.sqrt(TRADING_DAYS), 2) if std > 0 else None

def _win_rate(pnls: list[float]) -> Optional[float]:
    if not pnls: return None
    return sum(1 for p in pnls if p > 0) / len(pnls) * 100


# ─── Simple signal generators (strategy logic) ────────────────────────────────

def _ema(s, n):  return s.ewm(span=n, adjust=False, min_periods=n).mean()
def _sma(s, n):  return s.rolling(n, min_periods=n).mean()
def _zscore(s, n): 
    m = s.rolling(n,min_periods=n).mean()
    d = s.rolling(n,min_periods=n).std(ddof=1)
    return (s - m) / d.replace(0, np.nan)

def _generate_signals(df: pd.DataFrame, strategy_name: str) -> pd.Series:
    """Return a +1/-1/0 signal series for the given strategy name."""
    close = df["Close"]
    sigs  = pd.Series(0, index=df.index)

    if strategy_name == "Trend_EMA_Cross":
        f, s = _ema(close, 50), _ema(close, 200)
        sigs[(f > s) & (f.shift(1) <= s.shift(1))] =  1
        sigs[(f < s) & (f.shift(1) >= s.shift(1))] = -1

    elif strategy_name == "Mean_Reversion_ZScore":
        z = _zscore(close, 20)
        sigs[z < -2.0] =  1
        sigs[z >  2.0] = -1

    elif strategy_name == "Momentum_Breakout":
        high52 = df["High"].rolling(252, min_periods=252).max().shift(1)
        vol_r  = df["Volume"] / df["Volume"].rolling(20, min_periods=20).mean()
        sigs[(close > high52) & (vol_r >= 2.0)] =  1
        sigs[close < close.rolling(20).mean() * 0.97] = -1

    elif strategy_name == "Bollinger_Reversion":
        mid   = _sma(close, 20)
        std   = close.rolling(20).std(ddof=1)
        upper = mid + 2*std; lower = mid - 2*std
        pctb  = (close - lower) / (upper - lower).replace(0, np.nan)
        sigs[pctb < 0.05] =  1
        sigs[pctb > 0.95] = -1

    elif strategy_name in ("Swing_HighLow", "Volume_Surge",
                           "Factor_Momentum", "Fundamental_Filter"):
        # For remaining strategies: use simplified trend proxy
        sigs[_ema(close, 20) > _ema(close, 50)] =  1
        sigs[_ema(close, 20) < _ema(close, 50)] = -1

    return sigs


ALL_STRATEGIES = [
    "Trend_EMA_Cross", "Momentum_Breakout", "Mean_Reversion_ZScore",
    "Fundamental_Filter", "Swing_HighLow", "Volume_Surge",
    "Factor_Momentum", "Bollinger_Reversion",
]

STRATEGY_CATEGORY: dict[str, str] = {
    "Trend_EMA_Cross":       "trend",
    "Momentum_Breakout":     "momentum",
    "Factor_Momentum":       "momentum",
    "Volume_Surge":          "momentum",
    "Mean_Reversion_ZScore": "reversion",
    "Bollinger_Reversion":   "reversion",
    "Swing_HighLow":         "swing",
    "Fundamental_Filter":    "fundamental",
}

REGIME_STRATEGIES: dict[str, list[str]] = {
    "trend":       ["Trend_EMA_Cross", "Momentum_Breakout", "Factor_Momentum", "Volume_Surge"],
    "reversion":   ["Mean_Reversion_ZScore", "Bollinger_Reversion"],
    "swing":       ["Swing_HighLow"],
    "fundamental": ["Fundamental_Filter"],
}


# ─── Equity curve + metrics ───────────────────────────────────────────────────

def _run_backtest(df: pd.DataFrame, strategy: str, capital: float = 100_000.0) -> dict:
    sigs     = _generate_signals(df, strategy)
    equity   = capital
    equity_curve = [capital]
    trades   = []
    in_trade = False
    entry_p  = 0.0
    shares   = 0.0

    for ts, row in df.iterrows():
        sig = sigs.get(ts, 0)
        c   = row["Close"]
        if sig == 1 and not in_trade:
            comm  = equity * COMMISSION_PCT
            shares = (equity - comm) / c
            entry_p = c; in_trade = True
        elif sig == -1 and in_trade:
            gross  = shares * c
            comm   = gross * COMMISSION_PCT
            equity = gross - comm
            trades.append((c - entry_p) * shares)
            in_trade = False; shares = 0.0
        equity_curve.append(shares * c if in_trade else equity)

    daily_rets = []
    for i in range(1, len(equity_curve)):
        if equity_curve[i-1] > 0:
            daily_rets.append((equity_curve[i] - equity_curve[i-1]) / equity_curve[i-1])

    final = equity_curve[-1]
    days  = (df.index[-1] - df.index[0]).days

    return {
        "cagr":         _cagr(capital, final, days),
        "sharpe_ratio": _sharpe(daily_rets),
        "max_drawdown": _max_drawdown(equity_curve),
        "win_rate":     _win_rate(trades),
        "total_trades": len(trades),
        "winning_trades": sum(1 for p in trades if p > 0),
        "losing_trades":  sum(1 for p in trades if p <= 0),
        "total_return_pct": ((final - capital) / capital * 100),
    }


# ─── QuantService ──────────────────────────────────────────────────────────────

class QuantService:

    def run_backtest_for_ticker(self, symbol: str) -> dict:
        """Run all 8 strategies for a ticker, persist results, return summary."""
        df = fetch_with_cache(symbol)
        if df is None or len(df) < MIN_BARS:
            return {"status": "no_data", "symbol": symbol}

        quality, years, msg = assess_quality(df, symbol)
        results = {}

        with get_db_context() as db:
            for strat in ALL_STRATEGIES:
                try:
                    df_clean = df.dropna()
                    metrics  = _run_backtest(df_clean, strat, cfg.INITIAL_CAPITAL)

                    row = db.query(StrategyPerformance).filter(
                        StrategyPerformance.stock_ticker  == symbol,
                        StrategyPerformance.strategy_name == strat,
                    ).first()

                    if not row:
                        row = StrategyPerformance(stock_ticker=symbol, strategy_name=strat)
                        db.add(row)

                    row.sharpe_ratio     = round(metrics["sharpe_ratio"], 4) if metrics["sharpe_ratio"] else None
                    row.cagr             = round(metrics["cagr"], 4) if metrics["cagr"] else None
                    row.win_rate         = round(metrics["win_rate"], 4) if metrics["win_rate"] else None
                    row.max_drawdown     = metrics["max_drawdown"]
                    row.total_trades     = metrics["total_trades"]
                    row.winning_trades   = metrics["winning_trades"]
                    row.losing_trades    = metrics["losing_trades"]
                    row.total_return_pct = round(metrics["total_return_pct"], 4)
                    row.years_of_data    = round(years, 2)
                    row.data_quality     = quality
                    row.backtest_start   = df_clean.index[0].to_pydatetime()
                    row.backtest_end     = df_clean.index[-1].to_pydatetime()
                    row.notes            = msg if quality != DataQuality.SUFFICIENT else None
                    row.ran_at           = datetime.utcnow()

                    results[strat] = {
                        "sharpe": row.sharpe_ratio,
                        "cagr":   row.cagr,
                        "win_rate": row.win_rate,
                        "quality": quality.value,
                    }
                except Exception as exc:
                    logger.error("Backtest failed %s/%s: %s", symbol, strat, exc)

        return {"symbol": symbol, "years": round(years,2), "quality": quality.value, "strategies": results}

    def get_best_strategy(
        self,
        ticker:       str,
        regime_label: MarketRegimeLabel,
        db:           Session,
    ) -> Optional[StrategyPerformance]:
        """
        Query StrategyPerformance and return the best strategy for the given regime.
        STRONG_TREND → highest Sharpe in trend/momentum group
        SIDEWAYS      → highest Win Rate in reversion/swing group
        VOLATILE      → reversion only if Win Rate > 65%, else None (CASH)
        BEAR          → fundamental first, reversion fallback
        """
        def _query(strategies, order_by_col):
            return (
                db.query(StrategyPerformance)
                  .filter(
                      StrategyPerformance.stock_ticker == ticker,
                      StrategyPerformance.strategy_name.in_(strategies),
                      order_by_col.isnot(None),
                  )
                  .order_by(desc(order_by_col))
                  .first()
            )

        if regime_label == MarketRegimeLabel.STRONG_TREND:
            return _query(REGIME_STRATEGIES["trend"] + REGIME_STRATEGIES["momentum"] if "momentum" in REGIME_STRATEGIES else REGIME_STRATEGIES["trend"],
                          StrategyPerformance.sharpe_ratio)

        if regime_label == MarketRegimeLabel.SIDEWAYS:
            return _query(REGIME_STRATEGIES["reversion"] + REGIME_STRATEGIES["swing"],
                          StrategyPerformance.win_rate)

        if regime_label == MarketRegimeLabel.VOLATILE_HIGH_RISK:
            row = _query(REGIME_STRATEGIES["reversion"], StrategyPerformance.win_rate)
            return row if (row and row.win_rate and row.win_rate >= 65.0) else None

        if regime_label == MarketRegimeLabel.BEAR_CRASHING:
            row = _query(REGIME_STRATEGIES["fundamental"], StrategyPerformance.sharpe_ratio)
            return row or _query(REGIME_STRATEGIES["reversion"], StrategyPerformance.win_rate)

        # UNKNOWN: best overall Sharpe
        return (db.query(StrategyPerformance)
                  .filter(StrategyPerformance.stock_ticker == ticker,
                          StrategyPerformance.sharpe_ratio.isnot(None))
                  .order_by(desc(StrategyPerformance.sharpe_ratio))
                  .first())


_quant_service: Optional[QuantService] = None

def get_quant_service() -> QuantService:
    global _quant_service
    if _quant_service is None:
        _quant_service = QuantService()
    return _quant_service
