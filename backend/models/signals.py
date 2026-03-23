"""backend/models/signals.py — Live and final trading signals."""

import enum
import json
from datetime import datetime
from sqlalchemy import Column, Integer, Float, String, DateTime, Boolean, Enum, Text, Index, UniqueConstraint
from backend.core.database import Base


class SignalType(str, enum.Enum):
    BUY  = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    CASH = "CASH"


class SignalStatus(str, enum.Enum):
    ACTIVE    = "ACTIVE"
    TRIGGERED = "TRIGGERED"
    EXPIRED   = "EXPIRED"
    CANCELLED = "CANCELLED"


class RegimeMode(str, enum.Enum):
    STRONG_TREND       = "STRONG_TREND"
    SIDEWAYS           = "SIDEWAYS"
    VOLATILE_HIGH_RISK = "VOLATILE_HIGH_RISK"
    BEAR_CRASHING      = "BEAR_CRASHING"
    UNKNOWN            = "UNKNOWN"


class LiveSignal(Base):
    __tablename__ = "live_signals"
    id               = Column(Integer, primary_key=True, autoincrement=True)
    scan_id          = Column(String(40), nullable=False, index=True)
    symbol           = Column(String(50), nullable=False, index=True)
    strategy_name    = Column(String(100), nullable=False)
    signal_type      = Column(Enum(SignalType), default=SignalType.HOLD)
    price_at_signal  = Column(Float, nullable=True)
    volume_ratio     = Column(Float, nullable=True)
    adx_at_signal    = Column(Float, nullable=True)
    rsi_at_signal    = Column(Float, nullable=True)
    raw_confidence   = Column(Float, nullable=True)
    status           = Column(Enum(SignalStatus), default=SignalStatus.ACTIVE)
    generated_at     = Column(DateTime, default=datetime.utcnow, index=True)
    __table_args__   = (Index("ix_live_scan_sym", "scan_id", "symbol"),)


class FinalSignal(Base):
    __tablename__ = "final_signals"
    id                   = Column(Integer, primary_key=True, autoincrement=True)
    scan_id              = Column(String(40), nullable=False, index=True)
    ticker               = Column(String(50), nullable=False, index=True)
    regime               = Column(Enum(RegimeMode), nullable=False)
    selected_strategy    = Column(String(100), nullable=False)
    signal               = Column(Enum(SignalType), nullable=False)
    confidence           = Column(Float, nullable=False)
    entry_price          = Column(Float, nullable=True)
    stop_loss            = Column(Float, nullable=True)
    target_1             = Column(Float, nullable=True)
    target_2             = Column(Float, nullable=True)
    risk_reward_ratio    = Column(Float, nullable=True)
    adx                  = Column(Float, nullable=True)
    rsi                  = Column(Float, nullable=True)
    volume_ratio         = Column(Float, nullable=True)
    regime_confidence    = Column(Float, nullable=True)
    agreeing_strategies  = Column(Integer, nullable=True)
    total_strategies_run = Column(Integer, nullable=True)
    agreement_bonus      = Column(Float, nullable=True)
    bias_warning         = Column(Boolean, default=False)
    bias_message         = Column(Text, nullable=True)
    # ── Sentiment overlay (The Handshake — Module 5 writes here) ────────────
    sentiment_score      = Column(Float, nullable=True)
    sentiment_label      = Column(String(20), nullable=True)
    sentiment_override   = Column(Boolean, default=False)
    original_signal      = Column(Enum(SignalType), nullable=True)  # before override
    source_confirmations_json = Column(Text, nullable=True)         # JSON: TradingView, Moneycontrol, macro audit
    reason               = Column(Text, default="")
    status               = Column(Enum(SignalStatus), default=SignalStatus.ACTIVE, index=True)
    generated_at         = Column(DateTime, default=datetime.utcnow, index=True)
    expires_at           = Column(DateTime, nullable=True)
    __table_args__       = (
        UniqueConstraint("scan_id", "ticker", name="uq_scan_ticker"),
        Index("ix_final_ticker_time", "ticker", "generated_at"),
    )

    def to_frontend_json(self) -> dict:
        return {
            "ticker":            self.ticker,
            "regime":            self.regime.value,
            "selected_strategy": self.selected_strategy,
            "signal":            self.signal.value,
            "confidence":        round(self.confidence, 1),
            "entry_price":       self.entry_price,
            "stop_loss":         self.stop_loss,
            "target_1":          self.target_1,
            "target_2":          self.target_2,
            "risk_reward":       self.risk_reward_ratio,
            "adx":               self.adx,
            "rsi":               self.rsi,
            "volume_ratio":      self.volume_ratio,
            "agreeing_strategies": self.agreeing_strategies,
            "sentiment_score":   self.sentiment_score,
            "sentiment_label":   self.sentiment_label,
            "sentiment_override":self.sentiment_override,
            "original_signal":   self.original_signal.value if self.original_signal else None,
            "bias_warning":      self.bias_warning,
            "reason":            self.reason,
            "source_confirmations": json.loads(self.source_confirmations_json) if self.source_confirmations_json else None,
            "generated_at":      self.generated_at.isoformat() if self.generated_at else None,
        }


class SignalAgreementLog(Base):
    __tablename__ = "signal_agreement_log"
    id              = Column(Integer, primary_key=True, autoincrement=True)
    scan_id         = Column(String(40), index=True)
    ticker          = Column(String(50))
    buy_votes       = Column(Integer, default=0)
    sell_votes      = Column(Integer, default=0)
    hold_votes      = Column(Integer, default=0)
    total_votes     = Column(Integer, default=0)
    agreement_pct   = Column(Float, nullable=True)
    dominant_signal = Column(Enum(SignalType), nullable=True)
    agreement_bonus = Column(Float, default=0.0)
    bias_detected   = Column(Boolean, default=False)
    logged_at       = Column(DateTime, default=datetime.utcnow)
