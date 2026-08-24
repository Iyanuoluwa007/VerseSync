"""VerseSync standalone mic listener.

Captures speech, runs it through the configured STT engine + parser,
and prints any detected verse references with the actual verse text.

Engines (--engine):
    tiered   default. Tries local large-v3 -> large-v3-turbo -> Groq cloud.
    local    explicit local-only. Use --model to pick the size.
    groq     explicit cloud-only. Requires GROQ_API_KEY.

Quick-start:
    python scripts/listen.py                                    # English, tiered
    python scripts/listen.py --language yo --translation YOR    # Yoruba, tiered
    python scripts/listen.py --engine groq --language yo        # Cloud-only Yoruba
    python scripts/listen.py --engine local --model medium      # Strict local
    python scripts/listen.py --test-mic --mic 1                 # Audio level only
    python scripts/listen.py --list-devices                     # Show input devices

Pre-download both local Whisper models in one go:
    python scripts/preload_models.py
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time

# Adds backend/ to sys.path and forces UTF-8 console output so
# Yoruba scripture can be printed on a default Windows console.
from _bootstrap import ROOT  # noqa: F401  (import for side effects)

# --------------------------------------------------------------
# Logging: keep the console focused on pipeline events. Quieting
# the noisy transport libraries lets the huggingface tqdm
# progress bar actually be visible during model downloads.
# --------------------------------------------------------------

NOISY_LIBRARIES = (
    "httpcore", "httpx", "urllib3", "filelock",
    "huggingface_hub.file_download",
    "huggingface_hub._snapshot_download",
)


def _setup_logging(debug: bool) -> None:
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )
    for name in NOISY_LIBRARIES:
        logging.getLogger(name).setLevel(logging.WARNING)


# --------------------------------------------------------------
# Helpers
# --------------------------------------------------------------

def _resolve_mic(spec, list_devices_fn):
    devs = list_devices_fn()
    if spec is None:
        return None, "default"
    if isinstance(spec, int):
        for d in devs:
            if d["id"] == spec:
                return spec, d["name"]
        print(f"[!] No input device with id={spec}; falling back to default.",
              file=sys.stderr)
        return None, "default"
    needle = str(spec).lower()
    matches = [d for d in devs if needle in d["name"].lower()]
    if not matches:
        print(f"[!] No mic matches {spec!r}; falling back to default.",
              file=sys.stderr)
        return None, "default"
    return matches[0]["id"], matches[0]["name"]


def _run_test_mic(mic_id, mic_name) -> int:
    from app.stt.audio import MicrophoneStream
    print(f"[*] Test mode -- monitoring audio level on device "
          f"{mic_id} ({mic_name!r}) for 15 s.")
    print("    Speak now. RMS > 0.005 means you're being heard.")
    print("    Bar legend: . silence  - quiet  + normal  # loud")
    print()
    stream = MicrophoneStream(device=mic_id)
    chunks = 0
    try:
        with stream:
            print(f"[*] Native input: sr={stream.native_sample_rate} "
                  f"ch={stream.native_channels} -> resampled to 16000Hz mono")
            t_end = time.time() + 15.0
            while time.time() < t_end:
                chunk = stream.read(timeout=0.5)
                if chunk is None:
                    continue
                chunks += 1
                rms = stream.last_chunk_rms
                if rms < 0.001:
                    bar = "." * 60
                elif rms < 0.005:
                    bar = "-" * min(60, int(rms * 12000))
                elif rms < 0.05:
                    bar = "+" * min(60, int(rms * 1200))
                else:
                    bar = "#" * 60
                sys.stdout.write(f"\r  rms={rms:.4f}  {bar:60s}")
                sys.stdout.flush()
    finally:
        print()
    expected = int(15 * 16000 / 512)
    print(f"[OK] {chunks} chunks captured in 15 s "
          f"(expected ~{expected} at 32ms each).")
    return 0


def _build_engine(args, whisper_lang: str):
    if args.engine == "tiered":
        from app.stt.whisper_tiered import TieredWhisperEngine
        return TieredWhisperEngine(
            language=whisper_lang,
            device=args.device,
            cloud_fallback=not args.no_cloud_fallback,
        )

    if args.engine == "groq":
        if not os.getenv("GROQ_API_KEY"):
            raise RuntimeError(
                "--engine groq requires GROQ_API_KEY in env. "
                "Set it in backend/.env or your shell profile.")
        from app.stt.whisper_groq import GroqWhisperEngine
        print(f"[*] Using Groq cloud Whisper ({args.groq_model}) "
              f"-- no model download.")
        return GroqWhisperEngine(model=args.groq_model, language=whisper_lang)

    # args.engine == "local"
    from app.stt.whisper_engine import WhisperEngine
    print(f"[*] Loading Whisper {args.model} on {args.device}...")
    if args.language == "yo" and not args.model.startswith("large"):
        print(f"[!] Whisper '{args.model}' has weak Yoruba support.")
        print("    Prefer:  --engine tiered  (or --engine groq)")
    return WhisperEngine(
        model_size=args.model,
        device=args.device,
        language=whisper_lang,
    )


def _print_detection(d) -> None:
    """Format and print a Detection. Tolerates older Detection shapes."""
    ts = time.strftime("%H:%M:%S", time.localtime(d.timestamp))
    if d.reference:
        ref = d.reference
        ref_str = f"{ref['book']} {ref['chapter']}:{ref['verse_start']}"
        if ref.get("verse_end"):
            ref_str += f"-{ref['verse_end']}"
        conf = ref.get("confidence", 0.0)
        src = ref.get("source", "?")
        print(f"[{ts}] DETECTED  {ref_str}  (conf={conf:.2f}, src={src})",
              flush=True)
        print(f"          transcript: {d.transcript!r}", flush=True)
        for v in d.verses:
            print(f"          [{v['verse']}] {v['text']}", flush=True)
    else:
        lp = getattr(d, "language_probability", 0.0)
        ms = getattr(d, "transcribe_ms", 0)
        print(f"[{ts}] heard     {d.transcript!r} "
              f"(lang={d.language} p={lp:.2f}, transcribed in {ms}ms)",
              flush=True)


# --------------------------------------------------------------
# Main
# --------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description="VerseSync mic listener.")
    p.add_argument("--engine", default="tiered",
                   choices=["tiered", "local", "groq"],
                   help="STT backend: 'tiered' (default), 'local', 'groq'.")
    p.add_argument("--model", default="medium",
                   help="Whisper model for --engine local.")
    p.add_argument("--device", default="cuda",
                   choices=["cuda", "cpu", "auto"],
                   help="Compute device for local engines.")
    p.add_argument("--groq-model", default="whisper-large-v3-turbo",
                   choices=["whisper-large-v3", "whisper-large-v3-turbo"])
    p.add_argument("--no-cloud-fallback", action="store_true",
                   help="Disable cloud tier in --engine tiered mode.")
    p.add_argument("--language", default="en",
                   help="Language: en, yo, or auto")
    p.add_argument("--translation", default="KJV",
                   help="Translation for verse fetch (KJV / WEB / YOR)")
    p.add_argument("--mic", default=None,
                   type=lambda x: int(x) if x.isdigit() else x,
                   help="Audio device id or name substring.")
    p.add_argument("--vad-threshold", default=0.3, type=float,
                   help="VAD speech threshold 0..1 (default 0.3).")
    p.add_argument("--list-devices", action="store_true")
    p.add_argument("--test-mic", action="store_true",
                   help="Show live audio level only -- no VAD or whisper.")
    p.add_argument("--debug", action="store_true",
                   help="Verbose pipeline logging.")
    args = p.parse_args()

    _setup_logging(args.debug)

    if args.list_devices:
        try:
            from app.stt.audio import MicrophoneStream
        except ImportError as exc:
            print(f"[ERR] sounddevice not installed: {exc}", file=sys.stderr)
            return 1
        for dev in MicrophoneStream.list_devices():
            print(f"  [{dev['id']:2d}] {dev['name']!r:40s}  "
                  f"channels={dev['channels']}  sr={dev['default_samplerate']}")
        return 0

    try:
        from app.stt.audio import MicrophoneStream
    except ImportError as exc:
        print(f"[ERR] STT deps missing: {exc}\n"
              f"      pip install -r backend/requirements-stt.txt",
              file=sys.stderr)
        return 1
    mic_id, mic_name = _resolve_mic(args.mic, MicrophoneStream.list_devices)

    if args.test_mic:
        return _run_test_mic(mic_id, mic_name)

    if args.language == "auto":
        whisper_lang = "auto"
    elif args.language in ("en", "yo"):
        whisper_lang = args.language
    else:
        print(f"[ERR] Unsupported --language: {args.language}", file=sys.stderr)
        return 2

    try:
        from app.stt.pipeline import STTPipeline
    except ImportError as exc:
        print(f"[ERR] STT deps not installed: {exc}\n"
              f"      pip install -r backend/requirements-stt.txt",
              file=sys.stderr)
        return 1

    # ---------- Load engine ----------
    try:
        whisper = _build_engine(args, whisper_lang)
    except KeyboardInterrupt:
        print("\n[*] Cancelled during engine load.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"[ERR] Could not initialise STT engine: {exc}", file=sys.stderr)
        return 3

    # ---------- Construct pipeline (no mic kwarg here) ----------
    print(f"[*] Mic: id={mic_id} ({mic_name!r})")
    print(f"[*] Starting capture (lang={whisper.language}, "
          f"translation={args.translation}, "
          f"vad_threshold={args.vad_threshold})...")

    # Build kwargs the pipeline constructor actually accepts. Older
    # versions don't have vad_threshold or debug -- we drop them silently
    # if absent rather than crash.
    import inspect
    init_params = inspect.signature(STTPipeline).parameters

    construct_kwargs = {
        "whisper": whisper,
        "on_detection": _print_detection,
        "translation": args.translation,
    }
    if "vad_threshold" in init_params:
        construct_kwargs["vad_threshold"] = args.vad_threshold
    if "debug" in init_params:
        construct_kwargs["debug"] = args.debug

    pipeline = STTPipeline(**construct_kwargs)

    # ---------- Start with the device passed to start(), not __init__ ----------
    start_params = inspect.signature(pipeline.start).parameters
    start_kwargs = {}
    # The canonical signature is start(self, device=None). We support a
    # couple of alternate spellings just in case.
    for cand in ("device", "mic_device", "input_device", "mic"):
        if cand in start_params:
            start_kwargs[cand] = mic_id
            break
    else:
        if mic_id is not None:
            print("[!] STTPipeline.start accepts no device kwarg; "
                  "using system default mic.", file=sys.stderr)

    try:
        pipeline.start(**start_kwargs)
        if args.debug:
            print("[OK] Listening (debug mode -- VAD events + 5s heartbeat).")
        else:
            print("[OK] Listening. Speak Bible references; Ctrl+C to stop.")
        print()
        while True:
            time.sleep(0.5)
    except TypeError as exc:
        print(f"[ERR] Pipeline start failed: {exc}\n"
              f"      The pipeline.start() signature has changed -- check "
              f"backend/app/stt/pipeline.py.",
              file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print("\n[*] Stopping pipeline...")
    finally:
        try:
            pipeline.stop()
            print("[OK] Stopped.")
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
