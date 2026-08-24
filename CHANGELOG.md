# Changelog

All notable changes to VerseSync. This project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html); it is pre-1.0,
so minor versions may still change behaviour.

## [0.5.0] - 2026-08-24

The release that makes VerseSync usable with OBS Studio, plus a
pre-public-release audit of everything that was already here.

### Added

- **Projector overlay for OBS Browser Source** (`GET /projector`). A
  self-contained page with three themes (`lowerthird`, `caption`,
  `fullscreen`), four backgrounds including transparent and chroma
  green, and URL parameters for hold time, font scale and what to
  display. No external assets, so it works on an offline machine.
- **Retained display state.** A Browser Source that reconnects -- on a
  scene change, a source refresh, or an OBS restart -- is immediately
  re-sent the verse currently on screen instead of going blank.
- **Automatic reconnection** in the overlay, with backoff, so OBS
  starting before VerseSync is a non-event.
- **Manual projector control**: `POST /projector/show` (free-form text
  through the parser, or an explicit reference) and
  `POST /projector/clear`. The whole OBS path can now be set up and
  tested without a microphone, a GPU or a Whisper model.
- **OBS WebSocket v5 client** (`app/obs/`), optional. A detected verse
  can show or hide an OBS scene item and fill an OBS text source.
  Failures are contained: OBS being closed never interrupts the overlay.
- `GET /projector/obs-url` returns a copy-pasteable Browser Source URL
  built from the requesting host, plus the OBS settings to use.
- `GET /obs/guide` serves the OBS setup checklist as data.
- `GET /healthz` for process supervisors.
- CORS support, configurable and localhost-only by default.
- Cross-platform `setup.sh` alongside `setup.ps1`.
- CI on Windows and Linux across Python 3.11-3.13, including a secret
  scan and a check that the app still starts without the STT extras.
- MIT `LICENSE`, `CONTRIBUTING.md`, `SECURITY.md`, issue and PR
  templates, and `docs/OBS.md`.

### Fixed

- **Yoruba scripture was unprintable on a default Windows console.**
  `python scripts/query_verse.py JHN 3:16 --translation YOR` -- a command
  documented in the README -- died with `UnicodeEncodeError` under
  cp1252 before printing anything. Every CLI script now forces UTF-8
  output through `scripts/_bootstrap.py`.
- **`listen.py --test-mic` was completely broken.** It used
  `with stream:` and `stream.read(...)`, neither of which
  `MicrophoneStream` implemented, so the command raised
  `AttributeError: __enter__` immediately. `MicrophoneStream` is now a
  context manager and exposes `read`.
- **The VAD ran Silero twice per audio chunk.** Reading the speech
  probability for diagnostics invoked the model a second time on the
  same chunk. Silero's streaming model is a stateful RNN, so this both
  doubled the per-chunk cost and advanced the hidden state twice per
  32 ms of audio, desynchronising the state `VADIterator` uses to decide
  where speech starts and ends. The probability is now captured from the
  single existing forward pass.
- **The reported version was wrong.** `main.py` hard-coded `0.4.4` while
  the project shipped as v0.4.6. There is now one `__version__`.
- **Several Yoruba marker words could never match.** `looks_yoruba`
  folds its input to ASCII, but the marker table contained accented
  spellings (`kọrinti`, `tẹsalonika`) that folding turns into
  `korinti`/`tesalonika`. Those entries were dead.
- **Parsing a reference created a database.** Building the book lexicon
  called `connect()`, which runs the schema bootstrap, so merely parsing
  text wrote an empty `versesync.db` to disk. The lexicon now opens the
  database read-only, and only if it already exists.
- **An interrupted Bible ingest left the database lying.** The
  `translations` row was written before the verses streamed in, so a
  failure part-way through left the API advertising an installed
  translation whose verses were missing. The ingest is now one
  transaction.
- `/stt/status` could 500 depending on which STT engine was active,
  because it assumed every engine exposes `device`.
- `/stt/stop` and `/stt/language` did not hold the pipeline lock, and
  `stop()` blocked the event loop while joining the worker thread.
- The microphone was never released on shutdown; Ctrl+C left PortAudio
  holding the input device.

### Changed

- **The reference parser is roughly 900x faster on the miss path.**
  `find_book_in_text` compiled and ran 653 separate regexes per call, so
  a line of speech containing no book name -- the overwhelmingly common
  case during a sermon -- cost 653 full scans. Measured on the same
  machine: 27.9 ms -> 0.031 ms per call.
- **The Yoruba normaliser is roughly 230x faster.** It ran one `re.sub`
  per (book, ordinal) and (marker, ordinal) pair, about 3,900
  substitutions for a single line. Measured: 2.00 ms -> 0.0088 ms.
- **Bible ingest is roughly 34x faster**, 260 s -> 7.7 s for the same
  93,287 verses, by committing once instead of per statement.
- The STT WebSocket channel and the projector now share one event hub,
  so the overlay works whether verses arrive from the microphone or from
  the API.
- The API no longer hard-fails to import when the STT extras are absent;
  the "projector-only" install profile is now a supported way to run.
- `ParsedRef` output includes `book_name`, so clients can render
  "John 3:16" rather than "JHN 3:16".
- Settings are validated at startup. A malformed value in `.env` fails
  with a message naming the variable instead of silently defaulting.
- The virtual environment moved from `backend/venv` to `backend/.venv`.

### Removed

- `scripts/apply_v046_parser_patch.py`, a one-shot migration script whose
  edit had already been applied.

## [0.4.6] - 2026-05-05

Spoken-English parser fix. Natural phrasings that live transcription
produced were being rejected: `Luke 5 5`, `Psalm 145 verse 5`,
`Revelation 2 2`, `Psalms 150 from verse 1 to 10` all detected nothing.
Added `app/parser/english_spoken.py` to rewrite spoken forms into the
canonical `Book N:M` shape before the regex sees them.

## [0.4.5] - 2026-05-05

Tiered STT engine. Tries local `large-v3`, then local
`large-v3-turbo`, then Groq's hosted Whisper, announcing each step so it
is never a mystery which backend is active. Added
`scripts/preload_models.py` and `app/stt/whisper_groq.py`.

## [0.4.x] - 2026-05

Phase 0 modules 1-4: FastAPI skeleton, the SQLite Bible engine with USFM
ingest and FTS5, the scripture reference parser (regex, Yoruba lexicon
and Groq LLM fallback), and the STT pipeline (faster-whisper, Silero VAD,
`sounddevice` capture).

[0.5.0]: https://github.com/Iyanuoluwa007/VerseSync/releases/tag/v0.5.0
