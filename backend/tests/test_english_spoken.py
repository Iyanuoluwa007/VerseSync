"""Tests for the English spoken-form scripture normalizer.

Covers the patterns that v0.4.5 dropped on the floor: spoken
chapter/verse without a colon, the "verse N" keyword, and ranges
expressed as "from verse N to M" / "verses N through M".
"""
import pytest

from app.parser.english_spoken import (
    looks_english_spoken,
    normalize_english_spoken,
)
from app.parser.parser import parse

# ============================================================
# Direct normaliser tests
# ============================================================

@pytest.mark.parametrize("inp,expected", [
    # Live-session transcripts
    ("Luke 5 5", "Luke 5:5"),
    ("Psalm 145 verse 5", "Psalm 145:5"),
    ("Revelation 2 2", "Revelation 2:2"),
    ("Psalms 150 from verse 1 to 10", "Psalms 150:1-10"),
    # Common spoken phrasings
    ("John 3 16", "John 3:16"),
    ("Matthew 5 verses 3 through 12", "Matthew 5:3-12"),
    ("Romans chapter 8 verse 28", "Romans 8:28"),
    ("Romans chapter 8 verses 28 to 30", "Romans 8:28-30"),
    # 'thru' colloquialism
    ("Matthew 5 verses 3 thru 12", "Matthew 5:3-12"),
    # Plurals + variants
    ("verses 1 to 10", "1-10"),
    ("verse 5", "5"),
    ("chapter 8", "8"),
    # Already canonical -- pass through
    ("John 3:16", "John 3:16"),
    ("Romans 8:28", "Romans 8:28"),
    ("Psalms 150:1-10", "Psalms 150:1-10"),
    # No book + chapter alone
    ("Psalm 23", "Psalm 23"),
    # Book numbers must not get colon-injected
    ("1 John 3 16", "1 John 3:16"),
    ("2 Peter 1 5", "2 Peter 1:5"),
    # Triple-digit chapter (Psalm 119)
    ("Psalms 119 105", "Psalms 119:105"),
    # Don't touch unrelated text
    ("hello world", "hello world"),
    ("Read this in 5 minutes", "Read this in 5 minutes"),
])
def test_normalizer_canonicalizes_spoken_forms(inp, expected):
    assert normalize_english_spoken(inp) == expected


def test_normalizer_no_op_on_canonical():
    """Canonical references must pass through unchanged."""
    samples = ["John 3:16", "Romans 8:28-30", "1 Corinthians 13:4-7",
               "Genesis 1:1", "Psalms 150:1-10"]
    for s in samples:
        assert normalize_english_spoken(s) == s


def test_looks_english_spoken_detection():
    """Heuristic should flag patterns the normaliser would touch."""
    assert looks_english_spoken("Luke 5 5")
    assert looks_english_spoken("Psalm 145 verse 5")
    assert looks_english_spoken("from verse 1 to 10")
    assert looks_english_spoken("chapter 8")
    # No spoken markers -> no flag (canonical or unrelated)
    assert not looks_english_spoken("John 3:16")
    assert not looks_english_spoken("hello world")


# ============================================================
# End-to-end parser integration -- the real win
# ============================================================

@pytest.mark.parametrize("inp,expected", [
    # The exact failures from the live session
    ("Luke 5 5", "LUK 5:5"),
    ("Psalm 145 verse 5", "PSA 145:5"),
    ("Revelation 2 2", "REV 2:2"),
    ("Psalms 150 from verse 1 to 10", "PSA 150:1-10"),
    # Other common spoken forms
    ("John 3 16", "JHN 3:16"),
    ("Romans chapter 8 verse 28", "ROM 8:28"),
    ("Matthew 5 verses 3 through 12", "MAT 5:3-12"),
    # English with "from verse N to M"
    ("Genesis 1 from verse 1 to 5", "GEN 1:1-5"),
    # Numbered books still work
    ("1 John 3 16", "1JN 3:16"),
    ("2 Peter 1 5", "2PE 1:5"),
])
def test_spoken_forms_parse_end_to_end(inp, expected):
    """The patch's reason for existing: these used to all return None."""
    ref = parse(inp, use_llm=False)
    assert ref is not None, f"Parser returned None for {inp!r}"
    assert str(ref) == expected, f"For {inp!r}: expected {expected}, got {ref}"


def test_canonical_forms_still_work():
    """Don't regress the existing canonical-form parsing."""
    cases = [
        ("John 3:16", "JHN 3:16"),
        ("Romans 8:28", "ROM 8:28"),
        ("Romans 8:28-30", "ROM 8:28-30"),
        ("1 Corinthians 13:4-7", "1CO 13:4-7"),
        ("Genesis 1:1", "GEN 1:1"),
        ("Psalms 23:1", "PSA 23:1"),
    ]
    for inp, expected in cases:
        ref = parse(inp, use_llm=False)
        assert ref is not None, f"None for {inp!r}"
        assert str(ref) == expected


def test_word_form_numbers_still_work():
    """Don't regress the words_to_digits + parser path."""
    ref = parse("first thessalonians five sixteen", use_llm=False)
    assert ref is not None
    assert str(ref) == "1TH 5:16"

    ref = parse("John three sixteen", use_llm=False)
    assert ref is not None
    assert str(ref) == "JHN 3:16"


def test_unrelated_text_stays_unparsed():
    """Don't false-positive on non-scripture text."""
    assert parse("hello world", use_llm=False) is None
    assert parse("Read this in 5 minutes", use_llm=False) is None
