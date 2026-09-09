from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime, parseaddr
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from hub.config import Settings, get_settings
from hub.db.session import session_scope
from hub.models import IssuedLicense
from hub.repositories import billing as billing_repo

log = logging.getLogger("hub.billing.delivery")


class LicenseMailer(Protocol):
    async def send(self, license: IssuedLicense) -> None: ...


def _valid_address(value: str) -> bool:
    name, address = parseaddr(value)
    return not name and address == value and "@" in address and "\n" not in value


def build_license_message(license: IssuedLicense, settings: Settings) -> EmailMessage:
    recipient = license.delivery_email or ""
    if not _valid_address(recipient) or not _valid_address(settings.billing_email_from):
        raise ValueError("Billing email sender or recipient is invalid")
    message = EmailMessage()
    message["From"] = settings.billing_email_from
    message["To"] = recipient
    message["Subject"] = "Your Control Plane offline license"
    message["Date"] = format_datetime(datetime.now(timezone.utc))
    message["Message-ID"] = f"<{license.id}@control-plane-license>"
    if settings.billing_email_reply_to:
        if not _valid_address(settings.billing_email_reply_to):
            raise ValueError("Billing reply-to address is invalid")
        message["Reply-To"] = settings.billing_email_reply_to
    expiry = license.expires_at.isoformat() if license.expires_at else "not specified"
    message.set_content(
        "Your Control Plane offline license is ready.\n\n"
        f"Expires: {expiry}\n\n"
        "Paste this token into Settings > License on your self-hosted hub:\n\n"
        f"{license.raw_token}\n\n"
        "Treat this token as a secret and store it securely."
    )
    return message


class SmtpLicenseMailer:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def send(self, license: IssuedLicense) -> None:
        message = build_license_message(license, self.settings)
        await asyncio.to_thread(self._send_sync, message)

    def _send_sync(self, message: EmailMessage) -> None:
        cfg = self.settings
        if not cfg.billing_smtp_host:
            raise RuntimeError("Billing SMTP is not configured")
        context = ssl.create_default_context()
        if cfg.billing_smtp_ssl:
            client_context = smtplib.SMTP_SSL(
                cfg.billing_smtp_host,
                cfg.billing_smtp_port,
                timeout=cfg.billing_smtp_timeout_seconds,
                context=context,
            )
        else:
            client_context = smtplib.SMTP(
                cfg.billing_smtp_host,
                cfg.billing_smtp_port,
                timeout=cfg.billing_smtp_timeout_seconds,
            )
        with client_context as client:
            if cfg.billing_smtp_starttls and not cfg.billing_smtp_ssl:
                client.starttls(context=context)
            if cfg.billing_smtp_username:
                client.login(cfg.billing_smtp_username, cfg.billing_smtp_password)
            client.send_message(message)


def _safe_error(exc: Exception) -> str:
    # SMTP/network messages may echo credentials, addresses, or message bodies.
    return f"delivery failed ({type(exc).__name__})"


async def deliver_pending(
    session: AsyncSession,
    mailer: LicenseMailer,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> int:
    now = now or datetime.now(timezone.utc)
    rows = await billing_repo.due_license_deliveries(
        session,
        now=now,
        max_attempts=settings.billing_email_max_attempts,
    )
    for license in rows:
        license.delivery_attempts += 1
        try:
            await mailer.send(license)
        except Exception as exc:
            license.delivery_error = _safe_error(exc)
            if license.delivery_attempts >= settings.billing_email_max_attempts:
                license.delivery_status = "FAILED"
                license.next_delivery_at = None
            else:
                license.delivery_status = "RETRY"
                delay = min(60 * (2 ** (license.delivery_attempts - 1)), 3600)
                license.next_delivery_at = now + timedelta(seconds=delay)
            log.warning("license delivery %s failed: %s", license.id, license.delivery_error)
        else:
            license.delivery_status = "SENT"
            license.delivered_at = now
            license.next_delivery_at = None
            license.delivery_error = None
    await session.flush()
    return len(rows)


async def delivery_loop(stop: asyncio.Event) -> None:
    settings = get_settings()
    if not settings.billing_email_enabled:
        return
    mailer = SmtpLicenseMailer(settings)
    while not stop.is_set():
        try:
            async with session_scope() as session:
                await deliver_pending(session, mailer, settings)
        except Exception:
            log.exception("billing license delivery pass failed")
        try:
            await asyncio.wait_for(
                stop.wait(), timeout=settings.billing_email_interval_seconds
            )
        except asyncio.TimeoutError:
            pass
