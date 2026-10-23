"""Declarative metadata and UTC storage convention for MySQL DATETIME."""

from datetime import datetime, timezone

from sqlalchemy.orm import DeclarativeBase


def utc_now() -> datetime:
    """Return naive UTC for MySQL DATETIME, which has no timezone storage."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    """Shared SQLAlchemy 2 declarative base."""
