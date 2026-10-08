"""Real PostgreSQL tests. Each run uses and removes an isolated random schema."""
import hashlib
import os
import secrets
import uuid
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from dotenv import dotenv_values
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.config import Settings, get_settings
from app.db import get_db
from app.main import app
from app.models import Device, TermsAcceptance, TermsVersion, User
from app.security import hash_password, verify_password


@pytest.fixture
def pg_client(monkeypatch):
    url = os.getenv("TEST_DATABASE_URL") or dotenv_values(Path(__file__).parents[1] / ".env").get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not configured; real PostgreSQL verification requires local credentials")
    Settings(database_url=url, _env_file=None).validate_database_url()
    schema = "test_crashpulse_" + uuid.uuid4().hex
    root_engine = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})
    with root_engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    engine = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5, "options": f"-c search_path={schema}"})
    try:
        import scripts.migrate as migration
        monkeypatch.setattr(migration, "get_engine", lambda: engine)
        migration.main()
        migration.main()  # Reapplication is a no-op, with checksum verification.
        monkeypatch.setenv("JWT_SECRET", secrets.token_urlsafe(48))
        monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
        get_settings.cache_clear()
        factory = sessionmaker(engine)
        password = secrets.token_urlsafe(24)
        with factory() as db:
            db.add(User(login_id="test-operator", password_hash=hash_password(password), role="ADMIN", is_active=True))
            content = "Test-only terms: human review required; no automatic emergency action."
            db.add(TermsVersion(version="test-v1", content=content, content_sha256=hashlib.sha256(content.encode()).hexdigest(), is_current=True))
            db.commit()
        def sessions():
            with factory() as db:
                yield db
        app.dependency_overrides[get_db] = sessions
        from app.routers.auth import limiter
        limiter._events.clear()
        client = TestClient(app, client=("127.0.0.1", 40000))
        result = client.post("/api/v1/auth/login", json={"login_id": "test-operator", "password": password})
        assert result.status_code == 200
        yield client, factory, {"Authorization": "Bearer " + result.json()["access_token"]}
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()
        engine.dispose()
        with root_engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        root_engine.dispose()


def test_real_account_creation_duplicate_login_and_disable(pg_client):
    client, factory, headers = pg_client
    password = secrets.token_urlsafe(24)
    payload = {"login_id": "mobile-test", "password": password}
    created = client.post("/api/v1/admin/users", headers=headers, json=payload)
    assert created.status_code == 201
    assert "password" not in created.text
    with factory() as db:
        row = db.scalar(select(User).where(User.login_id == "mobile-test"))
        assert row is not None and row.role == "USER"
        assert row.password_hash != password and verify_password(password, row.password_hash)
    assert client.post("/api/v1/admin/users", headers=headers, json=payload).status_code == 409
    login = client.post("/api/v1/auth/login", json=payload)
    assert login.status_code == 200
    mobile_headers = {"Authorization": "Bearer " + login.json()["access_token"]}
    assert client.post("/api/v1/admin/users", headers=mobile_headers, json={"login_id": "denied-test", "password": password}).status_code == 403
    assert client.post(f'/api/v1/admin/users/{created.json()["id"]}/disable', headers=headers).status_code == 200
    assert client.get("/api/v1/auth/me", headers=mobile_headers).status_code == 401
    assert client.post("/api/v1/auth/login", json=payload).status_code == 401


def test_refresh_rotation_and_logout_revoke_access(pg_client):
    client, _, headers = pg_client
    # Create a mobile account to obtain fresh independently revocable credentials.
    payload = {"login_id": "session-test", "password": secrets.token_urlsafe(24)}
    assert client.post("/api/v1/admin/users", headers=headers, json=payload).status_code == 201
    tokens = client.post("/api/v1/auth/login", json=payload).json()
    rotated = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert rotated.status_code == 200
    assert client.get("/api/v1/auth/me", headers={"Authorization": "Bearer " + tokens["access_token"]}).status_code == 401
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401
    assert client.post("/api/v1/auth/logout", json={"refresh_token": rotated.json()["refresh_token"]}).status_code == 204
    assert client.get("/api/v1/auth/me", headers={"Authorization": "Bearer " + rotated.json()["access_token"]}).status_code == 401


