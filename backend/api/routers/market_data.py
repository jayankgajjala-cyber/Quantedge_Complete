"""
backend/api/routers/market_data.py
======================================
Real-time macro data endpoints for the Intelligence Marquee and dashboard.

GET /api/market/macro          → DXY, US10Y yield, Brent Crude (live scrape)
GET /api/market/news-context   → Moneycontrol headlines + RBI + mood
GET /api/market/tv/{ticker}    → TradingView technical summary for one ticker
GET /api/market/inception/{ticker} → Inception date + quality flag for UI banner
"""

import logging
from datetime import datetime
from typing import Optional, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.core.auth     import get_current_user
from backend.core.database import get_db
from backend.services.data_manager import (
    DATA_UNAVAILABLE, fetch_macro_context, fetch_news_context,
    fetch_tv_consensus, fetch_ohlcv, get_data_manager,
)

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/market",
    tags=["Market Data"],
    dependencies=[Depends(get_current_user)],
)


# ─── Pydantic schemas ─────────────────────────────────────────────────────────

class MacroOut(BaseModel):
    us_10y_yield:  Any
    dxy_index:     Any
    brent_crude:   Any
    fetched_at:    str
    source_flags:  dict


class NewsContextOut(BaseModel):
    headlines:     Any           # list[str] or "DATA_UNAVAILABLE"
    rbi_updates:   Any
    market_mood:   Any
    fetched_at:    str
    source_flags:  dict


class TVSummaryOut(BaseModel):
    ticker:          str
    summary:         Any
    oscillators:     Any
    moving_averages: Any
    source_flags:    dict


class InceptionOut(BaseModel):
    ticker:            str
    inception_date:    Optional[str]
    years_available:   float
    quality:           str
    quality_message:   str
    is_inception:      bool
    ui_banner:         Optional[str]


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/macro", response_model=MacroOut,
            summary="Real-time DXY, US 10Y Yield, Brent Crude from Investing.com")
def get_macro():
    """
    Scrapes Investing.com for global macro data.
    Fields set to 'DATA_UNAVAILABLE' when source is unreachable.
    Used by the frontend Intelligence Marquee ticker bar.
    """
    macro = fetch_macro_context()
    return MacroOut(
        us_10y_yield = macro.us_10y_yield,
        dxy_index    = macro.dxy_index,
        brent_crude  = macro.brent_crude,
        fetched_at   = macro.fetched_at.isoformat(),
        source_flags = macro.source_flags,
    )


@router.get("/news-context", response_model=NewsContextOut,
            summary="Moneycontrol market headlines + RBI updates + mood")
def get_news_context():
    """
    Scrapes Moneycontrol for latest market news and RBI updates.
    market_mood = BULLISH / BEARISH / NEUTRAL based on keyword analysis.
    All fields = 'DATA_UNAVAILABLE' when Moneycontrol is unreachable.
    """
    news = fetch_news_context()
    return NewsContextOut(
        headlines    = news.headlines,
        rbi_updates  = news.rbi_updates,
        market_mood  = news.market_mood,
        fetched_at   = news.fetched_at.isoformat(),
        source_flags = news.source_flags,
    )


@router.get("/tv/{ticker}", response_model=TVSummaryOut,
            summary="TradingView technical consensus for a single ticker")
def get_tv_summary(ticker: str):
    """
    Fetches TradingView-TA summary for an NSE ticker.
    Returns DATA_UNAVAILABLE fields if tradingview-ta is not installed
    or TradingView rate-limits the request.
    """
    tv = fetch_tv_consensus(ticker.upper())
    return TVSummaryOut(
        ticker          = ticker.upper(),
        summary         = tv.summary,
        oscillators     = tv.oscillators,
        moving_averages = tv.moving_averages,
        source_flags    = tv.source_flags,
    )


@router.get("/inception/{ticker}", response_model=InceptionOut,
            summary="Inception date + data quality banner for a ticker")
