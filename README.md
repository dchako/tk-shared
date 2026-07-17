# thunderclouds-shared

Shared Python utilities for the Thunderclouds platform. Consumed by all backend services (`tk-auth`, `tk-billing`, `tk-resources`, `tk-provisioning`, `tk-notifications`, `tk-mcp`, `tk-bridge`).

## What's inside

| Module | Description |
|--------|-------------|
| `thunderclouds_shared.auth` | JWT decoding (HS256 / RS256), admin guard, FastAPI dependency factories, internal secret verification |
| `thunderclouds_shared.http` | Async HTTP client for service-to-service calls with retry, exponential backoff, jitter, and circuit breaker |

---

## Installation

```bash
# From any service repo (development)
pip install -e ../tk-shared

# With test dependencies
pip install -e "../tk-shared[test]"
```

Requirements: Python ≥ 3.11, FastAPI ≥ 0.100, httpx ≥ 0.25, python-jose[cryptography] ≥ 3.3, pydantic ≥ 2.0.

---

## Module reference

### `thunderclouds_shared.auth`

#### `decode_jwt(token, settings) -> dict`

Decodes and validates a JWT. Raises `InvalidTokenError` (HTTP 401) on failure.

`settings` is any object with these attributes:

| Attribute | Required | Description |
|-----------|----------|-------------|
| `JWT_SECRET_KEY` | yes (HS256) | Shared secret for HS256 |
| `JWT_PUBLIC_KEY` | yes (RS256) | PEM-encoded RSA public key; takes precedence over secret |
| `JWT_ALGORITHM` | no | Algorithm for HS256 (default `"HS256"`) |
| `JWT_ISSUER` | no | Expected `iss` claim; omit to skip issuer validation |

Always requires a valid `exp` claim and a non-null `sub`.

When `JWT_PUBLIC_KEY` is set, the public key is parsed once and cached per settings instance.

#### FastAPI dependency factories

```python
from thunderclouds_shared.auth.dependencies import (
    get_current_user_payload,
    get_current_user_id,
    get_admin_user_id,
    internal_secret_verifier,
)

# In your router setup:
bearer_scheme = HTTPBearer()

# Returns the full decoded JWT payload
payload_dep = get_current_user_payload(bearer_scheme, settings)

# Returns the user ID (int) from payload["sub"]
user_id_dep = get_current_user_id(bearer_scheme, settings)

# Returns the user ID only if payload["is_admin"] is True; 403 otherwise
admin_id_dep = get_admin_user_id(bearer_scheme, settings)

# Validates X-Internal-Secret header against settings.INTERNAL_SECRET
internal_dep = internal_secret_verifier(settings)
```

Usage in a route:

```python
@router.get("/protected")
async def protected(user_id: int = Depends(user_id_dep)):
    ...

@router.post("/internal/callback")
async def callback(_: None = Depends(internal_dep)):
    ...
```

#### `verify_internal_secret(provided, expected)`

Constant-time comparison of two strings. Raises `InvalidInternalSecretError` (HTTP 401) on mismatch. Used internally by `internal_secret_verifier`.

#### Exceptions

| Exception | HTTP status | When raised |
|-----------|-------------|-------------|
| `InvalidTokenError` | 401 (or 403) | Invalid/expired JWT, missing `sub`, non-admin user on admin-only route |
| `InvalidInternalSecretError` | 401 | Wrong or missing `X-Internal-Secret` |

---

### `thunderclouds_shared.http`

#### `InternalServiceClient`

Async HTTP client wrapping `httpx.AsyncClient` with:

- Automatic injection of the auth header (`X-Internal-Secret` by default) on every request.
- Retry with exponential backoff + jitter on transport errors and `502 / 503 / 504` responses.
- Circuit breaker: opens after N consecutive failures; resets after a configurable timeout.

```python
from thunderclouds_shared.http.client import InternalServiceClient

client = InternalServiceClient(
    base_url="http://tk-billing:8001",
    secret=settings.INTERNAL_SECRET,
)

# Standard HTTP methods (all async)
response = await client.get("/internal/quota/42")
response = await client.post("/internal/reserve", json={"resource": "postgres"})
response = await client.patch("/internal/quota/42", json={"delta": -1})
response = await client.delete("/internal/quota/42")

# Cleanup (call on app shutdown)
await client.aclose()
```

Constructor parameters:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `base_url` | required | Base URL of the target service |
| `secret` | required | Value for the auth header |
| `auth_header` | `"X-Internal-Secret"` | Header name for the secret |
| `timeout` | `Timeout(10.0, connect=2.0)` | httpx Timeout object |
| `retries` | `3` | Max retry attempts after the first failure |
| `backoff_factor` | `0.5` | Base for exponential backoff: `factor * 2^attempt` |
| `circuit_breaker_failures` | `5` | Consecutive failures before circuit opens |
| `circuit_breaker_reset_timeout` | `30` | Seconds before circuit resets to half-open |

`CircuitBreakerOpenError` is raised (not an HTTP error) when a request is blocked by an open circuit.

---

## Running tests

```bash
cd tk-shared
pip install -e ".[test]"
pytest
```

Test files:

- `tests/test_auth.py` — JWT decode, expiry, internal secret
- `tests/test_http_client.py` — auth header injection, retry on 503, circuit breaker

---

## Project structure

```
tk-shared/
├── pyproject.toml                  # Package metadata and dependencies
├── thunderclouds_shared/
│   ├── __init__.py
│   ├── auth/
│   │   ├── __init__.py
│   │   ├── core.py                 # decode_jwt, verify_internal_secret
│   │   ├── dependencies.py         # FastAPI dependency factories
│   │   └── exceptions.py           # InvalidTokenError, InvalidInternalSecretError
│   └── http/
│       ├── __init__.py
│       ├── client.py               # InternalServiceClient
│       └── exceptions.py           # CircuitBreakerOpenError
└── tests/
    ├── test_auth.py
    └── test_http_client.py
```
