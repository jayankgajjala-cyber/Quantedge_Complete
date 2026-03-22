"""backend/models/alerts.py — Alert dispatch log for rate limiting."""

from datetime import datetime
from sqlalchemy import Column, Integer, Float, String, DateTime, Boolean, Text, Index
from backend.core.database import Base


class AlertDispatchLog(Base):
    __tablename__ = "alert_dispatch_log"
    id          = Column(Integer, primary_key=True, autoincrement=True)
    ticker      = Column(String(50), index=True)
    signal_type = Column(String(30))
    confidence  = Column(Float, nullable=True)
    regime      = Column(String(30), nullable=True)
    channel     = Column(String(20), default="EMAIL")
    subject     = Column(String(300), nullable=True)
    delivered   = Column(Boolean, default=True)
    error_msg   = Column(Text, nullable=True)
    sent_at     = Column(DateTime, default=datetime.utcnow, index=True)
    __table_args__ = (Index("ix_alert_ticker_signal", "ticker", "signal_type"),)
