"""English spoken-form scripture phrasing normalizer.

Pastors typically cite scripture with patterns the written-form regex
parser doesn't directly recognize:

    "Luke 5 5"                          (no colon between chapter/verse)
    "Psalm 145 verse 5"                 (verse keyword)
    "Revelation 2 2"                    (no colon)
    "Psalms 150 from verse 1 to 10"     (range with from/to)
    "Romans chapter 8 verse 28"         (chapter+verse keywords)
    "Matthew 5 verses 3 through 12"     (verses + through)

This module rewrites those into the canonical "Book N:M" or
"Book N:M-K" form the existing regex already handles. It runs AFTER
the Yoruba normaliser and AFTER `words_to_digits` so both written
digits and spoken word numbers feed through one consistent pipeline.

Idempotent on canonical input: "John 3:16" passes through unchanged.
"""
from __future__ import annotations

import re


# "from verse N to/through M" -> "N-M"
_FROM_VERSE_RANGE_RX = re.compile(
    r"\bfrom\s+verses?\s+(\d{1,3})\s+(?:to|through|until|thru)\s+(\d{1,3})\b",
    re.IGNORECASE,
)

# "verses N to M" / "verses N through M" / "verses N - M"
# (not preceded by "from", which step 1 already handled)
_VERSE_RANGE_RX = re.compile(
    r"\bverses?\s+(\d{1,3})\s*(?:to|through|until|thru|-|\u2013|\u2014)\s*(\d{1,3})\b",
    re.IGNORECASE,
)

# "verse N" / "verses N"
_VERSE_SINGLE_RX = re.compile(
    r"\bverses?\s+(\d{1,3})\b",
    re.IGNORECASE,
)

# "chapter N" / "chapters N"
_CHAPTER_SINGLE_RX = re.compile(
    r"\bchapters?\s+(\d{1,3})\b",
    re.IGNORECASE,
)

# "<word> N M" -> "<word> N:M"  (insert colon between two consecutive
# numbers when preceded by a letter token -- the book name).
# Negative lookahead blocks the rewrite if M is followed by ":" or "."
# (already structured) or by a digit (continued multi-digit number).
# A trailing "-K" IS allowed: it indicates a verse range, so we want
# "Psalms 150 1-10" -> "Psalms 150:1-10".
# Word must be 2+ letters to avoid mangling book numbers like "1 John 3 16".
_SPACED_CV_RX = re.compile(
    r"\b([A-Za-z][A-Za-z]+)\s+(\d{1,3})\s+(\d{1,3})(?![\d:.])",
)


def normalize_english_spoken(text: str) -> str:
    """Rewrite spoken English scripture forms into canonical regex-friendly form.

    Idempotent on already-canonical input. Safe on non-scripture text --
    the rules only fire on specific keyword patterns that don't occur
    in normal English at random.
    """
    out = text

    # Step 1: explicit "from verse N to M"
    out = _FROM_VERSE_RANGE_RX.sub(r"\1-\2", out)

    # Step 2: bare "verses N to M"
    out = _VERSE_RANGE_RX.sub(r"\1-\2", out)

    # Step 3: bare "verse N" / "verses N" -> "N"
    out = _VERSE_SINGLE_RX.sub(r"\1", out)

    # Step 4: bare "chapter N" / "chapters N" -> "N"
    out = _CHAPTER_SINGLE_RX.sub(r"\1", out)

    # Step 5: insert colon between two adjacent numbers preceded by a
    # book-name-like word.  "Luke 5 5" -> "Luke 5:5".
    out = _SPACED_CV_RX.sub(r"\1 \2:\3", out)

    # Collapse residual whitespace.
    out = re.sub(r"\s+", " ", out).strip()
    return out


def looks_english_spoken(text: str) -> bool:
    """Cheap heuristic: do any spoken-form markers appear?

    False positives are OK -- normalize_english_spoken is a no-op on
    strings without the patterns it cares about. The check exists only
    to skip the regex passes on totally unrelated text.
    """
    lower = text.lower()
    if "verse" in lower or "chapter" in lower:
        return True
    # Two adjacent <=3-digit numbers separated by space and preceded by
    # a word -- the spoken "Book N M" form.
    if re.search(r"\b[a-z]{2,}\s+\d{1,3}\s+\d{1,3}\b", lower):
        return True
    return False
