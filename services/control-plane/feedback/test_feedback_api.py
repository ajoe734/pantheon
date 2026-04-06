import sys
import tempfile
import unittest
from pathlib import Path

try:
    from fastapi.testclient import TestClient
except ModuleNotFoundError:  # pragma: no cover - depends on local dev extras
    TestClient = None

FEEDBACK_DIR = Path(__file__).resolve().parent
if str(FEEDBACK_DIR) not in sys.path:
    sys.path.insert(0, str(FEEDBACK_DIR))

if TestClient is not None:
    from main import create_app
else:  # pragma: no cover - depends on local dev extras
    create_app = None


@unittest.skipUnless(TestClient is not None, "fastapi test dependencies are not installed")
class TestTraderFeedbackAPI(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store_path = Path(self.temp_dir.name) / "trader_feedback.jsonl"
        self.client = TestClient(create_app(self.store_path))

    def tearDown(self):
        self.temp_dir.cleanup()

    def _event(self, **overrides):
        payload = {
            "event_id": "evt-001",
            "event_type": "approve",
            "created_at": "2026-04-05T13:45:00Z",
            "actor_id": "trader-01",
            "actor_role": "operator",
            "channel": "console",
            "rationale": "Risk review passed and registry candidate is acceptable.",
            "target": {
                "registry_id": "reg-123",
                "strategy_id": "alpha-mean-reversion",
                "artifact_version": "1.2.0",
                "artifact_type": "strategy_spec",
                "promotion_state": "candidate",
            },
        }
        payload.update(overrides)
        return payload

    def test_capture_approve_event(self):
        response = self.client.post("/trader-feedback", json=self._event())
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["status"], "created")
        self.assertEqual(body["event"]["target"]["registry_id"], "reg-123")
        self.assertTrue(self.store_path.exists())

    def test_duplicate_event_id_is_idempotent(self):
        first = self.client.post("/trader-feedback", json=self._event())
        second = self.client.post("/trader-feedback", json=self._event())
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["status"], "duplicate")

    def test_edit_event_requires_edits(self):
        response = self.client.post(
            "/trader-feedback",
            json=self._event(event_id="evt-edit", event_type="edit"),
        )
        self.assertEqual(response.status_code, 422)

    def test_rationale_event_requires_rationale(self):
        payload = self._event(
            event_id="evt-rationale",
            event_type="rationale",
        )
        payload.pop("rationale")
        response = self.client.post("/trader-feedback", json=payload)
        self.assertEqual(response.status_code, 422)

    def test_reviewer_role_is_rejected(self):
        response = self.client.post(
            "/trader-feedback",
            json=self._event(event_id="evt-reviewer", actor_role="reviewer"),
        )
        self.assertEqual(response.status_code, 422)

    def test_query_filters(self):
        approve = self._event()
        reject = self._event(
            event_id="evt-002",
            event_type="reject",
            rationale="Trader rejected due to oversized turnover.",
        )
        self.client.post("/trader-feedback", json=approve)
        self.client.post("/trader-feedback", json=reject)

        response = self.client.get(
            "/trader-feedback",
            params={
                "strategy_id": "alpha-mean-reversion",
                "event_type": "reject",
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["events"][0]["event_type"], "reject")

    def test_query_rejects_invalid_created_after_timestamp(self):
        response = self.client.get(
            "/trader-feedback",
            params={"created_after": "not-a-timestamp"},
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("created_after", response.json()["detail"])

    def test_query_rejects_inverted_created_at_window(self):
        response = self.client.get(
            "/trader-feedback",
            params={
                "created_after": "2026-04-05T14:00:00Z",
                "created_before": "2026-04-05T13:00:00Z",
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("created_after", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
