"""
Master Scheduler
=================
Single APScheduler instance that manages ALL scheduled jobs
across the entire trading system.

Jobs registered
---------------
  heartbeat_5min         interval 300s   weekdays 09:15–15:35 IST
  regime_detector        interval 300s   always (runs own market hours check)
  sl_target_monitor      interval 300s   always (runs own market hours check)
  weekly_report          cron  Sat 18:00 UTC
  weekly_backtest        cron  Sat 01:00 UTC
  research_refresh       interval 3600s  always (60-min cache guard inside)

Design decisions
----------------
• A single scheduler instance prevents duplicate jobs.
• max_instances=1 on all jobs prevents pile-up if a job runs long.
• Misfire grace period = 120s — if server restarts within 2 min of a
  scheduled time, the job will still fire rather than being skipped.
• coalesce=True — if multiple firings are missed (server downtime),
  only one catch-up run is executed.
"""

from __future__ import annotations

import logging
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.events import (
    EVENT_JOB_ERROR,
    EVENT_JOB_EXECUTED,
    EVENT_JOB_MISSED,
    JobExecutionEvent,
)

from core.config import (
    HEARTBEAT_INTERVAL_SECS,
    WEEKLY_BACKTEST_DAY,
    WEEKLY_BACKTEST_HOUR_UTC,
    WEEKLY_REPORT_DAY,
    WEEKLY_REPORT_HOUR_UTC,
)

logger = logging.getLogger(__name__)

_scheduler: Optional[AsyncIOScheduler] = None


# ─── Event listeners ─────────────────────────────────────────────────────────

def _on_job_executed(event: JobExecutionEvent) -> None:
    logger.debug("Job '%s' executed successfully", event.job_id)


def _on_job_error(event: JobExecutionEvent) -> None:
    logger.error(
        "Job '%s' raised an exception: %s",
        event.job_id, event.exception, exc_info=event.traceback,
    )


def _on_job_missed(event: JobExecutionEvent) -> None:
    logger.warning("Job '%s' missed its scheduled time", event.job_id)


# ─── Scheduler factory ────────────────────────────────────────────────────────

def create_scheduler() -> AsyncIOScheduler:
    """Build and configure the master APScheduler instance."""
    scheduler = AsyncIOScheduler(
        timezone    = "UTC",
        job_defaults= {
            "coalesce":           True,
            "max_instances":      1,
            "misfire_grace_time": 120,
        },
    )

    # Attach event listeners
    scheduler.add_listener(_on_job_executed, EVENT_JOB_EXECUTED)
    scheduler.add_listener(_on_job_error,    EVENT_JOB_ERROR)
    scheduler.add_listener(_on_job_missed,   EVENT_JOB_MISSED)

    return scheduler


def register_all_jobs(scheduler: AsyncIOScheduler) -> None:
    """
    Register every scheduled job on the provided scheduler instance.
    Call this after create_scheduler() and before scheduler.start().
    """

    # ── 1. Heartbeat: 5-min market-hours pipeline ─────────────────────────────
    try:
        from scheduler.heartbeat import heartbeat_job
        scheduler.add_job(
            heartbeat_job,
            trigger          = "interval",
            seconds          = HEARTBEAT_INTERVAL_SECS,
            id               = "heartbeat_5min",
            name             = "5-Min Market Heartbeat",
            replace_existing = True,
        )
        logger.info("✓ heartbeat_5min registered (every %ds)", HEARTBEAT_INTERVAL_SECS)
    except Exception as exc:
        logger.error("Failed to register heartbeat_5min: %s", exc)

    # ── 2. Regime detector ────────────────────────────────────────────────────
    try:
        from engine.regime_detector import _regime_job
        scheduler.add_job(
            _regime_job,
            trigger          = "interval",
            seconds          = 300,
            id               = "regime_detector",
            name             = "Market Regime Detector",
            replace_existing = True,
        )
        logger.info("✓ regime_detector registered (every 300s)")
    except Exception as exc:
        logger.error("Failed to register regime_detector: %s", exc)

    # ── 3. SL/Target monitor ──────────────────────────────────────────────────
    try:
        from services.paper.risk_monitor import sl_monitor_job
        scheduler.add_job(
            sl_monitor_job,
            trigger          = "interval",
            seconds          = 300,
            id               = "sl_target_monitor",
            name             = "SL/Target Risk Monitor",
            replace_existing = True,
        )
        logger.info("✓ sl_target_monitor registered (every 300s)")
    except Exception as exc:
        logger.error("Failed to register sl_target_monitor: %s", exc)

    # ── 4. Weekly paper trading report ────────────────────────────────────────
    try:
        from services.paper.weekly_report import weekly_report_job
        scheduler.add_job(
            weekly_report_job,
            trigger          = "cron",
            day_of_week      = WEEKLY_REPORT_DAY,
            hour             = WEEKLY_REPORT_HOUR_UTC,
            minute           = 0,
            id               = "weekly_report",
            name             = "Weekly Paper Trading Report",
            replace_existing = True,
        )
        logger.info(
            "✓ weekly_report registered (day=%d, %02d:00 UTC)",
            WEEKLY_REPORT_DAY, WEEKLY_REPORT_HOUR_UTC,
        )
    except Exception as exc:
        logger.error("Failed to register weekly_report: %s", exc)

    # ── 5. Weekly backtest refresh ─────────────────────────────────────────────
    try:
        from scheduler.weekly_backtest import weekly_backtest_job
        scheduler.add_job(
            weekly_backtest_job,
            trigger          = "cron",
            day_of_week      = WEEKLY_BACKTEST_DAY,
            hour             = WEEKLY_BACKTEST_HOUR_UTC,
            minute           = 0,
            id               = "weekly_backtest",
            name             = "Weekly Backtest Refresh (Nifty 500)",
            replace_existing = True,
        )
        logger.info(
            "✓ weekly_backtest registered (Saturday %02d:00 UTC)",
            WEEKLY_BACKTEST_HOUR_UTC,
        )
    except Exception as exc:
        logger.error("Failed to register weekly_backtest: %s", exc)

    # ── 6. Portfolio research refresh ─────────────────────────────────────────
    try:
        async def _research_refresh_job():
            import asyncio
            from models.session import SessionLocal as _SL
            from services.research.deep_research_service import DeepResearchService as _DRS
            db = _SL()
            try:
                await asyncio.get_event_loop().run_in_executor(
                    None, lambda: _DRS(db).refresh_portfolio()
                )
            finally:
                db.close()

        scheduler.add_job(
            _research_refresh_job,
            trigger          = "interval",
            seconds          = 3600,
            id               = "research_refresh",
            name             = "Portfolio Research Refresh",
            replace_existing = True,
        )
        logger.info("✓ research_refresh registered (every 3600s)")
    except Exception as exc:
        logger.error("Failed to register research_refresh: %s", exc)

    logger.info(
        "Master scheduler: %d jobs registered",
        len(scheduler.get_jobs()),
    )


def start_master_scheduler() -> AsyncIOScheduler:
    """
    Create, configure, and start the master scheduler.
    Returns the running scheduler instance for later shutdown.
    """
    global _scheduler
    _scheduler = create_scheduler()
    register_all_jobs(_scheduler)
    _scheduler.start()
    logger.info(
        "Master scheduler STARTED — %d jobs active",
        len(_scheduler.get_jobs()),
    )
    return _scheduler


def get_scheduler() -> Optional[AsyncIOScheduler]:
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Master scheduler stopped")
    _scheduler = None
