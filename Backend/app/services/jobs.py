from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Thread
from uuid import UUID

from sqlalchemy import select, text, update
from sqlalchemy.exc import SQLAlchemyError

from ..config import get_settings
from ..db import get_engine, get_session_factory
from ..models import AnalysisJob, Incident
from .detector import DetectorAdapter, DetectorAdapterError, DetectorCancelled
from .notifications import enqueue_incident_notifications


def private_job_dir(job_id: UUID) -> Path:
    path = Path(get_settings().private_data_dir).resolve() / "jobs" / str(job_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def process_job(job_id: UUID, stopping: Event | None = None) -> None:
    factory = get_session_factory()
    with factory() as db:
        job = db.scalar(select(AnalysisJob).where(AnalysisJob.id == job_id).with_for_update())
        if job is None or job.status != "QUEUED":
            return
        job.status = "RUNNING"
        db.commit()
        input_path = Path(job.input_reference or "")
        output_dir = private_job_dir(job.id) / "output"

        def cancelled() -> bool:
            if stopping is not None and stopping.is_set():
                return True
            with factory() as check:
                return check.scalar(select(AnalysisJob.status).where(AnalysisJob.id == job_id)) == "CANCEL_REQUESTED"

        try:
            run = DetectorAdapter().run(input_path, output_dir, cancelled=cancelled)
            # Release any stale cached job state before final persistence/cancel check.
            db.expire_all()
            job = db.scalar(select(AnalysisJob).where(AnalysisJob.id == job_id).with_for_update())
            if job.status == "CANCEL_REQUESTED":
                raise DetectorCancelled("analysis cancelled")
            job.result_reference = str(run.result_path)
            job.decision = run.result["incident_decision"]
            first_incident = None
            for candidate in run.result["incidents"]:
                detector_key = f"{run.result['run_id']}:{candidate['incident_id']}"
                incident = db.scalar(select(Incident).where(Incident.detector_run_id == detector_key))
                if incident is None:
                    incident = Incident(
                        owner_id=job.owner_id, job_id=job.id, detector_run_id=detector_key,
                        status="UNREAD", safe_summary="Temporal detector confirmation requires human review.",
                        result_reference=str(run.result_path), evidence_reference=str(run.bundle_path) if run.bundle_path else None,
                        # Report generation is not a real-world crash timestamp.
                        occurred_at=None,
                    )
                    db.add(incident)
                    db.flush()
                    enqueue_incident_notifications(db, incident)
                first_incident = first_incident or incident.id
            job.incident_id = first_incident
            job.status = "SUCCEEDED"
            job.completed_at = datetime.now(timezone.utc)
            db.commit()
        except Exception as error:
            db.rollback()  # Never leave partial incident/outbox persistence behind.
            job = db.get(AnalysisJob, job_id)
            job.status = "CANCELLED" if isinstance(error, DetectorCancelled) else "FAILED"
            job.error_message = str(error) if isinstance(error, DetectorAdapterError) else "analysis failed during result persistence"
            job.completed_at = datetime.now(timezone.utc)
            db.commit()
        finally:
            input_path.unlink(missing_ok=True)


class JobWorker:
    """Durable PostgreSQL queue; one bounded worker owner per database."""

    def __init__(self):
        self.stopping = Event()
        self.thread = None
        self.lock_connection = None

    def start(self) -> None:
        self.lock_connection = get_engine().connect()
        owned = self.lock_connection.execute(text("SELECT pg_try_advisory_lock(73683001)")).scalar_one()
        self.lock_connection.commit()
        if not owned:
            self.lock_connection.close()
            raise RuntimeError("another Backend worker is active; run uvicorn with --workers 1")
        with get_session_factory()() as db:
            db.execute(update(AnalysisJob).where(AnalysisJob.status.in_(["RUNNING", "CANCEL_REQUESTED", "UPLOADING"])).values(status="FAILED", error_message="previous server stopped before job completion", completed_at=datetime.now(timezone.utc)))
            db.commit()
        self.thread = Thread(target=self.run, name="crashpulse-queue", daemon=True)
        self.thread.start()

    def run(self) -> None:
        maximum = get_settings().detector_max_concurrent_jobs
        with ThreadPoolExecutor(max_workers=maximum, thread_name_prefix="crashpulse-detector") as executor:
            running = {}
            while not self.stopping.is_set():
                for job_id, future in list(running.items()):
                    if future.done():
                        del running[job_id]
                capacity = maximum - len(running)
                if capacity:
                    try:
                        with get_session_factory()() as db:
                            query = select(AnalysisJob.id).where(AnalysisJob.status == "QUEUED").order_by(AnalysisJob.created_at).limit(maximum)
                            for job_id in db.scalars(query):
                                if job_id not in running and capacity:
                                    running[job_id] = executor.submit(process_job, job_id, self.stopping)
                                    capacity -= 1
                    except SQLAlchemyError:
                        pass  # Durable queued rows are retried when PostgreSQL recovers.
                self.stopping.wait(0.5)

    def stop(self) -> None:
        self.stopping.set()
        if self.thread:
            self.thread.join()
        if self.lock_connection is not None:
            self.lock_connection.execute(text("SELECT pg_advisory_unlock(73683001)"))
            self.lock_connection.commit()
            self.lock_connection.close()


def submit_job(job_id: UUID) -> None:
    # The row is already durable. The lifespan worker polls the queue; no
    # unbounded in-memory future is submitted from an HTTP request.
    return None
