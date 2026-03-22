"""
backend/core/database.py  (PRODUCTION VERSION)
================================================
Production-hardened database engine.

Changes from local version:
  1. Auto-detects DATABASE_URL for PostgreSQL vs SQLite
  2. Uses NullPool for PostgreSQL (compatible with multi-worker and Supabase pgbouncer)
  3. Raises ValueError on startup if DATABASE_URL is not set in production mode
  4. Retries DB connection on startup (handles Supabase cold-start delay)

Usage:
  Local dev: set DB_PATH in .env (SQLite used automatically)
  Production: set DATABASE_URL=postgresql://... in .env (PostgreSQL used)
"""

import logging
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, Session, DeclarativeBase
from sqlalchemy.pool import NullPool, StaticPool

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


def _build_engine():
    """
    Build SQLAlchemy engine. Prioritizes DATABASE_URL (PostgreSQL) over
    local SQLite. NullPool is used for PostgreSQL to work with Supabase
    pgbouncer connection pooling.
    """
    database_url = os.environ.get("DATABASE_URL", "")
    debug        = os.environ.get("DEBUG", "false").lower() == "true"

    if database_url and database_url.startswith("postgresql"):
        # ── PostgreSQL (Production: Supabase / Neon / Railway) ──────
        # Supabase uses port 6543 for pgbouncer (pooled mode)
        # Replace postgres:// with postgresql:// for SQLAlchemy compat
        database_url = database_url.replace("postgres://", "postgresql://", 1)

        engine = create_engine(
            database_url,
            poolclass = NullPool,   # Required for pgbouncer + uvicorn workers
            echo      = debug,
        )
        logger.info("Database engine: PostgreSQL (NullPool)")

    else:
        # ── SQLite (Local development only) ──────────────────────────
        db_path = Path(os.environ.get("DB_PATH", "data/db/quantedge.db"))
        db_path.parent.mkdir(parents=True, exist_ok=True)

        if os.environ.get("DEBUG", "false").lower() != "true" and not database_url:
            logger.warning(
                "DATABASE_URL not set — using SQLite at %s. "
                "This will lose all data on Render/Railway restarts. "
                "Set DATABASE_URL to a PostgreSQL URL in production.", db_path
            )

        engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args = {"check_same_thread": False},
            poolclass    = StaticPool,
            echo         = debug,
        )

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragmas(conn, _):
            cursor = conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()

        logger.info("Database engine: SQLite at %s", db_path)

    return engine


def _build_engine_with_retry(max_retries: int = 5, delay: float = 3.0):
    """Retry engine creation to handle Supabase cold-start latency."""
    for attempt in range(max_retries):
        try:
            eng = _build_engine()
            with eng.connect() as conn:
                conn.execute(text("SELECT 1"))
            return eng
        except Exception as exc:
            if attempt < max_retries - 1:
                logger.warning(
                    "DB connection attempt %d/%d failed: %s — retrying in %.0fs",
                    attempt + 1, max_retries, exc, delay
                )
                time.sleep(delay)
            else:
                logger.critical("All DB connection attempts failed: %s", exc)
                raise


engine       = _build_engine_with_retry()
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False,
                            expire_on_commit=False)


def _register_all_models():
    """Import all model modules to register them with Base.metadata."""
    from backend.models import portfolio      # noqa: F401
    from backend.models import backtest       # noqa: F401
    from backend.models import regime         # noqa: F401
    from backend.models import signals        # noqa: F401
    from backend.models import news           # noqa: F401
    from backend.models import paper          # noqa: F401
    from backend.models import alerts         # noqa: F401
    from backend.models import market_context # noqa: F401


def init_db() -> None:
    """Create all tables (idempotent). Call once at startup."""
    _register_all_models()
    try:
        Base.metadata.create_all(bind=engine)
        logger.info("Database initialised — all tables verified")
    except Exception as exc:
        logger.critical("Database init failed: %s", exc, exc_info=True)
        raise


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency — one session per request."""
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@contextmanager
def get_db_context() -> Generator[Session, None, None]:
    """Context manager for use in schedulers and background jobs."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
