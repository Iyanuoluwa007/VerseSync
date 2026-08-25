"""Device token issue and verification.

Uses PyJWT rather than a hand-rolled implementation. VerseSync implements
the obs-websocket protocol itself to avoid a dependency, but that is a
wire format; this is authentication, and the failure modes of hand-rolled
JWT (algorithm confusion, `alg: none`, non-constant-time comparison) are
exactly the ones that do not announce themselves.

Two hardening choices worth stating:

* `algorithms=["HS256"]` is passed explicitly on decode. Without it a
  token can nominate its own algorithm, which is the classic JWT
  vulnerability.
* Tokens carry a `ver` claim matched against the device's
  `token_version` column. The plain-JWT design in the Phase 0 plan could
  not revoke a device before its token expired; bumping `token_version`
  on revoke invalidates every outstanding token for that device on the
  next request. `/auth/revoke-device` has to mean "now".
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

logger = logging.getLogger(__name__)

ALGORITHM = "HS256"
ISSUER = "versesync"

# A device is a projector in a building, not a browser session. Long
# expiry, with revocation as the real control.
DEFAULT_TTL = timedelta(days=30)
# A device awaiting approval only needs to survive the walk across the
# room to the person with the admin PIN.
PENDING_TTL = timedelta(minutes=30)


class TokenError(Exception):
    """The token is missing, malformed, expired, or not ours."""


@dataclass(frozen=True)
class TokenClaims:
    device_id: str
    role: str
    status: str
    token_version: int
    expires_at: datetime

    @property
    def is_pending(self) -> bool:
        return self.status == "pending"


def issue_token(*, device_id: str, role: str, status: str,
                token_version: int, signing_key: str,
                ttl: timedelta | None = None) -> str:
    """Sign a device token."""
    if ttl is None:
        ttl = PENDING_TTL if status == "pending" else DEFAULT_TTL

    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "iss": ISSUER,
        "sub": device_id,
        "role": role,
        "st": status,
        "ver": token_version,
        "iat": now,
        "nbf": now,
        "exp": now + ttl,
    }
    return jwt.encode(payload, signing_key, algorithm=ALGORITHM)


def decode_token(token: str, signing_key: str) -> TokenClaims:
    """Verify a token's signature and claims. Raises TokenError."""
    if not token or not isinstance(token, str):
        raise TokenError("No token supplied")

    try:
        payload = jwt.decode(
            token,
            signing_key,
            # Explicit, so a token cannot nominate its own algorithm.
            algorithms=[ALGORITHM],
            issuer=ISSUER,
            options={
                "require": ["exp", "iat", "nbf", "sub", "iss"],
                "verify_signature": True,
                "verify_exp": True,
                "verify_nbf": True,
                "verify_iss": True,
            },
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("Token has expired") from exc
    except jwt.InvalidIssuerError as exc:
        raise TokenError("Token was not issued by this server") from exc
    except jwt.InvalidTokenError as exc:
        # Covers bad signature, alg mismatch, malformed, missing claims.
        raise TokenError(f"Invalid token: {exc}") from exc

    device_id = payload.get("sub")
    role = payload.get("role")
    status = payload.get("st")
    version = payload.get("ver")

    if not isinstance(device_id, str) or not device_id:
        raise TokenError("Token has no device id")
    if role not in ("admin", "operator", "projector"):
        raise TokenError("Token has an unknown role")
    if status not in ("pending", "approved"):
        raise TokenError("Token has an unknown status")
    if not isinstance(version, int):
        raise TokenError("Token has no version")

    return TokenClaims(
        device_id=device_id,
        role=role,
        status=status,
        token_version=version,
        expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
    )


def extract_bearer(header_value: str | None) -> str | None:
    """Pull the token out of an `Authorization: Bearer <token>` header."""
    if not header_value:
        return None
    parts = header_value.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None
