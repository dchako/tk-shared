from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import status
from jose import jwt

from thunderclouds_shared.auth.core import decode_jwt, verify_internal_secret
from thunderclouds_shared.auth.dependencies import (
    get_admin_user_id,
    get_current_user_id,
    get_current_user_payload,
    internal_secret_verifier,
)
from thunderclouds_shared.auth.exceptions import InvalidInternalSecretError, InvalidTokenError


class DummySettings:
    JWT_SECRET_KEY = "super-secret-key-that-is-long-enough-123456"
    JWT_PUBLIC_KEY = ""
    JWT_ALGORITHM = "HS256"
    JWT_ISSUER = "tk-auth"
    INTERNAL_SECRET = "internal-secret-with-sufficient-length-123456"


class DummySettingsNoIssuer:
    JWT_SECRET_KEY = "super-secret-key-that-is-long-enough-123456"
    JWT_PUBLIC_KEY = ""
    JWT_ALGORITHM = "HS256"
    JWT_ISSUER = None
    INTERNAL_SECRET = "internal-secret-with-sufficient-length-123456"


def _make_token(
    exp_delta_seconds: int,
    sub: str | None = "42",
    issuer: str | None = DummySettings.JWT_ISSUER,
    extra: dict | None = None,
) -> str:
    payload: dict = {"exp": datetime.now(UTC) + timedelta(seconds=exp_delta_seconds)}
    if sub is not None:
        payload["sub"] = sub
    if issuer is not None:
        payload["iss"] = issuer
    if extra:
        payload.update(extra)
    return jwt.encode(payload, DummySettings.JWT_SECRET_KEY, algorithm=DummySettings.JWT_ALGORITHM)


# ---------------------------------------------------------------------------
# decode_jwt — happy path
# ---------------------------------------------------------------------------


def test_decode_valid_token() -> None:
    token = _make_token(exp_delta_seconds=60)

    payload = decode_jwt(token, DummySettings)

    assert payload["sub"] == "42"


def test_decode_valid_token_returns_full_payload() -> None:
    token = _make_token(exp_delta_seconds=60, extra={"tenant_id": 7})

    payload = decode_jwt(token, DummySettings)

    assert payload["tenant_id"] == 7


# ---------------------------------------------------------------------------
# decode_jwt — rejection cases
# ---------------------------------------------------------------------------


def test_decode_expired_token_rejected() -> None:
    token = _make_token(exp_delta_seconds=-60)

    with pytest.raises(InvalidTokenError):
        decode_jwt(token, DummySettings)


def test_decode_token_without_sub_rejected() -> None:
    token = _make_token(exp_delta_seconds=60, sub=None)

    with pytest.raises(InvalidTokenError):
        decode_jwt(token, DummySettings)


def test_decode_token_with_wrong_issuer_rejected() -> None:
    token = _make_token(exp_delta_seconds=60, issuer="untrusted-service")

    with pytest.raises(InvalidTokenError):
        decode_jwt(token, DummySettings)


def test_decode_token_issuer_skipped_when_not_configured() -> None:
    # When JWT_ISSUER is None, any issuer (or no issuer) should be accepted.
    token = _make_token(exp_delta_seconds=60, issuer="whatever")

    payload = decode_jwt(token, DummySettingsNoIssuer)

    assert payload["sub"] == "42"


def test_decode_garbage_token_rejected() -> None:
    with pytest.raises(InvalidTokenError):
        decode_jwt("not.a.token", DummySettings)


# ---------------------------------------------------------------------------
# verify_internal_secret
# ---------------------------------------------------------------------------


def test_valid_internal_secret_accepted() -> None:
    # Should not raise
    verify_internal_secret(DummySettings.INTERNAL_SECRET, DummySettings.INTERNAL_SECRET)


def test_invalid_internal_secret_rejected() -> None:
    with pytest.raises(InvalidInternalSecretError):
        verify_internal_secret("wrong-secret", DummySettings.INTERNAL_SECRET)


