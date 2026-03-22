"""
backend/main.py  — v9.2 (Production-Hardened)

FIX 4: APScheduler timezone hardcoded to "Asia/Kolkata".
        All cron jobs now expressed in IST — no UTC offset confusion.
        Weekly backtest: Saturday 06:30 IST | Weekly report: Saturday 23:30 IST.
FIX 5: CORS allow_origins pulled from FRONTEND_URL env var (not wildcard).
        allow_credentials=True is safe because origins are now explicit.
Other:  yfinance cache dir set to /tmp/yf_cache on startup.
        datetime.utcnow() replaced with datetime.now(timezone.utc) (deprecation fix).
"""

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.core.config         import get_settings
from backend.core.database       import init_db
from backend.core.logging_config import configure_logging

cfg = get_settings()

configure_logging(log_dir="logs", level=10 if cfg.DEBUG else 20)
logger = logging.getLogger(__name__)

import yfinance as yf
yf.set_tz_cache_location("/tmp/yf_cache")


# ── Scheduler ─────────────────────────────────────────────────────────────────

def _build_scheduler():
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED

    # FIX 4: Timezone explicitly set to Asia/Kolkata
    scheduler = AsyncIOScheduler(
        timezone    = "Asia/Kolkata",
        job_defaults= {"coalesce": True, "max_instances": 1, "misfire_grace_time": 120},
    )

    def _on_error(event):
        logger.error("Scheduler job '%s' raised: %s", event.job_id, event.exception)
    def _on_missed(event):
        logger.warning("Scheduler job '%s' missed its fire time", event.job_id)

    scheduler.add_listener(_on_error,  EVENT_JOB_ERROR)
    scheduler.add_listener(_on_missed, EVENT_JOB_MISSED)

    # ── 5-min heartbeat (market hours gate inside) ────────────────────────────
    async def _heartbeat():
        import asyncio
        from zoneinfo import ZoneInfo
        from datetime import time as dtime
        from backend.services.signal_engine import get_signal_engine

        try:
            IST  = ZoneInfo("Asia/Kolkata")
            now  = datetime.now(IST)
            open_t  = dtime(cfg.MARKET_OPEN_HOUR_IST,  cfg.MARKET_OPEN_MIN_IST)
            close_t = dtime(cfg.MARKET_CLOSE_HOUR_IST, cfg.MARKET_CLOSE_MIN_IST)
            if now.weekday() > 4 or not (open_t <= now.time() <= close_t):
                logger.debug("Market closed (%s %s) — heartbeat skipped",
                             now.strftime("%A"), now.strftime("%H:%M IST"))
                return
        except Exception as exc:
            logger.warning("Market hours check failed: %s", exc)

        logger.info("══ HEARTBEAT START ══")
        loop = asyncio.get_event_loop()

        scan_results = []
        try:
            engine_obj   = get_signal_engine()
            scan_results = await loop.run_in_executor(None, engine_obj.run_scan)
            logger.info("Heartbeat: %d signals produced", len(scan_results))
        except Exception as exc:
            logger.error("Heartbeat scan failed: %s", exc, exc_info=True)

        if scan_results:
            try:
                await loop.run_in_executor(None, _dispatch_alerts, scan_results)
            except Exception as exc:
                logger.error("Alert dispatch failed: %s", exc)

        try:
            await loop.run_in_executor(None, _run_sl_monitor)
        except Exception as exc:
            logger.error("SL monitor failed: %s", exc)

        logger.info("══ HEARTBEAT DONE ══")

    scheduler.add_job(_heartbeat, "interval", seconds=cfg.HEARTBEAT_INTERVAL_SECS,
                      id="heartbeat_5min", name="5-Min Market Heartbeat")

    # ── Regime detector every 5 min ───────────────────────────────────────────
    async def _regime_job():
        import asyncio
        from backend.services.regime_service import get_regime_service
        try:
            await asyncio.get_event_loop().run_in_executor(
                None, get_regime_service().detect_and_persist
            )
        except Exception as exc:
            logger.error("Regime job failed: %s", exc)

    scheduler.add_job(_regime_job, "interval", seconds=300,
                      id="regime_detector", name="Market Regime Detector")

    # ── Portfolio research refresh every 60 min ───────────────────────────────
    async def _research_refresh():
        import asyncio
        from backend.core.database    import get_db_context
        from backend.models.portfolio import Holding
        from backend.services.news_service import get_news_service
        try:
            with get_db_context() as db:
                tickers = [h.symbol for h in db.query(Holding).all()]
            svc = get_news_service()
            for t in tickers:
                try:
                    with get_db_context() as db:
                        svc.analyse(t, db)
                except Exception as exc:
                    logger.warning("Research refresh failed for %s: %s", t, exc)
        except Exception as exc:
            logger.error("Research refresh job failed: %s", exc)

    scheduler.add_job(_research_refresh, "interval", seconds=3600,
                      id="research_refresh", name="Portfolio Research Refresh")

    # ── Weekly backtest — Saturday 06:30 IST (was 01:00 UTC) ─────────────────
    async def _weekly_backtest():
        import asyncio
        from backend.core.database    import get_db_context
        from backend.models.portfolio import Holding
        from backend.services.quant_service import get_quant_service
        try:
            with get_db_context() as db:
                tickers = [h.symbol for h in db.query(Holding).all()]
            svc = get_quant_service()
            for t in tickers:
                await asyncio.get_event_loop().run_in_executor(
                    None, svc.run_backtest_for_ticker, t
                )
                import time; time.sleep(3)   # yfinance rate-limit protection
            logger.info("Weekly backtest complete for %d tickers", len(tickers))
        except Exception as exc:
            logger.error("Weekly backtest failed: %s", exc, exc_info=True)

    # FIX 4: hour/minute expressed in IST because scheduler TZ = Asia/Kolkata
    scheduler.add_job(
        _weekly_backtest, "cron",
        day_of_week = cfg.WEEKLY_BACKTEST_DAY,
        hour        = cfg.WEEKLY_BACKTEST_HOUR_IST,
        minute      = cfg.WEEKLY_BACKTEST_MINUTE_IST,
        id="weekly_backtest", name="Weekly Backtest Refresh (Sat 06:30 IST)",
    )

    # ── Weekly P&L report — Saturday 23:30 IST ───────────────────────────────
    async def _weekly_report():
        import asyncio
        from backend.core.database import get_db_context
        from backend.services.paper.weekly_report import generate_weekly_report
        try:
            with get_db_context() as db:
                await asyncio.get_event_loop().run_in_executor(
                    None, lambda: generate_weekly_report(db)
                )
        except Exception as exc:
            logger.error("Weekly report failed: %s", exc)

    scheduler.add_job(
        _weekly_report, "cron",
        day_of_week = cfg.WEEKLY_REPORT_DAY,
        hour        = cfg.WEEKLY_REPORT_HOUR_IST,
        minute      = cfg.WEEKLY_REPORT_MINUTE_IST,
        id="weekly_report", name="Weekly P&L Report (Sat 23:30 IST)",
    )

    logger.info("Scheduler configured — timezone: Asia/Kolkata — %d jobs", len(scheduler.get_jobs()))
    return scheduler


