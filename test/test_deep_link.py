"""Regression test suite for toast notification deep-linking and selection stability."""

import json
import subprocess
from pathlib import Path
import urllib.request
import pytest

from app import HTML_TEMPLATE, MailAgentHTTPHandler, ThreadingHTTPServer
from email_parser import ParsedEmail
from storage import EmailStorage


@pytest.fixture
def populated_storage(tmp_path: Path) -> EmailStorage:
    db = EmailStorage(tmp_path / "deep_link_test.db")
    emails = [
        ParsedEmail(
            message_id="msg-1-needs-action",
            thread_id="t1",
            sender="recruiter@company.com",
            subject="Interview Request",
            received_at="2026-10-14T10:00:00+00:00",
            body="Interview body",
            body_source="plain",
        ),
        ParsedEmail(
            message_id="msg-2-interview",
            thread_id="t2",
            sender="lead@stripe.com",
            subject="Technical Interview Tomorrow",
            received_at="2026-10-14T11:00:00+00:00",
            body="Stripe interview body",
            body_source="plain",
        ),
        ParsedEmail(
            message_id="msg-3-handled",
            thread_id="t3",
            sender="old@archive.com",
            subject="Archived Notification",
            received_at="2026-10-13T09:00:00+00:00",
            body="Old body",
            body_source="plain",
        ),
        ParsedEmail(
            message_id="msg-4-suppressed",
            thread_id="t4",
            sender="promo@marketing.com",
            subject="4x More Profile Views",
            received_at="2026-10-13T08:00:00+00:00",
            body="Promo body",
            body_source="plain",
        ),
    ]
    for email in emails:
        db.save_email(email)

    db.save_analysis("msg-1-needs-action", {
        "category": "job",
        "intent": "interview_invitation",
        "importance": "high",
        "urgency": "urgent",
        "summary": "Urgent interview request",
        "requires_action": True,
    })
    db.save_analysis("msg-2-interview", {
        "category": "job",
        "intent": "interview_scheduling",
        "importance": "high",
        "urgency": "high",
        "summary": "Stripe interview scheduled",
        "requires_action": True,
    })
    db.save_analysis("msg-3-handled", {
        "category": "other",
        "intent": "other",
        "importance": "low",
        "urgency": "low",
        "summary": "Handled item",
        "requires_action": False,
    })
    db.acknowledge("msg-3-handled")

    db.save_analysis("msg-4-suppressed", {
        "category": "other",
        "intent": "promotional",
        "spam_status": "suspected_spam",
        "importance": "low",
        "urgency": "low",
        "summary": "Promotional spam",
        "requires_action": False,
    })

    return db


@pytest.fixture
def running_server(populated_storage: EmailStorage):
    MailAgentHTTPHandler.storage = populated_storage
    server = ThreadingHTTPServer(("127.0.0.1", 0), MailAgentHTTPHandler)
    port = server.server_address[1]
    import threading
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    server.server_close()


def test_html_template_contains_deep_link_infrastructure():
    """Verify that HTML_TEMPLATE contains data-filter attributes and deep-link handling."""
    assert 'data-filter="needs_action"' in HTML_TEMPLATE
    assert 'data-filter="interviews"' in HTML_TEMPLATE
    assert 'data-filter="deadlines"' in HTML_TEMPLATE
    assert 'data-filter="opportunities"' in HTML_TEMPLATE
    assert 'data-filter="review_queue"' in HTML_TEMPLATE
    assert 'data-filter="suppressed"' in HTML_TEMPLATE
    assert 'data-filter="handled"' in HTML_TEMPLATE
    assert 'data-filter="all"' in HTML_TEMPLATE
    assert "deepLinkProcessed" in HTML_TEMPLATE
    assert "urlParams.get('email')" in HTML_TEMPLATE


def test_server_serves_deep_link_query_urls(running_server: str):
    """Verify the server responds with HTTP 200 when requested with ?email= query parameter."""
    req = urllib.request.urlopen(f"{running_server}/?email=msg-2-interview")
    assert req.status == 200
    content = req.read().decode("utf-8")
    assert "Mail Agent" in content
    assert "deepLinkProcessed" in content


