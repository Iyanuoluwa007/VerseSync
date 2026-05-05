"""Tests for v0.4.4: parser bounds check + Whisper hallucination filter.

These guard against two real failures we hit in live use:

1. Whisper sometimes mishears compound words like "Matt 25:5" as "Matt255"
   (no space). The regex parser would happily emit MAT 255:1, which is
   nonsense (Matthew has 28 chapters). The bounds check now rejects it,
   forcing fallback to the LLM which handles malformed input better.

2. Whisper-medium on poor Yoruba audio hallucinates Tibetan / Bengali /
   Punjabi scripts at confidence=1.00. We don't want to feed that to the
   parser or burn LLM quota on it.
"""
import pytest

from app.bible.books import is_valid_chapter, BOOK_MAX_CHAPTERS
from app.parser.parser import parse
from app.stt.pipeline import _is_hallucinated_transcript


# ============================================================
# Bounds check
# ============================================================

class TestBoundsCheck:

    def test_max_chapters_table_complete(self):
        # All 66 books must be present
        assert len(BOOK_MAX_CHAPTERS) == 66

    def test_known_max_chapters(self):
        assert BOOK_MAX_CHAPTERS["MAT"] == 28
        assert BOOK_MAX_CHAPTERS["PSA"] == 150     # Psalms is the long one
        assert BOOK_MAX_CHAPTERS["JHN"] == 21
        assert BOOK_MAX_CHAPTERS["REV"] == 22
        assert BOOK_MAX_CHAPTERS["OBA"] == 1       # Single-chapter books
        assert BOOK_MAX_CHAPTERS["JUD"] == 1
        assert BOOK_MAX_CHAPTERS["3JN"] == 1

    def test_is_valid_chapter(self):
        assert is_valid_chapter("MAT", 1)
        assert is_valid_chapter("MAT", 28)
        assert not is_valid_chapter("MAT", 0)
        assert not is_valid_chapter("MAT", 29)
        assert not is_valid_chapter("MAT", 255)
        assert is_valid_chapter("PSA", 150)
        assert not is_valid_chapter("PSA", 151)
        assert not is_valid_chapter("XXX", 1)      # unknown book

    def test_parser_rejects_out_of_range_chapter_no_llm(self):
        """The Whisper-induced bug: 'Matt255' parses to MAT 255:1, which
        is invalid. With LLM disabled, the regex match must be rejected
        and the whole parse should return None."""
        assert parse("Matt255", use_llm=False) is None

    def test_parser_accepts_valid_high_chapter(self):
        ref = parse("Psalm 150:1", use_llm=False)
        assert ref is not None
        assert ref.book == "PSA"
        assert ref.chapter == 150

    def test_parser_rejects_psalm_151(self):
        assert parse("Psalm 151:1", use_llm=False) is None

    def test_parser_normal_refs_still_work(self):
        cases = [
            ("Matt 25:5", "MAT", 25, 5),
            ("John 3:16", "JHN", 3, 16),
            ("Genesis 1:1", "GEN", 1, 1),
            ("Romans 8:28", "ROM", 8, 28),
            ("1 John 3:4", "1JN", 3, 4),
            ("Revelation 22:21", "REV", 22, 21),
        ]
        for inp, book, ch, vs in cases:
            ref = parse(inp, use_llm=False)
            assert ref is not None, f"Parse failed for {inp!r}"
            assert (ref.book, ref.chapter, ref.verse_start) == (book, ch, vs), \
                f"For {inp!r}: got {ref}"

    def test_parser_rejects_revelation_23(self):
        # Revelation has 22 chapters
        assert parse("Revelation 23:1", use_llm=False) is None

    def test_parser_yoruba_bounds(self):
        # Yoruba phrasing also gets bounds-checked
        assert parse("Genesisi ori 51 ese 1", use_llm=False) is None
        ref = parse("Genesisi ori kini ese kini", use_llm=False)
        assert ref is not None and ref.book == "GEN"


# ============================================================
# Hallucination filter
# ============================================================

class TestHallucinationFilter:

    @pytest.mark.parametrize("text", [
        # The actual outputs from the real Yoruba session that triggered this work
        "སེེེེེེེེེེེེེེེེེེེེེེེེེེེེེེེེེེེེེེེེེེེེེེེེེེེེེེེེེེེེེེེེེེེེེེེེེ",
        "ʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻʻ",
        "ਸੀਸੀਸੀਸੀਸੀਸੀਸੀਸੀਸੀਸੀਸੀਸੀਸੀਸੀਸੀਸੀਸੀਸੀਸੀਸੀਸੀਸੀਸੀਸੀਸੀਸੀਸੀਸੀਸੀਸੀਸੀਸੀਸੀਸੀਸੀਸੀਸੀਸੀਸੀਸੀਸੀਸਸੀਸੀਸੀ",
        "বেবববববববববববববববববববববববববববববববববববববববববববববববববববববববববববববববববববববববববববববব",
    ])
    def test_real_garbled_yoruba_transcripts_dropped(self, text):
        is_halluc, reason = _is_hallucinated_transcript(text, "yo")
        assert is_halluc, f"Expected hallucination flag for: {text[:40]!r}"
        assert reason  # explanation present

    @pytest.mark.parametrize("text", [
        "Jamukini orikeri esekeri",                 # Garbled but Latin
        "Johanu kini Ori keta Ese kerin",           # Real Yoruba speech
        "Bí Ọlọ́run ti fẹ́ aráyé tó bẹ́ẹ̀",          # Yoruba w/ heavy diacritics
        "1 johanu 3:4",                              # already-normalised form
    ])
    def test_real_yoruba_passes_through(self, text):
        is_halluc, _ = _is_hallucinated_transcript(text, "yo")
        assert not is_halluc, f"False positive for real Yoruba: {text!r}"

    @pytest.mark.parametrize("text", [
        "John 3.16",
        "In the beginning, God created everyone else.",
        "We are Ambassador for Christ.",
        "Matt255",
        "Loop.",
        "and I'll see you next time.",
    ])
    def test_english_passes_through(self, text):
        is_halluc, _ = _is_hallucinated_transcript(text, "en")
        assert not is_halluc, f"False positive for English: {text!r}"

    def test_empty_and_short_inputs_safe(self):
        for text in ("", " ", "!", "ok"):
            is_halluc, _ = _is_hallucinated_transcript(text, "en")
            assert not is_halluc

    def test_repetition_detector(self):
        # >70% same-character repetition flagged regardless of script
        is_halluc, reason = _is_hallucinated_transcript("aaaaaaaaaaaaaaaa", "en")
        assert is_halluc
        assert "single character" in reason

    def test_mostly_punctuation_not_flagged(self):
        # Punctuation isn't alphabetic, so hits the "no alpha chars" path
        is_halluc, _ = _is_hallucinated_transcript("...", "en")
        assert not is_halluc
