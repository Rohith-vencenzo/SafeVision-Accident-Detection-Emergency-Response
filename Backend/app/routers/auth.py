import hashlib
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..dependencies import CurrentUser, get_current_user
from ..models import RefreshSession, User
from ..schemas import LoginRequest, RefreshRequest, TokenResponse, UserResponse
from ..security import LoginRateLimiter, create_access_token, create_refresh_token, hash_password, verify_password
from ..config import get_settings

router = APIRouter(prefix="/auth", tags=["auth"])
limiter = LoginRateLimiter(get_settings().login_rate_limit_attempts, get_settings().login_rate_limit_window_seconds)
dummy_hash = hash_password("not-a-usable-account-password")


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, request: Request, db: Annotated[Session, Depends(get_db)]):
    client_key = request.client.host if request.client else "unknown"
    if not limiter.allow(client_key):
        raise HTTPException(status_code=429, detail="too many login attempts; try again later")
    user = db.scalar(select(User).where(User.login_id == payload.login_id))
    valid = verify_password(payload.password, user.password_hash if user else dummy_hash)
    if user is None or not user.is_active or not valid:
        raise HTTPException(status_code=401, detail="invalid credentials")
    refresh, refresh_hash, refresh_expires = create_refresh_token()
    session_id = uuid.uuid4()
    access, expires_at = create_access_token(str(user.id), user.role, str(session_id))
    db.add(RefreshSession(id=session_id, user_id=user.id, token_hash=refresh_hash, expires_at=refresh_expires))
    db.commit()
    return TokenResponse(access_token=access, refresh_token=refresh, expires_at=expires_at)


@router.post("/refresh", response_model=TokenResponse)
def refresh(payload: RefreshRequest, db: Annotated[Session, Depends(get_db)]):
    token_hash = hashlib.sha256(payload.refresh_token.encode("utf-8")).hexdigest()
    session = db.scalar(select(RefreshSession).where(RefreshSession.token_hash == token_hash).with_for_update())
    now = datetime.now(timezone.utc)
    if session is None or session.revoked_at is not None or session.expires_at <= now:
        raise HTTPException(status_code=401, detail="invalid refresh token")
    user = db.get(User, session.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="account is inactive")
    session.revoked_at = now
    new_refresh, new_hash, new_expires = create_refresh_token()
    session_id = uuid.uuid4()
    access, expires_at = create_access_token(str(user.id), user.role, str(session_id))
    db.add(RefreshSession(id=session_id, user_id=user.id, token_hash=new_hash, expires_at=new_expires))
    db.commit()
    return TokenResponse(access_token=access, refresh_token=new_refresh, expires_at=expires_at)


@router.post("/logout", status_code=204)
def logout(payload: RefreshRequest, db: Annotated[Session, Depends(get_db)]):
    token_hash = hashlib.sha256(payload.refresh_token.encode("utf-8")).hexdigest()
    session = db.scalar(select(RefreshSession).where(RefreshSession.token_hash == token_hash))
    if session is not None and session.revoked_at is None:
        session.revoked_at = datetime.now(timezone.utc)
        db.commit()


@router.get("/me", response_model=UserResponse)
def me(current: Annotated[CurrentUser, Depends(get_current_user)]):
    return current.user
