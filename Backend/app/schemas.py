import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    login_id: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=256, repr=False)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=20, max_length=500, repr=False)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    login_id: str
    role: str
    is_active: bool
    created_at: datetime


class CreateUserRequest(BaseModel):
    login_id: str = Field(min_length=3, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,99}$")
    password: str = Field(min_length=12, max_length=256, repr=False)


class DeviceRequest(BaseModel):
    token: str = Field(min_length=20, max_length=4096, repr=False)
    platform: Literal["ANDROID"] = "ANDROID"


class DeviceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    platform: str
    is_active: bool
    last_seen_at: datetime


class TermsResponse(BaseModel):
    version: str
    content: str
    content_sha256: str
    is_accepted: bool = False


class AcceptanceResponse(BaseModel):
    accepted: bool
    version: str
    accepted_at: datetime


class AcceptTermsRequest(BaseModel):
    version: str = Field(min_length=1, max_length=50)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class HealthResponse(BaseModel):
    status: Literal["ok", "not_ready"]
    database: Literal["ok", "unavailable"] | None = None


class JobResponse(BaseModel):
    id: uuid.UUID
    status: str
    error_message: str | None = None
    incident_id: uuid.UUID | None = None
    created_at: datetime
    completed_at: datetime | None = None
    decision: str | None = None


class PhoneLocationRequest(BaseModel):
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    accuracy_meters: float = Field(ge=0, le=100000, allow_inf_nan=False)
    captured_at: datetime
    consent: Literal[True]


class IncidentResponse(BaseModel):
    id: uuid.UUID
    status: str
    safe_summary: str
    occurred_at: datetime | None
    created_at: datetime
    detector_run_id: str | None
    result_available: bool
    evidence_available: bool


class IncidentDetailResponse(IncidentResponse):
    job_id: uuid.UUID | None
    user_actions: list[dict]


class IncidentActionRequest(BaseModel):
    action: Literal["IGNORED", "MARKED_REVIEWED", "PROCEEDED"]
    call_method: Literal["DIALER", "CALL_NOW"] | None = None
    location_consent: bool = False


class LocationAttachmentRequest(BaseModel):
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    accuracy_meters: float = Field(ge=0, le=100000, allow_inf_nan=False)
    captured_at: datetime
    consent: Literal[True]


class ActionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    incident_id: uuid.UUID
    action: str
    call_method: str | None
    location_consent: bool
    created_at: datetime
