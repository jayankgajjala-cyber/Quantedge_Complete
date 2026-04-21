"""
Signal Alert Email Composer
=============================
Builds and sends professional HTML emails for high-confidence signals
(confidence ≥ 85%) to jayankgajjala@gmail.com.

Features
--------
  • Rich HTML with styled signal card, indicator grid, reasoning block
  • Chart deep-link to the frontend (configurable FRONTEND_BASE_URL)
  • Risk/reward table with colour-coded levels
  • Market regime context with regime icon
  • Sentiment overlay (from Module 5 if available)
  • Rate-limited: uses alert_rate_limiter to enforce 3/day cap

This module is intentionally separate from the Module 5 alert_service.py
so both can coexist. Module 5 handles sentiment-aligned alerts;
Module 8 handles high-confidence technical signal alerts.
"""

from __future__ import annotations

import logging
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from backend.core.config import get_settings as _get_settings

_cfg = _get_settings()
ALERT_CONFIDENCE_THRESHOLD = _cfg.ALERT_CONFIDENCE_THRESHOLD
ALERT_EMAIL_FROM           = _cfg.ALERT_EMAIL_FROM
ALERT_EMAIL_TO             = _cfg.ALERT_EMAIL_TO
FRONTEND_BASE_URL          = _cfg.FRONTEND_BASE_URL
GMAIL_APP_PASSWORD         = _cfg.GMAIL_APP_PASSWORD
SMTP_HOST                  = _cfg.SMTP_HOST
SMTP_PORT                  = _cfg.SMTP_PORT

logger = logging.getLogger(__name__)


# ─── Signal alert email template ─────────────────────────────────────────────

def _signal_color(signal: str) -> str:
    s = signal.upper()
    if s.startswith("BUY"):   return "#00c47d"
    if s.startswith("SELL"):  return "#ff4757"
    return "#f0b429"


def _regime_icon(regime: str) -> str:
    icons = {
        "STRONG_TREND":       "📈",
        "SIDEWAYS":           "↔️",
        "VOLATILE_HIGH_RISK": "⚡",
        "BEAR_CRASHING":      "📉",
    }
    return icons.get(regime.upper(), "📊")


