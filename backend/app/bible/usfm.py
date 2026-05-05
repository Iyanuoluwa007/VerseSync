"""USFM parser for VerseSync.

USFM (Unified Standard Format Markers) is the SIL standard used by
eBible.org and most Bible distributions. We don't need a full parser --
just enough to extract clean (book, chapter, verse, text) tuples from
files like:

    \\id JHN The Gospel According to John
    \\h John
    \\c 1
    \\p
    \\v 1 \\w In|strong="G1722"\\w* the \\w beginning|strong="G0746"\\w*...
    \\v 2 ...

We strip every backslash marker, drop Strong's annotations, and emit
plain UTF-8 verse text. Yoruba diacritics and Greek/Hebrew Unicode pass
through untouched -- we never lowercase or normalise the text content.

Reference: https://ubsicap.github.io/usfm/
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class Verse:
    book: str       # USFM 3-letter code from \id
    chapter: int    # from \c
    verse: int      # from \v (just the start verse if a range)
    text: str       # cleaned verse text


# --- Regex patterns for USFM cleanup ---

# \w word|strong="G1234"\w*  ->  word
# \w word|lemma="..."  \w*   ->  word
_RX_WORD_ATTR = re.compile(r"\\\+?w\s+([^|\\]+?)\|[^\\]*?\\\+?w\*")

# \w word\w*  ->  word  (no attributes)
_RX_WORD_PLAIN = re.compile(r"\\\+?w\s+([^\\]+?)\\\+?w\*")

# \add ...\add*  ->  ...   (translator's added words)
_RX_ADD = re.compile(r"\\add\s+(.*?)\\add\*", re.DOTALL)

# \nd ...\nd*  ->  ...   (name of deity, e.g. LORD)
_RX_ND = re.compile(r"\\nd\s+(.*?)\\nd\*", re.DOTALL)

# \wj ...\wj*  ->  ...   (words of Jesus -- keep text, lose styling)
_RX_WJ = re.compile(r"\\wj\s*(.*?)\\wj\*", re.DOTALL)

# Footnote / cross-ref blocks: \f ... \f*  and  \x ... \x*   ->  drop entirely
_RX_FOOTNOTE = re.compile(r"\\f\s.*?\\f\*", re.DOTALL)
_RX_XREF = re.compile(r"\\x\s.*?\\x\*", re.DOTALL)

# Any remaining backslash marker like \p, \q, \m, \nb, \pi1, etc -> drop
_RX_GENERIC_MARKER = re.compile(r"\\[+a-z]+\d*\*?")

# Pilcrow paragraph mark frequently appears as a literal character
_RX_PILCROW = re.compile(r"¶\s*")

# Collapse whitespace
_RX_WS = re.compile(r"\s+")


def _clean_verse_text(raw: str) -> str:
    """Apply the markup-stripping passes to a single verse body."""
    s = raw

    # Footnotes and cross-refs first (they can contain other markers).
    s = _RX_FOOTNOTE.sub("", s)
    s = _RX_XREF.sub("", s)

    # Word-level annotations (handle attributed form before plain form so
    # we don't accidentally strip the attributes off as "text").
    # Apply repeatedly to handle nested cases.
    for _ in range(3):
        before = s
        s = _RX_WORD_ATTR.sub(r"\1", s)
        s = _RX_WORD_PLAIN.sub(r"\1", s)
        if s == before:
            break

    # Inline character-level markers we keep the text from.
    s = _RX_ADD.sub(r"\1", s)
    s = _RX_ND.sub(r"\1", s)
    s = _RX_WJ.sub(r"\1", s)

    # Anything else with a backslash is structural styling; drop it.
    s = _RX_GENERIC_MARKER.sub("", s)

    # Decorative pilcrows
    s = _RX_PILCROW.sub("", s)

    # Collapse whitespace and trim
    s = _RX_WS.sub(" ", s).strip()
    return s


def parse_usfm(text: str) -> Iterator[Verse]:
    """Yield Verse objects from a USFM document string.

    Caller is responsible for filtering to canonical books -- this parser
    will happily emit verses from front matter or apocrypha if the file
    has them.
    """
    # First pull out the book code. \id <CODE> [optional rest]
    m_id = re.search(r"\\id\s+([A-Z0-9]{3})", text)
    if not m_id:
        return  # not a Bible book file
    book = m_id.group(1)

    # Walk through the file, tracking current chapter, accumulating
    # verse text until the next \v or \c marker.
    current_chapter: int | None = None
    pending_verse_num: int | None = None
    pending_buf: list[str] = []

    def flush() -> Verse | None:
        if pending_verse_num is None or current_chapter is None:
            return None
        cleaned = _clean_verse_text("".join(pending_buf))
        if not cleaned:
            return None
        return Verse(book=book, chapter=current_chapter,
                     verse=pending_verse_num, text=cleaned)

    # Tokenise on \c and \v boundaries (regex split keeps the markers).
    # We process line-by-line because USFM is line-oriented.
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        # Chapter marker: \c N
        m_c = re.match(r"\\c\s+(\d+)", stripped)
        if m_c:
            v = flush()
            if v:
                yield v
            pending_buf = []
            pending_verse_num = None
            current_chapter = int(m_c.group(1))
            continue

        # Verse marker: \v N text...
        m_v = re.match(r"\\v\s+(\d+)(?:[a-z])?\s*(.*)$", stripped)
        if m_v and current_chapter is not None:
            v = flush()
            if v:
                yield v
            pending_verse_num = int(m_v.group(1))
            pending_buf = [m_v.group(2)]
            continue

        # Continuation line of an in-progress verse
        if pending_verse_num is not None:
            pending_buf.append(" ")
            pending_buf.append(stripped)

    v = flush()
    if v:
        yield v


def parse_usfm_file(path: Path) -> Iterator[Verse]:
    """Convenience wrapper: open a file and parse it."""
    with open(path, encoding="utf-8-sig") as f:  # utf-8-sig handles BOM
        text = f.read()
    yield from parse_usfm(text)
