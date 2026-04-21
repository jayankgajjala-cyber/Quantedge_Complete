"""
backend/api/routers/dashboard.py — v9.8 (Fixed)
- scan-now uses BackgroundTasks to avoid frontend timeout
- Added /scan-status polling endpoint
- Added /backtests endpoint for backtest results tab
"""

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Optional

import sqlalchemy.exc as sa_exc
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from backend.core.auth     import get_current_user
from backend.core.database import get_db
from backend.models.backtest  import StrategyPerformance
from backend.models.news      import NewsAnalysis, NewsArticle as NewsArticleModel
from backend.models.portfolio import DataQuality, Holding
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

_EMPTY_PORTFOLIO_MSG = (
    "No portfolio found; waiting for Zerodha holdings input in CSV file."
)

# In-memory scan job store (keyed by scan_id)
_scan_jobs: dict[str, dict] = {}


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


class BacktestResult(BaseModel):
    stock_ticker:     str
    strategy_name:    str
    sharpe_ratio:     Optional[float] = None
    cagr:             Optional[float] = None
    win_rate:         Optional[float] = None
    max_drawdown:     Optional[float] = None
    sortino_ratio:    Optional[float] = None
    profit_factor:    Optional[float] = None
    total_trades:     Optional[int]   = None
    winning_trades:   Optional[int]   = None
    losing_trades:    Optional[int]   = None
    total_return_pct: Optional[float] = None
    annual_volatility:Optional[float] = None
    calmar_ratio:     Optional[float] = None
    avg_win:          Optional[float] = None
    avg_loss:         Optional[float] = None
    backtest_start:   Optional[datetime] = None
    backtest_end:     Optional[datetime] = None
    years_of_data:    Optional[float] = None
    data_quality:     str = "SUFFICIENT"
    initial_capital:  Optional[float] = None
    ran_at:           Optional[datetime] = None
    notes:            Optional[str]   = None


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

def _holdings_exist(db: Session) -> bool:
    return db.query(Holding.id).limit(1).scalar() is not None


def _signal_to_out(r: FinalSignal) -> SignalOut:
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
        if not _holdings_exist(db):
            return {
                "status":             "empty",
                "message":            _EMPTY_PORTFOLIO_MSG,
                "regime":             "UNKNOWN",
                "regime_confidence":  0.0,
                "regime_summary":     None,
                "total_buy_signals":  0,
                "total_sell_signals": 0,
                "total_hold_signals": 0,
                "top_signals":        [],
                "bias_warning":       False,
                "bias_message":       "",
                "last_scan_at":       None,
            }

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
            "status":             "success",
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


@router.get("/regime", summary="Latest Nifty 50 market regime snapshot")
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
        return {
            "status":         "empty",
            "message":        "No regime data yet — scheduler runs every 5 minutes.",
            "regime_label":   "UNKNOWN",
            "confidence_score": 0.0,
            "regime_summary": None,
            "timestamp":      None,
        }

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
    if not _holdings_exist(db):
        logger.info("get_latest_signals: no holdings found, returning empty list")
        return []

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


def _run_scan_background(scan_id: str) -> None:
    """Background worker — runs regime detection then signal scan."""
    from backend.core.database import get_db_context
    from backend.models.regime import MarketRegime
    from sqlalchemy import desc

    _scan_jobs[scan_id]["status"] = "running"

    try:
        with get_db_context() as db:
            has_holdings = db.query(Holding.id).limit(1).scalar() is not None

        if not has_holdings:
            _scan_jobs[scan_id].update({
                "status":        "done",
                "signals_count": 0,
                "regime_label":  "UNKNOWN",
                "regime_summary": None,
                "message":       _EMPTY_PORTFOLIO_MSG,
            })
            return

        try:
            regime_svc = get_regime_service()
            new_regime = regime_svc.detect_and_persist()
            logger.info(
                "bg-scan: regime → %s",
                new_regime.regime_label.value if new_regime else "unknown",
            )
        except Exception as exc:
            logger.warning("bg-scan: regime detection failed (non-fatal): %s", exc)

        engine  = get_signal_engine()
        results = engine.run_scan()

        with get_db_context() as db:
            regime_row = db.query(MarketRegime).order_by(desc(MarketRegime.timestamp)).first()

        _scan_jobs[scan_id].update({
            "status":        "done",
            "signals_count": len(results),
            "regime_label":  regime_row.regime_label.value if regime_row else "UNKNOWN",
            "regime_summary": regime_row.regime_summary if regime_row else None,
            "message":       f"Scan complete — {len(results)} signals generated",
        })

    except Exception as exc:
        logger.error("bg-scan failed: %s", exc, exc_info=True)
        _scan_jobs[scan_id].update({
            "status":  "error",
            "message": f"Scan failed: {exc}",
        })


@router.post("/scan-now", status_code=status.HTTP_202_ACCEPTED,
             summary="Trigger an immediate signal scan (non-blocking)")
def scan_now(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """
    Immediately returns a scan_id.
    Poll GET /api/dashboard/scan-status/{scan_id} for completion.
    This prevents frontend 30s timeout on slow scans.
    """
    if not _holdings_exist(db):
        return {
            "status":        "empty",
            "message":       _EMPTY_PORTFOLIO_MSG,
            "signals_count": 0,
            "signals":       [],
            "regime_label":  "UNKNOWN",
            "regime_summary": None,
        }

    scan_id = str(uuid.uuid4())
    _scan_jobs[scan_id] = {"status": "pending", "started_at": datetime.utcnow().isoformat()}
    background_tasks.add_task(_run_scan_background, scan_id)

    return {"status": "accepted", "scan_id": scan_id}


@router.get("/scan-status/{scan_id}", summary="Poll background scan job status")
def scan_status(scan_id: str):
    job = _scan_jobs.get(scan_id)
    if not job:
        raise HTTPException(404, f"Scan job '{scan_id}' not found")
    return job


@router.get("/backtests", response_model=list[BacktestResult],
            summary="All backtest results grouped by ticker")
def get_backtests(
    ticker: Optional[str] = Query(None, description="Filter by ticker symbol"),
    limit:  int           = Query(200, ge=1, le=500),
    db:     Session       = Depends(get_db),
):
    q = db.query(StrategyPerformance)
    if ticker:
        q = q.filter(StrategyPerformance.stock_ticker == ticker.upper())
    rows = (
        q.order_by(
            StrategyPerformance.stock_ticker,
            desc(StrategyPerformance.sharpe_ratio),
        )
        .limit(limit)
        .all()
    )
    return [
        BacktestResult(
            stock_ticker      = r.stock_ticker,
            strategy_name     = r.strategy_name,
            sharpe_ratio      = r.sharpe_ratio,
            cagr              = r.cagr,
            win_rate          = r.win_rate,
            max_drawdown      = r.max_drawdown,
            sortino_ratio     = r.sortino_ratio,
            profit_factor     = r.profit_factor,
            total_trades      = r.total_trades,
            winning_trades    = r.winning_trades,
            losing_trades     = r.losing_trades,
            total_return_pct  = r.total_return_pct,
            annual_volatility = r.annual_volatility,
            calmar_ratio      = r.calmar_ratio,
            avg_win           = r.avg_win,
            avg_loss          = r.avg_loss,
            backtest_start    = r.backtest_start,
            backtest_end      = r.backtest_end,
            years_of_data     = r.years_of_data,
            data_quality      = r.data_quality.value if hasattr(r.data_quality, "value") else str(r.data_quality),
            initial_capital   = r.initial_capital,
            ran_at            = r.ran_at,
            notes             = r.notes,
        )
        for r in rows
    ]


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