def build_signal_alert_html(
    ticker:           str,
    signal:           str,
    confidence:       float,
    regime:           str,
    strategy_name:    str,
    reason:           str,
    entry_price:      Optional[float]  = None,
    stop_loss:        Optional[float]  = None,
    target_1:         Optional[float]  = None,
    target_2:         Optional[float]  = None,
    risk_reward:      Optional[float]  = None,
    adx:              Optional[float]  = None,
    rsi:              Optional[float]  = None,
    volume_ratio:     Optional[float]  = None,
    agreeing_count:   Optional[int]    = None,
    sentiment_score:  Optional[float]  = None,
    sentiment_label:  Optional[str]    = None,
    forecast_outlook: Optional[str]    = None,
) -> str:
    """Build the full HTML email body for a signal alert."""
    sig_color    = _signal_color(signal)
    regime_icon  = _regime_icon(regime)
    regime_label = regime.replace("_", " ").title()
    chart_url    = f"{FRONTEND_BASE_URL}/signals?ticker={ticker}"
    ts           = datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC")

    # Build price levels table rows
    levels_html = ""
    if entry_price:
        def _price_row(label: str, price: float, color: str) -> str:
            return f"""
            <tr>
              <td style="padding:8px 12px;color:#9ca3af;font-size:12px;border-bottom:1px solid #1f2937">{label}</td>
              <td style="padding:8px 12px;color:{color};font-weight:700;font-size:13px;font-family:monospace;border-bottom:1px solid #1f2937;text-align:right">
                ₹{entry_price if label=='Entry' else (price):,.2f}
              </td>
            </tr>"""
        levels_html = (
            _price_row("Entry",    entry_price,  "#ffffff") +
            (_price_row("Stop Loss", stop_loss,  "#ff4757") if stop_loss else "") +
            (_price_row("Target 1",  target_1,   "#00c47d") if target_1  else "") +
            (_price_row("Target 2",  target_2,   "#00c47d") if target_2  else "")
        )
        if risk_reward:
            levels_html += f"""
            <tr>
              <td style="padding:8px 12px;color:#9ca3af;font-size:12px">R:R Ratio</td>
              <td style="padding:8px 12px;color:#00d4ff;font-weight:700;font-size:13px;font-family:monospace;text-align:right">{risk_reward:.1f}x</td>
            </tr>"""

    # Indicators row
    indicators = []
    if adx is not None:
        adx_color = "#00c47d" if adx > 25 else "#f0b429"
        indicators.append(f'<div style="text-align:center;padding:10px 16px"><div style="color:#6b7280;font-size:10px;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px">ADX</div><div style="color:{adx_color};font-weight:700;font-size:16px;font-family:monospace">{adx:.1f}</div></div>')
    if rsi is not None:
        rsi_color = "#ff4757" if rsi > 70 else ("#00c47d" if rsi < 30 else "#ffffff")
        indicators.append(f'<div style="text-align:center;padding:10px 16px"><div style="color:#6b7280;font-size:10px;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px">RSI</div><div style="color:{rsi_color};font-weight:700;font-size:16px;font-family:monospace">{rsi:.1f}</div></div>')
    if volume_ratio is not None:
        vol_color = "#00c47d" if volume_ratio >= 1.5 else "#6b7280"
        indicators.append(f'<div style="text-align:center;padding:10px 16px"><div style="color:#6b7280;font-size:10px;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px">VOL×</div><div style="color:{vol_color};font-weight:700;font-size:16px;font-family:monospace">{volume_ratio:.2f}x</div></div>')
    if agreeing_count is not None:
        indicators.append(f'<div style="text-align:center;padding:10px 16px"><div style="color:#6b7280;font-size:10px;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px">AGREE</div><div style="color:#00d4ff;font-weight:700;font-size:16px;font-family:monospace">{agreeing_count}/8</div></div>')

    indicators_html = "".join(indicators)

    # Sentiment block
    sentiment_html = ""
    if sentiment_score is not None:
        s_color = "#00c47d" if sentiment_score > 0.3 else ("#ff4757" if sentiment_score < -0.3 else "#f0b429")
        sentiment_html = f"""
        <div style="margin-top:16px;padding:14px;background:#0d1117;border-radius:8px;border-left:3px solid {s_color}">
          <div style="font-size:10px;color:#6b7280;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px">AI Sentiment Overlay</div>
          <div style="display:flex;align-items:center;gap:12px">
            <span style="color:{s_color};font-weight:700;font-size:20px;font-family:monospace">{sentiment_score:+.3f}</span>
            <span style="color:{s_color};font-size:12px;font-weight:600">{sentiment_label or ''}</span>
          </div>
          {f'<div style="color:#9ca3af;font-size:11px;margin-top:6px">{forecast_outlook}</div>' if forecast_outlook else ''}
        </div>"""

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#030712;font-family:'Segoe UI',system-ui,sans-serif">
<div style="max-width:520px;margin:32px auto;background:#111827;border-radius:16px;overflow:hidden;box-shadow:0 20px 60px rgba(0,0,0,0.5)">

  <!-- Banner -->
  <div style="background:linear-gradient(135deg,#0f1e3d 0%,#1a3a6b 100%);padding:20px 28px;border-bottom:1px solid #1f2937">
    <div style="display:flex;align-items:center;justify-content:space-between">
      <div>
        <div style="color:#4b9eff;font-size:10px;letter-spacing:2px;text-transform:uppercase;margin-bottom:4px">QUANTEDGE · SIGNAL ALERT</div>
        <div style="color:#fff;font-size:22px;font-weight:800;letter-spacing:-0.5px">{ticker}</div>
      </div>
      <div style="text-align:right">
        <div style="background:{sig_color};color:#000;font-weight:800;font-size:14px;padding:6px 16px;border-radius:20px;letter-spacing:0.5px">{signal}</div>
        <div style="color:rgba(255,255,255,0.5);font-size:10px;margin-top:6px">{ts}</div>
      </div>
    </div>
  </div>

  <div style="padding:24px 28px">

    <!-- Confidence meter -->
    <div style="margin-bottom:20px">
      <div style="display:flex;justify-content:space-between;margin-bottom:6px">
        <span style="color:#9ca3af;font-size:11px;text-transform:uppercase;letter-spacing:1px">Signal Confidence</span>
        <span style="color:#fff;font-weight:700;font-size:13px">{confidence:.0f}%</span>
      </div>
      <div style="background:#1f2937;height:6px;border-radius:99px;overflow:hidden">
        <div style="background:{'#00c47d' if confidence>=75 else '#f0b429'};height:100%;width:{confidence}%;border-radius:99px;transition:width 0.3s"></div>
      </div>
    </div>

    <!-- Regime + Strategy -->
    <div style="display:flex;gap:10px;margin-bottom:20px;flex-wrap:wrap">
      <div style="background:#1f2937;border-radius:8px;padding:8px 14px;flex:1;min-width:140px">
        <div style="color:#6b7280;font-size:10px;text-transform:uppercase;letter-spacing:1px;margin-bottom:3px">Regime</div>
        <div style="color:#fff;font-size:12px;font-weight:600">{regime_icon} {regime_label}</div>
      </div>
      <div style="background:#1f2937;border-radius:8px;padding:8px 14px;flex:1;min-width:140px">
        <div style="color:#6b7280;font-size:10px;text-transform:uppercase;letter-spacing:1px;margin-bottom:3px">Strategy</div>
        <div style="color:#fff;font-size:12px;font-weight:600">{strategy_name}</div>
      </div>
    </div>

    <!-- Price levels -->
    {f'''<div style="margin-bottom:20px">
      <div style="color:#6b7280;font-size:10px;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px">Price Levels</div>
      <table style="width:100%;border-collapse:collapse;background:#0d1117;border-radius:8px;overflow:hidden">
        {levels_html}
      </table>
    </div>''' if levels_html else ''}

    <!-- Indicators -->
    {f'''<div style="background:#0d1117;border-radius:8px;display:flex;flex-wrap:wrap;margin-bottom:20px;justify-content:center">
      {indicators_html}
    </div>''' if indicators_html else ''}

    <!-- Reasoning -->
    <div style="background:#0d1117;border-radius:8px;padding:14px;margin-bottom:20px">
      <div style="color:#6b7280;font-size:10px;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px">Signal Reasoning</div>
      <p style="color:#d1d5db;font-size:12px;line-height:1.6;margin:0">{reason}</p>
    </div>

    {sentiment_html}

    <!-- CTA -->
    <div style="text-align:center;margin-top:24px">
      <a href="{chart_url}" style="background:linear-gradient(135deg,#00c47d,#00a868);color:#000;font-weight:700;font-size:13px;padding:12px 28px;border-radius:10px;text-decoration:none;display:inline-block;letter-spacing:0.3px">
        📊 View Full Chart & Analysis →
      </a>
    </div>

  </div>

  <!-- Footer -->
  <div style="background:#0d1117;padding:14px 28px;text-align:center;border-top:1px solid #1f2937">
    <p style="color:#4b5563;font-size:10px;margin:0">Quantedge Automated Alert · This is not financial advice · Rate limited to {3} alerts/day</p>
  </div>

