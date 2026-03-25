"""
backend/api/routers/dashboard.py
===================================
All dashboard data endpoints — all JWT-protected.

Router prefix: /dashboard
Mounted in main.py as: app.include_router(dashboard_router, prefix="/api")

Resulting URLs:
GET  /api/dashboard/              → summary: regime + signals + budget
GET  /api/dashboard/regime        → latest market regime snapshot  (matches useLatestRegime)
GET  /api/dashboard/signals       → latest FinalSignals            (matches useLatestSignals)
GET  /api/dashboard/signals/{ticker} → signal history for one ticker
POST /api/dashboard/scan-now      → force immediate signal scan    (matches triggerScanNow)
GET  /api/dashboard/research/{ticker} → full news + sentiment + forecast
GET  /api/dashboard/research/{ticker}/articles → raw news articles
GET  /api/dashboard/notifications → recent alert dispatch log      (matches useNotifications)
GET  /api/dashboard/leaderboard   → top strategies by Sharpe       (matches useLeaderboard)
"""

import json
import logging
from datetime import datetime
from typing import Any, Optional

import sqlalchemy.exc as sa_exc
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from backend.core.auth     import get_current_user
from backend.core.database import get_db
from backend.models.backtest  import StrategyPerformance
from backend.models.news      import NewsAnalysis, NewsArticle as NewsArticleModel
from backend.models.portfolio import DataQuality
from backend.models.regime    import MarketRegime
from backend.models.signals   import FinalSignal, SignalStatus, SignalType
from backend.models.alerts           import AlertDispatchLog
from backend.services.news_service   import get_news_service
from backend.services.regime_service import get_regime_service
from backend.services.signal_engine  import get_signal_engine
from backend.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(
    prefix="/dashboard",
    tags=["Dashboard"],
    dependencies=[Depends(get_current_user)],
)


# ─── Pydantic out-schemas ─────────────────────────────────────────────────────

class RegimeOut(BaseModel):
    id:               int
    timestamp:        datetime
    regime_label:     str
    adx_14:           Optional[float] = None
    atr_percentile:   Optional[float] = None
    ema_200:          Optional[float] = None
    close_price:      Optional[float] = None
    price_vs_ema:     Optional[str]   = None
    regime_summary:   Optional[str]   = None
    confidence_score: Optional[float] = None
    class Config: from_attributes = True


class SignalOut(BaseModel):
    id:                   int
    scan_id:              str
    ticker:               str
    regime:               str
    selected_strategy:    str
    signal:               str
    confidence:           float
    entry_price:          Optional[float] = None
    stop_loss:            Optional[float] = None
    target_1:             Optional[float] = None
    target_2:             Optional[float] = None
    risk_reward_ratio:    Optional[float] = None
    adx:                  Optional[float] = None
    rsi:                  Optional[float] = None
    volume_ratio:         Optional[float] = None
    agreeing_strategies:  Optional[int]   = None
    total_strategies_run: Optional[int]   = None
    agreement_bonus:      Optional[float] = None
    sentiment_score:      Optional[float] = None
    sentiment_label:      Optional[str]   = None
    sentiment_override:   Optional[bool]  = None
    original_signal:      Optional[str]   = None
    bias_warning:         Optional[bool]  = None
    bias_message:         Optional[str]   = None
    source_confirmations: Optional[dict]  = None
    reason:               str
    status:               str
    generated_at:         datetime
    expires_at:           Optional[datetime] = None


class ResearchOut(BaseModel):
    ticker:               str
    avg_sentiment_score:  Optional[float] = None
    sentiment_label:      Optional[str]   = None
    sentiment_std_dev:    Optional[float] = None
    conflict_detected:    bool
    conflict_detail:      Optional[str]   = None
    executive_summary:    list[str]
    forecast_outlook:     Optional[str]   = None
    forecast_direction:   Optional[str]   = None
    forecast_confidence:  Optional[float] = None
    price_slope_annual:   Optional[float] = None
    revenue_cagr:         Optional[float] = None
    articles_analysed:    int
    positive_count:       int
    neutral_count:        int
    negative_count:       int
    insufficient_coverage: bool
    coverage_message:     Optional[str]   = None
    analysed_at:          datetime