def _dispatch_alerts(scan_results: list[dict]) -> None:
    """Email alerts for signals with confidence >= ALERT_CONFIDENCE_THRESHOLD."""
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text      import MIMEText
    from backend.core.database import get_db_context
    from backend.models.alerts import AlertDispatchLog
    from datetime import timedelta

    for sig in scan_results:
        conf   = float(sig.get("confidence", 0))
        signal = sig.get("signal", "HOLD")
        ticker = sig.get("ticker", "")
        if signal.upper() in ("HOLD", "CASH") or conf < cfg.ALERT_CONFIDENCE_THRESHOLD:
            continue

        with get_db_context() as db:
            today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            daily = db.query(AlertDispatchLog).filter(AlertDispatchLog.sent_at >= today).count()
            if daily >= cfg.MAX_ALERTS_PER_DAY:
                logger.info("Daily alert cap (%d) reached — suppressing", cfg.MAX_ALERTS_PER_DAY)
                break

            dedup = datetime.now(timezone.utc) - timedelta(minutes=60)
            if db.query(AlertDispatchLog).filter(
                AlertDispatchLog.ticker      == ticker,
                AlertDispatchLog.signal_type == signal,
                AlertDispatchLog.sent_at     >= dedup,
            ).first():
                continue

            if cfg.ALERT_EMAIL_FROM and cfg.GMAIL_APP_PASSWORD:
                subject = f"Quantedge Alert: {ticker} {signal} {conf:.0f}% [{sig.get('regime','')}]"
                body = (
                    f"<h2>Quantedge Signal Alert</h2>"
                    f"<p><b>Ticker:</b> {ticker} | <b>Signal:</b> {signal} | <b>Confidence:</b> {conf:.0f}%</p>"
                    f"<p><b>Strategy:</b> {sig.get('selected_strategy','')}</p>"
                    f"<p><b>Reason:</b> {sig.get('reason','')}</p>"
                    f"<p><b>Sentiment:</b> {sig.get('sentiment_score','N/A')} ({sig.get('sentiment_label','N/A')})</p>"
                    f"<p><b>TradingView:</b> {sig.get('source_confirmations',{}).get('tradingview_summary','N/A')}</p>"
                    f"<p><b>Moneycontrol:</b> {sig.get('source_confirmations',{}).get('moneycontrol_mood','N/A')}</p>"
                    f"<p><a href='{cfg.FRONTEND_BASE_URL}/signals?ticker={ticker}'>View Full Analysis</a></p>"
                    f"<hr><small>Rate limited to {cfg.MAX_ALERTS_PER_DAY} alerts/day</small>"
                )
                try:
                    msg = MIMEMultipart("alternative")
                    msg["Subject"] = subject
                    msg["From"]    = cfg.ALERT_EMAIL_FROM
                    msg["To"]      = cfg.ALERT_EMAIL_TO
                    msg.attach(MIMEText(body, "html"))
                    with smtplib.SMTP(cfg.SMTP_HOST, cfg.SMTP_PORT, timeout=15) as s:
                        s.starttls()
                        s.login(cfg.ALERT_EMAIL_FROM, cfg.GMAIL_APP_PASSWORD)
                        s.sendmail(cfg.ALERT_EMAIL_FROM, cfg.ALERT_EMAIL_TO, msg.as_string())
                    logger.info("Alert sent: %s %s %.0f%%", ticker, signal, conf)
                except Exception as exc:
                    logger.error("Alert email failed: %s", exc)

            db.add(AlertDispatchLog(
                ticker=ticker, signal_type=signal, confidence=conf,
                regime=sig.get("regime", ""), channel="EMAIL",
                subject=f"{ticker} {signal} {conf:.0f}%",
            ))


