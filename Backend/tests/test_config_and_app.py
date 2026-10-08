import pytest
from fastapi.testclient import TestClient
def test_sqlite_is_rejected(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///not-allowed.db")
    from app.config import Settings

    with pytest.raises(RuntimeError, match="PostgreSQL"):
        Settings().validate_database_url()


def test_routes_are_declared_without_database_connection():
    from app.main import app

    paths = app.openapi()["paths"]
    assert "/health/live" in paths
    assert "/health/ready" in paths
    assert "/api/v1/auth/login" in paths
    assert "/api/v1/admin/users" in paths
    assert "/api/v1/terms/acceptance" in paths
    assert "/api/v1/devices/fcm-token/{device_id}" in paths
    assert "/api/v1/auth/register" not in paths


def test_liveness_and_local_admin_page_work_without_db():
    from app.main import app
    client = TestClient(app, client=("127.0.0.1", 50000))
    assert client.get("/health/live").json()["status"] == "ok"
    response = client.get("/admin")
    assert response.status_code == 200
    assert "real PostgreSQL accounts" in response.text
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert client.get("/admin/assets/admin.js").status_code == 200


def test_admin_page_is_loopback_only():
    from app.main import app
    client = TestClient(app, client=("192.168.1.40", 50000))
    assert client.get("/admin").status_code == 403


def test_readiness_reports_unconfigured_database(monkeypatch):
    from app.main import app
    from app.routers import health
    def unavailable():
        raise RuntimeError("missing database config")
    monkeypatch.setattr(health, "get_engine", unavailable)
    response = TestClient(app).get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "database": "unavailable"}
