"""
backend/core/config.py  — v9.2 (Production-Hardened)

FIX 2: JWT_SECRET_KEY no longer auto-generates. Must be set as env var.
        ValueError raised at startup if missing in production (DEBUG=false).
FIX 5: CORS driven by FRONTEND_URL (not wildcard).
FIX 6: PARQUET_CACHE_DIR defaults to /tmp/parquet (writable on all platforms).
NEW:   DATABASE_URL, SCRAPERAPI_KEY added.
"""

from functools import lru_cache
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8",
        case_sensitive=False, extra="ignore",
    )

    # Application
    APP_NAME:    str  = "Quantedge Trading System"
    APP_VERSION: str  = "9.2.0"
    DEBUG:       bool = False

    # FIX 1 — Database: DATABASE_URL drives PostgreSQL in prod; blank = SQLite local dev
    DATABASE_URL: Optional[str] = None
    DB_PATH:      str           = "data/db/quantedge.db"

    # FIX 6 — Parquet: /tmp/parquet is always writable on Render/Railway/Fly
    PARQUET_CACHE_DIR: str = "/tmp/parquet"

    # FIX 2 — JWT: no default; validated at startup
    JWT_SECRET_KEY:     str = ""
    JWT_ALGORITHM:      str = "HS256"
    JWT_EXPIRE_MINUTES: int = 60

    # Auth
    AUTH_USERNAME: str = "Jayank8294"
    AUTH_PASSWORD: str = "Jayanju@9498"

    # OTP & Resend
    OTP_RECIPIENT_EMAIL: str = "jayankgajjala@gmail.com"
    OTP_SENDER_EMAIL:    str = "noreply@yourdomain.com"
    OTP_EXPIRE_SECONDS:  int = 300
    OTP_LENGTH:          int = 6
    RESEND_API_KEY:      str = ""

    # Gmail SMTP
    ALERT_EMAIL_TO:     str = "jayankgajjala@gmail.com"
    ALERT_EMAIL_FROM:   str = ""
    GMAIL_APP_PASSWORD: str = ""
    SMTP_HOST:          str = "smtp.gmail.com"
    SMTP_PORT:          int = 587

    # Brute-force
    MAX_FAILED_ATTEMPTS: int   = 3
    LOCKOUT_SECONDS:     float = 30.0

    # HuggingFace
    HF_API_KEY:       str = ""
    FINBERT_MODEL:    str = "ProsusAI/finbert"
    SUMMARIZER_MODEL: str = "facebook/bart-large-cnn"
    HF_INFERENCE_URL: str = "https://api-inference.huggingface.co/models"

    # News
    NEWS_API_KEY:        str = ""
    NEWS_CACHE_TTL_SECS: int = 3600
    NEWS_MAX_ARTICLES:   int = 10

    # FIX 3 — ScraperAPI: proxy for Cloudflare-protected sites
    SCRAPERAPI_KEY: str = ""

    # Sentiment thresholds
    SENTIMENT_NEGATIVE_THRESHOLD: float = -0.6
    SENTIMENT_POSITIVE_THRESHOLD: float =  0.6
    SENTIMENT_CONFLICT_THRESHOLD: float =  1.0

    # Data quality
    MIN_YEARS_SUFFICIENT: int = 10
    MIN_YEARS_CONFIDENCE: int = 2

    # Backtest & signals
    INITIAL_CAPITAL:           float = 100_000.0
    RISK_FREE_RATE:            float = 0.065
    TRADING_DAYS_PER_YEAR:     int   = 252
    HIGH_CONFIDENCE_THRESHOLD: float = 75.0
    COMMISSION_PCT:            float = 0.001

    # Budget
    MONTHLY_BUDGET_INR:   float = 15_000.0
    MAX_SINGLE_TRADE_PCT: float = 0.40
    MIN_SINGLE_TRADE_PCT: float = 0.10

    # FIX 4 — Scheduler: cron fields now in IST (scheduler TZ = Asia/Kolkata)
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

    # FIX 5 — CORS: driven by FRONTEND_URL, not wildcard
    FRONTEND_URL:      str = "http://localhost:3000"
    FRONTEND_BASE_URL: str = "http://localhost:3000"

    # Macro thresholds
    DXY_RISK_THRESHOLD:    float = 106.0
    US10Y_RISK_THRESHOLD:  float = 5.0
    BRENT_RISK_THRESHOLD:  float = 95.0
    TV_CONFIDENCE_PENALTY: float = 5.0
    TV_CONFIRM_BONUS:      float = 12.0
    MC_CONFIRM_BONUS:      float = 8.0


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = Settings()
    # FIX 2: Hard-fail if JWT secret missing in production
    if not s.DEBUG:
        if not s.JWT_SECRET_KEY:
            raise ValueError(
                "JWT_SECRET_KEY is not set. "
                "Generate: python -c \"import secrets; print(secrets.token_hex(32))\" "
                "then add to .env or your hosting platform environment variables."
            )
        if len(s.JWT_SECRET_KEY) < 32:
            raise ValueError(
                f"JWT_SECRET_KEY too short ({len(s.JWT_SECRET_KEY)} chars). Minimum 32 required."
            )
    return s


settings = get_settings()
