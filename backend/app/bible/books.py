"""Canonical 66-book Protestant Bible list.

Books are identified by their 3-letter USFM code (the SIL standard used by
every modern Bible distribution). Codes are stable across translations,
which is what makes cross-translation lookup work.

Apocryphal/deuterocanonical books are intentionally excluded for Phase 0;
they don't appear in the typical preaching references we need to detect.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Book:
    code: str       # USFM 3-letter code, e.g. "GEN", "1CO"
    ord: int        # canonical order, 1..66
    name_en: str    # English name
    testament: str  # "OT" or "NT"


BOOKS: tuple[Book, ...] = (
    # Old Testament
    Book("GEN", 1, "Genesis", "OT"),
    Book("EXO", 2, "Exodus", "OT"),
    Book("LEV", 3, "Leviticus", "OT"),
    Book("NUM", 4, "Numbers", "OT"),
    Book("DEU", 5, "Deuteronomy", "OT"),
    Book("JOS", 6, "Joshua", "OT"),
    Book("JDG", 7, "Judges", "OT"),
    Book("RUT", 8, "Ruth", "OT"),
    Book("1SA", 9, "1 Samuel", "OT"),
    Book("2SA", 10, "2 Samuel", "OT"),
    Book("1KI", 11, "1 Kings", "OT"),
    Book("2KI", 12, "2 Kings", "OT"),
    Book("1CH", 13, "1 Chronicles", "OT"),
    Book("2CH", 14, "2 Chronicles", "OT"),
    Book("EZR", 15, "Ezra", "OT"),
    Book("NEH", 16, "Nehemiah", "OT"),
    Book("EST", 17, "Esther", "OT"),
    Book("JOB", 18, "Job", "OT"),
    Book("PSA", 19, "Psalms", "OT"),
    Book("PRO", 20, "Proverbs", "OT"),
    Book("ECC", 21, "Ecclesiastes", "OT"),
    Book("SNG", 22, "Song of Solomon", "OT"),
    Book("ISA", 23, "Isaiah", "OT"),
    Book("JER", 24, "Jeremiah", "OT"),
    Book("LAM", 25, "Lamentations", "OT"),
    Book("EZK", 26, "Ezekiel", "OT"),
    Book("DAN", 27, "Daniel", "OT"),
    Book("HOS", 28, "Hosea", "OT"),
    Book("JOL", 29, "Joel", "OT"),
    Book("AMO", 30, "Amos", "OT"),
    Book("OBA", 31, "Obadiah", "OT"),
    Book("JON", 32, "Jonah", "OT"),
    Book("MIC", 33, "Micah", "OT"),
    Book("NAM", 34, "Nahum", "OT"),
    Book("HAB", 35, "Habakkuk", "OT"),
    Book("ZEP", 36, "Zephaniah", "OT"),
    Book("HAG", 37, "Haggai", "OT"),
    Book("ZEC", 38, "Zechariah", "OT"),
    Book("MAL", 39, "Malachi", "OT"),
    # New Testament
    Book("MAT", 40, "Matthew", "NT"),
    Book("MRK", 41, "Mark", "NT"),
    Book("LUK", 42, "Luke", "NT"),
    Book("JHN", 43, "John", "NT"),
    Book("ACT", 44, "Acts", "NT"),
    Book("ROM", 45, "Romans", "NT"),
    Book("1CO", 46, "1 Corinthians", "NT"),
    Book("2CO", 47, "2 Corinthians", "NT"),
    Book("GAL", 48, "Galatians", "NT"),
    Book("EPH", 49, "Ephesians", "NT"),
    Book("PHP", 50, "Philippians", "NT"),
    Book("COL", 51, "Colossians", "NT"),
    Book("1TH", 52, "1 Thessalonians", "NT"),
    Book("2TH", 53, "2 Thessalonians", "NT"),
    Book("1TI", 54, "1 Timothy", "NT"),
    Book("2TI", 55, "2 Timothy", "NT"),
    Book("TIT", 56, "Titus", "NT"),
    Book("PHM", 57, "Philemon", "NT"),
    Book("HEB", 58, "Hebrews", "NT"),
    Book("JAS", 59, "James", "NT"),
    Book("1PE", 60, "1 Peter", "NT"),
    Book("2PE", 61, "2 Peter", "NT"),
    Book("1JN", 62, "1 John", "NT"),
    Book("2JN", 63, "2 John", "NT"),
    Book("3JN", 64, "3 John", "NT"),
    Book("JUD", 65, "Jude", "NT"),
    Book("REV", 66, "Revelation", "NT"),
)

assert len(BOOKS) == 66, "Protestant canon must have exactly 66 books"

BY_CODE: dict[str, Book] = {b.code: b for b in BOOKS}


# Canonical chapter counts per book.  Used by the parser to reject
# obviously-invalid references like "MAT 255:1" (Whisper-induced).
# Values from the Hebrew/Greek source tradition; consistent across all
# Protestant translations including KJV/WEB/NIV/ESV/NLT.
BOOK_MAX_CHAPTERS: dict[str, int] = {
    # Old Testament
    "GEN": 50, "EXO": 40, "LEV": 27, "NUM": 36, "DEU": 34,
    "JOS": 24, "JDG": 21, "RUT": 4,  "1SA": 31, "2SA": 24,
    "1KI": 22, "2KI": 25, "1CH": 29, "2CH": 36, "EZR": 10,
    "NEH": 13, "EST": 10, "JOB": 42, "PSA": 150, "PRO": 31,
    "ECC": 12, "SNG": 8,  "ISA": 66, "JER": 52, "LAM": 5,
    "EZK": 48, "DAN": 12, "HOS": 14, "JOL": 3,  "AMO": 9,
    "OBA": 1,  "JON": 4,  "MIC": 7,  "NAM": 3,  "HAB": 3,
    "ZEP": 3,  "HAG": 2,  "ZEC": 14, "MAL": 4,
    # New Testament
    "MAT": 28, "MRK": 16, "LUK": 24, "JHN": 21, "ACT": 28,
    "ROM": 16, "1CO": 16, "2CO": 13, "GAL": 6,  "EPH": 6,
    "PHP": 4,  "COL": 4,  "1TH": 5,  "2TH": 3,  "1TI": 6,
    "2TI": 4,  "TIT": 3,  "PHM": 1,  "HEB": 13, "JAS": 5,
    "1PE": 5,  "2PE": 3,  "1JN": 5,  "2JN": 1,  "3JN": 1,
    "JUD": 1,  "REV": 22,
}
assert set(BOOK_MAX_CHAPTERS.keys()) == {b.code for b in BOOKS}, \
    "BOOK_MAX_CHAPTERS missing/extra entries"


def is_valid_chapter(book: str, chapter: int) -> bool:
    """True if `book` is a known USFM code AND chapter is in 1..max."""
    max_ch = BOOK_MAX_CHAPTERS.get(book)
    if max_ch is None:
        return False
    return 1 <= chapter <= max_ch