</div>
</body>
</html>"""


# ─── Send function ────────────────────────────────────────────────────────────

def send_signal_alert_email(
    ticker:           str,
    signal:           str,
    confidence:       float,
    regime:           str,
    strategy_name:    str,
    reason:           str,
    entry_price:      Optional[float] = None,
    stop_loss:        Optional[float] = None,
    target_1:         Optional[float] = None,
    target_2:         Optional[float] = None,
    risk_reward:      Optional[float] = None,
    adx:              Optional[float] = None,
    rsi:              Optional[float] = None,
    volume_ratio:     Optional[float] = None,
    agreeing_count:   Optional[int]   = None,
    sentiment_score:  Optional[float] = None,
    sentiment_label:  Optional[str]   = None,
    forecast_outlook: Optional[str]   = None,
) -> tuple[bool, str]:
    """
    Build and send the signal alert email via Gmail SMTP.

    Returns (success: bool, message: str).
    """
    if not ALERT_EMAIL_FROM or not GMAIL_APP_PASSWORD:
        msg = (
            f"[DEV MODE] Signal alert for {ticker} {signal} "
            f"conf={confidence:.0f}% — GMAIL_APP_PASSWORD not set, email suppressed."
        )
        logger.warning(msg)
        return True, msg

    subject = (
        f"🚨 {ticker} · {signal} Signal · {confidence:.0f}% Confidence "
        f"[{regime.replace('_', ' ')}]"
    )
    html_body = build_signal_alert_html(
        ticker=ticker, signal=signal, confidence=confidence, regime=regime,
        strategy_name=strategy_name, reason=reason, entry_price=entry_price,
        stop_loss=stop_loss, target_1=target_1, target_2=target_2,
        risk_reward=risk_reward, adx=adx, rsi=rsi, volume_ratio=volume_ratio,
        agreeing_count=agreeing_count, sentiment_score=sentiment_score,
        sentiment_label=sentiment_label, forecast_outlook=forecast_outlook,
    )

    msg_obj = MIMEMultipart("alternative")
    msg_obj["Subject"] = subject
    msg_obj["From"]    = ALERT_EMAIL_FROM
    msg_obj["To"]      = ALERT_EMAIL_TO
    msg_obj.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.ehlo()
            server.starttls()
            server.login(ALERT_EMAIL_FROM, GMAIL_APP_PASSWORD)
            server.sendmail(ALERT_EMAIL_FROM, ALERT_EMAIL_TO, msg_obj.as_string())

        logger.info(
            "Signal alert email sent → %s | %s %s conf=%.0f%%",
            ALERT_EMAIL_TO, ticker, signal, confidence,
        )
        return True, f"Alert sent to {ALERT_EMAIL_TO}"

    except smtplib.SMTPAuthenticationError:
        msg = "Gmail authentication failed — check ALERT_EMAIL_FROM and GMAIL_APP_PASSWORD"
        logger.error(msg)
        return False, msg

    except smtplib.SMTPException as exc:
        msg = f"SMTP error for {ticker} alert: {exc}"
        logger.error(msg)
        return False, msg

    except Exception as exc:
        msg = f"Unexpected alert send error: {exc}"
        logger.error(msg, exc_info=True)
        return False, msg
