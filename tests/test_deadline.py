"""
Tests for thunderclouds_shared.http.deadline and .middleware and client propagation.
"""
from __future__ import annotations

import json
import time

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from thunderclouds_shared.http.client import InternalServiceClient
from thunderclouds_shared.http.deadline import (
    HEADER_NAME,
    DeadlineExceededError,
    _deadline_epoch_ms_var,
    check_deadline,
    deadline_header_value,
    remaining_ms,
    reset_deadline,
    set_deadline,
    set_deadline_from_header,
)
from thunderclouds_shared.http.middleware import DeadlineMiddleware


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _future_ms(delta_ms: int = 60_000) -> int:
    return int(time.time() * 1000) + delta_ms


def _past_ms(delta_ms: int = 1_000) -> int:
    return int(time.time() * 1000) - delta_ms


# ---------------------------------------------------------------------------
# deadline.py — set / reset / remaining
# ---------------------------------------------------------------------------


def test_set_deadline_stores_value():
    epoch = _future_ms(5_000)
    token = set_deadline(epoch)
    try:
        assert _deadline_epoch_ms_var.get() == epoch
    finally:
        reset_deadline(token)


def test_reset_deadline_restores_none():
    token = set_deadline(_future_ms())
    reset_deadline(token)
    assert _deadline_epoch_ms_var.get() is None


def test_remaining_ms_no_deadline():
    assert remaining_ms() is None


def test_remaining_ms_future():
    token = set_deadline(_future_ms(10_000))
    try:
        rem = remaining_ms()
        assert rem is not None
        assert 9_000 < rem <= 10_000
    finally:
        reset_deadline(token)


def test_remaining_ms_past():
    token = set_deadline(_past_ms(2_000))
    try:
        rem = remaining_ms()
        assert rem is not None
        assert rem < 0
    finally:
        reset_deadline(token)


def test_deadline_header_value_no_deadline():
    assert deadline_header_value() is None


def test_deadline_header_value_present():
    epoch = _future_ms(30_000)
    token = set_deadline(epoch)
    try:
        assert deadline_header_value() == str(epoch)
    finally:
        reset_deadline(token)


# ---------------------------------------------------------------------------
# set_deadline_from_header — parse / invalid
# ---------------------------------------------------------------------------


def test_set_deadline_from_header_valid():
    epoch = _future_ms(5_000)
    tok = set_deadline_from_header(str(epoch))
    try:
        assert _deadline_epoch_ms_var.get() == epoch
    finally:
        if tok is not None:
            reset_deadline(tok)


def test_set_deadline_from_header_invalid_returns_none():
    tok = set_deadline_from_header("not-a-number")
    assert tok is None
    # ContextVar should be unchanged
    assert _deadline_epoch_ms_var.get() is None


def test_set_deadline_from_header_float_string_fails():
    # float string is NOT a valid int
    tok = set_deadline_from_header("1234567890.5")
    assert tok is None


def test_set_deadline_from_header_empty_returns_none():
    tok = set_deadline_from_header("")
    assert tok is None


# ---------------------------------------------------------------------------
# check_deadline
# ---------------------------------------------------------------------------


def test_check_deadline_no_deadline_passes():
    check_deadline()  # Should not raise


def test_check_deadline_future_passes():
    token = set_deadline(_future_ms(10_000))
    try:
        check_deadline(min_budget_ms=500)
    finally:
        reset_deadline(token)


def test_check_deadline_expired_raises():
    token = set_deadline(_past_ms(1_000))
    try:
        with pytest.raises(DeadlineExceededError) as exc_info:
            check_deadline()
        assert exc_info.value.remaining_ms < 0
    finally:
        reset_deadline(token)


def test_check_deadline_insufficient_budget_raises():
    # 100ms left, min_budget=500 → raises
    token = set_deadline(_future_ms(100))
    try:
        with pytest.raises(DeadlineExceededError):
            check_deadline(min_budget_ms=500)
    finally:
        reset_deadline(token)


def test_deadline_exceeded_error_has_remaining_ms():
    token = set_deadline(_past_ms(500))
    try:
        with pytest.raises(DeadlineExceededError) as exc_info:
            check_deadline()
        err = exc_info.value
        assert hasattr(err, "remaining_ms")
        assert err.remaining_ms < 0
    finally:
        reset_deadline(token)


# ---------------------------------------------------------------------------
# middleware.py
# ---------------------------------------------------------------------------


def _make_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(DeadlineMiddleware)

    @app.get("/ping")
    async def ping():
        return {"pong": True, "remaining_ms": remaining_ms()}

    return app


