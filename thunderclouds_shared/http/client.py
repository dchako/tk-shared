from __future__ import annotations

import asyncio
import random
import time
from typing import Any

import httpx

from thunderclouds_shared.http.exceptions import CircuitBreakerOpenError


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
        for attempt in range(attempts):
            try:
                response = await self._client.request(method=method, url=path, **request_kwargs)
            except httpx.TransportError:
                self._record_failure()
                if attempt >= self._retries:
                    raise
                await self._sleep_before_retry(attempt)
                continue

            if response.status_code in {502, 503, 504}:
                self._record_failure()
                if attempt >= self._retries:
                    return response
                await self._sleep_before_retry(attempt)
                continue

            self._record_success()
            return response

        raise RuntimeError("Unexpected retry loop termination")

    def _merge_headers(self, headers: Any) -> dict[str, str]:
        merged: dict[str, str] = {}
        if headers:
            merged.update(dict(headers))
        merged[self._auth_header] = self._secret
        return merged

    async def _sleep_before_retry(self, attempt: int) -> None:
        delay = self._backoff_factor * (2 ** attempt)
        jitter = random.uniform(0, delay / 2 if delay > 0 else 0)
        await asyncio.sleep(delay + jitter)

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