class LeaderboardEntry(BaseModel):
    rank:          int
    stock_ticker:  str
    strategy_name: str
    sharpe_ratio:  Optional[float] = None
    cagr:          Optional[float] = None
    win_rate:      Optional[float] = None
    max_drawdown:  Optional[float] = None
    years_of_data: Optional[float] = None
    data_quality:  str = "UNKNOWN"


class NewsArticleOut(BaseModel):
    title:           str
    source_name:     str
    published_at:    datetime
    url:             Optional[str]   = None
    description:     Optional[str]   = None
    sentiment_score: Optional[float] = None
    sentiment_label: Optional[str]   = None
    class Config: from_attributes = True


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _signal_to_out(r: FinalSignal) -> SignalOut:
    """Safely serialise a FinalSignal ORM row to SignalOut."""
    confirmations: Optional[dict] = None
    if getattr(r, "source_confirmations_json", None):
        try:
            confirmations = json.loads(r.source_confirmations_json)
        except json.JSONDecodeError as exc:
            logger.warning(
                "Failed to parse source_confirmations_json for signal id=%s: %s",
                getattr(r, "id", "?"), exc,
            )

    return SignalOut(
        id                   = r.id,
        scan_id              = r.scan_id,
        ticker               = r.ticker,
        regime               = r.regime.value if hasattr(r.regime, "value") else str(r.regime),
        selected_strategy    = r.selected_strategy,
        signal               = r.signal.value if hasattr(r.signal, "value") else str(r.signal),
        confidence           = r.confidence,
        entry_price          = r.entry_price,
        stop_loss            = r.stop_loss,
        target_1             = r.target_1,
        target_2             = r.target_2,
        risk_reward_ratio    = r.risk_reward_ratio,
        adx                  = r.adx,
        rsi                  = r.rsi,
        volume_ratio         = r.volume_ratio,
        agreeing_strategies  = r.agreeing_strategies,
        total_strategies_run = r.total_strategies_run,
        agreement_bonus      = r.agreement_bonus,
        sentiment_score      = r.sentiment_score,
        sentiment_label      = r.sentiment_label,
        sentiment_override   = r.sentiment_override,
        original_signal      = (
            r.original_signal.value
            if r.original_signal and hasattr(r.original_signal, "value")
            else r.original_signal
        ),
        bias_warning         = r.bias_warning,
        bias_message         = r.bias_message,
        source_confirmations = confirmations,
        reason               = r.reason or "",
        status               = r.status.value if hasattr(r.status, "value") else str(r.status),
        generated_at         = r.generated_at,
        expires_at           = r.expires_at,
    )


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/", summary="Aggregated dashboard payload")
def get_dashboard(db: Session = Depends(get_db)):
    try:
        regime = db.query(MarketRegime).order_by(desc(MarketRegime.timestamp)).first()

        subq = (
            db.query(
                FinalSignal.ticker,
                func.max(FinalSignal.generated_at).label("latest"),
            )
            .group_by(FinalSignal.ticker)
            .subquery()
        )
        signals = (
            db.query(FinalSignal)
            .join(subq, (FinalSignal.ticker == subq.c.ticker) &
                        (FinalSignal.generated_at == subq.c.latest))
            .filter(FinalSignal.status == SignalStatus.ACTIVE)
            .all()
        )

        buy_count  = sum(1 for s in signals if s.signal == SignalType.BUY)
        sell_count = sum(1 for s in signals if s.signal == SignalType.SELL)
        hold_count = len(signals) - buy_count - sell_count

        top_buys = sorted(
            [s for s in signals if s.signal == SignalType.BUY],
            key=lambda s: s.confidence, reverse=True,
        )[:10]

        top_signals_out = [_signal_to_out(s).model_dump() for s in top_buys]

        total_sigs = len(signals)
        hold_sigs  = sum(1 for s in signals if s.signal == SignalType.HOLD)
        bias_flag  = total_sigs > 0 and (hold_sigs / total_sigs) >= 0.80
        bias_msg   = f"Bias detected: {hold_sigs}/{total_sigs} HOLD signals — confidence reduced" if bias_flag else ""

        return {
            "regime":             regime.regime_label.value if regime else "UNKNOWN",
            "regime_confidence":  regime.confidence_score if regime else 0.0,
            "regime_summary":     regime.regime_summary if regime else None,
            "total_buy_signals":  buy_count,
            "total_sell_signals": sell_count,
            "total_hold_signals": hold_count,
            "top_signals":        top_signals_out,
            "bias_warning":       bias_flag,
            "bias_message":       bias_msg,
            "last_scan_at":       signals[0].generated_at.isoformat() if signals else None,
        }

    except sa_exc.OperationalError as exc:
        logger.error("get_dashboard DB error: %s", exc, exc_info=True)
        raise HTTPException(
            503,
            detail=(
                "Cannot reach the database. "
                "Check DATABASE_URL in Railway environment variables and Supabase status."
            ),
        )
    except sa_exc.ProgrammingError as exc:
        logger.error("get_dashboard schema error: %s", exc, exc_info=True)
        raise HTTPException(
            500,
            detail=(
                "A required database table is missing from Supabase. "
                "Verify your DATABASE_URL points to the correct project and tables are initialised."
            ),
        )
    except Exception as exc:
        logger.error("get_dashboard unexpected error: %s", exc, exc_info=True)
        raise HTTPException(500, detail=f"Dashboard load failed: {exc}")


