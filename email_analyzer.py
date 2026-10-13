from datetime import datetime, timezone

from analysis_parser import parse_email_analysis
from deadline_normalizer import normalize_deadline
from llm_client import OllamaClient
from schemas import EmailAnalysis
from urgency_policy import determine_urgency, evaluate_priority_and_safety

SYSTEM_INSTRUCTIONS = """You are an expert personal email intelligence assistant.
Analyze the email using only verifiable facts from the subject and body.

Determine:
1. intent: one of:
   - "interview_invitation": genuine interview invitation, screening, or interview round
   - "interview_scheduling": request to select or confirm an interview slot
   - "interview_confirmation": interview confirmation or scheduled meeting details
   - "application_update": job application status, rejection, or progression
   - "job_opportunity": genuine personal outreach from a recruiter for a specific role
   - "hackathon": genuine hackathon registration or event
   - "competition": genuine coding/tech competition
   - "promotional": job search tips, profile view marketing, job boards, course ads, tracking tools
   - "newsletter": general tech or news digest
   - "other": transactional, personal, or administrative
   - "uncertain": ambiguous message where intent cannot be verified with certainty

2. importance: "critical", "high", "normal", "low"
3. urgency: "urgent", "high", "normal", "low"
4. spam_status: "not_spam", "suspected_spam", "confirmed_spam"
5. requires_action: boolean
6. summary: 1-2 concise, factual sentences explaining the context.
7. deadline: verbatim or explicit deadline string, or null.
8. recommended_action: clear recommended next step for the user, or null.
9. priority_reason: concise explanation of why this email was given this priority.
10. evidence: array of 1 to 3 short literal excerpts from the email.

CRITICAL RULES:
- Job-related does NOT automatically mean important. A genuine interview invitation is high importance. An advertisement for job tracking, profile views, subscription services, or bulk alerts is promotional noise (intent: promotional, spam_status: suspected_spam, requires_action: false, importance: low).
- Never fabricate deadlines or meeting times. If none is stated, use null.
- Output ONLY a single JSON object. No Markdown fences, no surrounding commentary."""


def analyze_email(
    subject: str,
    body: str,
    client: OllamaClient | None = None,
    received_at: datetime | None = None,
    max_retries: int = 2,
) -> EmailAnalysis:
    if not subject.strip() and not body.strip():
        raise ValueError("Email subject and body cannot both be empty")

    if client is None:
        client = OllamaClient()

    if received_at is None:
        received_at = datetime.now(timezone.utc)

    cleaned_body = "\n".join(line.strip() for line in body.splitlines() if line.strip())
    if len(cleaned_body) > 3500:
        cleaned_body = cleaned_body[:3500] + "\n[... message content clipped ...]"

    current_prompt = (
        f"{SYSTEM_INSTRUCTIONS}\n\n"
        f"Subject: {subject}\n"
        f"Body:\n{cleaned_body}\n"
    )

    last_error: Exception | None = None
    analysis: EmailAnalysis | None = None

    # Agent self-correction loop: if schema validation or JSON decoding fails,
    # feed the validation error back to the LLM to self-correct on the next iteration.
    for attempt in range(max_retries + 1):
        raw_response = client.generate(current_prompt)
        try:
            analysis = parse_email_analysis(raw_response)
            break
        except Exception as exc:
            last_error = exc
            if attempt < max_retries:
                current_prompt = (
                    f"{current_prompt}\n\n"
                    f"CRITICAL FORMATTING ERROR IN PREVIOUS ATTEMPT:\n{exc}\n\n"
                    f"Please self-correct: output ONLY the single, valid JSON object matching the required schema."
                )

    if analysis is None:
        assert last_error is not None
        raise last_error

    # Determine urgency and apply deterministic safety & priority policy
    normalized_deadline = normalize_deadline(analysis.deadline, received_at)
    return evaluate_priority_and_safety(
        analysis=analysis,
        subject=subject,
        body=body,
        received_at=received_at,
        normalized_deadline=normalized_deadline,
    )

