"""Parser data types.

ParsedRef is the canonical output of every parser stage. ParseContext
threads state across multiple parses for "next chapter" / "verses 9-10"
style follow-up references.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

# Where in the pipeline a parse came from. Useful for analytics and for
# the API to flag low-confidence results.
ParseSource = Literal[
    "regex_written",   # "John 3:16" style
    "regex_spoken",    # "John three sixteen" style (after number normalization)
    "context",         # resolved against last reference ("next chapter")
    "llm",             # Groq fallback
]


@dataclass(frozen=True)
class ParsedRef:
    book: str                    # USFM 3-letter, e.g. "JHN"
    chapter: int
    verse_start: int
    verse_end: Optional[int]     # None = single verse, else inclusive end
    source: ParseSource
    confidence: float            # 0.0..1.0

    def to_dict(self) -> dict:
        return {
            "book": self.book,
            "chapter": self.chapter,
            "verse_start": self.verse_start,
            "verse_end": self.verse_end,
            "source": self.source,
            "confidence": self.confidence,
        }

    def __str__(self) -> str:
        if self.verse_end and self.verse_end != self.verse_start:
            return f"{self.book} {self.chapter}:{self.verse_start}-{self.verse_end}"
        return f"{self.book} {self.chapter}:{self.verse_start}"


@dataclass
class ParseContext:
    """Last cited reference, threaded through stateful parsing.

    Mutable on purpose -- the parser's update() call rebinds it after
    every successful parse so follow-ups like "the next chapter" can
    resolve.
    """
    last_book: Optional[str] = None
    last_chapter: Optional[int] = None
    last_verse_end: Optional[int] = None

    def update(self, ref: ParsedRef) -> None:
        self.last_book = ref.book
        self.last_chapter = ref.chapter
        self.last_verse_end = ref.verse_end or ref.verse_start

    def to_dict(self) -> dict:
        return {
            "last_book": self.last_book,
            "last_chapter": self.last_chapter,
            "last_verse_end": self.last_verse_end,
        }
