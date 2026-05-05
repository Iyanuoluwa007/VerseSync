"""Yoruba scripture phrasing normalizer.

Yoruba pastors typically cite scripture as:
    "Johanu kini ori keta ese kerin"
     |  book  |  | ord | |chapter| |ord-3| |verse| |ord-4|
     1 John, chapter 3, verse 4

This module converts that phrasing into a digit form that the existing
English-trained regex parser can handle:
    "Johanu kini ori keta ese kerin"  ->  "1 johanu 3 4"

The rules:
    1. Yoruba ordinal AFTER an ordinal-eligible book name moves to the
       front as a digit:        "Johanu kini" -> "1 johanu"
    2. "Ori" + ordinal becomes a chapter digit:    "ori keta" -> "3"
    3. "Ese" + ordinal becomes a verse digit:      "ese kerin" -> "4"
    4. Range marker "si" between ordinals becomes a hyphen:
                                "ese kerin si keje" -> "4 - 7"

We deliberately keep the table to ordinals 1..30 and a few common
multiples. Beyond that, the LLM fallback handles it -- the Yoruba
vigesimal system above 30 gets compound and not worth hand-coding.
"""
from __future__ import annotations

import re
import unicodedata


# Yoruba ordinal numerals 1..30. Multiple spellings per number because
# diacritics in spoken speech (and STT output) are unreliable.
# Source: Yoruba Wikibooks ordinal table; cross-checked against BMYO
# verse references and Yoruba Bible reading examples.
YORUBA_ORDINAL_TO_INT: dict[str, int] = {
    # 1
    "kini": 1, "ikini": 1, "akoko": 1, "akọkọ": 1, "ekini": 1, "kíní": 1,
    # 2
    "keji": 2, "kéjì": 2, "ekeji": 2, "èkejì": 2,
    # 3
    "keta": 3, "kẹta": 3, "eketa": 3, "èkẹta": 3,
    # 4
    "kerin": 4, "kẹrin": 4, "ekerin": 4, "èkẹrin": 4,
    # 5
    "karun": 5, "kàrún": 5, "kárún": 5, "karunun": 5, "kàrúnún": 5,
    "karun-un": 5, "ekarun": 5,
    # 6
    "kefa": 6, "kẹfa": 6, "kẹfà": 6, "ekefa": 6, "èkẹfà": 6,
    # 7
    "keje": 7, "ekeje": 7, "èkeje": 7,
    # 8
    "kejo": 8, "kẹjọ": 8, "ekejo": 8, "èkẹjọ": 8,
    # 9
    "kesan": 9, "kẹsan": 9, "kẹsàán": 9, "kesanan": 9,
    "ekesan": 9, "èkẹsàán": 9,
    # 10
    "kewa": 10, "kẹwa": 10, "kewaa": 10, "kẹwaa": 10, "kẹwàá": 10,
    "ekewa": 10, "èkẹwàá": 10,
    # 11
    "kokanla": 11, "kọkanla": 11, "kọ́kànlá": 11, "ọkanla": 11, "ọkànlá": 11,
    # 12
    "kejila": 12, "kéjìlá": 12, "kẹjila": 12, "kẹjìlá": 12,
    # 13
    "ketala": 13, "kẹtàlá": 13, "kẹtala": 13, "kétàlá": 13,
    # 14
    "kerinla": 14, "kẹrinla": 14, "kẹ́rìnlá": 14, "kẹrìnlá": 14,
    # 15
    "keedogun": 15, "kẹẹdogun": 15, "kẹẹ̀dógún": 15, "kéédógún": 15,
    "kedogun": 15, "kẹdogun": 15,
    "marundinlogun": 15, "márùndínlógún": 15, "mẹẹdogun": 15,
    # 16-19 (subtractive: "X less than 20")
    "kerindinlogun": 16, "kẹrindinlogun": 16, "kẹrìndínlógún": 16,
    "ẹrindinlogun": 16, "ẹ́rìndínlógún": 16,
    "ketadinlogun": 17, "kẹtadinlogun": 17, "kẹtàdínlógún": 17,
    "ẹtadinlogun": 17, "ẹ́tàdínlógún": 17,
    "kejidinlogun": 18, "kẹjidinlogun": 18, "kéjìdínlógún": 18,
    "ẹjidinlogun": 18, "éjìdínlógún": 18,
    "kokandinlogun": 19, "kọkandinlogun": 19, "kọ́kàndínlógún": 19,
    "ẹkokandinlogun": 19, "ọ́kàndínlógún": 19,
    # 20
    "kogun": 20, "ogun": 20, "kogún": 20, "ọgún": 20,
    # 21-29 (additive: "20 + X" or subtractive from 30)
    "kokanlelogun": 21, "kọ́kànlélógún": 21, "ọkanlelogun": 21,
    "kejilelogun": 22, "kẹjilelogun": 22, "méjìlélógún": 22,
    "mejilelogun": 22,
    "ketalelogun": 23, "kẹtalelogun": 23, "mẹ́tàlélógún": 23,
    "metalelogun": 23, "kẹtàlélógún": 23,
    "kerinlelogun": 24, "kẹrinlelogun": 24, "mẹ́rìnlélógún": 24,
    "merinlelogun": 24, "kẹrìnlélógún": 24,
    "marundinlogbon": 25, "márùndínlọ́gbọ̀n": 25, "mẹẹdogbon": 25,
    "kerindinlogbon": 26, "kẹrindinlogbon": 26, "mẹ́rìndínlọ́gbọ̀n": 26,
    "ketadinlogbon": 27, "kẹtadinlogbon": 27, "mẹ́tàdínlọ́gbọ̀n": 27,
    "kejidinlogbon": 28, "kẹjidinlogbon": 28, "méjìdínlọ́gbọ̀n": 28,
    "kokandinlogbon": 29, "kọkandinlogbon": 29, "ọ̀kándínlọ́gbọ̀n": 29,
    # 30
    "ogbon": 30, "ọgbọn": 30, "ọgbọ̀n": 30,
}

