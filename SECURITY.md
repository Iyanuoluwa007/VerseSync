# Security Policy

## Reporting a vulnerability

Please report security issues privately, **not** as a public GitHub issue.

Use [GitHub's private vulnerability reporting](https://github.com/Iyanuoluwa007/VerseSync/security/advisories/new)
on this repository. Include what you found, how to reproduce it, and what
an attacker could do with it. You should get an acknowledgement within a
few days.

## Supported versions

VerseSync is pre-1.0 and moves fast. Only the latest release on `main`
receives fixes.

## Authentication

VerseSync has an admin PIN, device tokens and an audit log. See
[docs/AUTH.md](docs/AUTH.md) for the walkthrough.

**Authentication is not on until you turn it on.** A fresh install has no
admin PIN, and until one is set every endpoint is open to anyone who can
reach the server. This is deliberate: it means upgrading an existing
install cannot lock an operator out mid-service. It also means a new
install is unprotected until someone runs one command.

```bash
curl -X POST http://localhost:8000/auth/setup-pin -H "Content-Type: application/json" -d "{\"pin\":\"choose-a-real-one\"}"
```

`GET /` tells you which state you are in, and warns while unprotected.
Set `VERSESYNC_REQUIRE_AUTH=true` to fail closed instead: protected
routes return 503 until a PIN exists.

### What is protected

| Surface | Requires |
| ------- | -------- |
| `/`, `/healthz`, `/docs`, `/auth/status` | nothing |
| `/auth/setup-pin` | nothing, and works exactly once |
| `/auth/login`, `/auth/approve-device`, `/auth/revoke-device` | the admin PIN |
| `/projector`, `/ws/transcripts`, `/verse/*`, `/translations` | nothing by default, see below |
| `/projector/show`, `/projector/clear`, `/parse`, `/stt/*` | an `operator` device token |
| `/obs/connect`, `/auth/devices`, `/auth/audit` | an `admin` device token |

A route with no policy entry is **denied**, so adding an endpoint without
adding a rule produces a 403 rather than an accidental hole.

### Why the projector display is public by default

An OBS Browser Source cannot send an `Authorization` header, and neither
can a browser WebSocket client — neither API allows it. If the display
path required a header, the OBS integration could not work at all.

So the read-only display surfaces are open by default. They expose verse
text and nothing else: no control, no configuration, no audit data.

To lock them down too, set `VERSESYNC_PUBLIC_PROJECTOR=false` and put a
device token in the URL:

```
http://localhost:8000/projector?token=<device token>
```

A token in a query string will appear in server logs and in the OBS
source configuration. That is the only mechanism OBS leaves available, so
it is offered with the tradeoff stated rather than pretended away.

## Threat model, stated plainly

**The admin PIN is low entropy.** It is hashed with scrypt (RFC 7914,
memory-hard, from the standard library), but no KDF makes a six-digit
secret safe against an attacker who holds the hash. The real defences
are:

- **Lockout**: 5 wrong PINs within 15 minutes locks the PIN for the rest
  of that window, persisted in the database so restarting the server is
  not an escape. Even the correct PIN is refused while locked.
- **Keeping the database off the network.** `versesync.db` holds the PIN
  hash and the token signing key. Anyone who can read that file can mint
  tokens. Do not put it on a network share.

**Device tokens are checked against the database on every request**, not
just verified as signatures. That is what makes `/auth/revoke-device`
take effect immediately rather than whenever the token happens to expire.
Changing the admin PIN rotates the signing key, which invalidates every
device at once.

## Network exposure

| Deployment | Safe? | Notes |
|---|---|---|
| Loopback only (`VERSESYNC_HOST=127.0.0.1`, the default) | Yes | OBS on the same machine. The intended setup. |
| Trusted LAN (`VERSESYNC_HOST=0.0.0.0`) **with a PIN set** | Reasonable | Control endpoints need a token. Registration is capped, so it cannot be used to fill the disk. |
| Trusted LAN **with no PIN set** | **No** | Anyone on the network can put text on your screen and switch your microphone on. |
| Exposed to the internet | **No** | There is no TLS, no rate limiting beyond the PIN lockout, and no protection against a determined attacker. Put it behind a VPN. |

VerseSync speaks plain HTTP. On a LAN, a token in an `Authorization`
header is visible to anyone who can see the traffic. If that matters to
you, terminate TLS in front of it.

## Handling secrets

None of these are ever stored in the `Settings` object, returned by any
endpoint, or written to a log or the audit trail:

| Secret | Where it lives | Notes |
|---|---|---|
| Admin PIN | Hashed in `versesync.db` | scrypt, salted per install. |
| Token signing key | `versesync.db` | Generated at setup; rotated on PIN change. |
| `GROQ_API_KEY` | `backend/.env` | Optional. |
| `OBS_WS_PASSWORD` | `backend/.env` | Optional. Read at point of use; `/obs/status` reports only whether one is set. |

`backend/.env` is gitignored, and CI fails the build if a `.env` file or
a recognisable credential pattern is ever committed. The audit log
filters a denylist of secret-shaped keys before writing, and a test
asserts a PIN passed into an audit call never reaches the database.

If you do leak a key, **rotate it first**, then clean the history.

## Notes on specific components

- **Verse text is rendered with `textContent`, never `innerHTML`.** It
  arrives over a socket, and an overlay burned into a live stream is not
  a place to execute markup.
- **Static asset serving is a whitelist**, not a directory mount, so path
  traversal is not expressible.
- **Device tokens are HS256 JWTs via PyJWT**, decoded with an explicit
  `algorithms=["HS256"]`, so a token cannot nominate its own algorithm.
  `alg: none` forgeries and tokens signed with another key are both
  covered by tests.
- **The OBS WebSocket password is never transmitted.** obs-websocket v5
  uses a SHA256 challenge-response; only the derived string goes over the
  wire.
- **Bible data is downloaded over HTTPS from eBible.org** at install
  time. The archives are not checksum-pinned; verify them yourself if you
  need supply-chain assurance for the text.
- **Whisper models are downloaded from Hugging Face** on first use by
  `faster-whisper`. Standard Hugging Face trust assumptions apply.

## Privacy

VerseSync processes audio from a live microphone. Be aware of what leaves
the machine:

- **Local Whisper** (`--engine local`, or the local tiers of `tiered`)
  sends no audio anywhere. Everything stays on the machine.
- **Groq cloud Whisper** (`--engine groq`, or the cloud fallback tier)
  uploads speech segments to Groq for transcription.
- **The Groq LLM parser fallback** sends the *transcript text* (not
  audio) to Groq when the regex parsers fail.

If your congregation has not consented to audio leaving the building, run
with `--no-cloud-fallback` and leave `GROQ_API_KEY` unset.

The audit log records who did what and when, including the client IP of
each request. It is stored locally and never transmitted.
