"""
backend/api/routers/auth.py
=============================
Two-step authentication:
  POST /api/auth/login       → password verify → OTP dispatch
  POST /api/auth/verify-otp  → OTP verify → JWT issue
  GET  /api/auth/me          → protected, returns current user
  POST /api/auth/logout      → invalidates pending OTP
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
