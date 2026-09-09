from __future__ import annotations

import hmac
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from hub.config import Settings
from hub.repositories import agent_tokens as token_repo


@dataclass(frozen=True)
class AgentIdentity:
    tenant_id: str
    group_slug: str | None
    label: str


async def authenticate_agent(
    session: AsyncSession,
    *,
    presented: str,
    default_tenant_id: str,
    settings: Settings,
) -> AgentIdentity | None:
    # Legacy/self-hosted shared credential remains scoped to the default tenant.
    if settings.agent_bootstrap_token and presented and hmac.compare_digest(
        presented, settings.agent_bootstrap_token
    ):
        return AgentIdentity(default_tenant_id, None, "bootstrap-token")

    if presented:
        match = await token_repo.authenticate(session, presented)
        if match is not None:
            token, group = match
            return AgentIdentity(token.tenant_id, group.slug, token.name)

    # Preserve zero-config local mode only while no credential mechanism exists.
    if not settings.agent_bootstrap_token and not await token_repo.any_active(session):
        return AgentIdentity(default_tenant_id, None, "anonymous-agent")
    return None
