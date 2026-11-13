"""Admin password hashing, browser sessions and login throttling."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from netmon.db import Database

SESSION_COOKIE = "netmon_session"
SESSION_TTL = 30 * 86400
PASSWORD_KEY = "admin_password_hash"

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R,
                            p=_SCRYPT_P, dklen=32)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        candidate = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n),
                                   r=int(r), p=int(p), dklen=len(digest) // 2)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate.hex(), digest)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Auth:
    def __init__(self, db: Database):
        self.db = db

    def is_configured(self) -> bool:
        return bool(self.db.get_setting(PASSWORD_KEY))

    def set_password(self, password: str) -> None:
        """Set the admin password and sign out every existing session."""
        self.db.set_setting(PASSWORD_KEY, hash_password(password))
        self.db.delete_all_sessions()

    def check_password(self, password: str) -> bool:
        stored = self.db.get_setting(PASSWORD_KEY)
        return bool(stored) and verify_password(password, stored)

    def create_session(self) -> str:
        token = secrets.token_urlsafe(32)
        self.db.add_session(_token_hash(token), time.time() + SESSION_TTL)
        return token

    def session_valid(self, token: str | None) -> bool:
        return bool(token) and self.db.session_valid(_token_hash(token))

    def end_session(self, token: str | None) -> None:
        if token:
            self.db.delete_session(_token_hash(token))


class LoginThrottle:
    """Lock a client out for a while after repeated wrong passwords."""

    MAX_FAILURES = 5
    LOCK_SECONDS = 60

    def __init__(self) -> None:
        self._failures: dict[str, tuple[int, float]] = {}

    def retry_after(self, client: str) -> int:
        count, locked_until = self._failures.get(client, (0, 0.0))
        remaining = locked_until - time.time()
        return int(remaining) + 1 if count >= self.MAX_FAILURES and remaining > 0 else 0

    def failed(self, client: str) -> None:
        now = time.time()
        count, locked_until = self._failures.get(client, (0, 0.0))
        if locked_until <= now:
            count = 0  # earlier failures are forgotten after LOCK_SECONDS
        self._failures[client] = (count + 1, now + self.LOCK_SECONDS)
        if len(self._failures) > 10000:
            self._failures = {k: v for k, v in self._failures.items() if v[1] > now}

    def succeeded(self, client: str) -> None:
        self._failures.pop(client, None)
