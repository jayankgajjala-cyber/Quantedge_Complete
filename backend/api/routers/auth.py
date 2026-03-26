"""
backend/api/routers/auth.py
=============================
Two-step authentication:
  POST /api/auth/login       → password verify → OTP dispatch
  POST /api/auth/verify-otp  → OTP verify → JWT issue
  GET  /api/auth/me          → protected, returns current user
  POST /api/auth/logout      → invalidates pending OTP

Multi-worker safety
--------------------
OTP storage and login-lockout state MUST live in the database, never in
Python module-level dicts.  In-process dicts are per-worker: Railway /
Gunicorn spawns multiple workers, so worker-A creates an OTP that
worker-B cannot see, breaking the flow entirely.

Required DB table (add to your Alembic migration or init_db):

    class AuthState(Base):
        __tablename__ = "auth_state"
        id              = Column(Integer, primary_key=True)
        username        = Column(String,  unique=True, index=True, nullable=False)
        otp_hash        = Column(String,  nullable=True)   # bcrypt hash of raw OTP
        otp_expires_at  = Column(DateTime(timezone=True), nullable=True)
        failed_attempts = Column(Integer, default=0, nullable=False)
        locked_until    = Column(DateTime(timezone=True), nullable=True)
        updated_at      = Column(DateTime(timezone=True),
                                 default=func.now(), onupdate=func.now())

The functions imported from backend.core.auth are expected to use this
table (via SQLAlchemy sessions from get_db_context()) instead of any
module-level dict.  The router itself does NOT hold any mutable state.
"""

import logging
from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel, Field
from backend.core.auth import (
    check_lockout, create_access_token, create_otp,
    get_current_user, invalidate_otp, record_failure,
    record_success, send_otp_email, verify_credentials, verify_otp,
)
from backend.core.config import get_settings

logger = logging.getLogger(__name__)
cfg    = get_settings()
router = APIRouter(prefix="/auth", tags=["Authentication"])

# ─── DB-backed auth state helpers (multi-worker safe) ─────────────────────────
#
# These helpers are the reference implementation that backend.core.auth MUST use.
# They replace any module-level dicts such as:
#   _otp_store:       dict[str, tuple[str, datetime]] = {}   ← WORKER-UNSAFE
#   _failed_attempts: dict[str, int]                 = {}   ← WORKER-UNSAFE
#   _lockout_until:   dict[str, datetime]            = {}   ← WORKER-UNSAFE
#
# Because each Gunicorn/Uvicorn worker is a separate OS process, a dict
# written in worker-1 is invisible to worker-2. The load balancer routes the
# OTP-verify request to whichever worker is free — possibly not the one that
# stored the OTP — causing spurious "invalid OTP" errors in production that
# never appear in single-worker local testing.
#
# All state must instead be stored in the `auth_state` table so every worker
# reads consistent data on every request.
#
# Reference implementation for backend/core/auth.py:
#
#   from datetime import datetime, timezone, timedelta
#   import bcrypt, random
#   from backend.core.database import get_db_context
#   from backend.models.auth_state import AuthState   # see schema in module docstring
#
#   async def create_otp(username: str) -> str:
#       raw_otp = "".join(random.choices("0123456789", k=6))
#       hashed  = bcrypt.hashpw(raw_otp.encode(), bcrypt.gensalt()).decode()
#       expires = datetime.now(timezone.utc) + timedelta(seconds=cfg.OTP_TTL_SECONDS)
#       with get_db_context() as db:
#           row = db.query(AuthState).filter_by(username=username).first()
#           if row is None:
#               row = AuthState(username=username)
#               db.add(row)
#           row.otp_hash       = hashed
#           row.otp_expires_at = expires
#       return raw_otp
#
#   async def verify_otp(username: str, raw_otp: str) -> tuple[bool, str]:
#       with get_db_context() as db:
#           row = db.query(AuthState).filter_by(username=username).first()
#           if not row or not row.otp_hash:
#               return False, "No pending OTP for this account."
#           if datetime.now(timezone.utc) > row.otp_expires_at:
#               row.otp_hash = None
#               return False, "OTP has expired. Please log in again."
#           if not bcrypt.checkpw(raw_otp.encode(), row.otp_hash.encode()):
#               return False, "Incorrect OTP."
#           row.otp_hash = None   # one-time use — consume immediately
#       return True, "ok"
#
#   async def invalidate_otp(username: str) -> None:
#       with get_db_context() as db:
#           row = db.query(AuthState).filter_by(username=username).first()
#           if row:
#               row.otp_hash = row.otp_expires_at = None
#
#   async def check_lockout(username: str) -> tuple[bool, float]:
#       with get_db_context() as db:
#           row = db.query(AuthState).filter_by(username=username).first()
#           if row and row.locked_until:
#               remaining = (row.locked_until - datetime.now(timezone.utc)).total_seconds()
#               if remaining > 0:
#                   return True, remaining
#               row.locked_until = None; row.failed_attempts = 0  # lock expired
#       return False, 0.0
#
#   async def record_failure(username: str) -> tuple[int, bool]:
#       with get_db_context() as db:
#           row = db.query(AuthState).filter_by(username=username).first()
#           if row is None:
#               row = AuthState(username=username); db.add(row)
#           row.failed_attempts = (row.failed_attempts or 0) + 1
#           just_locked = False
#           if row.failed_attempts >= cfg.MAX_FAILED_ATTEMPTS:
#               row.locked_until    = datetime.now(timezone.utc) + timedelta(seconds=cfg.LOCKOUT_SECONDS)
#               row.failed_attempts = 0
#               just_locked         = True
#           return row.failed_attempts, just_locked
#
#   async def record_success(username: str) -> None:
#       with get_db_context() as db:
#           row = db.query(AuthState).filter_by(username=username).first()
#           if row:
#               row.failed_attempts = 0; row.locked_until = None


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, example="Jayank8294")
    password: str = Field(..., min_length=1, example="Jayanju@9498")