def test_empty_internal_secret_rejected() -> None:
    with pytest.raises(InvalidInternalSecretError):
        verify_internal_secret("", DummySettings.INTERNAL_SECRET)


def test_none_internal_secret_rejected() -> None:
    with pytest.raises(InvalidInternalSecretError):
        verify_internal_secret(None, DummySettings.INTERNAL_SECRET)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# get_current_user_payload dependency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_current_user_payload_returns_dict() -> None:
    token = _make_token(exp_delta_seconds=60)
    scheme = MagicMock()
    credentials = MagicMock(credentials=token)
    dep = get_current_user_payload(scheme, DummySettings)

    payload = await dep(credentials=credentials)

    assert payload["sub"] == "42"


@pytest.mark.asyncio
async def test_get_current_user_payload_raises_on_invalid_token() -> None:
    scheme = MagicMock()
    credentials = MagicMock(credentials="bad.token.here")
    dep = get_current_user_payload(scheme, DummySettings)

    with pytest.raises(InvalidTokenError):
        await dep(credentials=credentials)


# ---------------------------------------------------------------------------
# get_current_user_id dependency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_current_user_id_returns_int() -> None:
    token = _make_token(exp_delta_seconds=60, sub="99")
    scheme = MagicMock()
    credentials = MagicMock(credentials=token)

    payload_dep = get_current_user_payload(scheme, DummySettings)
    payload = await payload_dep(credentials=credentials)

    id_dep = get_current_user_id(scheme, DummySettings)
    user_id = await id_dep(payload=payload)

    assert user_id == 99
    assert isinstance(user_id, int)


# ---------------------------------------------------------------------------
# get_admin_user_id dependency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_admin_user_id_returns_id_for_admin() -> None:
    token = _make_token(exp_delta_seconds=60, sub="7", extra={"is_admin": True})
    scheme = MagicMock()
    credentials = MagicMock(credentials=token)

    payload_dep = get_current_user_payload(scheme, DummySettings)
    payload = await payload_dep(credentials=credentials)

    admin_dep = get_admin_user_id(scheme, DummySettings)
    admin_id = await admin_dep(payload=payload)

    assert admin_id == 7


@pytest.mark.asyncio
async def test_get_admin_user_id_raises_403_for_non_admin() -> None:
    token = _make_token(exp_delta_seconds=60, sub="5", extra={"is_admin": False})
    scheme = MagicMock()
    credentials = MagicMock(credentials=token)

    payload_dep = get_current_user_payload(scheme, DummySettings)
    payload = await payload_dep(credentials=credentials)

    admin_dep = get_admin_user_id(scheme, DummySettings)

    with pytest.raises(InvalidTokenError) as exc_info:
        await admin_dep(payload=payload)

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_get_admin_user_id_raises_403_when_is_admin_missing() -> None:
    token = _make_token(exp_delta_seconds=60, sub="5")
    scheme = MagicMock()
    credentials = MagicMock(credentials=token)

    payload_dep = get_current_user_payload(scheme, DummySettings)
    payload = await payload_dep(credentials=credentials)

    admin_dep = get_admin_user_id(scheme, DummySettings)

    with pytest.raises(InvalidTokenError) as exc_info:
        await admin_dep(payload=payload)

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN


# ---------------------------------------------------------------------------
# internal_secret_verifier dependency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_internal_secret_verifier_passes_correct_secret() -> None:
    dep = internal_secret_verifier(DummySettings)
    # Should not raise
    await dep(x_internal_secret=DummySettings.INTERNAL_SECRET)


@pytest.mark.asyncio
async def test_internal_secret_verifier_raises_on_wrong_secret() -> None:
    dep = internal_secret_verifier(DummySettings)

    with pytest.raises(InvalidInternalSecretError):
        await dep(x_internal_secret="totally-wrong")
