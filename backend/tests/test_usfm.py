"""Tests for the USFM parser."""
from app.bible.usfm import parse_usfm


def test_parses_simple_verse():
    src = """\\id JHN
\\c 3
\\v 16 For God so loved the world.
"""
    verses = list(parse_usfm(src))
    assert len(verses) == 1
    assert verses[0].book == "JHN"
    assert verses[0].chapter == 3
    assert verses[0].verse == 16
    assert verses[0].text == "For God so loved the world."


def test_strips_strongs_word_attributes():
    src = """\\id JHN
\\c 1
\\v 1 \\w In|strong="G1722"\\w* the \\w beginning|strong="G0746"\\w*.
"""
    verses = list(parse_usfm(src))
    assert verses[0].text == "In the beginning."


def test_strips_words_of_jesus_marker():
    src = """\\id MAT
\\c 5
\\v 3 \\wj Blessed are the poor in spirit.\\wj*
"""
    verses = list(parse_usfm(src))
    assert verses[0].text == "Blessed are the poor in spirit."


def test_strips_added_words_marker():
    src = """\\id GEN
\\c 1
\\v 1 In the beginning \\add God\\add* created.
"""
    verses = list(parse_usfm(src))
    assert verses[0].text == "In the beginning God created."


def test_drops_footnotes_and_xrefs():
    src = """\\id JHN
\\c 1
\\v 1 In the beginning was the Word\\f + \\fr 1.1 \\ft footnote here\\f* and so on.
\\v 2 The Word \\x - \\xo 1.2 \\xt cross ref\\x* was with God.
"""
    verses = list(parse_usfm(src))
    assert "footnote" not in verses[0].text
    assert "cross ref" not in verses[1].text
    assert verses[0].text == "In the beginning was the Word and so on."


def test_handles_multiline_verse():
    src = """\\id PSA
\\c 23
\\v 1 The LORD is my shepherd;
I shall not want.
"""
    verses = list(parse_usfm(src))
    assert verses[0].text == "The LORD is my shepherd; I shall not want."


def test_preserves_yoruba_diacritics():
    src = """\\id JHN
\\c 1
\\v 1 Ní àtètèkọ́ṣe ni Ọ̀rọ̀ wà.
"""
    verses = list(parse_usfm(src))
    # Critical: every accented character must survive untouched.
    assert verses[0].text == "Ní àtètèkọ́ṣe ni Ọ̀rọ̀ wà."


def test_skips_files_without_id_marker():
    src = "\\h Front Matter\nNo \\id marker here.\n"
    verses = list(parse_usfm(src))
    assert verses == []


def test_drops_pilcrow_paragraph_marks():
    src = """\\id JHN
\\c 1
\\v 6 ¶ There was a man sent from God.
"""
    verses = list(parse_usfm(src))
    assert verses[0].text == "There was a man sent from God."


def test_handles_paragraph_marker_between_verses():
    src = """\\id JHN
\\c 1
\\v 5 Light shines.
\\p
\\v 6 A man was sent.
"""
    verses = list(parse_usfm(src))
    assert len(verses) == 2
    assert verses[0].text == "Light shines."
    assert verses[1].text == "A man was sent."
