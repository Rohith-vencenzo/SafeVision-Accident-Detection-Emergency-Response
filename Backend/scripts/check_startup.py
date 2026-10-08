"""Exercise FastAPI lifespan and readiness using local, non-secret path config."""
from fastapi.testclient import TestClient

from app.main import app


def main() -> None:
    with TestClient(app) as client:
        response = client.get("/health/ready")
        print(f"startup_status={response.status_code} readiness={response.json().get('status')}")
        if response.status_code != 200:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
