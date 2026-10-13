import re
from datetime import datetime
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from schemas import EmailAnalysis

UrgencyLevel = Literal["urgent", "high", "normal", "low"]
ImportanceLevel = Literal["critical", "high", "normal", "low"]


def determine_urgency(
    requires_action: bool,
    deadline: datetime | None,
    received_at: datetime,
) -> UrgencyLevel:
    """Determine urgency deterministically based on action requirement and deadline.

    Policy:
    - low: no action required.
    - normal: action required, but no known deadline OR deadline is beyond 72 hours.
    - high: action required, with a deadline between 24 and 72 hours away.
    - urgent: action required, with a deadline within 24 hours (or overdue).
    """
    if not requires_action:
        return "low"

    if deadline is None:
        return "normal"

    # Ensure timezone awareness alignment
    if deadline.tzinfo is not None and received_at.tzinfo is None:
        received_at = received_at.replace(tzinfo=deadline.tzinfo)
    elif deadline.tzinfo is None and received_at.tzinfo is not None:
        deadline = deadline.replace(tzinfo=received_at.tzinfo)

    diff = (deadline - received_at).total_seconds()
    hours_left = diff / 3600.0

    if hours_left <= 24.0:
        return "urgent"
    if hours_left <= 72.0:
        return "high"
    return "normal"


# Promotional and marketing signals that indicate bulk or service advertising
PROMOTIONAL_PATTERNS = [
    r"profile\s+views",
    r"track\s+your\s+job\s+applications",
    r"job\s+applies\s+done",
    r"make\s+your\s+job\s+search\s+safer",
    r"finish\s+setting\s+up\s+your",
    r"going\s+live\s+at",
    r"felicitation\s+ceremony",
    r"security\s+alert",
    r"webinar",
    r"special\s+offer",
    r"discount",
    r"newsletter",
    r"weekly\s+digest",
    r"unsubscribe",
    r"jobs\s+you\s+may\s+be\s+interested\s+in",
    r"recommended\s+jobs\s+for\s+you",
    r"daily\s+job\s+alert",
]

# Authentic interview signals
INTERVIEW_PATTERNS = [
    r"invitation\s+to\s+interview",
    r"interview\s+invitation",
    r"interview\s+screening",
    r"technical\s+interview",
    r"schedule\s+(your\s+)?interview",
    r"interview\s+scheduled",
    r"shortlisted\s+for(\s+an?)?\s+interview",
    r"hackerrank\s+(test|screening|assessment)",
    r"coding\s+assessment",
    r"online\s+assessment",
]

# Completed interview / assessment signals
INTERVIEW_COMPLETED_PATTERNS = [
    r"interview\s+(is\s+)?complete",
    r"interview\s+has\s+been\s+submitted",
    r"assessment\s+(is\s+)?complete",
    r"assessment\s+has\s+been\s+submitted",
    r"test\s+has\s+been\s+submitted",
    r"test\s+(is\s+)?complete",
    r"thank\s+you\s+for\s+completing",
    r"thanks\s+for\s+completing",
    r"submission\s+(has\s+been\s+)?received",
    r"will\s+review\s+your\s+interview",
]

# Rejection signals
REJECTION_PATTERNS = [
    r"moved?\s+forward\s+with\s+another\s+candidate",
    r"decided\s+not\s+to\s+move\s+forward",
    r"will\s+not\s+be\s+moving\s+forward",
    r"not\s+selected",
    r"wish\s+you\s+(the\s+)?best\s+in\s+your\s+job\s+search",
    r"unfortunately,?\s+we",
]


