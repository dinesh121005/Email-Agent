import sys
import json
from datetime import datetime
from storage import EmailStorage
from schemas import EmailAnalysis
from urgency_policy import evaluate_priority_and_safety

sys.stdout.reconfigure(encoding="utf-8")

def reanalyze_all_existing_emails():
    storage = EmailStorage()
    with storage._connect() as conn:
        rows = conn.execute("SELECT * FROM emails").fetchall()
        print(f"Total emails in database: {len(rows)}")
        updated = 0
        for r in rows:
            if not r["category"]:
                continue
            try:
                ev = json.loads(r["evidence"]) if r["evidence"] else []
            except Exception:
                ev = []
            
            initial = EmailAnalysis(
                category=r["category"] if r["category"] in ["job", "hackathon", "competition", "other", "uncertain"] else "other",
                urgency=r["urgency"] if r["urgency"] in ["urgent", "high", "normal", "low"] else "low",
                summary=r["summary"] or "Email message",
                deadline=r["deadline"],
                requires_action=bool(r["requires_action"]),
                intent=r["intent"] or "other",
                importance=r["importance"] or "normal",
                spam_status=r["spam_status"] or "not_spam",
                recommended_action=r["recommended_action"],
                priority_reason=r["priority_reason"],
                evidence=ev,
            )
            
            rcv = None
            if r["received_at"]:
                try:
                    rcv = datetime.fromisoformat(r["received_at"])
                except Exception:
                    pass
            if not rcv:
                rcv = datetime.now()
                
            dl = None
            if r["deadline"]:
                try:
                    dl = datetime.fromisoformat(r["deadline"])
                except Exception:
                    pass
                    
            new_analysis = evaluate_priority_and_safety(
                analysis=initial,
                subject=r["subject"] or "",
                body=r["body"] or "",
                received_at=rcv,
                normalized_deadline=dl,
            )
            
            conn.execute(
                """
                UPDATE emails SET
                    intent = ?,
                    importance = ?,
                    urgency = ?,
                    spam_status = ?,
                    requires_action = ?,
                    priority_reason = ?,
                    recommended_action = ?,
                    evidence = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE message_id = ?
                """,
                (
                    new_analysis.intent,
                    new_analysis.importance,
                    new_analysis.urgency,
                    new_analysis.spam_status,
                    1 if new_analysis.requires_action else 0,
                    new_analysis.priority_reason,
                    new_analysis.recommended_action,
                    json.dumps(new_analysis.evidence),
                    r["message_id"],
                ),
            )
            updated += 1
            mid = r["message_id"][:10]
            subj = (r["subject"] or "")[:32]
            print(f"[{mid}] {subj:<35} | intent: {new_analysis.intent:<22} | imp: {new_analysis.importance:<8} | act: {new_analysis.requires_action}")

        print(f"\nSuccessfully re-analyzed and refreshed {updated} existing emails with latest Resume RAG & Priority Policy!")

if __name__ == "__main__":
    reanalyze_all_existing_emails()