def test_client_deep_link_resolution_logic_via_node():
    """Test the complete client-side selection and filter switching logic across all test scenarios using Node.js."""
    js_test_script = """
    // Mock sample dataset
    const allEmails = [
      {
        message_id: 'msg-1-needs-action',
        subject: 'Interview Request',
        intent: 'interview_invitation',
        category: 'job',
        urgency: 'urgent',
        importance: 'high',
        requires_action: true,
        spam_status: 'not_spam',
        acknowledged_at: null
      },
      {
        message_id: 'msg-2-interview',
        subject: 'Technical Interview Tomorrow',
        intent: 'interview_scheduling',
        category: 'job',
        urgency: 'high',
        importance: 'high',
        requires_action: true,
        spam_status: 'not_spam',
        acknowledged_at: null
      },
      {
        message_id: 'msg-3-handled',
        subject: 'Archived Notification',
        intent: 'other',
        category: 'other',
        urgency: 'low',
        importance: 'low',
        requires_action: false,
        spam_status: 'not_spam',
        acknowledged_at: '2026-10-13T09:00:00Z'
      },
      {
        message_id: 'msg-4-suppressed',
        subject: '4x More Profile Views',
        intent: 'promotional',
        category: 'other',
        urgency: 'low',
        importance: 'low',
        requires_action: false,
        spam_status: 'suspected_spam',
        acknowledged_at: null
      }
    ];

    function runScenario({ urlSearch, initialSelectedId, emails = allEmails, isSubsequentRefresh = false }) {
      let currentFilter = 'needs_action';
      let selectedEmailId = initialSelectedId || null;
      let deepLinkProcessed = isSubsequentRefresh ? true : false;
      let toastMessage = null;

      function showToast(msg) { toastMessage = msg; }

      function getFilteredEmails() {
        return emails.filter(m => {
          const isHandled = !!m.acknowledged_at;
          const isSpam = m.spam_status === 'confirmed_spam' || m.spam_status === 'suspected_spam' || m.intent === 'promotional';
          const isInterview = ['interview_invitation', 'interview_scheduling', 'interview_confirmation'].includes(m.intent) ||
                              (m.subject || '').toLowerCase().includes('interview');
          const isActionable = (m.requires_action === 1 || m.requires_action === true) && !isSpam;

          if (currentFilter === 'needs_action') return !isHandled && !isSpam && (isActionable || m.urgency === 'urgent' || isInterview);
          if (currentFilter === 'interviews') return isInterview && !isSpam;
          if (currentFilter === 'deadlines') return !!m.deadline && !isHandled && !isSpam;
          if (currentFilter === 'opportunities') return !isSpam && (['job', 'hackathon', 'competition'].includes(m.category) || ['job_opportunity', 'hackathon', 'competition'].includes(m.intent));
          if (currentFilter === 'review_queue') return m.intent === 'uncertain' || m.spam_status === 'suspected_spam';
          if (currentFilter === 'suppressed') return isSpam;
          if (currentFilter === 'handled') return isHandled;
          return true; // 'all'
        });
      }

      function setFilter(filter) {
        currentFilter = filter;
      }

      function selectEmail(id) {
        selectedEmailId = id;
      }

      function renderList() {
        const filtered = getFilteredEmails();
        if (filtered.length > 0) {
          if (!selectedEmailId || !emails.some(e => e.message_id === selectedEmailId)) {
            selectEmail(filtered[0].message_id);
          }
        } else {
          if (!selectedEmailId || !emails.some(e => e.message_id === selectedEmailId)) {
            selectedEmailId = null;
          }
        }
      }

      // Simulate loadData() logic
      if (!deepLinkProcessed) {
        deepLinkProcessed = true;
        const urlParams = new URLSearchParams(urlSearch);
        const rawEmail = urlParams.get('email');
        if (rawEmail && typeof rawEmail === 'string') {
          const cleanId = rawEmail.trim();
          if (cleanId) {
            const target = emails.find(e => e.message_id === cleanId);
            if (target) {
              const isVisible = getFilteredEmails().some(e => e.message_id === cleanId);
              if (!isVisible) {
                let targetFilter = 'all';
                if (target.acknowledged_at) {
                  targetFilter = 'handled';
                } else if (target.spam_status === 'confirmed_spam' || target.spam_status === 'suspected_spam' || target.intent === 'promotional') {
                  targetFilter = 'suppressed';
                } else if (['interview_invitation', 'interview_scheduling', 'interview_confirmation'].includes(target.intent) || (target.subject || '').toLowerCase().includes('interview')) {
                  targetFilter = 'interviews';
                } else if (target.deadline) {
                  targetFilter = 'deadlines';
                } else if (['job', 'hackathon', 'competition'].includes(target.category) || ['job_opportunity', 'hackathon', 'competition'].includes(target.intent)) {
                  targetFilter = 'opportunities';
                }
                setFilter(targetFilter);
              }
              selectedEmailId = cleanId;
              renderList();
              selectEmail(cleanId);
              return { selectedEmailId, currentFilter, toastMessage };
            } else {
              showToast('Email specified in link not found in local records');
            }
          }
        }
      }

      renderList();
      return { selectedEmailId, currentFilter, toastMessage };
    }

    // 1. Valid deep link to first email
    const r1 = runScenario({ urlSearch: '?email=msg-1-needs-action' });
    if (r1.selectedEmailId !== 'msg-1-needs-action') throw new Error('Failed r1: ' + JSON.stringify(r1));

    // 2. Valid deep link to non-first email
    const r2 = runScenario({ urlSearch: '?email=msg-2-interview' });
    if (r2.selectedEmailId !== 'msg-2-interview') throw new Error('Failed r2: ' + JSON.stringify(r2));

    // 3. Target outside current category filter (handled message in archive)
    const r3 = runScenario({ urlSearch: '?email=msg-3-handled' });
    if (r3.selectedEmailId !== 'msg-3-handled') throw new Error('Failed r3: ' + JSON.stringify(r3));
    if (r3.currentFilter !== 'handled') throw new Error('Filter was not switched to handled: ' + r3.currentFilter);

    // 4. Target in suppressed spam
    const r4 = runScenario({ urlSearch: '?email=msg-4-suppressed' });
    if (r4.selectedEmailId !== 'msg-4-suppressed' || r4.currentFilter !== 'suppressed') throw new Error('Failed r4');

    // 5. Unknown message ID
    const r5 = runScenario({ urlSearch: '?email=non-existent-id' });
    if (r5.selectedEmailId !== 'msg-1-needs-action') throw new Error('Failed r5: should fallback to first visible email');
    if (!r5.toastMessage) throw new Error('Failed r5: toast message should be shown');

    // 6. Malformed message ID (spaces, empty)
    const r6 = runScenario({ urlSearch: '?email=   ' });
    if (r6.selectedEmailId !== 'msg-1-needs-action') throw new Error('Failed r6');

    // 7. Missing query parameters (normal first load)
    const r7 = runScenario({ urlSearch: '' });
    if (r7.selectedEmailId !== 'msg-1-needs-action') throw new Error('Failed r7');

    // 8. Empty inbox
    const r8 = runScenario({ urlSearch: '?email=msg-1-needs-action', emails: [] });
    if (r8.selectedEmailId !== null) throw new Error('Failed r8: expected null on empty inbox');

    // 9. Automatic refresh does not override manual user selection
    const r9 = runScenario({ urlSearch: '?email=msg-1-needs-action', initialSelectedId: 'msg-2-interview', isSubsequentRefresh: true });
    if (r9.selectedEmailId !== 'msg-2-interview') throw new Error('Failed r9: refresh overwrote selection');

    console.log(JSON.stringify({ success: true }));
    """
    proc = subprocess.run(["node", "-e", js_test_script], capture_output=True, text=True)
    assert proc.returncode == 0, f"Node script failed:\n{proc.stderr}"
    data = json.loads(proc.stdout)
    assert data["success"] is True
