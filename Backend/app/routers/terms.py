from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..dependencies import CurrentUser, get_current_user
from ..models import TermsAcceptance, TermsVersion
from ..schemas import AcceptanceResponse, AcceptTermsRequest, TermsResponse

router = APIRouter(prefix="/terms", tags=["terms"])


@router.get("/current", response_model=TermsResponse)
def current_terms(current: Annotated[CurrentUser, Depends(get_current_user)], db: Annotated[Session, Depends(get_db)]):
    terms = db.scalar(select(TermsVersion).where(TermsVersion.is_current.is_(True)))
    if terms is None:
        raise HTTPException(status_code=404, detail="terms have not been configured")
    accepted = db.scalar(select(TermsAcceptance).where(TermsAcceptance.user_id == current.user.id, TermsAcceptance.terms_version_id == terms.id))
    return TermsResponse(version=terms.version, content=terms.content, content_sha256=terms.content_sha256, is_accepted=accepted is not None)


@router.get("/acceptance", response_model=AcceptanceResponse | None)
def acceptance(current: Annotated[CurrentUser, Depends(get_current_user)], db: Annotated[Session, Depends(get_db)]):
    terms = db.scalar(select(TermsVersion).where(TermsVersion.is_current.is_(True)))
    if terms is None:
        raise HTTPException(status_code=404, detail="terms have not been configured")
    accepted = db.scalar(select(TermsAcceptance).where(TermsAcceptance.user_id == current.user.id, TermsAcceptance.terms_version_id == terms.id))
    if accepted is None:
        return None
    return AcceptanceResponse(accepted=True, version=terms.version, accepted_at=accepted.accepted_at)


@router.post("/acceptance", response_model=AcceptanceResponse, status_code=201)
def accept_terms(payload: AcceptTermsRequest, current: Annotated[CurrentUser, Depends(get_current_user)], db: Annotated[Session, Depends(get_db)]):
    terms = db.scalar(select(TermsVersion).where(TermsVersion.is_current.is_(True)).with_for_update())
    if terms is None:
        raise HTTPException(status_code=404, detail="terms have not been configured")
    if payload.version != terms.version or payload.content_sha256 != terms.content_sha256:
        raise HTTPException(status_code=409, detail="terms changed; fetch and review the current version")
    accepted = db.scalar(select(TermsAcceptance).where(TermsAcceptance.user_id == current.user.id, TermsAcceptance.terms_version_id == terms.id))
    if accepted is None:
        accepted = TermsAcceptance(user_id=current.user.id, terms_version_id=terms.id, content_sha256=terms.content_sha256)
        db.add(accepted)
        db.commit()
        db.refresh(accepted)
    return AcceptanceResponse(accepted=True, version=terms.version, accepted_at=accepted.accepted_at)