def test_terms_audit_and_device_rotation_are_persisted(pg_client):
    client, factory, headers = pg_client
    token = "test-token-" + secrets.token_urlsafe(32)
    assert client.post("/api/v1/devices/fcm-token", headers=headers, json={"token": token}).status_code == 403
    terms = client.get("/api/v1/terms/current", headers=headers).json()
    acceptance = {"version": terms["version"], "content_sha256": terms["content_sha256"]}
    assert client.post("/api/v1/terms/acceptance", headers=headers, json=acceptance).status_code == 201
    assert client.post("/api/v1/terms/acceptance", headers=headers, json=acceptance).status_code == 201
    device = client.post("/api/v1/devices/fcm-token", headers=headers, json={"token": token})
    assert device.status_code == 200
    assert token not in device.text
    token2 = "rotated-token-" + secrets.token_urlsafe(32)
    assert client.patch(f'/api/v1/devices/fcm-token/{device.json()["id"]}', headers=headers, json={"token": token2}).status_code == 200
    with factory() as db:
        assert len(list(db.scalars(select(TermsAcceptance)))) == 1
        stored = db.get(Device, uuid.UUID(device.json()["id"]))
        assert stored.token_hash == hashlib.sha256(token2.encode()).hexdigest()
        assert token2 not in stored.token_ciphertext
    assert client.delete(f'/api/v1/devices/fcm-token/{device.json()["id"]}', headers=headers).status_code == 204


def test_fake_outbox_delivery_is_persisted_without_network(pg_client):
    from app.models import Incident, NotificationAttempt, NotificationOutbox
    from app.services.notifications import FakeNotificationSender, dispatch_pending, enqueue_incident_notifications

    client, factory, headers = pg_client
    terms = client.get("/api/v1/terms/current", headers=headers).json()
    assert client.post("/api/v1/terms/acceptance", headers=headers, json={"version": terms["version"], "content_sha256": terms["content_sha256"]}).status_code == 201
    token = "outbox-token-" + secrets.token_urlsafe(32)
    device = client.post("/api/v1/devices/fcm-token", headers=headers, json={"token": token}).json()
    with factory() as db:
        owner = db.scalar(select(User).where(User.login_id == "test-operator"))
        incident = Incident(owner_id=owner.id, status="UNREAD", safe_summary="Human review required.", detector_run_id="test-run:test-incident")
        db.add(incident)
        db.flush()
        assert enqueue_incident_notifications(db, incident) == 1
        db.commit()
        incident_id = incident.id
        sender = FakeNotificationSender(sent=[])
        assert dispatch_pending(db, sender) == 1
        outbox = db.scalar(select(NotificationOutbox).where(NotificationOutbox.incident_id == incident_id))
        assert outbox.status == "SIMULATED"
        assert len(list(db.scalars(select(NotificationAttempt).where(NotificationAttempt.outbox_id == outbox.id)))) == 1
        assert len(sender.sent) == 1
    assert device["id"]