def get_inception_info(ticker: str):
    """
    Returns the actual listing/inception date for a stock and the UI banner
    message to display when data < 10 years.

    UI banner examples:
      - SUFFICIENT: None (no banner needed)
      - INSUFFICIENT: "Backtesting performed from inception [14 Jan 2019] only;
                        10-year historical data unavailable for ABC."
      - LOW_CONFIDENCE: same + "LOW CONFIDENCE due to <24 months history"
    """
    result = fetch_ohlcv(ticker.upper())
    banner = None
    if result.quality != "SUFFICIENT":
        banner = result.quality_message

    return InceptionOut(
        ticker          = ticker.upper(),
        inception_date  = result.inception_date.strftime("%d %b %Y") if result.inception_date else None,
        years_available = round(result.years_available, 2),
        quality         = result.quality,
        quality_message = result.quality_message,
        is_inception    = result.is_inception,
        ui_banner       = banner,
    )


@router.get("/ohlcv/{ticker}", summary="OHLCV candlestick bars for a ticker (daily, parquet-cached)")
def get_ohlcv_bars(
    ticker: str,
    limit:  int = Query(500, ge=50, le=2000),
):
    """
    Returns daily OHLCV bars for a ticker as a JSON array.
    Data is sourced from the parquet cache (yfinance, up to 10 years).
    Used by CandlestickChart component on the Signals and Research pages.

    Response shape: [{ timestamp, open, high, low, close, volume }, ...]
    """
    result = fetch_ohlcv(ticker.upper())
    if result.df is None or result.df.empty:
        raise HTTPException(
            status_code=404,
            detail=f"No OHLCV data available for {ticker.upper()}. "
                   f"Quality: {result.quality}. {result.quality_message}"
        )

    df = result.df.tail(limit).copy()
    # Normalise index to plain date strings — lightweight-charts expects UTC seconds
    df.index = df.index.tz_localize(None) if df.index.tzinfo else df.index
    bars = [
        {
            "timestamp": idx.isoformat(),
            "open":      round(float(row["Open"]),   2),
            "high":      round(float(row["High"]),   2),
            "low":       round(float(row["Low"]),    2),
            "close":     round(float(row["Close"]),  2),
            "volume":    int(row["Volume"]),
        }
        for idx, row in df.iterrows()
    ]
    return {
        "ticker":          ticker.upper(),
        "bars":            bars,
        "count":           len(bars),
        "quality":         result.quality,
        "inception_date":  result.inception_date.strftime("%d %b %Y") if result.inception_date else None,
        "years_available": round(result.years_available, 2),
    }


@router.get("/all/{ticker}",
            summary="All four source results for one ticker (diagnostic / debug)")
def get_all_sources(ticker: str):
    """
    Runs the full DataManager.fetch_all() pipeline for one ticker.
    Returns all source results, flags, and inception info.
    Useful for debugging source availability.
    """
    mgr    = get_data_manager()
    result = mgr.fetch_all(ticker.upper())
    ohlcv  = result["ohlcv"]
    macro  = result["macro"]
    news   = result["news"]
    tv     = result["tv"]

    return {
        "ticker":   ticker.upper(),
        "ohlcv": {
            "quality":         ohlcv.quality,
            "quality_message": ohlcv.quality_message,
            "years_available": ohlcv.years_available,
            "inception_date":  ohlcv.inception_date.strftime("%d %b %Y") if ohlcv.inception_date else None,
            "is_inception":    ohlcv.is_inception,
            "bars":            len(ohlcv.df) if ohlcv.df is not None else 0,
            "source_flags":    ohlcv.source_flags,
        },
        "macro":   macro.as_dict(),
        "news": {
            "market_mood":     news.market_mood,
            "headline_count":  len(news.headlines) if isinstance(news.headlines, list) else 0,
            "rbi_count":       len(news.rbi_updates) if isinstance(news.rbi_updates, list) else 0,
            "source_flags":    news.source_flags,
        },
        "tradingview": {
            "summary":      tv.summary,
            "source_flags": tv.source_flags,
        },
        "all_source_flags": result["summary_flags"],
    }