# Books that take an ordinal SUFFIX in Yoruba speech.
# (USFM_code, base_yoruba_name, ordinal_value)
YORUBA_ORDINAL_BOOKS: list[tuple[str, str, int]] = [
    ("1JN", "johanu", 1), ("2JN", "johanu", 2), ("3JN", "johanu", 3),
    ("1CO", "kọrinti", 1), ("2CO", "kọrinti", 2),
    ("1CO", "korinti", 1), ("2CO", "korinti", 2),       # diacritic-free
    ("1TH", "tẹsalonika", 1), ("2TH", "tẹsalonika", 2),
    ("1TH", "tesalonika", 1), ("2TH", "tesalonika", 2),
    ("1TI", "timotiu", 1), ("2TI", "timotiu", 2),
    ("1PE", "peteru", 1), ("2PE", "peteru", 2),
    ("1SA", "samueli", 1), ("2SA", "samueli", 2),
    ("1SA", "samuẹli", 1), ("2SA", "samuẹli", 2),
    ("1KI", "ọba", 1), ("2KI", "ọba", 2),
    ("1KI", "oba", 1), ("2KI", "oba", 2),
    ("1CH", "kronika", 1), ("2CH", "kronika", 2),
]

# Pre-compute reverse map: int -> set of ordinal spellings, for the
# book-suffix step.
_INT_TO_ORDINALS: dict[int, list[str]] = {}
for _ord, _val in YORUBA_ORDINAL_TO_INT.items():
    _INT_TO_ORDINALS.setdefault(_val, []).append(_ord)

# Sort ordinal table so we always try the LONGEST spelling first; this
# prevents "keji" prefix-matching inside "kejila" etc.
_SORTED_ORDINALS = sorted(YORUBA_ORDINAL_TO_INT.items(),
                          key=lambda kv: (-len(kv[0]), kv[0]))

# Yoruba range word: "si" = "to". Don't be too aggressive -- "si" is
# also a common Yoruba word elsewhere, so we only treat it as a range
# marker when sandwiched between digits (after the ordinal pass).
_RANGE_WORD_RX = re.compile(r"\b(\d{1,3})\s+si\s+(\d{1,3})\b", re.IGNORECASE)


