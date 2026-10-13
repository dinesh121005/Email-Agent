import pytest
from pydantic import ValidationError

from schemas import EmailAnalysis


def test_valid_analysis_is_accepted():
    data = {
        "category": "hackathon",
        "urgency": "high",
        "summary": "Hackathon registration closes tomorrow.",
        "deadline": "tomorrow at 5 PM",
        "requires_action": True,
    }

    analysis = EmailAnalysis(**data)

    assert analysis.category == "hackathon"
    assert analysis.urgency == "high"
    assert analysis.summary == "Hackathon registration closes tomorrow."
    assert analysis.deadline == "tomorrow at 5 PM"
    assert analysis.requires_action is True


def test_unsupported_category_is_rejected():
    with pytest.raises(ValidationError):
        EmailAnalysis(
            category="personal",
            urgency="normal",
            summary="Catch up this weekend",
            requires_action=False,
        )


def test_unsupported_urgency_is_rejected():
    with pytest.raises(ValidationError):
        EmailAnalysis(
            category="job",
            urgency="critical",
            summary="Interview invitation",
            requires_action=True,
        )


def test_missing_optional_deadline_defaults_to_none():
    analysis = EmailAnalysis(
        category="competition",
        urgency="low",
        summary="Annual chess tournament",
        requires_action=False,
    )

    assert analysis.deadline is None


def test_missing_required_field_is_rejected():
    with pytest.raises(ValidationError):
        EmailAnalysis(
            category="job",
            urgency="normal",
            # missing summary and requires_action
        )
