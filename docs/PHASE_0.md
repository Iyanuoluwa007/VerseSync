# Phase 0 — Foundation

Five modules, each independently testable. End state: speak into a mic,
see references print to the terminal with timestamps. Auth foundation
gates remote control endpoints.

## Module 1 — Repo skeleton (this commit)

- Folder structure created.
- FastAPI runs and returns health JSON.
- venv configured, requirements pinned.
- Smoke test passes.

Verify:
1. `uvicorn app.main:app --reload --port 8000`
2. http://localhost:8000 returns `{"status":"ok",...}`.
3. `pytest -v` shows 1 passing test.

## Module 2 — Bible engine (this commit)

- SQLite schema for translations + books + verses + FTS5 index
- USFM parser (handles Strong's, words of Jesus, footnotes, Yoruba diacritics)
- Ingest script populates DB from KJV/WEB/YOR USFM zips
- Query module: `get_verse`, `get_passage`, `list_translations`
- REST endpoints: `/verse/{book}/{ch}/{v}`, `/passage/{book}/{ch}/{s}-{e}`, `/translations`
- LICENSES.md with full attribution for CC BY-SA Yoruba Bible
- 18 new tests (USFM parser + Bible engine integration)

Verified: 93,287 verses across 3 translations × 66 books, ~30 MB DB.

Verify on your machine:
1. `python scripts/download_bibles.py` -> 3 zips in data/bibles/
2. `python scripts/ingest_bibles.py` -> versesync.db with ~93k verses
3. `python scripts/query_verse.py JHN 3:16 --translation KJV` -> verse text
4. `python scripts/query_verse.py JHN 3:16 --translation YOR` -> Yoruba verse
5. `pytest -v` -> 19 passing
6. uvicorn + curl http://localhost:8000/verse/JHN/3/16?translation=KJV -> JSON

## Module 3 — Scripture parser (this commit)

- Number-word normaliser: "twenty-three" -> 23, "one hundred and nineteen" -> 119
- Book lexicon: 653 patterns (English + auto-captured Yoruba) sorted longest-first
- Filler stripper: "open your bibles to", "turn with me to", "the book of"
- Regex parser: written ("John 3:16") and spoken ("John three sixteen") forms
- Single-chapter book special-case: "Jude 24" -> JUD 1:24
- State machine: "the next chapter", "verses 9-10", "verse twelve" resolve against context
- LLM fallback (Groq Llama 3.3 70B) with circuit breaker for ambiguous cases
- 113 new tests covering every path; LLM tests use mocks (no network)
- 2 new endpoints: `POST /parse`, `POST /parse-and-fetch`

Verified: 154 tests pass, 1.34s. Sample inputs that work:
- "John 3:16" / "John 3:16-18" / "1 John 4:8" / "Jude 24"
- "Romans eight twenty-eight through thirty"
- "open your bibles to first thessalonians five sixteen"
- "Johanu 3:16" (Yoruba) / "1 Kọrinti 13:4" / "Ifihan 22:13"
- "the next chapter" / "verses 9 and 10" (with context)

Verify on your machine:
1. `pytest -v` -> 154 passing
2. `curl -X POST localhost:8000/parse -d '{"text": "Romans eight twenty-eight"}'`
3. `curl -X POST 'localhost:8000/parse-and-fetch?translation=YOR' -d '{"text": "johanu 3:16"}'`
4. With `GROQ_API_KEY` set in `.env`, `/parse` returns `llm_available: true`.

## Module 4 — STT pipeline (this commit)

- Mic capture: `sounddevice` (cross-platform PortAudio) at 16 kHz mono
  float32, 32 ms chunks, drop-oldest backpressure on overflow.
- VAD: Silero ONNX model with stateful start/end events; min 190ms speech
  to gate clicks, min 700ms silence to close a segment.
- Whisper engine: `faster-whisper` with switchable `cuda`/`cpu` device,
  `medium` default model, language `en`/`yo`/`auto` per session.
- Pipeline orchestrator: PortAudio thread -> queue -> pipeline thread ->
  VAD per chunk -> Whisper per segment -> parser -> Detection callback.
- Hard cap: 30s max segment to prevent unbounded buffer growth.
- WebSocket router: `/stt/start`, `/stt/stop`, `/stt/language`,
  `/stt/status`, `/stt/devices`, `/ws/transcripts`.
- Standalone CLI: `python scripts/listen.py [--list-devices | --language yo |
  --translation YOR | --model small | --device cpu | --mic 2]`.
- Graceful degradation: endpoints return clean 503s with install
  instructions if `requirements-stt.txt` deps are missing.
- 16 STT tests + 34 Yoruba phrasing tests (mock-based, no real audio needed).

### v0.4.4 patch (parser bounds + Whisper hallucination filter)

The first live runs in v0.4.3 surfaced two real failure modes. Fixed in this patch:

**Parser chapter-bounds validation.** Whisper sometimes mishears
compound references like "Matt 25:5" as "Matt255" (no space), and the
regex parser was happily emitting `MAT 255:1`. Matthew has 28 chapters.
The new `BOOK_MAX_CHAPTERS` table in `app/bible/books.py` covers all 66
books; the parser now rejects out-of-range chapters and falls through
to the LLM, which handles malformed input better. Verified: "Matt255"
-> None, "Psalm 150:1" -> PSA 150:1 (valid), "Psalm 151:1" -> None.

**Whisper hallucination filter.** Whisper-medium on poor Yoruba audio
hallucinates entire transcripts in *unrelated scripts* -- Tibetan
(སེེེེེ), Bengali (বেবববব), Punjabi (ਸੀਸੀਸੀ), and pure repetition
(ʻʻʻʻʻ) -- all with `confidence=1.00`. faster-whisper's compression-ratio
threshold logs the warning but still emits the output. The pipeline now
drops these before the parse step using two heuristics:

- Repetition: >70% of characters are the same character -> drop.
- Script mismatch: for `lang=en/yo`, <50% of alphabetic chars in the
  Latin range -> drop.

Saves Groq API quota and prevents fake detections from poisoning the
context state.

**Runtime warning when using non-large model with Yoruba.** The medium
model has weak Yoruba support. `listen.py --language yo` now prints a
warning unless `--model large-v3` is also set, with the explanation
that smaller models will produce sparse but accurate transcripts.

Verified in sandbox with the exact garbled transcripts from the live
session -- 4/4 garbage caught, 0 false positives on real Yoruba or
English. 230/230 tests pass (was 204; +26 new tests for bounds and
hallucination filter).

For Yoruba production work, run with `--model large-v3` (~3 GB,
significantly better Yoruba acoustic modelling). Medium remains fine
for English-only sessions.

### v0.4.3 patch (Windows audio robustness)


The v0.4.2 listener captured 0 RMS on every Windows mic. Several
real Windows-audio quirks bite at the same time:

- Some mics enumerate as 2-channel but only carry audio on one channel;
  requesting `channels=1` from PortAudio silently picks the dead channel.
- WASAPI rejects non-native sample rates with PaErrorCode -9997 (this
  was the device-18 error in v0.4.2).
- MME accepts arbitrary rates but its internal resampler can produce
  zeros if channel mapping disagrees with the request.

Fix: capture at the device's NATIVE sample rate and channel count,
downmix to mono, software-resample to 16 kHz in numpy, then chunk into
clean 512-sample blocks. This path is identical regardless of host API
and sidesteps every one of those quirks.

What changed:
- `app/stt/audio.py`: rewritten capture using native rate + channels,
  internal rolling buffer, linear resampler. `MicrophoneStream` now
  exposes `.native_sample_rate` and `.native_channels` for visibility.
- `scripts/listen.py`: prints `Native input: sr=44100 ch=2 -> resampled
  to 16000Hz mono` on startup so the resampling chain is visible.
- Verified in sandbox: stereo with audio on one channel + silence on
  the other produces correct mono RMS (0.176 from a 0.354-amplitude
  sine on the live channel).

Verification recipe (do BEFORE applying this patch):
1. **Hardware mute**: check for a function-key mic mute on your laptop.
2. **Windows mic privacy**: `start ms-settings:privacy-microphone` --
   all three toggles ON, especially "Let desktop apps access your
   microphone".
3. **Voice Recorder test**: open Windows Voice Recorder, record 5 s,
   play back. If silent there too, fix the OS first; no patch helps.

After Voice Recorder confirms the mic works, apply v0.4.3 and run:
```powershell
python scripts\listen.py --test-mic --mic 1
# Should now print: Native input: sr=44100 ch=2 -> resampled to 16000Hz mono
# RMS should jump above 0.005 when you talk.
```

### v0.4.2 patch (mic capture fix + diagnostic logging)


The v0.4.1 listener was silent: mic captured nothing visible to whisper.
Two real bugs:

1. **First ~190 ms of speech was dropped.** The pipeline only collected
   chunks once `vad.is_speaking` was `True`, but Silero needs continuous
   speech for ~190 ms to flip that flag. Result: every utterance started
   mid-word; if it was short enough, Whisper got nothing useful.
2. **Wrong Silero VAD API.** The previous code called the raw model
   directly. Silero 5.x ships a proper streaming class `VADIterator`
   with internal state, hysteresis, and `speech_pad_ms` -- which is
   what we now use.

Fixes shipped in v0.4.2:

- `app/stt/vad.py` rewritten on top of `silero_vad.VADIterator`. New
  defaults: threshold=0.3 (was 0.5; preachers start sentences quietly),
  speech_pad=200 ms, min_silence=500 ms.
- `app/stt/pipeline.py` keeps an 8-chunk pre-roll ring buffer (~256 ms).
  When VAD signals "start", we PREPEND that ring to the segment so the
  audio that triggered the detection isn't lost.
- New diagnostic logging: speech_start / speech_end events, transcribe
  timing, and a heartbeat (chunks received, RMS audio level, current
  VAD probability, speaking state) every 5 s in `--debug` mode or 30 s
  otherwise.
- `MicrophoneStream` now exposes `last_chunk_rms` so the heartbeat can
  show whether the mic is actually picking up sound.
- `scripts/listen.py` gains:
   - `--debug` for the verbose pipeline log.
   - `--test-mic` for a 15-second live RMS meter, no VAD, no whisper.
     This is the fastest way to diagnose "is my mic the right one?"
   - `--mic <id|substring>` accepts substring like `realtek` not just int.
   - `--vad-threshold` float to override the default if needed.

Verification recipe (do these in order):

```powershell
# 1. Confirm the mic is the right device
python scripts\listen.py --list-devices
python scripts\listen.py --test-mic --mic 1
# Speak. RMS should jump above 0.005 when you talk.
# If RMS stays at 0.000, --mic 1 is not your active input.

# 2. Verbose listen on the right mic
python scripts\listen.py --language en --mic 1 --debug
# You'll see [hb] heartbeats every 5s plus [VAD] speech START/END
# events when speech is detected, plus [whisper] timing on each segment.

# 3. Yoruba session
python scripts\listen.py --language yo --translation YOR --mic 1 --debug
```

Heartbeat columns:
- `chunks=N`: how many 32 ms blocks the mic has produced (470/15s)
- `rms=X.XXXX`: most recent audio level (>0.005 = speech, ~0.001 = quiet)
- `vad_prob=X.XX`: most recent Silero confidence (>0.30 = speech)
- `speaking=True/False`: whether we're currently inside a speech segment

### v0.4.1 patch (Yoruba scripture phrasing)

Recognises the natural Yoruba reference pattern preachers actually use:
"Johanu kini Ori keta Ese kerin" -> 1JN 3:4. Specifically:

- Yoruba ordinal numerals 1..30 with all the diacritic variants
  (kini/keji/keta/.../kọkanlelogun/ogbon).
- "Ori <ordinal>" or "Oriketa" (no space) -> chapter digit.
- "Ese <ordinal>" or "Esekerin" (no space) -> verse digit.
- Range word "si" between ordinals -> verse range.
- Book + ordinal suffix ("Johanu kini" / "Kọrinti keji") -> ordinal book.
- ASCII-folded matching, so any partial-tone-mark spelling works.
- Context follow-ups: after "Romu 8:1", "ese kefa" resolves to ROM 8:6.

Also fixed: `requests` added to `requirements-stt.txt` -- faster-whisper 1.1
needs it for downloading mel filter definitions but huggingface_hub 1.x
no longer pulls it in transitively.

Verify on your machine:
1. `pytest -q` -> 204 passing (no STT deps yet).
2. `pip install -r requirements-stt.txt` -> installs sounddevice +
   faster-whisper + silero-vad + torch + requests (~2 GB once whisper
   model downloads).
3. `python scripts/listen.py --list-devices` -> mic enumeration works.
4. `python scripts/listen.py --language yo --translation YOR` -> speak
   "Johanu kini ori keta ese kerin" -> 1JN 3:4 with Yoruba verse text.
5. uvicorn must run from `backend/`: `cd backend; uvicorn app.main:app`

### v0.4.5 patch (tiered STT engine + cloud fallback)

The `large-v3` and `large-v3-turbo` downloads in v0.4.4 were both
interrupted before completion -- 3 GB and 1.6 GB are a lot to fetch
sequentially during sermon prep. This patch adds a tiered engine that
will reach for whichever backend is actually available, with clear
status output so it's never a mystery which one is in use.

**Fallback chain** (default `--engine tiered`):

    1. PRIMARY     local whisper-large-v3        (~3.0 GB, best quality)
    2. FALLBACK 1  local whisper-large-v3-turbo  (~1.6 GB, faster)
    3. FALLBACK 2  Groq cloud Whisper API        (no download, ~$0.04/hr)

If a tier fails to load (network drop, partial download, VRAM OOM,
missing API key, etc.) the engine prints the reason and moves to the
next. KeyboardInterrupt always propagates -- Ctrl+C still quits cleanly.
The cloud tier requires `GROQ_API_KEY` (already configured for the LLM
parser); if absent it's skipped silently. `--no-cloud-fallback` keeps
the system fully local.

**Status output during load:**

    [*] STT engine: tiered (3 backends, automatic fallback)
    [*] [primary] Loading large-v3 on cuda  (~3.0 GB on first run)...
        First run downloads the model; subsequent runs reuse the cache.
    [OK] [primary] large-v3 ready in 142.3s
    [*] Active engine: local:large-v3

Or on a partial download:

    [!] [primary] large-v3 failed after 18.4s: ConnectionError: ...
        Falling through to next tier...
    [*] [fallback 1] Loading large-v3-turbo on cuda  (~1.6 GB)...
    [OK] [fallback 1] large-v3-turbo ready in 78.5s

**New: `scripts/preload_models.py`** -- run once when you have a stable
connection. Downloads both local models on CPU/int8 (no GPU touch),
prints the huggingface progress bar, summarises at the end. After
this, the tiered engine loads instantly from disk.

**Bonus fix: noisy-library log filtering.** The `httpcore` /
`httpx` / `filelock` debug streams were drowning out the
huggingface tqdm progress bar in v0.4.4 debug mode. They're now
clamped to WARNING regardless of `--debug`, so you can actually see
download progress.

**New cloud engine `app/stt/whisper_groq.py`:** a `GroqWhisperEngine`
mirroring `WhisperEngine`'s public surface. Sends 16 kHz mono audio
as in-memory WAV bytes via Groq's OpenAI-compatible
`/audio/transcriptions` endpoint. Soft-fails (returns empty
transcript) on transport / auth / rate-limit errors so the live
pipeline keeps moving rather than dying mid-sermon. Note: Groq pads
clips < 30s to 30s and bills accordingly -- effectively a rounding
error for sermon-length usage.

**CLI changes:**

    python scripts/listen.py                              # tiered (default)
    python scripts/listen.py --language yo                # tiered, Yoruba
    python scripts/listen.py --engine groq --language yo  # cloud-only
    python scripts/listen.py --engine local --model medium  # strict local
    python scripts/listen.py --no-cloud-fallback          # tiered without cloud

`--engine local` is the v0.4.4 behaviour (single explicit model);
`--engine groq` is the strict-cloud path; `--engine tiered` is the
new default.

29 new tests added (16 GroqWhisperEngine, 13 TieredWhisperEngine).
259/259 tests pass. No new dependencies -- `groq` was already in
`requirements.txt` for the LLM parser; everything else (numpy, wave,
io) is stdlib.

## Module 5 — Auth foundation (complete)

Shipped in v0.6.0. Full documentation: `docs/AUTH.md`.

Verified end to end against a running server: register -> approve -> use
a protected route -> revoke (revocation effective on the next request);
the audit log contains every step and no secrets; 5 wrong PINs inside 15
minutes triggers the lockout, survives a restart, and refuses even the
correct PIN while locked. 90 automated tests cover it, including
`alg: none` and wrong-key token forgeries.

Two deliberate deviations from the plan below, explained in `docs/AUTH.md`:
scrypt instead of Argon2id (no compiled dependency), and tokens validated
against the device record so revocation is immediate.

### As originally planned

- `POST /auth/setup-pin` — first-run only, sets admin PIN (Argon2id hash).
- `POST /auth/register-device` — returns 6-digit code + pending JWT.
- `POST /auth/approve-device` — admin endpoint, takes code + role.
- `POST /auth/revoke-device` — admin endpoint.
- JWT verification middleware on all non-public routes.
- Audit log table: every action with device_id, timestamp, payload.
- LAN-only network binding by default.

Verify:
- curl flow: register -> approve -> use JWT to hit a protected route -> revoke.
- Audit log shows every step.
- 5 wrong PINs in 15 min triggers lockout.

## Phase 0 done when

- All 5 modules have passing tests.  **Done: 580 tests passing.**
- End-to-end smoke: speak into mic, watch terminal print detected refs,
  watch audit log record the projector device joining.
- Phase 1 (Next.js projector view) can begin.