def _run_sl_monitor() -> None:
    """Auto-close paper trades whose SL or target has been breached."""
    import yfinance as yf
    from backend.core.database import get_db_context
    from backend.models.paper  import (
        LedgerEntryType, PaperTrade, TradeDirection, TradeStatus, VirtualLedger,
    )

    with get_db_context() as db:
        open_trades = db.query(PaperTrade).filter(PaperTrade.status == TradeStatus.OPEN).all()
        if not open_trades:
            return
        prices: dict = {}
        for sym in {t.symbol for t in open_trades}:
            try:
                info  = yf.Ticker(f"{sym}.NS").fast_info
                price = float(info.get("last_price") or info.get("previous_close") or 0)
                if price > 0:
                    prices[sym] = price
            except Exception:
                pass

        for trade in open_trades:
            ltp = prices.get(trade.symbol)
            if not ltp:
                continue
            is_buy = trade.direction == TradeDirection.BUY
            sl_hit = trade.stop_loss and (ltp <= trade.stop_loss if is_buy else ltp >= trade.stop_loss)
            tg_hit = trade.target    and (ltp >= trade.target    if is_buy else ltp <= trade.target)
            reason = "SL_HIT" if sl_hit else ("TARGET_HIT" if tg_hit else None)
            if not reason:
                continue
            exit_price       = trade.stop_loss if sl_hit else trade.target
            trade.exit_price = exit_price
            trade.exit_time  = datetime.now(timezone.utc)
            trade.status     = TradeStatus.CLOSED
            trade.pnl        = ((exit_price - trade.entry_price) if is_buy
                                else (trade.entry_price - exit_price)) * trade.quantity
            trade.pnl_pct    = trade.pnl / (trade.entry_price * trade.quantity) * 100
            gross      = exit_price * trade.quantity
            commission = gross * cfg.COMMISSION_PCT
            db.add(VirtualLedger(
                trade_id=trade.id, symbol=trade.symbol,
                entry_type=LedgerEntryType(reason),
                price=exit_price, quantity=trade.quantity,
                gross_value=gross, commission=commission, net_value=gross - commission,
                realised_pnl=trade.pnl, realised_pnl_pct=trade.pnl_pct,
                close_reason=reason,
            ))
            logger.warning("AUTO-CLOSE %s (id=%d) %s @ %.2f | P&L %.2f",
                           trade.symbol, trade.id, reason, exit_price, trade.pnl)


