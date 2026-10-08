import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import Uuid


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    login_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="USER")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class RefreshSession(Base):
    __tablename__ = "refresh_sessions"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TermsVersion(Base):
    __tablename__ = "terms_versions"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    version: Mapped[str] = mapped_column(String(50), unique=True)
    content: Mapped[str] = mapped_column(Text)
    content_sha256: Mapped[str] = mapped_column(String(64))
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TermsAcceptance(Base):
    __tablename__ = "terms_acceptances"
    __table_args__ = (UniqueConstraint("user_id", "terms_version_id", name="uq_terms_user_version"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    terms_version_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("terms_versions.id", ondelete="RESTRICT"))
    content_sha256: Mapped[str] = mapped_column(String(64))
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Device(Base):
    __tablename__ = "devices"
    __table_args__ = (UniqueConstraint("user_id", "token_hash", name="uq_device_user_token"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    token_ciphertext: Mapped[str] = mapped_column(Text)
    platform: Mapped[str] = mapped_column(String(20), default="ANDROID")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AnalysisJob(Base):
    __tablename__ = "analysis_jobs"
    __table_args__ = (UniqueConstraint("owner_id", "idempotency_key", name="uq_job_owner_key"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    status: Mapped[str] = mapped_column(String(30), default="QUEUED", index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    input_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decision: Mapped[str | None] = mapped_column(String(40), nullable=True)
    input_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    incident_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Incident(Base):
    __tablename__ = "incidents"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("analysis_jobs.id", ondelete="SET NULL"), nullable=True)
    detector_run_id: Mapped[str | None] = mapped_column(String(200), nullable=True, unique=True)
    status: Mapped[str] = mapped_column(String(30), default="UNREAD", index=True)
    safe_summary: Mapped[str] = mapped_column(Text)
    result_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NotificationOutbox(Base):
    __tablename__ = "notification_outbox"
    __table_args__ = (Index("ix_outbox_incident_device", "incident_id", "device_id", unique=True),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    incident_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("incidents.id", ondelete="CASCADE"))
    device_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("devices.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NotificationAttempt(Base):
    __tablename__ = "notification_attempts"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    outbox_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("notification_outbox.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20))
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserAction(Base):
    __tablename__ = "user_actions"
    __table_args__ = (UniqueConstraint("user_id", "idempotency_key", name="uq_user_action_idempotency"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    incident_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(40))
    call_method: Mapped[str | None] = mapped_column(String(40), nullable=True)
    location_consent: Mapped[bool] = mapped_column(Boolean, default=False)
    location_result_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    location_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
