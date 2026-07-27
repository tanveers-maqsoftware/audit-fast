"""Declarative base for all ORM models.

Kept in its own module so Alembic can import the metadata without pulling in the
session engine (which would try to connect at import time).
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class every ORM model inherits from. ``Base.metadata`` drives migrations."""