@router.get("/regime", response_model=RegimeOut, summary="Latest Nifty 50 market regime snapshot")
def get_regime(db: Session = Depends(get_db)):
    try:
        row = db.query(MarketRegime).order_by(desc(MarketRegime.timestamp)).first()
    except sa_exc.OperationalError as exc:
        logger.error("get_regime DB connection error: %s", exc, exc_info=True)
        raise HTTPException(
            503,
            detail=(
                "Cannot reach the database to load regime data. "
                "Check DATABASE_URL in Railway environment variables."
            ),
        )
    except sa_exc.ProgrammingError as exc:
        logger.error("get_regime schema error (table missing?): %s", exc, exc_info=True)
        raise HTTPException(
            500,
            detail=(
                "Database table 'market_regimes' not found in Supabase. "
                "Verify your DATABASE_URL and run the app once to initialise tables."
            ),
        )
    if not row:
        raise HTTPException(404, "No regime data yet. Scheduler runs every 5 minutes.")
    return RegimeOut(
        id               = row.id,
        timestamp        = row.timestamp,
        regime_label     = row.regime_label.value if hasattr(row.regime_label, "value") else str(row.regime_label),
        adx_14           = row.adx_14,
        atr_percentile   = row.atr_percentile,
        ema_200          = row.ema_200,
        close_price      = row.close_price,
        price_vs_ema     = row.price_vs_ema,
        regime_summary   = row.regime_summary,
        confidence_score = row.confidence_score,
    )


@router.get("/signals", response_model=list[SignalOut],
            summary="Latest FinalSignals for all holdings (with sentiment overlay)")
def get_latest_signals(
    signal_filter:  Optional[str] = Query(None, alias="signal"),
    min_confidence: float         = Query(0.0, ge=0, le=100),
    limit:          int           = Query(50, ge=1, le=200),
    db:             Session       = Depends(get_db),
):
    subq = (
        db.query(
            FinalSignal.ticker,
            func.max(FinalSignal.generated_at).label("latest"),
        )
        .group_by(FinalSignal.ticker)
        .subquery()
    )

    q = (
        db.query(FinalSignal)
        .join(subq, (FinalSignal.ticker == subq.c.ticker) &
                    (FinalSignal.generated_at == subq.c.latest))
        .filter(
            FinalSignal.status    == SignalStatus.ACTIVE,
            FinalSignal.confidence >= min_confidence,
        )
    )

    if signal_filter:
        try:
            q = q.filter(FinalSignal.signal == SignalType(signal_filter.upper()))
        except ValueError:
            raise HTTPException(400, f"Invalid signal type '{signal_filter}'")

    rows = q.order_by(desc(FinalSignal.confidence)).limit(limit).all()
    return [_signal_to_out(r) for r in rows]


@router.get("/signals/{ticker}", response_model=list[SignalOut],
            summary="Signal history for a specific ticker")
def get_ticker_signals(
    ticker: str,
    limit:  int     = Query(20, ge=1, le=100),
    db:     Session = Depends(get_db),
):
    rows = (
        db.query(FinalSignal)
        .filter(FinalSignal.ticker == ticker.upper())
        .order_by(desc(FinalSignal.generated_at))
        .limit(limit)
        .all()
    )
    if not rows:
        raise HTTPException(404, f"No signals for '{ticker.upper()}'. Run a scan first.")
    return [_signal_to_out(r) for r in rows]


