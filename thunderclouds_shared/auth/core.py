from __future__ import annotations

import hashlib
import hmac
from typing import Any

from jose import JWTError, jwt

from thunderclouds_shared.auth.exceptions import (
    ImproperlyConfiguredError,
    InvalidInternalSecretError,
    InvalidTokenError,
)

# P2-59: keyed by a stable SHA-256 digest of the PEM content, not by id(settings).
# id() is only unique for the lifetime of the object; once an object is garbage-
# collected its id can be reused by a new object with a *different* key, causing
# the cache to return a stale value without any visible error.
_PUBLIC_KEY_CACHE: dict[str, object] = {}


def _public_key_cache_key(pem: str) -> str:
    """Return a stable, content-based cache key for a PEM string."""
    return hashlib.sha256(pem.encode()).hexdigest()


def get_key_and_alg(settings: Any) -> tuple[object, list[str]]:
    jwt_public_key = getattr(settings, "JWT_PUBLIC_KEY", "")
    if jwt_public_key:
        cache_key = _public_key_cache_key(jwt_public_key)
        if cache_key not in _PUBLIC_KEY_CACHE:
            from cryptography.hazmat.primitives.serialization import load_pem_public_key

            # P2-60: catch ValueError from a malformed PEM and surface it as a
            # clear configuration error instead of letting it propagate as a
            # generic 500 (decode_jwt only catches JWTError, not ValueError).
            try:
                _PUBLIC_KEY_CACHE[cache_key] = load_pem_public_key(jwt_public_key.encode())
            except ValueError as exc:
                raise ImproperlyConfiguredError(
                    "JWT_PUBLIC_KEY contains an invalid or malformed PEM — "
                    "check the service configuration."
                ) from exc
        return _PUBLIC_KEY_CACHE[cache_key], ["RS256"]

    jwt_secret = getattr(settings, "JWT_SECRET_KEY", "")
    jwt_algorithm = getattr(settings, "JWT_ALGORITHM", "HS256")
    return jwt_secret, [jwt_algorithm]


def decode_jwt(token: str, settings: Any) -> dict:
    key, algorithms = get_key_and_alg(settings)
    jwt_issuer = getattr(settings, "JWT_ISSUER", None)
    kwargs: dict[str, Any] = {
        "algorithms": algorithms,
        # python-jose usa options por-claim con prefijo `require_<claim>` (a
        # diferencia de PyJWT, que usa {"require": ["exp"]}) — con la clave
        # equivocada jose la ignora silenciosamente y el default require_exp=False
        # queda vigente, así que un token sin `exp` se decodifica igual.
        "options": {"require_exp": True},
    }
    # Con `issuer` seteado, jose rechaza tanto un `iss` incorrecto como un
    # token que directamente no trae el claim (anti token-confusion, M6):
    # todo JWT emitido por la plataforma debe declarar su emisor.
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
    # P1-16: if the service never configured INTERNAL_SECRET (expected is falsy),
    # hmac.compare_digest("", "") returns True and any request without the header
    # silently passes. Guard against this fail-open scenario by rejecting
    # immediately when the *expected* secret is not set — this is a configuration
    # error on the service side and must never authenticate anyone.
    if not expected:
        raise InvalidInternalSecretError("Invalid internal secret")
    if not hmac.compare_digest(provided or "", expected):
        raise InvalidInternalSecretError("Invalid internal secret")
