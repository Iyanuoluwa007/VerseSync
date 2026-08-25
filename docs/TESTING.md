# Manual test plan

What still needs a human, in priority order. Everything here is something
automated tests genuinely cannot cover: it needs a microphone, a GPU, a
real OBS install, or another machine.

Work top-down. Each item says what to run, what you should see, and what
it means if you see something else.

---

## Read this first: your install now enforces authentication

An admin PIN is set on this machine, so **every control endpoint needs a
device token**. Example commands in the README and `docs/OBS.md` that do
not pass one will return 401. That is correct behaviour, not a bug.

Get yourself an operator token before you start:

```bash
curl -X POST http://localhost:8000/auth/register-device -H "Content-Type: application/json" -d "{\"name\":\"Test operator\",\"role\":\"operator\"}"
```

Take the `approval_code` from the response, then:

```bash
curl -X POST http://localhost:8000/auth/approve-device -H "Content-Type: application/json" -d "{\"pin\":\"<your PIN>\",\"code\":\"<6 digits>\",\"role\":\"operator\"}"
```

Keep the `access_token`. Every command below marked **(token)** needs
`-H "Authorization: Bearer <token>"`.

Your PIN is in `ADMIN_PIN.txt` at the repo root. Move it to a password
manager and delete that file when you are done.

> The three demo devices created during setup have been revoked. Device
> counts should read `approved: 0, revoked: 3` until you add your own.

---

## Already verified — don't re-test these

So you don't spend time on ground already covered:

| Verified | How |
|---|---|
| 583 automated tests | Run on Windows and Linux, Python 3.11/3.12/3.13 in CI |
| Bible download and ingest | Real run: 93,287 verses, 66 books × 3 translations, 7.7 s |
| CLI verse lookup, including Yorùbá | Ran `query_verse.py` for KJV/WEB/YOR |
| Every HTTP endpoint | Live server, real responses |
| Overlay rendering, all themes | Real Chromium against a live server |
| Overlay auto-reconnect | Killed the server mid-session; page recovered with no reload |
| Retained state on reconnect | Reloaded mid-verse; the verse came back |
| Transparent background | Confirmed `rgba(0,0,0,0)`, zero external requests |
| Auth end to end | register → approve → use → revoke, live |
| Lockout | 5 failures locked it, survived a restart, refused the correct PIN |
| Audit log | Every step recorded, no secrets in it |
| `setup.ps1` | Ran end to end, completes clean |
| Fresh clone install | Bare clone, projector-only profile, boots and serves |

The gap is everything below.

---

## Priority 1 — the live audio path

**None of this has ever been run.** No microphone or GPU was available.
The VAD fix, the `--test-mic` fix and the pipeline changes are covered by
unit tests with fakes, but no real audio has gone through this build.

### 1.1 Install the STT stack

```bash
.\setup.ps1 -WithSTT
```

- [ ] Completes without error.

**Watch for:** `numpy` resolves to 2.x here, and `faster-whisper` /
`torch` have historically been fussy about that. If you get a binary
incompatibility error mentioning numpy, pin `numpy<2` in
`backend/requirements-stt.txt` and tell me — that is a real finding and
the pin belongs in the repo.

### 1.2 Microphone enumeration

```bash
python scripts/listen.py --list-devices
```

- [ ] Your microphone appears, with a sensible channel count and sample rate.
- [ ] Note its id for the next step.

### 1.3 Microphone capture — the fix I could not test

```bash
python scripts/listen.py --test-mic --mic <id>
```

This command was **completely broken** before this audit (it raised
`AttributeError: __enter__` immediately). This is the first real run of
the fix.

- [ ] It runs for 15 seconds instead of crashing.
- [ ] Speaking moves `rms` above **0.005**. Silence sits near 0.001.
- [ ] The final line reports roughly **470 chunks** captured in 15 s.

**If rms stays near zero** while you speak, it is the device or the id,
not VerseSync — try another `--mic`. **If it crashes**, copy the
traceback; that is a bug in my fix.

### 1.4 Voice activity detection

```bash
python scripts/listen.py --language en --debug
```

Watch the `[hb]` heartbeat lines.

- [ ] `chunks=` climbs steadily (about 31 per second).
- [ ] `vad_prob` rises above **0.30** when you speak and falls when you stop.
- [ ] `speaking=True` appears while you talk, `False` after you pause.
- [ ] `[VAD] speech START` and `speech END` appear around each utterance.

