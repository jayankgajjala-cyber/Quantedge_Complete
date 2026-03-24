"""
backend/main.py  — v10.0 (Production-Ready)

CHANGES FROM v9.2:
─────────────────────────────────────────────────────────────────────────────
FIX 1 — ROUTE PREFIX (the 404 root cause):
    Routers were mounted at prefix="/api", but each router already had its
    own prefix (/auth, /dashboard, /trading, /market). This produced:
        /api/auth/...        ✓  frontend calls   /api/auth/...
        /api/dashboard/...   ✓  frontend calls   /api/dashboard/...
        /api/trading/...     ✓  frontend calls   /api/trading/...
        /api/market/...      ✓  frontend calls   /api/market/...
    The original code was CORRECT for these routes. The 404s were caused by
    the health endpoint being at /health while the frontend and Railway
    healthcheck both probe /api/health. Fixed: health moved to /api/health.
    The root / is kept for Railway's "service running" check.

FIX 2 — CORS:
    allow_origins now reads CORS_ORIGINS env var (comma-separated list).
    Default is still "*" so local dev works without config, but production
    should set: CORS_ORIGINS=https://your-app.vercel.app
    allow_credentials is set to True only when specific origins are given
    (browser blocks credentials with wildcard origins).

FIX 3 — STRUCTURED LOGGING:
    Replaced logging_config with utils/logger.py which streams full
    tracebacks to Railway terminal. LoggingMiddleware times every request
    and logs method/path/status/duration.

FIX 4 — GLOBAL EXCEPTION HANDLER:
    All unhandled exceptions now return structured JSON:
        {"error": "INTERNAL_ERROR", "message": "...", "path": "...", "request_id": "..."}
    Never returns a blank 500 page again.

FIX 5 — /api/health ENDPOINT:
    - Checks Supabase/PostgreSQL connection with a real SELECT
    - Returns scheduler status + active job count
    - Returns 200 with status="degraded" (not 500) when DB is unavailable
      so Railway doesn't restart the pod on a transient DB hiccup

FIX 6 — PARQUET CACHE:
    yfinance cache and parquet cache both forced to /tmp/* at startup.
    Prevents "Read-only filesystem" errors on Railway's ephemeral FS.
─────────────────────────────────────────────────────────────────────────────
"""

import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# FIX 3: Use structured logger before anything else
from backend.utils.logger import (
    configure_logging,
    get_logger,
    log_startup_banner,
    log_request,
)
from backend.core.config   import get_settings
from backend.core.database import init_db

cfg = get_settings()

# Configure logging FIRST — before any other import that might call getLogger
configure_logging(
    log_dir  = "/tmp/logs",   # /tmp is always writable on Railway
    level    = logging.DEBUG if cfg.DEBUG else logging.INFO,
    use_color= True,
)
logger = get_logger(__name__)

# FIX 6: Force all caches to /tmp immediately after logging is up
import yfinance as yf
yf.set_tz_cache_location("/tmp/yf_cache")
os.makedirs("/tmp/parquet",  exist_ok=True)
os.makedirs("/tmp/yf_cache", exist_ok=True)
logger.info("Cache dirs: /tmp/parquet, /tmp/yf_cache")


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
            event.job_id, event.exception, exc_info=(type(event.exception), event.exception, event.traceback)
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
                logger.error("Alert dispatch failed: %s", exc, exc_info=True)

        try:
            await loop.run_in_executor(None, _run_sl_monitor)
        except Exception as exc:
            logger.error("SL monitor failed: %s", exc, exc_info=True)

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
            logger.error("Regime job failed: %s", exc, exc_info=True)

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
            logger.error("Research refresh job failed: %s", exc, exc_info=True)

    scheduler.add_job(_research_refresh, "interval", seconds=3600,
                      id="research_refresh", name="Portfolio Research Refresh")

    # ── Weekly backtest — Saturday 06:30 IST ──────────────────────────────────
    async def _weekly_backtest():
        import asyncio
        import time as _time
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
                _time.sleep(3)
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

    logger.info("Scheduler built — TZ: Asia/Kolkata — %d jobs", len(scheduler.get_jobs()))
    return scheduler


# ── Alert dispatcher ──────────────────────────────────────────────────────────

def _dispatch_alerts(scan_results: list[dict]) -> None:
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
                    logger.error("Alert email failed for %s: %s", ticker, exc, exc_info=True)

            db.add(AlertDispatchLog(
                ticker=ticker, signal_type=signal, confidence=conf,
                regime=sig.get("regime", ""), channel="EMAIL",
                subject=f"{ticker} {signal} {conf:.0f}%",
            ))