def test_middleware_passthrough_no_header():
    client = TestClient(_make_app())
    resp = client.get("/ping")
    assert resp.status_code == 200
    data = resp.json()
    assert data["pong"] is True
    # No deadline active — remaining_ms returns None inside handler
    assert data["remaining_ms"] is None


def test_middleware_valid_future_deadline_sets_contextvar():
    epoch = _future_ms(60_000)
    client = TestClient(_make_app())
    resp = client.get("/ping", headers={HEADER_NAME: str(epoch)})
    assert resp.status_code == 200
    data = resp.json()
    # remaining_ms should be roughly 60s
    assert data["remaining_ms"] is not None
    assert 55_000 < data["remaining_ms"] <= 60_000


def test_middleware_expired_deadline_returns_504():
    past_epoch = _past_ms(5_000)
    client = TestClient(_make_app())
    resp = client.get("/ping", headers={HEADER_NAME: str(past_epoch)})
    assert resp.status_code == 504
    body = resp.json()
    assert body["error"] == "deadline_exceeded"
    assert body["remaining_ms"] < 0


def test_middleware_invalid_header_passes_through():
    client = TestClient(_make_app())
    resp = client.get("/ping", headers={HEADER_NAME: "garbage"})
    # Invalid header treated as absent → passthrough
    assert resp.status_code == 200


def test_middleware_resets_contextvar_after_request():
    epoch = _future_ms(60_000)
    client = TestClient(_make_app())
    client.get("/ping", headers={HEADER_NAME: str(epoch)})
    # After the request the ContextVar should be back to None
    # (check via deadline module directly)
    assert _deadline_epoch_ms_var.get() is None


# ---------------------------------------------------------------------------
# client.py — deadline header propagation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_client_propagates_deadline_header():
    epoch = _future_ms(30_000)
    route = respx.get("http://svc.test/ping").mock(return_value=httpx.Response(200))
    client = InternalServiceClient(base_url="http://svc.test", secret="s3cr3t")

    token = set_deadline(epoch)
    try:
        await client.get("/ping")
    finally:
        reset_deadline(token)

    assert route.called
    assert route.calls.last.request.headers[HEADER_NAME] == str(epoch)
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_client_no_deadline_header_when_not_set():
    route = respx.get("http://svc.test/ping").mock(return_value=httpx.Response(200))
    client = InternalServiceClient(base_url="http://svc.test", secret="s3cr3t")

    await client.get("/ping")

    assert route.called
    assert HEADER_NAME not in route.calls.last.request.headers
    await client.aclose()


# ---------------------------------------------------------------------------
# middleware.py — WARNING + structured fields on 504 enforcement
# ---------------------------------------------------------------------------


def test_middleware_504_emits_warning_not_info(caplog):
    """Deadline enforcement (504) must emit WARNING, not INFO."""
    import logging
    past_epoch = _past_ms(5_000)
    with caplog.at_level(logging.DEBUG, logger="thunderclouds_shared.http.middleware"):
        client = TestClient(_make_app())
        resp = client.get("/ping", headers={HEADER_NAME: str(past_epoch)})
    assert resp.status_code == 504
    middleware_records = [
        r for r in caplog.records
        if r.name == "thunderclouds_shared.http.middleware"
    ]
    assert middleware_records, "Expected at least one log from DeadlineMiddleware"
    assert any(r.levelname == "WARNING" for r in middleware_records), (
        "DeadlineMiddleware must log at WARNING level when enforcing 504"
    )
    # Must NOT be INFO
    assert not any(r.levelname == "INFO" for r in middleware_records), (
        "DeadlineMiddleware must not use INFO for 504 enforcement"
    )


def test_middleware_504_log_has_deadline_enforced_field(caplog):
    """WARNING log must carry deadline_enforced=True in the extra dict."""
    import logging
    past_epoch = _past_ms(5_000)
    with caplog.at_level(logging.WARNING, logger="thunderclouds_shared.http.middleware"):
        client = TestClient(_make_app())
        client.get("/ping", headers={HEADER_NAME: str(past_epoch)})
    warning = next(
        (r for r in caplog.records if r.levelname == "WARNING"),
        None,
    )
    assert warning is not None
    assert getattr(warning, "deadline_enforced", None) is True, (
        "Log record must have extra field deadline_enforced=True"
    )


