"""
Regime Switchboard
===================
Implements the Selection Matrix that maps the current MarketRegime
to the best historical strategy for each ticker.

Selection Matrix
----------------
STRONG_TREND       → Filter for Trend / Momentum strategies
                     → Pick highest Sharpe Ratio for that stock
SIDEWAYS           → Filter for Mean Reversion / Swing strategies
                     → Pick highest Win Rate for that stock
VOLATILE_HIGH_RISK → Force CASH mode
                     → Allow Mean Reversion ONLY if Win Rate > 65%
BEAR_CRASHING      → Prioritise Short / Fundamental Value strategies
                     → Filter for low Debt/Equity proxied by Fundamental

Each call to `map_best_strategy(ticker, db)` returns a
StrategySelectionResult describing the chosen strategy and why.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import desc
from sqlalchemy.orm import Session

from backend.models.regime import MarketRegime, MarketRegimeLabel
from backend.models.backtest import StrategyPerformance
from backend.models.signals import RegimeMode

logger = logging.getLogger(__name__)


# ─── Strategy groupings ──────────────────────────────────────────────────────
# Keys must match BaseStrategy.name values in engine/strategies/library.py

TREND_STRATEGIES: set[str] = {
    "Trend_EMA_Cross",
    "Momentum_Breakout",
    "Factor_Momentum",
    "Volume_Surge",
}

MEAN_REVERSION_STRATEGIES: set[str] = {
    "Mean_Reversion_ZScore",
    "Bollinger_Reversion",
}

SWING_STRATEGIES: set[str] = {
    "Swing_HighLow",
}

FUNDAMENTAL_STRATEGIES: set[str] = {
    "Fundamental_Filter",
}

VOLATILE_ALLOWED_WIN_RATE = 65.0    # minimum Win Rate to trade in volatile regime


# ─── Result dataclass ────────────────────────────────────────────────────────

@dataclass
class StrategySelectionResult:
    ticker:           str
    regime:           RegimeMode
    selected_strategy:Optional[str]
    selection_metric: Optional[float]   # the Sharpe / Win Rate used to rank
    metric_name:      str               # "Sharpe Ratio" | "Win Rate"
    force_cash:       bool = False
    reason:           str  = ""
    perf_row:         Optional[StrategyPerformance] = None


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _regime_label_to_mode(label: MarketRegimeLabel) -> RegimeMode:
    mapping = {
        MarketRegimeLabel.STRONG_TREND:       RegimeMode.STRONG_TREND,
        MarketRegimeLabel.SIDEWAYS:           RegimeMode.SIDEWAYS,
        MarketRegimeLabel.VOLATILE_HIGH_RISK: RegimeMode.VOLATILE_HIGH_RISK,
        MarketRegimeLabel.BEAR_CRASHING:      RegimeMode.BEAR_CRASHING,
        MarketRegimeLabel.UNKNOWN:            RegimeMode.UNKNOWN,
    }
    return mapping.get(label, RegimeMode.UNKNOWN)


def _best_by_sharpe(
    db:         Session,
    ticker:     str,
    strategies: set[str],
) -> Optional[StrategyPerformance]:
    """Return the strategy row with the highest Sharpe Ratio for ticker."""
    return (
        db.query(StrategyPerformance)
          .filter(
              StrategyPerformance.stock_ticker  == ticker,
              StrategyPerformance.strategy_name.in_(strategies),
              StrategyPerformance.sharpe_ratio.isnot(None),
          )
          .order_by(desc(StrategyPerformance.sharpe_ratio))
          .first()
    )


def _best_by_win_rate(
    db:              Session,
    ticker:          str,
    strategies:      set[str],
    min_win_rate:    float = 0.0,
) -> Optional[StrategyPerformance]:
    """Return the strategy row with the highest Win Rate for ticker."""
    q = (
        db.query(StrategyPerformance)
          .filter(
              StrategyPerformance.stock_ticker  == ticker,
              StrategyPerformance.strategy_name.in_(strategies),
              StrategyPerformance.win_rate.isnot(None),
          )
    )
    if min_win_rate > 0:
        q = q.filter(StrategyPerformance.win_rate >= min_win_rate)
    return q.order_by(desc(StrategyPerformance.win_rate)).first()


def _any_strategy_fallback(
    db:     Session,
    ticker: str,
) -> Optional[StrategyPerformance]:
    """Last-resort: return the best Sharpe across all strategies."""
    return (
        db.query(StrategyPerformance)
          .filter(
              StrategyPerformance.stock_ticker == ticker,
              StrategyPerformance.sharpe_ratio.isnot(None),
          )
          .order_by(desc(StrategyPerformance.sharpe_ratio))
          .first()
    )


# ─── Core selection function ─────────────────────────────────────────────────

def map_best_strategy(
    ticker:        str,
    db:            Session,
    regime_label:  Optional[MarketRegimeLabel] = None,
) -> StrategySelectionResult:
    """
    Join current market regime with historical StrategyPerformance data
    and return the best strategy for *ticker*.

    If regime_label is None, the latest MarketRegime row is fetched from DB.

    Returns StrategySelectionResult (force_cash=True means do not trade).
    """
    # ── Fetch regime ─────────────────────────────────────────────────────────
    if regime_label is None:
        latest_regime = (
            db.query(MarketRegime)
              .order_by(desc(MarketRegime.timestamp))
              .first()
        )
        if latest_regime is None:
            return StrategySelectionResult(
                ticker           = ticker,
                regime           = RegimeMode.UNKNOWN,
                selected_strategy= None,
                selection_metric = None,
                metric_name      = "N/A",
                reason           = "No regime data available — run regime detection first",
            )
        regime_label = latest_regime.regime_label

    mode = _regime_label_to_mode(regime_label)
    logger.debug("map_best_strategy: %s | regime=%s", ticker, mode.value)

    # ── STRONG TREND ─────────────────────────────────────────────────────────
    if mode == RegimeMode.STRONG_TREND:
        row = _best_by_sharpe(db, ticker, TREND_STRATEGIES)
        if row:
            return StrategySelectionResult(
                ticker            = ticker,
                regime            = mode,
                selected_strategy = row.strategy_name,
                selection_metric  = row.sharpe_ratio,
                metric_name       = "Sharpe Ratio",
                reason            = (
                    f"STRONG TREND regime: selected '{row.strategy_name}' "
                    f"with highest Sharpe={row.sharpe_ratio:.2f} among "
                    f"Trend/Momentum strategies for {ticker}."
                ),
                perf_row          = row,
            )

    # ── SIDEWAYS ─────────────────────────────────────────────────────────────
    if mode == RegimeMode.SIDEWAYS:
        all_sideways = MEAN_REVERSION_STRATEGIES | SWING_STRATEGIES
        row = _best_by_win_rate(db, ticker, all_sideways)
        if row:
            return StrategySelectionResult(
                ticker            = ticker,
                regime            = mode,
                selected_strategy = row.strategy_name,
                selection_metric  = row.win_rate,
                metric_name       = "Win Rate",
                reason            = (
                    f"SIDEWAYS regime: selected '{row.strategy_name}' "
                    f"with highest Win Rate={row.win_rate:.1f}% among "
                    f"Mean Reversion/Swing strategies for {ticker}."
                ),
                perf_row          = row,
            )

    # ── VOLATILE / HIGH RISK ─────────────────────────────────────────────────
    if mode == RegimeMode.VOLATILE_HIGH_RISK:
        # Attempt mean reversion ONLY if Win Rate > 65%
        row = _best_by_win_rate(
            db, ticker, MEAN_REVERSION_STRATEGIES,
            min_win_rate=VOLATILE_ALLOWED_WIN_RATE,
        )
        if row:
            return StrategySelectionResult(
                ticker            = ticker,
                regime            = mode,
                selected_strategy = row.strategy_name,
                selection_metric  = row.win_rate,
                metric_name       = "Win Rate",
                reason            = (
                    f"VOLATILE regime: mean reversion allowed "
                    f"(Win Rate={row.win_rate:.1f}% > {VOLATILE_ALLOWED_WIN_RATE}%). "
                    f"Using '{row.strategy_name}' with tight stops."
                ),
                perf_row          = row,
            )
        # No qualifying mean-reversion → force cash
        return StrategySelectionResult(
            ticker            = ticker,
            regime            = mode,
            selected_strategy = None,
            selection_metric  = None,
            metric_name       = "N/A",
            force_cash        = True,
            reason            = (
                f"VOLATILE/HIGH-RISK regime: no mean-reversion strategy "
                f"meets Win Rate > {VOLATILE_ALLOWED_WIN_RATE}% threshold for {ticker}. "
                f"CASH mode enforced."
            ),
        )

    # ── BEAR / CRASHING ───────────────────────────────────────────────────────
    if mode == RegimeMode.BEAR_CRASHING:
        # Prioritise Fundamental (value/defensive) first
        row = _best_by_sharpe(db, ticker, FUNDAMENTAL_STRATEGIES)
        if not row:
            # Fall back to mean reversion (can profit in range-bound bear)
            row = _best_by_win_rate(db, ticker, MEAN_REVERSION_STRATEGIES)
        if row:
            return StrategySelectionResult(
                ticker            = ticker,
                regime            = mode,
                selected_strategy = row.strategy_name,
                selection_metric  = row.sharpe_ratio or row.win_rate,
                metric_name       = "Sharpe Ratio" if row.sharpe_ratio else "Win Rate",
                reason            = (
                    f"BEAR/CRASHING regime: selected '{row.strategy_name}' "
                    f"(fundamental/defensive). Reduce sizing, prefer low-D/E stocks."
                ),
                perf_row          = row,
            )

    # ── UNKNOWN / fallback ────────────────────────────────────────────────────
    row = _any_strategy_fallback(db, ticker)
    if row:
        return StrategySelectionResult(
            ticker            = ticker,
            regime            = mode,
            selected_strategy = row.strategy_name,
            selection_metric  = row.sharpe_ratio,
            metric_name       = "Sharpe Ratio (fallback)",
            reason            = (
                f"No regime match or regime=UNKNOWN. "
                f"Fallback: best overall Sharpe '{row.strategy_name}' "
                f"(Sharpe={row.sharpe_ratio:.2f}) for {ticker}."
            ),
            perf_row          = row,
        )

    # Absolutely no data for this ticker
    return StrategySelectionResult(
        ticker            = ticker,
        regime            = mode,
        selected_strategy = None,
        selection_metric  = None,
        metric_name       = "N/A",
        force_cash        = True,
        reason            = f"No backtest data found for {ticker}. Run backtest first.",
    )