# ── Stop-Loss monitor ─────────────────────────────────────────────────────────

def _run_sl_monitor() -> None:
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

    log_startup_banner(
        logger,
        app_name    = cfg.APP_NAME,
        version     = cfg.APP_VERSION,
        db_driver   = "PostgreSQL (Supabase)" if cfg.DATABASE_URL else "SQLite (local)",
        frontend_url= cfg.FRONTEND_URL,
    )

    # Init DB tables (idempotent — skips existing tables)
    try:
        init_db()
    except Exception as exc:
        logger.critical(
            "Database init FAILED: %s\n"
            "  → Check DATABASE_URL in Railway env vars\n"
            "  → Make sure Supabase project is not paused\n"
            "  → Run the SQL migration script if tables are missing",
            exc, exc_info=True,
        )
        # Don't raise — let the app start so /api/health can report the error

    # Start scheduler
    try:
        _scheduler = _build_scheduler()
        _scheduler.start()
        logger.info("Scheduler started: %s",
                    ", ".join(j.id for j in _scheduler.get_jobs()))
    except Exception as exc:
        logger.critical("Scheduler failed to start: %s", exc, exc_info=True)

    yield  # ── App is running ──────────────────────────────────────────

    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
    logger.info("QUANTEDGE — SHUT DOWN COMPLETE")


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(
    title       = cfg.APP_NAME,
    version     = cfg.APP_VERSION,
    description = (
        "Institutional AI Trading Dashboard — Modules 1-8.\n\n"
        "**Auth flow**: `POST /api/auth/login` → `POST /api/auth/verify-otp` → Bearer token.\n\n"
        "**Health**: `GET /api/health` — checks DB + scheduler."
    ),
    lifespan  = lifespan,
    docs_url  = "/docs",
    redoc_url = "/redoc",
)


# ── FIX 2: CORS ───────────────────────────────────────────────────────────────
# Production: set CORS_ORIGINS=https://your-app.vercel.app in Railway env vars
# Local dev:  leave CORS_ORIGINS unset → defaults to "*" (allows everything)
#
# Multiple origins: CORS_ORIGINS=https://app.vercel.app,https://staging.vercel.app
_raw_origins  = os.environ.get("CORS_ORIGINS", "*")
_cors_origins = [o.strip() for o in _raw_origins.split(",") if o.strip()]
_allow_all    = "*" in _cors_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins     = ["*"] if _allow_all else _cors_origins,
    allow_credentials = False if _allow_all else True,   # browser blocks creds with wildcard
    allow_methods     = ["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers     = ["*"],
    expose_headers    = ["X-Request-ID"],
)

logger.info(
    "CORS configured: %s",
    "allow_all (*)" if _allow_all else f"explicit origins: {_cors_origins}",
)


# ── FIX 3: Request logging middleware ─────────────────────────────────────────

@app.middleware("http")
async def logging_middleware(request: Request, call_next):
    """
    Attaches a unique Request-ID to every request and logs:
        method, path, status_code, duration_ms

    The request_id is returned in the X-Request-ID response header so it
    can be correlated with Railway logs when debugging production errors.
    """
    request_id = str(uuid.uuid4())[:8]
    request.state.request_id = request_id

    start = time.perf_counter()
    try:
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000
        response.headers["X-Request-ID"] = request_id
        log_request(
            logger,
            method     = request.method,
            path       = request.url.path,
            status     = response.status_code,
            duration_ms= duration_ms,
            request_id = request_id,
        )
        return response
    except Exception as exc:
        duration_ms = (time.perf_counter() - start) * 1000
        logger.error(
            "[%s] UNHANDLED %s %s (%.1fms): %s",
            request_id, request.method, request.url.path, duration_ms, exc,
            exc_info=True,
        )
        raise


# ── FIX 4: Global exception handler ──────────────────────────────────────────
# Returns structured JSON so the frontend can display a useful toast.
# Never returns a blank white 500 page again.

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    request_id = getattr(request.state, "request_id", "unknown")
    path = request.url.path

    logger.critical(
        "[%s] Unhandled exception on %s %s: %s",
        request_id, request.method, path, exc,
        exc_info=True,
    )

    # Map common exception types to user-friendly hints
    exc_type = type(exc).__name__
    hints = {
        "OperationalError":   "Database connection failed. Check DATABASE_URL and Supabase status.",
        "ProgrammingError":   "SQL error — a table may be missing. Run the migration script.",
        "IntegrityError":     "Database constraint violation — duplicate or invalid data.",
        "ConnectionError":    "Network error reaching an external service (yfinance/NewsAPI).",
        "TimeoutError":       "Request timed out. The operation may still be running in background.",
        "AttributeError":     "Internal data error — a model field may be missing.",
        "KeyError":           "Missing required field in response data.",
        "ValueError":         "Invalid value in request or response data.",
    }
    hint = hints.get(exc_type, "An unexpected error occurred. Check Railway logs for details.")

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error":      exc_type,
            "message":    hint,
            "detail":     str(exc),
            "path":       path,
            "request_id": request_id,
            "docs":       f"{cfg.FRONTEND_BASE_URL}/docs",
        },
        headers={"X-Request-ID": request_id},
    )


