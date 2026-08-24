"""Comprehensive parser fixture tests.

Covers the four pipeline stages:
1. Written-form regex (digit input)
2. Spoken-form regex (word numbers)
3. Context resolution ("the next chapter")
4. Should-not-match cases

LLM fallback is tested separately with a mock to avoid external calls.

Each fixture is (input_text, expected_str) where expected_str is the
__str__ form of the ParsedRef ("BOOK CH:V" or "BOOK CH:V-V") -- or None
for inputs that should not match.
"""
import pytest

from app.parser.parser import parse
from app.parser.types import ParseContext


def _expect(inp: str, expected: str | None):
    """Run parse() with LLM disabled and assert string form."""
    ref = parse(inp, use_llm=False)
    if expected is None:
        assert ref is None, f"expected no match for {inp!r}, got {ref}"
    else:
        assert ref is not None, f"expected {expected} for {inp!r}, got None"
        assert str(ref) == expected, (
            f"for {inp!r}: expected {expected}, got {ref!s}"
        )


# ============================================================
# Written form -- the bread and butter
# ============================================================

@pytest.mark.parametrize("inp,expected", [
    # Standard
    ("John 3:16", "JHN 3:16"),
    ("John 3:16-18", "JHN 3:16-18"),
    ("Romans 8:28", "ROM 8:28"),
    ("Romans 8:28-30", "ROM 8:28-30"),
    ("Genesis 1:1", "GEN 1:1"),
    ("Revelation 22:13", "REV 22:13"),
    ("Psalm 119:105", "PSA 119:105"),
    # Abbreviations
    ("Ps 23:1", "PSA 23:1"),
    ("Rom 8:28", "ROM 8:28"),
    ("Heb 11:1", "HEB 11:1"),
    ("Mt 5:3", "MAT 5:3"),
    ("Jn 14:6", "JHN 14:6"),
    # With period in abbrev
    ("Rom. 8:28", "ROM 8:28"),
    ("2 Cor. 5:17", "2CO 5:17"),
    # Ordinal-prefixed
    ("1 John 4:8", "1JN 4:8"),
    ("2 John 1", "2JN 1:1"),  # single-chapter book
    ("3 John 1:14", "3JN 1:14"),
    ("1 Corinthians 13:4-7", "1CO 13:4-7"),
    ("2 Timothy 3:16", "2TI 3:16"),
    # No-space digit prefix
    ("1Cor 13:4", "1CO 13:4"),
    ("2Tim 3:16", "2TI 3:16"),
    # Roman numerals
    ("II Corinthians 5:17", "2CO 5:17"),
    ("III John 1:4", "3JN 1:4"),
    # Ranges with various dashes
    ("Psalm 23:1-6", "PSA 23:1-6"),
    ("John 3:16\u201318", "JHN 3:16-18"),  # en-dash
    ("John 3:16\u201418", "JHN 3:16-18"),  # em-dash
    # Single-chapter books
    ("Jude 24", "JUD 1:24"),
    ("Jude 24-25", "JUD 1:24-25"),
    ("Obadiah 17", "OBA 1:17"),
    ("Philemon 6", "PHM 1:6"),
    ("3 John 4", "3JN 1:4"),
    # Chapter-only (less specific but valid)
    ("Psalm 23", "PSA 23:1"),
    ("Genesis 1", "GEN 1:1"),
    # Alt names
    ("Song of Solomon 2:1", "SNG 2:1"),
    ("Song of Songs 2:1", "SNG 2:1"),
    ("Canticles 2:1", "SNG 2:1"),
    ("The Gospel of John 1:1", "JHN 1:1"),
    ("Revelations 22:13", "REV 22:13"),  # common typo
])
def test_written_form(inp, expected):
    _expect(inp, expected)


# ============================================================
# Spoken form -- words to digits
# ============================================================

@pytest.mark.parametrize("inp,expected", [
    ("John three sixteen", "JHN 3:16"),
    ("John chapter three verse sixteen", "JHN 3:16"),
    ("Romans eight twenty-eight", "ROM 8:28"),
    ("Romans eight twenty eight", "ROM 8:28"),
    ("Psalm twenty three verse one", "PSA 23:1"),
    ("Psalm one hundred and nineteen verse one oh five", "PSA 119:1"),
    ("first thessalonians five sixteen", "1TH 5:16"),
    ("first thessalonians five sixteen through eighteen", "1TH 5:16-18"),
    ("second corinthians twelve nine", "2CO 12:9"),
    ("first john four eight", "1JN 4:8"),
    ("third john verse four", "3JN 1:4"),
    # Filler
    ("open your bibles to John three sixteen", "JHN 3:16"),
    ("let's turn to romans eight twenty eight", "ROM 8:28"),
    ("turn with me to first john four eight", "1JN 4:8"),
    ("the book of revelation chapter twenty two", "REV 22:1"),
    # Range words
    ("john three sixteen to eighteen", "JHN 3:16-18"),
    ("john three sixteen thru eighteen", "JHN 3:16-18"),
    ("romans eight twenty-eight through thirty", "ROM 8:28-30"),
    # Mixed digits and words
    ("Romans 8 twenty-eight", "ROM 8:28"),
    ("first cor 13 verse 4", "1CO 13:4"),
])
def test_spoken_form(inp, expected):
    _expect(inp, expected)


