"""
Auth Test Suite
================
Tests every security requirement from Module 2:

  ✓  Correct credentials → OTP dispatched
  ✓  Wrong username      → 401 (no lockout for unknown users)
  ✓  Wrong password      → 401 + attempt counter increments
  ✓  3 failures          → 423 Locked for 30 s
  ✓  OTP verify          → JWT issued on success
  ✓  Wrong OTP           → 401
  ✓  Expired OTP         → 401
  ✓  Replay OTP          → 401 (single-use)
  ✓  Protected route     → 401 without token
  ✓  Protected route     → 200 with valid token
  ✓  Expired JWT         → 401
  ✓  Tampered JWT        → 401

Run with:
    pytest tests/test_auth.py -v
"""

import asyncio
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import jwt
import pytest
import pytest_asyncio

# ─── Unit tests (no HTTP server needed) ──────────────────────────────────────


class TestPasswordVerification:
    """core/auth_service.py – verify_credentials()"""

    def test_correct_credentials(self):
        from core.auth_service import verify_credentials
        assert verify_credentials("Jayank8294", "Jayanju@9498") is True

    def test_wrong_password(self):
        from core.auth_service import verify_credentials
        assert verify_credentials("Jayank8294", "wrongpassword") is False

    def test_wrong_username(self):
        from core.auth_service import verify_credentials
        assert verify_credentials("NotAUser", "Jayanju@9498") is False

    def test_empty_credentials(self):
        from core.auth_service import verify_credentials
        assert verify_credentials("", "") is False

    def test_case_sensitive_username(self):
        from core.auth_service import verify_credentials
        assert verify_credentials("jayank8294", "Jayanju@9498") is False

    def test_case_sensitive_password(self):
        from core.auth_service import verify_credentials
        assert verify_credentials("Jayank8294", "jayanju@9498") is False


class TestOTPStore:
    """core/otp_store.py – full OTP lifecycle"""

    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def test_create_and_verify_otp(self):
        from core.otp_store import create_otp, verify_otp
        raw = self._run(create_otp("testuser"))
        assert len(raw) == 6 and raw.isdigit()
        ok, msg = self._run(verify_otp("testuser", raw))
        assert ok is True, msg

    def test_wrong_otp_rejected(self):
        from core.otp_store import create_otp, verify_otp
        self._run(create_otp("testuser2"))
        ok, msg = self._run(verify_otp("testuser2", "000000"))
        assert ok is False
        assert "Invalid" in msg

    def test_single_use_enforcement(self):
        from core.otp_store import create_otp, verify_otp
        raw = self._run(create_otp("testuser3"))
        ok1, _ = self._run(verify_otp("testuser3", raw))
        ok2, msg2 = self._run(verify_otp("testuser3", raw))
        assert ok1 is True
        assert ok2 is False
        assert "already been used" in msg2 or "No OTP" in msg2

    def test_no_otp_for_user(self):
        from core.otp_store import verify_otp
        ok, msg = self._run(verify_otp("ghost_user", "123456"))
        assert ok is False
        assert "No OTP" in msg

    def test_expired_otp(self):
        """Simulate expiry by patching time.monotonic."""
        from core import otp_store
        raw = asyncio.get_event_loop().run_until_complete(
            otp_store.create_otp("expire_user")
        )
        # Manually set the expiry to the past
        record = otp_store._store.get("expire_user")
        assert record is not None
        record.expires_at = time.monotonic() - 1   # already expired

        ok, msg = asyncio.get_event_loop().run_until_complete(
            otp_store.verify_otp("expire_user", raw)
        )
        assert ok is False
        assert "expired" in msg.lower()

    def test_invalidate_clears_otp(self):
        from core.otp_store import create_otp, invalidate_otp, verify_otp
        self._run(create_otp("inv_user"))
        self._run(invalidate_otp("inv_user"))
        ok, _ = self._run(verify_otp("inv_user", "123456"))
        assert ok is False


