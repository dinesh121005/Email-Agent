import json
import re

from pydantic import ValidationError

from schemas import EmailAnalysis


class EmailAnalysisParseError(ValueError):
    """Raised when model output cannot become a valid email analysis."""
    pass


def parse_email_analysis(raw_text: str) -> EmailAnalysis:
    if not raw_text.strip():
        raise EmailAnalysisParseError("Model output must not be empty")

    cleaned = raw_text.strip()

    # Strip thinking tags from reasoning models (e.g. Qwen3) if present
    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", cleaned).strip()

    # If wrapped in markdown code fence
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned)
    if fence_match:
        cleaned = fence_match.group(1).strip()
    else:
        # Extract from first { to last } if surrounding commentary exists
        first_brace = cleaned.find("{")
        last_brace = cleaned.rfind("}")
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            cleaned = cleaned[first_brace : last_brace + 1].strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise EmailAnalysisParseError(
            "Model output is not valid JSON"
        ) from exc

    if not isinstance(data, dict):
        raise EmailAnalysisParseError(
            "Model output must be a JSON object"
        )

    try:
        return EmailAnalysis.model_validate(data)
    except ValidationError as exc:
        raise EmailAnalysisParseError(
            f"Model output does not match EmailAnalysis: {exc}"
        ) from exc
