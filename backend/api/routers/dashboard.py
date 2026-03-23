"""
backend/api/routers/dashboard.py
===================================
All dashboard data endpoints — all JWT-protected.

GET  /api/dashboard/              → summary: regime + signals + budget
GET  /api/dashboard/regime        → latest market regime snapshot
GET  /api/dashboard/signals       → latest FinalSignals (with sentiment overlay)
GET  /api/dashboard/signals/{ticker} → signal history for one ticker
POST /api/dashboard/scan-now      → force immediate signal scan
GET  /api/dashboard/research/{ticker} → full news + sentiment + forecast
GET  /api/dashboard/leaderboard   → top strategies by Sharpe
"""

import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from backend.core.auth     import get_current_user
from backend.core.database import get_db
from backend.models.backtest  import StrategyPerformance
from backend.models.news      import NewsAnalysis, NewsArticle as NewsArticleModel
from backend.models.regime    import MarketRegime
from backend.models.signals   import FinalSignal, SignalStatus, SignalType
from backend.services.news_service   import get_news_service
from backend.services.signal_engine  import get_signal_engine

logger = logging.getLogger(__name__)
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
    adx_14:           Optional[float]
    atr_percentile:   Optional[float]
    ema_200:          Optional[float]
    close_price:      Optional[float]
    price_vs_ema:     Optional[str]
    regime_summary:   Optional[str]
    confidence_score: Optional[float]
    class Config: from_attributes = True


class SignalOut(BaseModel):
    ticker:               str
    regime:               str
    selected_strategy:    str
    signal:               str
    confidence:           float
    entry_price:          Optional[float]
    stop_loss:            Optional[float]
    target_1:             Optional[float]
    target_2:             Optional[float]
    agreeing_strategies:  Optional[int]
    sentiment_score:      Optional[float]
    sentiment_label:      Optional[str]
    sentiment_override:   Optional[bool]
    original_signal:      Optional[str]
    bias_warning:         Optional[bool]
    reason:               str
    generated_at:         datetime
    class Config: from_attributes = True


class ResearchOut(BaseModel):
    ticker:               str
    avg_sentiment_score:  Optional[float]
    sentiment_label:      Optional[str]
    conflict_detected:    bool
    conflict_detail:      Optional[str]
    executive_summary:    list[str]
    forecast_outlook:     Optional[str]
    forecast_direction:   Optional[str]
    articles_analysed:    int
    insufficient_coverage: bool
    coverage_message:     Optional[str]
    analysed_at:          datetime


class LeaderboardEntry(BaseModel):
    rank:          int
    stock_ticker:  str
    strategy_name: str
    sharpe_ratio:  Optional[float]
    cagr:          Optional[float]
    win_rate:      Optional[float]
    max_drawdown:  Optional[float]
    years_of_data: Optional[float]


class NewsArticleOut(BaseModel):
    """Single news article with sentiment score — returned by /research/{ticker}/articles."""
    title:           str
    source_name:     str
    published_at:    datetime
    url:             Optional[str]
    description:     Optional[str]
    sentiment_score: Optional[float]
    sentiment_label: Optional[str]
    class Config: from_attributes = True


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/", summary="Aggregated dashboard payload")
def get_dashboard(db: Session = Depends(get_db)):
    regime = (db.query(MarketRegime)
               .order_by(desc(MarketRegime.timestamp)).first())

    subq = (db.query(
                FinalSignal.ticker,
                func.max(FinalSignal.generated_at).label("latest"),
            ).group_by(FinalSignal.ticker).subquery())
    signals = (db.query(FinalSignal)
                 .join(subq, (FinalSignal.ticker == subq.c.ticker) &
                             (FinalSignal.generated_at == subq.c.latest))
                 .filter(FinalSignal.status == SignalStatus.ACTIVE)
                 .all())

    buy_count  = sum(1 for s in signals if s.signal == SignalType.BUY)
    sell_count = sum(1 for s in signals if s.signal == SignalType.SELL)
    hold_count = len(signals) - buy_count - sell_count

    top_buys = sorted(
        [s for s in signals if s.signal == SignalType.BUY],
        key=lambda s: s.confidence, reverse=True
    )[:10]

    return {
        "regime":             regime.regime_label.value if regime else "UNKNOWN",
        "regime_confidence":  regime.confidence_score if regime else 0.0,
        "regime_summary":     regime.regime_summary if regime else None,
        "total_buy_signals":  buy_count,
        "total_sell_signals": sell_count,
        "total_hold_signals": hold_count,
        "top_signals":        [s.to_frontend_json() for s in top_buys],
        "last_scan_at":       signals[0].generated_at.isoformat() if signals else None,
    }


@router.get("/regime", response_model=RegimeOut,
            summary="Latest Nifty 50 market regime snapshot")
