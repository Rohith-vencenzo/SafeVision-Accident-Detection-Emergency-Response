"""Team-wide alerting: every active field reviewer is paged, not just the owner.

These are pure unit tests with mocked sessions, so they run without PostgreSQL.
The SQL predicates are asserted on the generated statement text because that is
the part a mock cannot otherwise verify.
"""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from app.models import Device, Incident, NotificationOutbox, User
from app.security import encrypt_device_token
from app.services.notifications import dispatch_pending, enqueue_incident_notifications


def _incident() -> Incident:
    return Incident(id=uuid.uuid4(), owner_id=uuid.uuid4(), status="UNREAD", safe_summary="Temporal detector confirmation requires human review.")


def test_alert_fans_out_to_every_active_reviewer_device():
    reviewer_a = User(id=uuid.uuid4(), login_id="alice", role="USER", is_active=True)
    reviewer_b = User(id=uuid.uuid4(), login_id="bob", role="USER", is_active=True)
    device_a = MagicMock(id=uuid.uuid4(), user_id=reviewer_a.id, is_active=True)
    device_b = MagicMock(id=uuid.uuid4(), user_id=reviewer_b.id, is_active=True)

    db = MagicMock()
    db.scalars.side_effect = [[reviewer_a, reviewer_b], [device_a, device_b]]
    db.scalar.return_value = None  # no existing outbox row

    added = enqueue_incident_notifications(db, _incident())

    assert added == 2
    queued = {call.args[0].device_id for call in db.add.call_args_list if isinstance(call.args[0], NotificationOutbox)}
    assert queued == {device_a.id, device_b.id}


def test_a_second_reviewer_receives_the_alert_even_when_not_the_owner():
    owner = User(id=uuid.uuid4(), login_id="owner", role="USER", is_active=True)
    other = User(id=uuid.uuid4(), login_id="other", role="USER", is_active=True)
    owner_device = MagicMock(id=uuid.uuid4(), user_id=owner.id, is_active=True)
    other_device = MagicMock(id=uuid.uuid4(), user_id=other.id, is_active=True)

    incident = _incident()
    incident.owner_id = owner.id

    db = MagicMock()
    db.scalars.side_effect = [[owner, other], [owner_device, other_device]]
    db.scalar.return_value = None

    assert enqueue_incident_notifications(db, incident) == 2


def test_admin_accounts_are_excluded_from_the_alert_set():
    db = MagicMock()
    db.scalars.side_effect = [[], []]

    enqueue_incident_notifications(db, _incident())

    recipients_query = str(db.scalars.call_args_list[0].args[0])
    assert 'users.is_active IS true' in recipients_query.lower() or "users.is_active IS true" in recipients_query
    assert "users.role !=" in recipients_query or "users.role <>" in recipients_query
    # Nothing queued because there were no eligible reviewers.
    assert db.add.call_count == 0


def test_no_recipients_is_not_an_error():
    db = MagicMock()
    db.scalars.side_effect = [[], []]
    assert enqueue_incident_notifications(db, _incident()) == 0


def test_existing_queue_row_is_not_duplicated():
    reviewer = User(id=uuid.uuid4(), login_id="alice", role="USER", is_active=True)
    device = MagicMock(id=uuid.uuid4(), user_id=reviewer.id, is_active=True)

    db = MagicMock()
    db.scalars.side_effect = [[reviewer], [device]]
    db.scalar.return_value = object()  # an outbox row already exists

    assert enqueue_incident_notifications(db, _incident()) == 0
    assert db.add.call_count == 0

# ---------------------------------------------------------------------------
# Dispatch-time guard.
#
# Regression coverage: an earlier version re-checked `device.user_id ==
# incident.owner_id` here. That made every queued alert to a second reviewer
# SKIPP with zero attempts, so the handset was never contacted even though the
# outbox row existed. These tests drive the real dispatch loop.
# ---------------------------------------------------------------------------


class _Outbox:
    def __init__(self, incident_id, device_id):
        self.id = uuid.uuid4()
        self.incident_id = incident_id
        self.device_id = device_id
        self.status = "PENDING"
        self.attempts = 0
        self.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)


def _dispatch_db(outbox, device, recipient):
    db = MagicMock()
    db.scalars.return_value = [outbox]
    db.get.side_effect = lambda model, key: {
        Device: device,
        Incident: _incident(),
        User: recipient,
    }.get(model)
    db.commit = MagicMock()
    return db


def test_dispatch_sends_to_a_reviewer_who_does_not_own_the_incident():
    """The regression: a non-owner reviewer must still be sent the alert."""
    owner = User(id=uuid.uuid4(), login_id="owner", role="USER", is_active=True)
    reviewer = User(id=uuid.uuid4(), login_id="reviewer", role="USER", is_active=True)
    device = MagicMock(id=uuid.uuid4(), user_id=reviewer.id, is_active=True)
    device.token_ciphertext = encrypt_device_token('valid-fcm-token')[1]
    incident_id, device_id = uuid.uuid4(), device.id
    db = _dispatch_db(_Outbox(incident_id, device_id), device, reviewer)

    sent = []

    class Sender:
        def send(self, *, token, incident_id, safe_summary, occurred_at):
            sent.append(device_id)
            return "provider-id"

    delivered = dispatch_pending(db, Sender())

    assert delivered == 1, "a non-owner reviewer must not be skipped"
    assert sent == [device_id]


def test_dispatch_skips_a_deactivated_device():
    owner = User(id=uuid.uuid4(), login_id="owner", role="USER", is_active=True)
    reviewer = User(id=uuid.uuid4(), login_id="reviewer", role="USER", is_active=True)
    device = MagicMock(id=uuid.uuid4(), user_id=reviewer.id, is_active=False)
    outbox = _Outbox(uuid.uuid4(), device.id)
    db = _dispatch_db(outbox, device, reviewer)

    class Sender:
        def send(self, **kwargs):
            raise AssertionError("must not attempt a deactivated device")

    assert dispatch_pending(db, Sender()) == 0
    assert outbox.status == "SKIPPED"


def test_dispatch_skips_when_the_recipient_account_is_disabled():
    reviewer = User(id=uuid.uuid4(), login_id="reviewer", role="USER", is_active=False)
    device = MagicMock(id=uuid.uuid4(), user_id=reviewer.id, is_active=True)
    outbox = _Outbox(uuid.uuid4(), device.id)
    db = _dispatch_db(outbox, device, reviewer)

    class Sender:
        def send(self, **kwargs):
            raise AssertionError("must not attempt a disabled account")

    assert dispatch_pending(db, Sender()) == 0
    assert outbox.status == "SKIPPED"
