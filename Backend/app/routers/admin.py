from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import get_db
from ..dependencies import CurrentUser, require_admin
from ..models import User
from ..schemas import CreateUserRequest, UserResponse
from ..security import hash_password

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: CreateUserRequest,
    _: Annotated[CurrentUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    if db.scalar(select(User).where(User.login_id == payload.login_id)) is not None:
        raise HTTPException(status_code=409, detail="login ID already exists")
    user = User(login_id=payload.login_id, password_hash=hash_password(payload.password), role="USER", is_active=True)
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="login ID already exists") from None
    db.refresh(user)
    return user


@router.get("/users", response_model=list[UserResponse])
def list_users(
    _: Annotated[CurrentUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    return list(db.scalars(select(User).order_by(User.login_id).offset(offset).limit(limit)))


@router.post("/users/{user_id}/disable", response_model=UserResponse)
def disable_user(
    user_id: UUID,
    current: Annotated[CurrentUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    if current.user.id == user_id:
        raise HTTPException(status_code=400, detail="administrator cannot disable the current account")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    if user.role != "USER":
        raise HTTPException(status_code=400, detail="this endpoint only disables mobile accounts")
    user.is_active = False
    db.commit()
    db.refresh(user)
    return user
