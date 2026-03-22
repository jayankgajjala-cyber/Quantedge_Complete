"""backend/models/paper.py — Paper trading, budget, and ledger tables."""

import enum
from datetime import datetime
from sqlalchemy import Column, Integer, Float, String, DateTime, Boolean, Enum, Text, Index, UniqueConstraint, ForeignKey
from sqlalchemy.orm import relationship
from backend.core.database import Base
from backend.models.portfolio import DataQuality


class TradeDirection(str, enum.Enum):
    BUY  = "BUY"
    SELL = "SELL"


class TradeStatus(str, enum.Enum):
    OPEN   = "OPEN"
    CLOSED = "CLOSED"


class LedgerEntryType(str, enum.Enum):
    TRADE_OPEN   = "TRADE_OPEN"
    TRADE_CLOSE  = "TRADE_CLOSE"
    SL_HIT       = "SL_HIT"
    TARGET_HIT   = "TARGET_HIT"
    MANUAL_CLOSE = "MANUAL_CLOSE"


class AllocationStatus(str, enum.Enum):
    SUGGESTED = "SUGGESTED"
    EXECUTED  = "EXECUTED"
    SKIPPED   = "SKIPPED"
    REJECTED  = "REJECTED"


class PaperTrade(Base):
    __tablename__ = "paper_trades"
    id            = Column(Integer, primary_key=True, autoincrement=True)
    holding_id    = Column(Integer, ForeignKey("holdings.id", ondelete="SET NULL"), nullable=True)
    symbol        = Column(String(50), nullable=False, index=True)
    direction     = Column(Enum(TradeDirection), nullable=False)
    quantity      = Column(Float, nullable=False)
    entry_price   = Column(Float, nullable=False)
    exit_price    = Column(Float, nullable=True)
    stop_loss     = Column(Float, nullable=True)
    target        = Column(Float, nullable=True)
    status        = Column(Enum(TradeStatus), default=TradeStatus.OPEN, index=True)
    strategy_name = Column(String(200), nullable=True)
    pnl           = Column(Float, nullable=True)
    pnl_pct       = Column(Float, nullable=True)
    entry_time    = Column(DateTime, default=datetime.utcnow)
    exit_time     = Column(DateTime, nullable=True)
    notes         = Column(Text, nullable=True)
    holding       = relationship("Holding", back_populates="paper_trades")


class VirtualLedger(Base):
    __tablename__ = "virtual_ledger"
    id               = Column(Integer, primary_key=True, autoincrement=True)
    trade_id         = Column(Integer, nullable=False, index=True)
    symbol           = Column(String(50), index=True)
    entry_type       = Column(Enum(LedgerEntryType))
    price            = Column(Float)
    quantity         = Column(Float)
    gross_value      = Column(Float)
    commission       = Column(Float, default=0.0)
    net_value        = Column(Float)
    realised_pnl     = Column(Float, nullable=True)
    realised_pnl_pct = Column(Float, nullable=True)
    budget_cycle_id  = Column(Integer, nullable=True)
    close_reason     = Column(String(50), nullable=True)
    trigger_price    = Column(Float, nullable=True)
    timestamp        = Column(DateTime, default=datetime.utcnow, index=True)
    notes            = Column(Text, nullable=True)


class BudgetCycle(Base):
    __tablename__ = "budget_cycles"
    id             = Column(Integer, primary_key=True, autoincrement=True)
    year           = Column(Integer)
    month          = Column(Integer)
    total_budget   = Column(Float, default=15_000.0)
    allocated      = Column(Float, default=0.0)
    realised_pnl   = Column(Float, default=0.0)
    open_trades    = Column(Integer, default=0)
    closed_trades  = Column(Integer, default=0)
    winning_trades = Column(Integer, default=0)
    updated_at     = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @property
    def remaining_budget(self) -> float:
        return max(0.0, self.total_budget - self.allocated)

    @property
    def utilisation_pct(self) -> float:
        return (self.allocated / self.total_budget * 100) if self.total_budget > 0 else 0.0

    __table_args__ = (UniqueConstraint("year", "month", name="uq_budget_ym"),)


class AllocationEvent(Base):
    __tablename__ = "allocation_events"
    id                 = Column(Integer, primary_key=True, autoincrement=True)
    ticker             = Column(String(50), index=True)
    signal_confidence  = Column(Float)
    current_price      = Column(Float)
    budget_remaining   = Column(Float)
    allocation_amount  = Column(Float)
    allocation_pct     = Column(Float)
    suggested_quantity = Column(Float)
    actual_cost        = Column(Float)
    stop_loss          = Column(Float, nullable=True)
    target             = Column(Float, nullable=True)
    risk_reward_ratio  = Column(Float, nullable=True)
    risk_per_trade_inr = Column(Float, nullable=True)
    status             = Column(Enum(AllocationStatus), default=AllocationStatus.SUGGESTED)
    trade_id           = Column(Integer, nullable=True)
    skip_reason        = Column(Text, nullable=True)
    created_at         = Column(DateTime, default=datetime.utcnow)


class WeeklyReport(Base):
    __tablename__ = "weekly_reports"
    id                  = Column(Integer, primary_key=True, autoincrement=True)
    week_start          = Column(DateTime, index=True)
    week_end            = Column(DateTime)
    total_trades_week   = Column(Integer, default=0)
    winning_trades_week = Column(Integer, default=0)
    net_pnl_week        = Column(Float, default=0.0)
    gross_pnl_week      = Column(Float, default=0.0)
    commission_paid     = Column(Float, default=0.0)
    unrealised_pnl      = Column(Float, default=0.0)
    cagr_cumulative     = Column(Float, nullable=True)
    max_drawdown        = Column(Float, nullable=True)
    sharpe_ratio        = Column(Float, nullable=True)
    win_rate_cumulative = Column(Float, nullable=True)
    total_trades_all    = Column(Integer, default=0)
    profit_factor       = Column(Float, nullable=True)
    budget_used_month   = Column(Float, default=0.0)
    budget_remaining    = Column(Float, default=0.0)
    summary_text        = Column(Text, nullable=True)
    email_sent          = Column(Boolean, default=False)
    generated_at        = Column(DateTime, default=datetime.utcnow)


class LivePnLSnapshot(Base):
    __tablename__ = "live_pnl_snapshots"
    id                  = Column(Integer, primary_key=True, autoincrement=True)
    trade_id            = Column(Integer, index=True)
    symbol              = Column(String(50), index=True)
    entry_price         = Column(Float)
    current_price       = Column(Float)
    quantity            = Column(Float)
    unrealised_pnl      = Column(Float)
    unrealised_pct      = Column(Float)
    sl_distance_pct     = Column(Float, nullable=True)
    target_distance_pct = Column(Float, nullable=True)
    sl_breached         = Column(Boolean, default=False)
    target_hit          = Column(Boolean, default=False)
    snapshot_at         = Column(DateTime, default=datetime.utcnow, index=True)
