"""
backend/core/config.py

Startup-safe configuration. The app must start without any env vars set
so developers can run it immediately after cloning. Sensitive defaults
are used for local dev; production deployments should override them.
"""

import secrets
import logging
from functools import lru_cache
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8",
        case_sensitive=False, extra="ignore",
    )

    # ── Application ───────────────────────────────────────────────────────────
    APP_NAME:    str  = "Quantedge Trading System"
    APP_VERSION: str  = "9.2.0"
    DEBUG:       bool = False

    # ── Database ──────────────────────────────────────────────────────────────
    # Set DATABASE_URL to a PostgreSQL URL for production.
    # Blank = SQLite at DB_PATH (fine for local dev, data lost on container restart).
    DATABASE_URL: Optional[str] = None
    DB_PATH:      str           = "data/db/quantedge.db"

    # ── Parquet cache ─────────────────────────────────────────────────────────
    # /tmp/parquet is writable on Render/Railway/Fly/Docker without config.
    PARQUET_CACHE_DIR: str = "/tmp/parquet"

    # ── JWT ───────────────────────────────────────────────────────────────────
    # Leave blank → a random key is generated at startup (safe for single-instance).
    # Set a fixed value in production so tokens survive restarts.
    JWT_SECRET_KEY:     str = ""
    JWT_ALGORITHM:      str = "HS256"
    JWT_EXPIRE_MINUTES: int = 60

    # ── Auth ──────────────────────────────────────────────────────────────────
    AUTH_USERNAME: str = "Jayank8294"
    AUTH_PASSWORD: str = "Jayanju@9498"

    # ── OTP / Email ───────────────────────────────────────────────────────────
    # RESEND_API_KEY blank → OTP is printed to server logs (dev mode).
    OTP_RECIPIENT_EMAIL: str = "jayankgajjala@gmail.com"
    OTP_SENDER_EMAIL:    str = "noreply@yourdomain.com"
    OTP_EXPIRE_SECONDS:  int = 300
    OTP_LENGTH:          int = 6
    RESEND_API_KEY:      str = ""

    # ── Gmail SMTP ────────────────────────────────────────────────────────────
    ALERT_EMAIL_TO:     str = "jayankgajjala@gmail.com"
    ALERT_EMAIL_FROM:   str = ""
    GMAIL_APP_PASSWORD: str = ""
    SMTP_HOST:          str = "smtp.gmail.com"
    SMTP_PORT:          int = 587

    # ── Brute-force protection ────────────────────────────────────────────────
    MAX_FAILED_ATTEMPTS: int   = 3
    LOCKOUT_SECONDS:     float = 30.0

    # ── HuggingFace ───────────────────────────────────────────────────────────
    HF_API_KEY:       str = ""
    FINBERT_MODEL:    str = "ProsusAI/finbert"
    SUMMARIZER_MODEL: str = "facebook/bart-large-cnn"
    HF_INFERENCE_URL: str = "https://api-inference.huggingface.co/models"

    # ── News ──────────────────────────────────────────────────────────────────
    NEWS_API_KEY:        str = ""
    NEWS_CACHE_TTL_SECS: int = 3600
    NEWS_MAX_ARTICLES:   int = 10

    # ── ScraperAPI ────────────────────────────────────────────────────────────
    SCRAPERAPI_KEY: str = ""

    # ── Sentiment thresholds ──────────────────────────────────────────────────
    SENTIMENT_NEGATIVE_THRESHOLD: float = -0.6
    SENTIMENT_POSITIVE_THRESHOLD: float =  0.6
    SENTIMENT_CONFLICT_THRESHOLD: float =  1.0

    # ── Data quality ──────────────────────────────────────────────────────────
    MIN_YEARS_SUFFICIENT: int = 10
    MIN_YEARS_CONFIDENCE: int = 2

    # ── Backtest & signals ────────────────────────────────────────────────────
    INITIAL_CAPITAL:           float = 100_000.0
    RISK_FREE_RATE:            float = 0.065
    TRADING_DAYS_PER_YEAR:     int   = 252
    HIGH_CONFIDENCE_THRESHOLD: float = 75.0
    COMMISSION_PCT:            float = 0.001

    # ── Budget ────────────────────────────────────────────────────────────────
    MONTHLY_BUDGET_INR:   float = 15_000.0
    MAX_SINGLE_TRADE_PCT: float = 0.40
    MIN_SINGLE_TRADE_PCT: float = 0.10

    # ── Scheduler ────────────────────────────────────────────────────────────
    HEARTBEAT_INTERVAL_SECS:    int   = 300
    ALERT_CONFIDENCE_THRESHOLD: float = 85.0
    MAX_ALERTS_PER_DAY:         int   = 3
    WEEKLY_BACKTEST_DAY:        int   = 5
    WEEKLY_BACKTEST_HOUR_IST:   int   = 6
    WEEKLY_BACKTEST_MINUTE_IST: int   = 30
    WEEKLY_REPORT_DAY:          int   = 5
    WEEKLY_REPORT_HOUR_IST:     int   = 23
    WEEKLY_REPORT_MINUTE_IST:   int   = 30
    MARKET_OPEN_HOUR_IST:       int   = 9
    MARKET_OPEN_MIN_IST:        int   = 15
    MARKET_CLOSE_HOUR_IST:      int   = 15
    MARKET_CLOSE_MIN_IST:       int   = 30

    # ── CORS / Frontend ───────────────────────────────────────────────────────
    # Default "*" means the app works immediately without any env var setup.
    # Restrict in production by setting CORS_ORIGINS=https://your-frontend.vercel.app
    FRONTEND_URL:      str = "http://localhost:3000"
    FRONTEND_BASE_URL: str = "http://localhost:3000"

    # ── Macro thresholds ──────────────────────────────────────────────────────
    DXY_RISK_THRESHOLD:    float = 106.0
    US10Y_RISK_THRESHOLD:  float = 5.0
    BRENT_RISK_THRESHOLD:  float = 95.0
    TV_CONFIDENCE_PENALTY: float = 5.0
    TV_CONFIRM_BONUS:      float = 12.0
    MC_CONFIRM_BONUS:      float = 8.0


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = Settings()

    # Auto-generate JWT secret if not set instead of crashing.
    # A generated key is safe for single-instance deployments — tokens are
    # invalidated on restart, which just means users log in again.
    # For persistent tokens across restarts, set JWT_SECRET_KEY in env vars.
    if not s.JWT_SECRET_KEY:
        generated = secrets.token_hex(32)
        object.__setattr__(s, "JWT_SECRET_KEY", generated)
        logger.warning(
            "JWT_SECRET_KEY not set — generated a random key for this session. "
            "Users will need to log in again after each restart. "
            "Set JWT_SECRET_KEY env var to a fixed value to avoid this."
        )

    return s


settings = get_settings()
