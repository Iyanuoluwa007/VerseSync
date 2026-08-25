"""Auth business logic.

The shape of the system:

* One **admin PIN**, set once on first run. Whoever knows it can approve
  and revoke devices. It is not a user account; there are no users.
* **Devices** register themselves and get a 6-digit code plus a pending
  token. The code is read out to whoever holds the PIN, who approves the
  device and assigns it a role. Until then the device can do nothing.
* **Revocation is immediate**, because every request re-checks the
  device row rather than trusting the token alone.

Roles, least privilege first:

| role      | can |
|-----------|-----|
| projector | receive verses; nothing else |
| operator  | drive the projector and the STT pipeline |
| admin     | everything, including approving and revoking devices |
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.auth import audit
from app.auth.db import auth_connection, transaction
from app.auth.passwords import (
    PinPolicyError,
    generate_approval_code,
    generate_signing_key,
    hash_pin,
    validate_pin,
    verify_pin,
)
from app.auth.tokens import TokenClaims, TokenError, decode_token, issue_token
from app.core.config import settings

logger = logging.getLogger(__name__)

ROLES = ("admin", "operator", "projector")
ROLE_RANK = {"projector": 0, "operator": 1, "admin": 2}

# 5 wrong PINs inside 15 minutes locks the admin PIN for the same window.
MAX_PIN_ATTEMPTS = 5
LOCKOUT_WINDOW = timedelta(minutes=15)

# A registration code is only useful for as long as it takes to walk
# across the room.
CODE_TTL = timedelta(minutes=15)

MAX_PENDING_DEVICES = 20


class AuthError(Exception):
    """Base class for auth failures."""


class NotConfiguredError(AuthError):
    """No admin PIN has been set yet."""


class AlreadyConfiguredError(AuthError):
    """An admin PIN already exists."""


class LockedOutError(AuthError):
    """Too many failed PIN attempts."""

    def __init__(self, retry_after_seconds: int):
        self.retry_after_seconds = retry_after_seconds
        super().__init__(
            f"Too many incorrect PIN attempts. Locked for "
            f"{retry_after_seconds} more seconds."
        )


class InvalidPinError(AuthError):
    """The PIN was wrong."""

    def __init__(self, attempts_remaining: int):
        self.attempts_remaining = attempts_remaining
        super().__init__(
            f"Incorrect PIN. {attempts_remaining} attempt(s) remaining "
            f"before lockout."
        )


class DeviceError(AuthError):
    """A device operation failed."""


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(moment: datetime | None = None) -> str:
    return (moment or _now()).isoformat()


@dataclass(frozen=True)
class Device:
    id: str
    name: str
    role: str
    status: str
    token_version: int
    created_at: str
    approved_at: str | None
    revoked_at: str | None
    last_seen_at: str | None
    has_pending_code: bool

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "status": self.status,
            "created_at": self.created_at,
            "approved_at": self.approved_at,
            "revoked_at": self.revoked_at,
            "last_seen_at": self.last_seen_at,
            "awaiting_approval": self.has_pending_code,
        }


def _device_from_row(row: sqlite3.Row) -> Device:
    return Device(
        id=row["id"], name=row["name"], role=row["role"],
        status=row["status"], token_version=row["token_version"],
        created_at=row["created_at"], approved_at=row["approved_at"],
        revoked_at=row["revoked_at"], last_seen_at=row["last_seen_at"],
        has_pending_code=bool(row["approval_code"]),
    )


# ---------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------

# The middleware asks "is a PIN set?" on every single request, and each
# answer cost a fresh SQLite connection plus a query -- about 0.6 ms, or
# a third of the total time on the fast paths.
#
# The flag is monotonic: nothing un-configures an installation, so a True
# can be cached for the life of the process. A False is re-checked, but
# no more often than _UNCONFIGURED_RECHECK_S, which bounds the cost while
# still noticing setup performed by another process.
_configured_cache: dict[str, bool] = {}
_unconfigured_checked_at: dict[str, float] = {}
_UNCONFIGURED_RECHECK_S = 2.0
_cache_lock = threading.Lock()


def invalidate_configured_cache() -> None:
    """Forget the cached PIN-is-set flag. Called on setup, and by tests."""
    with _cache_lock:
        _configured_cache.clear()
        _unconfigured_checked_at.clear()


def is_configured(conn: sqlite3.Connection | None = None) -> bool:
    """True once an admin PIN has been set."""
    if conn is not None:
        # An explicit connection means a caller inside a transaction or a
        # test with its own database; never serve those from the cache.
        return conn.execute(
            "SELECT 1 FROM auth_config WHERE id = 1").fetchone() is not None

    key = str(settings.db_path)
    now = time.monotonic()
    with _cache_lock:
        if _configured_cache.get(key):
            return True
        last = _unconfigured_checked_at.get(key)
        if last is not None and (now - last) < _UNCONFIGURED_RECHECK_S:
            return False

    c = auth_connection()
    try:
        found = c.execute(
            "SELECT 1 FROM auth_config WHERE id = 1").fetchone() is not None
    finally:
        c.close()

    with _cache_lock:
        if found:
            _configured_cache[key] = True
            _unconfigured_checked_at.pop(key, None)
        else:
            _unconfigured_checked_at[key] = now
    return found


def get_signing_key(conn: sqlite3.Connection | None = None) -> str:
    own = conn is None
    c = conn or auth_connection()
    try:
        row = c.execute(
            "SELECT signing_key FROM auth_config WHERE id = 1").fetchone()
        if row is None:
            raise NotConfiguredError(
                "No admin PIN has been set. Call POST /auth/setup-pin first."
            )
        return row["signing_key"]
    finally:
        if own:
            c.close()


def setup_pin(pin: str, *, source: str | None = None,
              conn: sqlite3.Connection | None = None) -> None:
    """Set the admin PIN. First run only.

    Refuses if a PIN already exists: this endpoint is unauthenticated by
    necessity, so allowing it twice would let anyone on the network seize
    the admin role at any time.
    """
    own = conn is None
    c = conn or auth_connection()
    try:
        if is_configured(c):
            raise AlreadyConfiguredError(
                "An admin PIN is already set. To change it use "
                "POST /auth/change-pin with the current PIN."
            )
        validate_pin(pin)      # raises PinPolicyError with a usable message
        now = _iso()
        with transaction(c):
            c.execute(
                """INSERT INTO auth_config
                       (id, pin_hash, signing_key, created_at, updated_at)
                   VALUES (1, ?, ?, ?, ?)""",
                (hash_pin(pin), generate_signing_key(), now, now),
            )
        invalidate_configured_cache()
        audit.record("auth.setup_pin", actor_type="admin",
                     source=source, conn=c)
        logger.info("Admin PIN configured; authentication is now enforced")
    finally:
        if own:
            c.close()


def change_pin(current_pin: str, new_pin: str, *,
               source: str | None = None,
               conn: sqlite3.Connection | None = None) -> None:
    """Rotate the admin PIN. Requires the current one.

    Also rotates the signing key, which logs every device out. That is
    the intended behaviour: you change the PIN because you think it is
    compromised, and a compromised PIN means every device it approved is
    suspect.
    """
    own = conn is None
    c = conn or auth_connection()
    try:
        verify_admin_pin(current_pin, source=source, conn=c)
        validate_pin(new_pin)
        with transaction(c):
            c.execute(
                """UPDATE auth_config
                      SET pin_hash = ?, signing_key = ?, updated_at = ?
                    WHERE id = 1""",
                (hash_pin(new_pin), generate_signing_key(), _iso()),
            )
            c.execute("UPDATE devices SET token_version = token_version + 1")
        audit.record("auth.change_pin", actor_type="admin", source=source,
                     detail={"devices_invalidated": True}, conn=c)
        logger.warning("Admin PIN changed; all device tokens invalidated")
    finally:
        if own:
            c.close()


# ---------------------------------------------------------------------
# PIN verification and lockout
# ---------------------------------------------------------------------

def _recent_failures(c: sqlite3.Connection) -> int:
    cutoff = _iso(_now() - LOCKOUT_WINDOW)
    return c.execute(
        "SELECT COUNT(*) AS n FROM pin_attempts WHERE attempted_at > ?",
        (cutoff,),
    ).fetchone()["n"]


def lockout_status(conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """How close the admin PIN is to being locked out."""
    own = conn is None
    c = conn or auth_connection()
    try:
        failures = _recent_failures(c)
        locked = failures >= MAX_PIN_ATTEMPTS
        retry_after = 0
        if locked:
            oldest = c.execute(
                """SELECT attempted_at FROM pin_attempts
                    WHERE attempted_at > ?
                    ORDER BY attempted_at ASC LIMIT 1""",
                (_iso(_now() - LOCKOUT_WINDOW),),
            ).fetchone()
            if oldest:
                unlock_at = (datetime.fromisoformat(oldest["attempted_at"])
                             + LOCKOUT_WINDOW)
                retry_after = max(0, int((unlock_at - _now()).total_seconds()))
        return {
            "locked": locked,
            "failed_attempts": failures,
            "max_attempts": MAX_PIN_ATTEMPTS,
            "window_minutes": int(LOCKOUT_WINDOW.total_seconds() // 60),
            "retry_after_seconds": retry_after,
        }
    finally:
        if own:
            c.close()


def verify_admin_pin(pin: str, *, source: str | None = None,
                     conn: sqlite3.Connection | None = None) -> None:
    """Check the admin PIN, enforcing the lockout. Raises on failure.

    Failures are recorded before the answer is returned, so hammering the
    endpoint concurrently cannot outrun the counter.
    """
    own = conn is None
    c = conn or auth_connection()
    try:
        row = c.execute(
            "SELECT pin_hash FROM auth_config WHERE id = 1").fetchone()
        if row is None:
            raise NotConfiguredError(
                "No admin PIN has been set. Call POST /auth/setup-pin first."
            )

        status = lockout_status(c)
        if status["locked"]:
            audit.record("auth.login_blocked", actor_type="anonymous",
                         source=source,
                         detail={"reason": "locked_out",
                                 "retry_after_seconds":
                                     status["retry_after_seconds"]},
                         conn=c)
            raise LockedOutError(status["retry_after_seconds"])

        if verify_pin(pin, row["pin_hash"]):
            with transaction(c):
                # A correct PIN clears the failure history.
                c.execute("DELETE FROM pin_attempts")
            audit.record("auth.login", actor_type="admin", source=source,
                         conn=c)
            return

        with transaction(c):
            c.execute(
                "INSERT INTO pin_attempts (attempted_at, source) VALUES (?, ?)",
                (_iso(), source),
            )
        remaining = max(0, MAX_PIN_ATTEMPTS - _recent_failures(c))
        audit.record("auth.login_failed", actor_type="anonymous",
                     source=source,
                     detail={"attempts_remaining": remaining}, conn=c)
        if remaining == 0:
            raise LockedOutError(int(LOCKOUT_WINDOW.total_seconds()))
        raise InvalidPinError(remaining)
    finally:
        if own:
            c.close()


def clear_lockout(conn: sqlite3.Connection | None = None) -> None:
    """Clear the failed-attempt history. Admin-only escape hatch."""
    own = conn is None
    c = conn or auth_connection()
    try:
        with transaction(c):
            c.execute("DELETE FROM pin_attempts")
        audit.record("auth.clear_lockout", actor_type="admin", conn=c)
    finally:
        if own:
            c.close()


# ---------------------------------------------------------------------
# Device lifecycle
# ---------------------------------------------------------------------

def register_device(name: str, *, requested_role: str = "projector",
                    source: str | None = None,
                    conn: sqlite3.Connection | None = None
                    ) -> tuple[Device, str, str]:
    """Register a device. Returns (device, approval_code, pending_token).

    The requested role is recorded but not granted; the approving admin
    chooses the actual role. A device cannot promote itself.
    """
    name = (name or "").strip()
    if not name:
        raise DeviceError("Device name is required")
    if len(name) > 64:
        raise DeviceError("Device name must be 64 characters or fewer")
    if requested_role not in ROLES:
        raise DeviceError(f"Unknown role {requested_role!r}. "
                          f"Use one of {list(ROLES)}.")

    own = conn is None
    c = conn or auth_connection()
    try:
        if not is_configured(c):
            raise NotConfiguredError(
                "No admin PIN has been set. Call POST /auth/setup-pin first."
            )

        _expire_stale_codes(c)

        pending = c.execute(
            "SELECT COUNT(*) AS n FROM devices WHERE status = 'pending'"
        ).fetchone()["n"]
        if pending >= MAX_PENDING_DEVICES:
            # Stops an unauthenticated endpoint being used to fill the disk.
            raise DeviceError(
                f"Too many devices are already awaiting approval "
                f"({pending}). Approve or revoke some first."
            )

        device_id = str(uuid.uuid4())
        code = _unique_code(c)
        now = _now()

        with transaction(c):
            c.execute(
                """INSERT INTO devices
                       (id, name, role, status, approval_code,
                        code_expires_at, token_version, created_at)
                   VALUES (?, ?, ?, 'pending', ?, ?, 1, ?)""",
                (device_id, name, requested_role, code,
                 _iso(now + CODE_TTL), _iso(now)),
            )

        row = c.execute("SELECT * FROM devices WHERE id = ?",
                        (device_id,)).fetchone()
        device = _device_from_row(row)

        token = issue_token(
            device_id=device_id, role=requested_role, status="pending",
            token_version=1, signing_key=get_signing_key(c),
        )

        audit.record("auth.device_registered", actor_type="device",
                     actor_id=device_id, actor_name=name,
                     role=requested_role, source=source,
                     detail={"requested_role": requested_role}, conn=c)
        return device, code, token
    finally:
        if own:
            c.close()


def _unique_code(c: sqlite3.Connection) -> str:
    """A 6-digit code not currently in use."""
    for _ in range(50):
        code = generate_approval_code()
        clash = c.execute(
            "SELECT 1 FROM devices WHERE approval_code = ?", (code,)
        ).fetchone()
        if clash is None:
            return code
    raise DeviceError("Could not allocate an approval code; try again.")


def _expire_stale_codes(c: sqlite3.Connection) -> None:
    """Drop codes that have timed out, so they cannot be used later."""
    with transaction(c):
        c.execute(
            """UPDATE devices
                  SET approval_code = NULL, code_expires_at = NULL
                WHERE status = 'pending'
                  AND code_expires_at IS NOT NULL
                  AND code_expires_at < ?""",
            (_iso(),),
        )


def approve_device(code: str, *, role: str = "projector",
                   source: str | None = None,
                   conn: sqlite3.Connection | None = None
                   ) -> tuple[Device, str]:
    """Approve a pending device by its code. Returns (device, token).

    The caller must already have verified the admin PIN.
    """
    code = (code or "").strip()
    if not code:
        raise DeviceError("Approval code is required")
    if role not in ROLES:
        raise DeviceError(f"Unknown role {role!r}. Use one of {list(ROLES)}.")

    own = conn is None
    c = conn or auth_connection()
    try:
        _expire_stale_codes(c)

        row = c.execute(
            "SELECT * FROM devices WHERE approval_code = ? AND status = 'pending'",
            (code,),
        ).fetchone()
        if row is None:
            audit.record("auth.approve_failed", actor_type="admin",
                         source=source, detail={"reason": "unknown_or_expired"},
                         conn=c)
            raise DeviceError(
                "No device is waiting with that code. Codes expire after "
                f"{int(CODE_TTL.total_seconds() // 60)} minutes; ask the "
                "device to register again."
            )

        device_id = row["id"]
        # Bump the version so the pending token cannot be reused as an
        # approved one; the device must exchange it for a real token.
        new_version = row["token_version"] + 1

        with transaction(c):
            c.execute(
                """UPDATE devices
                      SET status = 'approved', role = ?, approval_code = NULL,
                          code_expires_at = NULL, approved_at = ?,
                          token_version = ?
                    WHERE id = ?""",
                (role, _iso(), new_version, device_id),
            )

        updated = c.execute("SELECT * FROM devices WHERE id = ?",
                            (device_id,)).fetchone()
        device = _device_from_row(updated)

        token = issue_token(
            device_id=device_id, role=role, status="approved",
            token_version=new_version, signing_key=get_signing_key(c),
        )

        audit.record("auth.device_approved", actor_type="admin",
                     actor_id=device_id, actor_name=device.name, role=role,
                     source=source, detail={"granted_role": role}, conn=c)
        logger.info("Device %s (%s) approved as %s",
                    device.name, device_id[:8], role)
        return device, token
    finally:
        if own:
            c.close()


def revoke_device(device_id: str, *, source: str | None = None,
                  conn: sqlite3.Connection | None = None) -> Device:
    """Revoke a device. Takes effect on its very next request."""
    own = conn is None
    c = conn or auth_connection()
    try:
        row = c.execute("SELECT * FROM devices WHERE id = ?",
                        (device_id,)).fetchone()
        if row is None:
            raise DeviceError(f"No device with id {device_id!r}")

        with transaction(c):
            c.execute(
                """UPDATE devices
                      SET status = 'revoked', approval_code = NULL,
                          code_expires_at = NULL, revoked_at = ?,
                          token_version = token_version + 1
                    WHERE id = ?""",
                (_iso(), device_id),
            )

        updated = c.execute("SELECT * FROM devices WHERE id = ?",
                            (device_id,)).fetchone()
        device = _device_from_row(updated)
        audit.record("auth.device_revoked", actor_type="admin",
                     actor_id=device_id, actor_name=device.name,
                     source=source, conn=c)
        logger.warning("Device %s (%s) revoked", device.name, device_id[:8])
        return device
    finally:
        if own:
            c.close()


def list_devices(conn: sqlite3.Connection | None = None) -> list[Device]:
    own = conn is None
    c = conn or auth_connection()
    try:
        _expire_stale_codes(c)
        rows = c.execute(
            "SELECT * FROM devices ORDER BY created_at DESC").fetchall()
        return [_device_from_row(r) for r in rows]
    finally:
        if own:
            c.close()


# ---------------------------------------------------------------------
# Request-time authentication
# ---------------------------------------------------------------------

@dataclass(frozen=True)
class Principal:
    """Who is making this request."""
    device_id: str
    name: str
    role: str
    status: str

    @property
    def is_approved(self) -> bool:
        return self.status == "approved"

    def has_role(self, minimum: str) -> bool:
        return ROLE_RANK.get(self.role, -1) >= ROLE_RANK.get(minimum, 99)

    def to_dict(self) -> dict:
        return {
            "device_id": self.device_id,
            "name": self.name,
            "role": self.role,
            "status": self.status,
        }


def authenticate(token: str, *, conn: sqlite3.Connection | None = None,
                 touch: bool = True) -> Principal:
    """Resolve a token to a Principal. Raises TokenError if it is not valid.

    Every call re-reads the device row. That is what makes revocation
    immediate, and it is cheap: the database is local SQLite and the row
    is a primary-key lookup.
    """
    own = conn is None
    c = conn or auth_connection()
    try:
        try:
            signing_key = get_signing_key(c)
        except NotConfiguredError as exc:
            raise TokenError("Authentication is not configured") from exc

        claims: TokenClaims = decode_token(token, signing_key)

        row = c.execute("SELECT * FROM devices WHERE id = ?",
                        (claims.device_id,)).fetchone()
        if row is None:
            raise TokenError("Device no longer exists")
        if row["status"] == "revoked":
            raise TokenError("Device has been revoked")
        if row["token_version"] != claims.token_version:
            raise TokenError(
                "Token is no longer valid; the device must re-authenticate"
            )
        if row["status"] != claims.status:
            raise TokenError(
                "Token status is stale; the device must re-authenticate"
            )

        if touch:
            try:
                with transaction(c):
                    c.execute(
                        "UPDATE devices SET last_seen_at = ? WHERE id = ?",
                        (_iso(), claims.device_id),
                    )
            except sqlite3.Error:
                # A busy database must not fail an otherwise valid request.
                logger.debug("Could not update last_seen_at", exc_info=True)

        return Principal(
            device_id=row["id"], name=row["name"],
            role=row["role"], status=row["status"],
        )
    finally:
        if own:
            c.close()


def status_summary(conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Public description of the auth system's state.

    Safe to serve unauthenticated: it reveals whether a PIN is set and
    how many devices exist, never the PIN, the key, or any code.
    """
    own = conn is None
    c = conn or auth_connection()
    try:
        configured = is_configured(c)
        counts = {"pending": 0, "approved": 0, "revoked": 0}
        for row in c.execute(
                "SELECT status, COUNT(*) AS n FROM devices GROUP BY status"):
            counts[row["status"]] = row["n"]
        return {
            "configured": configured,
            "enforcing": configured,
            "devices": counts,
            "lockout": lockout_status(c) if configured else None,
        }
    finally:
        if own:
            c.close()


__all__ = [
    "ROLES", "ROLE_RANK", "Device", "Principal",
    "AuthError", "NotConfiguredError", "AlreadyConfiguredError",
    "LockedOutError", "InvalidPinError", "DeviceError", "PinPolicyError",
    "is_configured", "invalidate_configured_cache", "setup_pin", "change_pin", "verify_admin_pin",
    "lockout_status", "clear_lockout", "register_device", "approve_device",
    "revoke_device", "list_devices", "authenticate", "status_summary",
    "get_signing_key",
]
