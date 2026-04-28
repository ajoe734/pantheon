import os
import sys
import tempfile
import unittest
from fastapi.testclient import TestClient

# When running from repo root with 'python3 -m services.consultation.smoke_test',
# we should use relative or absolute package imports.
# To support direct execution as well, we can ensure the parent is in path,
# but the 'correct' way for this repo seems to be package-style.

try:
    from .main import app, store
    from .models import (
        ConsultRequestType,
        ConsultRequestStatus,
        MemoType,
        AuthorType,
        Recommendation,
        MemoStatus,
        GateHandoffStatus
    )
    from .store import ConsultationStore
except ImportError:
    # Fallback for direct execution if needed
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    from services.consultation.main import app, store
    from services.consultation.models import (
        ConsultRequestType,
        ConsultRequestStatus,
        MemoType,
        AuthorType,
        Recommendation,
        MemoStatus,
        GateHandoffStatus
    )
    from services.consultation.store import ConsultationStore

class ConsultationSmokeTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        # Use a temporary directory for store data during tests
        self.test_dir = tempfile.TemporaryDirectory()
        # We need to re-initialize the store with the test directory
        store.__init__(self.test_dir.name)

    def tearDown(self):
        self.test_dir.cleanup()

    def test_health_check(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "service": "consultation"})

    def test_consultation_lifecycle_with_audit_and_handoff(self):
        # 1. Create a request
        request_payload = {
            "request_type": "strategy_review",
            "requested_by": {"actor_type": "persona", "actor_id": "p-alpha"},
            "target_type": "strategy",
            "target_id": "strat-001",
            "trace_id": "trace-123"
        }
        response = self.client.post("/api/consult/requests", json=request_payload)
        self.assertEqual(response.status_code, 201)
        request_id = response.json()["request_id"]
        self.assertTrue(request_id.startswith("cr-"))

        # 2. Submit the request
        response = self.client.post(f"/api/consult/requests/{request_id}/submit")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "submitted")

        # 3. Assign a committee participant.
        participant_payload = {
            "participant_type": "committee",
            "participant_ref": "risk-committee",
            "role": "risk_reviewer",
            "trace_id": "trace-123",
            "initiated_by": {"actor_type": "human", "actor_id": "operator-7"}
        }
        response = self.client.post(
            f"/api/consult/requests/{request_id}/participants",
            json=participant_payload
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["request_id"], request_id)

        # 4. Attach governed evidence to the request.
        evidence_payload = {
            "evidence_ref": {
                "id": "ev-999",
                "evidence_type": "lineage_trace",
                "artifact_ref": "lineage://trace/trace-123",
                "description": "Replayable slippage evidence",
                "link": "workspace://evidence/ev-999"
            },
            "attached_by": {"actor_type": "persona", "actor_id": "p-beta"},
            "trace_id": "trace-123"
        }
        response = self.client.post(
            f"/api/consult/requests/{request_id}/evidence",
            json=evidence_payload
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["evidence_ref"]["id"], "ev-999")

        # 5. Add a transcript event
        event_payload = {
            "request_id": request_id,
            "event_type": "debate_statement",
            "actor": {"actor_type": "persona", "actor_id": "p-beta"},
            "content": {"message": "I have concerns about the slippage model."},
            "evidence_refs": ["ev-999"]
        }
        response = self.client.post(f"/api/consult/requests/{request_id}/events", json=event_payload)
        self.assertEqual(response.status_code, 201)

        # 6. Submit a memo
        memo_payload = {
            "request_id": request_id,
            "memo_type": "committee_summary",
            "author_type": "persona",
            "author_ref": "p-risk-analyst",
            "summary": "This strategy looks good but has some tail risk.",
            "findings": [
                {
                    "severity": "medium",
                    "category": "risk",
                    "claim": "Tail risk is higher than reported",
                    "recommendation": "Lower capital allocation"
                }
            ],
            "recommendation": "approve_with_conditions",
            "trace_id": "trace-123"
        }
        response = self.client.post("/api/consult/memos", json=memo_payload)
        self.assertEqual(response.status_code, 201)
        memo_id = response.json()["memo_id"]
        self.assertEqual(response.json()["status"], "submitted")

        # 7. Publish the memo
        response = self.client.post(f"/api/consult/memos/{memo_id}/publish")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "published")

        # 8. Published memos are immutable at the store boundary.
        published_memo = store.get_memo(memo_id)
        published_memo.summary = "attempted overwrite"
        with self.assertRaises(ValueError):
            store.put_memo(published_memo)

        # 9. Create a gate handoff
        handoff_payload = {
            "request_id": request_id,
            "target_gate": "EP5-GOV-GATE",
            "memo_ids": [memo_id],
            "evidence_refs": ["ev-999"],
            "trace_id": "trace-123",
            "initiated_by": {"actor_type": "human", "actor_id": "operator-7"}
        }
        response = self.client.post("/api/consult/handoffs", json=handoff_payload)
        self.assertEqual(response.status_code, 201)
        handoff_id = response.json()["handoff_id"]
        self.assertTrue(handoff_id.startswith("gh-"))
        self.assertIn("ev-999", response.json()["evidence_refs"])
        self.assertEqual(response.json()["trace_id"], "trace-123")
        self.assertGreater(len(response.json()["audit_refs"]), 0)

        # 10. Verify audit log via store directly (or we could add an API for it)
        audit_events = store.list_audit_for_request(request_id)
        actions = [a.action for a in audit_events]
        self.assertIn("request_created", actions)
        self.assertIn("request_submitted", actions)
        self.assertIn("participant_assigned", actions)
        self.assertIn("evidence_attached", actions)
        self.assertIn("transcript_event_added", actions)
        self.assertIn("memo_submitted", actions)
        self.assertIn("memo_published", actions)
        self.assertIn("gate_handoff_created", actions)

        participant_audit = next(a for a in audit_events if a.action == "participant_assigned")
        self.assertEqual(participant_audit.actor_ref.actor_id, "operator-7")
        self.assertEqual(participant_audit.service_actor_ref.actor_id, "consultation-svc")
        handoff_audit = next(a for a in audit_events if a.action == "gate_handoff_created")
        self.assertEqual(handoff_audit.actor_ref.actor_id, "operator-7")
        self.assertEqual(handoff_audit.service_actor_ref.actor_id, "consultation-svc")

        # 11. Restart/replay preserves the full lifecycle and stable handoff refs.
        replayed = ConsultationStore(self.test_dir.name)
        replayed_request = replayed.get_request(request_id)
        self.assertEqual(replayed_request.status, ConsultRequestStatus.PUBLISHED)
        self.assertEqual(len(replayed.list_participants_for_request(request_id)), 1)
        self.assertEqual(len(replayed.list_evidence_for_request(request_id)), 1)
        self.assertEqual(len(replayed.get_transcript(request_id).events), 1)
        self.assertEqual(replayed.get_memo(memo_id).status, MemoStatus.PUBLISHED)
        self.assertEqual(replayed.get_handoff(handoff_id).audit_refs, response.json()["audit_refs"])
        self.assertEqual(
            [event.audit_id for event in replayed.list_audit_for_request(request_id)],
            [event.audit_id for event in audit_events],
        )
        self.assertGreater(len(replayed.outbox.load()), 0)

if __name__ == "__main__":
    unittest.main()