@router.post("/scan-now", status_code=status.HTTP_200_OK,
             summary="Trigger an immediate signal scan + fresh regime detection")
def scan_now(db: Session = Depends(get_db)):
    """
    Two-phase synchronous pipeline:
    1. Regime detection  — calls RegimeService.detect_and_persist() so the regime
       written to DB is fresh (not the stale scheduler value from hours ago).
    2. Signal scan       — SignalEngine.run_scan() reads the newly persisted regime.

    Both phases run in the same request so the toast in the UI reflects the
    actual regime that was just computed, not a cached one.
    """
    try:
        # Phase 1 — always detect regime fresh before scanning signals
        regime_svc = get_regime_service()
        try:
            new_regime = regime_svc.detect_and_persist()
            logger.info(
                "scan-now: regime freshly detected → %s",
                new_regime.regime_label.value if new_regime else "unknown",
            )
        except Exception as regime_exc:
            # Non-fatal: regime detection can fail (e.g. market closed, yfinance down)
            # Signal scan will fall back to reading the last persisted regime from DB
            logger.warning("scan-now: regime detection failed (non-fatal): %s", regime_exc)

        # Phase 2 — run signal scan (reads regime from DB, now fresh from phase 1)
        engine  = get_signal_engine()
        results = engine.run_scan()

        # Return fresh regime label for the toast in the frontend
        regime_row = (
            db.query(MarketRegime)
            .order_by(desc(MarketRegime.timestamp))
            .first()
        )

        return {
            "message":       f"Scan complete — {len(results)} signals generated",
            "signals_count": len(results),
            "signals":       results,
            "regime_label":  regime_row.regime_label.value if regime_row else "UNKNOWN",
            "regime_summary": regime_row.regime_summary if regime_row else None,
        }
    except sa_exc.OperationalError as exc:
        logger.error("scan-now DB error: %s", exc, exc_info=True)
        raise HTTPException(
            503,
            detail=(
                "Database connection lost during scan. "
                "Check DATABASE_URL in Railway environment variables."
            ),
        )
    except Exception as exc:
        logger.error("on-demand scan failed: %s", exc, exc_info=True)
        raise HTTPException(500, detail=f"Scan failed: {exc}. Check Railway backend logs for details.")


@router.get("/research/{ticker}", response_model=ResearchOut,
            summary="Full news analysis + sentiment + 12-24mo forecast (60-min cache)")
def get_research(ticker: str, db: Session = Depends(get_db)):
    try:
        svc = get_news_service()
        row = svc.analyse(ticker.upper(), db)
    except sa_exc.OperationalError as exc:
        logger.error("get_research DB error for %s: %s", ticker, exc, exc_info=True)
        raise HTTPException(
            503,
            detail=(
                f"Database connection failed while loading research for '{ticker.upper()}'. "
                "Check DATABASE_URL in Railway environment variables."
            ),
        )
    except sa_exc.ProgrammingError as exc:
        logger.error("get_research schema error for %s: %s", ticker, exc, exc_info=True)
        raise HTTPException(
            500,
            detail=(
                "Database table 'news_analysis' or 'news_articles' not found in Supabase. "
                "Verify DATABASE_URL points to the correct Supabase project."
            ),
        )
    except json.JSONDecodeError as exc:
        logger.error("get_research JSON parse error for %s: %s", ticker, exc, exc_info=True)
        raise HTTPException(
            500,
            detail=(
                f"Malformed JSON in stored news data for '{ticker.upper()}'. "
                "The executive_summary or sentiment field may be corrupted — "
                "re-run the analysis to regenerate."
            ),
        )
    except Exception as exc:
        logger.error("get_research unexpected error for %s: %s", ticker, exc, exc_info=True)
        raise HTTPException(500, detail=f"Research failed for '{ticker.upper()}': {exc}")

    bullets = json.loads(row.executive_summary or "[]")
    return ResearchOut(
        ticker                = row.ticker,
        avg_sentiment_score   = row.avg_sentiment_score,
        sentiment_label       = row.sentiment_label.value if row.sentiment_label else None,
        sentiment_std_dev     = row.sentiment_std_dev,
        conflict_detected     = row.conflict_detected,
        conflict_detail       = row.conflict_detail,
        executive_summary     = bullets,
        forecast_outlook      = row.forecast_outlook,
        forecast_direction    = row.forecast_direction,
        forecast_confidence   = row.forecast_confidence,
        price_slope_annual    = row.price_slope_annual,
        revenue_cagr          = row.revenue_cagr,
        articles_analysed     = row.articles_analysed,
        positive_count        = row.positive_count or 0,
        neutral_count         = row.neutral_count  or 0,
        negative_count        = row.negative_count or 0,
        insufficient_coverage = row.insufficient_coverage,
        coverage_message      = row.coverage_message,
        analysed_at           = row.analysed_at,
    )


