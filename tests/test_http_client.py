from __future__ import annotations

import httpx
import pytest
import respx

from thunderclouds_shared.http.client import InternalServiceClient
from thunderclouds_shared.http.exceptions import CircuitBreakerOpenError


# ---------------------------------------------------------------------------
# Auth header injection
# ---------------------------------------------------------------------------


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
    assert route.calls.last.request.headers["X-Internal-Secret"] == "s3cr3t"
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_custom_auth_header_name_is_used() -> None:
    route = respx.get("http://svc.test/ping").mock(return_value=httpx.Response(200))
    client = InternalServiceClient(
        base_url="http://svc.test",
        secret="tok",
        auth_header="X-Service-Token",
    )

    await client.get("/ping")

    headers = route.calls.last.request.headers
    assert headers["X-Service-Token"] == "tok"
    assert "X-Internal-Secret" not in headers
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_caller_provided_headers_are_preserved() -> None:
    route = respx.get("http://svc.test/ping").mock(return_value=httpx.Response(200))
    client = InternalServiceClient(base_url="http://svc.test", secret="s3cr3t")

    await client.get("/ping", headers={"X-Trace-Id": "abc123"})

    headers = route.calls.last.request.headers
    assert headers["X-Internal-Secret"] == "s3cr3t"
    assert headers["X-Trace-Id"] == "abc123"
    await client.aclose()


# ---------------------------------------------------------------------------
# HTTP methods
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_post_method() -> None:
    route = respx.post("http://svc.test/items").mock(return_value=httpx.Response(201))
    client = InternalServiceClient(base_url="http://svc.test", secret="s")

    response = await client.post("/items", json={"name": "db"})

    assert response.status_code == 201
    assert route.called
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_patch_method() -> None:
    route = respx.patch("http://svc.test/items/1").mock(return_value=httpx.Response(200))
    client = InternalServiceClient(base_url="http://svc.test", secret="s")

    response = await client.patch("/items/1", json={"status": "running"})

    assert response.status_code == 200
    assert route.called
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_put_method() -> None:
    route = respx.put("http://svc.test/items/1").mock(return_value=httpx.Response(200))
    client = InternalServiceClient(base_url="http://svc.test", secret="s")

    response = await client.put("/items/1", json={"quota": 10})

    assert response.status_code == 200
    assert route.called
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_delete_method() -> None:
    route = respx.delete("http://svc.test/items/1").mock(return_value=httpx.Response(204))
    client = InternalServiceClient(base_url="http://svc.test", secret="s")

    response = await client.delete("/items/1")

    assert response.status_code == 204
    assert route.called
    await client.aclose()


# ---------------------------------------------------------------------------
# Retry behaviour
# ---------------------------------------------------------------------------


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
async def test_retry_on_502() -> None:
    route = respx.get("http://svc.test/ping").mock(
        side_effect=[httpx.Response(502), httpx.Response(200)]
    )
    client = InternalServiceClient(base_url="http://svc.test", secret="s", retries=2, backoff_factor=0)

    response = await client.get("/ping")

    assert response.status_code == 200
    assert route.call_count == 2
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_retry_on_504() -> None:
    route = respx.get("http://svc.test/ping").mock(
        side_effect=[httpx.Response(504), httpx.Response(200)]
    )
    client = InternalServiceClient(base_url="http://svc.test", secret="s", retries=2, backoff_factor=0)

    response = await client.get("/ping")

    assert response.status_code == 200
    assert route.call_count == 2
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_returns_last_error_response_when_retries_exhausted() -> None:
    route = respx.get("http://svc.test/ping").mock(return_value=httpx.Response(503))
    client = InternalServiceClient(base_url="http://svc.test", secret="s", retries=2, backoff_factor=0)

    response = await client.get("/ping")

    # After exhausting retries the last error response is returned, not raised
    assert response.status_code == 503
    assert route.call_count == 3  # 1 initial + 2 retries
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_retry_on_transport_error_then_success() -> None:
    route = respx.get("http://svc.test/ping").mock(
        side_effect=[httpx.ConnectError("timeout"), httpx.Response(200)]
    )
    client = InternalServiceClient(base_url="http://svc.test", secret="s", retries=2, backoff_factor=0)

    response = await client.get("/ping")

    assert response.status_code == 200
    assert route.call_count == 2
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_transport_error_raises_after_retries_exhausted() -> None:
    respx.get("http://svc.test/ping").mock(side_effect=httpx.ConnectError("down"))
    client = InternalServiceClient(base_url="http://svc.test", secret="s", retries=1, backoff_factor=0)

    with pytest.raises(httpx.ConnectError):
        await client.get("/ping")

    await client.aclose()


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------


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


@pytest.mark.asyncio
@respx.mock
async def test_circuit_breaker_resets_after_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    respx.get("http://svc.test/ping").mock(return_value=httpx.Response(503))
    svc = InternalServiceClient(
        base_url="http://svc.test",
        secret="s",
        retries=0,
        backoff_factor=0,
        circuit_breaker_failures=1,
        circuit_breaker_reset_timeout=30,
    )

    # Trip the circuit
    await svc.get("/ping")
    assert svc._opened_at is not None
    opened_at = svc._opened_at

    with pytest.raises(CircuitBreakerOpenError):
        await svc.get("/ping")

    # Simulate time passing beyond the reset timeout
    monkeypatch.setattr(time, "monotonic", lambda: opened_at + 31)

    # Now mock a successful response for the half-open probe
    respx.get("http://svc.test/ping").mock(return_value=httpx.Response(200))
    response = await svc.get("/ping")

    assert response.status_code == 200
    await svc.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_successful_request_resets_failure_counter() -> None:
    route = respx.get("http://svc.test/ping").mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(200),  # success resets counter
            httpx.Response(503),  # would need another N failures to open
        ]
    )
    client = InternalServiceClient(
        base_url="http://svc.test",
        secret="s",
        retries=0,
        backoff_factor=0,
        circuit_breaker_failures=2,
        circuit_breaker_reset_timeout=60,
    )

    await client.get("/ping")  # failure 1
    await client.get("/ping")  # success → resets to 0
    await client.get("/ping")  # failure 1 again (not 2, so circuit stays closed)

    # Circuit should still be closed (only 1 consecutive failure after reset)
    assert not client._is_circuit_open()
    await client.aclose()
