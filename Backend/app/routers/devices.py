from typing import Annotated
from uuid import UUID
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..db import get_db
from ..dependencies import CurrentUser, require_terms
from ..models import Device
from ..schemas import DeviceRequest, DeviceResponse
from ..security import encrypt_device_token

router = APIRouter(prefix="/devices", tags=["devices"])


@router.post("/fcm-token", response_model=DeviceResponse)
def register_token(payload: DeviceRequest, current: Annotated[CurrentUser, Depends(require_terms)], db: Annotated[Session, Depends(get_db)]):
    token_hash, token_ciphertext = encrypt_device_token(payload.token)
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": int(token_hash[:15], 16)})
    device = db.scalar(select(Device).where(Device.token_hash == token_hash).with_for_update())
    if device is None:
        device = Device(user_id=current.user.id, token_hash=token_hash, token_ciphertext=token_ciphertext, platform=payload.platform)
        db.add(device)
    else:
        # A shared phone signing into a new account must stop receiving old-user pushes.
        device.user_id = current.user.id
        device.token_ciphertext = token_ciphertext
        device.is_active = True
    device.last_seen_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(device)
    return device


@router.patch("/fcm-token/{device_id}", response_model=DeviceResponse)
def rotate_token(device_id: UUID, payload: DeviceRequest, current: Annotated[CurrentUser, Depends(require_terms)], db: Annotated[Session, Depends(get_db)]):
    token_hash, token_ciphertext = encrypt_device_token(payload.token)
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": int(token_hash[:15], 16)})
    device = db.scalar(select(Device).where(Device.id == device_id, Device.user_id == current.user.id).with_for_update())
    if device is None:
        raise HTTPException(status_code=404, detail="device not found")
    existing = db.scalar(select(Device).where(Device.token_hash == token_hash))
    if existing is not None and existing.id != device.id:
        raise HTTPException(status_code=409, detail="token already registered; register the current token instead")
    device.token_hash = token_hash
    device.token_ciphertext = token_ciphertext
    device.last_seen_at = datetime.now(timezone.utc)
    device.is_active = True
    db.commit()
    db.refresh(device)
    return device


@router.delete("/fcm-token/{device_id}", status_code=204)
def delete_token(device_id: UUID, current: Annotated[CurrentUser, Depends(require_terms)], db: Annotated[Session, Depends(get_db)]):
    device = db.scalar(select(Device).where(Device.id == device_id, Device.user_id == current.user.id))
    if device is not None:
        device.is_active = False
        db.commit()
