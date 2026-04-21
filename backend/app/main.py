"""
app/main.py — Quantedge FastAPI Application
===========================================

STARTUP SEQUENCE
  1. Supabase  — create Auth + Holdings tables (SQLAlchemy, asyncpg + PgBouncer fix)
  2. Neon      — create Sentiment Results table (SQLAlchemy, asyncpg direct)
  3. MongoDB   — seed baseline macro signals (Motor)
  4. News loop — start 5-minute background fetch + sentiment cycle

SHUTDOWN SEQUENCE
  1. Stop news loop
  2. Dispose Supabase + Neon engines
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.db.session import supabase_engine, neon_engine, dispose_all_engines, Base
from app.db.neon_base import NeonBase
from app.api.routes import auth, holdings
from app.api.routes.global_routes import router as global_router
from app.api.routes.news_routes import router as news_router
from app.services.news_service import start_news_loop, stop_news_loop
from app.services.sentiment_engine import ensure_macro_signals
import app.models  # noqa: F401 — registers Supabase ORM models with Base

logger = logging.getLogger(__name__)


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── 1. Supabase — Auth + Holdings tables ─────────────────────────────────
    try:
        async with supabase_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("[startup] Supabase tables ready")
    except Exception as exc:
        logger.error("[startup] Supabase table creation failed: %s", exc)

    # ── 2. Neon — Sentiment Results table ────────────────────────────────────
    if neon_engine is not None:
        try:
            # Import model so NeonBase.metadata knows about the table
            import app.models.sentiment_result  # noqa: F401
            async with neon_engine.begin() as conn:
                await conn.run_sync(NeonBase.metadata.create_all)
            logger.info("[startup] Neon tables ready")
        except Exception as exc:
            logger.warning("[startup] Neon table creation failed (non-fatal): %s", exc)
    else:
        logger.warning(
            "[startup] NEON_DATABASE_URL not set — sentiment scores will not be "
            "persisted to Neon. Set NEON_DATABASE_URL to enable."
        )

    # ── 3. MongoDB — baseline macro signals ───────────────────────────────────
    try:
        await ensure_macro_signals()
        logger.info("[startup] MongoDB macro signals seeded")
    except Exception as exc:
        logger.warning("[startup] Macro signal seeding failed (non-fatal): %s", exc)

    # ── 4. News ingestion + sentiment loop ────────────────────────────────────
    start_news_loop()
    logger.info("[startup] All systems ready")

    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    stop_news_loop()
    await dispose_all_engines()
    logger.info("[shutdown] Clean shutdown complete")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Quantedge API",
    version="2.0.0",
    description=(
        "Portfolio management backend — "
        "Supabase (auth/holdings) · Neon (sentiment) · MongoDB (news archive)"
    ),
    lifespan=lifespan,
)

# ── CORS ──────────────────────────────────────────────────────────────────────

clean_origins = [o.strip().rstrip("/") for o in settings.origins_list]

app.add_middleware(
    CORSMiddleware,
    allow_origins=clean_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(auth.router)
app.include_router(holdings.router)
app.include_router(global_router)
app.include_router(news_router)


@app.get("/health", tags=["health"])
async def health():
    """
    Returns connectivity status for all three databases.
    Safe to call without auth — used by OCI health checks.
    """
    from datetime import datetime, timezone

    status: dict = {
        "status":  "ok",
        "service": "quantedge-backend",
        "time":    datetime.now(timezone.utc).isoformat(),
        "databases": {
            "supabase": "unknown",
            "neon":     "disabled" if neon_engine is None else "unknown",
            "mongodb":  "unknown",
        },
    }

    # Supabase ping
    try:
        async with supabase_engine.connect() as conn:
            await conn.execute(__import__("sqlalchemy").text("SELECT 1"))
        status["databases"]["supabase"] = "ok"
    except Exception as exc:
        status["databases"]["supabase"] = f"error: {exc}"
        status["status"] = "degraded"

    # Neon ping
    if neon_engine is not None:
        try:
            async with neon_engine.connect() as conn:
                await conn.execute(__import__("sqlalchemy").text("SELECT 1"))
            status["databases"]["neon"] = "ok"
        except Exception as exc:
            status["databases"]["neon"] = f"error: {exc}"
            status["status"] = "degraded"

    # MongoDB ping
    try:
        from motor.motor_asyncio import AsyncIOMotorClient  # type: ignore
        client = AsyncIOMotorClient(settings.MONGODB_URI, serverSelectionTimeoutMS=2000)
        await client.admin.command("ping")
        status["databases"]["mongodb"] = "ok"
    except Exception as exc:
        status["databases"]["mongodb"] = f"error: {exc}"
        status["status"] = "degraded"

    return status
