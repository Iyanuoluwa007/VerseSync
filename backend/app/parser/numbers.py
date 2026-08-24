"""English number-word parser.

Bible references max out at chapter 150 (Psalms) and verse 176 (Ps 119),
so we only need three-digit numbers. We convert spoken phrases like
"twenty-three" to 23 and "one hundred and fifty" to 150, and -- crucially --
correctly split adjacent independent numbers like "three sixteen" into
[3, 16] rather than collapsing them into 316.

We keep the rules explicit:

* ONES (0..9) and TEENS (10..19) are terminals -- if the next token is
  another ONES or TEEN, that's the start of a new number.
* TENS (20..90) can absorb one ONES word: "twenty three" -> 23.
* HUNDRED multiplies whatever's accumulated and accepts more digits
  after, optionally with "and": "one hundred and fifty" -> 150.

Yoruba spoken numbers are not handled here -- they fall through to the
LLM fallback, which Llama 3.3 understands well enough for our purposes.
"""
from __future__ import annotations

import re

ONES: dict[str, int] = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
}
TEENS: dict[str, int] = {
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19,
}
TENS: dict[str, int] = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
MULTIPLIERS: dict[str, int] = {"hundred": 100, "thousand": 1000}

# "and" as in "one hundred AND fifty" is filler we tolerate inside a phrase.
FILLER_WORDS = {"and"}

ALL_NUMBER_WORDS = set(ONES) | set(TEENS) | set(TENS) | set(MULTIPLIERS)

_TOKEN_RX = re.compile(r"[A-Za-zÀ-ÿ\u0100-\u024f\u1e00-\u1eff]+|\d+|[^\s\w]")


def _can_extend(phrase: list[str], next_tok: str) -> bool:
    """Decide whether next_tok continues the current number phrase."""
    if next_tok in FILLER_WORDS:
        return True  # "and" is permissive

    if not phrase:
        return next_tok in ALL_NUMBER_WORDS or next_tok.isdigit()

    # Look at the last *non-filler* token; "and" alone shouldn't break the chain.
    last = next((t for t in reversed(phrase) if t not in FILLER_WORDS), None)
    if last is None:
        return next_tok in ALL_NUMBER_WORDS or next_tok.isdigit()

    # Hundred/thousand can absorb anything number-like that follows.
    if last in MULTIPLIERS:
        return next_tok in ALL_NUMBER_WORDS or next_tok.isdigit()
    # "twenty" + "three" works; "twenty" + "twenty" does not.
    if last in TENS:
        return next_tok in ONES
    # Ones/teens can accept hundred/thousand multipliers, nothing else.
    if last in ONES or last in TEENS:
        return next_tok in MULTIPLIERS
    # Bare digits: don't try to greedily merge with adjacent words.
    return False


def _evaluate(phrase: list[str]) -> int | None:
    """Resolve a list of tokens to a single integer."""
    if not phrase:
        return None
    total = 0
    current = 0
    for tok in phrase:
        if tok in FILLER_WORDS:
            continue
        if tok in ONES:
            current += ONES[tok]
        elif tok in TEENS:
            current += TEENS[tok]
        elif tok in TENS:
            current += TENS[tok]
        elif tok in MULTIPLIERS:
            mult = MULTIPLIERS[tok]
            current = (current or 1) * mult
            total += current
            current = 0
        elif tok.isdigit():
            current += int(tok)
        else:
            return None
    return total + current


def parse_number(text: str) -> int | None:
    """Parse a single spoken or written number. Returns None on failure."""
    text = text.strip().lower().replace("-", " ")
    if not text:
        return None
    if text.isdigit():
        return int(text)
    tokens = [t for t in text.split() if t]
    if not tokens:
        return None
    return _evaluate(tokens)


def words_to_digits(text: str) -> str:
    """Replace English number-word phrases in text with digit equivalents.

    Non-number tokens pass through untouched. Hyphens between number words
    are normalised to spaces ("twenty-three" behaves like "twenty three").
    """
    # Normalise hyphens between letters so "twenty-three" splits naturally.
    text = re.sub(r"(?<=[a-zA-Z])-(?=[a-zA-Z])", " ", text)
    # Lower-case for matching, but preserve the original surface for
    # non-number tokens by re-tokenising the original text and lowering
    # only when checking against our dictionaries.
    raw_tokens = _TOKEN_RX.findall(text)

    out: list[str] = []
    i = 0
    n = len(raw_tokens)
    while i < n:
        tok = raw_tokens[i]
        low = tok.lower()
        if low in ALL_NUMBER_WORDS or tok.isdigit():
            phrase = [low if not tok.isdigit() else tok]
            j = i + 1
            # Greedily extend, allowing "and" as filler
            while j < n:
                nxt = raw_tokens[j]
                nxt_low = nxt.lower()
                if _can_extend(phrase, nxt_low):
                    phrase.append(nxt_low if not nxt.isdigit() else nxt)
                    j += 1
                else:
                    break
            # Strip trailing "and" if present
            while phrase and phrase[-1] in FILLER_WORDS:
                phrase.pop()
                j -= 1
            value = _evaluate(phrase)
            if value is not None:
                out.append(str(value))
                i = j
                continue
        out.append(tok)
        i += 1

    # Reassemble with single spaces (caller can re-tokenise as needed).
    # We avoid keeping original whitespace because downstream parsers
    # tokenise anyway.
    return " ".join(out)
