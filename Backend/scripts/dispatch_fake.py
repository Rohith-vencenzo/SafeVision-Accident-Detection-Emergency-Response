"""Dispatch pending outbox rows through the no-network fake sender."""
from app.db import get_session_factory
from app.services.notifications import FakeNotificationSender, dispatch_pending
from scripts.common import run_operator


def main() -> None:
    sender = FakeNotificationSender(sent=[])
    with get_session_factory()() as db:
        delivered = dispatch_pending(db, sender)
    print(f"fake notification deliveries recorded: {delivered}")


if __name__ == "__main__":
    run_operator(main)
