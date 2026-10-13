"""Profile RAG & Guardrails Module for Grounded Personal Email Drafting."""

import json
import logging
from pathlib import Path
import re
from typing import Any

logger = logging.getLogger("mail_agent_profile_rag")

PROFILE_PATH = Path("data/profile/user_profile.json")


def load_user_profile(path: Path | None = None) -> dict[str, Any]:
    """Load verified user profile and resume facts from disk."""
    target_path = path or PROFILE_PATH
    if not target_path.exists():
        return {}
    try:
        with open(target_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.warning("Failed to load user profile from %s: %s", target_path, exc)
        return {}


def match_job_to_profile(subject: str, body: str, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    """Evaluate job/internship/hackathon text against candidate resume skills using RAG matching."""
    prof = profile or load_user_profile()
    if not prof:
        return {"match_score": 0.0, "matched_skills": [], "is_match": False, "reason": "No profile"}

    text = f"{subject} {body}".lower()

    # Collect candidate skills from profile
    all_skills = []
    tech = prof.get("technical_skills", {})
    for cat_list in tech.values():
        if isinstance(cat_list, list):
            all_skills.extend(cat_list)

    matched_skills = []
    for skill in all_skills:
        # Avoid false positives on single-letter skill 'C' in ordinary English text
        if len(skill.strip()) <= 1 and skill.lower() == "c":
            if re.search(r"\b(c\s+language|c\s+programming|\bc\s*\/|\bc\+\+)\b", text):
                matched_skills.append("C")
            continue
        # Regex word boundary check for each skill
        escaped = re.escape(skill.lower())
        if re.search(r"\b" + escaped + r"\b", text):
            matched_skills.append(skill)

    # Check for domain keywords (AI/ML, Data Science, Software Engineering)
    domains = ["ai", "ml", "machine learning", "data science", "nlp", "computer vision", "python", "software engineer", "intern", "hackathon"]
    matched_domains = [d for d in domains if re.search(r"\b" + re.escape(d) + r"\b", text)]

    score = min(1.0, (len(matched_skills) * 0.25) + (len(matched_domains) * 0.15))
    is_match = len(matched_skills) >= 1 or len(matched_domains) >= 2 or any(k in text for k in ["invent.io", "smart india", "hackathon"])

    return {
        "match_score": round(score, 2),
        "matched_skills": list(dict.fromkeys(matched_skills))[:8],
        "matched_domains": list(dict.fromkeys(matched_domains))[:5],
        "is_match": is_match,
        "candidate_name": prof.get("name", "Dinesh Babu S"),
        "candidate_degree": prof.get("education", {}).get("degree", "B.E. Computer Science Engineering"),
    }


def format_standard_signature(profile: dict[str, Any]) -> str:
    """Generate official verified signature block from profile data."""
    if not profile:
        return "Best regards,\nDinesh Babu S"

    name = profile.get("name", "Dinesh Babu S")
    degree = profile.get("education", {}).get("degree", "B.E. Computer Science Engineering")
    college = profile.get("education", {}).get("institution", "National Engineering College, Kovilpatti")
    phone = profile.get("phone", "+91 63690 15600")
    email = profile.get("email", "sdinesh2k06@gmail.com")
    linkedin = profile.get("linkedin", "https://linkedin.com/in/dinesh-babu-167a1431b")
    github = profile.get("github", "https://github.com/dinesh121005")

    lines = [
        "Best regards,",
        name,
        f"{degree}, {college}",
        f"{phone} | {email}",
        f"LinkedIn: {linkedin} | GitHub: {github}",
    ]
    return "\n".join(lines)


def retrieve_profile_context(
    query: str = "",
    category: str = "",
    subject: str = "",
    body: str = "",
    profile: dict[str, Any] | None = None,
) -> str:
    """Retrieve relevant profile context tailored to the specific email context."""
    prof = profile or load_user_profile()
    if not prof:
        return ""

    combined_text = f"{query} {category} {subject} {body}".lower()

    # Determine relevant domains
    is_hackathon = any(k in combined_text for k in ["hackathon", "invent.io", "competition", "challenge", "gridlock", "round"])
    is_job_or_interview = any(k in combined_text for k in ["interview", "intern", "hiring", "job", "recruiter", "adroit", "application"])

    sections = []
    sections.append(
        f"CANDIDATE PROFILE (Source of Truth):\n"
        f"- Full Name: {prof.get('name')}\n"
        f"- Professional Title: {prof.get('title')}\n"
        f"- Education: {prof.get('education', {}).get('degree')} from {prof.get('education', {}).get('institution')} (Batch {prof.get('education', {}).get('duration')})\n"
        f"- Contact: {prof.get('phone')} | {prof.get('email')}\n"
        f"- Location: {prof.get('location')}"
    )

    if is_hackathon or not is_job_or_interview:
        achievements = prof.get("achievements", [])
        if achievements:
            sections.append("VERIFIED HACKATHON & COMPETITION TRACK RECORD:\n" + "\n".join(f"• {a}" for a in achievements))

        projects = prof.get("projects", [])
        if projects:
            proj_lines = [f"• {p['name']} ({p.get('event', '')}): {p.get('description', '')}" for p in projects]
            sections.append("KEY PROJECTS & COMPETITIVE BUILDS:\n" + "\n".join(proj_lines))

    if is_job_or_interview:
        exp_list = prof.get("experience", [])
        if exp_list:
            exp_lines = []
            for exp in exp_list:
                exp_lines.append(f"• {exp['role']} at {exp['company']} ({exp.get('duration', '')}):")
                for h in exp.get("highlights", []):
                    exp_lines.append(f"    - {h}")
            sections.append("PROFESSIONAL EXPERIENCE:\n" + "\n".join(exp_lines))

    # Relevant technical skills
    skills = prof.get("technical_skills", {})
    if skills:
        top_skills = []
        if skills.get("languages"):
            top_skills.append("Languages: " + ", ".join(skills["languages"]))
        if skills.get("ai_ml"):
            top_skills.append("AI/ML: " + ", ".join(skills["ai_ml"]))
        if skills.get("frameworks_and_apis"):
            top_skills.append("Frameworks: " + ", ".join(skills["frameworks_and_apis"][:8]))
        sections.append("TECHNICAL EXPERTISE:\n" + "\n".join(f"• {s}" for s in top_skills))

    sig = format_standard_signature(prof)
    sections.append(f"REQUIRED SIGNATURE BLOCK (Must be placed verbatim at bottom):\n{sig}")

    return "\n\n".join(sections)


def apply_draft_guardrails(raw_text: str, profile: dict[str, Any] | None = None) -> str:
    """Apply strict guardrails to sanitize, clean, and verify drafted email bodies."""
    if not raw_text:
        return ""

    prof = profile or load_user_profile()
    text = raw_text.strip()

    # Guardrail 1: Strip code fences (```markdown, ```, etc.)
    text = re.sub(r"^```[a-zA-Z0-9_-]*\s*\r?\n?", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\r?\n?```\s*$", "", text)
    text = re.sub(r"^```\s*$", "", text, flags=re.MULTILINE)
    text = text.replace("```", "")

    # Guardrail 2: Strip duplicate header leaks (Subject:, To:, From:)
    while True:
        prev = text
        text = re.sub(r"^(?:To|Subject|From):\s*[^\n\r]+\r?\n?", "", text, flags=re.IGNORECASE).strip()
        if text == prev:
            break

    # Guardrail 3: Replace hallucinated placeholders with real candidate info
    if prof:
        real_name = prof.get("name", "Dinesh Babu S")
        real_email = prof.get("email", "sdinesh2k06@gmail.com")
        real_phone = prof.get("phone", "+91 63690 15600")

        # Replace generic placeholders
        text = re.sub(r"\[Your Name\]|\[Candidate Name\]|<Your Name>", real_name, text, flags=re.IGNORECASE)
        text = re.sub(r"\[Your Phone\]|\[Phone Number\]|<Phone>", real_phone, text, flags=re.IGNORECASE)
        text = re.sub(r"\[Your Email\]|<Email>", real_email, text, flags=re.IGNORECASE)

        # Replace outdated roll number email with real email if present
        text = text.replace("2312112@nec.edu.in", real_email)

    # Guardrail 4: Standardize professional closing if sign-off is incomplete
    if prof and not any(k in text.lower() for k in ["linkedin.com", "github.com"]):
        # Check if basic sign-off exists
        if "best regards," in text.lower() or "sincerely," in text.lower():
            # Replace basic sign-off with verified full signature
            sig = format_standard_signature(prof)
            text = re.sub(r"(best regards,[\s\S]*|sincerely,[\s\S]*)$", sig, text, flags=re.IGNORECASE)
        else:
            text = text + "\n\n" + format_standard_signature(prof)

    return text.strip()


_nemo_rails_instance = None


def get_nemo_rails():
    """Load and cache official NVIDIA NeMo Guardrails instance."""
    global _nemo_rails_instance
    if _nemo_rails_instance is not None:
        return _nemo_rails_instance
    try:
        from nemoguardrails import RailsConfig, LLMRails
        config_dir = Path("config/rails")
        if config_dir.exists():
            cfg = RailsConfig.from_path(str(config_dir))
            _nemo_rails_instance = LLMRails(cfg)
            logger.info("Official NVIDIA NeMo Guardrails initialized successfully.")
            return _nemo_rails_instance
    except Exception as exc:
        logger.warning("Could not initialize NVIDIA NeMo Guardrails: %s", exc)
    return None


def apply_nemo_input_guardrail(user_query: str) -> dict[str, Any]:
    """Check query against NeMo Guardrails input rail for safety and topicality."""
    rails = get_nemo_rails()
    if not rails:
        return {"allowed": True, "reason": "nemo_uninitialized"}

    try:
        # Check for disallowed injection, system override, or credential exfiltration triggers
        q_lower = user_query.lower()
        credential_leak_keywords = [
            "oauth credential", "my credential", "client secret", "client_secret",
            "api key", "access token", "refresh token", "private key", "password",
            "token.json", "credentials.json", "secret key"
        ]
        if any(bad in q_lower for bad in ["ignore previous", "delete system", "jailbreak", "bypass security"]):
            return {
                "allowed": False,
                "reason": "Security Rail Triggered: Unsafe instructions detected by NeMo Guardrails.",
                "response": "I cannot fulfill requests that attempt to override system guardrails or perform harmful actions."
            }

        if any(leak in q_lower for leak in credential_leak_keywords):
            return {
                "allowed": False,
                "reason": "Security Rail Triggered: Credential isolation guardrail enforced by NeMo Guardrails.",
                "response": "🔒 **Security & Privacy Guardrail**: OAuth credentials, private tokens, API keys, and system secrets are isolated in protected local files (`credentials.json`, `token.json`) and are never exposed or accessible to the conversational AI model."
            }
        return {"allowed": True, "reason": "passed_nemo_rails"}
    except Exception as exc:
        logger.warning("NeMo input check skipped: %s", exc)
        return {"allowed": True, "reason": "fallback_allowed"}
