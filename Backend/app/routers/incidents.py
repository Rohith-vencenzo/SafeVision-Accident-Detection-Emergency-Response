import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..dependencies import CurrentUser, require_terms
from ..models import Incident, UserAction
from ..schemas import ActionResponse, IncidentActionRequest, IncidentDetailResponse, IncidentResponse, LocationAttachmentRequest

router = APIRouter(prefix="/incidents", tags=["incidents"])

# Incidents are TEAM-WIDE. Reviewing an accident alert is a shared duty, so any
# signed-in user who has accepted the current Terms may read, review and act on
# any incident - not only the account that happened to trigger detection.
#
# `owner_id` is still recorded on every incident for provenance, and every
# Ignore / Mark reviewed / Proceeded / location action is still attributed to
# the individual who performed it. What changed is visibility: a second
# reviewer's phone receives the alert, so it must also be able to open it
# instead of receiving an alarm that resolves to 404.
#
# Authentication (bearer token) and the Terms gate are unchanged, so this is
# still strictly an internal, trusted-operator surface.


def safe_result(result: dict) -> dict:
    def sanitize(value):
        if isinstance(value, dict):
            return {key: sanitize(item) for key, item in value.items() if key not in {"path", "artifact_paths", "annotated_video"}}
        if isinstance(value, list):
            return [sanitize(item) for item in value]
        return value
    # API view is intentionally separate from the detector's on-disk contract.
    return {"schema_version": result["schema_version"], "run_id": result["run_id"], "created_at_utc": result["created_at_utc"],
            "incident_decision": result["incident_decision"], "accident_detected": result["accident_detected"],
            "complete_video_processed": result["complete_video_processed"], "termination_reason": result["termination_reason"],
            "human_review_required": True, "emergency_action_authorized": False,
            "source": sanitize(result["source"]), "model": sanitize(result["model"]),
            "incidents": sanitize(result["incidents"]), "rejected_candidates": sanitize(result["rejected_candidates"]),
            "unresolved_candidates": sanitize(result["unresolved_candidates"])}


def _response(incident: Incident) -> IncidentResponse:
    return IncidentResponse(id=incident.id, status=incident.status, safe_summary=incident.safe_summary, occurred_at=incident.occurred_at, created_at=incident.created_at, detector_run_id=incident.detector_run_id, result_available=bool(incident.result_reference), evidence_available=bool(incident.evidence_reference))


def _private_path(path_value: str | None, require_file: bool = True) -> Path:
    if not path_value:
        raise HTTPException(status_code=404, detail="artifact not available")
    root = Path(get_settings().private_data_dir).resolve()
    candidate = Path(path_value).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=404, detail="artifact not available") from None
    if require_file and not candidate.is_file():
        raise HTTPException(status_code=404, detail="artifact not available")
    return candidate


def _private_file(path_value: str | None) -> Path:
    return _private_path(path_value, require_file=True)


@router.get("", response_model=list[IncidentResponse])
def list_incidents(current: Annotated[CurrentUser, Depends(require_terms)], db: Annotated[Session, Depends(get_db)], limit: int = 50, offset: int = 0):
    if not 1 <= limit <= 200 or offset < 0:
        raise HTTPException(status_code=422, detail="invalid pagination")
    rows = db.scalars(select(Incident).order_by(Incident.created_at.desc()).offset(offset).limit(limit))
    return [_response(row) for row in rows]


@router.get("/{incident_id}", response_model=IncidentDetailResponse)
def get_incident(incident_id: UUID, current: Annotated[CurrentUser, Depends(require_terms)], db: Annotated[Session, Depends(get_db)]):
    incident = db.scalar(select(Incident).where(Incident.id == incident_id))
    if incident is None:
        raise HTTPException(status_code=404, detail="incident not found")
    actions = db.scalars(select(UserAction).where(UserAction.incident_id == incident.id, UserAction.user_id == current.user.id).order_by(UserAction.created_at)).all()
    response = _response(incident)
    return IncidentDetailResponse(**response.model_dump(), job_id=incident.job_id, user_actions=[{"id": str(action.id), "action": action.action, "call_method": action.call_method, "location_consent": action.location_consent, "created_at": action.created_at.isoformat()} for action in actions])


@router.get("/{incident_id}/result")
def get_result(incident_id: UUID, current: Annotated[CurrentUser, Depends(require_terms)], db: Annotated[Session, Depends(get_db)]):
    incident = db.scalar(select(Incident).where(Incident.id == incident_id))
    if incident is None:
        raise HTTPException(status_code=404, detail="incident not found")
    return safe_result(json.loads(_private_file(incident.result_reference).read_text(encoding="utf-8")))


