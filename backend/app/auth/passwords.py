"""Admin PIN hashing.

The Phase 0 plan specified Argon2id. This uses **scrypt** from the
standard library instead, deliberately:

* `argon2-cffi` is a compiled dependency. VerseSync's base install is
  meant to stay small enough to run on a church laptop with no build
  tools, and the projector-only profile currently needs nothing compiled.
* scrypt (RFC 7914) is memory-hard, standardised, and has been in
  `hashlib` since Python 3.6. For this threat model it is equivalent in
  practice.
* The hash string records its own algorithm and parameters, so moving to
  Argon2id later is a verification-time branch, not a migration.

The honest caveat: **a numeric PIN is low-entropy no matter how it is
hashed.** A 6-digit PIN is a million possibilities; no KDF makes that
safe against an attacker holding the hash. The real defences are the
lockout in `service.py` and keeping the database off the network. The
KDF only buys time if the file leaks.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

# Tuned so a single verification costs roughly 100 ms on a modern laptop:
# slow enough to make online guessing pointless, fast enough that an
# operator unlocking the admin panel does not notice.
SCRYPT_N = 2 ** 15      # CPU/memory cost
SCRYPT_R = 8            # block size
SCRYPT_P = 1            # parallelisation
SCRYPT_DKLEN = 32
SALT_BYTES = 16

# scrypt needs roughly 128 * N * r bytes. At N=2^15, r=8 that is ~32 MB,
# comfortably under the interpreter's default maxmem once we ask for it.
_MAXMEM = 128 * SCRYPT_N * SCRYPT_R * 2

MIN_PIN_LENGTH = 6
MAX_PIN_LENGTH = 64


class PinPolicyError(ValueError):
    """The proposed PIN is not acceptable."""


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def validate_pin(pin: str) -> str:
    """Check a PIN against policy. Returns it stripped, or raises.

    Rejects the handful of PINs that show up in every breach corpus.
    This is not a substitute for the lockout, but there is no reason to
    let someone protect a live service with "123456".
    """
    if not isinstance(pin, str):
        raise PinPolicyError("PIN must be a string")
    pin = pin.strip()

    if len(pin) < MIN_PIN_LENGTH:
        raise PinPolicyError(
            f"PIN must be at least {MIN_PIN_LENGTH} characters."
        )
    if len(pin) > MAX_PIN_LENGTH:
        raise PinPolicyError(
            f"PIN must be at most {MAX_PIN_LENGTH} characters."
        )

    if pin.isdigit():
        if len(set(pin)) == 1:
            raise PinPolicyError("PIN cannot be a single repeated digit.")
        if pin in _SEQUENTIAL_RUNS:
            raise PinPolicyError("PIN cannot be a sequence of digits.")
        if pin in _COMMON_PINS:
            raise PinPolicyError(
                "That PIN appears in every list of common PINs. Choose another."
            )
    return pin


def _build_runs() -> frozenset[str]:
    """Ascending and descending digit runs of every allowed length."""
    runs: set[str] = set()
    digits = "0123456789" * 2          # wrap, so 890123 counts
    reverse = digits[::-1]
    for length in range(MIN_PIN_LENGTH, 21):
        for source in (digits, reverse):
            for start in range(len(source) - length + 1):
                runs.add(source[start:start + length])
    return frozenset(runs)


_SEQUENTIAL_RUNS = _build_runs()

_COMMON_PINS = frozenset({
    "123456", "654321", "111111", "000000", "121212", "112233",
    "123123", "696969", "159753", "147258", "142536", "abc123",
    "1234567", "12345678", "123456789", "1234567890",
})


def hash_pin(pin: str) -> str:
    """Hash a PIN. Returns a self-describing string safe to store.

    Format: ``scrypt$<n>$<r>$<p>$<salt_b64>$<hash_b64>``
    """
    pin = validate_pin(pin)
    salt = secrets.token_bytes(SALT_BYTES)
    derived = hashlib.scrypt(
        pin.encode("utf-8"), salt=salt,
        n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
        dklen=SCRYPT_DKLEN, maxmem=_MAXMEM,
    )
    return "$".join((
        "scrypt", str(SCRYPT_N), str(SCRYPT_R), str(SCRYPT_P),
        _b64e(salt), _b64e(derived),
    ))


def verify_pin(pin: str, stored: str) -> bool:
    """Constant-time check of a PIN against a stored hash.

    Returns False for any malformed or unknown-algorithm hash rather than
    raising, so a corrupted row locks the admin out instead of throwing a
    500 that leaks the parse error.
    """
    if not pin or not stored:
        return False

    parts = stored.split("$")
    if len(parts) != 6 or parts[0] != "scrypt":
        return False

    _, n_s, r_s, p_s, salt_s, hash_s = parts
    try:
        n, r, p = int(n_s), int(r_s), int(p_s)
        salt = _b64d(salt_s)
        expected = _b64d(hash_s)
    except (ValueError, TypeError):
        return False

    # Guard against a tampered row asking for an absurd memory cost.
    if not (2 <= n <= 2 ** 20) or not (1 <= r <= 64) or not (1 <= p <= 16):
        return False

    try:
        derived = hashlib.scrypt(
            pin.encode("utf-8"), salt=salt,
            n=n, r=r, p=p, dklen=len(expected),
            maxmem=128 * n * r * 2,
        )
    except (ValueError, MemoryError):
        return False

    return hmac.compare_digest(derived, expected)


def generate_signing_key() -> str:
    """A fresh 256-bit key for signing device tokens."""
    return _b64e(secrets.token_bytes(32))


def generate_approval_code() -> str:
    """A 6-digit code an operator can read aloud.

    Uses `secrets`, not `random`: this code is the only thing standing
    between a stranger on the LAN and an approved device.
    """
    return f"{secrets.randbelow(1_000_000):06d}"
