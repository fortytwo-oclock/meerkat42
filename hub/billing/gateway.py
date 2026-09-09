from __future__ import annotations

from typing import Any

import httpx


class StripeAPIError(RuntimeError):
    def __init__(self, status_code: int, request_id: str | None = None):
        self.status_code = status_code
        self.request_id = request_id
        suffix = f" (request {request_id})" if request_id else ""
        super().__init__(f"Stripe API request failed with HTTP {status_code}{suffix}")


class StripeGateway:
    def __init__(
        self,
        secret_key: str,
        *,
        base_url: str = "https://api.stripe.com/v1",
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.secret_key = secret_key
        self.base_url = base_url.rstrip("/")
        self.transport = transport

    async def _post(
        self, path: str, data: dict[str, str], idempotency_key: str
    ) -> dict[str, Any]:
        if not self.secret_key:
            raise StripeAPIError(503)
        headers = {
            "Authorization": f"Bearer {self.secret_key}",
            "Idempotency-Key": idempotency_key,
        }
        try:
            async with httpx.AsyncClient(
                timeout=15.0, transport=self.transport
            ) as client:
                response = await client.post(
                    f"{self.base_url}/{path.lstrip('/')}", headers=headers, data=data
                )
        except httpx.HTTPError as exc:
            raise StripeAPIError(502) from exc
        if response.status_code < 200 or response.status_code >= 300:
            raise StripeAPIError(
                response.status_code, response.headers.get("Request-Id")
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise StripeAPIError(502, response.headers.get("Request-Id")) from exc
        if not isinstance(body, dict):
            raise StripeAPIError(502, response.headers.get("Request-Id"))
        return body

    async def create_checkout_session(
        self,
        *,
        price_id: str,
        tenant_id: str,
        tenant_slug: str,
        customer_name: str,
        customer_email: str | None,
        stripe_customer_id: str | None,
        success_url: str,
        cancel_url: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        data = {
            "mode": "subscription",
            "success_url": success_url,
            "cancel_url": cancel_url,
            "client_reference_id": tenant_id,
            "line_items[0][price]": price_id,
            "line_items[0][quantity]": "1",
            "subscription_data[metadata][tenant_slug]": tenant_slug,
            "subscription_data[metadata][customer_name]": customer_name,
        }
        if customer_email:
            data["subscription_data[metadata][customer_email]"] = customer_email
        if stripe_customer_id:
            data["customer"] = stripe_customer_id
        elif customer_email:
            data["customer_email"] = customer_email
        return await self._post("checkout/sessions", data, idempotency_key)

    async def create_portal_session(
        self,
        *,
        customer_id: str,
        return_url: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return await self._post(
            "billing_portal/sessions",
            {"customer": customer_id, "return_url": return_url},
            idempotency_key,
        )
