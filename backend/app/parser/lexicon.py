"""Book name lexicon for parsing.

Every way a preacher might say or write each book of the Bible, mapped
to its USFM 3-letter code. We build the table programmatically rather
than hand-listing every variant -- ordinal-prefixed books (1/2/3 +
Samuel, Kings, etc.) explode into many forms but follow predictable
rules.

Yoruba book names are pulled from the BMYO running headers we captured
at ingest time and augmented with common spoken variants where the
headers drop tone marks.

Patterns are sorted longest-first at lookup time so "first thessalonians"
matches before "thessalonians" alone.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from functools import lru_cache

from app.bible.db import connect


# --- English base names + abbreviations ---
# (USFM_code, canonical_english, [abbrevs/aliases without ordinal prefix],
#  ordinal_prefix or None)
#
# For ordinal books (1/2/3 + book), the canonical and abbrevs are the
# *base* name (e.g. "Samuel", not "1 Samuel"); the prefix is added by
# generate_patterns().
_BOOK_DATA: list[tuple[str, str, list[str], int | None]] = [
    # OT
    ("GEN", "Genesis", ["gen", "ge", "gn"], None),
    ("EXO", "Exodus", ["exo", "ex", "exod"], None),
    ("LEV", "Leviticus", ["lev", "le", "lv"], None),
    ("NUM", "Numbers", ["num", "nu", "nm", "nb"], None),
    ("DEU", "Deuteronomy", ["deu", "deut", "dt"], None),
    ("JOS", "Joshua", ["jos", "josh", "jsh"], None),
    ("JDG", "Judges", ["jdg", "judg", "jg"], None),
    ("RUT", "Ruth", ["rut", "ru", "rth"], None),
    ("1SA", "Samuel", ["sam", "sa", "sm"], 1),
    ("2SA", "Samuel", ["sam", "sa", "sm"], 2),
    ("1KI", "Kings", ["kgs", "ki", "kin"], 1),
    ("2KI", "Kings", ["kgs", "ki", "kin"], 2),
    ("1CH", "Chronicles", ["chr", "chron", "ch"], 1),
    ("2CH", "Chronicles", ["chr", "chron", "ch"], 2),
    ("EZR", "Ezra", ["ezr", "ez"], None),
    ("NEH", "Nehemiah", ["neh", "ne"], None),
    ("EST", "Esther", ["est", "es", "esth"], None),
    ("JOB", "Job", ["job", "jb"], None),
    ("PSA", "Psalms", ["psa", "ps", "psalm", "pslm", "pss"], None),
    ("PRO", "Proverbs", ["pro", "pr", "prov", "prv"], None),
    ("ECC", "Ecclesiastes", ["ecc", "ec", "eccl", "qoh", "qoheleth"], None),
    ("SNG", "Song of Solomon",
     ["sng", "song", "song of songs", "ss", "canticles", "cant"], None),
    ("ISA", "Isaiah", ["isa", "is"], None),
    ("JER", "Jeremiah", ["jer", "je", "jr"], None),
    ("LAM", "Lamentations", ["lam", "la"], None),
    ("EZK", "Ezekiel", ["ezk", "eze", "ezek", "ek"], None),
    ("DAN", "Daniel", ["dan", "da", "dn"], None),
    ("HOS", "Hosea", ["hos", "ho"], None),
    ("JOL", "Joel", ["joe", "jol", "jl"], None),
    ("AMO", "Amos", ["amo", "am"], None),
    ("OBA", "Obadiah", ["oba", "ob"], None),
    ("JON", "Jonah", ["jon", "jnh"], None),
    ("MIC", "Micah", ["mic", "mi"], None),
    ("NAM", "Nahum", ["nam", "na", "nah"], None),
    ("HAB", "Habakkuk", ["hab", "hb"], None),
    ("ZEP", "Zephaniah", ["zep", "zp", "zeph"], None),
    ("HAG", "Haggai", ["hag", "hg"], None),
    ("ZEC", "Zechariah", ["zec", "zech", "zc"], None),
    ("MAL", "Malachi", ["mal", "ml"], None),
    # NT
    ("MAT", "Matthew", ["mat", "mt", "matt"], None),
    ("MRK", "Mark", ["mrk", "mk", "mar"], None),
    ("LUK", "Luke", ["luk", "lk", "lu"], None),
    ("JHN", "John",
     ["jhn", "jn", "jno",
      "gospel of john", "the gospel of john",
      "gospel according to john", "saint john", "st john"], None),
    ("ACT", "Acts", ["act", "ac", "acts of the apostles"], None),
    ("ROM", "Romans", ["rom", "ro", "rm"], None),
    ("1CO", "Corinthians", ["cor", "co"], 1),
    ("2CO", "Corinthians", ["cor", "co"], 2),
    ("GAL", "Galatians", ["gal", "ga"], None),
    ("EPH", "Ephesians", ["eph", "ep"], None),
    ("PHP", "Philippians", ["php", "phil", "pp"], None),
    ("COL", "Colossians", ["col", "cl"], None),
    ("1TH", "Thessalonians", ["thess", "th"], 1),
    ("2TH", "Thessalonians", ["thess", "th"], 2),
    ("1TI", "Timothy", ["tim", "ti"], 1),
    ("2TI", "Timothy", ["tim", "ti"], 2),
    ("TIT", "Titus", ["tit", "ti"], None),
    ("PHM", "Philemon", ["phm", "pm", "phlm"], None),
    ("HEB", "Hebrews", ["heb", "he"], None),
    ("JAS", "James", ["jas", "jm", "jam"], None),
    ("1PE", "Peter", ["pet", "pt", "pe"], 1),
    ("2PE", "Peter", ["pet", "pt", "pe"], 2),
    ("1JN", "John", ["jn", "jhn", "jno"], 1),
    ("2JN", "John", ["jn", "jhn", "jno"], 2),
    ("3JN", "John", ["jn", "jhn", "jno"], 3),
    ("JUD", "Jude", ["jud", "jd"], None),
    ("REV", "Revelation",
     ["rev", "rv", "re", "revelations", "the apocalypse", "apocalypse"], None),
]


# Spoken-form ordinal-prefix variants generated for each ordinal book.
_ORDINAL_PREFIXES: dict[int, list[str]] = {
    1: ["first", "1st", "1", "i", "one"],
    2: ["second", "2nd", "2", "ii", "two"],
    3: ["third", "3rd", "3", "iii", "three"],
}

# Bare books that can appear without an ordinal but are ambiguous; we
# resolve to the "1" version with reduced confidence.
_AMBIGUOUS_DEFAULTS: dict[str, str] = {
    "samuel": "1SA",
    "kings": "1KI",
    "chronicles": "1CH",
    "corinthians": "1CO",
    "thessalonians": "1TH",
    "timothy": "1TI",
    "peter": "1PE",
    # NB: bare "John" -> JHN (gospel), not 1JN. Handled in _build_lexicon.
}


# Yoruba book names captured from the BMYO USFM running headers.
# These are static fallbacks so the parser works in tests / fresh installs
# before the YOR Bible has been ingested. Once ingested, the DB values
# take precedence (which lets future translations override these).
_YORUBA_BOOK_NAMES: dict[str, str] = {
    "GEN": "Gẹnẹsisi",       "EXO": "Eksodu",        "LEV": "Lefitiku",
    "NUM": "Numeri",         "DEU": "Deuteronomi",   "JOS": "Joṣua",
    "JDG": "Onidajọ",        "RUT": "Rutu",          "1SA": "1 Samuẹli",
    "2SA": "2 Samuẹli",      "1KI": "1 Ọba",         "2KI": "2 Ọba",
    "1CH": "1 Kronika",      "2CH": "2 Kronika",     "EZR": "Esra",
    "NEH": "Nehemiah",       "EST": "Esteri",        "JOB": "Jobu",
    "PSA": "Saamu",          "PRO": "Òwe",           "ECC": "Oniwaasu",
    "SNG": "Orin Solomoni",  "ISA": "Isaiah",        "JER": "Jeremiah",
    "LAM": "Ẹkún Jeremiah",  "EZK": "Esekiẹli",      "DAN": "Daniẹli",
    "HOS": "Hosea",          "JOL": "Joẹli",         "AMO": "Amosi",
    "OBA": "Obadiah",        "JON": "Jona",          "MIC": "Mika",
    "NAM": "Nahumu",         "HAB": "Habakuku",      "ZEP": "Sefaniah",
    "HAG": "Hagai",          "ZEC": "Sekariah",      "MAL": "Malaki",
    "MAT": "Matiu",          "MRK": "Marku",         "LUK": "Luku",
    "JHN": "Johanu",         "ACT": "Ìṣe àwọn Aposteli", "ROM": "Romu",
    "1CO": "1 Kọrinti",      "2CO": "2 Kọrinti",     "GAL": "Galatia",
    "EPH": "Efesu",          "PHP": "Filipi",        "COL": "Kolose",
    "1TH": "1 Tẹsalonika",   "2TH": "2 Tẹsalonika",  "1TI": "1 Timotiu",
    "2TI": "2 Timotiu",      "TIT": "Titu",          "PHM": "Filemoni",
    "HEB": "Heberu",         "JAS": "Jakọbu",        "1PE": "1 Peteru",
    "2PE": "2 Peteru",       "1JN": "1 Johanu",      "2JN": "2 Johanu",
    "3JN": "3 Johanu",       "JUD": "Juda",          "REV": "Ìfihàn",
}


@dataclass(frozen=True)
class BookMatch:
    book_code: str
    pattern: str         # the lowercased pattern that matched
    confidence: float    # 1.0 = unambiguous; lower for defaulted/ambiguous


def _generate_patterns_for_book(
    code: str, canonical: str, abbrevs: list[str], ordinal: int | None,
) -> list[str]:
    """All English patterns for one book entry."""
    base_forms = [canonical] + list(abbrevs)
    base_forms = [b.lower() for b in base_forms]
    if ordinal is None:
        return base_forms
    # Ordinal book: prepend each prefix to each base form.
    out: list[str] = []
    for prefix in _ORDINAL_PREFIXES[ordinal]:
        for base in base_forms:
            out.append(f"{prefix} {base}")
            # Also allow no-space digit form: "1john", "2cor"
            if prefix in {"1", "2", "3"}:
                out.append(f"{prefix}{base}")
    return out


@lru_cache(maxsize=1)
def _build_lexicon() -> tuple[tuple[str, BookMatch], ...]:
    """Build and cache the full pattern table.

    Returns tuple of (pattern, BookMatch) sorted by pattern length descending.
    Includes English variants + Yoruba names from the DB.
    """
    entries: list[tuple[str, BookMatch]] = []

    for code, canonical, abbrevs, ordinal in _BOOK_DATA:
        for p in _generate_patterns_for_book(code, canonical, abbrevs, ordinal):
            entries.append((p, BookMatch(code, p, 1.0)))

    # Ambiguous bare names default to "1" version, lower confidence
    for pattern, default_code in _AMBIGUOUS_DEFAULTS.items():
        entries.append((pattern, BookMatch(default_code, pattern, 0.6)))

    # Yoruba book names. Static fallback first so the parser works without
    # any DB; if the YOR translation has been ingested, those values
    # override (they could differ for future translations).
    yoruba_map: dict[str, str] = dict(_YORUBA_BOOK_NAMES)
    try:
        conn = connect()
        try:
            rows = conn.execute(
                "SELECT code, name_yo FROM books WHERE name_yo IS NOT NULL"
            ).fetchall()
            for r in rows:
                yoruba_map[r["code"]] = r["name_yo"].strip()
        finally:
            conn.close()
    except Exception:
        # DB not initialised; static names alone are fine.
        pass

    for code, name_yo in yoruba_map.items():
        entries.append((name_yo.lower(), BookMatch(code, name_yo.lower(), 1.0)))
        stripped = _strip_diacritics(name_yo).lower()
        if stripped != name_yo.lower():
            entries.append((stripped, BookMatch(code, stripped, 0.9)))

    # De-duplicate while keeping longest-pattern entries first
    seen: set[tuple[str, str]] = set()
    unique: list[tuple[str, BookMatch]] = []
    for pat, m in entries:
        key = (pat, m.book_code)
        if key in seen:
            continue
        seen.add(key)
        unique.append((pat, m))

    unique.sort(key=lambda e: (-len(e[0]), e[0]))
    return tuple(unique)


def _strip_diacritics(s: str) -> str:
    """Crude ASCII-fold for forgiveness on STT-mangled Yoruba."""
    import unicodedata
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def find_book_in_text(text: str) -> tuple[BookMatch, int, int] | None:
    """Find the longest book pattern matching anywhere in `text`.

    Returns (BookMatch, start_idx, end_idx) on hit, None otherwise.
    `text` is expected to already be lowercased.
    """
    lex = _build_lexicon()
    for pattern, match in lex:
        # Word-boundary match. We use a regex with explicit \b plus
        # protection against partial book-name matches inside other words.
        rx = re.compile(rf"(?<![a-z]){re.escape(pattern)}(?![a-z])")
        m = rx.search(text)
        if m:
            return match, m.start(), m.end()
    return None


def reset_cache() -> None:
    """For tests that change the DB between runs."""
    _build_lexicon.cache_clear()
