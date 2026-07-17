from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Depends, Header, status
from fastapi.security import HTTPAuthorizationCredentials

from thunderclouds_shared.auth.core import decode_jwt, verify_internal_secret
from thunderclouds_shared.auth.exceptions import InvalidTokenError


def get_current_user_payload(scheme: Any, settings: Any) -> Callable[[], Awaitable[dict]]:
    async def dependency(
        credentials: HTTPAuthorizationCredentials = Depends(scheme),
    ) -> dict:
        return decode_jwt(credentials.credentials, settings)

    return dependency


def get_current_user_id(scheme: Any, settings: Any) -> Callable[[], Awaitable[int]]:
    payload_dependency = get_current_user_payload(scheme, settings)

    async def dependency(payload: dict = Depends(payload_dependency)) -> int:
        try:
            return int(payload["sub"])
        except (TypeError, ValueError, KeyError) as exc:
            raise InvalidTokenError("Token inválido") from exc

    return dependency


def get_admin_user_id(scheme: Any, settings: Any) -> Callable[[], Awaitable[int]]:
    payload_dependency = get_current_user_payload(scheme, settings)

    async def dependency(payload: dict = Depends(payload_dependency)) -> int:
        if not payload.get("is_admin", False):
            raise InvalidTokenError(
                "Se requieren permisos de administrador",
                status_code=status.HTTP_403_FORBIDDEN,
            )
        try:
            return int(payload["sub"])
        except (TypeError, ValueError, KeyError) as exc:
            raise InvalidTokenError("Token inválido") from exc

    return dependency


def internal_secret_verifier(settings: Any) -> Callable[[], None]:
    expected_secret = getattr(settings, "INTERNAL_SECRET", "")

    async def dependency(x_internal_secret: str = Header(default="", alias="X-Internal-Secret")) -> None:
        verify_internal_secret(x_internal_secret, expected_secret)

    return dependency
