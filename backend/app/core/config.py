from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    # ── JWT ───────────────────────────────────────────────────────────────────
    SECRET_KEY:                  str
    ALGORITHM:                   str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    # ── Resend (OTP email) ────────────────────────────────────────────────────
    RESEND_API_KEY: str
    OTP_FROM_EMAIL: str

    # ── Supabase (PostgreSQL) — Auth + Holdings ───────────────────────────────
    DATABASE_URL:      str          # asyncpg connection string for Supabase
    SUPABASE_URL:      str
    SUPABASE_ANON_KEY: str

    # ── Neon (PostgreSQL) — Sentiment Results, Backtesting, Paper Trades ──────
    # Format: postgresql+asyncpg://user:password@host/dbname?sslmode=require
    NEON_DATABASE_URL: str = ""

    # ── MongoDB — Raw News Ingestion + 7-day Archive ──────────────────────────
    MONGODB_URI: str = "mongodb://localhost:27017"

    # ── External API keys ─────────────────────────────────────────────────────
    FINNHUB_API_KEY: str = ""
    GNEWS_API_KEY:   str = ""

    # ── FinBERT / HuggingFace — OCI ARM Free Tier safe defaults ──────────────
    # Set TORCH_DEVICE=cpu explicitly; HF_HOME keeps model cache off root disk
    TORCH_DEVICE: str = "cpu"
    HF_HOME:      str = "/tmp/huggingface"

    # ── CORS ──────────────────────────────────────────────────────────────────
    ALLOWED_ORIGINS: str = "http://localhost:3000"

    @property
    def origins_list(self) -> List[str]:
        return [o.strip().rstrip("/") for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    class Config:
        env_file = ".env"
        extra    = "ignore"


settings = Settings()
