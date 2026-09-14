"""The owner login for the LAN listener (addendum §6, design §5.8). Standard library only.

Three rules are the reason this module exists, and every function below is shaped by one of
them:

1. **The loop never creates, copies or reads secret material** (D7). The three LAN files are
   the user's: the password hash they place themselves with `harness owner-password-hash`, and
   the two TLS files they generate on the host. Nothing here writes a file, and `lan_active`
   decides whether the feature exists from file *metadata* -- `is_file()` and a non-zero size,
   never `exists()` and never the contents.
2. **The session key is derived, never stored.** It is
   `sha256(b"sports-lan-session:" + <the hash line's bytes>)`, so there is no key file to leak
   and changing the password revokes every outstanding session on its own.
3. **Every path fails closed.** A missing, empty, unreadable or malformed hash line means every
   login fails, exactly as `/unkill` fails on a missing token file. A hash line carrying scrypt
   parameters other than the pinned ones is an error, not a weaker password to check against.

Nothing in this module passes a password, a hash line, a key or a cookie value to `log`, to
`repr` or to an exception message; the messages below name a parameter or a shape and never a
value. `harness/logging_setup.py` is not touched (invariant 4).
"""

import base64
import binascii
import hashlib
import hmac
import logging
import secrets
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

#: Pinned scrypt cost parameters. A line claiming anything else is invalid (A-I6): accepting it
#: would let whoever wrote that line choose the cost factor that guards the write routes.
SCRYPT_N, SCRYPT_R, SCRYPT_P = 16384, 8, 1
SCRYPT_MAXMEM = 64 * 1024 * 1024
SCRYPT_DKLEN = 64
SALT_BYTES = 16
COOKIE_NAME = "sports_session"
MAX_AGE_DAYS = 180
#: `Max-Age` is only a hint to the browser; `read_cookie` re-checks the age server-side (A-I5).
COOKIE_MAX_AGE_S = MAX_AGE_DAYS * 24 * 3600
LOGIN_FAILURES_PER_MINUTE = 5
LOGIN_WINDOW_S = 60
#: How many client addresses the failure table may hold. A LAN has a handful; the cap is what
#: keeps a burst of spoofed sources from growing the dict without bound.
LOGIN_MAX_ADDRESSES = 256
#: A cookie issued further ahead than this is refused rather than trusted: the issue instant is
#: the server's own clock, so a future stamp means a forged or replayed value, not skew.
CLOCK_SKEW_S = 60


def hash_password(password: str) -> str:
    """One `scrypt$16384$8$1$<salt_b64>$<hash_b64>` line. Returned, never written."""
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
                            maxmem=SCRYPT_MAXMEM, dklen=SCRYPT_DKLEN)
    return (f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}$"
            f"{base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}")


def _parse_hash_line(line: str) -> tuple[bytes, bytes]:
    """The salt and digest of a pinned line, or `ValueError` naming the part that is wrong.

    Every message below names a field or a parameter; none of them carries the line, the salt,
    the digest or anything derived from them.
    """
    parts = line.strip().split("$")
    if len(parts) != 6 or parts[0] != "scrypt":
        raise ValueError("hash line is not a scrypt line of six $-separated fields")
    if (parts[1], parts[2], parts[3]) != (str(SCRYPT_N), str(SCRYPT_R), str(SCRYPT_P)):
        raise ValueError("hash line does not carry the pinned scrypt n/r/p")
    try:
        salt = base64.b64decode(parts[4], validate=True)
        digest = base64.b64decode(parts[5], validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("hash line's salt or digest is not base64") from None
    if len(salt) != SALT_BYTES or len(digest) != SCRYPT_DKLEN:
        raise ValueError("hash line's salt or digest is the wrong length")
    return salt, digest


def verify_password(password: str, line: str) -> bool:
    """Constant-time comparison against a line whose parameters are pinned.

    A line carrying other parameters raises `ValueError` rather than verifying against them: it
    is a hash someone else's tool wrote, and accepting it would let a weaker cost factor decide
    who reaches the write routes. The exception message names the parameter, never the line.
    """
    salt, expected = _parse_hash_line(line)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
                            maxmem=SCRYPT_MAXMEM, dklen=SCRYPT_DKLEN)
    return hmac.compare_digest(digest, expected)


