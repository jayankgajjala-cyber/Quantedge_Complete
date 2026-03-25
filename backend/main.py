"""
backend/main.py  — v9.5 (Observability-Hardened)

Changes from v9.4:
  - Logging routed through backend.utils.logger (setup_logging / get_logger).
    Format: [timestamp] [LEVEL   ] [module]: message  → clean Railway console lines.
  - Global @app.exception_handler(Exception) returns structured JSON:
      { "error": "InternalServerError", "message": "<hint>", "detail": "<exc>",
        "path": "<route>" }
  - Startup pre-flight diagnostics logs:
      • DB driver + sanitised connection URL (password masked)
      • /tmp/parquet writability probe
      • Active CORS origins list
  - All URL paths, router mounts, scheduler jobs, and business logic UNCHANGED.
"""

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.core.config   import get_settings
from backend.core.database import init_db
from backend.utils.logger  import setup_logging, get_logger

cfg = get_settings()

# ── Logging bootstrap ─────────────────────────────────────────────────────────
setup_logging(log_dir="logs", level=logging.DEBUG if cfg.DEBUG else logging.INFO)
logger = get_logger(__name__)

import yfinance as yf
yf.set_tz_cache_location("/tmp/yf_cache")


# ── Scheduler ─────────────────────────────────────────────────────────────────

def _build_scheduler():
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED

    scheduler = AsyncIOScheduler(
        timezone    = "Asia/Kolkata",
        job_defaults= {"coalesce": True, "max_instances": 1, "misfire_grace_time": 120},
    )

    def _on_error(event):
        logger.error(
            "Scheduler job '%s' raised: %s",
            event.job_id, event.exception,
            exc_info=event.traceback,
        )
    def _on_missed(event):
        logger.warning("Scheduler job '%s' missed its fire time", event.job_id)

    scheduler.add_listener(_on_error,  EVENT_JOB_ERROR)
    scheduler.add_listener(_on_missed, EVENT_JOB_MISSED)

    # ── 5-min heartbeat ───────────────────────────────────────────────────────
    async def _heartbeat():
        import asyncio
        from zoneinfo import ZoneInfo
        from datetime import time as dtime
        from backend.services.signal_engine import get_signal_engine

        try:
            IST     = ZoneInfo("Asia/Kolkata")
            now     = datetime.now(IST)
            open_t  = dtime(cfg.MARKET_OPEN_HOUR_IST,  cfg.MARKET_OPEN_MIN_IST)
            close_t = dtime(cfg.MARKET_CLOSE_HOUR_IST, cfg.MARKET_CLOSE_MIN_IST)
            if now.weekday() > 4 or not (open_t <= now.time() <= close_t):
                logger.debug(
                    "Market closed (%s %s) — heartbeat skipped",
                    now.strftime("%A"), now.strftime("%H:%M IST"),
                )
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
                logger.error("Alert dispatch failed: %s", exc, exc_info=True)

        try:
            await loop.run_in_executor(None, _run_sl_monitor)
        except Exception as exc:
            logger.error("SL monitor failed: %s", exc, exc_info=True)

        logger.info("══ HEARTBEAT DONE ══")

    scheduler.add_job(
        _heartbeat, "interval", seconds=cfg.HEARTBEAT_INTERVAL_SECS,
        id="heartbeat_5min", name="5-Min Market Heartbeat",
    )

    # ── Regime detector every 5 min ───────────────────────────────────────────
    async def _regime_job():
        import asyncio
        from backend.services.regime_service import get_regime_service
        try:
            await asyncio.get_event_loop().run_in_executor(
                None, get_regime_service().detect_and_persist
            )
        except Exception as exc:
            logger.error("Regime job failed: %s", exc, exc_info=True)

    scheduler.add_job(
        _regime_job, "interval", seconds=300,
        id="regime_detector", name="Market Regime Detector",
    )

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
            logger.error("Research refresh job failed: %s", exc, exc_info=True)

    scheduler.add_job(
        _research_refresh, "interval", seconds=3600,
        id="research_refresh", name="Portfolio Research Refresh",
    )

    # ── Weekly backtest — Saturday 06:30 IST ──────────────────────────────────
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
                import time; time.sleep(3)
            logger.info("Weekly backtest complete for %d tickers", len(tickers))
        except Exception as exc:
            logger.error("Weekly backtest failed: %s", exc, exc_info=True)

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
            logger.error("Weekly report failed: %s", exc, exc_info=True)

    scheduler.add_job(
        _weekly_report, "cron",
        day_of_week = cfg.WEEKLY_REPORT_DAY,
        hour        = cfg.WEEKLY_REPORT_HOUR_IST,
        minute      = cfg.WEEKLY_REPORT_MINUTE_IST,
        id="weekly_report", name="Weekly P&L Report (Sat 23:30 IST)",
    )

    logger.info(
        "Scheduler configured — timezone: Asia/Kolkata — %d jobs",
        len(scheduler.get_jobs()),
    )
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
                    logger.error("Alert email failed: %s", exc, exc_info=True)

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
            logger.warning(
                "AUTO-CLOSE %s (id=%d) %s @ %.2f | P&L %.2f",
                trade.symbol, trade.id, reason, exit_price, trade.pnl,
            )


