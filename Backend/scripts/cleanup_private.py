"""Explicit local retention cleanup for terminal Backend job directories."""
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from app.config import get_settings
from app.db import get_session_factory
from app.models import AnalysisJob
from scripts.common import run_operator


def main() -> None:
    settings = get_settings()
    settings.validate_storage()
    root = Path(settings.private_data_dir).resolve() / "jobs"
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.artifact_retention_days)
    count = 0
    with get_session_factory()() as db:
        rows = db.scalars(select(AnalysisJob).where(AnalysisJob.status.in_(["SUCCEEDED", "FAILED", "CANCELLED"]), AnalysisJob.created_at < cutoff))
        for job in rows:
            candidate = root / str(UUID(str(job.id)))
            if candidate.is_symlink() or not candidate.is_dir():
                continue
            if any(item.is_symlink() for item in candidate.rglob("*")):
                continue
            shutil.rmtree(candidate)
            count += 1
    print(f"expired private job directories removed: {count}")


if __name__ == "__main__":
    run_operator(main)
