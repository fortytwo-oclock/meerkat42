"""Channel sender abstraction."""
from __future__ import annotations

import abc
import asyncio
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

import httpx

from hub.config import get_settings

_settings = get_settings()


class ChannelError(RuntimeError):
    pass


@dataclass(frozen=True)
class AlertPayload:
    event_id: str
    node_id: str
    hostname: str
    client_group: str
    severity: str
    type: str
    subject: str
    message: str
    state: str  # "FIRING" | "RESOLVED"
    started_at: str

    @property
    def title(self) -> str:
        verb = "RESOLVED" if self.state == "RESOLVED" else self.severity
        tail = f" · {self.subject}" if self.subject else ""
        return f"[{verb}] {self.type}{tail} on {self.hostname}"

    def as_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "node_id": self.node_id,
            "hostname": self.hostname,
            "client_group": self.client_group,
            "severity": self.severity,
            "type": self.type,
            "subject": self.subject,
            "message": self.message,
            "state": self.state,
            "started_at": self.started_at,
        }


_COLOR = {
    "CRITICAL": 0xE01E37,
    "HIGH": 0xF48C06,
    "WARNING": 0xFFD166,
    "INFO": 0x4CC9F0,
    "RESOLVED": 0x2EC4B6,
}


class Sender(abc.ABC):
    def __init__(self, config: dict):
        self.config = config

    @abc.abstractmethod
    async def send(self, payload: AlertPayload) -> None:
        ...


class WebhookSender(Sender):
    """Generic JSON POST — PagerDuty Events API, ticketing systems, custom sinks."""

    async def send(self, payload: AlertPayload) -> None:
        url = self.config.get("url")
        if not url:
            raise ChannelError("webhook channel missing 'url'")
        body = {"alert": payload.as_dict(), "title": payload.title}
        if extra := self.config.get("extra"):
            body.update(extra)
        await _post(url, json=body, headers=self.config.get("headers"), kind="webhook")


class DiscordSender(Sender):
    async def send(self, payload: AlertPayload) -> None:
        url = self.config.get("url")
        if not url:
            raise ChannelError("discord channel missing 'url'")
        key = "RESOLVED" if payload.state == "RESOLVED" else payload.severity
        await _post(
            url,
            json={
                "embeds": [
                    {
                        "title": payload.title,
                        "description": payload.message,
                        "color": _COLOR.get(key, 0x999999),
                        "fields": [
                            {"name": "Node", "value": payload.hostname, "inline": True},
                            {"name": "Client", "value": payload.client_group, "inline": True},
                        ],
                        "timestamp": payload.started_at,
                    }
                ]
            },
            kind="discord",
        )


class SlackSender(Sender):
    async def send(self, payload: AlertPayload) -> None:
        url = self.config.get("url")
        if not url:
            raise ChannelError("slack channel missing 'url'")
        emoji = ":white_check_mark:" if payload.state == "RESOLVED" else ":rotating_light:"
        await _post(
            url,
            json={
                "text": f"{emoji} *{payload.title}*",
                "blocks": [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": (
                                f"{emoji} *{payload.title}*\n{payload.message}\n"
                                f"_{payload.client_group} · {payload.node_id}_"
                            ),
                        },
                    }
                ],
            },
            kind="slack",
        )


class SmtpSender(Sender):
    async def send(self, payload: AlertPayload) -> None:
        c = self.config
        required = ("host", "from", "to")
        missing = [k for k in required if not c.get(k)]
        if missing:
            raise ChannelError(f"smtp channel missing {missing}")

        msg = EmailMessage()
        msg["Subject"] = payload.title
        msg["From"] = c["from"]
        recipients = c["to"] if isinstance(c["to"], list) else [c["to"]]
        msg["To"] = ", ".join(recipients)
        msg.set_content(
            f"{payload.message}\n\n"
            f"Node:     {payload.hostname} ({payload.node_id})\n"
            f"Client:   {payload.client_group}\n"
            f"Severity: {payload.severity}\n"
            f"State:    {payload.state}\n"
            f"Since:    {payload.started_at}\n"
        )
        await asyncio.to_thread(self._send_sync, c, msg, recipients)

    @staticmethod
    def _send_sync(c: dict, msg: EmailMessage, recipients: list[str]) -> None:
        port = int(c.get("port", 587))
        timeout = _settings.dispatch_http_timeout_seconds
        try:
            if c.get("ssl"):
                srv: smtplib.SMTP = smtplib.SMTP_SSL(c["host"], port, timeout=timeout)
            else:
                srv = smtplib.SMTP(c["host"], port, timeout=timeout)
                if c.get("starttls", True):
                    srv.starttls()
            with srv:
                if c.get("username"):
                    srv.login(c["username"], c.get("password", ""))
                srv.send_message(msg, c["from"], recipients)
        except OSError as exc:
            raise ChannelError(f"smtp send failed: {exc}") from exc


_SENDERS: dict[str, type[Sender]] = {
    "webhook": WebhookSender,
    "discord": DiscordSender,
    "slack": SlackSender,
    "smtp": SmtpSender,
}


def build_sender(kind: str, config: dict) -> Sender:
    cls = _SENDERS.get(kind)
    if cls is None:
        raise ChannelError(f"unknown channel kind '{kind}'")
    return cls(config)


async def _post(
    url: str, *, json: dict, headers: dict | None = None, kind: str = "webhook"
) -> None:
    # Re-validate right before the request — DNS may have changed since create.
    from hub.notify.urlguard import validate_webhook_url

    await asyncio.to_thread(validate_webhook_url, url, kind)
    try:
        async with httpx.AsyncClient(
            timeout=_settings.dispatch_http_timeout_seconds, follow_redirects=False
        ) as client:
            resp = await client.post(url, json=json, headers=headers or {})
        if resp.status_code >= 300:
            raise ChannelError(f"HTTP {resp.status_code}")
    except httpx.HTTPError as exc:
        raise ChannelError(f"{type(exc).__name__}") from exc
