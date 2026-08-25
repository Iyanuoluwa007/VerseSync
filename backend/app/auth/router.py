"""Authentication endpoints.

    GET  /auth/status           is a PIN set, how many devices, lockout state
    POST /auth/setup-pin        first run only: set the admin PIN
    POST /auth/change-pin       rotate the PIN (invalidates every device)
    POST /auth/login            check the PIN; used by an admin UI
    POST /auth/register-device  device asks to join, gets a 6-digit code
    POST /auth/approve-device   admin approves a code and assigns a role
    POST /auth/revoke-device    admin revokes a device, effective immediately
    POST /auth/clear-lockout    admin clears the failed-PIN counter
    GET  /auth/devices          admin: list devices
    GET  /auth/audit            admin: read the audit log
    GET  /auth/me               what this token is

The admin actions authenticate with the PIN in the request body rather
than a bearer token, because the person approving a device is standing
at the machine with the PIN, and may not have a device of their own yet.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth import audit, service
from app.auth.passwords import MAX_PIN_LENGTH, MIN_PIN_LENGTH, PinPolicyError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def _source(request: Request) -> str | None:
    client = request.scope.get("client")
    return client[0] if client else None


def _handle_pin_errors(exc: Exception) -> HTTPException:
    """Map auth exceptions to HTTP responses with actionable detail."""
    if isinstance(exc, service.LockedOutError):
        return HTTPException(
            status_code=429,
            detail=str(exc),
            headers={"Retry-After": str(exc.retry_after_seconds)},
        )
    if isinstance(exc, service.InvalidPinError):
        return HTTPException(status_code=401, detail=str(exc))
    if isinstance(exc, service.NotConfiguredError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, service.AlreadyConfiguredError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, PinPolicyError):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, service.DeviceError):
        return HTTPException(status_code=400, detail=str(exc))
    raise exc


# ---------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------

class PinBody(BaseModel):
    pin: str = Field(..., min_length=MIN_PIN_LENGTH, max_length=MAX_PIN_LENGTH,
                     description="The admin PIN")


class ChangePinBody(BaseModel):
    current_pin: str = Field(..., min_length=1, max_length=MAX_PIN_LENGTH)
    new_pin: str = Field(..., min_length=MIN_PIN_LENGTH,
                         max_length=MAX_PIN_LENGTH)


class RegisterDeviceBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=64,
                      description="Human-readable, e.g. 'Sanctuary projector'")
    role: str = Field("projector",
                      description="Role to request: projector | operator | admin. "
                                  "The approving admin decides the actual role.")


class ApproveDeviceBody(BaseModel):
    pin: str = Field(..., min_length=1, max_length=MAX_PIN_LENGTH)
    code: str = Field(..., min_length=6, max_length=6,
                      description="The 6-digit code the device displayed")
    role: str = Field("projector",
                      description="projector | operator | admin")


class RevokeDeviceBody(BaseModel):
    pin: str = Field(..., min_length=1, max_length=MAX_PIN_LENGTH)
    device_id: str = Field(..., min_length=1, max_length=64)


# ---------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------

@router.get("/status")
def auth_status() -> dict[str, Any]:
    """Whether authentication is configured. Safe to call unauthenticated."""
    summary = service.status_summary()
    if not summary["configured"]:
        summary["warning"] = (
            "No admin PIN is set, so every endpoint is open to anyone who "
            "can reach this server. Call POST /auth/setup-pin."
        )
    return summary


@router.post("/setup-pin", status_code=201)
def setup_pin(body: PinBody, request: Request) -> dict[str, Any]:
    """Set the admin PIN. Works exactly once.

    Unauthenticated by necessity, and single-use by design: once a PIN
    exists this endpoint refuses, so it cannot be used to take over a
    running installation.
    """
    try:
        service.setup_pin(body.pin, source=_source(request))
    except Exception as exc:
        raise _handle_pin_errors(exc) from exc
    return {
        "status": "configured",
        "message": "Admin PIN set. Authentication is now enforced.",
        "next": "Register a device with POST /auth/register-device, then "
                "approve it with POST /auth/approve-device.",
    }


@router.post("/change-pin")
def change_pin(body: ChangePinBody, request: Request) -> dict[str, Any]:
    """Rotate the admin PIN. Logs out every device."""
    try:
        service.change_pin(body.current_pin, body.new_pin,
                           source=_source(request))
    except Exception as exc:
        raise _handle_pin_errors(exc) from exc
    return {
        "status": "changed",
        "message": "Admin PIN changed. Every device token has been "
                   "invalidated and devices must be re-approved.",
    }


@router.post("/login")
def login(body: PinBody, request: Request) -> dict[str, Any]:
    """Verify the admin PIN.

    Returns no token: the PIN authorises admin actions directly, and
    minting a long-lived admin token from a 6-digit PIN would widen the
    blast radius of that PIN considerably.
    """
    try:
        service.verify_admin_pin(body.pin, source=_source(request))
    except Exception as exc:
        raise _handle_pin_errors(exc) from exc
    return {"status": "ok", "message": "PIN accepted."}


@router.post("/register-device", status_code=201)
def register_device(body: RegisterDeviceBody,
                    request: Request) -> dict[str, Any]:
    """Ask to join. Returns a 6-digit code to read to an admin."""
    try:
        device, code, token = service.register_device(
            body.name, requested_role=body.role, source=_source(request),
        )
    except Exception as exc:
        raise _handle_pin_errors(exc) from exc
    return {
        "status": "pending",
        "device": device.to_dict(),
        "approval_code": code,
        "pending_token": token,
        "message": "Read the approval code to an admin. This device can do "
                   "nothing until it is approved.",
    }


@router.post("/approve-device")
def approve_device(body: ApproveDeviceBody,
                   request: Request) -> dict[str, Any]:
    """Approve a pending device and give it a role. Requires the PIN."""
    source = _source(request)
    try:
        service.verify_admin_pin(body.pin, source=source)
        device, token = service.approve_device(
            body.code, role=body.role, source=source,
        )
    except Exception as exc:
        raise _handle_pin_errors(exc) from exc
    return {
        "status": "approved",
        "device": device.to_dict(),
        "access_token": token,
        "token_type": "bearer",
        "message": "Give this token to the device. It replaces the pending "
                   "token issued at registration.",
    }


@router.post("/revoke-device")
def revoke_device(body: RevokeDeviceBody, request: Request) -> dict[str, Any]:
    """Revoke a device. Takes effect on its next request."""
    source = _source(request)
    try:
        service.verify_admin_pin(body.pin, source=source)
        device = service.revoke_device(body.device_id, source=source)
    except Exception as exc:
        raise _handle_pin_errors(exc) from exc
    return {
        "status": "revoked",
        "device": device.to_dict(),
        "message": "Every token for this device is now invalid.",
    }


@router.post("/clear-lockout")
def clear_lockout(request: Request) -> dict[str, Any]:
    """Clear the failed-PIN counter. Requires an admin device token.

    Deliberately NOT authenticated with the PIN. The PIN is the thing
    that gets locked out, so a PIN-gated escape hatch would only work
    when it was not needed. An admin device that was approved before the
    lockout can rescue an operator who mistyped the PIN five times, with
    no way for an attacker to use it: they would need an approved admin
    device already.
    """
    principal = getattr(request.state, "principal", None)
    service.clear_lockout()
    audit.record(
        "auth.clear_lockout", actor_type="device",
        actor_id=getattr(principal, "device_id", None),
        actor_name=getattr(principal, "name", None),
        role=getattr(principal, "role", None),
        source=_source(request),
    )
    return {
        "status": "cleared",
        "message": "Failed PIN attempts cleared. The admin PIN can be used "
                   "again immediately.",
    }


@router.get("/devices")
def list_devices() -> dict[str, Any]:
    """List devices. Requires an admin device token."""
    return {"devices": [d.to_dict() for d in service.list_devices()]}


@router.get("/audit")
def read_audit(limit: int = 100, offset: int = 0,
               action: str | None = None,
               actor_id: str | None = None) -> dict[str, Any]:
    """Read the audit log, newest first. Requires an admin device token."""
    entries = audit.query(limit=limit, offset=offset,
                          action=action, actor_id=actor_id)
    return {
        "total": audit.count(),
        "count": len(entries),
        "entries": [e.to_dict() for e in entries],
    }


@router.get("/me")
def whoami(request: Request) -> dict[str, Any]:
    """Describe the device this token belongs to."""
    principal = getattr(request.state, "principal", None)
    if principal is None:
        raise HTTPException(
            status_code=401,
            detail="No device token supplied.",
        )
    return principal.to_dict()