def test_upload_is_bounded_private_and_idempotent(pg_client, monkeypatch, tmp_path):
    from app.config import get_settings
    from app.models import AnalysisJob
    from app.routers import jobs as jobs_router

    client, factory, headers = pg_client
    terms = client.get("/api/v1/terms/current", headers=headers).json()
    assert client.post("/api/v1/terms/acceptance", headers=headers, json={"version": terms["version"], "content_sha256": terms["content_sha256"]}).status_code == 201
    monkeypatch.setattr(get_settings(), "private_data_dir", str(tmp_path / "private"))
    submitted = []
    monkeypatch.setattr(jobs_router, "submit_job", lambda job_id: submitted.append(job_id))
    video_bytes = b"\x00\x00\x00\x18ftypisom" + b"video-bytes"
    response = client.post("/api/v1/videos/analyze", headers={**headers, "Idempotency-Key": "upload-once"}, files={"video": ("camera.mp4", video_bytes, "video/mp4")})
    assert response.status_code == 202
    first = response.json()
    assert submitted == [uuid.UUID(first["id"])]
    with factory() as db:
        job = db.get(AnalysisJob, uuid.UUID(first["id"]))
        assert job.input_reference.startswith(str(tmp_path / "private"))
        assert Path(job.input_reference).read_bytes() == video_bytes
    repeat = client.post("/api/v1/videos/analyze", headers={**headers, "Idempotency-Key": "upload-once"}, files={"video": ("other.mp4", video_bytes, "video/mp4")})
    assert repeat.status_code == 202
    assert repeat.json()["id"] == first["id"]
    assert len(submitted) == 1


def test_detector_result_creates_incident_and_job_status(pg_client, monkeypatch, tmp_path):
    import json
    from app.models import AnalysisJob, Incident
    from app.services.detector import DetectorRun
    from app.services import jobs as job_service

    client, factory, headers = pg_client
    result_path = Path(__file__).parents[2] / "outputs" / "phase5 simulation result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    output_result = tmp_path / "detector-result.json"
    output_result.write_text(json.dumps(result), encoding="utf-8")
    with factory() as db:
        owner = db.scalar(select(User).where(User.login_id == "test-operator"))
        job = AnalysisJob(owner_id=owner.id, status="QUEUED", input_reference=str(tmp_path / "input.mp4"))
        db.add(job)
        db.commit()
        job_id = job.id

    class FakeAdapter:
        def run(self, input_path, output_dir, cancelled=lambda: False):
            return DetectorRun(result=result, result_path=output_result, bundle_path=tmp_path / "private-job")

    monkeypatch.setattr(job_service, "DetectorAdapter", FakeAdapter)
    monkeypatch.setattr(job_service, "get_session_factory", lambda: factory)
    monkeypatch.setattr(job_service, "private_job_dir", lambda value: tmp_path / "private-job")
    job_service.process_job(job_id)
    with factory() as db:
        stored = db.get(AnalysisJob, job_id)
        incidents = list(db.scalars(select(Incident).where(Incident.job_id == job_id)))
        assert stored.status == "SUCCEEDED"
        assert stored.incident_id == incidents[0].id
        assert len(incidents) == len(result["incidents"])
        assert all(incident.evidence_reference == str(tmp_path / "private-job") for incident in incidents)


def test_incident_action_is_authorized_and_idempotent(pg_client):
    from app.models import Incident, UserAction

    client, factory, headers = pg_client
    terms = client.get("/api/v1/terms/current", headers=headers).json()
    assert client.post("/api/v1/terms/acceptance", headers=headers, json={"version": terms["version"], "content_sha256": terms["content_sha256"]}).status_code == 201
    with factory() as db:
        owner = db.scalar(select(User).where(User.login_id == "test-operator"))
        incident = Incident(owner_id=owner.id, status="UNREAD", safe_summary="Human review required.", detector_run_id="action-run:action-incident")
        db.add(incident)
        db.commit()
        incident_id = incident.id
    payload = {"action": "PROCEEDED", "call_method": "DIALER", "location_consent": True}
    action = client.post(f"/api/v1/incidents/{incident_id}/actions", headers={**headers, "Idempotency-Key": "action-once"}, json=payload)
    assert action.status_code == 201
    repeated = client.post(f"/api/v1/incidents/{incident_id}/actions", headers={**headers, "Idempotency-Key": "action-once"}, json=payload)
    assert repeated.status_code == 201
    assert repeated.json()["id"] == action.json()["id"]
    detail = client.get(f"/api/v1/incidents/{incident_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["status"] == "PROCEEDED"
    with factory() as db:
        assert len(list(db.scalars(select(UserAction).where(UserAction.incident_id == incident_id)))) == 1
