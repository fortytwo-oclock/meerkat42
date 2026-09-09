"""Declarative base and portable column helpers (SQLite <-> PostgreSQL)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, String, types
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# JSON that becomes JSONB on Postgres, TEXT-backed JSON on SQLite.
JSONVariant = types.JSON().with_variant(JSONB(), "postgresql")

# Timezone-aware timestamp, portable.
TZDateTime = DateTime(timezone=True)


class Base(DeclarativeBase):
    type_annotation_map = {
        dict: JSONVariant,
        list: JSONVariant,
    }


def uuid_pk():
    return mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )


def created_at_col():
    return mapped_column(TZDateTime, default=utcnow, nullable=False)