@router.get("/research/{ticker}/articles", response_model=list[NewsArticleOut],
            summary="Raw news articles for a ticker with per-article sentiment (latest 30)")
def get_news_articles(
    ticker: str,
    limit:  int     = Query(30, ge=1, le=100),
    db:     Session = Depends(get_db),
):
    """
    Returns persisted NewsArticle rows ordered latest-first.
    Articles are written by NewsService.analyse() when /research/{ticker} is hit.
    Returns [] (not 404) if no articles exist yet — frontend calls /research first
    which triggers ingestion, then calls /articles.
    """
    rows = (
        db.query(NewsArticleModel)
        .filter(NewsArticleModel.ticker == ticker.upper())
        .order_by(desc(NewsArticleModel.published_at))
        .limit(limit)
        .all()
    )
    return [
        NewsArticleOut(
            title           = r.title,
            source_name     = r.source_name.value if r.source_name else "UNKNOWN",
            published_at    = r.published_at,
            url             = r.url,
            description     = r.description,
            sentiment_score = r.sentiment_score,
            sentiment_label = r.sentiment_label.value if r.sentiment_label else None,
        )
        for r in rows
    ]


@router.get("/notifications", summary="Recent alert dispatch log (latest 20)")
def get_notifications(
    limit: int     = Query(20, ge=1, le=100),
    db:    Session = Depends(get_db),
):
    """
    Returns the most recent entries from alert_dispatch_log — real alerts
    that the scheduler fired (signal emails, SL hits, regime changes).
    Used by the Header notification dropdown to replace hardcoded static data.
    Returns [] when no alerts have been dispatched yet.
    """
    rows = (
        db.query(AlertDispatchLog)
        .order_by(desc(AlertDispatchLog.sent_at))
        .limit(limit)
        .all()
    )
    return [
        {
            "id":          r.id,
            "ticker":      r.ticker,
            "signal_type": r.signal_type,
            "confidence":  r.confidence,
            "regime":      r.regime,
            "channel":     r.channel,
            "subject":     r.subject,
            "delivered":   r.delivered,
            "sent_at":     r.sent_at.isoformat() if r.sent_at else None,
        }
        for r in rows
    ]


@router.get("/leaderboard", response_model=list[LeaderboardEntry],
            summary="Top strategies ranked by Sharpe Ratio")
def get_leaderboard(
    top_n:         int  = Query(20, ge=1, le=100),
    all_qualities: bool = Query(False),
    db:            Session = Depends(get_db),
):
    q = db.query(StrategyPerformance).filter(
        StrategyPerformance.sharpe_ratio.isnot(None)
    )
    # By default only return SUFFICIENT data quality rows; pass all_qualities=true for full table
    if not all_qualities:
        q = q.filter(StrategyPerformance.data_quality == DataQuality.SUFFICIENT)

    rows = q.order_by(desc(StrategyPerformance.sharpe_ratio)).limit(top_n).all()
    return [
        LeaderboardEntry(
            rank          = i + 1,
            stock_ticker  = r.stock_ticker,
            strategy_name = r.strategy_name,
            sharpe_ratio  = r.sharpe_ratio,
            cagr          = r.cagr,
            win_rate      = r.win_rate,
            max_drawdown  = r.max_drawdown,
            years_of_data = r.years_of_data,
            data_quality  = r.data_quality.value if hasattr(r.data_quality, "value") else str(r.data_quality),
        )
        for i, r in enumerate(rows)
    ]
