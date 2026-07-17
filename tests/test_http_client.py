from __future__ import annotations

import httpx
import pytest
import respx

from thunderclouds_shared.http.client import InternalServiceClient
from thunderclouds_shared.http.exceptions import CircuitBreakerOpenError


@pytest.mark.asyncio
@respx.mock
async def test_auth_header_is_injected() -> None:
    route = respx.get("http://billing.test/internal/ping").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    client = InternalServiceClient(base_url="http://billing.test", secret="s3cr3t")

    response = await client.get("/internal/ping")

    assert response.status_code == 200
    assert route.called
    request = route.calls.last.request
    assert request.headers["X-Internal-Secret"] == "s3cr3t"
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_retry_on_503_then_success() -> None:
    route = respx.get("http://billing.test/internal/flaky").mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(503),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    client = InternalServiceClient(
        base_url="http://billing.test",
        secret="s3cr3t",
        retries=3,
        backoff_factor=0,
    )

    response = await client.get("/internal/flaky")

    assert response.status_code == 200
    assert route.call_count == 3
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_circuit_breaker_opens_after_failures() -> None:
    respx.get("http://billing.test/internal/down").mock(return_value=httpx.Response(503))
    client = InternalServiceClient(
        base_url="http://billing.test",
        secret="s3cr3t",
        retries=0,
        backoff_factor=0,
        circuit_breaker_failures=2,
        circuit_breaker_reset_timeout=60,
    )

    first = await client.get("/internal/down")
    second = await client.get("/internal/down")

    assert first.status_code == 503
    assert second.status_code == 503

    with pytest.raises(CircuitBreakerOpenError):
        await client.get("/internal/down")

    await client.aclose()