def _strip_diacritics(s: str) -> str:
    """ASCII-fold for matching when STT drops tone marks."""
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def normalize_yoruba(text: str) -> str:
    """Convert Yoruba scripture phrasing to digit form.

    Idempotent for English/digit-only input -- the rules only fire on
    actual Yoruba marker words. Internally we work on an ASCII-folded
    copy so any combination of tone marks in the input matches.
    """
    # ASCII-strip + lowercase upfront: from here on every rule operates
    # on plain ASCII letters. The Yoruba book-name lexicon already has
    # ASCII-stripped variants registered, so the downstream parser still
    # finds them.
    out = _strip_diacritics(text).lower()

    # Step 1: book-suffix ordinals.  "Johanu kini" -> "1 Johanu"
    # We try each (book, ordinal) pair; longer ordinal spellings first.
    for ord_word, _ in _SORTED_ORDINALS:
        for code, book_yor, ordinal_val in YORUBA_ORDINAL_BOOKS:
            if YORUBA_ORDINAL_TO_INT.get(ord_word) != ordinal_val:
                continue
            # ASCII-strip the patterns to match the ASCII-stripped text.
            book_ascii = _strip_diacritics(book_yor).lower()
            ord_ascii = _strip_diacritics(ord_word).lower()
            pattern = rf"\b{re.escape(book_ascii)}\s+{re.escape(ord_ascii)}\b"
            replacement = f"{ordinal_val} {book_ascii}"
            out = re.sub(pattern, replacement, out)

    # Step 2: "Ori" + ordinal -> chapter digit
    #         "Ese" + ordinal -> verse digit
    for ord_word, value in _SORTED_ORDINALS:
        ord_ascii = _strip_diacritics(ord_word).lower()
        out = re.sub(
            rf"\b(?:ori)[\s\-]*{re.escape(ord_ascii)}\b",
            f" {value} ",
            out,
        )
        out = re.sub(
            rf"\b(?:ese)[\s\-]*{re.escape(ord_ascii)}\b",
            f" {value} ",
            out,
        )

    # Step 3: "Ori" / "Ese" followed by a bare digit (mixed-language
    # speech: "ori 3 ese 4")
    out = re.sub(r"\b(?:ori)\s+(\d{1,3})\b", r" \1 ", out)
    out = re.sub(r"\b(?:ese)\s+(\d{1,3})\b", r" \1 ", out)

    # Step 4: Yoruba range word "si" connecting verses.
    # First convert "<digit> si <ordinal>" -> "<digit> - <digit>" since
    # bare ordinals after "si" weren't preceded by ese/ori.
    for ord_word, value in _SORTED_ORDINALS:
        ord_ascii = _strip_diacritics(ord_word).lower()
        out = re.sub(
            rf"\b(\d{{1,3}})\s+si\s+{re.escape(ord_ascii)}\b",
            rf"\1-{value}",
            out,
        )
    # Then handle the all-digit form ("4 si 7" -> "4-7").
    out = _RANGE_WORD_RX.sub(r"\1-\2", out)

    # Collapse whitespace
    out = re.sub(r"\s+", " ", out).strip()
    return out


def looks_yoruba(text: str) -> bool:
    """Cheap heuristic: does this string contain Yoruba marker words?

    Used to decide whether to run the (somewhat expensive) Yoruba pass
    in the parser pipeline. False positives are OK -- the normaliser is
    a no-op on pure-English text. We only want to skip the work when
    nothing Yoruba-looking is present.
    """
    lower = _strip_diacritics(text).lower()
    return bool(re.search(r"\b(?:ori|ese|kini|keji|keta|kerin|karun|"
                          r"kefa|keje|kejo|kesan|kewa|johanu|kọrinti|"
                          r"korinti|saamu|romu|ifihan|matiu|marku|luku|"
                          r"tesalonika|tẹsalonika|timotiu|peteru|akoko|"
                          r"akọkọ)\b", lower))
