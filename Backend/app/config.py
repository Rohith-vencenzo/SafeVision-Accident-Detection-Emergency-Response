from functools import lru_cache
from pathlib import Path
from typing import Literal

from cryptography.fernet import Fernet
from pydantic import Field, field_validator
from sqlalchemy.engine import make_url
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "CrashPulse Backend"
    app_env: str = "development"
    database_url: str = Field(default="", repr=False)
    jwt_secret: str = Field(default="", repr=False)
    token_encryption_key: str = Field(default="", repr=False)
    access_token_ttl_minutes: int = Field(default=15, ge=1, le=60)
    refresh_token_ttl_days: int = Field(default=7, ge=1, le=30)
    login_rate_limit_attempts: int = Field(default=5, ge=1, le=100)
    login_rate_limit_window_seconds: int = Field(default=60, ge=1)
    max_upload_bytes: int = Field(default=100 * 1024 * 1024, ge=1024, le=1024 * 1024 * 1024)
    detector_config: str = ""
    detector_model: str = ""
    detector_timeout_seconds: int = Field(default=900, ge=30, le=7200)
    detector_max_concurrent_jobs: int = Field(default=1, ge=1, le=4)
    max_pending_jobs: int = Field(default=8, ge=1, le=100)
    private_data_dir: str = "private-data"
    artifact_retention_days: int = Field(default=7, ge=1, le=365)
    fcm_mode: Literal["disabled", "firebase"] = "disabled"
    firebase_project_id: str = "crashpulse-clg"
    firebase_credentials_file: str = Field(default="", repr=False)
    notification_max_attempts: int = Field(default=5, ge=1, le=10)
    notification_dispatch_interval_seconds: int = Field(default=5, ge=1, le=300)
    detector_alert_login: str = ""
    detector_alert_token: str = Field(default="", repr=False)

    def validate_storage(self) -> None:
        storage = Path(self.private_data_dir).resolve()
        backend = Path(__file__).parents[1].resolve()
        if not storage.is_relative_to(backend) or storage == backend or storage.is_relative_to(backend / "app"):
            raise RuntimeError("PRIVATE_DATA_DIR must be a private subdirectory of Backend outside app/")
    detector_root: str = ""
    detector_python: str = ""
    detector_entrypoint: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("access_token_ttl_minutes", "refresh_token_ttl_days")
    @classmethod
    def positive_duration(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("token durations must be positive")
        return value

    def validate_database_url(self) -> None:
        if not self.database_url:
            raise RuntimeError("DATABASE_URL is required")
        try:
            url = make_url(self.database_url)
        except Exception:
            raise RuntimeError("DATABASE_URL is invalid") from None
        if url.drivername != "postgresql+psycopg":
            raise RuntimeError("DATABASE_URL must use PostgreSQL; SQLite is not supported")
        if not url.database or "CHANGE_ME" in self.database_url:
            raise RuntimeError("DATABASE_URL must specify a configured database")

    def validate_auth_secrets(self) -> None:
        if len(self.jwt_secret) < 32 or "CHANGE_ME" in self.jwt_secret:
            raise RuntimeError("JWT_SECRET must be a locally generated secret of at least 32 characters")
        try:
            Fernet(self.token_encryption_key.encode("utf-8"))
        except (ValueError, TypeError):
            raise RuntimeError("TOKEN_ENCRYPTION_KEY must be a valid Fernet key") from None


@lru_cache
def get_settings() -> Settings:
    return Settings()
