import json
import tempfile
import unittest
from pathlib import Path

import jsonschema

import sys

FEEDBACK_DIR = Path(__file__).resolve().parent
if str(FEEDBACK_DIR) not in sys.path:
    sys.path.insert(0, str(FEEDBACK_DIR))

from schema_validation import build_trader_feedback_validator
from store import QueryFilterValidationError, QueryFilters, TraderFeedbackStore, build_query_filters

ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = ROOT / "services" / "feedback" / "schema" / "trader_feedback_event.schema.json"
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
VALIDATOR = build_trader_feedback_validator(SCHEMA)


class TestTraderFeedbackSchemaAndStore(unittest.TestCase):
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

    def test_schema_accepts_approve_event(self):
        VALIDATOR.validate(self._event())

    def test_schema_rejects_reviewer_role(self):
        with self.assertRaises(jsonschema.ValidationError):
            VALIDATOR.validate(self._event(actor_role="reviewer"))

    def test_schema_rejects_edit_without_edits(self):
        with self.assertRaises(jsonschema.ValidationError):
            VALIDATOR.validate(self._event(event_type="edit"))

    def test_schema_rejects_rationale_without_rationale(self):
        payload = self._event(event_type="rationale")
        payload.pop("rationale")
        with self.assertRaises(jsonschema.ValidationError):
            VALIDATOR.validate(payload)

    def test_schema_rejects_invalid_timestamp_format(self):
        with self.assertRaises(jsonschema.ValidationError):
            VALIDATOR.validate(self._event(created_at="not-a-timestamp"))

    def test_schema_rejects_target_without_governed_linkage(self):
        with self.assertRaises(jsonschema.ValidationError):
            VALIDATOR.validate(
                self._event(
                    target={
                        "strategy_id": "alpha-mean-reversion",
                        "promotion_state": "candidate",
                    }
                )
            )

    def test_store_append_deduplicates_and_filters(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = Path(temp_dir) / "feedback.jsonl"
            store = TraderFeedbackStore(store_path)

            created, first = store.append(self._event())
            duplicate, second = store.append(self._event())
            store.append(
                self._event(
                    event_id="evt-002",
                    event_type="reject",
                    rationale="Trader rejected due to oversized turnover.",
                )
            )

            self.assertTrue(created)
            self.assertFalse(duplicate)
            self.assertEqual(first["event_id"], second["event_id"])

            filtered = store.list(
                QueryFilters(
                    strategy_id="alpha-mean-reversion",
                    event_type="reject",
                )
            )
            self.assertEqual(len(filtered), 1)
            self.assertEqual(filtered[0]["event_type"], "reject")

    def test_build_query_filters_rejects_invalid_created_after_timestamp(self):
        with self.assertRaisesRegex(QueryFilterValidationError, "created_after"):
            build_query_filters(created_after="not-a-timestamp")

    def test_build_query_filters_rejects_invalid_created_before_timestamp(self):
        with self.assertRaisesRegex(QueryFilterValidationError, "created_before"):
            build_query_filters(created_before="not-a-timestamp")

    def test_build_query_filters_rejects_inverted_created_at_window(self):
        with self.assertRaisesRegex(
            QueryFilterValidationError,
            "created_after must be less than or equal to created_before",
        ):
            build_query_filters(
                created_after="2026-04-05T14:00:00Z",
                created_before="2026-04-05T13:00:00Z",
            )


if __name__ == "__main__":
    unittest.main()