def evaluate_priority_and_safety(
    analysis: "EmailAnalysis",
    subject: str,
    body: str,
    received_at: datetime,
    normalized_deadline: datetime | None = None,
) -> "EmailAnalysis":
    """Deterministic policy enforcing safety, spam suppression, and evidence-based priority.

    Rules:
    1. Completed interviews/assessments are high importance, no action required.
    2. Genuine upcoming interviews are high/critical importance, actionable, never spam.
    3. Promotional / marketing noise (job trackers, profile view upsells, alerts)
       is marked as promotional/suspected_spam and suppressed from the priority inbox.
    4. Rejections are marked low importance, no action required.
    5. Urgency is strictly computed from normalized deadline and action requirement.
    """
    text = f"{subject} {body}".lower()

    is_interview_completed = any(re.search(pat, text, re.IGNORECASE) for pat in INTERVIEW_COMPLETED_PATTERNS)
    is_interview = any(re.search(pat, text, re.IGNORECASE) for pat in INTERVIEW_PATTERNS) or (
        analysis.intent in ("interview_invitation", "interview_scheduling", "interview_confirmation")
    )
    is_rejection = any(re.search(pat, text, re.IGNORECASE) for pat in REJECTION_PATTERNS)
    is_promo = any(re.search(pat, text, re.IGNORECASE) for pat in PROMOTIONAL_PATTERNS) or (
        analysis.intent in ("promotional", "newsletter")
    )

    intent = analysis.intent
    importance: ImportanceLevel = analysis.importance
    spam_status = analysis.spam_status
    requires_action = analysis.requires_action
    priority_reason = analysis.priority_reason
    recommended_action = analysis.recommended_action
    evidence = list(analysis.evidence)

    # 1. Handle Completed Interviews / Assessments
    if is_interview_completed and not is_rejection:
        intent = "interview_confirmation"
        spam_status = "not_spam"
        requires_action = False
        importance = "high"
        priority_reason = (
            "Interview or technical assessment completed and submitted. Candidate action not required while awaiting review."
        )
        recommended_action = "No action needed — await review and next steps from hiring team."
        if not evidence and subject:
            evidence.append(f"Subject: {subject}")

    # 2. Handle Genuine Interviews (Upcoming / Action Required)
    elif is_interview and not is_rejection:
        intent = "interview_invitation" if intent not in (
            "interview_invitation", "interview_scheduling", "interview_confirmation"
        ) else intent
        spam_status = "not_spam"
        requires_action = True
        importance = "critical" if (normalized_deadline and (normalized_deadline - received_at).total_seconds() <= 86400) else "high"
        priority_reason = (
            "Verified interview communication requiring candidate preparation, scheduling, or attendance."
        )
        if not recommended_action:
            recommended_action = "Confirm interview slot or review preparation instructions."
        if not evidence and subject:
            evidence.append(f"Subject: {subject}")

    # 2. Handle Application Rejection
    elif is_rejection:
        intent = "application_update"
        spam_status = "not_spam"
        requires_action = False
        importance = "low"
        priority_reason = "Application status update (not selected); no candidate action required."
        recommended_action = "Archive or record status."

    # 3. Handle Promotional / Job-Service Ads (Check for Genuine RAG Job Match First)
    elif is_promo:
        from profile_rag import match_job_to_profile
        rag_match = match_job_to_profile(subject, body)
        # Only elevate if there is a concrete job opening/hiring signal, not a generic tracking summary
        has_job_opportunity_signal = any(
            k in text for k in ["hiring", "apply now", "job vacancy", "internship", "open role", "opening", "job alert:", "we are looking for", "hackathon"]
        ) and not any(k in text for k in ["job applies done", "track your job applications", "make your job search safer", "feedback on your naukri"])

        if rag_match.get("is_match") and has_job_opportunity_signal:
            matched_str = ", ".join(rag_match.get("matched_skills", [])[:4])
            intent = "job_opportunity" if "hackathon" not in text else "hackathon"
            spam_status = "not_spam"
            requires_action = True
            importance = "high"
            priority_reason = f"Verified job/opportunity matching your resume skills ({matched_str or 'AI/ML & Python Stack'})."
            recommended_action = f"Review role requirements matching your profile ({matched_str or 'AI/ML'}) and consider applying."
            if not evidence and subject:
                evidence.append(f"Subject: {subject}")
        else:
            intent = "promotional"
            spam_status = "suspected_spam"
            requires_action = False
            importance = "low"
            priority_reason = (
                "Promotional email or repetitive job service advertisement; suppressed from priority inbox."
            )
            recommended_action = "No action needed (promotional content)."

    # 4. Handle Uncertain Classifications
    elif intent == "uncertain":
        spam_status = "suspected_spam"
        importance = "normal"
        priority_reason = "Ambiguous message requiring human review."
        if not recommended_action:
            recommended_action = "Review message in Review Queue to confirm intent."

    # 5. Handle Hackathons / Competitions
    elif intent in ("hackathon", "competition"):
        spam_status = "not_spam"
        importance = "high"
        if not priority_reason:
            priority_reason = f"Verified {intent} opportunity with actionable timeline."

    # Compute deterministic urgency based on action and normalized deadline
    deterministic_urgency = determine_urgency(
        requires_action=requires_action,
        deadline=normalized_deadline,
        received_at=received_at,
    )

    # Urgent interview bonus: if interview is tomorrow/today, ensure urgent
    if is_interview and not is_interview_completed and normalized_deadline:
        hours_left = (normalized_deadline - received_at).total_seconds() / 3600.0
        if hours_left <= 24.0:
            deterministic_urgency = "urgent"
            importance = "critical"

    # Populate evidence from subject if empty
    if not evidence and subject.strip():
        evidence.append(f"Subject: {subject.strip()}")

    return analysis.model_copy(update={
        "intent": intent,
        "importance": importance,
        "urgency": deterministic_urgency,
        "spam_status": spam_status,
        "requires_action": requires_action,
        "priority_reason": priority_reason,
        "recommended_action": recommended_action,
        "evidence": evidence,
    })