# ── Pre-flight diagnostic helpers ─────────────────────────────────────────────

def _preflight_db() -> str:
    """Log DB driver and sanitised URL (password masked). Returns driver label."""
    db_url = cfg.DATABASE_URL or ""
    if db_url:
        try:
            from urllib.parse import urlparse, urlunparse
            parsed   = urlparse(db_url)
            netloc   = parsed.netloc.replace(f":{parsed.password}@", ":***@") if parsed.password else parsed.netloc
            safe_url = urlunparse(parsed._replace(netloc=netloc))
        except Exception:
            safe_url = "<url-parse-error>"
        logger.info("  DB │ Driver   : PostgreSQL (NullPool — Supabase/pgbouncer mode)")
        logger.info("  DB │ Attempting connection to: %s", safe_url)
        return "PostgreSQL"
    else:
        logger.info("  DB │ Driver   : SQLite (local dev — data lost on container restart)")
        logger.warning(
            "  DB │ WARNING  : DATABASE_URL not set. "
            "Set DATABASE_URL=postgresql://... in Railway env vars before production deploy."
        )
        return "SQLite"


def _preflight_parquet() -> bool:
    """Probe /tmp/parquet for writability. Returns True if writable."""
    parquet_dir = "/tmp/parquet"
    try:
        os.makedirs(parquet_dir, exist_ok=True)
        probe = os.path.join(parquet_dir, ".write_probe")
        with open(probe, "w") as fh:
            fh.write("ok")
        os.remove(probe)
        logger.info("  FS │ /tmp/parquet  : writable ✓")
        return True
    except Exception as exc:
        logger.error("  FS │ /tmp/parquet  : NOT writable ✗ — %s", exc, exc_info=True)
        return False


def _preflight_yf_cache() -> None:
    """Ensure /tmp/yf_cache exists."""
    try:
        os.makedirs("/tmp/yf_cache", exist_ok=True)
        logger.info("  FS │ /tmp/yf_cache : writable ✓")
    except Exception as exc:
        logger.error("  FS │ /tmp/yf_cache : NOT writable ✗ — %s", exc)


def _preflight_cors(origins: list[str]) -> None:
    """Log the active CORS origin list."""
    if "*" in origins:
        logger.warning(
            "  CORS │ Allowing ALL origins (*). "
            "Set CORS_ORIGINS=https://your-frontend.vercel.app in Railway to restrict."
        )
    else:
        logger.info("  CORS │ Allowed origins (%d):", len(origins))
        for o in origins:
            logger.info("         • %s", o)


# ── Lifespan ──────────────────────────────────────────────────────────────────

_scheduler = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _scheduler

    logger.info("=" * 70)
    logger.info("  QUANTEDGE v%s  —  STARTING UP", cfg.APP_VERSION)
    logger.info("=" * 70)

    # Pre-flight check 1: Database
    logger.info("[ PRE-FLIGHT 1/3 ] Database")
    db_driver = _preflight_db()

    # Pre-flight check 2: Filesystem
    logger.info("[ PRE-FLIGHT 2/3 ] Filesystem")
    _preflight_parquet()
    _preflight_yf_cache()

    # Pre-flight check 3: CORS
    logger.info("[ PRE-FLIGHT 3/3 ] CORS")
    _preflight_cors(_cors_origins)

    logger.info("=" * 70)
    logger.info("  DB driver: %s | Scheduler TZ: Asia/Kolkata", db_driver)
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

# ── CORS ──────────────────────────────────────────────────────────────────────
_cors_origins = [
    origin.strip().rstrip("/")
    for origin in cfg.CORS_ORIGINS.split(",")
    if origin.strip()
]
_allow_all = "*" in _cors_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins     = ["*"] if _allow_all else _cors_origins,
    allow_credentials = False if _allow_all else True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)


# ── Global exception handler ──────────────────────────────────────────────────

def _extract_table_name(exc_str: str) -> str:
    """Extract table name from SQLAlchemy error string."""
    import re
    m = re.search(r'relation "([^"]+)"', exc_str)
    if m:
        return m.group(1)
    m = re.search(r'no such table:\s*(\S+)', exc_str)
    if m:
        return m.group(1)
    return "unknown"


