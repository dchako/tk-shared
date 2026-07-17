from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from jose import jwt

from thunderclouds_shared.auth.core import decode_jwt, verify_internal_secret
from thunderclouds_shared.auth.exceptions import InvalidInternalSecretError, InvalidTokenError


class DummySettings:
    JWT_SECRET_KEY = "super-secret-key-that-is-long-enough-123456"
    JWT_PUBLIC_KEY = ""
    JWT_ALGORITHM = "HS256"
    JWT_ISSUER = "tk-auth"
    INTERNAL_SECRET = "internal-secret-with-sufficient-length-123456"


def _make_token(exp_delta_seconds: int) -> str:
    payload = {
        "sub": "42",
        "iss": DummySettings.JWT_ISSUER,
        "exp": datetime.now(UTC) + timedelta(seconds=exp_delta_seconds),
    }
    return jwt.encode(payload, DummySettings.JWT_SECRET_KEY, algorithm=DummySettings.JWT_ALGORITHM)


def test_decode_valid_token() -> None:
    token = _make_token(exp_delta_seconds=60)

    payload = decode_jwt(token, DummySettings)

    assert payload["sub"] == "42"


def test_decode_expired_token_rejected() -> None:
    token = _make_token(exp_delta_seconds=-60)

    with pytest.raises(InvalidTokenError):
        decode_jwt(token, DummySettings)


def test_invalid_internal_secret_rejected() -> None:
    with pytest.raises(InvalidInternalSecretError):
        verify_internal_secret("wrong-secret", DummySettings.INTERNAL_SECRET)