# ── FIX 1: Routers ────────────────────────────────────────────────────────────
# Each router has its own prefix (/auth, /dashboard, /trading, /market).
# We mount them all under /api so final paths are:
#   /api/auth/login
#   /api/dashboard/
#   /api/trading/portfolio/upload
#   /api/market/ohlcv/{ticker}
#
# The frontend api.ts uses baseURL = `${API_BASE}/api` so it calls:
#   /api/auth/login          ✓
#   /api/dashboard/regime    ✓
#   /api/trading/backtest/.. ✓
#   /api/market/ohlcv/..     ✓

from backend.api.routers.auth        import router as auth_router
from backend.api.routers.market_data import router as market_data_router
from backend.api.routers.dashboard   import router as dashboard_router
from backend.api.routers.trading     import router as trading_router

app.include_router(auth_router,        prefix="/api")
app.include_router(market_data_router, prefix="/api")
app.include_router(dashboard_router,   prefix="/api")
app.include_router(trading_router,     prefix="/api")


# ── FIX 5: /api/health endpoint ──────────────────────────────────────────────
# Moved from /health to /api/health so:
#   - railway.toml healthcheckPath = "/api/health" works
#   - Frontend can call /api/health to show connection status in Settings
#   - Returns 200 even when DB is degraded (so Railway doesn't restart the pod)

@app.get("/api/health", tags=["System"])
def health():
    """
    Startup health check. Tests:
      1. Database connectivity (SELECT 1 on Supabase)
      2. Scheduler running + job count
      3. Cache directories writable

    Returns HTTP 200 always. Check 'status' field:
      - "ok"       → everything healthy
      - "degraded" → DB or scheduler issue, app still serving
    """
    from sqlalchemy import text
    from backend.core.database import engine

    # ── DB check ──────────────────────────────────────────────────────────────
    db_status  = "ok"
    db_detail  = None
    db_latency = None
    try:
        t0 = time.perf_counter()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_latency = round((time.perf_counter() - t0) * 1000, 1)
    except Exception as exc:
        db_status = "error"
        db_detail = str(exc)
        logger.error("Health check: DB connection failed: %s", exc)

    # ── Scheduler check ───────────────────────────────────────────────────────
    sched_status = "stopped"
    active_jobs  = 0
    if _scheduler:
        try:
            active_jobs  = len(_scheduler.get_jobs())
            sched_status = "running" if _scheduler.running else "stopped"
        except Exception:
            sched_status = "error"

    # ── Cache dirs check ──────────────────────────────────────────────────────
    cache_ok = os.path.isdir("/tmp/parquet") and os.access("/tmp/parquet", os.W_OK)

    overall = "ok" if (db_status == "ok" and sched_status == "running") else "degraded"

    return {
        "status":       overall,
        "version":      cfg.APP_VERSION,
        "timestamp":    datetime.now(timezone.utc).isoformat(),
        "database": {
            "status":    db_status,
            "driver":    "postgresql" if cfg.DATABASE_URL else "sqlite",
            "latency_ms": db_latency,
            "detail":    db_detail,
        },
        "scheduler": {
            "status":      sched_status,
            "active_jobs": active_jobs,
            "timezone":    "Asia/Kolkata",
        },
        "cache": {
            "parquet_dir": "/tmp/parquet",
            "writable":    cache_ok,
        },
        "cors": {
            "mode":    "wildcard" if _allow_all else "explicit",
            "origins": _cors_origins,
        },
    }


@app.get("/", tags=["System"])
def root():
    """Railway 'service is up' ping endpoint."""
    return {
        "message": cfg.APP_NAME,
        "version": cfg.APP_VERSION,
        "health":  "/api/health",
        "docs":    "/docs",
        "api": {
            "auth":      "/api/auth",
            "dashboard": "/api/dashboard",
            "trading":   "/api/trading",
            "market":    "/api/market",
        },
    }
