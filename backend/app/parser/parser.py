"""Reference parser orchestrator.

Pipeline (first hit wins):

1. Strip filler phrases ("turn to", "open your bibles to", "the book of")
2. Try written-form regex on the raw text (catches "John 3:16")
3. Convert spoken numbers to digits, retry regex (catches "John three sixteen")
4. Try context resolution (catches "the next chapter", "verses 9-10")
5. LLM fallback to Groq (anything weird or multilingual)
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from app.bible.books import is_valid_chapter
from app.parser import llm as llm_module
from app.parser.lexicon import find_book_in_text
from app.parser.numbers import (
    ALL_NUMBER_WORDS, words_to_digits,
)
from app.parser.types import ParseContext, ParsedRef
from app.parser.yoruba import looks_yoruba, normalize_yoruba
from app.parser.english_spoken import normalize_english_spoken

logger = logging.getLogger(__name__)

# Word-boundary regex that fires if any English number word appears.
_NUM_WORD_RX = re.compile(
    r"\b(?:" + "|".join(sorted(ALL_NUMBER_WORDS, key=len, reverse=True)) + r")\b"
)


# --- Filler that preachers say before a reference ---
# Order matters: longest first to avoid leaving "to" residue.
_FILLER_PHRASES = [
    "open your bibles to",
    "turn with me to",
    "let us read from",
    "let's read from",
    "let us turn to",
    "let's turn to",
    "the book of",
    "in the book of",
    "we read in",
    "go to",
    "turn to",
    "read from",
    "open to",
    "from",
]
_FILLER_RX = re.compile(
    r"\b(?:" + "|".join(re.escape(p) for p in _FILLER_PHRASES) + r")\b",
    re.IGNORECASE,
)

# Chapter/verse number connectors we tolerate between digits.
# After words_to_digits(), we expect spaced digits separated by these.
_CHAPTER_VERSE_RX = re.compile(
    r"\b(\d{1,3})"                              # chapter
    r"\s*(?:[:.,]|"                             # ":", ".", ","
    r"chapter\s+\d+\s+verses?|"                 # "chapter 3 verse"
    r"verses?|"                                 # "verses 16"
    r"v\.?|"                                    # "v 16" or "v. 16"
    r"\s)\s*"                                   # whitespace
    r"(\d{1,3})"                                # verse start
    r"(?:\s*(?:[-\u2013\u2014]|through|thru|to)\s*(\d{1,3}))?",  # optional end
    re.IGNORECASE,
)

# Single-chapter books -- "Jude 24" means JUD 1:24
_SINGLE_CHAPTER_BOOKS = {"OBA", "PHM", "2JN", "3JN", "JUD"}


def _strip_filler(text: str) -> str:
    """Remove preacher filler phrases."""
    return _FILLER_RX.sub(" ", text).strip()


def _build_ref_if_valid(book: str, chapter: int, verse_start: int,
                        verse_end: Optional[int], source: str,
                        confidence: float) -> Optional[ParsedRef]:
    """Build a ParsedRef but return None if (book, chapter) is invalid.

    This guards against Whisper-induced nonsense like 'Matt255' parsing
    to MAT 255:1 -- Matthew has 28 chapters. By returning None here we
    let the parse() driver try the LLM fallback, which generally does
    better with malformed inputs.
    """
    if not is_valid_chapter(book, chapter):
        logger.debug("Rejecting out-of-range ref %s %d:%d (max chapter exceeded)",
                     book, chapter, verse_start)
        return None
    if verse_start < 1 or verse_start > 200:
        # 200 is generous but flags totally bogus values like '5 by 5' -> 5:55
        return None
    return ParsedRef(
        book=book,
        chapter=chapter,
        verse_start=verse_start,
        verse_end=verse_end,
        source=source,  # type: ignore[arg-type]
        confidence=confidence,
    )


def _try_regex(text: str, source_label: str) -> Optional[ParsedRef]:
    """Run the book + chapter:verse regex on `text`. Assumes numbers
    are in digit form already."""
    lower = text.lower()
    book_hit = find_book_in_text(lower)
    if not book_hit:
        return None
    book_match, _, end = book_hit
    after = lower[end:]

    # Single-chapter book special-case: "Jude 24" -> JUD 1:24
    if book_match.book_code in _SINGLE_CHAPTER_BOOKS:
        # Look for one number first; if there's a second one separated
        # by a colon, treat as chapter:verse anyway (some refs say "1:24").
        m_two = _CHAPTER_VERSE_RX.search(after)
        if m_two:
            ch, vs, ve = m_two.group(1), m_two.group(2), m_two.group(3)
            return _build_ref_if_valid(
                book=book_match.book_code,
                chapter=int(ch),
                verse_start=int(vs),
                verse_end=int(ve) if ve else None,
                source=source_label,
                confidence=book_match.confidence,
            )
        m_one = re.search(r"\b(\d{1,3})(?:\s*[-\u2013to\u2014]+\s*(\d{1,3}))?", after)
        if m_one:
            vs, ve = m_one.group(1), m_one.group(2)
            return _build_ref_if_valid(
                book=book_match.book_code,
                chapter=1,
                verse_start=int(vs),
                verse_end=int(ve) if ve else None,
                source=source_label,
                confidence=book_match.confidence,
            )
        return None

    m = _CHAPTER_VERSE_RX.search(after)
    if not m:
        # Maybe just "John 3" with no verse -- treat as chapter:1
        m_chap = re.search(r"\b(\d{1,3})\b", after)
        if m_chap:
            return _build_ref_if_valid(
                book=book_match.book_code,
                chapter=int(m_chap.group(1)),
                verse_start=1,
                verse_end=None,
                source=source_label,
                confidence=book_match.confidence * 0.7,  # less specific
            )
        return None

    ch, vs, ve = m.group(1), m.group(2), m.group(3)
    return _build_ref_if_valid(
        book=book_match.book_code,
        chapter=int(ch),
        verse_start=int(vs),
        verse_end=int(ve) if ve else None,
        source=source_label,
        confidence=book_match.confidence,
    )


def _try_context(text: str, ctx: ParseContext) -> Optional[ParsedRef]:
    """Resolve stateful references against the last cited verse."""
    if ctx.last_book is None or ctx.last_chapter is None:
        return None

    lower = text.lower().strip()
    # Normalize Yoruba phrasing too -- "ese kerin si keje" needs the
    # context resolver to see digit form.
    if looks_yoruba(lower):
        lower = normalize_yoruba(lower)
    digits = words_to_digits(lower)

    # "next chapter" / "the next chapter"
    if re.search(r"\b(the\s+)?next\s+chapter\b", lower):
        return ParsedRef(
            book=ctx.last_book,
            chapter=ctx.last_chapter + 1,
            verse_start=1,
            verse_end=None,
            source="context",
            confidence=0.8,
        )

    # "previous chapter"
    if re.search(r"\b(the\s+)?previous\s+chapter\b", lower) and ctx.last_chapter > 1:
        return ParsedRef(
            book=ctx.last_book,
            chapter=ctx.last_chapter - 1,
            verse_start=1,
            verse_end=None,
            source="context",
            confidence=0.8,
        )

    # "verse N" or "verses N to M" / "verses N and M" with no book.
    # Also matches the digit form of "ese N si M" produced by the
    # Yoruba normaliser. Only fires if no book pattern is present.
    if not find_book_in_text(lower):
        m = re.search(
            r"\bverses?\s+(\d{1,3})(?:\s*[-\u2013to\u2014and]+\s*(\d{1,3}))?",
            digits,
        )
        if not m:
            # Yoruba path: after normalize, "ese N" became bare digits;
            # try to grab "<digit> [- <digit>]" near the start.
            m = re.search(r"(?:^|\s)(\d{1,3})(?:\s*[-\u2013]\s*(\d{1,3}))?\s*$",
                          digits)
        if m:
            vs, ve = m.group(1), m.group(2)
            return ParsedRef(
                book=ctx.last_book,
                chapter=ctx.last_chapter,
                verse_start=int(vs),
                verse_end=int(ve) if ve else None,
                source="context",
                confidence=0.85,
            )

    return None


def parse(
    text: str,
    context: Optional[ParseContext] = None,
    *,
    use_llm: bool = True,
) -> Optional[ParsedRef]:
    """Parse a single Bible reference from `text`. Returns None if nothing found.

    If `context` is provided and `text` resolves to a stateful reference
    (e.g. "the next chapter"), the result will use `source="context"`.
    """
    if not text or not text.strip():
        return None

    cleaned = _strip_filler(text)

    # Yoruba scripture phrasing pre-pass: "Johanu kini Ori keta Ese kerin"
    # -> "1 johanu 3 4". Only runs when Yoruba markers are detected so
    # English-only input pays nothing.
    yoruba_used = False
    if looks_yoruba(cleaned):
        normalised = normalize_yoruba(cleaned)
        if normalised != cleaned:
            cleaned = normalised
            yoruba_used = True

    # Number normalisation first. If the text already contains only
    # digit numbers, this is essentially a no-op for matching purposes;
    # otherwise it produces a digit form ready for the regex. We label
    # the source by whether the *original* input contained any number
    # words (cheap check, doesn't false-positive on punctuation re-spacing).
    digits = words_to_digits(cleaned)
    digits = normalize_english_spoken(digits)
    source_label = (
        "regex_spoken"
        if (yoruba_used or _NUM_WORD_RX.search(cleaned.lower()))
        else "regex_written"
    )

    ref = _try_regex(digits, source_label=source_label)
    if ref:
        return ref

    # Fallback: try the un-normalised text (rare, but defensive).
    if digits != cleaned:
        ref = _try_regex(cleaned, source_label="regex_written")
        if ref:
            return ref

    # Context pass (only if we have prior state)
    if context is not None:
        ref = _try_context(text, context)
        if ref:
            return ref

    # LLM fallback
    if use_llm and llm_module.is_available():
        ref = llm_module.llm_parse(text, context)
        if ref:
            return ref

    return None
