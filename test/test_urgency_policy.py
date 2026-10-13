from datetime import datetime, timedelta, timezone

import pytest

from urgency_policy import determine_urgency


def test_no_action_required_always_low():
    received = datetime(2026, 10, 14, 10, 0, tzinfo=timezone.utc)
    imminent_deadline = received + timedelta(hours=2)

    assert determine_urgency(requires_action=False, deadline=imminent_deadline, received_at=received) == "low"
    assert determine_urgency(requires_action=False, deadline=None, received_at=received) == "low"


def test_action_required_without_deadline_is_normal():
    received = datetime(2026, 10, 14, 10, 0, tzinfo=timezone.utc)

    assert determine_urgency(requires_action=True, deadline=None, received_at=received) == "normal"


def test_deadline_within_24_hours_is_urgent():
    received = datetime(2026, 10, 14, 10, 0, tzinfo=timezone.utc)
    deadline_24h = received + timedelta(hours=24)
    deadline_5h = received + timedelta(hours=5)
    overdue = received - timedelta(hours=1)

    assert determine_urgency(requires_action=True, deadline=deadline_24h, received_at=received) == "urgent"
    assert determine_urgency(requires_action=True, deadline=deadline_5h, received_at=received) == "urgent"
    assert determine_urgency(requires_action=True, deadline=overdue, received_at=received) == "urgent"


def test_deadline_between_24_and_72_hours_is_high():
    received = datetime(2026, 10, 14, 10, 0, tzinfo=timezone.utc)
    deadline_25h = received + timedelta(hours=25)
    deadline_72h = received + timedelta(hours=72)

    assert determine_urgency(requires_action=True, deadline=deadline_25h, received_at=received) == "high"
    assert determine_urgency(requires_action=True, deadline=deadline_72h, received_at=received) == "high"


def test_deadline_beyond_72_hours_is_normal():
    received = datetime(2026, 10, 14, 10, 0, tzinfo=timezone.utc)
    deadline_73h = received + timedelta(hours=73)

    assert determine_urgency(requires_action=True, deadline=deadline_73h, received_at=received) == "normal"
