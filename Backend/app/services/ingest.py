"""Publish an externally-run detector analysis into CrashPulse.

The detector project is deliberately never imported or modified. This module
only accepts an already-validated detector result (as produced by
``DetectorAdapter``) and copies its bundle into private Backend storage so the
existing incident API can serve it.

Copying is required because the incident API refuses to serve any artifact
outside ``PRIVATE_DATA_DIR``. Evidence paths inside the detector result are
already relative to the run directory, so a faithful copy keeps resolving.
"""

import json
import shutil
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Incident, User
from .notifications import enqueue_incident_notifications
from .storage import sha256_file

SAFE_SUMMARY = "Temporal detector confirmation requires human review."


class IngestError(RuntimeError):
    """A safe ingest failure; detector internals stay private."""


def resolve_alert_owner(db: Session) -> User:
    """Resolve the single local account that live alerts are attributed to."""
    login = get_settings().detector_alert_login
    if not login:
        raise IngestError("DETECTOR_ALERT_LOGIN is not configured")
    owner = db.scalar(select(User).where(User.login_id == login))
    if owner is None:
        raise IngestError("the configured alert recipient does not exist")
    if not owner.is_active:
        raise IngestError("the configured alert recipient is disabled")
    return owner


def _publish(result: dict, bundle: Path) -> Path:
    """Copy a validated run directory into private storage and re-verify it."""
    if bundle is None or not Path(bundle).is_dir():
        raise IngestError("detector run directory is unavailable")
    source = Path(bundle).resolve()
    destination = Path(get_settings().private_data_dir).resolve() / "detector-ingest" / result["run_id"]
    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination)

    published = destination / "result.json"
    if not published.is_file():
        raise IngestError("copied detector bundle has no result.json")
    # The copy must remain byte-identical to what the adapter already validated,
    # otherwise evidence hashes below would refer to a different document.
    if json.loads(published.read_text(encoding="utf-8")) != result:
        raise IngestError("copied detector result does not match the validated result")

    for candidate in result["incidents"]:
        for evidence in candidate["evidence_frames"]:
            path = (destination / evidence["path"]).resolve()
            if not path.is_relative_to(destination) or not path.is_file():
                raise IngestError("copied evidence frame is missing")
            if sha256_file(path) != evidence["sha256"]:
                raise IngestError("copied evidence hash mismatch")
    return destination


def ingest_detector_run(db: Session, result: dict, bundle: Path, owner: User) -> list[Incident]:
    """Create incidents for every confirmed candidate and enqueue notifications.

    Idempotent on ``run_id:incident_id``, so re-running the same clip never
    duplicates an alert or re-notifies a device.
    """
    root = _publish(result, bundle)
    json_path = root / "result.json"
    created: list[Incident] = []
    for candidate in result["incidents"]:
        detector_key = f"{result['run_id']}:{candidate['incident_id']}"
        if db.scalar(select(Incident).where(Incident.detector_run_id == detector_key)) is not None:
            continue
        incident = Incident(
            owner_id=owner.id,
            job_id=None,
            detector_run_id=detector_key,
            status="UNREAD",
            safe_summary=SAFE_SUMMARY,
            result_reference=str(json_path),
            evidence_reference=str(root),
            # Report generation is not a real-world crash timestamp.
            occurred_at=None,
        )
        db.add(incident)
        db.flush()
        enqueue_incident_notifications(db, incident)
        created.append(incident)
    db.commit()
    return created