import hashlib
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.fernet import Fernet

from .config import get_settings

password_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("password must contain at least 12 characters")
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def encrypt_device_token(token: str) -> tuple[str, str]:
    settings = get_settings()
    if not settings.token_encryption_key:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY is required to register a device")
    encrypted = Fernet(settings.token_encryption_key.encode("utf-8")).encrypt(token.encode("utf-8")).decode("utf-8")
    return _hash_token(token), encrypted


def decrypt_device_token(ciphertext: str) -> str:
    settings = get_settings()
    settings.validate_auth_secrets()
    return Fernet(settings.token_encryption_key.encode("utf-8")).decrypt(ciphertext.encode("utf-8")).decode("utf-8")


def create_access_token(user_id: str, role: str, session_id: str) -> tuple[str, datetime]:
    settings = get_settings()
    settings.validate_auth_secrets()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_ttl_minutes)
    payload = {"sub": user_id, "sid": session_id, "role": role, "typ": "access", "jti": secrets.token_hex(16), "iat": datetime.now(timezone.utc), "exp": expires_at, "iss": "crashpulse", "aud": "crashpulse-api"}
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256"), expires_at


def decode_access_token(token: str) -> dict:
    settings = get_settings()
    settings.validate_auth_secrets()
    payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"], issuer="crashpulse", audience="crashpulse-api", options={"require": ["sub", "sid", "exp", "iat", "jti"]})
    if payload.get("typ") != "access":
        raise jwt.InvalidTokenError("invalid access token")
    return payload


def create_refresh_token() -> tuple[str, str, datetime]:
    settings = get_settings()
    settings.validate_auth_secrets()
    token = secrets.token_urlsafe(48)
    expires_at = datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_ttl_days)
    return token, _hash_token(token), expires_at


class LoginRateLimiter:
    """Small-process limiter for the local demo; use a shared store in deployment."""

    def __init__(self, attempts: int, window_seconds: int):
        self.attempts = attempts
        self.window_seconds = window_seconds
        self._events: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            self._events = {item: events for item, events in self._events.items() if events and now - events[-1] < self.window_seconds}
            if key not in self._events and len(self._events) >= 10000:
                return False
            events = [value for value in self._events.get(key, []) if now - value < self.window_seconds]
            if len(events) >= self.attempts:
                self._events[key] = events
                return False
            events.append(now)
            self._events[key] = events
            return True
