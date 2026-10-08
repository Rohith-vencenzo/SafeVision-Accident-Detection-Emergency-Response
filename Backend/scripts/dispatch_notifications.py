"""Operator-triggered outbox delivery after explicit authorized test approval."""
from app.config import get_settings
from app.db import get_session_factory
from app.services.notifications import FirebaseNotificationSender, dispatch_pending
from scripts.common import run_operator


def main() -> None:
    if get_settings().fcm_mode != "firebase":
        raise SystemExit("FCM is disabled. Configure authorized local Admin credentials before sending.")
    sender = FirebaseNotificationSender()
    with get_session_factory()() as db:
        print(f"FCM requests accepted by provider: {dispatch_pending(db, sender)}")
    # Provider acceptance is not device delivery, alarm display, or emergency action.


if __name__ == "__main__":
    run_operator(main)
