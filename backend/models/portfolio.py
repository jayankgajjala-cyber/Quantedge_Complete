"""backend/models/portfolio.py — Portfolio holdings and OHLCV history."""

import enum
from datetime import datetime
from sqlalchemy import Column, Integer, Float, String, DateTime, Enum, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import relationship
from backend.core.database import Base


class DataQuality(str, enum.Enum):
    SUFFICIENT   = "SUFFICIENT"
    INSUFFICIENT = "INSUFFICIENT DATA"   # 5-9 years
    LOW_CONFIDENCE = "LOW CONFIDENCE"    # < 5 years


class IntervalType(str, enum.Enum):
    ONE_MIN = "1min"
    DAILY   = "daily"


class Holding(Base):
    __tablename__ = "holdings"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    symbol        = Column(String(50), nullable=False, unique=True, index=True)
    isin          = Column(String(20), nullable=True)
    exchange      = Column(String(20), nullable=False, default="NSE")
    quantity      = Column(Float, nullable=False)
    average_price = Column(Float, nullable=False)
    current_price = Column(Float, nullable=True)
    pnl           = Column(Float, nullable=True)
    pnl_pct       = Column(Float, nullable=True)
    sector        = Column(String(100), nullable=True)
    data_quality  = Column(Enum(DataQuality), default=DataQuality.SUFFICIENT)
    uploaded_at   = Column(DateTime, default=datetime.utcnow)
    updated_at    = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    historical_data = relationship("HistoricalData", back_populates="holding",
                                    cascade="all, delete-orphan")
    paper_trades    = relationship("PaperTrade", back_populates="holding",
                                    cascade="all, delete-orphan")


class HistoricalData(Base):
    __tablename__ = "historical_data"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    holding_id   = Column(Integer, ForeignKey("holdings.id", ondelete="CASCADE"), nullable=False)
    symbol       = Column(String(50), nullable=False, index=True)
    interval = Column(Enum(IntervalType, name="intervaltype"), nullable=False)
    timestamp    = Column(DateTime, nullable=False, index=True)
    open         = Column(Float, nullable=False)
    high         = Column(Float, nullable=False)
    low          = Column(Float, nullable=False)
    close        = Column(Float, nullable=False)
    volume       = Column(Float, nullable=False)
    data_quality = Column(Enum(DataQuality), default=DataQuality.SUFFICIENT)
    fetched_at   = Column(DateTime, default=datetime.utcnow)

    holding = relationship("Holding", back_populates="historical_data")

    __table_args__ = (
        UniqueConstraint("symbol", "interval", "timestamp", name="uq_hist_sym_int_ts"),
        Index("ix_hist_sym_int", "symbol", "interval"),
    )