**This is the fix worth watching most closely.** The VAD was running
Silero twice per chunk, which advanced a stateful model's hidden state
twice per 32 ms of audio. If segment boundaries land in odd places — the
first word clipped, or segments that never close — that is the area to
suspect, and it would be a genuine finding.

If the VAD never fires, lower the threshold: `--vad-threshold 0.2`.

### 1.5 English transcription and detection

Still in `--debug`, say each of these clearly:

- [ ] "John three sixteen" → `DETECTED JHN 3:16`
- [ ] "Turn with me to Romans eight twenty-eight" → `ROM 8:28`
- [ ] "Psalm one forty-five verse five" → `PSA 145:5`
- [ ] "Luke five five" → `LUK 5:5`
- [ ] "Revelation two two" → `REV 2:2`
- [ ] "Psalms one fifty from verse one to ten" → `PSA 150:1-10`
- [ ] "Open your bibles to first thessalonians five sixteen" → `1TH 5:16`

The last four are the exact phrasings that failed in your May live
session and were fixed in v0.4.6. They pass in the parser tests; this
confirms they survive real transcription.

- [ ] Verse text prints under each detection, in the right translation.
- [ ] Ordinary speech containing no reference produces `heard` lines, not
      false detections.

**If a transcript is right but no reference is detected**, the parser is
at fault, not the audio. Paste the transcript into `POST /parse` and send
me the result.

### 1.6 Yorùbá

```bash
python scripts/listen.py --language yo --translation YOR --debug
```

- [ ] "Johanu kini ori keta ese kerin" → `1JN 3:4`
- [ ] "Saamu ori keji ese kini" → `PSA 2:1`
- [ ] "Romu ori kejo ese kejilelogun" → `ROM 8:22`
- [ ] Verse text renders with correct tone marks and sub-dots.

**Expect this to be the weakest area.** Whisper smashes Yorùbá word
boundaries ("Johan nukini" for "Johanu kini") and the normaliser only
fires on recognisable marker words. **Record what Whisper actually
transcribed**, not just whether detection worked — that transcript is
what tells us whether to fix the parser or bias the model.

Try `--engine groq --language yo` for comparison; hosted large-v3 is
markedly better at Yorùbá than a local `medium`.

### 1.7 Engine fallback

```bash
python scripts/preload_models.py
```

- [ ] Both models download, progress bars visible.

```bash
python scripts/listen.py --engine tiered --debug
```

- [ ] Announces which tier it landed on.
- [ ] `--no-cloud-fallback` stays fully local.
- [ ] With `GROQ_API_KEY` unset, the cloud tier is skipped cleanly rather
      than erroring.
- [ ] Ctrl+C during a model download exits cleanly and does not hang.

### 1.8 Clean shutdown

- [ ] Ctrl+C stops the listener without a traceback.
- [ ] Start it again immediately — the microphone opens. Previously the
      device was left held until the process was killed.

---

## Priority 2 — OBS Studio

I verified the overlay in Chromium, which is the same engine OBS embeds,
**but never inside OBS itself**. The Browser Source path is the one that
matters most.

### 2.1 Browser Source

```bash
curl http://localhost:8000/projector/obs-url
```

Add a Browser Source with that URL, 1920×1080, and **clear the Custom CSS
box** — OBS pre-fills a rule that fights the page's own styling.

- [ ] The page loads (not a blank or error frame).
- [ ] Push a verse **(token)** and it appears:

```bash
curl -X POST http://localhost:8000/projector/show -H "Authorization: Bearer <token>" -H "Content-Type: application/json" -d "{\"text\":\"John three sixteen\"}"
```

- [ ] **The background is genuinely transparent** — your camera shows
      through, with no black box. This is the single most important check.
- [ ] Text is crisp, not soft or resampled.
- [ ] `POST /projector/clear` **(token)** removes it.

### 2.2 The scene-change behaviour

This is the feature I am least able to verify without OBS, and the one
most likely to surprise.

Tick **Shutdown source when not visible** on the Browser Source, then:

- [ ] Show a verse. Switch to another scene. Switch back.
- [ ] **The verse is still there**, restored from retained state, not a
      blank frame.
- [ ] Right-click → Refresh mid-verse: the verse comes back.
- [ ] Close OBS entirely, reopen it: the verse is restored.

**If any of those come back blank**, the retained-state replay is not
working through OBS's CEF the way it does in a normal browser. Check
`GET /projector/state` to see what the server thinks should be showing.

### 2.3 Server restart under OBS

- [ ] With OBS open and the source visible, stop the VerseSync server.
- [ ] Restart it.
- [ ] The Browser Source reconnects **on its own**, no refresh needed.