def get_regime(db: Session = Depends(get_db)):
    row = db.query(MarketRegime).order_by(desc(MarketRegime.timestamp)).first()
    if not row:
        raise HTTPException(404, "No regime data yet. Scheduler runs every 5 minutes.")
    return RegimeOut.from_orm(row)


@router.get("/signals", response_model=list[SignalOut],
            summary="Latest FinalSignals for all holdings (with sentiment overlay)")
def get_latest_signals(
    signal_filter: Optional[str] = Query(None, alias="signal"),
    min_confidence: float        = Query(0.0, ge=0, le=100),
    limit:          int          = Query(50, ge=1, le=200),
    db:             Session      = Depends(get_db),
):
    subq = (db.query(
                FinalSignal.ticker,
                func.max(FinalSignal.generated_at).label("latest"),
            ).group_by(FinalSignal.ticker).subquery())

    q = (db.query(FinalSignal)
           .join(subq, (FinalSignal.ticker == subq.c.ticker) &
                       (FinalSignal.generated_at == subq.c.latest))
           .filter(FinalSignal.status   == SignalStatus.ACTIVE,
                   FinalSignal.confidence >= min_confidence))

    if signal_filter:
        try:
            q = q.filter(FinalSignal.signal == SignalType(signal_filter.upper()))
        except ValueError:
            raise HTTPException(400, f"Invalid signal type '{signal_filter}'")

    rows = q.order_by(desc(FinalSignal.confidence)).limit(limit).all()
    return [SignalOut.from_orm(r) for r in rows]


@router.get("/signals/{ticker}", response_model=list[SignalOut],
            summary="Signal history for a specific ticker")
def get_ticker_signals(
    ticker: str,
    limit:  int     = Query(20, ge=1, le=100),
    db:     Session = Depends(get_db),
):
    rows = (db.query(FinalSignal)
              .filter(FinalSignal.ticker == ticker.upper())
              .order_by(desc(FinalSignal.generated_at))
              .limit(limit).all())
    if not rows:
        raise HTTPException(404, f"No signals for '{ticker.upper()}'. Run a scan first.")
    return [SignalOut.from_orm(r) for r in rows]


@router.post("/scan-now", status_code=status.HTTP_200_OK,
             summary="Trigger an immediate signal scan (all holdings)")
def scan_now():
    """
    Runs the full pipeline synchronously:
    Regime → QuantService → 8 strategies → Agreement → Sentiment override → FinalSignal
    """
    try:
        engine  = get_signal_engine()
        results = engine.run_scan()
        return {
            "message":        f"Scan complete — {len(results)} signals generated",
            "signals_count":  len(results),
            "signals":        results,
        }
    except Exception as exc:
        logger.error("on-demand scan failed: %s", exc, exc_info=True)
        raise HTTPException(500, f"Scan failed: {exc}")


@router.get("/research/{ticker}", response_model=ResearchOut,
            summary="Full news analysis + sentiment + 12-24mo forecast (60-min cache)")
def get_research(ticker: str, db: Session = Depends(get_db)):
    try:
        svc = get_news_service()
        row = svc.analyse(ticker.upper(), db)
    except Exception as exc:
        logger.error("Research failed for %s: %s", ticker, exc, exc_info=True)
        raise HTTPException(500, f"Research error: {exc}")

    bullets = json.loads(row.executive_summary or "[]")
    return ResearchOut(
        ticker                = row.ticker,
        avg_sentiment_score   = row.avg_sentiment_score,
        sentiment_label       = row.sentiment_label.value if row.sentiment_label else None,
        conflict_detected     = row.conflict_detected,
        conflict_detail       = row.conflict_detail,
        executive_summary     = bullets,
        forecast_outlook      = row.forecast_outlook,
        forecast_direction    = row.forecast_direction,
        articles_analysed     = row.articles_analysed,
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
    Returns persisted NewsArticle rows for a ticker, ordered latest-first.

    Articles are stored by NewsService.analyse() when GET /research/{ticker}
    is called. If no articles exist yet, returns an empty list — the frontend
    calls /research/{ticker} first (useResearch hook), which triggers ingestion,
    then calls this endpoint (useNews hook) to render the news feed.
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


@router.get("/leaderboard", response_model=list[LeaderboardEntry],
            summary="Top strategies ranked by Sharpe Ratio")
def get_leaderboard(
    top_n:  int     = Query(20, ge=1, le=100),
    db:     Session = Depends(get_db),
):
    from backend.models.portfolio import DataQuality
    rows = (db.query(StrategyPerformance)
              .filter(StrategyPerformance.data_quality == DataQuality.SUFFICIENT,
                      StrategyPerformance.sharpe_ratio.isnot(None))
              .order_by(desc(StrategyPerformance.sharpe_ratio))
              .limit(top_n).all())
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
        )
        for i, r in enumerate(rows)
    ]
