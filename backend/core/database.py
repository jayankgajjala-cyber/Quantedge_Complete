"""
backend/core/database.py  — v9.2 (Production-Hardened)

FIX 1: Auto-detects DATABASE_URL → PostgreSQL with NullPool.
        Falls back to SQLite + StaticPool for local dev only.
        NullPool is mandatory for Supabase pgbouncer compatibility.
        5-retry startup loop handles Supabase cold-start latency.
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
    db_url = os.environ.get("DATABASE_URL", "")
    debug  = os.environ.get("DEBUG", "false").lower() == "true"

    if db_url and ("postgres" in db_url or "postgresql" in db_url):
        # FIX 1a: Normalize Heroku/Railway-style "postgres://" prefix
        db_url = db_url.replace("postgres://", "postgresql://", 1)

        engine = create_engine(
            db_url,
            poolclass = NullPool,   # Required: pgbouncer + multi-worker safe
            echo      = debug,
        )
        logger.info("Database engine: PostgreSQL (NullPool) — cloud mode")
        return engine

    # Local dev: SQLite
    db_path = Path(os.environ.get("DB_PATH", "data/db/quantedge.db"))
    db_path.parent.mkdir(parents=True, exist_ok=True)

    if not debug and not db_url:
        logger.warning(
            "DATABASE_URL not set — using SQLite at %s. "
            "All data will be lost on Render/Railway container restarts. "
            "Set DATABASE_URL to a PostgreSQL URL before production deploy.", db_path
        )

    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=debug,
    )

    @event.listens_for(engine, "connect")
    def _pragmas(conn, _):
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()

    logger.info("Database engine: SQLite at %s — local dev mode", db_path)
    return engine


def _build_engine_with_retry(max_retries: int = 5, delay: float = 4.0):
    """Retry loop handles Supabase/Neon cold-start latency (up to 20s)."""
    for attempt in range(max_retries):
        try:
            eng = _build_engine()
            with eng.connect() as conn:
                conn.execute(text("SELECT 1"))
            logger.info("Database connection established (attempt %d/%d)", attempt + 1, max_retries)
            return eng
        except Exception as exc:
            if attempt < max_retries - 1:
                logger.warning(
                    "DB connection attempt %d/%d failed: %s — retrying in %.0fs",
                    attempt + 1, max_retries, exc, delay,
                )
                time.sleep(delay)
            else:
                logger.critical("All %d DB connection attempts failed: %s", max_retries, exc)
                raise


engine       = _build_engine_with_retry()
SessionLocal = sessionmaker(
    bind=engine, autocommit=False, autoflush=False, expire_on_commit=False
)


def _register_all_models():
    from backend.models import portfolio      # noqa: F401
    from backend.models import backtest       # noqa: F401
    from backend.models import regime         # noqa: F401
    from backend.models import signals        # noqa: F401
    from backend.models import news           # noqa: F401
    from backend.models import paper          # noqa: F401
    from backend.models import alerts         # noqa: F401
    from backend.models import market_context # noqa: F401


def init_db() -> None:
    _register_all_models()
    try:
        # Use checkfirst=True to skip already-existing types and tables
        Base.metadata.create_all(bind=engine, checkfirst=True)
        logger.info("Database initialised — all tables verified")
    except Exception as exc:
        logger.critical("Database init failed: %s", exc, exc_info=True)
        raise


def get_db() -> Generator[Session, None, None]:
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
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
