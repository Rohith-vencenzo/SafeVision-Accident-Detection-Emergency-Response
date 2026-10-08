import uuid
from unittest.mock import MagicMock
from datetime import datetime, timedelta, timezone

import pytest

from app.security import LoginRateLimiter, hash_password, verify_password
from app.config import get_settings
from cryptography.fernet import Fernet


@pytest.fixture
def auth_settings(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "unit-test-only-" + "a" * 40)
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_argon2_password_hash_is_not_plaintext():
    password = "a-long-local-test-password"
    digest = hash_password(password)
    assert digest != password
    assert digest.startswith("$argon2")
    assert verify_password(password, digest)
    assert not verify_password("wrong-password", digest)


def test_short_password_rejected():
    with pytest.raises(ValueError):
        hash_password("too-short")


def test_login_rate_limiter_blocks_after_limit():
    limiter = LoginRateLimiter(attempts=2, window_seconds=60)
    assert limiter.allow("local")
    assert limiter.allow("local")
    assert not limiter.allow("local")
    assert limiter.allow("different")


def test_device_tokens_are_encrypted(auth_settings):
    from app.security import encrypt_device_token
    token = "ephemeral-unit-test-fcm-token"
    digest, ciphertext = encrypt_device_token(token)
    assert token not in ciphertext
    assert len(digest) == 64
    assert Fernet(get_settings().token_encryption_key.encode()).decrypt(ciphertext.encode()).decode() == token


def test_revoked_access_session_is_rejected(auth_settings):
    from fastapi import HTTPException
    from fastapi.security import HTTPAuthorizationCredentials
    from app.dependencies import get_current_user
    from app.security import create_access_token
    user_id, session_id = uuid.uuid4(), uuid.uuid4()
    token, _ = create_access_token(str(user_id), "USER", str(session_id))
    user = MagicMock(id=user_id, is_active=True)
    session = MagicMock(user_id=user_id, revoked_at=datetime.now(timezone.utc))
    db = MagicMock()
    db.get.side_effect = [user, session]
    with pytest.raises(HTTPException) as result:
        get_current_user(HTTPAuthorizationCredentials(scheme="Bearer", credentials=token), db)
    assert result.value.status_code == 401


def test_access_tokens_require_signature_expiry_and_correct_audience(auth_settings):
    import jwt
    from app.security import create_access_token, decode_access_token
    token, _ = create_access_token(str(uuid.uuid4()), "USER", str(uuid.uuid4()))
    assert decode_access_token(token)["typ"] == "access"
    claims = jwt.decode(token, options={"verify_signature": False})
    claims["exp"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(jwt.encode(claims, get_settings().jwt_secret, algorithm="HS256"))
    claims["exp"] = datetime.now(timezone.utc) + timedelta(minutes=1)
    claims["aud"] = "different-service"
    with pytest.raises(jwt.InvalidAudienceError):
        decode_access_token(jwt.encode(claims, get_settings().jwt_secret, algorithm="HS256"))
