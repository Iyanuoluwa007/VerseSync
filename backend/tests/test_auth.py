"""Tests for Module 5: admin PIN, device tokens, roles and the audit log.

Structured around the acceptance criteria in docs/PHASE_0.md:
register -> approve -> use -> revoke, an audit trail covering every step,
and a lockout after 5 wrong PINs in 15 minutes.
"""
from __future__ import annotations

import base64
import dataclasses
import json
from datetime import UTC, datetime, timedelta

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient

from app.auth import audit, service
from app.auth import db as auth_db
from app.auth.passwords import (
    PinPolicyError,
    generate_approval_code,
    hash_pin,
    validate_pin,
    verify_pin,
)
from app.auth.tokens import TokenError, decode_token, issue_token
from app.core.events import hub

GOOD_PIN = "cornerstone-77"


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Point auth at a throwaway database for every test."""
    from app.core import config as config_module

    db_path = tmp_path / "auth.db"
    patched = dataclasses.replace(config_module.settings, db_path=db_path)
    monkeypatch.setattr(auth_db, "settings", patched)
    monkeypatch.setattr(config_module, "settings", patched)
    auth_db.reset_state()
    service.invalidate_configured_cache()
    yield db_path
    auth_db.reset_state()
    service.invalidate_configured_cache()
    hub.reset()


@pytest.fixture
def conn(isolated_db):
    c = auth_db.auth_connection(isolated_db)
    yield c
    c.close()


@pytest.fixture
def client(isolated_db, monkeypatch):
    """A TestClient whose middleware sees the isolated database."""
    from app.auth import middleware as mw
    from app.core import config as config_module
    from app.main import app

    patched = dataclasses.replace(config_module.settings, db_path=isolated_db)
    monkeypatch.setattr(mw, "settings", patched)
    with TestClient(app) as c:
        yield c


# =====================================================================
# PIN policy and hashing
# =====================================================================

def test_hash_and_verify_roundtrip():
    stored = hash_pin(GOOD_PIN)
    assert verify_pin(GOOD_PIN, stored) is True
    assert verify_pin("wrong-pin-here", stored) is False


def test_hash_is_salted():
    """Two hashes of the same PIN must differ, or the salt is not working."""
    assert hash_pin(GOOD_PIN) != hash_pin(GOOD_PIN)


def test_hash_is_self_describing():
    stored = hash_pin(GOOD_PIN)
    assert stored.startswith("scrypt$")
    assert len(stored.split("$")) == 6


def test_hash_does_not_contain_the_pin():
    assert GOOD_PIN not in hash_pin(GOOD_PIN)


@pytest.mark.parametrize("bad,reason", [
    ("12345", "too short"),
    ("111111", "repeated digit"),
    ("000000", "repeated digit"),
    ("123456", "sequence"),
    ("654321", "sequence"),
    ("456789", "sequence"),
    ("x" * 65, "too long"),
])
def test_weak_pins_are_rejected(bad, reason):
    with pytest.raises(PinPolicyError):
        validate_pin(bad)


@pytest.mark.parametrize("good", [
    "cornerstone-77", "918273", "correct horse battery", "Ẹ̀kọ́-2026",
])
def test_reasonable_pins_are_accepted(good):
    assert validate_pin(good) == good


def test_verify_rejects_malformed_hashes():
    for bad in ("", "not-a-hash", "scrypt$x$y$z", "argon2id$a$b$c$d$e",
                "scrypt$16384$8$1$notbase64$notbase64"):
        assert verify_pin(GOOD_PIN, bad) is False


def test_verify_rejects_absurd_cost_parameters():
    """A tampered row must not be able to demand gigabytes of memory."""
    stored = hash_pin(GOOD_PIN)
    parts = stored.split("$")
    parts[1] = str(2 ** 30)
    assert verify_pin(GOOD_PIN, "$".join(parts)) is False


def test_approval_codes_are_six_digits_and_varied():
    codes = {generate_approval_code() for _ in range(200)}
    assert all(len(c) == 6 and c.isdigit() for c in codes)
    # Not proof of randomness, but catches a constant or a tiny space.
    assert len(codes) > 150


# =====================================================================
# Tokens
# =====================================================================

def _token(key="k" * 32, **over):
    args = {"device_id": "dev-1", "role": "operator", "status": "approved",
            "token_version": 1, "signing_key": key}
    args.update(over)
    return issue_token(**args)


def test_token_roundtrip():
    claims = decode_token(_token(), "k" * 32)
    assert claims.device_id == "dev-1"
    assert claims.role == "operator"
    assert claims.token_version == 1


def test_token_signed_with_another_key_is_rejected():
    with pytest.raises(TokenError):
        decode_token(_token(), "different-key-entirely")


def test_tampered_token_is_rejected():
    token = _token()
    with pytest.raises(TokenError):
        decode_token(token[:-2] + "xy", "k" * 32)


def test_alg_none_token_is_rejected():
    """The classic JWT attack: an unsigned token claiming a role."""
    def b64(obj):
        return base64.urlsafe_b64encode(
            json.dumps(obj).encode()).rstrip(b"=").decode()

    forged = (b64({"alg": "none", "typ": "JWT"}) + "." +
              b64({"iss": "versesync", "sub": "dev-1", "role": "admin",
                   "st": "approved", "ver": 1, "iat": 0, "nbf": 0,
                   "exp": 9999999999}) + ".")
    with pytest.raises(TokenError):
        decode_token(forged, "k" * 32)


def test_expired_token_is_rejected():
    with pytest.raises(TokenError, match="expired"):
        decode_token(_token(ttl=timedelta(seconds=-10)), "k" * 32)


def test_token_from_another_issuer_is_rejected():
    foreign = pyjwt.encode(
        {"iss": "somebody-else", "sub": "dev-1", "role": "admin",
         "st": "approved", "ver": 1,
         "iat": datetime.now(UTC),
         "nbf": datetime.now(UTC),
         "exp": datetime.now(UTC) + timedelta(hours=1)},
        "k" * 32, algorithm="HS256")
    with pytest.raises(TokenError):
        decode_token(foreign, "k" * 32)


def test_token_with_unknown_role_is_rejected():
    with pytest.raises(TokenError, match="role"):
        decode_token(_token(role="superuser"), "k" * 32)


def test_token_missing_required_claims_is_rejected():
    thin = pyjwt.encode({"iss": "versesync", "sub": "dev-1"},
                        "k" * 32, algorithm="HS256")
    with pytest.raises(TokenError):
        decode_token(thin, "k" * 32)


def test_empty_token_is_rejected():
    with pytest.raises(TokenError):
        decode_token("", "k" * 32)


# =====================================================================
# Setup and lockout
# =====================================================================

def test_not_configured_initially(conn):
    assert service.is_configured(conn) is False


def test_setup_pin_configures(conn):
    service.setup_pin(GOOD_PIN, conn=conn)
    assert service.is_configured(conn) is True


def test_setup_pin_is_single_use(conn):
    service.setup_pin(GOOD_PIN, conn=conn)
    with pytest.raises(service.AlreadyConfiguredError):
        service.setup_pin("another-pin-42", conn=conn)


def test_verify_pin_before_setup_raises(conn):
    with pytest.raises(service.NotConfiguredError):
        service.verify_admin_pin(GOOD_PIN, conn=conn)


def test_correct_pin_verifies(conn):
    service.setup_pin(GOOD_PIN, conn=conn)
    service.verify_admin_pin(GOOD_PIN, conn=conn)   # must not raise


def test_wrong_pin_counts_down(conn):
    service.setup_pin(GOOD_PIN, conn=conn)
    for expected_remaining in (4, 3, 2, 1):
        with pytest.raises(service.InvalidPinError) as info:
            service.verify_admin_pin("wrong-pin-x", conn=conn)
        assert info.value.attempts_remaining == expected_remaining


def test_five_wrong_pins_locks_out(conn):
    """The acceptance criterion from the Phase 0 plan."""
    service.setup_pin(GOOD_PIN, conn=conn)
    for _ in range(4):
        with pytest.raises(service.InvalidPinError):
            service.verify_admin_pin("wrong-pin-x", conn=conn)
    with pytest.raises(service.LockedOutError):
        service.verify_admin_pin("wrong-pin-x", conn=conn)

    status = service.lockout_status(conn)
    assert status["locked"] is True
    assert status["failed_attempts"] == 5


def test_correct_pin_is_refused_during_lockout(conn):
    """Otherwise the lockout would not slow an attacker down at all."""
    service.setup_pin(GOOD_PIN, conn=conn)
    for _ in range(5):
        with pytest.raises(service.AuthError):
            service.verify_admin_pin("wrong-pin-x", conn=conn)
    with pytest.raises(service.LockedOutError):
        service.verify_admin_pin(GOOD_PIN, conn=conn)


def test_successful_pin_clears_failure_history(conn):
    service.setup_pin(GOOD_PIN, conn=conn)
    for _ in range(3):
        with pytest.raises(service.InvalidPinError):
            service.verify_admin_pin("wrong-pin-x", conn=conn)
    service.verify_admin_pin(GOOD_PIN, conn=conn)
    assert service.lockout_status(conn)["failed_attempts"] == 0


def test_old_failures_fall_out_of_the_window(conn):
    """Attempts older than the window must not count."""
    service.setup_pin(GOOD_PIN, conn=conn)
    stale = (datetime.now(UTC) - timedelta(minutes=30)).isoformat()
    for _ in range(10):
        conn.execute(
            "INSERT INTO pin_attempts (attempted_at, source) VALUES (?, ?)",
            (stale, "old"))
    assert service.lockout_status(conn)["locked"] is False
    service.verify_admin_pin(GOOD_PIN, conn=conn)


def test_lockout_survives_a_restart(isolated_db):
    """Held in the database, not in memory, so restarting is not an escape."""
    c1 = auth_db.auth_connection(isolated_db)
    service.setup_pin(GOOD_PIN, conn=c1)
    for _ in range(5):
        with pytest.raises(service.AuthError):
            service.verify_admin_pin("wrong-pin-x", conn=c1)
    c1.close()

    c2 = auth_db.auth_connection(isolated_db)
    try:
        assert service.lockout_status(c2)["locked"] is True
    finally:
        c2.close()


def test_change_pin_requires_the_current_one(conn):
    service.setup_pin(GOOD_PIN, conn=conn)
    with pytest.raises(service.InvalidPinError):
        service.change_pin("not-the-pin", "replacement-88", conn=conn)


def test_change_pin_rotates_and_invalidates_devices(conn):
    service.setup_pin(GOOD_PIN, conn=conn)
    _, code, _ = service.register_device("Screen", conn=conn)
    _, token = service.approve_device(code, role="operator", conn=conn)
    assert service.authenticate(token, conn=conn).role == "operator"

    service.change_pin(GOOD_PIN, "replacement-88", conn=conn)
    with pytest.raises(TokenError):
        service.authenticate(token, conn=conn)


# =====================================================================
# Device lifecycle
# =====================================================================

@pytest.fixture
def configured(conn):
    service.setup_pin(GOOD_PIN, conn=conn)
    return conn


def test_register_requires_a_configured_server(conn):
    with pytest.raises(service.NotConfiguredError):
        service.register_device("Screen", conn=conn)


def test_register_returns_code_and_pending_token(configured):
    device, code, token = service.register_device("Screen", conn=configured)
    assert device.status == "pending"
    assert len(code) == 6 and code.isdigit()
    assert token


def test_registered_device_cannot_act(configured):
    _, _, token = service.register_device("Screen", conn=configured)
    principal = service.authenticate(token, conn=configured)
    assert principal.is_approved is False


def test_device_cannot_grant_itself_a_role(configured):
    """A device may ask for admin; only the approver decides."""
    _, code, _ = service.register_device(
        "Sneaky", requested_role="admin", conn=configured)
    device, _ = service.approve_device(code, role="projector", conn=configured)
    assert device.role == "projector"


def test_approve_promotes_and_issues_a_token(configured):
    _, code, _ = service.register_device("Screen", conn=configured)
    device, token = service.approve_device(code, role="operator",
                                           conn=configured)
    assert device.status == "approved"
    principal = service.authenticate(token, conn=configured)
    assert principal.role == "operator"
    assert principal.is_approved


def test_approval_invalidates_the_pending_token(configured):
    _, code, pending = service.register_device("Screen", conn=configured)
    service.approve_device(code, role="operator", conn=configured)
    with pytest.raises(TokenError):
        service.authenticate(pending, conn=configured)


def test_code_cannot_be_used_twice(configured):
    _, code, _ = service.register_device("Screen", conn=configured)
    service.approve_device(code, role="projector", conn=configured)
    with pytest.raises(service.DeviceError):
        service.approve_device(code, role="admin", conn=configured)


def test_unknown_code_is_rejected(configured):
    with pytest.raises(service.DeviceError, match="No device is waiting"):
        service.approve_device("000000", conn=configured)


def test_expired_code_cannot_be_approved(configured):
    _, code, _ = service.register_device("Screen", conn=configured)
    stale = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    configured.execute(
        "UPDATE devices SET code_expires_at = ? WHERE approval_code = ?",
        (stale, code))
    with pytest.raises(service.DeviceError):
        service.approve_device(code, conn=configured)


def test_revoke_is_immediate(configured):
    """The reason tokens are re-checked against the row on every request."""
    device, code, _ = service.register_device("Screen", conn=configured)
    _, token = service.approve_device(code, role="operator", conn=configured)
    assert service.authenticate(token, conn=configured)

    service.revoke_device(device.id, conn=configured)
    with pytest.raises(TokenError, match="revoked"):
        service.authenticate(token, conn=configured)


def test_revoking_an_unknown_device_raises(configured):
    with pytest.raises(service.DeviceError):
        service.revoke_device("no-such-device", conn=configured)


def test_pending_device_cap(configured):
    """An unauthenticated endpoint must not be able to fill the disk."""
    for i in range(service.MAX_PENDING_DEVICES):
        service.register_device(f"Screen {i}", conn=configured)
    with pytest.raises(service.DeviceError, match="Too many devices"):
        service.register_device("One too many", conn=configured)


def test_device_name_is_required(configured):
    with pytest.raises(service.DeviceError):
        service.register_device("   ", conn=configured)


def test_unknown_role_is_rejected(configured):
    with pytest.raises(service.DeviceError):
        service.register_device("Screen", requested_role="root",
                                conn=configured)


def test_list_devices(configured):
    service.register_device("One", conn=configured)
    service.register_device("Two", conn=configured)
    assert len(service.list_devices(conn=configured)) == 2


# =====================================================================
# Roles
# =====================================================================

@pytest.mark.parametrize("role,minimum,allowed", [
    ("admin", "admin", True),
    ("admin", "operator", True),
    ("admin", "projector", True),
    ("operator", "admin", False),
    ("operator", "operator", True),
    ("operator", "projector", True),
    ("projector", "admin", False),
    ("projector", "operator", False),
    ("projector", "projector", True),
])
def test_role_hierarchy(role, minimum, allowed):
    principal = service.Principal("d", "Device", role, "approved")
    assert principal.has_role(minimum) is allowed


# =====================================================================
# Audit log
# =====================================================================

def test_audit_records_the_whole_lifecycle(configured):
    device, code, _ = service.register_device("Screen", conn=configured)
    service.approve_device(code, role="operator", conn=configured)
    service.revoke_device(device.id, conn=configured)

    actions = [e.action for e in audit.query(conn=configured)]
    for expected in ("auth.setup_pin", "auth.device_registered",
                     "auth.device_approved", "auth.device_revoked"):
        assert expected in actions


def test_audit_records_failed_logins(configured):
    with pytest.raises(service.InvalidPinError):
        service.verify_admin_pin("wrong-pin-x", conn=configured)
    assert "auth.login_failed" in [e.action for e in audit.query(conn=configured)]


def test_audit_never_stores_secrets(configured):
    audit.record("test.event", detail={
        "pin": "cornerstone-77",
        "token": "eyJsecret",
        "signing_key": "abc",
        "device_name": "Kept",
    }, conn=configured)
    entry = audit.query(limit=1, conn=configured)[0]
    assert entry.detail == {"device_name": "Kept"}


def test_audit_write_failure_does_not_raise(monkeypatch):
    """An audit failure must never take down the request it records."""
    def explode(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(auth_db, "auth_connection", explode)
    audit.record("test.event")     # must not raise


def test_audit_newest_first(configured):
    for i in range(5):
        audit.record(f"test.event{i}", conn=configured)
    entries = audit.query(limit=5, conn=configured)
    assert entries[0].action == "test.event4"


def test_audit_filters_by_action_prefix(configured):
    audit.record("alpha.one", conn=configured)
    audit.record("beta.one", conn=configured)
    results = audit.query(action="alpha", conn=configured)
    assert all(e.action.startswith("alpha") for e in results)
    assert results


def test_audit_limit_is_bounded(configured):
    for i in range(5):
        audit.record(f"test.{i}", conn=configured)
    assert len(audit.query(limit=10_000, conn=configured)) <= 1000


# =====================================================================
# HTTP surface
# =====================================================================

def test_api_is_open_before_setup(client):
    """Upgrading an existing install must not lock its operator out."""
    assert client.post("/projector/clear").status_code == 200


def test_health_warns_when_unconfigured(client):
    body = client.get("/").json()
    assert body["auth"]["configured"] is False
    assert "warning" in body["auth"]


def test_setup_then_enforced(client):
    assert client.post("/auth/setup-pin",
                       json={"pin": GOOD_PIN}).status_code == 201
    assert client.post("/projector/clear").status_code == 401
    assert client.get("/").json()["auth"]["configured"] is True


def test_setup_pin_twice_is_409(client):
    client.post("/auth/setup-pin", json={"pin": GOOD_PIN})
    r = client.post("/auth/setup-pin", json={"pin": "another-pin-42"})
    assert r.status_code == 409


def test_weak_pin_over_http_is_422(client):
    r = client.post("/auth/setup-pin", json={"pin": "111111"})
    assert r.status_code == 422


def _approve(client, name="Device", role="operator"):
    reg = client.post("/auth/register-device",
                      json={"name": name, "role": role}).json()
    approved = client.post("/auth/approve-device",
                           json={"pin": GOOD_PIN,
                                 "code": reg["approval_code"],
                                 "role": role}).json()
    return approved["device"]["id"], {
        "Authorization": f"Bearer {approved['access_token']}"}


def test_full_http_lifecycle(client):
    client.post("/auth/setup-pin", json={"pin": GOOD_PIN})
    device_id, headers = _approve(client)

    assert client.get("/auth/me", headers=headers).status_code == 200
    assert client.post("/projector/clear", headers=headers).status_code == 200

    r = client.post("/auth/revoke-device",
                    json={"pin": GOOD_PIN, "device_id": device_id})
    assert r.status_code == 200
    assert client.post("/projector/clear", headers=headers).status_code == 401


def test_role_boundary_over_http(client):
    client.post("/auth/setup-pin", json={"pin": GOOD_PIN})
    _, operator = _approve(client, "Op", "operator")
    _, projector = _approve(client, "Screen", "projector")

    assert client.post("/projector/clear", headers=operator).status_code == 200
    assert client.post("/projector/clear", headers=projector).status_code == 403
    assert client.post("/obs/disconnect", headers=operator).status_code == 403
    assert client.get("/auth/devices", headers=operator).status_code == 403

    _, admin = _approve(client, "Booth", "admin")
    assert client.get("/auth/devices", headers=admin).status_code == 200


def test_display_surfaces_stay_public(client):
    """An OBS Browser Source cannot send an Authorization header."""
    client.post("/auth/setup-pin", json={"pin": GOOD_PIN})
    for path in ("/projector", "/projector/static/projector.js",
                 "/projector/config", "/translations"):
        assert client.get(path).status_code == 200, path


def test_display_surfaces_can_be_locked_down(client, monkeypatch):
    from app.auth import middleware as mw
    client.post("/auth/setup-pin", json={"pin": GOOD_PIN})

    locked = dataclasses.replace(mw.settings, public_projector=False)
    monkeypatch.setattr(mw, "settings", locked)
    assert client.get("/projector").status_code == 401

    _, headers = _approve(client, "Screen", "projector")
    assert client.get("/projector", headers=headers).status_code == 200


def test_token_accepted_in_query_string(client, monkeypatch):
    """The only mechanism OBS leaves available for a locked-down overlay."""
    from app.auth import middleware as mw
    client.post("/auth/setup-pin", json={"pin": GOOD_PIN})

    reg = client.post("/auth/register-device",
                      json={"name": "Screen", "role": "projector"}).json()
    approved = client.post("/auth/approve-device",
                           json={"pin": GOOD_PIN,
                                 "code": reg["approval_code"],
                                 "role": "projector"}).json()
    token = approved["access_token"]

    locked = dataclasses.replace(mw.settings, public_projector=False)
    monkeypatch.setattr(mw, "settings", locked)
    assert client.get(f"/projector?token={token}").status_code == 200


def test_lockout_over_http_returns_429_with_retry_after(client):
    client.post("/auth/setup-pin", json={"pin": GOOD_PIN})
    for _ in range(4):
        assert client.post("/auth/login",
                           json={"pin": "wrong-pin-x"}).status_code == 401
    r = client.post("/auth/login", json={"pin": "wrong-pin-x"})
    assert r.status_code == 429
    assert "retry-after" in {k.lower() for k in r.headers}


def test_clear_lockout_needs_an_admin_device_not_the_pin(client):
    """The PIN is what is locked out, so a PIN-gated escape hatch would
    only work when it was not needed."""
    client.post("/auth/setup-pin", json={"pin": GOOD_PIN})
    _, admin = _approve(client, "Booth", "admin")

    for _ in range(5):
        client.post("/auth/login", json={"pin": "wrong-pin-x"})
    assert client.post("/auth/login", json={"pin": GOOD_PIN}).status_code == 429

    assert client.post("/auth/clear-lockout").status_code == 401
    assert client.post("/auth/clear-lockout", headers=admin).status_code == 200
    assert client.post("/auth/login", json={"pin": GOOD_PIN}).status_code == 200


def test_auth_status_is_public_and_leaks_nothing(client):
    client.post("/auth/setup-pin", json={"pin": GOOD_PIN})
    r = client.get("/auth/status")
    assert r.status_code == 200
    assert GOOD_PIN not in r.text
    assert "signing_key" not in r.text
    assert "pin_hash" not in r.text


def test_unapproved_device_gets_403_with_guidance(client):
    client.post("/auth/setup-pin", json={"pin": GOOD_PIN})
    reg = client.post("/auth/register-device",
                      json={"name": "Screen", "role": "projector"}).json()
    r = client.post("/projector/clear",
                    headers={"Authorization":
                             f"Bearer {reg['pending_token']}"})
    assert r.status_code == 403
    assert "approve" in r.json()["detail"].lower()


def test_missing_token_response_explains_how_to_get_one(client):
    client.post("/auth/setup-pin", json={"pin": GOOD_PIN})
    r = client.post("/projector/clear")
    assert r.status_code == 401
    assert "register-device" in r.json()["hint"]
    assert r.headers.get("www-authenticate") == "Bearer"


def test_unknown_paths_still_404(client):
    client.post("/auth/setup-pin", json={"pin": GOOD_PIN})
    assert client.get("/no/such/route").status_code == 404


def test_require_auth_fails_closed(client, monkeypatch):
    """Operators who want fail-closed behaviour can have it."""
    from app.auth import middleware as mw
    strict = dataclasses.replace(mw.settings, require_auth=True)
    monkeypatch.setattr(mw, "settings", strict)
    r = client.post("/projector/clear")
    assert r.status_code == 503
    assert "setup-pin" in r.json()["hint"]
    # The bootstrap path must stay reachable, or setup is impossible.
    assert client.get("/auth/status").status_code == 200


# =====================================================================
# Policy table
# =====================================================================

def test_every_route_has_a_policy():
    """A route with no rule is denied, so a missing rule is a bug."""
    from app.auth import policy
    from app.main import app

    missing = []
    for route in app.routes:
        path = getattr(route, "path", None)
        if not path or "{" in path:
            continue      # parameterised paths are covered by regex rules
        methods = getattr(route, "methods", None) or {"GET"}
        for method in methods:
            if method in ("HEAD", "OPTIONS"):
                continue
            if policy.find_rule(path.rstrip("/") or "/", method) is None:
                missing.append(f"{method} {path}")
    assert not missing, f"routes without an auth policy: {missing}"


def test_mutating_routes_are_never_public():
    """Nothing that changes state may be reachable without a token,
    except the auth bootstrap itself."""
    from app.auth import policy

    bootstrap = {"/auth/setup-pin", "/auth/login", "/auth/change-pin",
                 "/auth/register-device", "/auth/approve-device",
                 "/auth/revoke-device"}
    for rule in policy.RULES:
        if (rule.methods and "POST" in rule.methods
                and rule.required_role is None):
            assert any(rule.pattern.fullmatch(p) for p in bootstrap), (
                f"public POST rule {rule.pattern.pattern} is not a "
                f"bootstrap endpoint"
            )
