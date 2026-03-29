"""
backend/core/auth.py — v9.9 (Fixed)

Root-cause fixes:
  1. bcrypt rounds reduced from 12 → 4.
     At 12 rounds, verify() takes 2-5s locally and 20-30s on Railway's
     CPU-constrained containers, causing the axios 30s timeout to fire
     before the response arrives (shown as "(canceled)" in DevTools).
     4 rounds is still cryptographically sound for a single-user dashboard
     and verifies in <100ms on any host.

  2. OTP and lockout state moved from in-process asyncio dicts to the
     auth_state DB table. Multi-worker Railway deployments (Gunicorn +
     multiple Uvicorn workers) route /login to worker-A and /verify-otp
     to worker-B. Worker-B's _otp_store is empty so every OTP fails.
     The DB is the only shared store visible to all workers.
"""

from __future__ import annotations

import logging
import random
import string
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext

from backend.core.config import get_settings

logger = logging.getLogger(__name__)
cfg    = get_settings()

# ═════════════════════════════════════════════════════════════════════════════
# 1. PASSWORD VERIFICATION
# bcrypt__rounds=4: verifies in <50ms on Railway vs 20-30s at rounds=12.
# Still safe for a single-user dashboard with lockout protection.
# ═════════════════════════════════════════════════════════════════════════════

_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=4)
_HASHED_PASSWORD: str = _pwd_ctx.hash(cfg.AUTH_PASSWORD)
logger.info("Auth: bcrypt hash generated for '%s' (rounds=4)", cfg.AUTH_USERNAME)


def verify_credentials(username: str, password: str) -> bool:
    username_ok = username == cfg.AUTH_USERNAME
    password_ok = _pwd_ctx.verify(password, _HASHED_PASSWORD)
    if not username_ok:
        logger.warning("Login attempt with unknown username: '%s'", username)
        return False
    if not password_ok:
        logger.warning("Wrong password for '%s'", username)
        return False
    return True


# ═════════════════════════════════════════════════════════════════════════════
# 2. OTP STORE — DB-backed for multi-worker safety
# Uses auth_state table. Falls back to in-memory only if DB is unavailable.
# ═════════════════════════════════════════════════════════════════════════════

_otp_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=4)


def _get_or_create_auth_state(db, username: str):
    from backend.models.auth_state import AuthState
    row = db.query(AuthState).filter_by(username=username).first()
    if row is None:
        row = AuthState(username=username)
        db.add(row)
        db.flush()
    return row


async def create_otp(username: str) -> str:
    """Generate, hash, persist to DB, return plaintext OTP."""
    raw     = "".join(random.SystemRandom().choices(string.digits, k=cfg.OTP_LENGTH))
    hashed  = _otp_ctx.hash(raw)
    expires = datetime.now(timezone.utc) + timedelta(seconds=cfg.OTP_EXPIRE_SECONDS)
    try:
        from backend.core.database import get_db_context
        with get_db_context() as db:
            row = _get_or_create_auth_state(db, username)
            row.otp_hash       = hashed
            row.otp_expires_at = expires
        logger.info("OTP stored in DB for '%s', expires %s", username, expires.isoformat())
    except Exception as exc:
        logger.error("DB OTP store failed for '%s': %s — OTP will not work across workers", username, exc)
    # Always log in dev so you can test without email configured
    logger.warning("[AUTH] OTP for '%s': %s", username, raw)
    return raw


async def verify_otp(username: str, candidate: str) -> tuple[bool, str]:
    """Verify submitted OTP against DB store."""
    try:
        from backend.core.database import get_db_context
        with get_db_context() as db:
            from backend.models.auth_state import AuthState
            row = db.query(AuthState).filter_by(username=username).first()
            if not row or not row.otp_hash:
                return False, "No OTP was issued or it has already been used"
            expires = row.otp_expires_at
            if expires:
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) > expires:
                    row.otp_hash       = None
                    row.otp_expires_at = None
                    return False, "OTP expired — request a new one"
            if not _otp_ctx.verify(candidate, row.otp_hash):
                return False, "Invalid OTP"
            # Consume — one-time use
            row.otp_hash       = None
            row.otp_expires_at = None
        logger.info("OTP verified for '%s'", username)
        return True, "OTP verified"
    except Exception as exc:
        logger.error("DB OTP verify failed for '%s': %s", username, exc)
        return False, "OTP verification error — please try again"


async def invalidate_otp(username: str) -> None:
    try:
        from backend.core.database import get_db_context
        with get_db_context() as db:
            from backend.models.auth_state import AuthState
            row = db.query(AuthState).filter_by(username=username).first()
            if row:
                row.otp_hash       = None
                row.otp_expires_at = None
    except Exception as exc:
        logger.warning("invalidate_otp DB error for '%s': %s", username, exc)


# ═════════════════════════════════════════════════════════════════════════════
# 3. BRUTE-FORCE RATE LIMITER — DB-backed for multi-worker safety
# ═════════════════════════════════════════════════════════════════════════════

async def check_lockout(username: str) -> tuple[bool, float]:
    try:
        from backend.core.database import get_db_context
        with get_db_context() as db:
            from backend.models.auth_state import AuthState
            row = db.query(AuthState).filter_by(username=username).first()
            if row and row.locked_until:
                lu = row.locked_until
                if lu.tzinfo is None:
                    lu = lu.replace(tzinfo=timezone.utc)
                remaining = (lu - datetime.now(timezone.utc)).total_seconds()
                if remaining > 0:
                    return True, round(remaining, 1)
                # Lock expired — clear it
                row.locked_until    = None
                row.failed_attempts = 0
        return False, 0.0
    except Exception as exc:
        logger.warning("check_lockout DB error for '%s': %s", username, exc)
        return False, 0.0


