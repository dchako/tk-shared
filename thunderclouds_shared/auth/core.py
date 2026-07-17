from __future__ import annotations

import hmac
from typing import Any

from jose import JWTError, jwt

from thunderclouds_shared.auth.exceptions import InvalidInternalSecretError, InvalidTokenError

_PUBLIC_KEY_CACHE: dict[int, object] = {}


def _settings_id(settings: Any) -> int:
    return id(settings)


def get_key_and_alg(settings: Any) -> tuple[object, list[str]]:
    jwt_public_key = getattr(settings, "JWT_PUBLIC_KEY", "")
    if jwt_public_key:
        key_cache_id = _settings_id(settings)
        if key_cache_id not in _PUBLIC_KEY_CACHE:
            from cryptography.hazmat.primitives.serialization import load_pem_public_key

            _PUBLIC_KEY_CACHE[key_cache_id] = load_pem_public_key(jwt_public_key.encode())
        return _PUBLIC_KEY_CACHE[key_cache_id], ["RS256"]

    jwt_secret = getattr(settings, "JWT_SECRET_KEY", "")
    jwt_algorithm = getattr(settings, "JWT_ALGORITHM", "HS256")
    return jwt_secret, [jwt_algorithm]


def decode_jwt(token: str, settings: Any) -> dict:
    key, algorithms = get_key_and_alg(settings)
    jwt_issuer = getattr(settings, "JWT_ISSUER", None)
    kwargs: dict[str, Any] = {
        "algorithms": algorithms,
        "options": {"require": ["exp"]},
    }
    if jwt_issuer:
        kwargs["issuer"] = jwt_issuer

    try:
        payload = jwt.decode(token, key, **kwargs)
    except JWTError as exc:
        raise InvalidTokenError("Token inválido") from exc

    if payload.get("sub") is None:
        raise InvalidTokenError("Token inválido")

    return payload


def verify_internal_secret(provided: str, expected: str) -> None:
    if not hmac.compare_digest(provided or "", expected or ""):
        raise InvalidInternalSecretError("Invalid internal secret")
