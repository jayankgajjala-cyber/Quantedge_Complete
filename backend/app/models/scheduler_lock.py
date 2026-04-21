"""
backend/models/scheduler_lock.py
==================================
DB-backed scheduler leader-election lock for multi-worker Railway deployments.

Table: scheduler_lock
  lock_name    PK  -- e.g. "apscheduler"
  worker_pid       -- OS PID of the process that currently owns the lock
  refreshed_at     -- UTC timestamp; if stale (>60s) another worker takes over

This table is created by init_db() along with all other models.
It must be imported in backend.core.database._register_all_models().
"""

from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime
from backend.core.database import Base


class SchedulerLock(Base):
    __tablename__ = "scheduler_lock"

    lock_name    = Column(String(64), primary_key=True)   # e.g. "apscheduler"
    worker_pid   = Column(Integer, nullable=False)
    refreshed_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    def __repr__(self) -> str:
        return f"<SchedulerLock lock={self.lock_name} pid={self.worker_pid} refreshed={self.refreshed_at}>"