def _exception_hint(exc: Exception, path: str) -> str:
    """
    Return a concise, actionable hint string for a given exception.
    These appear in the structured JSON error body returned to the frontend.
    """
    import sqlalchemy.exc as sa_exc

    exc_type = type(exc).__name__
    exc_str  = str(exc).lower()

    if isinstance(exc, sa_exc.OperationalError):
        if "could not connect" in exc_str or "connection refused" in exc_str:
            return (
                "Cannot connect to Supabase. "
                "Check DATABASE_URL in Railway environment variables."
            )
        if "no such table" in exc_str or ("relation" in exc_str and "does not exist" in exc_str):
            table = _extract_table_name(str(exc).lower())
            return (
                f"Database table '{table}' not found in Supabase. "
                "Run init_db() or check your migration history."
            )
        return f"Database operational error on {path}. Check Railway DB logs."

    if isinstance(exc, sa_exc.ProgrammingError):
        table = _extract_table_name(str(exc).lower())
        return (
            f"Database schema error — table '{table}' may be missing from Supabase. "
            "Verify DATABASE_URL points to the correct project."
        )

    if isinstance(exc, sa_exc.IntegrityError):
        return (
            "Database integrity error — duplicate key or constraint violation. "
            f"Check the data being written on {path}."
        )

    if isinstance(exc, sa_exc.TimeoutError):
        return (
            "Supabase connection timed out. "
            "The database may be cold-starting — retry in 10–30 seconds."
        )

    if isinstance(exc, (ValueError, TypeError)) and "json" in exc_str:
        return (
            f"JSON serialisation failed on {path}. "
            "A model field may contain a non-serialisable type."
        )

    if "yfinance" in exc_str or "no data found" in exc_str:
        return (
            "yfinance returned no data. "
            "Ticker may be delisted or NSE markets may be closed."
        )

    if exc_type in ("ConnectionError", "ConnectTimeout", "ReadTimeout"):
        return (
            f"Network timeout on {path}. "
            "An external API (yfinance, HuggingFace, NewsAPI) may be unreachable."
        )

    if exc_type in ("JWTError", "DecodeError", "InvalidTokenError"):
        return "JWT token is invalid or expired. Please log in again."

    if isinstance(exc, (FileNotFoundError, PermissionError)):
        return (
            f"Filesystem error on {path}: {exc_type}. "
            "Check /tmp/parquet and /tmp/yf_cache are writable on Railway."
        )

    return (
        f"Unexpected {exc_type} on {path}. "
        "Check Railway backend logs for the full traceback."
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Catch any unhandled exception that escapes a route handler.

    1. Logs the full traceback to stdout → visible in Railway logs with file
       name and exact line number.
    2. Returns structured JSON to the frontend:
       { "error": "InternalServerError", "message": "<hint>",
         "detail": "<exc message>", "path": "<route>" }
    """
    exc_type = type(exc).__name__
    path     = request.url.path

    logger.critical(
        "Unhandled %s on [%s %s]: %s",
        exc_type, request.method, path, exc,
        exc_info=True,
    )

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error":   "InternalServerError",
            "message": _exception_hint(exc, path),
            "detail":  str(exc),
            "path":    path,
        },
    )


# ── Router registration ───────────────────────────────────────────────────────
# URL paths FROZEN — do not change prefixes.
# dashboard_router prefix="/dashboard" → /api/dashboard/* (matches useData.ts SWR hooks)
# trading_router   prefix="/trading"   → /api/trading/*   (matches useData.ts SWR hooks)

from backend.api.routers.auth        import router as auth_router
from backend.api.routers.market_data import router as market_data_router
from backend.api.routers.dashboard   import router as dashboard_router
from backend.api.routers.trading     import router as trading_router

app.include_router(auth_router,        prefix="/api")
app.include_router(market_data_router, prefix="/api")
app.include_router(dashboard_router,   prefix="/api")
app.include_router(trading_router,     prefix="/api")


# ── System endpoints ──────────────────────────────────────────────────────────

@app.get("/health", tags=["System"])
def health():
    from sqlalchemy import text
    from backend.core.database import engine

    db_ok    = False
    db_error = None
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception as exc:
        db_error = str(exc)

    parquet_dir      = "/tmp/parquet"
    parquet_writable = False
    try:
        os.makedirs(parquet_dir, exist_ok=True)
        probe = os.path.join(parquet_dir, ".write_probe")
        with open(probe, "w") as fh:
            fh.write("ok")
        os.remove(probe)
        parquet_writable = True
    except Exception:
        pass

    return {
        "status":           "ok",
        "version":          cfg.APP_VERSION,
        "database":         "ok" if db_ok else "error",
        "db_driver":        "postgresql" if cfg.DATABASE_URL else "sqlite",
        "db_error":         db_error,
        "scheduler":        "running" if (_scheduler and _scheduler.running) else "stopped",
        "active_jobs":      len(_scheduler.get_jobs()) if _scheduler else 0,
        "scheduler_tz":     "Asia/Kolkata",
        "parquet_cache":    parquet_dir,
        "parquet_writable": parquet_writable,
        "cors_origins":     _cors_origins,
    }


@app.get("/", tags=["System"])
def root():
    return {
        "message": cfg.APP_NAME,
        "version": cfg.APP_VERSION,
        "docs":    "/docs",
        "health":  "/health",
        "api": {
            "auth":      "/api/auth",
            "dashboard": "/api/dashboard",
            "trading":   "/api/trading",
        },
    }
