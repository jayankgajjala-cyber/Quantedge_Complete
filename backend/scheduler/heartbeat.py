"""
5-Minute Heartbeat Job
=======================
The central market-hours job that runs every 5 minutes while NSE is open
(Monday–Friday, 09:15–15:30 IST, non-holiday).

Each heartbeat tick executes these steps in sequence:
  1. Market hours check          – abort if market is closed
  2. Live price refresh          – update open paper trade MTM
  3. Module 4 Signal Engine      – run regime-aware signal scan
  4. Alert dispatcher            – send emails for conf ≥ 85% signals
  5. Module 5 News sentiment     – refresh research for holdings (cached 60 min)
  6. Module 7 SL/target monitor  – auto-close breached paper trades

Job identity: "heartbeat_5min" on the shared APScheduler instance.
max_instances=1 guarantees no overlapping runs.

Timing contract
---------------
Each step is wrapped in try/except so a failure in one step never
prevents the subsequent steps from running.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from models.session import SessionLocal
from scheduler.market_hours import is_market_open, market_status_summary

logger = logging.getLogger(__name__)


async def heartbeat_job() -> None:
    """
    Main 5-minute heartbeat coroutine executed by APScheduler.
    """
    tick_start = time.monotonic()
    now_utc    = datetime.now(timezone.utc)
    logger.info("═══ HEARTBEAT TICK  %s ═══", now_utc.strftime("%H:%M:%S UTC"))

    # ── Step 1: Market hours gate ─────────────────────────────────────────────
    if not is_market_open():
        status = market_status_summary()
        logger.info(
            "Market closed (%s, %s) — heartbeat skipped. Next open: %s",
            status["weekday"], status["ist_time"],
            status.get("next_open_utc", "N/A"),
        )
        return

    logger.info("Market OPEN — running full heartbeat pipeline")

    # Shared executor for blocking I/O steps
    loop = asyncio.get_event_loop()

    # ── Step 2: Live price refresh (open paper trades) ─────────────────────────
    try:
        await loop.run_in_executor(None, _refresh_live_prices)
    except Exception as exc:
        logger.error("[HEARTBEAT] Live price refresh failed: %s", exc, exc_info=True)

    # ── Step 3: Module 4 Signal Engine scan ───────────────────────────────────
    scan_results: list[dict] = []
    try:
        scan_results = await loop.run_in_executor(None, _run_signal_scan)
        logger.info("[HEARTBEAT] Signal scan produced %d results", len(scan_results))
    except Exception as exc:
        logger.error("[HEARTBEAT] Signal scan failed: %s", exc, exc_info=True)

    # ── Step 4: Alert dispatcher ──────────────────────────────────────────────
    if scan_results:
        try:
            await loop.run_in_executor(None, _dispatch_alerts, scan_results)
        except Exception as exc:
            logger.error("[HEARTBEAT] Alert dispatch failed: %s", exc, exc_info=True)

    # ── Step 5: Module 5 News sentiment refresh ───────────────────────────────
    try:
        await loop.run_in_executor(None, _refresh_news_sentiment)
    except Exception as exc:
        logger.error("[HEARTBEAT] News sentiment refresh failed: %s", exc, exc_info=True)

    # ── Step 6: SL/Target monitor ─────────────────────────────────────────────
    try:
        await loop.run_in_executor(None, _run_sl_monitor)
    except Exception as exc:
        logger.error("[HEARTBEAT] SL monitor failed: %s", exc, exc_info=True)

    elapsed = time.monotonic() - tick_start
    logger.info(
        "═══ HEARTBEAT DONE  %.1fs (%d signals, market open) ═══",
        elapsed, len(scan_results),
    )


# ─── Step implementations ─────────────────────────────────────────────────────

def _refresh_live_prices() -> None:
    """Refresh cached prices for all open paper trade symbols."""
    from models.database import PaperTrade, TradeStatus
    from services.paper.live_price import get_live_prices
    db = SessionLocal()
    try:
        open_trades = db.query(PaperTrade).filter(PaperTrade.status == TradeStatus.OPEN).all()
        if not open_trades:
            return
        symbols = list({t.symbol for t in open_trades})
        prices  = get_live_prices(symbols)
        hits    = sum(1 for p in prices.values() if p.valid)
        logger.info("[HEARTBEAT] Live prices refreshed: %d/%d symbols", hits, len(symbols))
    finally:
        db.close()


def _run_signal_scan() -> list[dict]:
    """Run the Module 4 RegimeAwareSignalEngine scan."""
    from engine.signals.signal_engine import get_signal_engine
    engine  = get_signal_engine()
    results = engine.run_scan()
    return results


def _dispatch_alerts(scan_results: list[dict]) -> None:
    """Evaluate scan results and send high-confidence email alerts."""
    from scheduler.alert_dispatcher import dispatch_alerts_for_scan
    db = SessionLocal()
    try:
        summary = dispatch_alerts_for_scan(scan_results, db)
        if summary["sent"] > 0:
            logger.info("[HEARTBEAT] Alerts sent: %d", summary["sent"])
    finally:
        db.close()


def _refresh_news_sentiment() -> None:
    """Refresh Module 5 research for portfolio holdings (respects 60-min cache)."""
    from models.database import Holding
    from services.research.deep_research_service import DeepResearchService
    db = SessionLocal()
    try:
        holdings = db.query(Holding).all()
        tickers  = [h.symbol for h in holdings]
        if not tickers:
            return
        svc    = DeepResearchService(db)
        result = svc.refresh_portfolio()
        logger.info(
            "[HEARTBEAT] News refresh: %d updated, %d cached, %d errors",
            result["refreshed"], result["cached"], result["errors"],
        )
    finally:
        db.close()


def _run_sl_monitor() -> None:
    """Run the Module 7 SL/target risk monitor."""
    from services.paper.risk_monitor import run_sl_target_monitor
    summary = run_sl_target_monitor()
    if summary.get("sl_hits", 0) or summary.get("target_hits", 0):
        logger.warning(
            "[HEARTBEAT] Risk events — SL: %d, TARGET: %d",
            summary["sl_hits"], summary["target_hits"],
        )