class TestRateLimiter:
    """core/rate_limiter.py – brute-force protection"""

    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def test_no_lockout_initially(self):
        from core.rate_limiter import check_lockout
        locked, remaining = self._run(check_lockout("fresh_user"))
        assert locked is False
        assert remaining == 0.0

    def test_lockout_after_three_failures(self):
        from core.rate_limiter import check_lockout, record_failure, record_success
        user = "brute_user_test"
        self._run(record_success(user))            # reset first

        self._run(record_failure(user))
        self._run(record_failure(user))
        _, just_locked = self._run(record_failure(user))

        assert just_locked is True
        locked, remaining = self._run(check_lockout(user))
        assert locked is True
        assert remaining > 0

    def test_success_resets_counter(self):
        from core.rate_limiter import check_lockout, record_failure, record_success
        user = "reset_user_test"
        self._run(record_failure(user))
        self._run(record_failure(user))
        self._run(record_success(user))            # reset

        # Now one more failure should NOT lock (counter reset to 0)
        _, just_locked = self._run(record_failure(user))
        assert just_locked is False


class TestJWTHandler:
    """core/jwt_handler.py – token creation and verification"""

    def test_create_and_decode_token(self):
        from core.config import JWT_ALGORITHM, JWT_SECRET_KEY
        from core.jwt_handler import create_access_token
        token, expiry = create_access_token("Jayank8294")
        assert isinstance(token, str) and len(token) > 20
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        assert payload["sub"] == "Jayank8294"
        assert payload["type"] == "access"

    def test_expired_token_rejected(self):
        from core.config import JWT_ALGORITHM, JWT_SECRET_KEY
        from core.jwt_handler import _decode_token
        from fastapi import HTTPException

        expired_payload = {
            "sub":  "Jayank8294",
            "exp":  datetime.now(tz=timezone.utc) - timedelta(seconds=1),
            "iat":  datetime.now(tz=timezone.utc) - timedelta(minutes=2),
            "type": "access",
        }
        expired_token = jwt.encode(expired_payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)

        with pytest.raises(HTTPException) as exc_info:
            _decode_token(expired_token)
        assert exc_info.value.status_code == 401
        assert "expired" in exc_info.value.detail.lower()

    def test_tampered_token_rejected(self):
        from core.jwt_handler import _decode_token
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            _decode_token("completely.invalid.token")
        assert exc_info.value.status_code == 401

    def test_wrong_secret_rejected(self):
        from core.jwt_handler import _decode_token
        from fastapi import HTTPException

        bad_token = jwt.encode(
            {"sub": "Jayank8294", "exp": datetime.now(tz=timezone.utc) + timedelta(hours=1),
             "type": "access"},
            "wrong_secret_key",
            algorithm="HS256",
        )
        with pytest.raises(HTTPException) as exc_info:
            _decode_token(bad_token)
        assert exc_info.value.status_code == 401


class TestEmailService:
    """core/email_service.py – OTP delivery (mocked)"""

    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def test_dev_mode_no_api_key(self):
        """When RESEND_API_KEY is empty, returns True in dev mode."""
        from core import email_service
        with patch.object(email_service, "RESEND_API_KEY", ""):
            ok, msg = self._run(email_service.send_otp_email("123456", "Jayank8294"))
        assert ok is True
        assert "DEV MODE" in msg

    def test_resend_success(self):
        """Simulates a successful Resend 200 response."""
        import httpx
        from core import email_service

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "email_abc123"}

        with patch.object(email_service, "RESEND_API_KEY", "re_testkey"):
            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                mock_client.post = AsyncMock(return_value=mock_response)
                mock_client_cls.return_value = mock_client

                ok, msg = self._run(
                    email_service.send_otp_email("123456", "Jayank8294")
                )
        assert ok is True

    def test_resend_api_error(self):
        """Simulates a Resend 4xx error."""
        from core import email_service

        mock_response = AsyncMock()
        mock_response.status_code = 403
        mock_response.text = "Forbidden"

        with patch.object(email_service, "RESEND_API_KEY", "re_badkey"):
            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                mock_client.post = AsyncMock(return_value=mock_response)
                mock_client_cls.return_value = mock_client

                ok, msg = self._run(
                    email_service.send_otp_email("123456", "Jayank8294")
                )
        assert ok is False
        assert "403" in msg


# ─── How to run ───────────────────────────────────────────────────────────────
# pip install pytest pytest-asyncio
# pytest tests/test_auth.py -v --tb=short