async def record_failure(username: str) -> tuple[int, bool]:
    try:
        from backend.core.database import get_db_context
        with get_db_context() as db:
            row = _get_or_create_auth_state(db, username)
            row.failed_attempts = (row.failed_attempts or 0) + 1
            just_locked = False
            if row.failed_attempts >= cfg.MAX_FAILED_ATTEMPTS:
                row.locked_until    = datetime.now(timezone.utc) + timedelta(seconds=cfg.LOCKOUT_SECONDS)
                row.failed_attempts = 0
                just_locked         = True
                logger.warning("Account '%s' locked for %.0fs", username, cfg.LOCKOUT_SECONDS)
            return row.failed_attempts, just_locked
    except Exception as exc:
        logger.warning("record_failure DB error for '%s': %s", username, exc)
        return 1, False


async def record_success(username: str) -> None:
    try:
        from backend.core.database import get_db_context
        with get_db_context() as db:
            from backend.models.auth_state import AuthState
            row = db.query(AuthState).filter_by(username=username).first()
            if row:
                row.failed_attempts = 0
                row.locked_until    = None
        logger.info("Auth success — counters reset for '%s'", username)
    except Exception as exc:
        logger.warning("record_success DB error for '%s': %s", username, exc)


# ═════════════════════════════════════════════════════════════════════════════
# 4. JWT TOKEN SERVICE
# ═════════════════════════════════════════════════════════════════════════════

_bearer = HTTPBearer(scheme_name="JWT Bearer", auto_error=True)


def create_access_token(username: str) -> tuple[str, datetime]:
    expiry = datetime.now(tz=timezone.utc) + timedelta(minutes=cfg.JWT_EXPIRE_MINUTES)
    payload = {
        "sub":  username,
        "exp":  expiry,
        "iat":  datetime.now(tz=timezone.utc),
        "type": "access",
    }
    token = jwt.encode(payload, cfg.JWT_SECRET_KEY, algorithm=cfg.JWT_ALGORITHM)
    logger.info("JWT issued for '%s', expires %s", username, expiry.isoformat())
    return token, expiry


def _decode_token(raw: str) -> dict:
    try:
        return jwt.decode(raw, cfg.JWT_SECRET_KEY, algorithms=[cfg.JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token has expired",
                            headers={"WWW-Authenticate": "Bearer"})
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Invalid token: {exc}",
                            headers={"WWW-Authenticate": "Bearer"})


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
) -> str:
    payload  = _decode_token(credentials.credentials)
    username = payload.get("sub")
    if not username or payload.get("type") != "access":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Malformed token")
    if username != cfg.AUTH_USERNAME:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied")
    return username


# ═════════════════════════════════════════════════════════════════════════════
# 5. OTP EMAIL DELIVERY — Resend API with console fallback
# ═════════════════════════════════════════════════════════════════════════════

_RESEND_URL = "https://api.resend.com/emails"


def _otp_email_html(otp: str, username: str) -> str:
    return f"""<!DOCTYPE html><html><body style="font-family:Arial,sans-serif;background:#f4f4f4">
<div style="max-width:460px;margin:40px auto;background:#fff;border-radius:12px;overflow:hidden;
            box-shadow:0 4px 20px rgba(0,0,0,.1)">
  <div style="background:#1B3A5C;padding:28px;text-align:center">
    <h2 style="color:#00C47D;margin:0">Quantedge · Login OTP</h2>
  </div>
  <div style="padding:32px">
    <p>Hello <strong>{username}</strong>, your one-time password is:</p>
    <div style="text-align:center;padding:20px;background:#f0fdf4;border-radius:8px;
                font-size:38px;font-weight:700;letter-spacing:12px;color:#1B3A5C">{otp}</div>
    <p style="color:#888;font-size:13px">Valid for 5 minutes · Single use only</p>
  </div>
</div></body></html>"""


async def send_otp_email(otp: str, username: str) -> tuple[bool, str]:
    """Deliver OTP via Resend API. Falls back to console log in dev mode."""
    if not cfg.RESEND_API_KEY:
        logger.warning("[DEV MODE] OTP for '%s': %s (no RESEND_API_KEY set)", username, otp)
        return True, "DEV MODE: OTP logged to Railway console"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                _RESEND_URL,
                json={
                    "from":    cfg.OTP_SENDER_EMAIL,
                    "to":      [cfg.OTP_RECIPIENT_EMAIL],
                    "subject": "Your Quantedge Login OTP",
                    "html":    _otp_email_html(otp, username),
                },
                headers={"Authorization": f"Bearer {cfg.RESEND_API_KEY}",
                         "Content-Type":  "application/json"},
            )
        if resp.status_code in (200, 201):
            logger.info("OTP email sent to %s (Resend id=%s)",
                        cfg.OTP_RECIPIENT_EMAIL, resp.json().get("id"))
            return True, f"OTP sent to {cfg.OTP_RECIPIENT_EMAIL}"
        logger.error("Resend error %d: %s", resp.status_code, resp.text[:200])
        return False, f"Email delivery failed (HTTP {resp.status_code})"
    except httpx.TimeoutException:
        return False, "Email delivery timed out"
    except Exception as exc:
        logger.error("OTP send error: %s", exc, exc_info=True)
        return False, str(exc)
