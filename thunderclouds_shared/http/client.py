from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any

import httpx

from thunderclouds_shared.http.deadline import (
    HEADER_NAME as _DEADLINE_HEADER,
    deadline_header_value,
    remaining_ms,
)
from thunderclouds_shared.http.exceptions import CircuitBreakerOpenError

logger = logging.getLogger(__name__)


class InternalServiceClient:
    def __init__(
        self,
        base_url: str,
        secret: str,
        auth_header: str = "X-Internal-Secret",
        timeout: httpx.Timeout = httpx.Timeout(10.0, connect=2.0),
        retries: int = 3,
        backoff_factor: float = 0.5,
        circuit_breaker_failures: int = 5,
        circuit_breaker_reset_timeout: int = 30,
    ) -> None:
        self._auth_header = auth_header
        self._secret = secret
        self._retries = retries
        self._backoff_factor = backoff_factor
        self._circuit_breaker_failures = circuit_breaker_failures
        self._circuit_breaker_reset_timeout = circuit_breaker_reset_timeout

        self._consecutive_failures = 0
        self._opened_at: float | None = None

        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout)

    async def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self._request("GET", path, **kwargs)

    async def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self._request("POST", path, **kwargs)

    async def patch(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self._request("PATCH", path, **kwargs)

    async def put(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self._request("PUT", path, **kwargs)

    async def delete(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self._request("DELETE", path, **kwargs)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        self._ensure_circuit_closed()

        request_kwargs = dict(kwargs)
        request_kwargs["headers"] = self._merge_headers(kwargs.get("headers"))

        attempts = self._retries + 1
        last_response: httpx.Response | None = None
        last_transport_exc: Exception | None = None

        for attempt in range(attempts):
            try:
                response = await self._client.request(method=method, url=path, **request_kwargs)
            except httpx.TransportError as exc:
                self._record_failure()
                last_transport_exc = exc
                if attempt >= self._retries:
                    raise
                sleep_ms = self._compute_sleep_ms(attempt)
                if not self._has_budget_for_retry(sleep_ms):
                    _rem = remaining_ms()
                    _host = str(self._client.base_url)
                    logger.warning(
                        "deadline: no presupuesto para reintentar sleep_ms=%d remaining_ms=%s host=%s path=%s",
                        sleep_ms,
                        _rem,
                        _host,
                        path,
                        extra={"remaining_ms": _rem, "host": _host, "path": path},
                    )
                    raise
                await asyncio.sleep(sleep_ms / 1000.0)
                continue

            if response.status_code in {502, 503, 504}:
                self._record_failure()
                last_response = response
                if attempt >= self._retries:
                    return response
                sleep_ms = self._compute_sleep_ms(attempt)
                if not self._has_budget_for_retry(sleep_ms):
                    _rem = remaining_ms()
                    _host = str(self._client.base_url)
                    logger.warning(
                        "deadline: no presupuesto para reintentar sleep_ms=%d remaining_ms=%s host=%s path=%s",
                        sleep_ms,
                        _rem,
                        _host,
                        path,
                        extra={"remaining_ms": _rem, "host": _host, "path": path},
                    )
                    return response
                await asyncio.sleep(sleep_ms / 1000.0)
                continue

            self._record_success()
            return response

        # Should be unreachable, but keep the compiler happy.
        if last_response is not None:
            return last_response
        raise RuntimeError("Unexpected retry loop termination")

    def _merge_headers(self, headers: Any) -> dict[str, str]:
        merged: dict[str, str] = {}
        if headers:
            merged.update(dict(headers))
        merged[self._auth_header] = self._secret
        # Propagate deadline header if a deadline is active in this context.
        dh = deadline_header_value()
        if dh is not None:
            merged[_DEADLINE_HEADER] = dh
        return merged

    def _compute_sleep_ms(self, attempt: int) -> float:
        """Return the sleep duration in milliseconds for *attempt* (0-based)."""
        delay_s = self._backoff_factor * (2 ** attempt)
        jitter_s = random.uniform(0, delay_s / 2 if delay_s > 0 else 0)
        return (delay_s + jitter_s) * 1000.0

    def _has_budget_for_retry(self, sleep_ms: float, min_budget_ms: int = 500) -> bool:
        """Return False if sleeping *sleep_ms* would leave < min_budget_ms remaining."""
        rem = remaining_ms()
        if rem is None:
            return True  # no deadline — always allow
        return (rem - sleep_ms) >= min_budget_ms

    async def _sleep_before_retry(self, attempt: int) -> None:
        sleep_ms = self._compute_sleep_ms(attempt)
        await asyncio.sleep(sleep_ms / 1000.0)

    def _is_circuit_open(self) -> bool:
        if self._opened_at is None:
            return False
        elapsed = time.monotonic() - self._opened_at
        if elapsed >= self._circuit_breaker_reset_timeout:
            self._opened_at = None
            self._consecutive_failures = 0
            return False
        return True

    def _ensure_circuit_closed(self) -> None:
        if self._is_circuit_open():
            raise CircuitBreakerOpenError("Circuit breaker is open")

    def _record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._circuit_breaker_failures:
            self._opened_at = time.monotonic()

    def _record_success(self) -> None:
        self._consecutive_failures = 0
        self._opened_at = None