Verified in Chromium; unverified in OBS.

### 2.4 Themes and resolutions

- [ ] `?theme=caption` and `?theme=fullscreen` render correctly.
- [ ] `?hold=0` leaves a verse up until replaced.
- [ ] `?fontScale=1.4` scales everything.
- [ ] At a 1280×720 canvas with a 1280×720 source, layout is still right.
- [ ] `?debug=1` shows the connection badge; **without it, no badge is
      ever visible** (it must never leak onto a stream).

### 2.5 OBS WebSocket — completely untested

The protocol is implemented against the published spec and unit-tested
against a fake server. **It has never touched a real OBS.**

In OBS: Tools → WebSocket Server Settings → enable, note port and password.

```ini
OBS_WS_ENABLED=true
OBS_WS_PORT=4455
OBS_WS_PASSWORD=<from OBS>
OBS_SCENE_NAME=<your scene>
OBS_SCENE_ITEM=<a source in it>
OBS_TEXT_SOURCE=<a GDI+ text source>
```

- [ ] `curl -X POST http://localhost:8000/obs/connect` **(admin token)**
      returns 200 with an `obs_version`.
- [ ] `GET /obs/status` shows `connected: true`.
- [ ] Showing a verse makes the named scene item **appear**.
- [ ] The text source fills with e.g. `John 3:16 (KJV)`.
- [ ] `POST /projector/clear` hides the item and blanks the text.
- [ ] **Close OBS while VerseSync runs.** Verses must keep appearing on
      the Browser Source; only scene control stops. It should log a few
      failures then go quiet, not spam.
- [ ] Reopen OBS, `POST /obs/connect`, control resumes.

**Most likely failure point:** the authentication handshake. If you get
"OBS rejected the Identify handshake", the SHA256 challenge-response
derivation is wrong and I need to know.

### 2.6 Virtual Camera and streaming

- [ ] Start Virtual Camera; join a meeting; verses appear to participants.
- [ ] Start a stream (or a local recording); verses are burned in.
- [ ] Measure the delay between speaking a reference and the verse
      appearing. **Expect 1–4 seconds.** Write down the actual number —
      that figure is quoted in the docs and is currently an estimate.

### 2.7 Locked-down projector

```ini
VERSESYNC_PUBLIC_PROJECTOR=false
```

- [ ] The Browser Source now fails (401) with no token — expected.
- [ ] Approve a `projector` device, append `?token=<token>` to the URL.
- [ ] The overlay works again, including the WebSocket.

---

## Priority 3 — authentication on real hardware

The flow is verified; these are the operational edges.

- [ ] Restart the server. The PIN and devices survive.
- [ ] Approve a `projector` device and confirm it **cannot** call
      `/projector/show` (403).
- [ ] Revoke a device mid-session while it is connected. Its next request
      must fail immediately.
- [ ] Deliberately mistype the PIN 5 times. Confirm lockout, then clear it
      with an admin device token.
- [ ] `GET /auth/audit` **(admin token)** shows everything you just did.
- [ ] Change the PIN; confirm every device is logged out.

---

## Priority 4 — installation and platforms

- [ ] `setup.sh` on macOS or Linux, if you have one.
- [ ] The live audio path on macOS — **entirely untested**, and CoreAudio
      via PortAudio behaves differently to WASAPI.
- [ ] Two machines: VerseSync on one with `VERSESYNC_HOST=0.0.0.0`, OBS on
      the other pointed at the LAN IP.
- [ ] A clean clone on a machine that has never run VerseSync, following
      only the README.

---

## Priority 5 — endurance

Worth doing once before you rely on it live.

- [ ] Run the listener for a **full 45 minutes**, roughly a sermon.
- [ ] Memory does not climb steadily (a leak in the audio queue or the
      segment buffer would show here).
- [ ] Detections still work at the end as well as at the start.
- [ ] No `Audio queue overflow` warnings in the log. If you see them, the
      consumer is falling behind the microphone.
- [ ] Speak for over 30 seconds without pausing: the segment force-flushes
      at the cap rather than growing without bound.
- [ ] Leave a Browser Source connected the whole time; it should never
      silently stop updating.

---

## Reporting what you find

For anything that fails, the useful details are:

- The command you ran and the full output or traceback.
- `GET /stt/status` for capture problems.
- `GET /projector/state` for overlay problems.
- `GET /auth/audit` for permission problems.
- For Yorùbá: **the transcript Whisper produced**, not just whether
  detection worked.
- OBS version, and whether OBS is on the same machine.

Strip any token or PIN before pasting.