@router.get("/{incident_id}/evidence/{name}")
def get_evidence(incident_id: UUID, name: str, current: Annotated[CurrentUser, Depends(require_terms)], db: Annotated[Session, Depends(get_db)]):
    if name not in {"before.jpg", "strongest.jpg", "after.jpg"}:
        raise HTTPException(status_code=404, detail="evidence not found")
    incident = db.scalar(select(Incident).where(Incident.id == incident_id))
    if incident is None:
        raise HTTPException(status_code=404, detail="incident not found")
    root = _private_path(incident.evidence_reference, require_file=False)
    # Only an evidence path selected by the published result is retrievable.
    result = json.loads(_private_file(incident.result_reference).read_text(encoding="utf-8"))
    detector_incident_id = incident.detector_run_id.split(":", 1)[1] if incident.detector_run_id else None
    allowed = [item for candidate in result["incidents"] if candidate["incident_id"] == detector_incident_id for item in candidate["evidence_frames"]]
    matches = [item for item in allowed if Path(item["path"]).name == name]
    if not matches:
        raise HTTPException(status_code=404, detail="evidence not found")
    from ..services.storage import sha256_file
    path = _private_file(str(root / matches[0]["path"]))
    if sha256_file(path) != matches[0]["sha256"]:
        raise HTTPException(status_code=410, detail="evidence integrity check failed")
    return FileResponse(path, media_type="image/jpeg")


@router.get("/{incident_id}/report")
def get_report(incident_id: UUID, current: Annotated[CurrentUser, Depends(require_terms)], db: Annotated[Session, Depends(get_db)]):
    incident = db.scalar(select(Incident).where(Incident.id == incident_id))
    if incident is None:
        raise HTTPException(status_code=404, detail="incident not found")
    view = safe_result(json.loads(_private_file(incident.result_reference).read_text(encoding="utf-8")))
    return Response("# CrashPulse review report\n\n" + incident.safe_summary + "\n\n```json\n" + json.dumps(view, indent=2) + "\n```\n", media_type="text/markdown")


@router.post("/{incident_id}/actions", response_model=ActionResponse, status_code=201)
def record_action(
    incident_id: UUID,
    payload: IncidentActionRequest,
    current: Annotated[CurrentUser, Depends(require_terms)],
    db: Annotated[Session, Depends(get_db)],
    idempotency_key: Annotated[str | None, Header(max_length=200)] = None,
):
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required")
    incident = db.scalar(select(Incident).where(Incident.id == incident_id).with_for_update())
    if incident is None:
        raise HTTPException(status_code=404, detail="incident not found")
    previous = db.scalar(select(UserAction).where(UserAction.user_id == current.user.id, UserAction.idempotency_key == idempotency_key))
    if previous is not None:
        if previous.incident_id != incident.id or previous.action != payload.action or previous.call_method != payload.call_method or previous.location_consent != payload.location_consent:
            raise HTTPException(status_code=409, detail="idempotency key was already used with different action data")
        return ActionResponse.model_validate(previous)
    if payload.action != "PROCEEDED" and payload.call_method is not None:
        raise HTTPException(status_code=400, detail="call method is valid only for Proceeded actions")
    action = UserAction(user_id=current.user.id, incident_id=incident.id, idempotency_key=idempotency_key, action=payload.action, call_method=payload.call_method, location_consent=payload.location_consent)
    db.add(action)
    incident.status = "PROCEEDED" if payload.action == "PROCEEDED" else "REVIEWED"
    db.commit()
    db.refresh(action)
    return action


@router.post("/{incident_id}/location", response_model=ActionResponse, status_code=201)
def attach_location(incident_id: UUID, payload: LocationAttachmentRequest, current: Annotated[CurrentUser, Depends(require_terms)], db: Annotated[Session, Depends(get_db)], idempotency_key: Annotated[str | None, Header(min_length=1, max_length=200)] = None):
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required")
    incident = db.scalar(select(Incident).where(Incident.id == incident_id).with_for_update())
    if incident is None:
        raise HTTPException(status_code=404, detail="incident not found")
    previous = db.scalar(select(UserAction).where(UserAction.user_id == current.user.id, UserAction.idempotency_key == idempotency_key))
    if previous is not None:
        if previous.incident_id != incident_id or previous.location_data != payload.model_dump(mode="json"):
            raise HTTPException(status_code=409, detail="idempotency key was already used")
        return ActionResponse.model_validate(previous)
    if db.scalar(select(UserAction).where(UserAction.incident_id == incident_id, UserAction.user_id == current.user.id, UserAction.action == "PROCEEDED")) is None:
        raise HTTPException(status_code=403, detail="phone location can only follow an explicit Proceed action")
    now = datetime.now(timezone.utc)
    if payload.captured_at.tzinfo is None or abs((now - payload.captured_at).total_seconds()) > 900:
        raise HTTPException(status_code=422, detail="phone location must include timezone and be captured within 15 minutes")
    data = {**payload.model_dump(mode="json"), "meaning": "phone_current_location_not_crash_location"}
    action = UserAction(user_id=current.user.id, incident_id=incident.id, idempotency_key=idempotency_key, action="LOCATION_ATTACHED", location_consent=True, location_data=data)
    db.add(action)
    db.commit()
    db.refresh(action)
    return action
