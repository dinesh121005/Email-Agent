import pytest

from analysis_parser import EmailAnalysisParseError, parse_email_analysis
from schemas import EmailAnalysis


def test_valid_json_parses_into_email_analysis():
    raw = """
    {
        "category": "hackathon",
        "urgency": "high",
        "summary": "Hackathon registration closes tomorrow.",
        "deadline": "tomorrow at 5 PM",
        "requires_action": true
    }
    """
    analysis = parse_email_analysis(raw)

    assert isinstance(analysis, EmailAnalysis)
    assert analysis.category == "hackathon"
    assert analysis.urgency == "high"
    assert analysis.summary == "Hackathon registration closes tomorrow."
    assert analysis.deadline == "tomorrow at 5 PM"
    assert analysis.requires_action is True


def test_invalid_json_is_rejected():
    with pytest.raises(
        EmailAnalysisParseError,
        match="not valid JSON",
    ):
        parse_email_analysis('{"category": "hackathon"')


def test_non_object_json_is_rejected():
    with pytest.raises(
        EmailAnalysisParseError,
        match="JSON object",
    ):
        parse_email_analysis('["hackathon", "urgent"]')


def test_unsupported_category_is_rejected():
    raw = """
    {
        "category": "personal",
        "urgency": "normal",
        "summary": "Personal email",
        "deadline": null,
        "requires_action": false
    }
    """
    with pytest.raises(
        EmailAnalysisParseError,
        match="does not match EmailAnalysis",
    ):
        parse_email_analysis(raw)


def test_missing_required_field_is_rejected():
    raw = """
    {
        "category": "job",
        "urgency": "high",
        "summary": "Interview invitation"
    }
    """
    with pytest.raises(
        EmailAnalysisParseError,
        match="does not match EmailAnalysis",
    ):
        parse_email_analysis(raw)


def test_empty_output_is_rejected():
    with pytest.raises(
        EmailAnalysisParseError,
        match="must not be empty",
    ):
        parse_email_analysis("   ")
