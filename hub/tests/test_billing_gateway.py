from __future__ import annotations

import unittest
from urllib.parse import parse_qs

import httpx

from hub.billing.gateway import StripeAPIError, StripeGateway


class StripeGatewayTest(unittest.IsolatedAsyncioTestCase):
    async def test_checkout_uses_subscription_metadata_and_idempotency(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/v1/checkout/sessions")
            self.assertEqual(request.headers["Authorization"], "Bearer sk_test_secret")
            self.assertEqual(request.headers["Idempotency-Key"], "checkout-attempt-1")
            form = parse_qs(request.content.decode())
            self.assertEqual(form["mode"], ["subscription"])
            self.assertEqual(form["line_items[0][price]"], ["price_enterprise"])
            self.assertEqual(
                form["subscription_data[metadata][tenant_slug]"], ["example-msp"]
            )
            self.assertEqual(form["customer_email"], ["owner@example.test"])
            return httpx.Response(
                200,
                json={
                    "id": "cs_test_example",
                    "url": "https://checkout.stripe.com/c/pay/example",
                },
            )

        gateway = StripeGateway(
            "sk_test_secret",
            transport=httpx.MockTransport(handler),
        )
        result = await gateway.create_checkout_session(
            price_id="price_enterprise",
            tenant_id="tenant-1",
            tenant_slug="example-msp",
            customer_name="Example MSP",
            customer_email="owner@example.test",
            stripe_customer_id=None,
            success_url="https://monitor.example/success",
            cancel_url="https://monitor.example/cancel",
            idempotency_key="checkout-attempt-1",
        )
        self.assertEqual(result["id"], "cs_test_example")

    async def test_portal_uses_existing_customer(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            form = parse_qs(request.content.decode())
            self.assertEqual(request.url.path, "/v1/billing_portal/sessions")
            self.assertEqual(form["customer"], ["cus_example"])
            return httpx.Response(
                200,
                json={"url": "https://billing.stripe.com/p/session/example"},
            )

        gateway = StripeGateway(
            "sk_test_secret", transport=httpx.MockTransport(handler)
        )
        result = await gateway.create_portal_session(
            customer_id="cus_example",
            return_url="https://monitor.example/settings",
            idempotency_key="portal-attempt-1",
        )
        self.assertTrue(result["url"].startswith("https://billing.stripe.com/"))

    async def test_upstream_error_does_not_leak_response_body_or_secret(self) -> None:
        async def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                400,
                headers={"Request-Id": "req_safe"},
                text="card and customer details must not leak",
            )

        gateway = StripeGateway(
            "sk_test_super_secret", transport=httpx.MockTransport(handler)
        )
        with self.assertRaises(StripeAPIError) as caught:
            await gateway.create_portal_session(
                customer_id="cus_example",
                return_url="https://monitor.example/settings",
                idempotency_key="portal-attempt-2",
            )
        message = str(caught.exception)
        self.assertIn("req_safe", message)
        self.assertNotIn("card and customer", message)
        self.assertNotIn("sk_test_super_secret", message)

    async def test_network_failure_is_normalized(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("sensitive network detail", request=request)

        gateway = StripeGateway(
            "sk_test_super_secret", transport=httpx.MockTransport(handler)
        )
        with self.assertRaises(StripeAPIError) as caught:
            await gateway.create_portal_session(
                customer_id="cus_example",
                return_url="https://monitor.example/settings",
                idempotency_key="portal-attempt-3",
            )
        self.assertEqual(caught.exception.status_code, 502)
        self.assertNotIn("sensitive network detail", str(caught.exception))
