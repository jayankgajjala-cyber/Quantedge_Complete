"""backend/models/backtest.py — Strategy performance results."""

import enum
from datetime import datetime
from sqlalchemy import Column, Integer, Float, String, DateTime, Enum, Text, Index, UniqueConstraint
from backend.core.database import Base
from backend.models.portfolio import DataQuality


class StrategyPerformance(Base):
    __tablename__ = "strategy_performance"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    stock_ticker     = Column(String(50), nullable=False, index=True)
    strategy_name    = Column(String(100), nullable=False)
    sharpe_ratio     = Column(Float, nullable=True)
    cagr             = Column(Float, nullable=True)
    win_rate         = Column(Float, nullable=True)
    max_drawdown     = Column(Float, nullable=True)
    sortino_ratio    = Column(Float, nullable=True)
    profit_factor    = Column(Float, nullable=True)
    total_trades     = Column(Integer, nullable=True)
    winning_trades   = Column(Integer, nullable=True)
    losing_trades    = Column(Integer, nullable=True)
    total_return_pct = Column(Float, nullable=True)
    annual_volatility= Column(Float, nullable=True)
    calmar_ratio     = Column(Float, nullable=True)
    avg_win          = Column(Float, nullable=True)
    avg_loss         = Column(Float, nullable=True)
    backtest_start   = Column(DateTime, nullable=True)
    backtest_end     = Column(DateTime, nullable=True)
    years_of_data    = Column(Float, nullable=True)
    data_quality     = Column(Enum(DataQuality), default=DataQuality.SUFFICIENT)
    initial_capital  = Column(Float, default=100_000.0)
    strategy_params  = Column(Text, nullable=True)
    ran_at           = Column(DateTime, default=datetime.utcnow)
    notes            = Column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("stock_ticker", "strategy_name", name="uq_ticker_strategy"),
        Index("ix_perf_ticker_strategy", "stock_ticker", "strategy_name"),
    )
