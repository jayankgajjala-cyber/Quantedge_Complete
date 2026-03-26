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

from backend.core.database import SessionLocal
from backend.scheduler.market_hours import is_market_open, market_status_summary

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
    from backend.models.paper import PaperTrade, TradeStatus
    import yfinance as _yf
    db = SessionLocal()
    try:
        open_trades = db.query(PaperTrade).filter(PaperTrade.status == TradeStatus.OPEN).all()
        if not open_trades:
            return
        symbols = list({t.symbol for t in open_trades})
        prices  = {}
        for sym in symbols:
            try:
                info = _yf.Ticker(f"{sym}.NS").fast_info
                p = float(info.get("last_price") or info.get("previous_close") or 0)
                if p > 0:
                    prices[sym] = p
            except Exception:
                pass
        hits    = len(prices)
        logger.info("[HEARTBEAT] Live prices refreshed: %d/%d symbols", hits, len(symbols))
    finally:
        db.close()


def _run_signal_scan() -> list[dict]:
    """Run the Module 4 RegimeAwareSignalEngine scan."""
    from backend.engine.signals.signal_engine import get_signal_engine
    engine  = get_signal_engine()
    results = engine.run_scan()
    return results


def _dispatch_alerts(scan_results: list[dict]) -> None:
    """Evaluate scan results and send high-confidence email alerts."""
    from backend.scheduler.alert_dispatcher import dispatch_alerts_for_scan
    db = SessionLocal()
    try:
        summary = dispatch_alerts_for_scan(scan_results, db)
        if summary["sent"] > 0:
            logger.info("[HEARTBEAT] Alerts sent: %d", summary["sent"])
    finally:
        db.close()


def _refresh_news_sentiment() -> None:
    """Refresh Module 5 research for portfolio holdings (respects 60-min cache)."""
    from backend.models.portfolio import Holding
    from backend.services.news_service import get_news_service as _get_news_svc
    db = SessionLocal()
    try:
        holdings = db.query(Holding).all()
        tickers  = [h.symbol for h in holdings]
        if not tickers:
            return
        svc = _get_news_svc()
        refreshed = cached = errors = 0
        for ticker in tickers:
            try:
                svc.analyse(ticker, db)
                refreshed += 1
            except Exception as _e:
                logger.warning("[HEARTBEAT] News refresh failed for %s: %s", ticker, _e)
                errors += 1
        logger.info(
            "[HEARTBEAT] News refresh: %d updated, %d cached, %d errors",
            refreshed, cached, errors,
        )
    finally:
        db.close()


def _run_sl_monitor() -> None:
    """Run the Module 7 SL/target risk monitor."""
    from backend.core.database import get_db_context as _gdc
    from backend.models.paper import PaperTrade, TradeStatus, TradeDirection, VirtualLedger, LedgerEntryType
    from backend.core.config import get_settings as _gs
    import yfinance as _yf2
    from datetime import datetime, timezone
    _cfg2 = _gs()
    sl_hits = target_hits = 0
    with _gdc() as _db:
        open_trades = _db.query(PaperTrade).filter(PaperTrade.status == TradeStatus.OPEN).all()
        prices = {}
        for sym in {t.symbol for t in open_trades}:
            try:
                info = _yf2.Ticker(f"{sym}.NS").fast_info
                p = float(info.get("last_price") or info.get("previous_close") or 0)
                if p > 0: prices[sym] = p
            except Exception: pass
        for trade in open_trades:
            ltp = prices.get(trade.symbol)
            if not ltp: continue
            is_buy = trade.direction == TradeDirection.BUY
            sl_hit = trade.stop_loss and (ltp <= trade.stop_loss if is_buy else ltp >= trade.stop_loss)
            tg_hit = trade.target and (ltp >= trade.target if is_buy else ltp <= trade.target)
            reason = "SL_HIT" if sl_hit else ("TARGET_HIT" if tg_hit else None)
            if not reason: continue
            exit_p = trade.stop_loss if sl_hit else trade.target
            trade.exit_price = exit_p; trade.exit_time = datetime.now(timezone.utc)
            trade.status = TradeStatus.CLOSED
            trade.pnl = ((exit_p - trade.entry_price) if is_buy else (trade.entry_price - exit_p)) * trade.quantity
            trade.pnl_pct = trade.pnl / (trade.entry_price * trade.quantity) * 100
            _db.add(VirtualLedger(trade_id=trade.id, symbol=trade.symbol,
                entry_type=LedgerEntryType(reason), price=exit_p, quantity=trade.quantity,
                gross_value=exit_p*trade.quantity, commission=exit_p*trade.quantity*_cfg2.COMMISSION_PCT,
                net_value=exit_p*trade.quantity*(1-_cfg2.COMMISSION_PCT),
                realised_pnl=trade.pnl, realised_pnl_pct=trade.pnl_pct, close_reason=reason))
            if sl_hit: sl_hits += 1
            else: target_hits += 1
    summary = {"sl_hits": sl_hits, "target_hits": target_hits}
    if summary.get("sl_hits", 0) or summary.get("target_hits", 0):
        logger.warning(
            "[HEARTBEAT] Risk events — SL: %d, TARGET: %d",
            summary["sl_hits"], summary["target_hits"],
        )
