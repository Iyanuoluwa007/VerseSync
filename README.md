# VerseSync

Voice-activated scripture projection for live preaching. When the preacher
speaks a reference, the verse appears on the projector immediately.

## Status

Phase 0 / Module 4 complete. Local Windows development at `E:\VerseSync\`.

## Stack

- Backend: FastAPI + SQLite (Python 3.11+)
- STT: faster-whisper (CUDA) + Silero VAD + sounddevice
- LLM: Groq Llama 3.3 70B for parser fallback
- Frontend: Next.js 14 (deferred to Phase 1)

## Translations bundled in free tier

| Code | Name                                             | Language | License        | Verses |
| ---- | ------------------------------------------------ | -------- | -------------- | ------ |
| KJV  | King James Version (1611)                        | English  | Public Domain  | 31,102 |
| WEB  | World English Bible                              | English  | Public Domain  | 31,098 |
| YOR  | Bíbélì Mímọ́ ní Èdè Yorùbá Òde-Òní (Biblica)      | Yoruba   | CC BY-SA 4.0   | 31,087 |

Sourced from eBible.org. See `LICENSES.md` for full notices, `docs/BACKLOG.md`
for the roadmap of additional English / Yoruba translations.

## Phase 0 modules

1. [x] Repo skeleton + FastAPI health check
2. [x] Bible engine (SQLite ingest + verse lookup + REST API)
3. [x] Scripture reference parser (regex + Yoruba lexicon + Groq LLM fallback)
4. [x] STT pipeline (faster-whisper + Silero VAD + per-session language)
5. [ ] Auth foundation (admin PIN + device JWT + audit log)

## Quick start (Windows, PowerShell)

First-time setup (run once):

```powershell
cd E:\VerseSync
.\setup.ps1
```

Download and ingest the bundled Bibles (run once, ~2 min):

```powershell
cd backend
.\venv\Scripts\Activate.ps1
cd ..
python scripts\download_bibles.py
python scripts\ingest_bibles.py
```

Start the server:

```powershell
cd backend
.\venv\Scripts\Activate.ps1
uvicorn app.main:app --reload --port 8000
```

Verify in a browser:

- http://localhost:8000 — health JSON
- http://localhost:8000/translations — installed translations
- http://localhost:8000/verse/JHN/3/16?translation=KJV
- http://localhost:8000/verse/JHN/3/16?translation=YOR
- http://localhost:8000/passage/ROM/8/28-30?translation=KJV
- http://localhost:8000/docs — interactive API docs

## CLI verse lookup

```powershell
python scripts\query_verse.py --list
python scripts\query_verse.py JHN 3:16 --translation KJV
python scripts\query_verse.py JHN 3:16 --translation YOR
python scripts\query_verse.py ROM 8:28-30 --translation KJV
```

## Run tests

```powershell
cd backend
.\venv\Scripts\Activate.ps1
pytest -v
```
