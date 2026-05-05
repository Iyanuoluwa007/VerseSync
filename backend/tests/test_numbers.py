"""Tests for the number-word parser."""
import pytest

from app.parser.numbers import parse_number, words_to_digits


@pytest.mark.parametrize("inp,expected", [
    ("zero", 0),
    ("one", 1),
    ("three", 3),
    ("nine", 9),
    ("ten", 10),
    ("sixteen", 16),
    ("nineteen", 19),
    ("twenty", 20),
    ("twenty-three", 23),
    ("twenty three", 23),
    ("ninety-nine", 99),
    ("one hundred", 100),
    ("one hundred fifty", 150),
    ("one hundred and fifty", 150),
    ("one hundred and nineteen", 119),
    ("one hundred and seventy-six", 176),
    ("two hundred and twelve", 212),
    # Bare digits pass through
    ("3", 3),
    ("119", 119),
    ("176", 176),
])
def test_parse_number_succeeds(inp, expected):
    assert parse_number(inp) == expected


@pytest.mark.parametrize("inp", [
    "",
    "hello",
    "abc def",
])
def test_parse_number_returns_none_for_garbage(inp):
    assert parse_number(inp) is None


@pytest.mark.parametrize("inp,expected", [
    # Bible-relevant phrases
    ("John three sixteen", "John 3 16"),
    ("Romans eight twenty-eight", "Romans 8 28"),
    ("first thessalonians five sixteen", "first thessalonians 5 16"),
    ("Psalm one hundred and nineteen", "Psalm 119"),
    ("two hundred and twelve", "212"),
    # Adjacent independent numbers must NOT collapse
    ("three sixteen", "3 16"),
    ("one twenty three", "1 23"),  # 1 then 23 (Psalm 1:23 nonsense, but illustrative)
    ("five seventeen", "5 17"),
    # Already digits pass through
    ("John 3:16", "John 3 : 16"),
    ("Romans 8 28", "Romans 8 28"),
    # Mixed
    ("Romans eight 28", "Romans 8 28"),
    # Empty / no numbers
    ("hello world", "hello world"),
])
def test_words_to_digits(inp, expected):
    assert words_to_digits(inp) == expected
