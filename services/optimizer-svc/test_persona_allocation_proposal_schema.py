"""
Tests for the MGMT-SYN-001 PersonaAllocationProposal schema contract.

Run:
    python3 -m unittest services/optimizer-svc/test_persona_allocation_proposal_schema.py
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from portfolio_synthesis import (  # noqa: E402
    PersonaAllocationProposal,
    SynthesisError,
    validate_persona_allocation_proposal_json,
)

SCHEMA_PATH = (
    Path(__file__).resolve().parent
    / "portfolio_synthesis"
    / "persona_allocation_proposal.schema.json"
)


def valid_payload(**overrides):
    payload = {
        "proposal_id": "pap-001",
        "persona_id": "persona-alpha",
        "capital_pool_id": "pool-001",
        "scope_ref": "paper",
        "target_type": "asset",
        "directions": ["long"],
        "target_weights": {"2330.TW": 0.55, "0050.TW": 0.25},
        "conviction": 0.82,
        "uncertainty": 0.18,
        "rationale_ref": "memo-001",
        "regime_ref": "regime-risk-on-001",
        "valid_from": "2026-05-15T00:00:00Z",
        "valid_to": None,
        "evidence_refs": ["ooda-MGMT-OODA-M5-persona-synthesis"],
        "created_at": "2026-05-15T14:53:01Z",
        "reliability_score": 0.91,
        "regime_fit_score": 0.87,
        "governance_multiplier": 1.0,
        "metadata": {
            "asset_classes": ["equity"],
            "strategy_family": "tw_large_cap_momentum",
        },
    }
    payload.update(overrides)
    return payload


class TestPersonaAllocationProposalSchema(unittest.TestCase):
    def test_schema_file_is_valid_json(self) -> None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertEqual(schema["title"], "PersonaAllocationProposal")
        self.assertIn("target_weights", schema["properties"])
        self.assertIn("created_at", schema["required"])

    def test_schema_accepts_canonical_payload(self) -> None:
        try:
            from jsonschema import Draft7Validator, FormatChecker
        except ImportError:
            self.skipTest("jsonschema is not installed")

        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        validator = Draft7Validator(schema, format_checker=FormatChecker())
        errors = sorted(validator.iter_errors(valid_payload()), key=lambda e: e.path)
        self.assertEqual([], [error.message for error in errors])

    def test_schema_rejects_unknown_target_type(self) -> None:
        try:
            from jsonschema import Draft7Validator
        except ImportError:
            self.skipTest("jsonschema is not installed")

        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        validator = Draft7Validator(schema)
        errors = list(validator.iter_errors(valid_payload(target_type="runtime")))
        self.assertTrue(any("is not one of" in error.message for error in errors))

    def test_dataclass_serializes_schema_fields(self) -> None:
        proposal = PersonaAllocationProposal(**valid_payload())
        payload = proposal.to_dict()
        self.assertEqual(payload["evidence_refs"], ["ooda-MGMT-OODA-M5-persona-synthesis"])
        self.assertEqual(payload["created_at"], "2026-05-15T14:53:01Z")
        self.assertEqual([], validate_persona_allocation_proposal_json(payload))

    def test_dataclass_rejects_invalid_direction(self) -> None:
        with self.assertRaises(SynthesisError):
            PersonaAllocationProposal(**valid_payload(directions=["rotate"]))

    def test_dataclass_rejects_duplicate_directions(self) -> None:
        with self.assertRaisesRegex(SynthesisError, "directions entries must be unique"):
            PersonaAllocationProposal(**valid_payload(directions=["long", "long"]))

    def test_dataclass_rejects_invalid_target_weight_key(self) -> None:
        with self.assertRaisesRegex(SynthesisError, "target_weights keys must match pattern"):
            PersonaAllocationProposal(**valid_payload(target_weights={"2330 TW": 0.7}))

    def test_api_rejects_omitted_created_at(self) -> None:
        from fastapi.testclient import TestClient
        from main import app

        proposal = valid_payload()
        proposal.pop("created_at")

        response = TestClient(app).post(
            "/api/optimizer/synthesize",
            json={
                "proposals": [proposal],
                "capital_pool_id": "pool-001",
                "scope_ref": "paper",
            },
        )

        self.assertEqual(422, response.status_code)
        self.assertIn("created_at", response.text)

    def test_dataclass_rejects_weight_sum_above_one(self) -> None:
        errors = validate_persona_allocation_proposal_json(
            valid_payload(target_weights={"2330.TW": 0.7, "0050.TW": 0.4})
        )
        self.assertTrue(any("target_weights sum" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
