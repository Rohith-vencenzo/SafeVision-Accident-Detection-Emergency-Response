"""Background outbox delivery.

``scripts.dispatch_notifications`` is operator-triggered only. A phone alert is
useless unless delivery also happens while the server is simply running, so the
API owns one dispatcher thread for the lifetime of the process.

Provider acceptance is not device delivery, alarm display, or emergency action.
FCM remains best effort.
"""

from threading import Event, Thread

from sqlalchemy.exc import SQLAlchemyError

from ..config import get_settings
from ..db import get_session_factory
from .notifications import FirebaseNotificationSender, dispatch_pending


class NotificationDispatcher:
    """Single bounded delivery thread; disabled unless FCM_MODE=firebase."""

    def __init__(self) -> None:
        self.stopping = Event()
        self.thread: Thread | None = None

    @property
    def enabled(self) -> bool:
        return get_settings().fcm_mode == "firebase"

    def start(self) -> None:
        if not self.enabled:
            # Leave the outbox intact and PENDING so nothing is silently dropped.
            return
        # Constructed once: initialising the Firebase Admin app is expensive and
        # raises if the local credential is missing, which should surface here
        # rather than on the first alert.
        self._sender = FirebaseNotificationSender()
        self.thread = Thread(target=self.run, name="crashpulse-dispatch", daemon=True)
        self.thread.start()

    def run(self) -> None:
        interval = max(1, get_settings().notification_dispatch_interval_seconds)
        while not self.stopping.is_set():
            try:
                with get_session_factory()() as db:
                    dispatch_pending(db, self._sender)
            except SQLAlchemyError:
                # Durable PENDING rows are retried when PostgreSQL recovers.
                pass
            self.stopping.wait(interval)

    def stop(self) -> None:
        self.stopping.set()
        if self.thread:
            self.thread.join(timeout=5)