"""Append-only audit log.

Every authentication event and every state-changing request lands here.
The design rule is that writing an audit row must never be able to break
the request it is recording: a failure to log is logged to stderr and
swallowed. An audit trail that can take down a live service is worse than
no audit trail.

Nothing secret is ever written. `detail` is JSON assembled from an
explicit allowlist of fields at each call site, never from a whole
request body, so a PIN or a token cannot end up on disk by accident.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.auth.db import auth_connection

logger = logging.getLogger(__name__)

# Fields we will never write, whatever a caller passes.
_FORBIDDEN_KEYS = frozenset({
    "pin", "password", "token", "access_token", "signing_key",
    "authorization", "secret", "api_key", "pin_hash",
})


@dataclass(frozen=True)
class AuditEntry:
    id: int
    ts: str
    actor_type: str
    actor_id: str | None
    actor_name: str | None
    role: str | None
    action: str
    method: str | None
    path: str | None
    status_code: int | None
    source: str | None
    detail: dict | None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "ts": self.ts,
            "actor_type": self.actor_type,
            "actor_id": self.actor_id,
            "actor_name": self.actor_name,
            "role": self.role,
            "action": self.action,
            "method": self.method,
            "path": self.path,
            "status_code": self.status_code,
            "source": self.source,
            "detail": self.detail,
        }


def _scrub(detail: dict | None) -> str | None:
    """Drop anything secret-shaped, then serialise."""
    if not detail:
        return None
    safe = {
        key: value for key, value in detail.items()
        if key.lower() not in _FORBIDDEN_KEYS
    }
    if not safe:
        return None
    try:
        return json.dumps(safe, default=str, ensure_ascii=False)[:4000]
    except (TypeError, ValueError):
        return None


def record(action: str, *,
           actor_type: str = "anonymous",
           actor_id: str | None = None,
           actor_name: str | None = None,
           role: str | None = None,
           method: str | None = None,
           path: str | None = None,
           status_code: int | None = None,
           source: str | None = None,
           detail: dict | None = None,
           conn: sqlite3.Connection | None = None) -> None:
    """Write one audit row. Never raises."""
    try:
        own = conn is None
        connection = conn or auth_connection()
        try:
            connection.execute(
                """INSERT INTO audit_log
                       (ts, actor_type, actor_id, actor_name, role, action,
                        method, path, status_code, source, detail)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    datetime.now(UTC).isoformat(),
                    actor_type, actor_id, actor_name, role, action,
                    method, path, status_code, source, _scrub(detail),
                ),
            )
        finally:
            if own:
                connection.close()
    except Exception:
        # An audit failure must not propagate into the request path.
        logger.exception("Failed to write audit entry for %r", action)


def query(*, limit: int = 100, offset: int = 0,
          action: str | None = None,
          actor_id: str | None = None,
          conn: sqlite3.Connection | None = None) -> list[AuditEntry]:
    """Read the audit log, newest first."""
    limit = max(1, min(int(limit), 1000))
    offset = max(0, int(offset))

    clauses: list[str] = []
    params: list[Any] = []
    if action:
        # Prefix match, so "auth" returns every auth.* action.
        clauses.append("action LIKE ?")
        params.append(f"{action}%")
    if actor_id:
        clauses.append("actor_id = ?")
        params.append(actor_id)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.extend([limit, offset])

    own = conn is None
    connection = conn or auth_connection()
    try:
        rows = connection.execute(
            f"""SELECT id, ts, actor_type, actor_id, actor_name, role, action,
                       method, path, status_code, source, detail
                  FROM audit_log
                  {where}
                 ORDER BY id DESC
                 LIMIT ? OFFSET ?""",
            params,
        ).fetchall()
    finally:
        if own:
            connection.close()

    entries: list[AuditEntry] = []
    for row in rows:
        raw = row["detail"]
        try:
            parsed = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            parsed = None
        entries.append(AuditEntry(
            id=row["id"], ts=row["ts"], actor_type=row["actor_type"],
            actor_id=row["actor_id"], actor_name=row["actor_name"],
            role=row["role"], action=row["action"], method=row["method"],
            path=row["path"], status_code=row["status_code"],
            source=row["source"], detail=parsed,
        ))
    return entries


def count(conn: sqlite3.Connection | None = None) -> int:
    own = conn is None
    connection = conn or auth_connection()
    try:
        return connection.execute(
            "SELECT COUNT(*) AS n FROM audit_log").fetchone()["n"]
    finally:
        if own:
            connection.close()
