from fastapi import APIRouter
from sqlalchemy import text
from fastapi.responses import JSONResponse
import hashlib
from pathlib import Path

from ..db import get_engine
from ..schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health/live", response_model=HealthResponse)
def live():
    return HealthResponse(status="ok")


@router.get("/health/ready", response_model=HealthResponse)
def ready():
    try:
        from ..config import get_settings
        get_settings().validate_auth_secrets()
        with get_engine().connect() as connection:
            applied = dict(connection.execute(text("SELECT version, checksum FROM schema_migrations")).all())
            for migration in (Path(__file__).parents[2] / "scripts" / "migrations").glob("*.sql"):
                expected = hashlib.sha256(migration.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
                if applied.get(migration.name) != expected:
                    raise RuntimeError("schema mismatch")
    except Exception:
        return JSONResponse(status_code=503, content={"status": "not_ready", "database": "unavailable"})
    return HealthResponse(status="ok", database="ok")
