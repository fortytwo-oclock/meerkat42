from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from hub.billing.delivery import build_license_message, deliver_pending
from hub.config import Settings
from hub.db.base import Base
from hub.models import BillingSubscription, IssuedLicense, StripeWebhookEvent, Tenant


class FakeMailer:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.sent: list[str] = []

    async def send(self, license: IssuedLicense) -> None:
        self.sent.append(license.id)
        if self.error:
            raise self.error


class BillingDeliveryTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.now = datetime(2027, 1, 1, tzinfo=timezone.utc)
        self.settings = Settings(
            billing_email_from="licenses@example.test",
            billing_email_max_attempts=2,
        )

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def _seed(self) -> str:
        async with self.sessions() as session:
            tenant = Tenant(id="tenant-1", slug="tenant-1", name="Tenant One")
            event = StripeWebhookEvent(
                event_id="evt_delivery", event_type="customer.subscription.created"
            )
            subscription = BillingSubscription(
                id="billing-1",
                tenant_id=tenant.id,
                stripe_customer_id="cus_1",
                stripe_subscription_id="sub_1",
                stripe_product_id="prod_1",
                status="active",
            )
            session.add_all((tenant, event))
            await session.flush()
            session.add(subscription)
            await session.flush()
            license = IssuedLicense(
                id="license-1",
                tenant_id=tenant.id,
                billing_subscription_id=subscription.id,
                stripe_event_id=event.event_id,
                raw_token="secret-license-token",
                claims={"tier": "enterprise"},
                expires_at=self.now + timedelta(days=30),
                delivery_email="owner@example.test",
                delivery_status="PENDING",
                next_delivery_at=self.now,
            )
            session.add(license)
            await session.commit()
        return license.id

    async def test_successful_delivery_is_recorded(self) -> None:
        license_id = await self._seed()
        mailer = FakeMailer()
        async with self.sessions() as session:
            self.assertEqual(
                await deliver_pending(session, mailer, self.settings, now=self.now), 1
            )
            await session.commit()
        async with self.sessions() as session:
            license = await session.get(IssuedLicense, license_id)
            self.assertEqual(license.delivery_status, "SENT")
            self.assertEqual(license.delivery_attempts, 1)
            self.assertIsNotNone(license.delivered_at)
            self.assertEqual(mailer.sent, [license_id])

    async def test_failure_retries_then_stops_without_leaking_error(self) -> None:
        license_id = await self._seed()
        mailer = FakeMailer(RuntimeError("secret-license-token password=hunter2"))
        async with self.sessions() as session:
            await deliver_pending(session, mailer, self.settings, now=self.now)
            license = await session.get(IssuedLicense, license_id)
            self.assertEqual(license.delivery_status, "RETRY")
            self.assertNotIn("secret-license-token", license.delivery_error)
            license.next_delivery_at = self.now
            await session.commit()
        async with self.sessions() as session:
            await deliver_pending(session, mailer, self.settings, now=self.now)
            license = await session.get(IssuedLicense, license_id)
            self.assertEqual(license.delivery_status, "FAILED")
            self.assertEqual(license.delivery_attempts, 2)
            self.assertIsNone(license.next_delivery_at)

    async def test_message_has_deterministic_id_and_token(self) -> None:
        await self._seed()
        async with self.sessions() as session:
            license = await session.get(IssuedLicense, "license-1")
            message = build_license_message(license, self.settings)
            self.assertEqual(message["Message-ID"], "<license-1@control-plane-license>")
            self.assertIn("secret-license-token", message.get_content())
