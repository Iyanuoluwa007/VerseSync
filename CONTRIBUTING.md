# Contributing to VerseSync

Thanks for considering a contribution. VerseSync is built for people
running live services on real Sunday mornings, so the bar for changes is
"would I trust this mid-sermon?"

## Ground rules

1. **Never break the service to add a feature.** Anything optional --
   OBS WebSocket control, the LLM fallback, cloud STT -- must degrade to
   a log line, not an exception. The verse on the screen is the product.
2. **No new required dependency without a reason.** The projector-only
   profile installs in seconds and runs on any laptop. Keep it that way.
3. **No Bible text in the repository.** Translations are downloaded from
   eBible.org at install time. The Yoruba text is CC BY-SA 4.0 with a
   trademark condition; it travels from its source, not from here.

## Getting set up

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
```

Most work needs no Bible data. If you are touching the Bible engine or
the projector, populate it once:

```bash
python scripts/download_bibles.py && python scripts/ingest_bibles.py
```

## Before you open a pull request

```bash
ruff check .
```

```bash
cd backend && pytest
```

Both must pass. CI runs them on Windows and Linux across Python
3.11-3.13, plus a check that the app still imports without the optional
STT extras installed.

## Testing expectations

- **Every bug fix gets a regression test.** `backend/tests/test_regressions.py`
  documents each defect that shipped; add to it.
- **Test behaviour, not implementation.** The parser tests read as a list
  of things a preacher might say. Keep that style.
- **No network in tests.** The Groq and OBS tests use fakes. A test that
  needs a live OBS instance does not belong in the suite.
- **The suite must pass with no Bible database present.** Build a
  temporary one in a fixture, as `tests/test_projector.py` does.

## Testing what CI cannot

CI has no microphone, no GPU and no OBS. If you have any of those,
[docs/TESTING.md](docs/TESTING.md) lists exactly what still needs a human
and what a correct result looks like. Reporting a failure from that list
is one of the most useful contributions available right now.

## Things that are especially welcome

- **Yoruba accuracy.** Whisper mishears Yoruba word boundaries
  (`"Johan nukini"` for `"Johanu kini"`). Better normalisation, a wider
  ordinal table, or `initial_prompt` biasing would all help.
- **More open-licensed translations.** See `docs/BACKLOG.md` for the
  candidate list and the ingest checklist.
- **OBS workflows we have not covered.** If your setup needed a
  workaround, that is a documentation bug worth reporting.

## Style

- Follow the surrounding code. Comments explain *why*, not *what*.
- No em-dashes in prose or comments.
- Keep docstrings practical: what breaks if this is wrong.

## Reporting bugs

Use the issue templates. For anything involving live capture, include
your OS, whether OBS is on the same machine, the output of
`GET /stt/status`, and the relevant log lines.

Security issues go to [SECURITY.md](SECURITY.md), not the issue tracker.
