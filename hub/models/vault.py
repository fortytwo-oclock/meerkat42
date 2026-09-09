from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from hub.db.base import Base, TZDateTime, created_at_col, uuid_pk


class ConfigBackup(Base):
    """An encrypted point-in-time capture of a node's compose / env files.

    The hub stores ciphertext only. Files are AES-256-GCM encrypted by the agent
    with a key the hub never sees; decryption happens offline with
    ``scripts/vault_decrypt.py``.
    """

    __tablename__ = "config_backups"

    id: Mapped[str] = uuid_pk()
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    node_id: Mapped[str] = mapped_column(
        ForeignKey("nodes.node_id", ondelete="CASCADE"), index=True
    )
    command_id: Mapped[str] = mapped_column(String(64), index=True)
    requested_by: Mapped[str] = mapped_column(String(200), default="operator")

    status: Mapped[str] = mapped_column(String(16), default="PENDING")  # PENDING|COMPLETE|ERROR
    error: Mapped[Optional[str]] = mapped_column(String(500), default=None)
    file_count: Mapped[int] = mapped_column(Integer, default=0)
    total_bytes: Mapped[int] = mapped_column(Integer, default=0)
    key_fingerprint: Mapped[Optional[str]] = mapped_column(String(64), default=None)

    # [{path, nonce, ciphertext, sha256, size}] — ciphertext is base64, opaque to the hub
    files: Mapped[list] = mapped_column(default=list)

    requested_at: Mapped[datetime] = created_at_col()
    completed_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, default=None)
