from datetime import datetime, timezone
from schemas import EmailAnalysis
from profile_rag import match_job_to_profile, load_user_profile
from urgency_policy import evaluate_priority_and_safety

def test_naukri_matching_job_promoted():
    profile = load_user_profile()
    subject = "Naukri Job Alert: Senior Python & AI/ML Engineer opening at InnovateAI"
    body = "Hi Dinesh, We have an opening for an AI Engineer with PyTorch, FastAPI, and Agentic AI workflow experience. Click here to apply."
    
    match = match_job_to_profile(subject, body, profile)
    assert match["is_match"] is True
    assert any(s in match["matched_skills"] for s in ["Python", "AI/ML", "PyTorch", "FastAPI"])
    
    # Simulate an LLM initially miscategorizing as promotional
    initial_analysis = EmailAnalysis(
        category="other",
        intent="promotional",
        urgency="low",
        importance="low",
        summary="Naukri job alert email",
        spam_status="suspected_spam",
        requires_action=False,
        priority_reason="Job alert newsletter",
        recommended_action="None",
        evidence=["Naukri job alert"],
    )
    
    result = evaluate_priority_and_safety(
        analysis=initial_analysis,
        subject=subject,
        body=body,
        received_at=datetime.now(timezone.utc),
    )
    
    assert result.intent == "job_opportunity"
    assert result.spam_status == "not_spam"
    assert result.requires_action is True
    assert result.importance == "high"
    assert "Verified job/opportunity matching your resume skills" in result.priority_reason
    assert "Review role requirements matching your profile" in result.recommended_action


def test_naukri_generic_marketing_suppressed():
    subject = "Upgrade your Naukri FastForward account today!"
    body = "Get 3x more recruiter views with our resume booster discount pack. Limited time deal!"
    
    initial_analysis = EmailAnalysis(
        category="other",
        intent="promotional",
        urgency="low",
        importance="low",
        summary="Naukri marketing promotional",
        spam_status="suspected_spam",
        requires_action=False,
        priority_reason="Generic promotional marketing",
        recommended_action="No action needed",
        evidence=[],
    )
    
    result = evaluate_priority_and_safety(
        analysis=initial_analysis,
        subject=subject,
        body=body,
        received_at=datetime.now(timezone.utc),
    )
    
    assert result.intent == "promotional"
    assert result.spam_status == "suspected_spam"
    assert result.requires_action is False


def test_unstop_hackathon_promoted():
    subject = "Unstop Alert: AI Innovation Hackathon 2026 - Registrations Open"
    body = "Participate in the National Hackathon on Generative AI, PyTorch and Machine Learning solutions. Cash prizes worth 5 Lakhs."
    
    initial_analysis = EmailAnalysis(
        category="other",
        intent="promotional",
        urgency="low",
        importance="low",
        summary="Hackathon alert promo",
        spam_status="suspected_spam",
        requires_action=False,
        priority_reason="Hackathon alert promo",
        recommended_action="None",
        evidence=[],
    )
    
    result = evaluate_priority_and_safety(
        analysis=initial_analysis,
        subject=subject,
        body=body,
        received_at=datetime.now(timezone.utc),
    )
    
    assert result.intent == "hackathon"
    assert result.spam_status == "not_spam"
    assert result.requires_action is True
    assert "Verified job/opportunity matching your resume skills" in result.priority_reason
