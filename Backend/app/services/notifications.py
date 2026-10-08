from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Device, Incident, NotificationAttempt, NotificationOutbox
from ..models import User
from ..config import get_settings
from ..security import decrypt_device_token


class NotificationSender(Protocol):
    def send(self, *, token: str, incident_id: UUID, safe_summary: str, occurred_at: datetime | None) -> str:
        ...


@dataclass
class FakeNotificationSender:
    """Deterministic sender for local tests; it never performs network I/O."""

    sent: list[dict]

    def send(self, *, token: str, incident_id: UUID, safe_summary: str, occurred_at: datetime | None) -> str:
        message_id = f"fake-{incident_id.hex[:20]}-{len(self.sent) + 1}"
        self.sent.append({"message_id": message_id, "incident_id": str(incident_id), "safe_summary": safe_summary})
        return message_id


def enqueue_incident_notifications(db: Session, incident: Incident) -> int:
    devices = list(db.scalars(select(Device).where(Device.user_id == incident.owner_id, Device.is_active.is_(True))))
    added = 0
    for device in devices:
        existing = db.scalar(select(NotificationOutbox).where(NotificationOutbox.incident_id == incident.id, NotificationOutbox.device_id == device.id))
        if existing is None:
            db.add(NotificationOutbox(incident_id=incident.id, device_id=device.id))
            added += 1
    return added


def _is_unregistered(error: Exception) -> bool:
    """True when the provider says this token no longer exists.

    FCM reports a token from an uninstalled or reinstalled app as
    UnregisteredError / NotFoundError. Retrying such a token can never succeed,
    so the device is deactivated instead of consuming every retry budget.
    The class is matched by name so an optional dependency is not imported here.
    """
    return type(error).__name__ in {"UnregisteredError", "NotFoundError"}


def dispatch_pending(db: Session, sender: NotificationSender, limit: int = 50) -> int:
    rows = list(db.scalars(select(NotificationOutbox).where(NotificationOutbox.status == "PENDING", NotificationOutbox.next_attempt_at <= datetime.now(timezone.utc)).order_by(NotificationOutbox.created_at).limit(limit).with_for_update(skip_locked=True)))
    delivered = 0
    for outbox in rows:
        device = db.get(Device, outbox.device_id)
        incident = db.get(Incident, outbox.incident_id)
        owner = db.get(User, incident.owner_id) if incident is not None else None
        if device is None or incident is None or not device.is_active or device.user_id != incident.owner_id or owner is None or not owner.is_active:
            outbox.status = "SKIPPED"
            continue
        outbox.attempts += 1
        try:
            message_id = sender.send(token=decrypt_device_token(device.token_ciphertext), incident_id=incident.id, safe_summary=incident.safe_summary, occurred_at=incident.occurred_at)
        except Exception as error:
            if _is_unregistered(error):
                # Dead token: stop trying, and let the handset re-register.
                device.is_active = False
                outbox.status = "SKIPPED"
                db.add(NotificationAttempt(outbox_id=outbox.id, status="SKIPPED", error_code=type(error).__name__))
                continue
            max_attempts = get_settings().notification_max_attempts
            outbox.status = "FAILED" if outbox.attempts >= max_attempts else "PENDING"
            outbox.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=min(3600, 2 ** outbox.attempts))
            db.add(NotificationAttempt(outbox_id=outbox.id, status="FAILED", error_code=type(error).__name__))
            continue
        outbox.status = "SIMULATED" if isinstance(sender, FakeNotificationSender) else "SENT"
        db.add(NotificationAttempt(outbox_id=outbox.id, status=outbox.status, provider_message_id=message_id))
        delivered += 1
    db.commit()
    return delivered


class FirebaseNotificationSender:
    """Lazy Firebase Admin sender; CLI login is not an Admin credential."""

    def __init__(self):
        import firebase_admin
        from firebase_admin import credentials
        from pathlib import Path

        settings = get_settings()
        if settings.fcm_mode != "firebase":
            raise RuntimeError("FCM_MODE=firebase must be explicitly configured to send")
        try:
            self.application = firebase_admin.get_app("crashpulse")
        except ValueError:
            credential = credentials.Certificate(str(Path(settings.firebase_credentials_file).resolve())) if settings.firebase_credentials_file else credentials.ApplicationDefault()
            self.application = firebase_admin.initialize_app(credential, {"projectId": settings.firebase_project_id}, name="crashpulse")

    def send(self, *, token: str, incident_id: UUID, safe_summary: str, occurred_at: datetime | None) -> str:
        from firebase_admin import messaging

        # Persisted incident ID is the client dedupe key. This is data-only and
        # creates no backend-triggered emergency action. FCM remains best-effort.
        message = messaging.Message(token=token, data={
            "incident_id": str(incident_id), "delivery_key": str(incident_id),
            "event_type": "incident_temporal_confirmation",
            "timestamp": (occurred_at or datetime.now(timezone.utc)).isoformat(),
            "title": "CrashPulse: review required", "body": "A detector alert is available. Open CrashPulse to review.",
        }, android=messaging.AndroidConfig(priority="high", ttl=timedelta(minutes=5)))
        return messaging.send(message, app=self.application)
