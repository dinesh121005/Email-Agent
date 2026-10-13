from datetime import datetime, timedelta, timezone
import pytest

from email_analyzer import analyze_email
from llm_client import OllamaClientError
from schemas import EmailAnalysis


class FakeClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.last_prompt: str | None = None

    def generate(self, prompt: str) -> str:
        self.last_prompt = prompt
        return self.response


def test_analyze_email_returns_validated_analysis():
    client = FakeClient(
        """
        {
            "category": "hackathon",
            "urgency": "urgent",
            "summary": "Complete hackathon registration.",
            "deadline": "tomorrow at 5 PM",
            "requires_action": true
        }
        """
    )
    result = analyze_email(
        subject="Hackathon registration closes tomorrow",
        body="Complete your registration before 5 PM tomorrow.",
        client=client,
    )
    assert isinstance(result, EmailAnalysis)
    assert result.category == "hackathon"
    assert result.requires_action is True
    assert "Hackathon registration closes tomorrow" in client.last_prompt
    assert "before 5 PM tomorrow" in client.last_prompt


def test_analyze_email_rejects_empty_email():
    with pytest.raises(
        ValueError,
        match="cannot both be empty",
    ):
        analyze_email("", "", client=FakeClient("{}"))


def test_analyze_email_propagates_invalid_model_output():
    client = FakeClient("This is not JSON")
    with pytest.raises(ValueError, match="not valid JSON"):
        analyze_email("Job interview", "Interview tomorrow", client=client)


def test_analyze_email_propagates_client_failure():
    class FailingClient:
        def generate(self, prompt: str) -> str:
            raise OllamaClientError("Connection failed", retryable=True)

    with pytest.raises(OllamaClientError, match="Connection failed") as exc_info:
        analyze_email("Job interview", "Interview tomorrow", client=FailingClient())
    assert exc_info.value.retryable is True


def test_analyze_email_applies_deterministic_urgency_override():
    # Model incorrectly outputs "urgent" for a deadline 4 days out
    client = FakeClient(
        """
        {
            "category": "job",
            "urgency": "urgent",
            "summary": "Interview invitation",
            "deadline": "2026-10-20T10:00:00Z",
            "requires_action": true
        }
        """
    )
    received = datetime(2026, 10, 15, 10, 0, tzinfo=timezone.utc)
    result = analyze_email(
        subject="Interview",
        body="Interview details",
        client=client,
        received_at=received,
    )
    # 5 days (120h) away -> policy overrides model's "urgent" to "normal"
    assert result.urgency == "normal"


def test_analyze_email_non_actionable_always_low():
    # Model incorrectly outputs "normal" for a newsletter
    client = FakeClient(
        """
        {
            "category": "other",
            "urgency": "normal",
            "summary": "Newsletter issue",
            "deadline": null,
            "requires_action": false
        }
        """
    )
    result = analyze_email(
        subject="Newsletter",
        body="Reading content",
        client=client,
    )
    assert result.urgency == "low"


def test_analyze_email_recovers_via_agent_loop():
    class SelfCorrectingClient:
        def __init__(self):
            self.calls = 0

        def generate(self, prompt: str) -> str:
            self.calls += 1
            if self.calls == 1:
                # First attempt: invalid syntax
                return "Here is the result: { invalid json ... }"
            # Second attempt: valid self-corrected JSON
            return """
            {
                "category": "hackathon",
                "urgency": "urgent",
                "summary": "Self-corrected hackathon analysis.",
                "deadline": null,
                "requires_action": true
            }
            """

    client = SelfCorrectingClient()
    result = analyze_email(
        subject="Hackathon update",
        body="Hackathon details",
        client=client,
        max_retries=2,
    )
    assert client.calls == 2
    assert isinstance(result, EmailAnalysis)
    assert result.category == "hackathon"

