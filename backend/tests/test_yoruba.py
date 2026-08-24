"""Tests for the Yoruba scripture phrasing normalizer + parser integration.

Covers the patterns Iyanuoluwa flagged: 'Johanu kini Ori keta Ese kerin'
style references, plus context-stateful follow-ups, and graceful no-op
behaviour on English-only input.
"""
import pytest

from app.parser.parser import parse
from app.parser.types import ParseContext
from app.parser.yoruba import looks_yoruba, normalize_yoruba

# ============================================================
# Direct normaliser tests -- output is a string, before regex.
# ============================================================

@pytest.mark.parametrize("inp,contains_in_order", [
    ("Johanu kini Ori keta Ese kerin", ["1 johanu", "3", "4"]),
    ("Saamu Ori kẹtalelogun ese kini", ["saamu", "23", "1"]),
    ("Romu ori kẹjọ ese kọkanlelogun", ["romu", "8", "21"]),
    ("Kọrinti kini ori kẹtàlá ese kefa", ["1 korinti", "13", "6"]),
    ("Genesisi ori kini ese kini", ["genesisi", "1", "1"]),
])
def test_normalizer_produces_expected_tokens(inp, contains_in_order):
    out = normalize_yoruba(inp).lower()
    last_pos = -1
    for token in contains_in_order:
        idx = out.find(token, last_pos + 1)
        assert idx != -1, (
            f"Token {token!r} missing or out of order in {out!r} (input: {inp!r})"
        )
        last_pos = idx


def test_normalizer_no_op_on_english():
    assert normalize_yoruba("John 3:16") in ("john 3:16", "john 3 : 16")
    assert "romans 8:28" in normalize_yoruba("Romans 8:28").lower()


def test_looks_yoruba_detects_markers():
    assert looks_yoruba("Johanu kini Ori keta Ese kerin")
    assert looks_yoruba("Saamu ori kogun")
    assert looks_yoruba("ese kerin si keje")
    assert not looks_yoruba("John 3:16")
    assert not looks_yoruba("Romans eight twenty-eight")
    assert not looks_yoruba("hello world")


# ============================================================
# End-to-end parser integration
# ============================================================

@pytest.mark.parametrize("inp,expected", [
    # The exact pattern Iyanuoluwa flagged
    ("Johanu kini Ori keta Ese kerin", "1JN 3:4"),
    # Compact form (no spaces between marker and ordinal)
    ("Johanu kini oriketa esekerin", "1JN 3:4"),
    # Various ordinals in chapter and verse
    ("Saamu ori keta ese kini", "PSA 3:1"),
    ("Saamu Ori kẹtalelogun ese kini", "PSA 23:1"),
    ("Saamu ori kerinla ese karun", "PSA 14:5"),
    # Different ordinal-prefixed books
    ("Kọrinti kini ori kẹtàlá ese kerin", "1CO 13:4"),
    ("Korinti keji ori karun ese kẹrindinlogun", "2CO 5:16"),
    ("Tẹsalonika kini ori karun ese kerindinlogun", "1TH 5:16"),
    ("Timotiu keji ori keta ese kẹrindinlogun", "2TI 3:16"),
    ("Peteru kini ori karun ese keje", "1PE 5:7"),
    ("Johanu keji ori kini ese kerin", "2JN 1:4"),
    # Single-word books
    ("Romu ori kejo ese kejilelogun", "ROM 8:22"),
    ("Romu ori kẹjọ ese kọkanlelogun", "ROM 8:21"),
    ("Genesisi ori kini ese kini", "GEN 1:1"),
    ("Ifihan ori kogun ese kini", "REV 20:1"),
    ("Matiu ori karun ese keta", "MAT 5:3"),
    # Higher numbers (within valid bounds for the book)
    ("Saamu ori karun ese kerinla", "PSA 5:14"),
    ("Romu ori kerinla ese kẹsan", "ROM 14:9"),
    # Diacritic permutations on the same ordinal -- should all work
    ("Romu ori kejo ese kọ́kànlélógún", "ROM 8:21"),
    ("Romu ori kejo ese kokanlelogun", "ROM 8:21"),
    ("Romu ori kejo ese kọkanlelogun", "ROM 8:21"),
])
def test_yoruba_full_phrasing(inp, expected):
    ref = parse(inp, use_llm=False)
    assert ref is not None, f"Got None for {inp!r}"
    assert str(ref) == expected, f"For {inp!r}: expected {expected}, got {ref}"


# ============================================================
# Range with "si"
# ============================================================

def test_yoruba_range_with_si_in_full_reference():
    """'Romu ori kejo ese kerin si keje' -> ROM 8:4-7 (range)."""
    ref = parse("Romu ori kejo ese kerin si keje", use_llm=False)
    assert ref is not None
    assert ref.book == "ROM"
    assert ref.chapter == 8
    assert ref.verse_start == 4
    assert ref.verse_end == 7


def test_yoruba_range_with_si_in_context():
    """After 'Romu 8:1', 'ese kerin si keje' -> ROM 8:4-7."""
    ctx = ParseContext()
    ctx.update(parse("Romu ori kejo ese kini", use_llm=False))
    ref = parse("ese kerin si keje", context=ctx, use_llm=False)
    assert ref is not None
    assert ref.book == "ROM"
    assert ref.chapter == 8
    assert ref.verse_start == 4
    assert ref.verse_end == 7


# ============================================================
# Context follow-ups
# ============================================================

def test_yoruba_bare_ese_resolves_against_context():
    """After 'Romu 8:21', 'ese kefa' -> ROM 8:6."""
    ctx = ParseContext()
    ctx.update(parse("Romu ori kejo ese kọkanlelogun", use_llm=False))
    ref = parse("ese kefa", context=ctx, use_llm=False)
    assert ref is not None
    assert str(ref) == "ROM 8:6"


def test_yoruba_doesnt_break_english():
    """English phrasing must keep working untouched."""
    ref = parse("Romans 8:28", use_llm=False)
    assert str(ref) == "ROM 8:28"
    ref = parse("first thessalonians five sixteen", use_llm=False)
    assert str(ref) == "1TH 5:16"


def test_yoruba_source_label_is_spoken():
    """Yoruba inputs go through normalisation -> labelled as spoken."""
    ref = parse("Johanu kini Ori keta Ese kerin", use_llm=False)
    assert ref.source == "regex_spoken"


# ============================================================
# Confidence
# ============================================================

def test_yoruba_unambiguous_book_full_confidence():
    ref = parse("Johanu kini Ori keta Ese kerin", use_llm=False)
    assert ref.confidence == 1.0
