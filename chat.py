"""Personal AI Email Assistant conversational service backed by SQLite tools and live Gmail data."""

from datetime import datetime, timezone
import json
import logging
import re
from typing import Any

from digest import build_daily_digest, format_digest_text
from ingestion import ingest_recent_emails
from llm_client import OllamaClient
from storage import EmailStorage
from tools import ToolRegistry

logger = logging.getLogger(__name__)


def build_chat_registry(
    storage: EmailStorage,
    gmail_client: Any | None = None,
) -> ToolRegistry:
    """Build and register all database-backed and live Gmail tools for the chat assistant."""
    registry = ToolRegistry()

    @registry.register("search_local_emails")
    def search_local_emails(query: str = "", limit: int = 10) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 50))
        results = storage.search_emails(str(query), limit=safe_limit)
        for r in results:
            r["freshness"] = "locally_cached"
        return results

    # Backward compatibility alias
    registry.register("search_emails", search_local_emails)

    @registry.register("get_email_details")
    def get_email_details(message_id: str) -> dict[str, Any] | None:
        clean_id = str(message_id).strip()
        if not clean_id:
            return None
        item = storage.get_email(clean_id)
        if item:
            item["freshness"] = "locally_cached"
            if item.get("attachments") and isinstance(item["attachments"], str):
                try:
                    item["attachments"] = json.loads(item["attachments"])
                except Exception:
                    pass
        return item

    @registry.register("refresh_gmail_messages")
    def refresh_gmail_messages(
        query: str = "newer_than:7d",
        max_results: int = 10,
        max_messages: int | None = None,
    ) -> dict[str, Any]:
        """Check live Gmail mailbox and synchronize new messages into local storage."""
        limit = max_messages if max_messages is not None else max_results
        safe_limit = max(1, min(int(limit), 50))

        # Check if lock can be acquired to avoid race conditions with background sync
        if not storage.acquire_lock("sync_lock", owner_id="chatbot_sync", lease_seconds=60):
            return {
                "status": "sync_in_progress",
                "freshness": "locally_cached",
                "message": "Another synchronization cycle is currently active. Using existing local data.",
            }

        try:
            from gmail_client import GmailClient, GmailClientError
            client = gmail_client or GmailClient()

            report = ingest_recent_emails(
                client=client,
                storage=storage,
                query=query,
                max_results=safe_limit,
            )

            # Retrieve recently synced emails
            recent_rows = storage.get_important_emails(unacknowledged_only=False, limit=safe_limit)
            return {
                "status": "success",
                "freshness": "freshly_verified_gmail",
                "discovered": report.discovered,
                "inserted": report.inserted,
                "duplicates": report.duplicates,
                "recent_emails": recent_rows[:5],
                "message": f"Successfully synchronized with Gmail. Discovered {report.discovered} messages ({report.inserted} new).",
            }
        except Exception as exc:
            logger.warning("Live Gmail refresh failed in chat: %s", type(exc).__name__)
            return {
                "status": "error",
                "freshness": "locally_cached_only",
                "error": type(exc).__name__,
                "message": f"Could not check live Gmail mailbox: {exc}. Reporting from locally cached data.",
            }
        finally:
            storage.release_lock("sync_lock", owner_id="chatbot_sync")

    @registry.register("get_upcoming_deadlines")
    def get_upcoming_deadlines(limit: int = 10) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 50))
        with storage._connect() as conn:
            rows = conn.execute("""
                SELECT message_id, subject, sender, received_at, intent, urgency,
                       summary, deadline, recommended_action
                FROM emails
                WHERE status = 'completed'
                  AND (spam_status = 'not_spam' OR spam_status IS NULL)
                  AND deadline IS NOT NULL
                  AND acknowledged_at IS NULL
                ORDER BY deadline ASC
                LIMIT ?
            """, (safe_limit,)).fetchall()
            results = [dict(r) for r in rows]
            for r in results:
                r["freshness"] = "locally_cached"
            return results

    # Backward compatibility alias
    registry.register("find_upcoming_deadlines", get_upcoming_deadlines)

    @registry.register("get_pending_actions")
    def get_pending_actions(limit: int = 10) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 50))
        with storage._connect() as conn:
            rows = conn.execute("""
                SELECT message_id, subject, sender, received_at, intent, importance, urgency,
                       summary, deadline, recommended_action, priority_reason
                FROM emails
                WHERE status = 'completed'
                  AND (spam_status = 'not_spam' OR spam_status IS NULL)
                  AND requires_action = 1
                  AND acknowledged_at IS NULL
                ORDER BY
                    CASE WHEN deadline IS NOT NULL THEN 0 ELSE 1 END,
                    deadline ASC,
                    received_at DESC
                LIMIT ?
            """, (safe_limit,)).fetchall()
            results = [dict(r) for r in rows]
            for r in results:
                r["freshness"] = "locally_cached"
            return results

    # Backward compatibility alias
    registry.register("list_pending_actions", get_pending_actions)

    @registry.register("get_email_attachments")
    def get_email_attachments(
        message_id: str,
        attachment_id: str = "",
        live_gmail_fetch: bool = False,
    ) -> dict[str, Any]:
        """Retrieve attachment metadata and filenames for an email."""
        clean_id = str(message_id).strip()
        if not clean_id:
            return {"found": False, "error": "Message ID must not be empty.", "attachments": []}

        if attachment_id and gmail_client:
            try:
                data = gmail_client.get_attachment(clean_id, attachment_id)
                return {
                    "found": True,
                    "message_id": clean_id,
                    "attachment_id": attachment_id,
                    "fetched_bytes": len(data),
                    "freshness": "freshly_verified_gmail",
                    "status": "success",
                }
            except Exception as exc:
                logger.warning("Live attachment lookup failed: %s", exc)

        email_row = storage.get_email(clean_id)
        if not email_row:
            return {
                "found": False,
                "error": f"Email '{clean_id}' not found in database.",
                "attachments": [],
                "freshness": "locally_cached",
            }

        # Check local attachments column
        attachments = []
        raw_att = email_row.get("attachments")
        if raw_att:
            if isinstance(raw_att, str):
                try:
                    attachments = json.loads(raw_att)
                except Exception:
                    attachments = []
            elif isinstance(raw_att, list):
                attachments = raw_att

        if attachments:
            return {
                "found": True,
                "message_id": clean_id,
                "subject": email_row.get("subject", ""),
                "sender": email_row.get("sender", ""),
                "attachments": attachments,
                "attachment_count": len(attachments),
                "freshness": "locally_cached",
                "count": len(attachments),
            }

        # If empty locally and live client available, check live Gmail
        if (live_gmail_fetch or not attachments) and gmail_client:
            try:
                raw_msg = gmail_client.get_message(clean_id)
                from email_parser import parse_gmail_message
                parsed = parse_gmail_message(raw_msg)
                return {
                    "found": True,
                    "message_id": clean_id,
                    "subject": parsed.subject,
                    "sender": parsed.sender,
                    "attachments": parsed.attachments,
                    "attachment_count": len(parsed.attachments),
                    "freshness": "freshly_verified_gmail",
                    "count": len(parsed.attachments),
                }
            except Exception as exc:
                logger.warning("Live attachment lookup failed: %s", exc)

        return {
            "found": True,
            "message_id": clean_id,
            "subject": email_row.get("subject", ""),
            "attachments": [],
            "attachment_count": 0,
            "freshness": "locally_cached",
            "count": 0,
            "message": "No attachments associated with this email.",
        }

    @registry.register("get_thread_context")
    def get_thread_context(
        thread_id: str = "",
        message_id: str = "",
        live_gmail_fetch: bool = False,
    ) -> dict[str, Any]:
        """Retrieve full conversation context and all messages in a thread."""
        target_thread_id = str(thread_id).strip()

        if not target_thread_id and message_id:
            email_row = storage.get_email(str(message_id).strip())
            if email_row and email_row.get("thread_id"):
                target_thread_id = str(email_row["thread_id"])

        if not target_thread_id:
            return {
                "found": False,
                "error": "Must provide a valid thread_id or message_id.",
                "messages": [],
            }

        # If live_gmail_fetch requested, or no local messages exist
        if live_gmail_fetch and gmail_client:
            try:
                raw_thread = gmail_client.get_thread(target_thread_id)
                thread_msgs = raw_thread.get("messages", [])
                if thread_msgs:
                    from email_parser import parse_gmail_message
                    live_list = []
                    for m in thread_msgs:
                        try:
                            p = parse_gmail_message(m)
                            storage.save_email(p)
                            live_list.append({
                                "message_id": p.message_id,
                                "sender": p.sender,
                                "subject": p.subject,
                                "received_at": p.received_at.isoformat(),
                                "body_snippet": p.body[:300] if p.body else "",
                                "attachments": p.attachments,
                            })
                        except Exception:
                            mid = m.get("id") if isinstance(m, dict) else None
                            if mid:
                                live_list.append({
                                    "message_id": mid,
                                    "sender": m.get("sender", ""),
                                    "subject": m.get("subject", ""),
                                    "received_at": datetime.now(timezone.utc).isoformat(),
                                    "body_snippet": m.get("snippet", ""),
                                    "attachments": [],
                                })
                    if live_list:
                        return {
                            "found": True,
                            "thread_id": target_thread_id,
                            "freshness": "freshly_verified_gmail",
                            "message_count": len(live_list),
                            "messages": live_list,
                        }
            except Exception as exc:
                logger.warning("Live thread retrieval failed: %s", exc)

        local_msgs = storage.get_thread_emails(target_thread_id)

        formatted_local = []
        for m in local_msgs:
            formatted_local.append({
                "message_id": m.get("message_id"),
                "sender": m.get("sender"),
                "subject": m.get("subject"),
                "received_at": m.get("received_at"),
                "intent": m.get("intent") or m.get("category"),
                "summary": m.get("summary") or m.get("body", "")[:200],
            })

        return {
            "found": True,
            "thread_id": target_thread_id,
            "freshness": "locally_cached",
            "message_count": len(formatted_local),
            "messages": formatted_local,
        }

    @registry.register("explain_email_decision")
    def explain_email_decision(message_id: str) -> dict[str, Any]:
        """Explain why an email was classified and whether it triggered a notification."""
        clean_id = str(message_id).strip()
        row = storage.get_email(clean_id)
        if not row:
            return {"found": False, "error": f"Message '{clean_id}' not found in database."}

        ev = row.get("evidence")
        evidence_list = []
        if isinstance(ev, str) and ev:
            try:
                loaded = json.loads(ev)
                evidence_list = loaded if isinstance(loaded, list) else [loaded]
            except Exception:
                evidence_list = [ev]
        elif isinstance(ev, list):
            evidence_list = ev

        notified_str = f"Notified at {row.get('notified_at')}" if row.get("notified_at") else "No notification delivered (Silent)"
        ack_str = f"Handled at {row.get('acknowledged_at')}" if row.get("acknowledged_at") else "Pending User Action"

        decision_dict = {
            "intent": row.get("intent") or row.get("category"),
            "importance": row.get("importance") or "normal",
            "urgency": row.get("urgency") or "normal",
            "spam_status": row.get("spam_status") or "not_spam",
            "requires_action": bool(row.get("requires_action")),
            "priority_reason": row.get("priority_reason") or "Standard automated rule evaluation.",
            "recommended_action": row.get("recommended_action") or "Review email content.",
            "deadline": row.get("deadline"),
        }

        return {
            "found": True,
            "message_id": row.get("message_id"),
            "subject": row.get("subject"),
            "sender": row.get("sender"),
            "decision": decision_dict,
            "intent": decision_dict["intent"],
            "importance": decision_dict["importance"],
            "urgency": decision_dict["urgency"],
            "spam_status": decision_dict["spam_status"],
            "requires_action": decision_dict["requires_action"],
            "priority_reason": decision_dict["priority_reason"],
            "recommended_action": decision_dict["recommended_action"],
            "summary": row.get("summary"),
            "deadline": row.get("deadline"),
            "evidence": evidence_list,
            "notification_status": notified_str,
            "handling_status": ack_str,
            "freshness": "locally_cached",
        }

    # Backward compatibility alias
    registry.register("explain_classification", explain_email_decision)

    @registry.register("find_interviews")
    def find_interviews(limit: int = 10) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 50))
        with storage._connect() as conn:
            rows = conn.execute("""
                SELECT message_id, subject, sender, received_at, intent, importance, urgency,
                       summary, deadline, recommended_action, priority_reason, acknowledged_at
                FROM emails
                WHERE status = 'completed'
                  AND (spam_status = 'not_spam' OR spam_status IS NULL)
                  AND (
                      intent IN ('interview_invitation', 'interview_scheduling', 'interview_confirmation')
                      OR subject LIKE '%interview%'
                      OR subject LIKE '%screening%'
                      OR body LIKE '%invitation to interview%'
                  )
                ORDER BY
                    CASE WHEN acknowledged_at IS NULL THEN 0 ELSE 1 END,
                    received_at DESC
                LIMIT ?
            """, (safe_limit,)).fetchall()
            results = [dict(r) for r in rows]
            for r in results:
                r["freshness"] = "locally_cached"
            return results

    @registry.register("find_opportunities")
    def find_opportunities(limit: int = 10) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 50))
        with storage._connect() as conn:
            rows = conn.execute("""
                SELECT message_id, subject, sender, received_at, intent, category, importance, urgency,
                       summary, deadline, recommended_action
                FROM emails
                WHERE status = 'completed'
                  AND (spam_status = 'not_spam' OR spam_status IS NULL)
                  AND (
                      category IN ('job', 'hackathon', 'competition')
                      OR intent IN ('job_opportunity', 'hackathon', 'competition')
                  )
                ORDER BY received_at DESC
                LIMIT ?
            """, (safe_limit,)).fetchall()
            results = [dict(r) for r in rows]
            for r in results:
                r["freshness"] = "locally_cached"
            return results

    @registry.register("inspect_suppressed_promotions")
    def inspect_suppressed_promotions(limit: int = 10) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 50))
        results = storage.get_suppressed_emails(limit=safe_limit)
        for r in results:
            r["freshness"] = "locally_cached"
        return results

    @registry.register("get_daily_summary")
    def get_daily_summary(hours: float = 24.0) -> dict[str, Any]:
        digest = build_daily_digest(storage, opportunity_window_hours=float(hours))
        return {
            "generated_at": digest.generated_at,
            "upcoming_deadlines_count": len(digest.upcoming_deadlines),
            "new_opportunities_count": len(digest.new_opportunities),
            "awaiting_action_count": len(digest.awaiting_action),
            "health_stats": {
                "total": digest.health_stats.total_emails,
                "completed": digest.health_stats.completed,
                "pending": digest.health_stats.pending,
                "failed": digest.health_stats.failed,
            },
            "formatted_text": format_digest_text(digest),
            "freshness": "locally_cached",
        }

    @registry.register("create_email_draft")
    def create_email_draft(
        to: str = "",
        subject: str = "",
        body: str = "",
        thread_id: str | None = None,
        message_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a real draft in Gmail and provide a confirmation card for sending."""
        target_to = str(to).strip()
        target_subject = str(subject).strip()
        target_body = str(body).strip()
        target_thread_id = thread_id

        if message_id and (not target_to or not target_subject):
            email_row = storage.get_email(message_id)
            if email_row:
                if not target_to:
                    target_to = email_row.get("sender") or ""
                if not target_subject:
                    orig_subj = email_row.get("subject") or ""
                    target_subject = orig_subj if orig_subj.lower().startswith("re:") else f"Re: {orig_subj}"
                if not target_thread_id:
                    target_thread_id = email_row.get("thread_id")

        if not target_to:
            return {
                "status": "error",
                "error": "Recipient email address ('to') is required to create a draft.",
            }

        try:
            from gmail_client import GmailClient
            client = gmail_client or GmailClient()
            draft_res = client.create_draft(
                to=target_to,
                subject=target_subject,
                body=target_body,
                thread_id=target_thread_id,
            )
            return {
                "status": "draft_created",
                "freshness": "live_gmail",
                "draft_id": draft_res.get("draft_id"),
                "to": target_to,
                "subject": target_subject,
                "body": target_body,
                "thread_id": target_thread_id,
                "requires_confirmation": True,
                "message": (
                    f"Draft created successfully in Gmail for {target_to} with subject '{target_subject}'. "
                    "Review the preview card below. Click 'Confirm & Send' when you are ready to send."
                ),
            }
        except Exception as exc:
            logger.warning("Failed to create Gmail draft: %s", exc)
            return {
                "status": "local_draft_only",
                "to": target_to,
                "subject": target_subject,
                "body": target_body,
                "requires_confirmation": True,
                "error": str(exc),
                "message": (
                    f"Draft prepared locally for {target_to}. Live Gmail draft creation: {type(exc).__name__}."
                ),
            }

    return registry


def plan_tool_execution(query: str, selected_message_id: str | None = None) -> tuple[str, dict[str, Any]]:
    """Determine the most appropriate tool to satisfy the user's inquiry."""
    q = query.lower().strip()

    found_id = ""
    # If the user has a selected email active in the UI context, prioritize it
    if selected_message_id and str(selected_message_id).strip():
        found_id = str(selected_message_id).strip()
    else:
        # Match explicit ID patterns: e.g. message_id="...", id: "...", or id=...
        id_with_prefix = re.search(r'\b(?:id|message_id)\s*[:=]\s*["\']?([a-zA-Z0-9_-]{3,64})["\']?', query, re.IGNORECASE)
        # Match structured IDs (like 16-32 char hex or msg-xxx)
        structured_id = re.search(r'\b([0-9a-fA-F]{16,32}|msg-[a-zA-Z0-9_-]+)\b', query)

        if id_with_prefix:
            found_id = id_with_prefix.group(1)
        elif structured_id:
            found_id = structured_id.group(1)

    # 1. Live Gmail refresh / Sync inquiry
    if any(k in q for k in ["check gmail", "refresh", "fetch new", "sync gmail", "check live", "new deadline updates"]):
        return "refresh_gmail_messages", {"max_results": 10}

    # 2. Attachments inquiry
    if any(k in q for k in ["attachment", "attachments", "files", "attached"]):
        return "get_email_attachments", {"message_id": found_id}

    # 3. Thread / Conversation inquiry
    if any(k in q for k in ["thread", "chain", "conversation", "related message", "related messages"]):
        return "get_thread_context", {"thread_id": found_id, "message_id": found_id}

    # 4. Explanation / Why inquiry
    if any(k in q for k in ["why", "explain", "decision", "trigger", "reason", "important"]):
        if found_id:
            return "explain_email_decision", {"message_id": found_id}

    # 5. Live Gmail Draft or Send email
    send_keywords = [
        "create draft", "create a draft", "draft email", "compose",
        "send email", "send an email", "send a mail", "send this mail",
        "send this email", "send the mail", "send the email", "send it",
        "send this", "send reply", "send as reply", "send replay",
        "send as the replay", "send as the reply", "send to", "draft a mail",
    ]
    if any(k in q for k in send_keywords):
        to_match = re.search(r'\bto\s+([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})\b', query)
        to_addr = to_match.group(1) if to_match else ""
        if found_id or to_addr:
            return "create_email_draft", {
                "to": to_addr,
                "message_id": found_id,
            }

    # 5b. Draft reply inquiry
    if any(k in q for k in ["draft", "reply", "replay", "respond", "send"]):
        if found_id:
            return "get_email_details", {"message_id": found_id}

    # 6. Action / Recommendation for selected email
    if any(k in q for k in ["action", "what action", "what should i do"]):
        if found_id:
            return "explain_email_decision", {"message_id": found_id}

    # 7. Summarize this specific email
    if found_id and any(k in q for k in ["summarize this", "summary of this", "summarize email", "summarize the email", "this email"]):
        return "get_email_details", {"message_id": found_id}

    # 8. Interview inquiries
    if any(k in q for k in ["interview", "interviews", "screening", "interviewer", "rounds"]):
        return "find_interviews", {"limit": 10}

    # 9. Upcoming deadlines
    if any(k in q for k in ["deadline", "deadlines", "due date", "due today", "due tomorrow", "expire"]):
        return "find_upcoming_deadlines", {"limit": 10}

    # 10. Actions / Pending items / Focus today
    if any(k in q for k in ["focus", "today", "to do", "todo", "action", "pending", "need to do", "should i do", "require my response"]):
        return "list_pending_actions", {"limit": 10}

    # 11. Suppressed emails / Promotional noise / Spam
    if any(k in q for k in ["suppress", "suppressed", "promo", "promotional", "spam", "noise", "ignored"]):
        return "inspect_suppressed_promotions", {"limit": 10}

    # 12. Competitions & Hackathons & Opportunities
    if any(k in q for k in ["hackathon", "hackathons", "competition", "competitions", "opportunity", "opportunities"]):
        return "find_opportunities", {"limit": 10}

    # 13. Specific email lookup by ID
    if found_id and any(k in q for k in ["show", "get", "view", "details"]):
        return "get_email_details", {"message_id": found_id}

    # 14. Daily summary / digest
    if any(k in q for k in ["summary", "summarize", "digest", "yesterday", "last 24", "overview"]):
        return "get_daily_summary", {"hours": 24.0}

    # 15. General search
    search_keywords = re.sub(r"[^\w\s-]", "", query).strip()
    return "search_emails", {"query": search_keywords, "limit": 10}


def format_context_records(records: Any) -> str:
    """Safely format database tool results for prompt context without leaking sensitive secrets."""
    if not records:
        return "No matching database records found."

    if isinstance(records, dict):
        if "formatted_text" in records:
            return records["formatted_text"]
        if records.get("status") == "gmail_unavailable":
            return (
                f"[LIVE GMAIL STATUS: UNAVAILABLE]\n"
                f"Note: Could not check live Gmail ({records.get('error')}). Using locally cached records."
            )
        return json.dumps(records, indent=2)

    if isinstance(records, list):
        if not records:
            return "No matching database records found."
        lines = []
        for i, item in enumerate(records, start=1):
            if isinstance(item, dict):
                subj = item.get("subject", "No subject")
                sender = item.get("sender", "Unknown sender")
                intent = item.get("intent") or item.get("category", "")
                urgency = item.get("urgency", "")
                deadline = item.get("deadline") or "None"
                summary = item.get("summary", "")
                action = item.get("recommended_action") or ""
                msg_id = item.get("message_id", "")
                freshness = item.get("freshness", "locally_cached")
                lines.append(
                    f"[{i}] ID: {msg_id} ({freshness})\n"
                    f"    Subject: {subj}\n"
                    f"    From: {sender}\n"
                    f"    Intent: {intent} | Urgency: {urgency} | Deadline: {deadline}\n"
                    f"    Summary: {summary}\n"
                    f"    Recommended Action: {action}"
                )
            else:
                lines.append(f"[{i}] {str(item)}")
        return "\n\n".join(lines)

    return str(records)


def answer_chat_query(
    query: str,
    storage: EmailStorage,
    history: list[dict[str, str]] | None = None,
    client: OllamaClient | None = None,
    gmail_client: Any | None = None,
    selected_message_id: str | None = None,
) -> dict[str, Any]:
    """Execute real database or Gmail tool retrieval and generate grounded response."""
    # Run NVIDIA NeMo Input Guardrail Check
    from profile_rag import apply_nemo_input_guardrail
    rail_check = apply_nemo_input_guardrail(query)
    if not rail_check.get("allowed", True):
        return {
            "reply": rail_check.get("response", "Request blocked by safety guardrails."),
            "tool_used": "nemo_guardrails_input_rail",
            "freshness": "locally_cached",
        }

    registry = build_chat_registry(storage, gmail_client=gmail_client)
    tool_name, tool_args = plan_tool_execution(query, selected_message_id=selected_message_id)

    try:
        retrieved_data = registry.execute(tool_name, tool_args)
    except Exception as exc:
        logger.error("Tool execution failed: %s", exc)
        retrieved_data = {"error": f"Tool execution failed: {type(exc).__name__}"}

    context_str = format_context_records(retrieved_data)

    q_lower = query.lower()
    is_drafting = (
        any(k in q_lower for k in [
            "draft a", "draft the", "compose", "write a reply", "write an email",
            "write a response", "send this mail", "send an email", "send reply",
            "draft reply", "reply to this", "respond to this"
        ])
        or (any(k in q_lower for k in ["draft", "compose"]) and not any(k in q_lower for k in ["why is", "why this", "what action", "what should i"]))
    )
    profile_block = ""
    if is_drafting:
        from profile_rag import retrieve_profile_context
        email_cat = retrieved_data.get("category", "") if isinstance(retrieved_data, dict) else ""
        email_subj = retrieved_data.get("subject", "") if isinstance(retrieved_data, dict) else ""
        email_body = retrieved_data.get("body", "") if isinstance(retrieved_data, dict) else ""
        prof_ctx = retrieve_profile_context(
            query=query,
            category=email_cat,
            subject=email_subj,
            body=email_body,
        )
        if prof_ctx:
            profile_block = f"\nVERIFIED CANDIDATE RESUME & PROFILE FACTS:\n{prof_ctx}\n"

    system_prompt = (
        "You are the Personal AI Mail Assistant for Dinesh Babu S.\n"
        "Your responses must be grounded strictly in the retrieved facts below.\n\n"
        "RULES:\n"
        "1. Distinguish verified database facts from inference. Never guess or fabricate emails, dates, or deadlines.\n"
        "2. If no matching records exist, clearly state: 'I checked your database, and no matching records were found.'\n"
        "3. If a live Gmail check was requested but could not be completed, clearly communicate that the live mailbox could not be checked and identify the freshness limitation.\n"
        "4. Highlight recommended next actions clearly.\n"
        "5. Keep your answer crisp, concise, and structured with Markdown bullet points.\n"
        "6. If the user asks why an email is important or what action to take, answer directly as their assistant explaining the email's details and actionable steps. Do NOT format your response as an email draft or letter unless the user explicitly requested to draft or compose an email.\n"
        "7. If drafting an email or reply: tailor the message authentically using the candidate's verified resume facts (education, skills, projects, achievements). NEVER invent or hallucinate false credentials. Output clean, professional email body text ready to send. NEVER wrap the draft in markdown code fences or backticks (no ```markdown or ```). Start directly with the greeting ('Dear ...' or 'Hi ...') and end with the candidate's verified signature block.\n\n"
        f"RETRIEVED RECORDS (Source of Truth):\n"
        f"{context_str}\n"
        f"{profile_block}"
    )

    user_prompt = f"User Question: {query}"

    reply_text = ""
    if client is not None:
        full_prompt = f"{system_prompt}\n\n{user_prompt}\n\nAssistant Response:"
        try:
            reply_text = client.generate(full_prompt)
            if is_drafting:
                from profile_rag import apply_draft_guardrails
                reply_text = apply_draft_guardrails(reply_text)
            elif "```" in reply_text:
                reply_text = re.sub(r"^```(?:markdown)?\s*\r?\n?", "", reply_text.strip(), flags=re.IGNORECASE)
                reply_text = re.sub(r"\r?\n?```\s*$", "", reply_text.strip())
        except Exception as exc:
            logger.warning("Local LLM generation failed (%s); providing structured retrieval output.", exc)
            reply_text = f"**Retrieval Results:**\n\n{context_str}"
    else:
        # Structured deterministic fallback when client is omitted (e.g. testing)
        reply_text = f"**Database Retrieval Results:**\n\n{context_str}"
        if is_drafting:
            from profile_rag import apply_draft_guardrails
            reply_text = apply_draft_guardrails(reply_text)

    freshness = "locally_cached"
    if isinstance(retrieved_data, dict):
        freshness = retrieved_data.get("freshness", "locally_cached")
    elif isinstance(retrieved_data, list) and retrieved_data:
        freshness = retrieved_data[0].get("freshness", "locally_cached") if isinstance(retrieved_data[0], dict) else "locally_cached"

    return {
        "reply": reply_text.strip(),
        "tool_used": tool_name,
        "tool_arguments": tool_args,
        "evidence": retrieved_data if isinstance(retrieved_data, list) else [retrieved_data],
        "freshness": freshness,
    }