# ── Lifespan ──────────────────────────────────────────────────────────────────

_scheduler = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _scheduler
    logger.info("=" * 70)
    logger.info("  QUANTEDGE v%s  —  STARTING UP", cfg.APP_VERSION)
    logger.info("  Scheduler TZ: Asia/Kolkata | DB: %s",
                "PostgreSQL" if cfg.DATABASE_URL else "SQLite")
    logger.info("=" * 70)

    init_db()

    try:
        _scheduler = _build_scheduler()
        _scheduler.start()
        logger.info("Scheduler started: %s", ", ".join(j.id for j in _scheduler.get_jobs()))
    except Exception as exc:
        logger.critical("Scheduler failed to start: %s", exc, exc_info=True)

    yield

    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
    logger.info("  QUANTEDGE — SHUT DOWN")


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(
    title       = cfg.APP_NAME,
    version     = cfg.APP_VERSION,
    description = (
        "Institutional AI Trading Dashboard — Modules 1-8.\n\n"
        "**Auth**: POST /api/auth/login → POST /api/auth/verify-otp → Bearer token."
    ),
    lifespan  = lifespan,
    docs_url  = "/docs",
    redoc_url = "/redoc",
)

# FIX 5: CORS — explicit origin list from FRONTEND_URL, not wildcard
_cors_origins = [o.strip() for o in cfg.FRONTEND_URL.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins     = _cors_origins,
    allow_credentials = True,          # Safe because origins are now explicit
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.critical("Unhandled [%s %s]: %s", request.method, request.url.path, exc, exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"error": "Internal server error", "detail": str(exc)},
    )


from backend.api.routers.auth        import router as auth_router
from backend.api.routers.market_data import router as market_data_router
from backend.api.routers.dashboard   import router as dashboard_router
from backend.api.routers.trading     import router as trading_router

app.include_router(auth_router,        prefix="/api")
app.include_router(market_data_router, prefix="/api")
app.include_router(dashboard_router,   prefix="/api")
app.include_router(trading_router,     prefix="/api")


@app.get("/health", tags=["System"])
def health():
    from sqlalchemy import text
    from backend.core.database import engine
    db_ok = False
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        pass
    return {
        "status":       "ok",
        "version":      cfg.APP_VERSION,
        "database":     "ok" if db_ok else "error",
        "db_driver":    "postgresql" if cfg.DATABASE_URL else "sqlite",
        "scheduler":    "running" if (_scheduler and _scheduler.running) else "stopped",
        "active_jobs":  len(_scheduler.get_jobs()) if _scheduler else 0,
        "scheduler_tz": "Asia/Kolkata",
    }


@app.get("/", tags=["System"])
def root():
    return {
        "message": cfg.APP_NAME, "version": cfg.APP_VERSION,
        "docs": "/docs", "health": "/health",
        "api": {"auth": "/api/auth", "dashboard": "/api/dashboard", "trading": "/api/trading"},
    }
