from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


class CommandPolicyError(ValueError):
    pass


@dataclass(frozen=True)
class CommandPolicy:
    destructive: bool
    validate: Callable[[dict], dict]


def _empty(payload: dict) -> dict:
    if payload:
        raise CommandPolicyError("ping does not accept a payload")
    return {}


POLICIES: dict[str, CommandPolicy] = {
    "ping": CommandPolicy(destructive=False, validate=_empty),
}


def validate_command(action: str, payload: dict) -> tuple[CommandPolicy, dict]:
    policy = POLICIES.get(action)
    if policy is None:
        raise CommandPolicyError("action is not allowed")
    if not isinstance(payload, dict):
        raise CommandPolicyError("payload must be an object")
    return policy, policy.validate(payload)
