"""Groq LLM fallback for ambiguous parses.

Only invoked when the regex/spoken parsers return None. We keep the
prompt tight (JSON-only output, 100 max tokens) and protect ourselves
with a circuit breaker so a Groq outage doesn't stall every parse call.

The Groq client is imported lazily so the rest of the parser works in
LLM-disabled mode (no GROQ_API_KEY set) without paying the import cost.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time

from app.parser.types import ParseContext, ParsedRef

logger = logging.getLogger(__name__)

# The model is read from the environment, not hard-coded. The previous
# default, llama-3.3-70b-versatile, was decommissioned by Groq and every
# fallback call started returning 404 -- silently, because the circuit
# breaker did its job and the parser simply lost a tier. Pinning a
# third-party model name in source with no override was the root cause.
DEFAULT_GROQ_MODEL = "openai/gpt-oss-20b"
GROQ_MODEL = os.getenv("GROQ_PARSER_MODEL", "").strip() or DEFAULT_GROQ_MODEL

# Measured on Groq for the default model: median 0.28 s, worst 0.43 s over
# the parser's real fallback inputs. 3 s leaves room for a slow network
# without stalling the pipeline; the LLM only runs when the regex and
# Yoruba passes have both already failed.
TIMEOUT_S = float(os.getenv("GROQ_PARSER_TIMEOUT", "3.0"))

# Reasoning models spend tokens thinking before they answer. At 100 the
# budget was consumed by reasoning and the response came back empty,
# which Groq rejects as `json_validate_failed` with an empty generation.
MAX_TOKENS = 1024

# Keep reasoning short: this is a extraction task, not a puzzle. Sent via
# extra_body because older groq SDK versions do not accept it as a named
# argument, and an unknown named argument is a TypeError, not a warning.
REASONING_EFFORT = "low"

# Circuit breaker: 3 failures within the window trip it for the cooldown.
_BREAKER_THRESHOLD = 3
_BREAKER_COOLDOWN_S = 60.0


class _CircuitBreaker:
    """Thread-safe trip-on-failure breaker."""

    def __init__(self, threshold: int, cooldown_s: float):
        self.threshold = threshold
        self.cooldown_s = cooldown_s
        self._failures = 0
        self._tripped_until = 0.0
        self._lock = threading.Lock()

    def can_call(self) -> bool:
        with self._lock:
            if self._failures < self.threshold:
                return True
            return time.time() >= self._tripped_until

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._tripped_until = 0.0

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self.threshold:
                self._tripped_until = time.time() + self.cooldown_s
                logger.warning(
                    "Groq circuit breaker tripped for %.0fs after %d failures",
                    self.cooldown_s, self._failures,
                )


_breaker = _CircuitBreaker(_BREAKER_THRESHOLD, _BREAKER_COOLDOWN_S)


_SYSTEM_PROMPT = """You extract Bible references from preacher speech.

Output ONE LINE of JSON, exactly this shape, nothing else:
{"book":"JHN","chapter":3,"verse_start":16,"verse_end":null}

Rules:
- "book" MUST be a USFM 3-letter code: GEN EXO LEV NUM DEU JOS JDG RUT 1SA 2SA 1KI 2KI 1CH 2CH EZR NEH EST JOB PSA PRO ECC SNG ISA JER LAM EZK DAN HOS JOL AMO OBA JON MIC NAM HAB ZEP HAG ZEC MAL MAT MRK LUK JHN ACT ROM 1CO 2CO GAL EPH PHP COL 1TH 2TH 1TI 2TI TIT PHM HEB JAS 1PE 2PE 1JN 2JN 3JN JUD REV
- "verse_end" is null for single verses, integer for ranges.
- Use the optional last reference for context-dependent inputs like "the next chapter" or "verses 9-10".
- If no reference can be extracted, return exactly: {"book":null}
- Yoruba, Pidgin, and other languages are acceptable inputs."""


def is_available() -> bool:
    """True iff the Groq key is configured AND the breaker is closed."""
    if not os.getenv("GROQ_API_KEY"):
        return False
    return _breaker.can_call()


def llm_parse(text: str, context: ParseContext | None = None) -> ParsedRef | None:
    """Call Groq Llama 3.3 to parse a reference. Returns None on failure.

    Errors are swallowed -- the caller treats this as a soft fallback
    and falls back to "not found" if the LLM itself can't help.
    """
    if not is_available():
        return None

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return None

    try:
        # Lazy import so users without `groq` installed in regex-only mode
        # don't hit ImportError at module import time.
        from groq import Groq
    except ImportError:
        logger.warning("groq package not installed; LLM fallback disabled")
        return None

    user_msg = text.strip()
    if context and context.last_book:
        ctx_json = json.dumps({
            "last_book": context.last_book,
            "last_chapter": context.last_chapter,
            "last_verse_end": context.last_verse_end,
        })
        user_msg = f"Last reference: {ctx_json}\n\nInput: {text}"

    try:
        client = Groq(api_key=api_key, timeout=TIMEOUT_S)
        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=MAX_TOKENS,
            extra_body={"reasoning_effort": REASONING_EFFORT},
        )
        raw = resp.choices[0].message.content or ""
    except Exception as exc:
        # A retired or misspelled model is a configuration problem, not a
        # transient one, so say what to change instead of burying it in a
        # generic warning the operator will never act on.
        if "model_not_found" in str(exc) or "does not exist" in str(exc):
            logger.error(
                "Groq model %r is unavailable. Set GROQ_PARSER_MODEL to a "
                "model your key can use (see https://console.groq.com/docs/models). "
                "The parser still works without the LLM fallback.",
                GROQ_MODEL,
            )
        else:
            logger.warning("Groq call failed: %s", exc)
        _breaker.record_failure()
        return None

    _breaker.record_success()

    parsed = _coerce_response(raw)
    if parsed is None:
        return None
    return ParsedRef(
        book=parsed["book"],
        chapter=parsed["chapter"],
        verse_start=parsed["verse_start"],
        verse_end=parsed.get("verse_end"),
        source="llm",
        confidence=0.85,
    )


def _coerce_response(raw: str) -> dict | None:
    """Validate and normalise the JSON the model returned."""
    raw = raw.strip()
    if not raw or raw == "null":
        return None
    # Handle the case where the model wraps in {"reference": ...}
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Groq returned non-JSON: %r", raw[:200])
        return None
    if obj is None:
        return None
    # Some prompts produce a wrapper -- unwrap if needed.
    if "book" not in obj and isinstance(obj.get("reference"), dict):
        obj = obj["reference"]
    if "book" not in obj:
        return None

    # Basic shape validation
    book = obj.get("book")
    if not (isinstance(book, str) and len(book) == 3 and book.isupper()):
        return None
    try:
        chapter = int(obj["chapter"])
        verse_start = int(obj["verse_start"])
    except (KeyError, TypeError, ValueError):
        return None
    verse_end = obj.get("verse_end")
    if verse_end is not None:
        try:
            verse_end = int(verse_end)
        except (TypeError, ValueError):
            verse_end = None

    # Validate book code is in the known canon
    from app.bible.books import BY_CODE
    if book not in BY_CODE:
        logger.warning("Groq returned unknown book code: %s", book)
        return None

    return {
        "book": book,
        "chapter": chapter,
        "verse_start": verse_start,
        "verse_end": verse_end,
    }
