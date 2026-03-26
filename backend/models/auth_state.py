"""
backend/models/auth_state.py
==============================
DB-backed auth state for multi-worker safe OTP and lockout tracking.

Table: auth_state
  id              PK
  username        UNIQUE — matches the login username
  otp_hash        bcrypt hash of the raw 6-digit OTP (nullable when no OTP pending)
  otp_expires_at  UTC expiry of the OTP window
  failed_attempts running count of consecutive failed login attempts
  locked_until    UTC datetime until which this account is locked out (nullable)
  updated_at      auto-updated on every write

This table is created by init_db() along with all other models.
It is imported in backend.core.database._register_all_models().
"""

from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.sql import func

from backend.core.database import Base


class AuthState(Base):
    __tablename__ = "auth_state"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    username        = Column(String(255), unique=True, index=True, nullable=False)
    otp_hash        = Column(String(255), nullable=True)
    otp_expires_at  = Column(DateTime(timezone=True), nullable=True)
    failed_attempts = Column(Integer, default=0, nullable=False)
    locked_until    = Column(DateTime(timezone=True), nullable=True)
    updated_at      = Column(
        DateTime(timezone=True),
        default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<AuthState username={self.username!r} "
            f"failed={self.failed_attempts} "
            f"locked_until={self.locked_until}>"
        )