class OTPRequest(BaseModel):
    username: str = Field(..., example="Jayank8294")
    otp:      str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")


class LoginResponse(BaseModel):
    message:    str
    otp_sent:   bool
    email_hint: str


class TokenResponse(BaseModel):
    access_token: str
    token_type:   str = "bearer"
    expires_at:   str
    username:     str


def _mask(email: str) -> str:
    try:
        local, domain = email.split("@", 1)
        return f"{local[0]}***@{domain}"
    except Exception:
        return "***"


@router.post("/login", response_model=LoginResponse,
             summary="Step 1 — Password verification → OTP dispatch")
async def login(payload: LoginRequest):
    locked, remaining = await check_lockout(payload.username)
    if locked:
        raise HTTPException(
            status.HTTP_423_LOCKED,
            f"Account locked. Try again in {remaining:.0f}s.",
        )

    if not verify_credentials(payload.username, payload.password):
        failures, just_locked = await record_failure(payload.username)
        remaining_attempts = max(0, cfg.MAX_FAILED_ATTEMPTS - failures)
        if just_locked:
            raise HTTPException(
                status.HTTP_423_LOCKED,
                f"Too many failures — account locked for {cfg.LOCKOUT_SECONDS:.0f}s.",
            )
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            f"Invalid credentials. {remaining_attempts} attempt(s) remaining.",
        )

    await record_success(payload.username)
    await invalidate_otp(payload.username)  # clear any stale OTP

    raw_otp = await create_otp(payload.username)
    sent, msg = await send_otp_email(raw_otp, payload.username)
    if not sent:
        logger.error("OTP email failed for '%s': %s", payload.username, msg)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"Could not deliver OTP: {msg}",
        )

    return LoginResponse(
        message    = "Password verified. Check your email for the OTP.",
        otp_sent   = True,
        email_hint = _mask(cfg.OTP_RECIPIENT_EMAIL),
    )


@router.post("/verify-otp", response_model=TokenResponse,
             summary="Step 2 — OTP verification → JWT")
async def verify_otp_endpoint(payload: OTPRequest):
    ok, reason = await verify_otp(payload.username, payload.otp)
    if not ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, reason)

    token, expiry = create_access_token(payload.username)
    logger.info("Full auth complete for '%s'", payload.username)
    return TokenResponse(
        access_token = token,
        expires_at   = expiry.isoformat(),
        username     = payload.username,
    )


@router.get("/me", summary="Verify JWT and return current user (protected)")
def get_me(current_user: str = Depends(get_current_user)):
    return {"username": current_user, "authenticated": True}


@router.post("/logout", summary="Invalidate pending OTP")
async def logout(current_user: str = Depends(get_current_user)):
    await invalidate_otp(current_user)
    return {"message": "Logged out. Discard your JWT on the client side."}
