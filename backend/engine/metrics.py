"""
Performance Metrics Calculator
================================
Computes every required backtest metric from a trade log and equity curve.

Required metrics:
    CAGR         – Compound Annual Growth Rate
    Sharpe Ratio – Annualised risk-adjusted return
    Max Drawdown – Largest peak-to-trough decline (%)
    Win Rate     – Fraction of profitable trades

Extended metrics also computed:
    Sortino Ratio, Calmar Ratio, Profit Factor,
    Avg Win / Avg Loss, Annual Volatility
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from typing import Optional

import numpy as np
import pandas as pd

from backend.models.portfolio import DataQuality

logger = logging.getLogger(__name__)

TRADING_DAYS_PER_YEAR = 252
RISK_FREE_RATE        = 0.065   # 6.5% – approximate Indian 10-yr G-Sec yield


# ─── Trade record ─────────────────────────────────────────────────────────────

@dataclass
class TradeRecord:
    entry_date:    pd.Timestamp
    exit_date:     pd.Timestamp
    entry_price:   float
    exit_price:    float
    direction:     str = "LONG"       # LONG | SHORT
    quantity:      float = 1.0

    @property
    def pnl_pct(self) -> float:
        if self.direction == "LONG":
            return (self.exit_price - self.entry_price) / self.entry_price * 100
        return (self.entry_price - self.exit_price) / self.entry_price * 100

    @property
    def is_winner(self) -> bool:
        return self.pnl_pct > 0


# ─── Metrics dataclass ────────────────────────────────────────────────────────

@dataclass
class BacktestMetrics:
    # Core required
    cagr:              Optional[float] = None
    sharpe_ratio:      Optional[float] = None
    max_drawdown:      Optional[float] = None
    win_rate:          Optional[float] = None

    # Extended
    sortino_ratio:     Optional[float] = None
    calmar_ratio:      Optional[float] = None
    profit_factor:     Optional[float] = None
    total_trades:      int             = 0
    winning_trades:    int             = 0
    losing_trades:     int             = 0
    total_return_pct:  Optional[float] = None
    annual_volatility: Optional[float] = None
    avg_trade_return:  Optional[float] = None
    avg_win:           Optional[float] = None
    avg_loss:          Optional[float] = None

    # Meta
    years_of_data:     float          = 0.0
    data_quality:      DataQuality    = DataQuality.SUFFICIENT
    notes:             str            = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["data_quality"] = self.data_quality.value
        return d


# ─── Equity curve builder ─────────────────────────────────────────────────────

def build_equity_curve(
    df:              pd.DataFrame,
    initial_capital: float = 100_000.0,
    commission_pct:  float = 0.001,       # 0.1% per side = 0.2% round-trip
) -> tuple[pd.Series, list[TradeRecord]]:
    """
    Simulate a long-only strategy from a signals DataFrame.

    Commission / slippage model
    ---------------------------
    A flat 0.1% is deducted on BOTH the entry and exit legs, giving a
    realistic round-trip cost of 0.2% per trade (covers NSE STT, stamp
    duty, brokerage, and conservative slippage).

    Without symmetric commission the backtest overstates returns because
    the capital deployed at entry is too large (no entry friction) while
    the exit proceeds are still reduced — an asymmetric error that
    compounds over many trades.

    Parameters
    ----------
    df              : DataFrame with 'signal', 'position', 'Close' columns
    initial_capital : starting portfolio value
    commission_pct  : one-way commission + slippage fraction (default 0.1%)

    Returns
    -------
    (equity_curve, trades)
    """
    equity = initial_capital
    equity_series: list[tuple] = []
    trades:        list[TradeRecord] = []

    in_trade      = False
    entry_price   = 0.0
    entry_date    = None
    shares_held   = 0.0

    for ts, row in df.iterrows():
        close = row["Close"]
        sig   = row.get("signal", 0)
        pos   = row.get("position", 0)

        if sig == 1 and not in_trade:
            # Open long — deduct entry commission from deployable capital
            entry_commission = equity * commission_pct
            shares_held      = (equity - entry_commission) / close
            entry_price      = close
            entry_date       = ts
            in_trade         = True

        elif (sig == -1 or (in_trade and pos == 0)) and in_trade:
            # Close long — deduct exit commission from gross proceeds
            gross           = shares_held * close
            exit_commission = gross * commission_pct
            equity          = gross - exit_commission
            trades.append(TradeRecord(
                entry_date  = entry_date,
                exit_date   = ts,
                entry_price = entry_price,
                exit_price  = close,
            ))
            shares_held = 0.0
            in_trade    = False

        # Mark-to-market
        if in_trade:
            mtm_equity = shares_held * close
        else:
            mtm_equity = equity

        equity_series.append((ts, mtm_equity))

    equity_curve = pd.Series(
        dict(equity_series),
        dtype=float,
    )
    equity_curve.index = pd.to_datetime(equity_curve.index)
    return equity_curve, trades


# ─── Core metrics functions ───────────────────────────────────────────────────

def _cagr(equity_curve: pd.Series) -> Optional[float]:
    if equity_curve.empty or len(equity_curve) < 2:
        return None
    start = equity_curve.iloc[0]
    end   = equity_curve.iloc[-1]
    if start <= 0:
        return None
    days  = (equity_curve.index[-1] - equity_curve.index[0]).days
    years = days / 365.25
    if years < 0.1:
        return None
    return ((end / start) ** (1 / years) - 1) * 100


def _max_drawdown(equity_curve: pd.Series) -> Optional[float]:
    if equity_curve.empty:
        return None
    roll_max = equity_curve.cummax()
    drawdown = (equity_curve - roll_max) / roll_max.replace(0, np.nan)
    return float(drawdown.min() * 100)       # negative %


def _sharpe(equity_curve: pd.Series, rfr: float = RISK_FREE_RATE) -> Optional[float]:
    if equity_curve.empty or len(equity_curve) < 30:
        return None
    daily_ret = equity_curve.pct_change().dropna()
    if daily_ret.std() == 0:
        return None
    excess_ret = daily_ret - (rfr / TRADING_DAYS_PER_YEAR)
    return float((excess_ret.mean() / daily_ret.std()) * np.sqrt(TRADING_DAYS_PER_YEAR))


def _sortino(equity_curve: pd.Series, rfr: float = RISK_FREE_RATE) -> Optional[float]:
    if equity_curve.empty or len(equity_curve) < 30:
        return None
    daily_ret    = equity_curve.pct_change().dropna()
    excess_ret   = daily_ret - (rfr / TRADING_DAYS_PER_YEAR)
    downside_ret = excess_ret[excess_ret < 0]
    if len(downside_ret) < 2 or downside_ret.std() == 0:
        return None
    return float((excess_ret.mean() / downside_ret.std()) * np.sqrt(TRADING_DAYS_PER_YEAR))


def _calmar(cagr: Optional[float], mdd: Optional[float]) -> Optional[float]:
    if cagr is None or mdd is None or mdd == 0:
        return None
    return abs(cagr / mdd)


def _win_rate(trades: list[TradeRecord]) -> Optional[float]:
    if not trades:
        return None
    winners = sum(1 for t in trades if t.is_winner)
    return winners / len(trades) * 100


def _profit_factor(trades: list[TradeRecord]) -> Optional[float]:
    if not trades:
        return None
    gross_profit = sum(t.pnl_pct for t in trades if t.pnl_pct > 0)
    gross_loss   = abs(sum(t.pnl_pct for t in trades if t.pnl_pct < 0))
    if gross_loss == 0:
        return None
    return gross_profit / gross_loss


def _annual_volatility(equity_curve: pd.Series) -> Optional[float]:
    if equity_curve.empty or len(equity_curve) < 10:
        return None
    daily_ret = equity_curve.pct_change().dropna()
    return float(daily_ret.std() * np.sqrt(TRADING_DAYS_PER_YEAR) * 100)


# ─── Master calculator ────────────────────────────────────────────────────────

def calculate_metrics(
    df:              pd.DataFrame,
    initial_capital: float = 100_000.0,
    min_years:       float = 5.0,
) -> BacktestMetrics:
    """
    Full pipeline: build equity curve from signals → compute all metrics.

    Parameters
    ----------
    df              : enriched signals DataFrame (must have 'Close', 'signal', 'position')
    initial_capital : portfolio start value
    min_years       : minimum data span required for LOW_CONFIDENCE flag

    Returns
    -------
    BacktestMetrics dataclass
    """
    metrics = BacktestMetrics()

    if df.empty or "signal" not in df.columns:
        metrics.notes        = "No signals generated"
        metrics.data_quality = DataQuality.LOW_CONFIDENCE
        return metrics

    # Year span
    df_clean = df.dropna(subset=["Close"])
    if df_clean.empty:
        metrics.notes = "No valid price data"
        metrics.data_quality = DataQuality.LOW_CONFIDENCE
        return metrics

    days  = (df_clean.index[-1] - df_clean.index[0]).days
    years = days / 365.25
    metrics.years_of_data = round(years, 2)

    if years < 5.0:
        metrics.data_quality = DataQuality.LOW_CONFIDENCE
        metrics.notes        = f"INSUFFICIENT DATA: only {years:.1f} years available"
        logger.warning("Backtest data quality: %s", metrics.notes)
        # Still compute whatever metrics we can on available data
    elif years < 10.0:
        metrics.data_quality = DataQuality.INSUFFICIENT
        metrics.notes        = f"INSUFFICIENT DATA: {years:.1f} years (need ≥ 10)"
    else:
        metrics.data_quality = DataQuality.SUFFICIENT

    # Build equity curve and trade log
    try:
        equity_curve, trades = build_equity_curve(df_clean, initial_capital)
    except Exception as exc:
        logger.error("Equity curve build failed: %s", exc, exc_info=True)
        metrics.notes = f"Equity curve error: {exc}"
        return metrics

    if equity_curve.empty:
        metrics.notes = "Empty equity curve"
        return metrics

    # Trade statistics
    metrics.total_trades   = len(trades)
    metrics.winning_trades = sum(1 for t in trades if t.is_winner)
    metrics.losing_trades  = metrics.total_trades - metrics.winning_trades

    pnl_pcts = [t.pnl_pct for t in trades]
    winners  = [p for p in pnl_pcts if p > 0]
    losers   = [p for p in pnl_pcts if p <= 0]

    metrics.win_rate        = _win_rate(trades)
    metrics.profit_factor   = _profit_factor(trades)
    metrics.avg_trade_return = np.mean(pnl_pcts) if pnl_pcts else None
    metrics.avg_win          = np.mean(winners)  if winners  else None
    metrics.avg_loss         = np.mean(losers)   if losers   else None

    # Equity curve metrics
    metrics.cagr             = _cagr(equity_curve)
    metrics.max_drawdown     = _max_drawdown(equity_curve)
    metrics.sharpe_ratio     = _sharpe(equity_curve)
    metrics.sortino_ratio    = _sortino(equity_curve)
    metrics.calmar_ratio     = _calmar(metrics.cagr, metrics.max_drawdown)
    metrics.annual_volatility= _annual_volatility(equity_curve)

    start_val = equity_curve.iloc[0]
    end_val   = equity_curve.iloc[-1]
    metrics.total_return_pct = ((end_val - start_val) / start_val * 100) if start_val else None

    logger.debug(
        "Metrics: CAGR=%.2f%% Sharpe=%.2f MDD=%.2f%% WinRate=%.1f%% Trades=%d",
        metrics.cagr or 0,
        metrics.sharpe_ratio or 0,
        metrics.max_drawdown or 0,
        metrics.win_rate or 0,
        metrics.total_trades,
    )
    return metrics