# ============================================================
# Yoruba book names
# ============================================================

@pytest.mark.parametrize("inp,expected", [
    ("Johanu 3:16", "JHN 3:16"),
    ("johanu 3 16", "JHN 3:16"),
    ("1 Kọrinti 13:4", "1CO 13:4"),
    ("Saamu 23:1", "PSA 23:1"),
    ("Òwe 3:5", "PRO 3:5"),
    ("Ìfihàn 22:13", "REV 22:13"),
    ("Ifihan 22 13", "REV 22:13"),  # diacritics stripped
    ("Romu 8:28", "ROM 8:28"),
    ("1 Tẹsalonika 5:16", "1TH 5:16"),
    ("Eksodu 20:3", "EXO 20:3"),
])
def test_yoruba_form(inp, expected):
    _expect(inp, expected)


# ============================================================
# Context-stateful parsing
# ============================================================

def test_next_chapter_resolves():
    ctx = ParseContext()
    ctx.update(parse("Romans 8:28", use_llm=False))
    ref = parse("the next chapter", context=ctx, use_llm=False)
    assert ref is not None
    assert str(ref) == "ROM 9:1"
    assert ref.source == "context"


def test_previous_chapter_resolves():
    ctx = ParseContext()
    ctx.update(parse("Romans 8:28", use_llm=False))
    ref = parse("the previous chapter", context=ctx, use_llm=False)
    assert ref is not None
    assert str(ref) == "ROM 7:1"


def test_verse_only_resolves_against_context():
    ctx = ParseContext()
    ctx.update(parse("Romans 8:28", use_llm=False))
    ref = parse("verse twelve", context=ctx, use_llm=False)
    assert ref is not None
    assert str(ref) == "ROM 8:12"


def test_verse_range_resolves_against_context():
    ctx = ParseContext()
    ctx.update(parse("Romans 8:28", use_llm=False))
    ref = parse("verses 9 and 10", context=ctx, use_llm=False)
    assert ref is not None
    assert str(ref) == "ROM 8:9-10"


def test_explicit_book_overrides_context():
    """If the new input names a book, ignore context entirely."""
    ctx = ParseContext()
    ctx.update(parse("Romans 8:28", use_llm=False))
    ref = parse("John 3:16", context=ctx, use_llm=False)
    assert str(ref) == "JHN 3:16"
    assert ref.source != "context"


def test_no_context_no_resolution():
    ctx = ParseContext()
    ref = parse("the next chapter", context=ctx, use_llm=False)
    assert ref is None


def test_previous_chapter_at_chapter_one_is_noop():
    ctx = ParseContext()
    ctx.update(parse("Genesis 1:1", use_llm=False))
    ref = parse("the previous chapter", context=ctx, use_llm=False)
    assert ref is None  # there is no Genesis 0


# ============================================================
# Negative cases
# ============================================================

@pytest.mark.parametrize("inp", [
    "",
    "   ",
    "hello world",
    "good morning church",
    "let us pray",
    "the lord is good",
    "12345",  # numbers without a book
])
def test_no_match(inp):
    _expect(inp, None)


# ============================================================
# Confidence and source tracking
# ============================================================

def test_unambiguous_book_has_full_confidence():
    ref = parse("John 3:16", use_llm=False)
    assert ref.confidence == 1.0


def test_ambiguous_bare_book_has_reduced_confidence():
    ref = parse("Corinthians 13:4", use_llm=False)
    assert ref is not None
    assert ref.book == "1CO"
    assert ref.confidence < 1.0


def test_diacritic_stripped_yoruba_has_reduced_confidence():
    ref = parse("Ifihan 22:13", use_llm=False)
    assert ref is not None
    assert ref.book == "REV"
    assert ref.confidence < 1.0


def test_source_marks_written_vs_spoken():
    assert parse("John 3:16", use_llm=False).source == "regex_written"
    assert parse("John three sixteen", use_llm=False).source == "regex_spoken"
