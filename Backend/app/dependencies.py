from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .db import get_db
from .models import RefreshSession, TermsAcceptance, TermsVersion, User
from sqlalchemy import select
from .security import decode_access_token

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class CurrentUser:
    user: User
    claims: dict


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    db: Annotated[Session, Depends(get_db)],
) -> CurrentUser:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication required")
    try:
        claims = decode_access_token(credentials.credentials)
        user = db.get(User, UUID(claims["sub"]))
        session = db.get(RefreshSession, UUID(claims["sid"]))
    except (jwt.InvalidTokenError, ValueError, RuntimeError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or expired token") from None
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="account is inactive")
    if session is None or session.user_id != user.id or session.revoked_at is not None or session.expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="session expired or revoked")
    return CurrentUser(user=user, claims=claims)


def require_local_operator(request: Request) -> None:
    if request.client is None or request.client.host not in {"127.0.0.1", "::1"}:
        raise HTTPException(status_code=403, detail="administrator console is available on loopback only")


def require_admin(current: Annotated[CurrentUser, Depends(get_current_user)], _: Annotated[None, Depends(require_local_operator)]) -> CurrentUser:
    if current.user.role != "ADMIN":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="administrator role required")
    return current


def require_terms(current: Annotated[CurrentUser, Depends(get_current_user)], db: Annotated[Session, Depends(get_db)]) -> CurrentUser:
    terms = db.scalar(select(TermsVersion).where(TermsVersion.is_current.is_(True)))
    if terms is None or db.scalar(select(TermsAcceptance).where(TermsAcceptance.user_id == current.user.id, TermsAcceptance.terms_version_id == terms.id)) is None:
        raise HTTPException(status_code=403, detail="current terms acceptance required")
    return current
