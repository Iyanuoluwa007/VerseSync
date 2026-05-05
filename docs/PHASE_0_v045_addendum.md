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
