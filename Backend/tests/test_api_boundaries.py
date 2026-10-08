import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.dependencies import CurrentUser, get_current_user
from app.main import app
from app.models import User


@pytest.fixture
def client():
    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    with_test_client = TestClient(app, client=("127.0.0.1", 40000))
    yield with_test_client, db
    app.dependency_overrides.clear()


def test_unauthenticated_admin_requests_cannot_create_accounts(client):
    response = client[0].post("/api/v1/admin/users", json={"login_id": "test-id", "password": "not-a-real-password"})
    assert response.status_code == 401
    client[1].add.assert_not_called()


def test_mobile_user_cannot_create_accounts(client):
    user = User(id=uuid.uuid4(), login_id="temporary", role="USER", is_active=True)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(user, {})
    response = client[0].post("/api/v1/admin/users", json={"login_id": "test-id", "password": "not-a-real-password"})
    assert response.status_code == 403
    client[1].add.assert_not_called()


def test_validation_does_not_echo_password(client):
    user = User(id=uuid.uuid4(), login_id="temporary", role="ADMIN", is_active=True)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(user, {})
    response = client[0].post("/api/v1/admin/users", json={"login_id": "invalid spaces", "password": "short-secret"})
    assert response.status_code == 422
    assert "short-secret" not in response.text
    assert "input" not in response.text


def test_acceptance_rejects_stale_terms_content(client):
    user = User(id=uuid.uuid4(), login_id="temporary", role="USER", is_active=True)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(user, {})
    client[1].scalar.return_value = MagicMock(version="2.0", content_sha256="b" * 64)
    response = client[0].post("/api/v1/terms/acceptance", json={"version": "1.0", "content_sha256": "a" * 64})
    assert response.status_code == 409
    client[1].add.assert_not_called()


def test_database_errors_do_not_return_secret_parameters(client):
    from sqlalchemy.exc import OperationalError
    client[1].scalar.side_effect = OperationalError("SELECT", {"password": "sensitive-parameter"}, Exception("database-password-example"))
    response = client[0].post("/api/v1/auth/login", json={"login_id": "test", "password": "sensitive-parameter"})
    assert response.status_code == 503
    assert "sensitive-parameter" not in response.text
    assert "database-password-example" not in response.text
