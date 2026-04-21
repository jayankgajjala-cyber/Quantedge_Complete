"""backend/models/regime.py — Market regime snapshots."""

import enum
from datetime import datetime
from sqlalchemy import Column, Integer, Float, String, DateTime, Enum, Text, Index
from backend.core.database import Base


class MarketRegimeLabel(str, enum.Enum):
    STRONG_TREND       = "STRONG_TREND"
    VOLATILE_HIGH_RISK = "VOLATILE_HIGH_RISK"
    SIDEWAYS           = "SIDEWAYS"
    BEAR_CRASHING      = "BEAR_CRASHING"
    UNKNOWN            = "UNKNOWN"


class MarketRegime(Base):
    __tablename__ = "market_regime"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    timestamp        = Column(DateTime, default=datetime.utcnow, index=True)
    index_symbol     = Column(String(30), default="^NSEI")
    regime_label     = Column(Enum(MarketRegimeLabel), default=MarketRegimeLabel.UNKNOWN)
    adx_14           = Column(Float, nullable=True)
    atr_14           = Column(Float, nullable=True)
    atr_percentile   = Column(Float, nullable=True)
    ema_200          = Column(Float, nullable=True)
    close_price      = Column(Float, nullable=True)
    bb_upper         = Column(Float, nullable=True)
    bb_lower         = Column(Float, nullable=True)
    slope_20d        = Column(Float, nullable=True)
    price_vs_ema     = Column(String(10), nullable=True)
    regime_summary   = Column(Text, nullable=True)
    confidence_score = Column(Float, nullable=True)

    __table_args__ = (Index("ix_regime_timestamp", "timestamp"),)
