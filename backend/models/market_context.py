"""
backend/models/market_context.py
===================================
Persistent tables for macro data and Moneycontrol news.
Updated every scheduler cycle (5 min for macro, 60 min for news).

Tables:
  market_context   — DXY, US10Y yield, Brent; one row per 5-min snapshot
  news_context     — Moneycontrol headlines + mood; one row per 60-min cache
"""

import json
from datetime import datetime
from sqlalchemy import Column, Integer, Float, String, DateTime, Boolean, Text, Index
from backend.core.database import Base


class MarketContextSnapshot(Base):
    """
    Macro data point from Investing.com. One row per scheduler tick.
    Fields set to None when source returned DATA_UNAVAILABLE.
    """
    __tablename__ = "market_context"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    timestamp    = Column(DateTime, default=datetime.utcnow, index=True)

    # Source B — Investing.com
    us_10y_yield = Column(Float, nullable=True)    # None = DATA_UNAVAILABLE
    dxy_index    = Column(Float, nullable=True)
    brent_crude  = Column(Float, nullable=True)

    # Derived risk signal
    risk_level   = Column(String(20), nullable=True)   # "LOW" | "MEDIUM" | "HIGH" | "EXTREME"
    risk_reason  = Column(Text, nullable=True)

    # Flags
    yield_available  = Column(Boolean, default=True)
    dxy_available    = Column(Boolean, default=True)
    crude_available  = Column(Boolean, default=True)
    source_flags     = Column(Text, nullable=True)     # JSON

    __table_args__ = (Index("ix_mktctx_ts", "timestamp"),)

    def flags_as_dict(self) -> dict:
        try:
            return json.loads(self.source_flags or "{}")
        except Exception:
            return {}


class NewsContextSnapshot(Base):
    """
    Moneycontrol news snapshot. One row per 60-min cache window.
    headlines / rbi_updates stored as JSON arrays.
    """
    __tablename__ = "news_context"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    timestamp     = Column(DateTime, default=datetime.utcnow, index=True)
    cache_until   = Column(DateTime, nullable=True)
    is_cache_valid= Column(Boolean, default=True)

    # Source C — Moneycontrol
    headlines     = Column(Text, nullable=True)    # JSON list[str] or null
    rbi_updates   = Column(Text, nullable=True)    # JSON list[str] or null
    market_mood   = Column(String(20), nullable=True)  # BULLISH/BEARISH/NEUTRAL
    headline_count= Column(Integer, default=0)

    mc_headlines_available = Column(Boolean, default=True)
    mc_rbi_available       = Column(Boolean, default=True)
    source_flags  = Column(Text, nullable=True)

    __table_args__ = (Index("ix_newsctx_ts", "timestamp"),)

    def get_headlines(self) -> list[str]:
        try:
            return json.loads(self.headlines or "[]")
        except Exception:
            return []

    def get_rbi_updates(self) -> list[str]:
        try:
            return json.loads(self.rbi_updates or "[]")
        except Exception:
            return []
