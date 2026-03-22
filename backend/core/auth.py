"""
backend/core/auth.py
======================
Consolidated security module. Replaces:
  core/auth_service.py, core/jwt_handler.py, core/otp_store.py,
  core/rate_limiter.py, core/email_service.py

Sections
---------
1. Password verification    – bcrypt, timing-safe, single user
2. OTP store                – bcrypt-hashed, single-use, 5-min TTL
3. Brute-force rate limiter – asyncio.Lock per username, 30s lockout
4. JWT token service        – HS256, get_current_user dependency
5. Resend email delivery    – OTP dispatch via Resend API
"""

from __future__ import annotations

import asyncio
import logging
import random
import secrets
import string
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

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
# ═════════════════════════════════════════════════════════════════════════════

_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)
_HASHED_PASSWORD: str = _pwd_ctx.hash(cfg.AUTH_PASSWORD)
logger.info("Auth: bcrypt hash generated for '%s'", cfg.AUTH_USERNAME)


def verify_credentials(username: str, password: str) -> bool:
    """
    Timing-safe credential check. Always runs hash.verify() even on wrong
    usernames to prevent user-enumeration via timing analysis.
    """
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
# 2. OTP STORE
# ═════════════════════════════════════════════════════════════════════════════

_otp_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")


@dataclass
class _OTPRecord:
    hashed_otp: str
    expires_at: float          # monotonic seconds
    used:       bool = False
    lock:       asyncio.Lock = field(default_factory=asyncio.Lock)


_otp_store: Dict[str, _OTPRecord] = {}
_otp_store_lock = asyncio.Lock()


async def create_otp(username: str) -> str:
    """Generate, hash, store, and return the plaintext OTP."""
    raw      = "".join(random.SystemRandom().choices(string.digits, k=cfg.OTP_LENGTH))
    hashed   = _otp_ctx.hash(raw)
    expires  = time.monotonic() + cfg.OTP_EXPIRE_SECONDS
    async with _otp_store_lock:
        _otp_store[username] = _OTPRecord(hashed_otp=hashed, expires_at=expires)
    logger.info("OTP issued for '%s', expires in %ds", username, cfg.OTP_EXPIRE_SECONDS)
    return raw


async def verify_otp(username: str, candidate: str) -> tuple[bool, str]:
    """Verify submitted OTP. Returns (success, reason)."""
    async with _otp_store_lock:
        record = _otp_store.get(username)
    if record is None:
        return False, "No OTP was issued or it has already been used"
    async with record.lock:
        if record.used:
            return False, "OTP has already been used"
        if time.monotonic() > record.expires_at:
            async with _otp_store_lock:
                _otp_store.pop(username, None)
            return False, "OTP expired — request a new one"
        if not _otp_ctx.verify(candidate, record.hashed_otp):
            return False, "Invalid OTP"
        record.used = True
    async with _otp_store_lock:
        _otp_store.pop(username, None)
    logger.info("OTP verified for '%s'", username)
    return True, "OTP verified"


async def invalidate_otp(username: str) -> None:
    async with _otp_store_lock:
        _otp_store.pop(username, None)


# ═════════════════════════════════════════════════════════════════════════════
# 3. BRUTE-FORCE RATE LIMITER
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class _AttemptRecord:
    failures:     int   = 0
    locked_until: float = 0.0
    lock:         asyncio.Lock = field(default_factory=asyncio.Lock)


_attempt_store: Dict[str, _AttemptRecord] = {}
_attempt_lock  = asyncio.Lock()


async def _get_attempt(username: str) -> _AttemptRecord:
    async with _attempt_lock:
        if username not in _attempt_store:
            _attempt_store[username] = _AttemptRecord()
        return _attempt_store[username]


async def check_lockout(username: str) -> tuple[bool, float]:
    rec = await _get_attempt(username)
    async with rec.lock:
        remaining = rec.locked_until - time.monotonic()
        if remaining > 0:
            return True, round(remaining, 1)
        return False, 0.0


async def record_failure(username: str) -> tuple[int, bool]:
    rec = await _get_attempt(username)
    async with rec.lock:
        rec.failures += 1
        just_locked  = False
        if rec.failures >= cfg.MAX_FAILED_ATTEMPTS:
            rec.locked_until = time.monotonic() + cfg.LOCKOUT_SECONDS
            just_locked      = True
            logger.warning("Account '%s' locked for %.0fs after %d failures",
                           username, cfg.LOCKOUT_SECONDS, rec.failures)
        return rec.failures, just_locked


async def record_success(username: str) -> None:
    rec = await _get_attempt(username)
    async with rec.lock:
        rec.failures     = 0
        rec.locked_until = 0.0
    logger.info("Auth success — counters reset for '%s'", username)


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
    """
    FastAPI dependency. Inject into any route that requires authentication.
    Returns the authenticated username string.
    """
    payload  = _decode_token(credentials.credentials)
    username = payload.get("sub")
    if not username or payload.get("type") != "access":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Malformed token")
    if username != cfg.AUTH_USERNAME:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied")
    return username


# ═════════════════════════════════════════════════════════════════════════════
# 5. RESEND EMAIL / OTP DELIVERY
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
        logger.warning("[DEV MODE] OTP for '%s': %s (email not sent)", username, otp)
        return True, "DEV MODE: OTP logged to console"
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
