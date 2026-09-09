from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from hub.db.session import get_session
from hub.deps import get_license_manager, get_tenant_id
from hub.license_manager import LicenseManager
from hub.repositories import groups as groups_repo
from hub.repositories import nodes as nodes_repo
from hub.schemas import GroupCreate

router = APIRouter()


@router.get("/groups")
async def list_groups(
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
) -> dict:
    groups = await groups_repo.list_groups(session, tenant_id)
    nodes = await nodes_repo.list_for_tenant(session, tenant_id)
    counts: dict[str, int] = {}
    for n in nodes:
        counts[n.group_id] = counts.get(n.group_id, 0) + 1
    return {
        "groups": [
            {
                "slug": g.slug,
                "name": g.name,
                "description": g.description,
                "node_count": counts.get(g.id, 0),
            }
            for g in groups
        ]
    }


@router.post("/groups", status_code=201)
async def create_group(
    req: GroupCreate,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
    lm: LicenseManager = Depends(get_license_manager),
) -> dict:
    if not lm.has_feature("multi_tenancy") and lm.claims.get("tier") == "community":
        raise HTTPException(
            status_code=402,
            detail="Client grouping requires the multi_tenancy feature (paid tier).",
        )
    existing = await groups_repo.get_by_slug(
        session, tenant_id, groups_repo.slugify(req.name)
    )
    if existing:
        raise HTTPException(status_code=409, detail="Group already exists")
    group = await groups_repo.create(session, tenant_id, req.name, req.description)
    await session.commit()
    return {"slug": group.slug, "name": group.name, "description": group.description}


@router.delete("/groups/{slug}")
async def delete_group(
    slug: str,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
) -> dict:
    group = await groups_repo.get_by_slug(session, tenant_id, slug)
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    if not await groups_repo.delete(session, group):
        raise HTTPException(status_code=409, detail="Group still has nodes assigned")
    await session.commit()
    return {"deleted": slug}
