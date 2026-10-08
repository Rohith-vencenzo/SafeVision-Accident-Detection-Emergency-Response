from pathlib import Path
from contextlib import asynccontextmanager
import uuid

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError

from .config import get_settings
from .dependencies import require_local_operator
from .logging_config import configure_logging
from .upload_limits import UploadLimitMiddleware
from .services.detector import DetectorAdapter
from .services.dispatcher import NotificationDispatcher
from .services.jobs import JobWorker
from .routers import admin, alerts, auth, devices, health, incidents, jobs, terms

settings = get_settings()
logger = configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_settings().validate_database_url()
    get_settings().validate_auth_secrets()
    get_settings().validate_storage()
    DetectorAdapter().paths()
    worker = JobWorker()
    worker.start()
    dispatcher = NotificationDispatcher()
    dispatcher.start()
    try:
        yield
    finally:
        dispatcher.stop()
        worker.stop()


app = FastAPI(title=settings.app_name, version="0.1.0", docs_url="/docs", redoc_url="/redoc", lifespan=lifespan)
app.add_middleware(UploadLimitMiddleware)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = uuid.uuid4().hex
    request.state.request_id = request_id
    if request.url.path == "/admin" or request.url.path.startswith("/admin/"):
        if request.client is None or request.client.host not in {"127.0.0.1", "::1"}:
            return JSONResponse(status_code=403, content={"detail": "administrator console is available on loopback only"})
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    if request.url.path.startswith("/admin"):
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'"
    logger.info("http_request", extra={"safe_fields": {"request_id": request_id, "method": request.method, "route": getattr(request.scope.get("route"), "path", "unmatched"), "status": response.status_code}})
    return response


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, error: RequestValidationError):
    errors = [{"loc": item["loc"], "msg": item["msg"], "type": item["type"]} for item in error.errors()]
    return JSONResponse(status_code=422, content={"detail": errors})


@app.exception_handler(SQLAlchemyError)
async def database_error(request: Request, error: SQLAlchemyError):
    return JSONResponse(status_code=503, content={"detail": "database unavailable; check local configuration and migrations"})


@app.exception_handler(Exception)
async def unexpected_error(request: Request, error: Exception):
    logger.error("request_failed", extra={"safe_fields": {"request_id": getattr(request.state, "request_id", "unknown"), "error_type": type(error).__name__}})
    return JSONResponse(status_code=500, content={"detail": "internal error", "request_id": getattr(request.state, "request_id", "unknown")})

app.include_router(health.router)
app.include_router(auth.router, prefix="/api/v1")
app.include_router(admin.router, prefix="/api/v1")
app.include_router(terms.router, prefix="/api/v1")
app.include_router(devices.router, prefix="/api/v1")
app.include_router(jobs.router, prefix="/api/v1")
app.include_router(alerts.router, prefix="/api/v1")
app.include_router(incidents.router, prefix="/api/v1")

ADMIN_DIR = Path(__file__).parent / "static" / "admin"
app.mount("/admin/assets", StaticFiles(directory=ADMIN_DIR / "assets"), name="admin-assets")


@app.get("/admin", include_in_schema=False, dependencies=[Depends(require_local_operator)])
def admin_page():
    return FileResponse(ADMIN_DIR / "index.html")
