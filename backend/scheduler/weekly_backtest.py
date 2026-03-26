"""
Weekly Backtest Refresh Job
============================
Runs every Saturday at 01:00 UTC (06:30 IST) to re-run the full
10-year backtest across all Nifty 500 tickers in the portfolio.

Purpose
-------
• Detect if the 'Best Strategy' for any ticker has changed
• Keep StrategyPerformance table fresh with the latest data
• Log a comparison: which strategies improved / degraded

The job runs outside market hours (Saturday) to avoid competing
with the live heartbeat for yfinance API bandwidth.

Comparison logic
----------------
Before running: snapshot current top strategies per ticker
After running:  compare new results — log any strategy changes
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from backend.core.database import SessionLocal

logger = logging.getLogger(__name__)


def _snapshot_best_strategies(db: Session) -> dict[str, dict]:
    """
    Capture the current top-Sharpe strategy per ticker.
    Returns dict: {ticker: {strategy_name, sharpe_ratio, cagr, win_rate}}
    """
    from backend.models.backtest import StrategyPerformance
    from backend.models.portfolio import DataQuality
    from sqlalchemy import func

    # Subquery: max Sharpe per ticker
    subq = (
        db.query(
            StrategyPerformance.stock_ticker,
            func.max(StrategyPerformance.sharpe_ratio).label("max_sharpe"),
        )
        .filter(
            StrategyPerformance.sharpe_ratio.isnot(None),
            StrategyPerformance.data_quality == DataQuality.SUFFICIENT,
        )
        .group_by(StrategyPerformance.stock_ticker)
        .subquery()
    )

    rows = (
        db.query(StrategyPerformance)
          .join(subq, (StrategyPerformance.stock_ticker == subq.c.stock_ticker) &
                      (StrategyPerformance.sharpe_ratio == subq.c.max_sharpe))
          .all()
    )

    return {
        r.stock_ticker: {
            "strategy_name": r.strategy_name,
            "sharpe_ratio":  r.sharpe_ratio,
            "cagr":          r.cagr,
            "win_rate":      r.win_rate,
        }
        for r in rows
    }


def _compare_strategies(
    before: dict[str, dict],
    after:  dict[str, dict],
) -> list[dict]:
    """
    Identify tickers where the best strategy has changed or metrics improved.
    Returns list of change records.
    """
    changes = []
    all_tickers = set(before) | set(after)

    for ticker in all_tickers:
        prev = before.get(ticker)
        curr = after.get(ticker)

        if prev is None and curr is not None:
            changes.append({
                "ticker":      ticker,
                "change_type": "NEW",
                "old_strategy":None,
                "new_strategy":curr["strategy_name"],
                "old_sharpe":  None,
                "new_sharpe":  curr["sharpe_ratio"],
            })
        elif prev is not None and curr is None:
            changes.append({
                "ticker":      ticker,
                "change_type": "REMOVED",
                "old_strategy":prev["strategy_name"],
                "new_strategy":None,
                "old_sharpe":  prev["sharpe_ratio"],
                "new_sharpe":  None,
            })
        elif prev and curr:
            # Strategy changed
            if prev["strategy_name"] != curr["strategy_name"]:
                changes.append({
                    "ticker":      ticker,
                    "change_type": "STRATEGY_CHANGED",
                    "old_strategy":prev["strategy_name"],
                    "new_strategy":curr["strategy_name"],
                    "old_sharpe":  prev["sharpe_ratio"],
                    "new_sharpe":  curr["sharpe_ratio"],
                })
            # Significant Sharpe improvement (≥ 0.1)
            elif (prev["sharpe_ratio"] or 0) + 0.1 < (curr["sharpe_ratio"] or 0):
                changes.append({
                    "ticker":      ticker,
                    "change_type": "SHARPE_IMPROVED",
                    "old_strategy":prev["strategy_name"],
                    "new_strategy":curr["strategy_name"],
                    "old_sharpe":  prev["sharpe_ratio"],
                    "new_sharpe":  curr["sharpe_ratio"],
                })

    return changes


def run_weekly_backtest_refresh() -> dict:
    """
    Full Saturday backtest refresh pipeline.

    1. Snapshot current best strategies
    2. Run 10-year backtest for all portfolio holdings
    3. Compare before/after
    4. Log strategy changes
    5. Send summary email

    Returns summary dict.
    """
    logger.info("═══ WEEKLY BACKTEST REFRESH START ═══")
    start_ts = datetime.utcnow()

    db = SessionLocal()
    try:
        # ── Snapshot before ───────────────────────────────────────────────────
        before = _snapshot_best_strategies(db)
        logger.info("Pre-refresh snapshot: %d tickers have strategy data", len(before))

        # ── Load portfolio tickers ────────────────────────────────────────────
        from backend.models.portfolio import Holding
        holdings = db.query(Holding).all()
        tickers  = [h.symbol for h in holdings]

        if not tickers:
            logger.warning("No holdings in portfolio — weekly backtest has nothing to do")
            return {"status": "skipped", "reason": "no_holdings", "changes": []}

        logger.info("Starting weekly backtest for %d tickers × 8 strategies", len(tickers))

    finally:
        db.close()

    # ── Run backtest (uses own DB sessions internally) ─────────────────────────
    from engine.backtest_engine import run_full_backtest
    try:
        backtest_summary = run_full_backtest(tickers, exchange="NSE")
    except Exception as exc:
        logger.error("Weekly backtest run failed: %s", exc, exc_info=True)
        backtest_summary = {"error": str(exc)}

    # ── Compare after ──────────────────────────────────────────────────────────
    db2 = SessionLocal()
    try:
        after   = _snapshot_best_strategies(db2)
        changes = _compare_strategies(before, after)
    finally:
        db2.close()

    elapsed = (datetime.utcnow() - start_ts).total_seconds()

    # ── Log changes ────────────────────────────────────────────────────────────
    if changes:
        logger.warning("═══ STRATEGY CHANGES DETECTED (%d) ═══", len(changes))
        for c in changes:
            logger.warning(
                "  %-12s %-20s → %-20s  Sharpe: %s → %s",
                c["ticker"],
                c.get("old_strategy") or "N/A",
                c.get("new_strategy") or "N/A",
                f"{c.get('old_sharpe'):.2f}" if c.get("old_sharpe") else "N/A",
                f"{c.get('new_sharpe'):.2f}" if c.get("new_sharpe") else "N/A",
            )
    else:
        logger.info("No strategy changes detected this week")

    summary = {
        "status":            "complete",
        "tickers_processed": len(tickers),
        "strategy_changes":  len(changes),
        "changes":           changes,
        "elapsed_seconds":   round(elapsed, 1),
        "backtest_summary":  backtest_summary,
        "ran_at":            start_ts.isoformat(),
    }

    # ── Send email summary ────────────────────────────────────────────────────
    try:
        _send_weekly_backtest_email(summary, changes)
    except Exception as exc:
        logger.warning("Weekly backtest email failed: %s", exc)

    logger.info(
        "═══ WEEKLY BACKTEST DONE  %.0fs | %d changes ═══",
        elapsed, len(changes),
    )
    return summary


def _send_weekly_backtest_email(summary: dict, changes: list[dict]) -> None:
    """Send a summary email of the weekly backtest results."""
    # send_priority_alert was removed (services.research.alert_service no longer exists).
    # Summary is logged to Railway logs instead — visible in the Railway dashboard.
    logger.info(
        "WEEKLY BACKTEST SUMMARY | tickers=%d | changes=%d | elapsed=%.0fs",
        summary['tickers_processed'], summary['strategy_changes'], summary['elapsed_seconds'],
    )
    for c in changes[:3]:
        logger.info(
            "  CHANGE %s: %s → %s (Sharpe: %s → %s)",
            c['ticker'], c.get('old_strategy','N/A'), c.get('new_strategy','N/A'),
            c.get('old_sharpe') or 'N/A', c.get('new_sharpe') or 'N/A',
        )


# ─── APScheduler async wrapper ────────────────────────────────────────────────

async def weekly_backtest_job() -> None:
    """Run in executor to avoid blocking the event loop."""
    try:
        await asyncio.get_event_loop().run_in_executor(
            None, run_weekly_backtest_refresh
        )
    except Exception as exc:
        logger.error("weekly_backtest_job failed: %s", exc, exc_info=True)
