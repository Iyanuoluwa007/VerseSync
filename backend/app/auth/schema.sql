-- VerseSync auth + audit schema (Phase 0 / Module 5)
--
-- Idempotent: every CREATE uses IF NOT EXISTS so re-running is safe.
-- Lives in the same database file as the Bible tables; the auth module
-- applies this script itself rather than extending bible/schema.sql, so
-- the two concerns stay separable.

-- Single-row configuration: the admin PIN hash and the JWT signing key.
-- Constrained to exactly one row by the CHECK on id, so there is no way
-- to end up with two competing admin credentials.
CREATE TABLE IF NOT EXISTS auth_config (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    pin_hash        TEXT NOT NULL,      -- self-describing, see passwords.py
    signing_key     TEXT NOT NULL,      -- base64, 32 random bytes
    created_at      TEXT NOT NULL,      -- ISO 8601 UTC
    updated_at      TEXT NOT NULL
);

-- Devices that have asked to join. A device is useless until an admin
-- approves it: `status` gates every token check.
CREATE TABLE IF NOT EXISTS devices (
    id              TEXT PRIMARY KEY,   -- uuid4
    name            TEXT NOT NULL,
    role            TEXT NOT NULL DEFAULT 'projector'
                        CHECK (role IN ('admin', 'operator', 'projector')),
    status          TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'approved', 'revoked')),
    -- 6-digit approval code the operator reads out loud. Cleared once
    -- used, so a code can never approve a second device.
    approval_code   TEXT,
    code_expires_at TEXT,
    -- Bumped on revoke. A token carrying an older value is rejected,
    -- which is what makes revocation take effect immediately.
    token_version   INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    approved_at     TEXT,
    revoked_at      TEXT,
    last_seen_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_devices_status ON devices (status);
-- Partial unique index: two devices may both have NULL codes, but no two
-- pending devices can share a live code.
CREATE UNIQUE INDEX IF NOT EXISTS idx_devices_code
    ON devices (approval_code) WHERE approval_code IS NOT NULL;

-- Failed admin PIN attempts, for the lockout window. Persisted rather
-- than held in memory so restarting the server cannot clear a lockout.
CREATE TABLE IF NOT EXISTS pin_attempts (
    id              INTEGER PRIMARY KEY,
    attempted_at    TEXT NOT NULL,      -- ISO 8601 UTC
    source          TEXT                -- client host, best effort
);

CREATE INDEX IF NOT EXISTS idx_pin_attempts_time ON pin_attempts (attempted_at);

-- Append-only audit trail.
CREATE TABLE IF NOT EXISTS audit_log (
    id              INTEGER PRIMARY KEY,
    ts              TEXT NOT NULL,      -- ISO 8601 UTC
    actor_type      TEXT NOT NULL,      -- 'admin' | 'device' | 'anonymous'
    actor_id        TEXT,               -- device id, or NULL
    actor_name      TEXT,
    role            TEXT,
    action          TEXT NOT NULL,      -- 'auth.login', 'projector.show', ...
    method          TEXT,
    path            TEXT,
    status_code     INTEGER,
    source          TEXT,               -- client host, best effort
    detail          TEXT                -- JSON, never contains secrets
);

CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log (ts DESC);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log (actor_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log (action, ts DESC);
