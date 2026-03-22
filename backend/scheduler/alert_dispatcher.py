"""
Alert Dispatcher
=================
After each heartbeat scan, evaluates the resulting FinalSignals
and dispatches email alerts for any signal meeting the threshold:

  confidence ≥ 85%  (ALERT_CONFIDENCE_THRESHOLD)

Pipeline per signal
--------------------
  1. Check rate limiter (3/day cap + 60-min dedup)
  2. Optionally enrich with Module 5 sentiment
  3. Build + send the HTML alert email
  4. Log dispatch in alert_dispatch_log

This is called at the end of every heartbeat cycle with the scan results.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

from core.config import ALERT_CONFIDENCE_THRESHOLD
from scheduler.alert_rate_limiter import can_send_alert, record_alert_sent
from scheduler.signal_alert_email import send_signal_alert_email

logger = logging.getLogger(__name__)


def dispatch_alerts_for_scan(
    scan_results: list[dict],
    db:           Session,
) -> dict:
    """
    Evaluate all FinalSignals from a scan and send email alerts
    for high-confidence signals that pass the rate limiter.

    Parameters
    ----------
    scan_results : list of FinalSignal.to_frontend_json() dicts
    db           : SQLAlchemy session for rate-limiter DB access

    Returns
    -------
    summary dict with alert counts
    """
    summary = {
        "evaluated":   len(scan_results),
        "above_threshold": 0,
        "sent":        0,
        "suppressed":  0,
        "errors":      0,
    }

    for sig in scan_results:
        ticker     = sig.get("ticker", "")
        signal     = sig.get("signal", "HOLD")
        confidence = float(sig.get("confidence", 0))

        # Skip HOLD/CASH signals regardless of confidence
        if signal.upper() in ("HOLD", "CASH"):
            continue

        # Confidence gate
        if confidence < ALERT_CONFIDENCE_THRESHOLD:
            continue

        summary["above_threshold"] += 1

        # Rate limiter
        allowed, reason = can_send_alert(ticker, signal, db)
        if not allowed:
            summary["suppressed"] += 1
            logger.info("Alert suppressed for %s %s: %s", ticker, signal, reason)
            continue

        # Optional: enrich with sentiment from Module 5
        sentiment_score  = sig.get("sentiment_score")
        sentiment_label  = sig.get("sentiment_label")
        forecast_outlook = sig.get("forecast_outlook")

        # Send email
        ok, msg = send_signal_alert_email(
            ticker           = ticker,
            signal           = signal,
            confidence       = confidence,
            regime           = sig.get("regime", "UNKNOWN"),
            strategy_name    = sig.get("selected_strategy", ""),
            reason           = sig.get("reason", ""),
            entry_price      = sig.get("entry_price"),
            stop_loss        = sig.get("stop_loss"),
            target_1         = sig.get("target_1"),
            target_2         = sig.get("target_2"),
            risk_reward      = sig.get("risk_reward"),
            adx              = sig.get("adx"),
            rsi              = sig.get("rsi"),
            volume_ratio     = sig.get("volume_ratio"),
            agreeing_count   = sig.get("agreeing_strategies"),
            sentiment_score  = sentiment_score,
            sentiment_label  = sentiment_label,
            forecast_outlook = forecast_outlook,
        )

        if ok:
            try:
                record_alert_sent(
                    db          = db,
                    ticker      = ticker,
                    signal_type = signal,
                    confidence  = confidence,
                    regime      = sig.get("regime", ""),
                    channel     = "EMAIL",
                    subject     = f"{ticker} {signal} {confidence:.0f}%",
                )
                summary["sent"] += 1
            except Exception as exc:
                logger.error("Failed to log alert record: %s", exc)
                summary["errors"] += 1
        else:
            summary["errors"] += 1
            logger.warning("Alert send failed for %s: %s", ticker, msg)

    logger.info(
        "Alert dispatch: evaluated=%d above_threshold=%d sent=%d suppressed=%d errors=%d",
        summary["evaluated"], summary["above_threshold"],
        summary["sent"], summary["suppressed"], summary["errors"],
    )
    return summary
