"""Team-wide alerting: every active field reviewer is paged, not just the owner.

These are pure unit tests with mocked sessions, so they run without PostgreSQL.
The SQL predicates are asserted on the generated statement text because that is
the part a mock cannot otherwise verify.
"""
import uuid
from unittest.mock import MagicMock

from app.models import Incident, NotificationOutbox, User
from app.services.notifications import enqueue_incident_notifications


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