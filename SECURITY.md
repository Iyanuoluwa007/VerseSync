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

## Threat model, stated plainly

**VerseSync has no authentication.** Every endpoint is unauthenticated,
including the ones that change what appears on screen (`/projector/show`,
`/projector/clear`) and the ones that start and stop the microphone
(`/stt/start`, `/stt/stop`). Authentication is planned but not built.

What follows from that:

| Deployment | Safe? | Notes |
|---|---|---|
| Loopback only (`VERSESYNC_HOST=127.0.0.1`, the default) | Yes | OBS on the same machine. This is the intended setup. |
| Trusted LAN (`VERSESYNC_HOST=0.0.0.0`) | With care | Anyone on that network can put text on your screen and switch your microphone on. Use only on a network you control. |
| Exposed to the internet | **No** | Do not do this. There is nothing stopping a stranger from controlling your service output. |

If you need remote access, put VerseSync behind a VPN or an authenticating
reverse proxy. Do not port-forward it.

## Handling secrets

Three secrets can be involved. None of them are ever stored in the
`Settings` object, returned by an API endpoint, or written to a log:

| Secret | Where it goes | Notes |
|---|---|---|
| `GROQ_API_KEY` | `backend/.env` | Optional. Only used for the LLM parser fallback and cloud Whisper. |
| `OBS_WS_PASSWORD` | `backend/.env` | Optional. Read at the point of use; `/obs/status` reports only whether one is configured. |
| Any future signing key | `backend/.env` | Same rules. |

`backend/.env` is gitignored, and CI fails the build if a `.env` file or
a recognisable credential pattern is ever committed. If you do leak a
key, **rotate it first**, then clean the history.

## Notes on specific components

- **The projector overlay renders verse text with `textContent`, never
  `innerHTML`.** Verse text arrives over a socket, and an overlay burned
  into a live stream is not a place to execute markup.
- **Static asset serving is a whitelist**, not a directory mount, so path
  traversal is not expressible.
- **The OBS WebSocket password is never transmitted.** obs-websocket v5
  uses a SHA256 challenge-response; only the derived string goes over the
  wire.
- **Bible data is downloaded over HTTPS from eBible.org** at install
  time. The archives are not checksum-pinned; if you need supply-chain
  assurance for the text, verify the downloads yourself before ingesting.
- **Whisper models are downloaded from Hugging Face** on first use by
  `faster-whisper`. Standard Hugging Face trust assumptions apply.

## Privacy

VerseSync processes audio from a live microphone. Be aware of what leaves
the machine:

- **Local Whisper (`--engine local`, or the local tiers of `tiered`)**
  sends no audio anywhere. Everything stays on the machine.
- **Groq cloud Whisper (`--engine groq`, or the cloud fallback tier)**
  uploads speech segments to Groq for transcription.
- **The Groq LLM parser fallback** sends the *transcript text* (not
  audio) to Groq when the regex parsers fail.

If your congregation has not consented to audio leaving the building,
run with `--no-cloud-fallback` and leave `GROQ_API_KEY` unset.
