from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from hub.config import get_settings
from hub.db.session import get_session
from hub.deps import Principal, get_tenant_id, get_tenant_license_manager, require_admin
from hub.license_manager import LicenseManager
from hub.repositories import nodes as nodes_repo
from hub.repositories import vault as vault_repo
from hub.ws.bus import send_command

router = APIRouter()
_settings = get_settings()


def _meta(b) -> dict:
    return {
        "id": b.id,
        "node_id": b.node_id,
        "status": b.status,
        "error": b.error,
        "file_count": b.file_count,
        "total_bytes": b.total_bytes,
        "key_fingerprint": b.key_fingerprint,
        "requested_by": b.requested_by,
        "requested_at": b.requested_at,
        "completed_at": b.completed_at,
    }


def _require_vault(lm: LicenseManager) -> None:
    if not _settings.vault_enabled:
        raise HTTPException(status_code=404, detail="vault disabled")
    if not lm.has_feature("vault_backup"):
        raise HTTPException(
            status_code=402,
            detail="Infrastructure Vault requires the vault_backup license feature.",
        )


@router.post("/nodes/{node_id}/backup", status_code=202)
async def request_backup(
    node_id: str,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
    principal: Principal = Depends(require_admin),
    lm: LicenseManager = Depends(get_tenant_license_manager),
) -> dict:
    _require_vault(lm)
    node = await nodes_repo.get_for_tenant(session, tenant_id, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")

    # Persist the correlation row before sending. A small backup can finish on
    # the agent before send_command returns, and ingest must already be able to
    # resolve its command_id.
    command_id = str(uuid.uuid4())
    backup = await vault_repo.create_pending(
        session,
        tenant_id=tenant_id,
        node_id=node_id,
        command_id=command_id,
        requested_by=principal.label,
    )
    await session.commit()

    try:
        delivered = await send_command(
            node_id,
            "request_config_backup",
            {"timeout_sec": _settings.vault_backup_timeout_seconds},
            tenant_id=tenant_id,
            command_id=command_id,
        )
    except Exception:
        delivered = None
    if delivered is None:
        await vault_repo.complete(
            session,
            command_id,
            files=[],
            key_fingerprint=None,
            error="Node has no active connection",
        )
        await session.commit()
        raise HTTPException(status_code=409, detail="Node has no active connection")
    return {"backup_id": backup.id, "command_id": command_id, "status": "PENDING"}


@router.get("/nodes/{node_id}/backups")
async def list_backups(
    node_id: str,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
    _: Principal = Depends(require_admin),
    lm: LicenseManager = Depends(get_tenant_license_manager),
) -> dict:
    _require_vault(lm)
    rows = await vault_repo.list_for_node(session, tenant_id, node_id)
    return {"backups": [_meta(b) for b in rows]}


@router.get("/backups/{backup_id}")
async def get_backup(
    backup_id: str,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
    _: Principal = Depends(require_admin),
    lm: LicenseManager = Depends(get_tenant_license_manager),
) -> dict:
    _require_vault(lm)
    b = await vault_repo.get_for_tenant(session, tenant_id, backup_id)
    if not b:
        raise HTTPException(status_code=404, detail="Backup not found")
    if b.status != "COMPLETE":
        return {**_meta(b), "files": []}
    # The ciphertext is opaque to the hub; the operator decrypts offline with the
    # vault key (scripts/vault_decrypt.py).
    return {**_meta(b), "files": b.files}
