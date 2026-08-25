<div align="center">

# VerseSync

### The preacher says the reference. The verse is already on screen.

Voice-activated scripture projection for live services and streams,
with a built-in OBS Studio overlay. English and Yorùbá.

**Free and open source. No paid tier, no feature gates, ever.**

[![CI](https://github.com/Iyanuoluwa007/VerseSync/actions/workflows/ci.yml/badge.svg)](https://github.com/Iyanuoluwa007/VerseSync/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![Ruff](https://img.shields.io/badge/lint-ruff-261230.svg)](https://github.com/astral-sh/ruff)
[![Free forever](https://img.shields.io/badge/free-forever-2ea44f.svg)](#license)

[Quick start](#quick-start) ·
[OBS integration](#obs-studio-integration) ·
[How it works](#how-it-works) ·
[Configuration](#configuration) ·
[Contributing](#contributing)

</div>

---

## The problem

Someone in the sound booth is listening for the preacher to say a
reference, typing it into presentation software, finding the right
translation, and clicking through. They are half a verse behind all
service, and when the preacher goes off-script they are lost.

VerseSync listens instead. When a reference is spoken, in English or
Yorùbá, the verse appears on the projector and in the stream on its own.

```
  "Turn with me to Romans eight, twenty-eight"
                    |
              [ microphone ]
                    |
     Silero VAD --> Whisper --> reference parser --> SQLite
                                                       |
                              +------------------------+
                              |                        |
                   OBS Browser Source          projector / stream
```

## Key features

- **Understands how people actually speak.** `"Romans eight twenty-eight
  through thirty"`, `"open your bibles to first thessalonians five
  sixteen"`, `"Psalm 145 verse 5"`, and `"the next chapter"` all resolve
  correctly. Written form works too.
- **Yorùbá scripture phrasing, natively.** `"Johanu kini ori keta ese
  kerin"` resolves to 1 John 3:4 through a Yorùbá ordinal table and book
  lexicon, not a translation layer.
- **A real OBS Studio overlay.** A transparent Browser Source page with
  three themes, which survives scene changes and reconnects on its own.
  Not a screenshot of a terminal.
- **Works without a microphone.** Drive the overlay from the API, a
  phone, or a Stream Deck. Useful for rehearsal, and for when the
  preacher goes somewhere the parser did not follow.
- **Three bundled translations**, two English and one Yorùbá, 93,287
  verses in local SQLite. No network call to show a verse.
- **Access control that fits a church.** One admin PIN, devices approved
  by reading a 6-digit code aloud, three roles, immediate revocation, and
  an audit log of who changed what. No user accounts to administer.
- **Degrades instead of failing.** No GPU, no internet, no Groq key, no
  OBS: each of those removes a capability and nothing else. The verse on
  the screen is the product, and nothing optional is allowed to
  interrupt it.

## What it looks like

The real overlay, captured from a running server in Chromium — the same
engine an OBS Browser Source uses. Regenerate them yourself any time with
`python scripts/capture_screenshots.py`.

<img src="docs/images/overlay-lowerthird.png" alt="The lowerthird theme showing Romans 8:28-30 in the KJV over a stage-lit background" width="100%">

<p align="center"><em><code>theme=lowerthird</code> — the default. A panel in the bottom-left, sized to leave the preacher visible.</em></p>

<table>
<tr>
<td width="50%"><img src="docs/images/overlay-caption.png" alt="The caption theme showing Psalm 23:1 as a full-width strip along the bottom of the frame"></td>
<td width="50%"><img src="docs/images/overlay-fullscreen.png" alt="The fullscreen theme showing John 3:16 as large centred text on a dark background"></td>
</tr>
<tr>
<td align="center"><em><code>theme=caption</code> — a broadcast-style strip.</em></td>
<td align="center"><em><code>theme=fullscreen</code> — for a scene with no camera behind it.</em></td>
</tr>
</table>

<img src="docs/images/overlay-yoruba.png" alt="The lowerthird theme showing John 3:16 in Yoruba, with tone marks and dotted characters rendering correctly" width="100%">

<p align="center"><em>Yorùbá, with tone marks and sub-dots intact — the reason the project exists.</em></p>

<details>
<summary><b>Proof the background is really transparent</b></summary>

<br>

<img src="docs/images/overlay-transparent.png" alt="The same overlay captured with no background at all, showing the transparent alpha channel around the panel" width="100%">

The same overlay captured with `omit_background`, so everything outside
the panel is genuine alpha. This is what OBS composites over your camera:
no chroma key, no black box.

</details>

> The background behind the overlay in these shots is a gradient drawn in
> code by the capture script, standing in for a camera feed. It is not a
> photograph of anyone's service.

## Technology stack

| Layer | Choice | Why |
|---|---|---|
| API | FastAPI + Uvicorn | Async, WebSockets, free OpenAPI docs |
| Storage | SQLite + FTS5 | 93k verses, single file, no server to run |
| Bible source | USFM from [eBible.org](https://ebible.org) | The SIL standard; USFM book codes are stable across translations |
| Voice activity | [Silero VAD](https://github.com/snakers4/silero-vad) | ~5 MB ONNX; only transcribe actual speech |
| Transcription | [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (CTranslate2) | Local, GPU or CPU, multilingual |
| Cloud STT fallback | Groq hosted Whisper | No 3 GB download; better Yorùbá than local `medium` |
| Parser fallback | Groq Llama 3.3 70B | Only when regex and the Yorùbá pass both miss |
| Overlay | Plain HTML/CSS/JS, no build step | An OBS machine at a church is often offline |
| OBS control | obs-websocket v5, implemented directly | ~40 lines of handshake beats a dependency |
| Auth | scrypt (stdlib) + PyJWT | Memory-hard hashing with no compiled dependency; an audited JWT library rather than a hand-rolled one |

## Requirements

**For the API, Bible engine, parser and OBS overlay:**

- Python 3.11 or newer (tested on 3.11, 3.12 and 3.13)
- About 60 MB of disk for the verse database
- No GPU, no internet after setup

**Additionally, for live microphone transcription:**

- A microphone
- An NVIDIA GPU is strongly recommended. CPU works but is slow.
- 2-6 GB of disk for Whisper models
- Internet on first run, to download the model

**Optional:**

- OBS Studio 28 or newer (obs-websocket v5 is built in from 28)
- A [Groq API key](https://console.groq.com/keys) for the LLM parser
  fallback and cloud transcription

## Installation

<details open>
<summary><b>Windows (PowerShell)</b></summary>

```powershell
git clone https://github.com/Iyanuoluwa007/VerseSync.git
cd VerseSync
.\setup.ps1
```

Add live transcription with `.\setup.ps1 -WithSTT`.

</details>

<details>
<summary><b>macOS / Linux</b></summary>

```bash
git clone https://github.com/Iyanuoluwa007/VerseSync.git
cd VerseSync
./setup.sh
```

Add live transcription with `./setup.sh --with-stt`.

</details>

<details>
<summary><b>Manual, if you prefer to see each step</b></summary>

```bash
git clone https://github.com/Iyanuoluwa007/VerseSync.git
cd VerseSync
python -m venv backend/.venv
```

Activate it (`backend\.venv\Scripts\Activate.ps1` on Windows,
`source backend/.venv/bin/activate` elsewhere), then:

```bash
pip install -r backend/requirements-dev.txt
cp backend/.env.example backend/.env
python scripts/download_bibles.py
python scripts/ingest_bibles.py
```

</details>

The setup script installs dependencies, creates `backend/.env`, downloads
the three translations from eBible.org, ingests 93,287 verses into SQLite
and runs the test suite. It is safe to re-run.

## Quick start

Start the server from `backend/`:

```bash
uvicorn app.main:app --port 8000
```

Put a verse on screen without saying a word:

```bash
curl -X POST http://localhost:8000/projector/show -H "Content-Type: application/json" -d "{\"text\":\"John three sixteen\"}"
```

Then open <http://localhost:8000/projector> and you should see it.
That page is what you point OBS at.

Other things worth opening:

| URL | What it is |
|---|---|
| <http://localhost:8000/> | Health and version |
| <http://localhost:8000/docs> | Interactive API documentation |
| <http://localhost:8000/projector> | The OBS overlay |
| <http://localhost:8000/verse/JHN/3/16?translation=YOR> | A verse, in Yorùbá |
| <http://localhost:8000/translations> | What is installed |

For live transcription (requires the STT extras):

```bash
python scripts/listen.py --language en --translation KJV
```

```bash
python scripts/listen.py --language yo --translation YOR
```

## OBS Studio integration

**Full walkthrough: [docs/OBS.md](docs/OBS.md).** The short version:

1. In OBS: **Sources -> + -> Browser**
2. URL: `http://localhost:8000/projector`
3. Width **1920**, Height **1080** (match your canvas)
4. Tick **Shutdown source when not visible**
5. **Clear the Custom CSS box** — OBS pre-fills a rule that fights the
   page's own styling

Ask the server for the exact URL and settings, so you never guess the
host:

```bash
curl http://localhost:8000/projector/obs-url
```

### Why this works properly with OBS

| Behaviour | What it means for you |
|---|---|
| Transparent background | Composites straight over your camera, no chroma key |
| Retained display state | Switching scenes brings the current verse *back*, instead of a blank frame |
| Automatic reconnection with backoff | OBS starting before VerseSync is a non-event |
| Zero external assets | Works on a locked-down or offline church network |
| Viewport-relative sizing | One URL is correct at 720p, 1080p, 1440p and 4K |
| Verse text rendered as text, never markup | Content arriving over a socket cannot execute in your stream |
| `no-store` on the page | No stale overlay after an upgrade |

### Overlay options

```
http://localhost:8000/projector?theme=caption&hold=0&fontScale=1.2
```

| Parameter | Values | Default |
|---|---|---|
| `theme` | `lowerthird`, `caption`, `fullscreen` | `lowerthird` |
| `bg` | `transparent`, `dark`, `light`, `green` | `transparent` |
| `hold` | seconds, `0` = until replaced | `12` |
| `fontScale` | `0.3`-`4.0` | `1.0` |
| `showRef`, `showTranslation` | `true`, `false` | `true` |
| `maxVerses` | `1`-`50` | `8` |
| `debug` | `true`, `false` | `false` |

### Other OBS workflows

| Workflow | Status |
|---|---|
| **Browser Source** | The main integration. See above. |
| **OBS WebSocket (v5)** | Optional. A verse can show/hide a scene item or fill an OBS text source. Set `OBS_WS_ENABLED=true`. |
| **Virtual Camera** | Works with no extra configuration. Add the Browser Source to your scene and start the Virtual Camera. |
| **RTMP / streaming** | Works with no extra configuration. The overlay is composited before encoding. |
| **Window / Display Capture** | Supported as a fallback. Use `?bg=green` with a Chroma Key filter on capture paths without alpha. |
| **Audio/video sync** | A verse appears 1-4 s after the reference is spoken, inherent to waiting for a complete utterance. [Compensating with a stream delay](docs/OBS.md#latency-and-audiovideo-sync) is covered in the OBS guide. |

## Access control

Off until you switch it on:

```bash
curl -X POST http://localhost:8000/auth/setup-pin -H "Content-Type: application/json" -d "{\"pin\":\"cornerstone-77\"}"
```

Then devices join by reading a code aloud to whoever holds the PIN:

```bash
curl -X POST http://localhost:8000/auth/register-device -H "Content-Type: application/json" -d "{\"name\":\"Sanctuary booth\",\"role\":\"operator\"}"
```

```bash
curl -X POST http://localhost:8000/auth/approve-device -H "Content-Type: application/json" -d "{\"pin\":\"cornerstone-77\",\"code\":\"584803\",\"role\":\"operator\"}"
```

The device then sends `Authorization: Bearer <token>` on every request.

| Role | Can do |
|---|---|
| `projector` | Receive verses, read scripture. Nothing else. |
| `operator` | Drive the overlay, the parser and the STT pipeline. |
| `admin` | Everything, plus manage devices, read the audit log, control OBS. |

Revocation takes effect on the device's **next request**, not whenever
its token expires, because every request re-checks the device record.
Five wrong PINs in 15 minutes locks the PIN, persisted so a restart is
not an escape.

Full walkthrough, including what to do when you lock yourself out:
**[docs/AUTH.md](docs/AUTH.md)**.

## How it works

```
backend/app/
├── bible/        USFM parsing, SQLite ingest, verse lookup
├── parser/       Speech -> (book, chapter, verse)
│   ├── numbers.py         "twenty-eight" -> 28, splits "three sixteen" -> [3, 16]
│   ├── lexicon.py         653 book-name patterns, English + Yorùbá
│   ├── english_spoken.py  "Psalm 145 verse 5" -> "Psalm 145:5"
│   ├── yoruba.py          "ori keta ese kerin" -> "3 4"
│   └── llm.py             Groq fallback, with a circuit breaker
├── stt/          Mic capture -> VAD -> Whisper -> parser
├── projector/    The OBS Browser Source overlay
├── obs/          obs-websocket v5 client (optional)
├── auth/         Admin PIN, device tokens, roles, audit log
└── core/         Settings and the projector event hub
```

The parser tries, in order: written regex, spoken-number normalisation,
context resolution (`"the next chapter"`), then the LLM. First hit wins,
and a reference outside a book's real chapter range is rejected rather
than displayed, which is what stops a Whisper mishearing from putting
"Matthew 255:1" on the screen.

Everything that puts a verse on screen publishes to one event hub, so the
overlay behaves identically whether the verse came from the microphone or
from an API call.

### Performance

Measured on this machine (Windows 11, Python 3.13) with
`backend/tests` as the correctness guard. The parser runs on the live
transcription thread, and the case that matters is the **miss** path,
because most of what a preacher says contains no scripture reference:

| Operation | Before | After |
|---|---|---|
| `parse()`, no reference in the text | 27.9 ms | 0.027 ms |
| `parse()`, English spoken reference | 0.72 ms | 0.023 ms |
| `parse()`, Yorùbá reference | 2.30 ms | 0.026 ms |
| Bible ingest, 93,287 verses | 260 s | 7.7 s |

The parser numbers come from replacing 653 sequential regex searches with
one compiled alternation, and ~3,900 Yorùbá substitutions with five. The
ingest number comes from committing once rather than per statement. See
[CHANGELOG.md](CHANGELOG.md) for the details.

## Configuration

Copy `backend/.env.example` to `backend/.env`. Every value is optional.

| Variable | Default | What it does |
|---|---|---|
| `VERSESYNC_HOST` | `127.0.0.1` | Set to `0.0.0.0` **only** if OBS is on another machine. Read [SECURITY.md](SECURITY.md) first. |
| `VERSESYNC_PORT` | `8000` | |
| `VERSESYNC_DEFAULT_TRANSLATION` | `KJV` | |
| `VERSESYNC_CORS_ORIGINS` | localhost only | Comma-separated. Empty disables CORS. |
| `VERSESYNC_DB_PATH` | `backend/data/versesync.db` | Holds the PIN hash and signing key; keep it off network shares. |
| `VERSESYNC_REQUIRE_AUTH` | `false` | `true` refuses protected routes until a PIN is set |
| `VERSESYNC_PUBLIC_PROJECTOR` | `true` | `false` requires a token on the overlay too |
| `PROJECTOR_THEME` | `lowerthird` | Default overlay theme |
| `PROJECTOR_HOLD_SECONDS` | `12` | `0` keeps a verse up until replaced |
| `PROJECTOR_FONT_SCALE` | `1.0` | |
| `OBS_WS_ENABLED` | `false` | Turn on OBS scene control |
| `OBS_WS_PORT` | `4455` | Match OBS |
| `OBS_WS_PASSWORD` | *(empty)* | From OBS -> Tools -> WebSocket Server Settings |
| `OBS_SCENE_NAME`, `OBS_SCENE_ITEM` | *(empty)* | Scene item toggled on a verse |
| `OBS_TEXT_SOURCE` | *(empty)* | OBS text source kept in sync |
| `GROQ_API_KEY` | *(empty)* | LLM parser fallback and cloud Whisper |
| `STT_MODEL_SIZE` | `medium` | `tiny`…`large-v3`. `large-v3` is much better for Yorùbá. |
| `STT_DEVICE` | `cuda` | `cuda` or `cpu` |
| `STT_LANGUAGE` | `en` | `en`, `yo` or `auto` |

A malformed value fails at startup with a message naming the variable,
rather than silently falling back to a default.

## Usage examples

<details>
<summary><b>Parse a reference from speech</b></summary>

```bash
curl -X POST http://localhost:8000/parse -H "Content-Type: application/json" -d "{\"text\":\"open your bibles to first thessalonians five sixteen\"}"
```

```json
{
  "reference": {
    "book": "1TH", "book_name": "1 Thessalonians",
    "chapter": 5, "verse_start": 16, "verse_end": null,
    "source": "regex_spoken", "confidence": 1.0
  }
}
```

</details>

<details>
<summary><b>Parse and fetch in one call</b></summary>

```bash
curl -X POST "http://localhost:8000/parse-and-fetch?translation=YOR" -H "Content-Type: application/json" -d "{\"text\":\"Johanu kini ori keta ese kerin\"}"
```

</details>

<details>
<summary><b>Drive the overlay directly</b></summary>

```bash
curl -X POST http://localhost:8000/projector/show -H "Content-Type: application/json" -d "{\"book\":\"ROM\",\"chapter\":8,\"verse_start\":28,\"verse_end\":30}"
```

```bash
curl -X POST http://localhost:8000/projector/clear
```

</details>

<details>
<summary><b>Subscribe to detections from your own client</b></summary>

```javascript
const ws = new WebSocket("ws://localhost:8000/ws/transcripts");
ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);
  if (msg.type === "detection" && msg.verses.length) {
    console.log(msg.reference.book_name, msg.reference.chapter, msg.verses);
  }
};
```

On connect you are immediately sent whatever is currently on screen, so a
client that reconnects mid-service catches up.

</details>

<details>
<summary><b>Command-line verse lookup</b></summary>

```bash
python scripts/query_verse.py --list
python scripts/query_verse.py JHN 3:16 --translation YOR
python scripts/query_verse.py ROM 8:28-30 --translation WEB
```

</details>

<details>
<summary><b>Live listening</b></summary>

```bash
python scripts/listen.py --list-devices
python scripts/listen.py --test-mic --mic 1
python scripts/listen.py --language yo --translation YOR --debug
python scripts/listen.py --engine groq --language yo   # cloud, no download
python scripts/listen.py --no-cloud-fallback           # fully local
```

</details>

## Bundled translations

| Code | Name | Language | License | Verses |
|---|---|---|---|---|
| `KJV` | King James Version (1611) | English | Public Domain | 31,102 |
| `WEB` | World English Bible | English | Public Domain | 31,098 |
| `YOR` | Bíbélì Mímọ́ ní Èdè Yorùbá Òde-Òní | Yorùbá | CC BY-SA 4.0 | 31,087 |

Downloaded from [eBible.org](https://ebible.org) at install time, never
committed to this repository. Full attribution in
[LICENSES.md](LICENSES.md); more translations are tracked in
[docs/BACKLOG.md](docs/BACKLOG.md).

## Supported platforms

| Platform | API + parser + OBS overlay | Live transcription |
|---|---|---|
| Windows 10/11 | Verified | Verified |
| Linux | CI (Ubuntu, Python 3.11-3.13) | Should work; not verified by the maintainer |
| macOS | Should work; not verified by the maintainer | CPU only, no CUDA |

Python 3.11, 3.12 and 3.13 are exercised in CI on Windows and Linux.

## Development

```bash
pip install -r backend/requirements-dev.txt
```

```bash
ruff check .
```

```bash
cd backend && pytest
```

**583 tests**, running in about 20 seconds, with no network access and no
Bible database required — every test that needs verses builds its own
temporary one — including the auth database, so the suite cannot be
affected by whether you have set a PIN on your own install. Groq and OBS
are covered with fakes, and the auth suite includes `alg: none` and
wrong-key token forgeries.

Most of that runtime is deliberate: the admin PIN is hashed with scrypt
tuned to roughly 100 ms per verification, and the auth tests exercise it
for real rather than weakening the parameters.

CI runs the suite on Windows and Linux across Python 3.11-3.13, plus a
secret scan and a check that the app still starts without the optional
STT dependencies installed.

See [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request.

## Troubleshooting

<details>
<summary><b>The OBS Browser Source is blank</b></summary>

Open the same URL in a normal browser with `?debug=1` to see a connection
badge. `reconnecting...` means the server is unreachable; if OBS is on
another machine, check you used the LAN IP and started with
`--host 0.0.0.0`. Full checklist in [docs/OBS.md](docs/OBS.md#troubleshooting).

</details>

<details>
<summary><b>Nothing is detected when I speak</b></summary>

Work down the pipeline:

```bash
python scripts/listen.py --test-mic --mic 1
```

`rms` above 0.005 means you are being heard. If it stays near zero, it is
the microphone or the device index, not VerseSync.

Then run with `--debug` and watch the heartbeat: `vad_prob` above 0.30
means speech is being detected. If audio arrives but the VAD never fires,
lower the threshold with `--vad-threshold 0.2`.

If transcripts appear but no reference is detected, the parser is the
problem, not the audio. Paste the transcript into `POST /parse` and open
an issue with it.

</details>

<details>
<summary><b>Yorùbá transcription is poor</b></summary>

Whisper `medium` has weak Yorùbá. Use `large-v3`, or the cloud engine:

```bash
python scripts/listen.py --engine groq --language yo --translation YOR
```

Whisper also tends to smash Yorùbá word boundaries (`"Johan nukini"` for
`"Johanu kini"`), which the parser cannot recover from. This is a known
limitation, tracked below.

</details>

<details>
<summary><b>Yorùbá text prints as garbage or crashes in the terminal</b></summary>

This was a real bug and is fixed in 0.5.0; every CLI script now forces
UTF-8 output. If you are on an older version, upgrade. In Windows
Terminal you can also run `chcp 65001` first.

</details>

<details>
<summary><b>faster-whisper or torch will not install</b></summary>

They lag new Python releases and need a matching CUDA runtime. The API,
the Bible engine, the parser and the whole OBS overlay work without
them — that is a supported way to run VerseSync. Install just
`backend/requirements.txt` and drive the overlay from the API.

</details>

<details>
<summary><b>"not found. Has the translation been ingested?"</b></summary>

```bash
python scripts/download_bibles.py && python scripts/ingest_bibles.py
```

</details>

## Security considerations

VerseSync has an admin PIN, device tokens with roles, immediate
revocation and an audit log. **None of it is active until you set a
PIN** — a fresh install is open, so that upgrading cannot lock an
operator out mid-service.

```bash
curl -X POST http://localhost:8000/auth/setup-pin -H "Content-Type: application/json" -d "{\"pin\":\"choose-a-real-one\"}"
```

`GET /` tells you which state you are in and warns while unprotected. Set
`VERSESYNC_REQUIRE_AUTH=true` to fail closed instead.

Two things to know:

- **The display path stays open by default.** An OBS Browser Source
  cannot send an `Authorization` header, so `/projector` and
  `/ws/transcripts` expose verse text without one. Set
  `VERSESYNC_PUBLIC_PROJECTOR=false` and pass `?token=` to change that.
- **There is no TLS.** On a LAN, treat the network as trusted or put
  VerseSync behind a VPN. Do not expose it to the internet.

Read [SECURITY.md](SECURITY.md) for the threat model and — importantly
for a church — exactly which configurations send congregation audio off
the machine and which do not.

## Roadmap

- [x] Bible engine: USFM ingest, verse lookup, REST API
- [x] Reference parser: English, Yorùbá, LLM fallback
- [x] STT pipeline: VAD, Whisper, tiered engine with cloud fallback
- [x] OBS Browser Source overlay
- [x] OBS WebSocket control
- [x] Authentication: admin PIN, device tokens, roles, audit log
- [ ] Operator dashboard: correct or override a detection live
- [ ] Better Yorùbá recognition: `initial_prompt` biasing and
      fuzzy ordinal matching for smashed word boundaries
- [ ] More open-licensed translations (see [docs/BACKLOG.md](docs/BACKLOG.md))
- [ ] Next.js projector view for non-OBS setups

## Contributing

Contributions are welcome, particularly around **Yorùbá accuracy**,
**additional open-licensed translations**, and **OBS workflows we have
not covered**. Start with [CONTRIBUTING.md](CONTRIBUTING.md).

## License

**VerseSync is free, and every feature is available to everyone.** There
is no paid tier, no licence key, no hosted plan, no feature held back.
Use it in your church, fork it, sell services around it, ship it inside
something else. That is what the MIT licence is for.

Source code: [MIT](LICENSE).

The bundled Bible translations are **separately licensed** and are not
covered by the MIT licence. The Yorùbá text is CC BY-SA 4.0 with a
trademark requirement. See [LICENSES.md](LICENSES.md).

Some well-known translations (NIV, ESV, NLT and others) are missing not
because of anything VerseSync charges for, but because their copyright
holders do not license them for redistribution in an open-source
application. See [docs/BACKLOG.md](docs/BACKLOG.md).

**Running costs are yours and optional.** VerseSync itself never bills
you. Local Whisper and the bundled Bibles cost nothing at all. The only
thing that can cost money is if *you* choose to use Groq for cloud
transcription or the LLM parser fallback, billed by Groq directly on your
own key. Leave `GROQ_API_KEY` unset and the whole system runs free and
offline.

## Acknowledgements

- [eBible.org](https://ebible.org) for maintaining and distributing USFM
  scripture, including the Yorùbá text
- [Biblica](https://www.biblica.com) for releasing the Open Yorùbá
  Contemporary Bible under CC BY-SA 4.0
- [SYSTRAN](https://github.com/SYSTRAN/faster-whisper) for faster-whisper
  and [OpenAI](https://github.com/openai/whisper) for Whisper
- [Silero](https://github.com/snakers4/silero-vad) for the VAD model
- The [OBS Project](https://obsproject.com), and the obs-websocket
  maintainers for a protocol document clear enough to implement from

## Support

- **Bugs and features**: [open an issue](https://github.com/Iyanuoluwa007/VerseSync/issues)
- **Questions and setup help**: [Discussions](https://github.com/Iyanuoluwa007/VerseSync/discussions)
- **Security**: [private vulnerability reporting](https://github.com/Iyanuoluwa007/VerseSync/security/advisories/new), not a public issue

<div align="center">

If VerseSync is useful to your church, a star helps others find it.

</div>
