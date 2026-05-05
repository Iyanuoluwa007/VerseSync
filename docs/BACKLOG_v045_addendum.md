## Distribution-bundle task (post-Module 5)

When packaging VerseSync for redistribution to others (church members,
collaborators, etc.), the cached Whisper models from
`~/.cache/huggingface/hub/` must travel with the project so the
recipient doesn't have to download 4.6 GB on first run.

Plan for `scripts/package_for_redistribution.py`:

1. Detect the HF cache for both required models:
   - `models--Systran--faster-whisper-large-v3/`
   - `models--mobiuslabsgmbh--faster-whisper-large-v3-turbo/`
2. Copy each into a project-local `models/` directory (preserving
   the HF cache layout so faster-whisper recognises it).
3. Set the `HF_HOME` environment variable (or pass `download_root=`
   to `WhisperModel`) so the engine looks at `models/` first instead
   of the user-level cache.
4. Bundle: project tree + `models/` + Bible data + `.env.example`
   (no real keys) into a single redistributable zip / installer.
5. Update README with a short "First run" section explaining that
   models are bundled and no download is needed.

Approximate final pack size: VerseSync source (~5 MB) + Bibles
(~30 MB) + cached models (~4.6 GB) = **~4.7 GB**. Acceptable for
USB-drive or large-file-share redistribution; too big for GitHub
release artefacts (100 MB limit), so this stays out of the public
repo.

This task is queued for after Module 5 (auth foundation) closes.
