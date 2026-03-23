#!/bin/sh
# Railway sets $PORT at runtime. This script expands it properly
# before passing it to uvicorn. TOML startCommand does not expand
# shell variables — this wrapper script fixes that.
exec uvicorn backend.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-8000}" \
  --workers 1 \
  --log-level info
