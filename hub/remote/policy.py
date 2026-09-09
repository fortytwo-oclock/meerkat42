from __future__ import annotations

from dataclasses import dataclass
import re
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


_NODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def _proxmox_guest(payload: dict) -> dict:
    if set(payload) != {"node", "guest_type", "vmid"}:
        raise CommandPolicyError("Proxmox actions require only node, guest_type, and vmid")
    node = payload.get("node")
    guest_type = payload.get("guest_type")
    vmid = payload.get("vmid")
    if not isinstance(node, str) or not _NODE.fullmatch(node):
        raise CommandPolicyError("invalid Proxmox node")
    if guest_type not in {"qemu", "lxc"}:
        raise CommandPolicyError("guest_type must be qemu or lxc")
    if isinstance(vmid, bool) or not isinstance(vmid, int) or not 100 <= vmid <= 999999999:
        raise CommandPolicyError("vmid must be an integer from 100 to 999999999")
    return {"node": node, "guest_type": guest_type, "vmid": vmid}


POLICIES: dict[str, CommandPolicy] = {
    "ping": CommandPolicy(destructive=False, validate=_empty),
    "proxmox_start": CommandPolicy(destructive=False, validate=_proxmox_guest),
    "proxmox_shutdown": CommandPolicy(destructive=True, validate=_proxmox_guest),
    "proxmox_reboot": CommandPolicy(destructive=True, validate=_proxmox_guest),
}


def validate_command(action: str, payload: dict) -> tuple[CommandPolicy, dict]:
    policy = POLICIES.get(action)
    if policy is None:
        raise CommandPolicyError("action is not allowed")
    if not isinstance(payload, dict):
        raise CommandPolicyError("payload must be an object")
    return policy, policy.validate(payload)
