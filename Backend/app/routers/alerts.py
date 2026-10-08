"""Receive a live alert the instant the temporal engine confirms.

The detector's normal report is published once, at the end of a run, so a
confirmation at t=1s would otherwise stay invisible until the whole clip had
been processed. The detector's opt-in ``--alert-webhook`` posts here instead,
which lets the notification dispatcher push while analysis is still running.

Only a **strongest evidence frame** exists at confirmation time; the
before/after context frames are chosen at run end and are not part of a live
alert. This endpoint therefore writes a minimal, self-consistent result document
so the existing incident, result and evidence endpoints work unchanged.
"""

import base64
import binascii
import hashlib
import hmac
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import Incident
from ..services.ingest import SAFE_SUMMARY, resolve_alert_owner
from ..services.notifications import enqueue_incident_notifications

router = APIRouter(prefix="/detector", tags=["detector"])

SUPPORTED_SCHEMA = "crashpulse_live_alert/1"
_MAX_IMAGE_BYTES = 8 * 1024 * 1024


class LiveAlertRequest(BaseModel):
    # Named schema_ because BaseModel already defines a deprecated .schema().
    # The wire format keeps the shorter, documented "schema" key.
    model_config = ConfigDict(populate_by_name=True)

    schema_: str = Field(alias="schema")
    # Identifies one detector invocation. Including it means a retried POST
    # from the same run is still idempotent, while deliberately replaying the
    # same clip later raises a new alert instead of being deduplicated away.
    run_token: str = Field(min_length=8, max_length=64)
    source_path: str = Field(max_length=1024)
    source_sha256: str = Field(min_length=64, max_length=64)
    confirmed_count: int = Field(ge=1)
    confirmed_at_seconds: float = Field(ge=0)
    frame_index: int = Field(ge=0)
    strongest: dict[str, Any] | None = None
    image_jpeg_base64: str | None = None


def _require_token(provided: str | None) -> None:
    """Constant-time shared-secret check; a missing token disables the route."""
    expected = get_settings().detector_alert_token
    if not expected:
        raise HTTPException(status_code=503, detail="live alert ingestion is not configured")
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="invalid detector token")


def _incident_key(payload: LiveAlertRequest) -> str:
    """Stable identity for one confirmation within one detector run."""
    material = f"{payload.run_token}:{payload.source_sha256}:{payload.confirmed_at_seconds:.3f}:{payload.frame_index}"
    return f"live_{hashlib.sha256(material.encode('utf-8')).hexdigest()[:32]}"


@router.post("/alert", status_code=status.HTTP_201_CREATED)
def receive_alert(
    payload: LiveAlertRequest,
    db: Annotated[Session, Depends(get_db)],
    x_detector_token: Annotated[str | None, Header(max_length=300)] = None,
) -> dict[str, Any]:
    _require_token(x_detector_token)
    if payload.schema_ != SUPPORTED_SCHEMA:
        raise HTTPException(status_code=422, detail="unsupported alert schema")

    owner = _owner_or_503(db)
    key = _incident_key(payload)
    incident_id = _incident_id_for(key)
    detector_key = f"{key}:{incident_id}"

    existing = db.scalar(select(Incident).where(Incident.detector_run_id == detector_key))
    if existing is not None:
        # Idempotent: a retried alert returns the original incident.
        return {"incident_id": str(existing.id), "duplicate": True, "notifications_queued": 0}

    image_bytes: bytes | None = None
    if payload.image_jpeg_base64:
        try:
            image_bytes = base64.b64decode(payload.image_jpeg_base64, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(status_code=422, detail="evidence image is not valid base64") from None
        if not image_bytes or len(image_bytes) > _MAX_IMAGE_BYTES:
            raise HTTPException(status_code=422, detail="evidence image is empty or too large")

    root = Path(get_settings().private_data_dir).resolve() / "live-alerts" / key
    root.mkdir(parents=True, exist_ok=True)

    evidence_frames: list[dict[str, Any]] = []
    if image_bytes is not None:
        digest = hashlib.sha256(image_bytes).hexdigest()
        (root / "strongest.jpg").write_bytes(image_bytes)
        strongest = payload.strongest or {}
        evidence_frames.append({
            "role": "strongest",
            "path": "strongest.jpg",
            "image_kind": "source_frame",
            "frame_index": strongest.get("frame_index", payload.frame_index),
            "timestamp_seconds": strongest.get("timestamp_seconds", payload.confirmed_at_seconds),
            "timestamp_source": strongest.get("timestamp_source", "decoder"),
            "sha256": digest,
            "offset_from_strongest_seconds": 0.0,
            "context_limited": True,
        })

    incident_id = _incident_id_for(key)
    result = {
        "schema_version": payload.schema_,
        "run_id": key,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "incident_decision": "confirmed_incident",
        "accident_detected": True,
        "complete_video_processed": False,
        "termination_reason": "live_alert_confirmed",
        "human_review_required": True,
        "emergency_action_authorized": False,
        "source": {"path": None, "sha256": payload.source_sha256},
        "model": {"path": None, "sha256": None},
        "incidents": [{
            "candidate_number": payload.confirmed_count,
            "state": "CONFIRMED",
            "incident_id": incident_id,
            "confirmed_at_seconds": payload.confirmed_at_seconds,
            "strongest_frame": payload.strongest or {},
            "evidence_frames": evidence_frames,
            "evidence_status": "live_strongest_frame_only",
        }],
        "rejected_candidates": [],
        "unresolved_candidates": [],
    }
    json_path = root / "result.json"
    json_path.write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")

    incident = Incident(
        owner_id=owner.id,
        job_id=None,
        detector_run_id=detector_key,
        status="UNREAD",
        safe_summary=SAFE_SUMMARY,
        result_reference=str(json_path),
        evidence_reference=str(root) if evidence_frames else None,
        # A live alert reports a video timestamp, not a wall-clock crash time.
        occurred_at=None,
    )
    db.add(incident)
    db.flush()
    queued = enqueue_incident_notifications(db, incident)
    db.commit()

    return {"incident_id": str(incident.id), "duplicate": False, "notifications_queued": queued,
            "evidence_available": bool(evidence_frames)}


def _incident_id_for(key: str) -> str:
    return f"inc_{key.removeprefix('live_')[:24]}"


def _owner_or_503(db: Session):
    from ..services.ingest import IngestError

    try:
        return resolve_alert_owner(db)
    except IngestError as error:
        raise HTTPException(status_code=503, detail=str(error)) from None