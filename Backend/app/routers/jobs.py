import hashlib
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, UploadFile, status
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..dependencies import CurrentUser, require_terms
from ..models import AnalysisJob
from ..schemas import JobResponse
from ..services.jobs import private_job_dir, submit_job
from ..services.storage import read_result
from .incidents import safe_result

router = APIRouter(tags=["analysis jobs"])
ALLOWED_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".webm"}
ALLOWED_CONTENT_TYPES = {"video/mp4", "video/quicktime", "video/x-msvideo", "video/x-matroska", "video/webm"}


def _job_response(job: AnalysisJob) -> JobResponse:
    return JobResponse(id=job.id, status=job.status, error_message=job.error_message, incident_id=job.incident_id, created_at=job.created_at, completed_at=job.completed_at, decision=job.decision)


def looks_like_video(prefix: bytes, extension: str) -> bool:
    if extension in {".mp4", ".mov"}:
        return len(prefix) >= 12 and prefix[4:8] == b"ftyp"
    if extension == ".avi":
        return prefix.startswith(b"RIFF") and prefix[8:12] == b"AVI "
    return prefix.startswith(b"\x1a\x45\xdf\xa3")


@router.post("/videos/analyze", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_video(request: Request, current: Annotated[CurrentUser, Depends(require_terms)], db: Annotated[Session, Depends(get_db)], idempotency_key: Annotated[str | None, Header(min_length=1, max_length=200)] = None):
    # Authenticate before FastAPI/Starlette parses and spools multipart data.
    settings = get_settings()
    form = await request.form(max_files=1, max_fields=0, max_part_size=settings.max_upload_bytes)
    try:
        video = form.get("video")
        if video is None or not hasattr(video, "read"):
            raise HTTPException(status_code=422, detail="one video file is required")
        extension = Path(video.filename or "").suffix.lower()
        if extension not in ALLOWED_EXTENSIONS or video.content_type not in ALLOWED_CONTENT_TYPES:
            raise HTTPException(status_code=415, detail="unsupported video container or media type")
        prefix = await video.read(32)
        await video.seek(0)
        if not looks_like_video(prefix, extension):
            raise HTTPException(status_code=415, detail="file header does not match an allowed video container")
        db.execute(text("SELECT pg_advisory_xact_lock(73683002)"))
        if idempotency_key:
            existing = db.scalar(select(AnalysisJob).where(AnalysisJob.owner_id == current.user.id, AnalysisJob.idempotency_key == idempotency_key))
            if existing is not None:
                if existing.status == "UPLOADING":
                    raise HTTPException(status_code=409, detail="upload with this key is still in progress")
                return _job_response(existing)
        pending = db.scalar(select(func.count()).select_from(AnalysisJob).where(AnalysisJob.status.in_(["UPLOADING", "QUEUED", "RUNNING", "CANCEL_REQUESTED"]))) or 0
        if pending >= settings.max_pending_jobs:
            raise HTTPException(status_code=429, detail="analysis queue is full; try again later")
        job = AnalysisJob(owner_id=current.user.id, status="UPLOADING", idempotency_key=idempotency_key)
        db.add(job)
        db.flush()
        input_path = private_job_dir(job.id) / f"input{extension}"
        job.input_reference = str(input_path)
        db.commit()  # Reserve a queue slot before receiving/storing file bytes.
        total, digest = 0, hashlib.sha256()
        try:
            with input_path.open("xb") as output:
                while chunk := await video.read(1024 * 1024):
                    total += len(chunk)
                    if total > settings.max_upload_bytes:
                        raise HTTPException(status_code=413, detail="video exceeds configured size limit")
                    output.write(chunk)
                    digest.update(chunk)
            job.input_sha256 = digest.hexdigest()
            job.status = "QUEUED"
            db.commit()
        except BaseException:
            db.rollback()
            input_path.unlink(missing_ok=True)
            stored = db.get(AnalysisJob, job.id)
            stored.status, stored.error_message = "FAILED", "upload interrupted or exceeded limits"
            db.commit()
            raise
        submit_job(job.id)
        db.refresh(job)
        return _job_response(job)
    finally:
        await form.close()


@router.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: UUID, current: Annotated[CurrentUser, Depends(require_terms)], db: Annotated[Session, Depends(get_db)]):
    job = db.scalar(select(AnalysisJob).where(AnalysisJob.id == job_id, AnalysisJob.owner_id == current.user.id))
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return _job_response(job)


@router.get("/jobs/{job_id}/result")
def job_result(job_id: UUID, current: Annotated[CurrentUser, Depends(require_terms)], db: Annotated[Session, Depends(get_db)]):
    job = db.scalar(select(AnalysisJob).where(AnalysisJob.id == job_id, AnalysisJob.owner_id == current.user.id))
    if job is None or job.status != "SUCCEEDED" or not job.result_reference:
        raise HTTPException(status_code=404, detail="job result not available")
    path = Path(job.result_reference).resolve()
    if not path.is_relative_to(Path(get_settings().private_data_dir).resolve()) or not path.is_file():
        raise HTTPException(status_code=410, detail="result expired or unavailable")
    return safe_result(read_result(path))


@router.post("/jobs/{job_id}/cancel", response_model=JobResponse)
def cancel_job(job_id: UUID, current: Annotated[CurrentUser, Depends(require_terms)], db: Annotated[Session, Depends(get_db)]):
    job = db.scalar(select(AnalysisJob).where(AnalysisJob.id == job_id, AnalysisJob.owner_id == current.user.id).with_for_update())
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job.status == "QUEUED":
        job.status = "CANCELLED"
        Path(job.input_reference).unlink(missing_ok=True)
    elif job.status == "RUNNING":
        job.status = "CANCEL_REQUESTED"
    db.commit()
    db.refresh(job)
    return _job_response(job)
