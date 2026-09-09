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
    timeout_seconds: int = 60


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


_IMAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:+-]{0,254}$")


def _docker_image(payload: dict) -> dict:
    if set(payload) != {"image"}:
        raise CommandPolicyError("Docker pull requires only image")
    image = payload.get("image")
    if (
        not isinstance(image, str) or not _IMAGE.fullmatch(image)
        or ".." in image or "@" in image
    ):
        raise CommandPolicyError("invalid or digest-pinned image reference")
    return {"image": image}


POLICIES: dict[str, CommandPolicy] = {
    "ping": CommandPolicy(destructive=False, validate=_empty),
    "proxmox_start": CommandPolicy(destructive=False, validate=_proxmox_guest, timeout_seconds=120),
    "proxmox_shutdown": CommandPolicy(destructive=True, validate=_proxmox_guest, timeout_seconds=120),
    "proxmox_reboot": CommandPolicy(destructive=True, validate=_proxmox_guest, timeout_seconds=120),
    "docker_pull_image": CommandPolicy(destructive=False, validate=_docker_image, timeout_seconds=900),
}


def validate_command(action: str, payload: dict) -> tuple[CommandPolicy, dict]:
    policy = POLICIES.get(action)
    if policy is None:
        raise CommandPolicyError("action is not allowed")
    if not isinstance(payload, dict):
        raise CommandPolicyError("payload must be an object")
    return policy, policy.validate(payload)
