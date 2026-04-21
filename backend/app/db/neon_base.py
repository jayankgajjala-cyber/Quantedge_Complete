"""
app/db/neon_base.py — Declarative Base for Neon (PostgreSQL)
============================================================

Kept separate from app/db/session.py Base (Supabase) so that
`Base.metadata.create_all` for Supabase never touches Neon tables,
and vice-versa.
"""

from sqlalchemy.orm import DeclarativeBase


class NeonBase(DeclarativeBase):
    pass
