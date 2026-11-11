"""Password hashing, session tokens, CSRF, and login rate limiting.

Design notes (ARCHITECTURE.md §11, ADR-021):

* Passwords are hashed with Argon2id. A plaintext password never leaves this
  module and is never logged.
* The session cookie holds an opaque random token. We store only its SHA-256
  hash, so a dump of the ``sessions`` collection cannot be replayed as a login.
* Login failures are uniform and similar-cost: a missing user still pays for a
  dummy hash verification, so response timing does not reveal who has an account.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass, field

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# Argon2id with argon2-cffi's defaults, which track RFC 9106 guidance.
_hasher = PasswordHasher()

# Burned when the email does not exist, so "unknown email" and "wrong password"
# are indistinguishable from outside.
_DUMMY_HASH = _hasher.hash("timing-equalisation-placeholder")

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 1024

SESSION_COOKIE = "biostruct_session"
CSRF_COOKIE = "biostruct_csrf"
CSRF_HEADER = "x-csrf-token"

# A deliberately small, high-signal list. A real deployment should check a full
# breach corpus; this catches what people actually try first.
_COMMON_PASSWORDS = frozenset(
    {
        "password",
        "password1",
        "password123",
        "passw0rd1234",
        "123456789012",
        "qwertyuiop",
        "qwerty123456",
        "letmein12345",
        "iloveyou1234",
        "administrator",
        "welcome12345",
        "biostruct123",
        "proteindesign",
    }
)


# --- passwords -------------------------------------------------------------


def hash_password(password: str) -> str:
    return str(_hasher.hash(password))


def verify_password(password: str, password_hash: str | None) -> bool:
    """Verify a password, spending similar time whether or not the user exists."""
    target = password_hash if password_hash is not None else _DUMMY_HASH
    try:
        _hasher.verify(target, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    # Never report success down the dummy path.
    return password_hash is not None


def needs_rehash(password_hash: str) -> bool:
    """True when Argon2 parameters have moved on; upgrade the hash on next login."""
    try:
        return bool(_hasher.check_needs_rehash(password_hash))
    except InvalidHashError:
        return False


def validate_password_strength(password: str, *, email: str = "", name: str = "") -> str | None:
    """Return a human-readable reason the password is unacceptable, or None if it is fine.

    Length first, then obvious reuse of the person's own identifiers, then the
    common-password list. We deliberately do not demand character classes:
    length and non-obviousness matter far more, and class rules push people
    towards ``Password1!``.
    """
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Use at least {MIN_PASSWORD_LENGTH} characters."
    if len(password) > MAX_PASSWORD_LENGTH:
        return f"Use at most {MAX_PASSWORD_LENGTH} characters."
    lowered = password.lower()
    if lowered in _COMMON_PASSWORDS:
        return "That password is too common. Choose something less guessable."
    local_part = email.split("@", 1)[0].lower()
    if local_part and len(local_part) >= 4 and local_part in lowered:
        return "Do not include your email address in your password."
    if name and len(name) >= 4 and name.lower() in lowered:
        return "Do not include your name in your password."
    if len(set(password)) <= 4:
        return "That password repeats too few distinct characters."
    return None


# --- session tokens --------------------------------------------------------


def new_session_token() -> str:
    """A 256-bit URL-safe token. This value goes in the cookie and is never stored."""
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    """What we actually persist. SHA-256 is right here: the token is already
    high-entropy, so there is nothing to brute force and we want it fast."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# --- CSRF ------------------------------------------------------------------


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_tokens_match(cookie_value: str | None, header_value: str | None) -> bool:
    """Double-submit cookie check, compared in constant time."""
    if not cookie_value or not header_value:
        return False
    return hmac.compare_digest(cookie_value, header_value)


# --- login rate limiting ---------------------------------------------------


@dataclass
class _Bucket:
    hits: list[float] = field(default_factory=list)


class RateLimiter:
    """A small in-process sliding-window limiter.

    In-process is honest about what it is: it bounds a single API instance. With
    several instances the effective limit multiplies, so the per-instance limit
    is set conservatively. The authoritative backstop against credential
    stuffing is the Argon2 cost, not this.

    Scoped per key, where a key is an email or an IP, so one noisy network
    cannot lock out an individual researcher and vice versa.
    """

    def __init__(self, *, max_hits: int, window_seconds: float) -> None:
        self.max_hits = max_hits
        self.window_seconds = window_seconds
        self._buckets: dict[str, _Bucket] = {}

    def _prune(self, bucket: _Bucket, now: float) -> None:
        cutoff = now - self.window_seconds
        bucket.hits = [t for t in bucket.hits if t > cutoff]

    def check(self, key: str) -> int | None:
        """Return seconds to wait if the key is limited, else None (and record the hit)."""
        now = time.monotonic()
        bucket = self._buckets.setdefault(key, _Bucket())
        self._prune(bucket, now)
        if len(bucket.hits) >= self.max_hits:
            oldest = min(bucket.hits)
            return max(1, int(self.window_seconds - (now - oldest)) + 1)
        bucket.hits.append(now)
        return None

    def reset(self, key: str) -> None:
        """Called on a successful login so a legitimate user is not punished for typos."""
        self._buckets.pop(key, None)

    def clear(self) -> None:
        self._buckets.clear()


# 10 attempts per email and 30 per IP, each per 15 minutes. Generous enough for
# a person fumbling a password, far too slow for guessing one.
login_limiter_by_email = RateLimiter(max_hits=10, window_seconds=900)
login_limiter_by_ip = RateLimiter(max_hits=30, window_seconds=900)
register_limiter_by_ip = RateLimiter(max_hits=10, window_seconds=3600)
