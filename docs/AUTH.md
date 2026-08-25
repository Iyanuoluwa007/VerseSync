# Authentication and devices

VerseSync protects the endpoints that *change* something — what is on
screen, whether the microphone is running, what OBS is doing — while
leaving the display itself readable so an OBS Browser Source keeps
working.

- [How it fits together](#how-it-fits-together)
- [Turning it on](#turning-it-on)
- [Adding a device](#adding-a-device)
- [Roles](#roles)
- [Revoking a device](#revoking-a-device)
- [If you get locked out](#if-you-get-locked-out)
- [Locking down the projector too](#locking-down-the-projector-too)
- [The audit log](#the-audit-log)
- [Endpoint reference](#endpoint-reference)
- [Design notes](#design-notes)

---

## How it fits together

There are no user accounts. There is:

- **One admin PIN.** Set once, on first run. Whoever knows it can approve
  and revoke devices.
- **Devices.** A projector, a booth laptop, an operator's phone. Each
  registers itself, gets a 6-digit code, and can do nothing at all until
  someone with the PIN approves it and assigns a role.

```
  device                          admin                    server
    |                               |                         |
    |-- POST /auth/register-device --------------------------->|
    |<-- 6-digit code + pending token (powerless) -------------|
    |                               |                         |
    |--- reads the code aloud ----->|                         |
    |                               |-- POST /auth/approve --->|
    |                               |    (PIN + code + role)   |
    |<-- admin passes back the access token -------------------|
    |                                                          |
    |-- Authorization: Bearer <token> ------------------------>|
```

**Authentication is off until you set a PIN.** That keeps upgrades from
locking an operator out mid-service, but it does mean a fresh install is
open until someone runs one command. `GET /` says which state you are in.

---

## Turning it on

```bash
curl -X POST http://localhost:8000/auth/setup-pin -H "Content-Type: application/json" -d "{\"pin\":\"cornerstone-77\"}"
```

```json
{
  "status": "configured",
  "message": "Admin PIN set. Authentication is now enforced."
}
```

This works **exactly once**. Because it has to be callable without a
token, allowing it twice would let anyone on the network seize the admin
role at any time. To change the PIN later, use `/auth/change-pin` with
the current one.

The PIN must be at least 6 characters. Repeated digits (`111111`),
digit runs (`123456`, `654321`) and the usual breach-corpus entries are
rejected. A passphrase is better than a number if the keypad allows it.

Check where you stand at any time:

```bash
curl http://localhost:8000/auth/status
```

---

## Adding a device

**On the device**, register:

```bash
curl -X POST http://localhost:8000/auth/register-device -H "Content-Type: application/json" -d "{\"name\":\"Sanctuary booth\",\"role\":\"operator\"}"
```

```json
{
  "status": "pending",
  "approval_code": "584803",
  "pending_token": "eyJhbGciOi...",
  "message": "Read the approval code to an admin. This device can do nothing until it is approved."
}
```

The `role` here is a *request*, not a grant. The approving admin decides
the real role, so a device cannot promote itself.

**With the PIN**, approve it:

```bash
curl -X POST http://localhost:8000/auth/approve-device -H "Content-Type: application/json" -d "{\"pin\":\"cornerstone-77\",\"code\":\"584803\",\"role\":\"operator\"}"
```

The response carries `access_token`. Give that to the device; it replaces
the pending token, which stops working the moment approval happens.

**On the device**, use it:

```bash
curl -X POST http://localhost:8000/projector/show -H "Authorization: Bearer <access_token>" -H "Content-Type: application/json" -d "{\"text\":\"John three sixteen\"}"
```

Codes expire after 15 minutes. If one lapses, register again.

---

## Roles

| Role | Can do |
|---|---|
| `projector` | Receive verses. Read scripture. Nothing else. |
| `operator` | Everything a projector can, plus drive the overlay, the parser and the STT pipeline. |
| `admin` | Everything, plus list and revoke devices, read the audit log, and control OBS. |

Give a screen in the sanctuary `projector`. Give the person running the
service `operator`. Keep `admin` for the machine you actually administer
from.

---

## Revoking a device

```bash
curl -X POST http://localhost:8000/auth/revoke-device -H "Content-Type: application/json" -d "{\"pin\":\"cornerstone-77\",\"device_id\":\"<id>\"}"
```

**This takes effect immediately**, on the device's very next request —
not whenever its token happens to expire. Every request re-checks the
device row, so revocation is not a promise about the future.

List devices to find an id (needs an admin device token):

```bash
curl http://localhost:8000/auth/devices -H "Authorization: Bearer <admin token>"
```

To invalidate *everything* at once, change the PIN. That rotates the
token signing key, and every device must be re-approved.

---

## If you get locked out

Five wrong PINs within 15 minutes locks the PIN for the rest of that
window. The lockout is stored in the database, so restarting the server
does not clear it, and **even the correct PIN is refused while locked** —
otherwise the lockout would not slow an attacker down at all.

Two ways out:

1. **Wait.** `GET /auth/status` reports `retry_after_seconds`.
2. **Use an admin device.** If you already have one approved:

   ```bash
   curl -X POST http://localhost:8000/auth/clear-lockout -H "Authorization: Bearer <admin token>"
   ```

   This is deliberately *not* PIN-authenticated. The PIN is the thing
   that is locked out, so a PIN-gated escape hatch could only ever be
   used when it was not needed.

If you have neither an admin device nor patience, stop the server and
delete the `auth_config` row from `backend/data/versesync.db`. That
resets setup entirely and does not touch your Bible data.

---

## Locking down the projector too

By default `/projector`, `/ws/transcripts` and the scripture read
endpoints are open, because **an OBS Browser Source cannot send an
`Authorization` header** and neither can a browser WebSocket. They expose
verse text and nothing else.

To require a token there as well:

```ini
VERSESYNC_PUBLIC_PROJECTOR=false
```

Then approve a device with the `projector` role and put its token in the
Browser Source URL:

```
http://localhost:8000/projector?token=<device token>
```

The WebSocket picks the token up from the same query string
automatically.

The tradeoff, stated plainly: a token in a URL appears in server logs and
in your OBS source configuration. It is the only mechanism OBS leaves
available.

---

## The audit log

Every authentication event and every state-changing request is recorded.

```bash
curl "http://localhost:8000/auth/audit?limit=20" -H "Authorization: Bearer <admin token>"
```

```
action                     actor      name               status
----------------------------------------------------------------
auth.setup_pin             admin      -
auth.device_registered     device     Booth laptop
auth.device_approved       admin      Booth laptop
projector.clear            device     Booth laptop       200
auth.denied                device     Sanctuary screen   403
auth.login_failed          anonymous  -
```

Filter with `?action=auth.device` (prefix match) or
`?actor_id=<device id>`.

Nothing secret is ever written. The writer drops a denylist of
secret-shaped keys before serialising, and a test asserts that a PIN
passed into an audit call never reaches the database. Writes are
best-effort by design: a failure to log is logged to stderr and
swallowed, because an audit trail that can take down a live service is
worse than no audit trail.

---

## Endpoint reference

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /auth/status` | none | Is a PIN set, device counts, lockout state |
| `POST /auth/setup-pin` | none, once | Set the admin PIN |
| `POST /auth/login` | PIN | Verify the PIN |
| `POST /auth/change-pin` | PIN | Rotate the PIN; invalidates every device |
| `POST /auth/register-device` | none | Ask to join; returns a code |
| `POST /auth/approve-device` | PIN | Approve a code, assign a role |
| `POST /auth/revoke-device` | PIN | Revoke a device, immediately |
| `POST /auth/clear-lockout` | admin token | Clear the failed-PIN counter |
| `GET /auth/devices` | admin token | List devices |
| `GET /auth/audit` | admin token | Read the audit log |
| `GET /auth/me` | any token | What this token is |

`/auth/login` returns **no token**. The PIN authorises admin actions
directly; minting a long-lived admin token from a six-digit PIN would
widen its blast radius considerably.

---

## Design notes

Two deliberate departures from the original Phase 0 plan, both
documented here so they are choices rather than drift.

**scrypt instead of Argon2id.** `argon2-cffi` is a compiled dependency,
and VerseSync's base install is meant to run on a church laptop with no
build tools. scrypt is RFC 7914, memory-hard, and in the standard
library. The stored hash records its own algorithm and parameters, so
adding Argon2id later is a verification-time branch, not a migration.
The honest caveat is in [SECURITY.md](../SECURITY.md): for a low-entropy
PIN the lockout matters far more than the KDF.

**Tokens are checked against the database, not just verified.** The
plain-JWT design in the plan could not revoke a device before its token
expired. Each token carries a version matched against the device row, so
revoking, or changing the PIN, invalidates outstanding tokens on the next
request. `/auth/revoke-device` has to mean "now".