def test_middleware_504_log_has_remaining_ms_field(caplog):
    """WARNING log must carry remaining_ms (negative) in the extra dict."""
    import logging
    past_epoch = _past_ms(5_000)
    with caplog.at_level(logging.WARNING, logger="thunderclouds_shared.http.middleware"):
        client = TestClient(_make_app())
        client.get("/ping", headers={HEADER_NAME: str(past_epoch)})
    warning = next(
        (r for r in caplog.records if r.levelname == "WARNING"),
        None,
    )
    assert warning is not None
    rem_field = getattr(warning, "remaining_ms", None)
    assert rem_field is not None, "Log record must have extra field remaining_ms"
    assert rem_field < 0, "remaining_ms must be negative for an already-expired deadline"


def test_middleware_504_log_has_path_field(caplog):
    """WARNING log must carry path field matching the request path."""
    import logging
    past_epoch = _past_ms(5_000)
    with caplog.at_level(logging.WARNING, logger="thunderclouds_shared.http.middleware"):
        client = TestClient(_make_app())
        client.get("/ping", headers={HEADER_NAME: str(past_epoch)})
    warning = next(
        (r for r in caplog.records if r.levelname == "WARNING"),
        None,
    )
    assert warning is not None
    assert getattr(warning, "path", None) == "/ping", (
        "Log record must have extra field path matching the request path"
    )


# ---------------------------------------------------------------------------
# client.py — retry-abort log includes remaining_ms + host/path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_client_retry_aborted_when_no_budget(monkeypatch):
    """When the deadline leaves < 500ms after the next sleep, retries are stopped."""
    # Force a very small remaining budget so the first sleep would exceed it.
    # remaining_ms after sleep_ms would be (200 - 500) = -300 → abort
    short_deadline = int(time.time() * 1000) + 200  # 200ms left

    call_count = 0

    async def _flaky(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return httpx.Response(503)

    respx.get("http://svc.test/ping").mock(side_effect=_flaky)

    client = InternalServiceClient(
        base_url="http://svc.test",
        secret="s",
        retries=3,
        backoff_factor=0.5,  # first sleep ≈ 500ms + jitter
    )

    token = set_deadline(short_deadline)
    try:
        resp = await client.get("/ping")
    finally:
        reset_deadline(token)

    # Should stop after the 1st attempt (budget is too small for the sleep)
    assert call_count == 1
    assert resp.status_code == 503
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_client_no_budget_log_includes_remaining_ms(caplog):
    """Retry-abort WARNING must include remaining_ms extra field."""
    import logging
    short_deadline = int(time.time() * 1000) + 200  # 200ms left

    async def _flaky(*args, **kwargs):
        return httpx.Response(503)

    respx.get("http://svc2.test/api/items").mock(side_effect=_flaky)

    client = InternalServiceClient(
        base_url="http://svc2.test",
        secret="s",
        retries=3,
        backoff_factor=0.5,
    )

    token = set_deadline(short_deadline)
    try:
        with caplog.at_level(logging.WARNING, logger="thunderclouds_shared.http.client"):
            await client.get("/api/items")
    finally:
        reset_deadline(token)

    records = [
        r for r in caplog.records
        if "no presupuesto" in r.getMessage().lower()
    ]
    assert records, "Expected 'no presupuesto' WARNING from retry-abort path"
    rec = records[0]
    assert hasattr(rec, "remaining_ms"), "Log record must have extra field remaining_ms"
    assert rec.levelname == "WARNING"
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_client_no_budget_log_includes_host_and_path(caplog):
    """Retry-abort WARNING must include host and path extra fields."""
    import logging
    short_deadline = int(time.time() * 1000) + 200  # 200ms left

    async def _flaky(*args, **kwargs):
        return httpx.Response(503)

    respx.get("http://svc3.test/some/endpoint").mock(side_effect=_flaky)

    client = InternalServiceClient(
        base_url="http://svc3.test",
        secret="s",
        retries=3,
        backoff_factor=0.5,
    )

    token = set_deadline(short_deadline)
    try:
        with caplog.at_level(logging.WARNING, logger="thunderclouds_shared.http.client"):
            await client.get("/some/endpoint")
    finally:
        reset_deadline(token)

    records = [
        r for r in caplog.records
        if "no presupuesto" in r.getMessage().lower()
    ]
    assert records, "Expected 'no presupuesto' WARNING from retry-abort path"
    rec = records[0]
    assert hasattr(rec, "host"), "Log record must have extra field host"
    assert hasattr(rec, "path"), "Log record must have extra field path"
    # host should contain the base URL, path the requested path
    assert "svc3.test" in str(rec.host)
    assert rec.path == "/some/endpoint"
    await client.aclose()
