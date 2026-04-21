"""
app/db/session.py — Multi-Database Session Management
======================================================

ENGINE MATRIX
  supabase_engine   → Supabase (PostgreSQL)
                      Tables : users, holdings, otp_store
                      Used by: auth routes, holdings routes
                      Fix    : prepared_statement_name_func="" for PgBouncer

  neon_engine       → Neon (PostgreSQL)
                      Tables : sentiment_results, paper_trades (future), backtests (future)
                      Used by: sentiment_engine (write), news routes (read sentiment)
                      Note   : standard asyncpg — no PgBouncer, no fix needed

  MongoDB           → Raw news ingestion + 7-day rolling archive
                      Managed in news_service.py via Motor (not SQLAlchemy)

USAGE
  # Supabase session (auth / holdings)
  from app.db.session import get_db
  async def route(db: AsyncSession = Depends(get_db)): ...

  # Neon session (sentiment results)
  from app.db.session import get_neon_db
  async def route(db: AsyncSession = Depends(get_neon_db)): ...
"""

import logging
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.core.config import settings

logger = logging.getLogger(__name__)


# ── Shared helpers ────────────────────────────────────────────────────────────

def _normalise_pg_url(url: str) -> str:
    """
    Ensure the URL uses the postgresql+asyncpg:// scheme and strip any
    query-string parameters (they conflict with connect_args).
    """
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgresql://") and not url.startswith("postgresql+asyncpg://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url.split("?")[0]


# ── Supabase Engine — Auth + Holdings ────────────────────────────────────────
# PgBouncer sits in front of Supabase; we must disable prepared statements.

_supabase_url = _normalise_pg_url(settings.DATABASE_URL)

supabase_engine = create_async_engine(
    _supabase_url,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    connect_args={
        "statement_cache_size":       0,
        "prepared_statement_cache_size": 0,
        # Empty string disables named prepared statements — required for PgBouncer
        "prepared_statement_name_func": lambda name=None: "",
    },
)

# Backwards-compatible alias so existing imports (engine, Base) keep working
engine = supabase_engine

AsyncSessionLocal = async_sessionmaker(
    supabase_engine, class_=AsyncSession, expire_on_commit=False
)


class Base(DeclarativeBase):
    pass


async def get_db():
    """FastAPI dependency — yields a Supabase session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


# ── Neon Engine — Sentiment Results / Backtesting / Paper Trades ─────────────
# Direct asyncpg to Neon — no PgBouncer, prepared statements are fine.

_neon_engine    = None
_NeonSession    = None


def _build_neon_engine():
    global _neon_engine, _NeonSession
    if not settings.NEON_DATABASE_URL:
        logger.warning(
            "[db] NEON_DATABASE_URL not set — Neon engine disabled. "
            "Sentiment results will not be persisted to Neon."
        )
        return

    neon_url = _normalise_pg_url(settings.NEON_DATABASE_URL)

    _neon_engine = create_async_engine(
        neon_url,
        pool_pre_ping=True,
        pool_size=3,
        max_overflow=5,
        # Neon uses standard PostgreSQL — no PgBouncer quirks
        connect_args={"ssl": "require"},
    )
    _NeonSession = async_sessionmaker(
        _neon_engine, class_=AsyncSession, expire_on_commit=False
    )
    logger.info("[db] Neon engine initialised")


# Initialise eagerly at import time (settings are already loaded)
_build_neon_engine()

neon_engine = _neon_engine     # may be None if URL not configured


async def get_neon_db():
    """
    FastAPI dependency — yields a Neon session.
    Raises RuntimeError if NEON_DATABASE_URL is not configured.
    """
    if _NeonSession is None:
        raise RuntimeError(
            "Neon database is not configured. "
            "Set NEON_DATABASE_URL in your environment."
        )
    async with _NeonSession() as session:
        try:
            yield session
        finally:
            await session.close()


# ── Dispose helper (called from main.py lifespan shutdown) ───────────────────

async def dispose_all_engines() -> None:
    await supabase_engine.dispose()
    if _neon_engine is not None:
        await _neon_engine.dispose()