def session_key(hash_line: str) -> bytes:
    """The HMAC key for the session cookie, derived from the hash line itself.

    No key file exists, so none can leak; and a new password is a new key, which is what makes
    `harness owner-password-hash` plus a restart a full session revocation (§6).
    """
    return hashlib.sha256(b"sports-lan-session:" + hash_line.strip().encode()).digest()


def issue_cookie(key: bytes, now: datetime) -> str:
    """`<issued_unix>.<hmac_sha256_hex>`. The instant is in the clear on purpose: it is what
    `read_cookie` checks the age against, and it is signed, so it cannot be moved."""
    issued = str(int(now.timestamp()))
    return f"{issued}.{hmac.new(key, issued.encode(), hashlib.sha256).hexdigest()}"


def read_cookie(key: bytes, value: str, now: datetime, max_age_days: int = MAX_AGE_DAYS) -> bool:
    """Signature first, then the server-side age (A-I5).

    The order matters: the age is read out of the cookie, so it means nothing until the
    signature says the cookie is ours. `Max-Age` on the `Set-Cookie` header is a hint to the
    browser and is never what expires a session here.
    """
    issued_text, _, signature = (value or "").partition(".")
    if not issued_text or not signature or not issued_text.isdigit():
        return False
    expected = hmac.new(key, issued_text.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return False
    issued = datetime.fromtimestamp(int(issued_text), tz=now.tzinfo)
    if issued > now + timedelta(seconds=CLOCK_SKEW_S):
        return False
    return now - issued <= timedelta(days=max_age_days)


def is_non_empty_file(path: Path) -> bool:
    """`is_file()` and a non-zero size, never `exists()` (A-I7). Compose materialises a missing
    bind source as an empty *directory*, so `exists()` would be True with nothing behind it."""
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def lan_active(settings) -> bool:
    """Whether the LAN listener has everything it needs: all three of the user's files present,
    regular and non-empty. Metadata only -- the contents are never read here (D7)."""
    return all(is_non_empty_file(Path(p)) for p in (settings.owner_password_hash_file,
                                                    settings.lan_tls_cert_file,
                                                    settings.lan_tls_key_file))


def read_hash_line(settings) -> str | None:
    """The owner's hash line, or `None` when there is no usable one -- which every caller must
    treat as "every login fails", not as "no password required" (fail closed).

    The line is returned to the caller and never logged; the warning below names the condition
    and nothing else.
    """
    path = Path(settings.owner_password_hash_file)
    if not is_non_empty_file(path):
        return None
    try:
        line = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        log.warning("owner password hash file is unreadable; every login fails")
        return None
    return line or None


class LoginLimiter:
    """Failed logins per client address: at most five a minute, in a bounded dict (§6).

    The address is the socket's (`request.client.host`), never a header: `X-Forwarded-For` is
    attacker-controlled on a listener with no proxy in front of it, so trusting it would hand
    out a fresh allowance per request. The table is capped so that a flood of source addresses
    cannot grow it without bound; when it is full the least recently active address is dropped,
    which at worst gives an attacker back the allowance they already spent.
    """

    def __init__(self, limit: int = LOGIN_FAILURES_PER_MINUTE, window_s: int = LOGIN_WINDOW_S,
                 max_addresses: int = LOGIN_MAX_ADDRESSES) -> None:
        self._limit = limit
        self._window = timedelta(seconds=window_s)
        self._max_addresses = max_addresses
        self._failures: dict[str, deque[datetime]] = {}

    def _prune(self, address: str, now: datetime) -> deque[datetime]:
        failures = self._failures.get(address)
        if failures is None:
            return deque()
        while failures and now - failures[0] > self._window:
            failures.popleft()
        if not failures:
            self._failures.pop(address, None)
        return failures

    def blocked(self, address: str, now: datetime) -> bool:
        return len(self._prune(address, now)) >= self._limit

    def record_failure(self, address: str, now: datetime) -> None:
        failures = self._prune(address, now)
        if address not in self._failures:
            if len(self._failures) >= self._max_addresses:
                self._evict(now)
            self._failures[address] = failures
        failures.append(now)

    def _evict(self, now: datetime) -> None:
        """Drop every address whose window has emptied; failing that, the least recently active
        one. Either way the dict is back under its cap before the next insert."""
        for address in list(self._failures):
            self._prune(address, now)
        while len(self._failures) >= self._max_addresses:
            oldest = min(self._failures, key=lambda a: self._failures[a][-1])
            self._failures.pop(oldest, None)
