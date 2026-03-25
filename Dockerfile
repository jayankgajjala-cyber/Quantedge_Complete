# ═══════════════════════════════════════════════════════════════════
#  Quantedge Trading System — Production Dockerfile
#  FastAPI backend — Modules 1-8
#  
#  Build:  docker build -t quantedge-backend .
#  Run:    docker run --env-file .env.production -p 8000:8000 quantedge-backend
# ═══════════════════════════════════════════════════════════════════

FROM python:3.11-slim

# ── System packages (required for lxml, pyarrow, psycopg2) ───────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libpq-dev \
    libxml2-dev \
    libxslt1-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# ── Working directory ─────────────────────────────────────────────────
WORKDIR /app

# ── Install Python dependencies ───────────────────────────────────────
# Copy requirements first for better layer caching
COPY backend/requirements.txt ./requirements.txt

RUN pip install --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt

# ── Copy application code ─────────────────────────────────────────────
COPY backend/ ./backend/

# ── Create necessary runtime directories ─────────────────────────────
# /tmp is always writable on ephemeral containers
RUN mkdir -p /tmp/parquet /tmp/yf_cache logs

# ── Non-root user for security ────────────────────────────────────────
RUN useradd -m -r appuser && chown -R appuser:appuser /app /tmp/parquet /tmp/yf_cache logs
USER appuser

# ── Health check ──────────────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# ── Expose port ───────────────────────────────────────────────────────
EXPOSE 8000

# ── Start command ─────────────────────────────────────────────────────
# --workers 1 required for APScheduler (single-process) + SQLite compat
# For PostgreSQL (production): increase to 2-4 workers with --preload-app
CMD ["uvicorn", "backend.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--log-level", "info", \
     "--access-log"]
