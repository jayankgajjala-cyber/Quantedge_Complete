"""
Alert Dispatch Log – SQLAlchemy Model
=======================================
Records every sent alert for rate limiting and audit purposes.
"""

from datetime import datetime
from sqlalchemy import Column, Integer, Float, String, DateTime, Boolean, Text, Index
from backend.core.database import Base


class AlertDispatchLog(Base):
    """
    One row per dispatched alert.
    Used by the rate limiter to enforce MAX_ALERTS_PER_DAY.
    """
    __tablename__ = "alert_dispatch_log"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    ticker       = Column(String(50), nullable=False, index=True)
    signal_type  = Column(String(30), nullable=False)   # BUY | SELL | HOLD | WEEKLY_REPORT
    confidence   = Column(Float, nullable=True)
    regime       = Column(String(30), nullable=True)
    channel      = Column(String(20), nullable=False, default="EMAIL")
    subject      = Column(String(300), nullable=True)
    delivered    = Column(Boolean, default=True)
    error_msg    = Column(Text, nullable=True)
    sent_at      = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    __table_args__ = (
        Index("ix_alert_log_ticker_signal", "ticker", "signal_type"),
        Index("ix_alert_log_sent_at",       "sent_at"),
    )

    def __repr__(self):
        return (
            f"<AlertDispatchLog {self.ticker} {self.signal_type} "
            f"conf={self.confidence} at={self.sent_at}>"
        )
