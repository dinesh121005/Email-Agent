from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

CategoryType = Literal["job", "hackathon", "competition", "other", "uncertain"]
UrgencyType = Literal["urgent", "high", "normal", "low"]
ImportanceType = Literal["critical", "high", "normal", "low"]
IntentType = Literal[
    "interview_invitation",
    "interview_scheduling",
    "interview_confirmation",
    "application_update",
    "job_opportunity",
    "hackathon",
    "competition",
    "promotional",
    "newsletter",
    "other",
    "uncertain",
]
SpamStatusType = Literal["not_spam", "suspected_spam", "confirmed_spam"]

INTENT_TO_CATEGORY: dict[str, CategoryType] = {
    "interview_invitation": "job",
    "interview_scheduling": "job",
    "interview_confirmation": "job",
    "application_update": "job",
    "job_opportunity": "job",
    "hackathon": "hackathon",
    "competition": "competition",
    "promotional": "other",
    "newsletter": "other",
    "other": "other",
    "uncertain": "uncertain",
}

CATEGORY_TO_INTENT: dict[str, IntentType] = {
    "job": "job_opportunity",
    "hackathon": "hackathon",
    "competition": "competition",
    "other": "other",
    "uncertain": "uncertain",
}


class EmailAnalysis(BaseModel):
    category: CategoryType
    urgency: UrgencyType
    summary: str
    deadline: str | None = None
    requires_action: bool

    intent: IntentType = "other"
    importance: ImportanceType = "normal"
    spam_status: SpamStatusType = "not_spam"
    recommended_action: str | None = None
    priority_reason: str | None = None
    evidence: list[str] = Field(default_factory=list)
    analysis_version: str = "2.0.0"

    @model_validator(mode="before")
    @classmethod
    def _validate_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        category = data.get("category")
        intent = data.get("intent")

        if category and not intent:
            if category in CATEGORY_TO_INTENT:
                data["intent"] = CATEGORY_TO_INTENT[category]
        elif intent and not category:
            if intent in INTENT_TO_CATEGORY:
                data["category"] = INTENT_TO_CATEGORY[intent]

        ev = data.get("evidence")
        if isinstance(ev, str):
            data["evidence"] = [ev] if ev.strip() else []
        elif ev is None:
            data["evidence"] = []

        return data

