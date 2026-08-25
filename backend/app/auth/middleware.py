"""Authentication middleware.

Resolves the caller for every HTTP request, enforces the route policy in
`policy.py`, and writes an audit row for anything that changed state.

Two behaviours worth being explicit about:

**Authentication activates when an admin PIN is set.** Before that, the
API is open and says so loudly in the logs and in `GET /`. This keeps
`setup.ps1` and every existing install working, and makes turning
security on a deliberate act rather than something that silently breaks
a service on upgrade. `VERSESYNC_REQUIRE_AUTH=true` makes the server
refuse to serve anything but the bootstrap endpoints until a PIN exists,
for operators who want fail-closed behaviour.

**A path with no rule is denied.** Adding a route without adding a policy
entry produces a 403 rather than an accidentally public endpoint.
"""
from __future__ import annotations

import logging

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.auth import audit, policy, service
from app.auth.tokens import TokenError, extract_bearer
from app.core.config import settings

logger = logging.getLogger(__name__)


def _client_host(request: Request) -> str | None:
    client = request.scope.get("client")
    return client[0] if client else None


def _extract_token(request: Request) -> str | None:
    """Find a token on the request.

    Header first. The query string is a fallback that exists only because
    OBS Browser Sources and browser WebSockets cannot set headers; it is
    not the preferred path and the token will appear in server logs, so
    the README says as much.
    """
    token = extract_bearer(request.headers.get("authorization"))
    if token:
        return token
    return request.query_params.get("token") or None


def _deny(status: int, message: str, *, hint: str = "") -> JSONResponse:
    body = {"detail": message}
    if hint:
        body["hint"] = hint
    headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
    return JSONResponse(body, status_code=status, headers=headers)


class AuthMiddleware(BaseHTTPMiddleware):
    """Enforces the route policy on every HTTP request."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path.rstrip("/") or "/"
        method = request.method

        # CORS preflight carries no credentials by definition.
        if method == "OPTIONS":
            return await call_next(request)

        request.state.principal = None
        request.state.auth_action = ""

        configured = service.is_configured()
        rule = policy.find_rule(path, method)

        # Unknown path: let FastAPI produce its own 404 rather than
        # inventing a 403 for something that does not exist.
        if rule is None:
            if _route_exists(request, path):
                logger.error(
                    "No auth policy for %s %s; denying. Add a Rule in "
                    "app/auth/policy.py.", method, path,
                )
                return _deny(403, f"No access policy is defined for {path}.")
            return await call_next(request)

        request.state.auth_action = rule.action

        # --- not configured yet: open, unless told to fail closed ---
        if not configured:
            if settings.require_auth and rule.required_role is not None:
                return _deny(
                    503,
                    "VerseSync is configured to require authentication but no "
                    "admin PIN has been set.",
                    hint="Call POST /auth/setup-pin to finish setup.",
                )
            return await call_next(request)

        # --- public routes ---
        if rule.required_role is None:
            return await self._proceed(request, call_next, rule, None)

        # --- display surfaces stay open unless locked down ---
        if rule.display_surface and settings.public_projector:
            # Still resolve a token if one was supplied, so the audit log
            # can name the device.
            principal = self._try_authenticate(request)
            return await self._proceed(request, call_next, rule, principal)

        # --- everything else needs a valid, approved device ---
        token = _extract_token(request)
        if not token:
            return _deny(
                401,
                "This endpoint requires a device token.",
                hint="Send 'Authorization: Bearer <token>'. Register with "
                     "POST /auth/register-device, then have an admin approve "
                     "the code with POST /auth/approve-device.",
            )

        try:
            principal = service.authenticate(token)
        except TokenError as exc:
            audit.record("auth.denied", actor_type="anonymous",
                         method=method, path=path, status_code=401,
                         source=_client_host(request),
                         detail={"reason": str(exc)})
            return _deny(401, str(exc))

        if not principal.is_approved:
            return _deny(
                403,
                "This device is registered but not yet approved.",
                hint="Read the 6-digit code to an admin and have them call "
                     "POST /auth/approve-device.",
            )

        if not principal.has_role(rule.required_role):
            audit.record("auth.denied", actor_type="device",
                         actor_id=principal.device_id,
                         actor_name=principal.name, role=principal.role,
                         method=method, path=path, status_code=403,
                         source=_client_host(request),
                         detail={"required_role": rule.required_role})
            return _deny(
                403,
                f"This action requires the '{rule.required_role}' role; "
                f"this device has '{principal.role}'.",
            )

        return await self._proceed(request, call_next, rule, principal)

    # ------------------------------------------------------------------

    def _try_authenticate(self, request: Request):
        """Best-effort principal resolution; never blocks the request."""
        token = _extract_token(request)
        if not token:
            return None
        try:
            return service.authenticate(token)
        except TokenError:
            return None

    async def _proceed(self, request: Request, call_next, rule, principal):
        request.state.principal = principal
        response = await call_next(request)

        if rule.action in policy.AUDITED_ACTIONS:
            if principal is not None:
                audit.record(
                    rule.action, actor_type="device",
                    actor_id=principal.device_id, actor_name=principal.name,
                    role=principal.role, method=request.method,
                    path=request.url.path, status_code=response.status_code,
                    source=_client_host(request),
                )
            else:
                audit.record(
                    rule.action, actor_type="anonymous",
                    method=request.method, path=request.url.path,
                    status_code=response.status_code,
                    source=_client_host(request),
                )
        return response


def _route_exists(request: Request, path: str) -> bool:
    """Does the app actually serve this path?

    Used to distinguish "route exists but has no policy" (a bug worth
    shouting about) from "no such route" (an ordinary 404).
    """
    app = request.scope.get("app")
    if app is None:
        return False
    for route in getattr(app, "routes", []):
        matcher = getattr(route, "path_regex", None)
        if matcher is not None and matcher.fullmatch(path):
            return True
        if getattr(route, "path", None) == path:
            return True
    return False


async def authenticate_websocket(websocket, required_role: str = "projector"):
    """Authenticate a WebSocket connection.

    Starlette runs HTTP middleware for WebSocket handshakes in some
    versions and not others, so the WebSocket route calls this directly
    rather than relying on the middleware.

    Returns a Principal, or None when the display surface is public.
    Raises PermissionError when the connection should be refused.
    """
    if not service.is_configured():
        return None
    if settings.public_projector:
        return None

    token = (websocket.query_params.get("token")
             or extract_bearer(websocket.headers.get("authorization")))
    if not token:
        raise PermissionError(
            "A device token is required. Append ?token=<token> to the "
            "WebSocket URL; browser WebSocket clients cannot send headers."
        )
    try:
        principal = service.authenticate(token)
    except TokenError as exc:
        raise PermissionError(str(exc)) from exc

    if not principal.is_approved:
        raise PermissionError("This device is not approved yet.")
    if not principal.has_role(required_role):
        raise PermissionError(
            f"This action requires the '{required_role}' role."
        )
    return principal
