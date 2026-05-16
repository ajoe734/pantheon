from __future__ import annotations

import json
import sys
from pathlib import Path


OPTIMIZER_DIR = Path(__file__).resolve().parent
if str(OPTIMIZER_DIR) not in sys.path:
    sys.path.insert(0, str(OPTIMIZER_DIR))

from fastapi.testclient import TestClient  # noqa: E402

from main import app  # noqa: E402
from portfolio_synthesis import (  # noqa: E402
    PersonaAllocationProposal,
    PortfolioSynthesizer,
    SynthesisMethod,
    validate_allocation_policy_artifact_json,
)


SCHEMA_PATH = OPTIMIZER_DIR / "portfolio_synthesis" / "allocation_policy_artifact.schema.json"


def proposal_payload(**overrides):
    payload = {
        "proposal_id": "pap-artifact-alpha",
        "persona_id": "persona-alpha",
        "capital_pool_id": "pool-paper",
        "scope_ref": "paper",
        "target_type": "asset",
        "directions": ["long"],
        "target_weights": {"2330.TW": 0.4, "0050.TW": 0.4},
        "conviction": 0.8,
        "uncertainty": 0.1,
        "rationale_ref": "memo-alpha",
        "regime_ref": "regime-risk-on",
        "valid_from": "2026-05-15T14:00:00Z",
        "valid_to": "2026-05-22T14:00:00Z",
        "evidence_refs": ["evidence://pap-artifact-alpha"],
        "created_at": "2026-05-15T14:00:00Z",
        "reliability_score": 0.9,
        "regime_fit_score": 1.0,
        "governance_multiplier": 1.0,
        "metadata": {"strategy_family": "tw_equity"},
    }
    payload.update(overrides)
    return payload


def test_schema_validates_synthesizer_artifact_output() -> None:
    artifact, log = PortfolioSynthesizer().synthesize_with_log(
        [
            PersonaAllocationProposal(**proposal_payload()),
            PersonaAllocationProposal(
                **proposal_payload(
                    proposal_id="pap-artifact-beta",
                    persona_id="persona-beta",
                    target_weights={"2330.TW": 0.1, "2454.TW": 0.5},
                    conviction=0.2,
                    created_at="2026-05-15T14:01:00Z",
                    evidence_refs=["evidence://pap-artifact-beta"],
                )
            ),
        ],
        capital_pool_id="pool-paper",
        scope_ref="paper",
        constraints_bundle={"max_names": 4, "environment": "paper"},
        risk_budget=0.25,
    )
    payload = artifact.to_dict()

    assert payload["synthesis_method"] == SynthesisMethod.WEIGHTED_FUSION.value
    assert payload["conflict_resolution_log_id"] == log.log_id
    assert payload["constraints_bundle"] == {"max_names": 4, "environment": "paper"}
    assert payload["risk_budget"] == 0.25
    assert validate_allocation_policy_artifact_json(payload) == []

    try:
        from jsonschema import Draft7Validator, FormatChecker
    except ImportError:
        return

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft7Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(payload), key=lambda error: error.path)
    assert [error.message for error in errors] == []


def test_api_returns_full_allocation_policy_artifact_and_conflict_log() -> None:
    response = TestClient(app).post(
        "/api/optimizer/synthesize",
        json={
            "proposals": [
                proposal_payload(),
                proposal_payload(
                    proposal_id="pap-artifact-beta-api",
                    persona_id="persona-beta",
                    target_weights={"2330.TW": 0.1, "2454.TW": 0.5},
                    conviction=0.2,
                    created_at="2026-05-15T14:01:00Z",
                    evidence_refs=["evidence://pap-artifact-beta-api"],
                ),
            ],
            "capital_pool_id": "pool-paper",
            "scope_ref": "paper",
            "constraints_bundle": {"max_names": 4, "environment": "paper"},
            "risk_budget": 0.25,
        },
    )

    assert response.status_code == 201
    payload = response.json()
    artifact = payload["allocation_policy_artifact"]
    log = payload["conflict_resolution_log"]

    assert payload["outcome"] == "artifact"
    assert payload["artifact_id"] == artifact["artifact_id"]
    assert payload["conflict_resolution_log_id"] == log["log_id"]
    assert artifact["conflict_resolution_log_id"] == log["log_id"]
    assert artifact["sponsor_persona_id"] == payload["sponsor_persona_id"]
    assert artifact["provenance_refs"] == ["pap-artifact-alpha", "pap-artifact-beta-api"]
    assert artifact["constraints_bundle"] == {"max_names": 4, "environment": "paper"}
    assert log["proposal_ids"] == ["pap-artifact-alpha", "pap-artifact-beta-api"]
    assert validate_allocation_policy_artifact_json(artifact) == []

    readback = TestClient(app).get(f"/api/optimizer/policies/{artifact['artifact_id']}")
    assert readback.status_code == 200
    assert readback.json() == artifact


def test_allocation_policy_artifact_validation_rejects_incomplete_output() -> None:
    errors = validate_allocation_policy_artifact_json(
        {
            "artifact_id": "alloc-invalid",
            "capital_pool_id": "pool-paper",
            "scope_ref": "paper",
            "sponsor_persona_id": "persona-alpha",
            "synthesis_method": "weighted_fusion",
            "target_weights": {"2330.TW": 0.7, "0050.TW": 0.4},
            "created_at": "2026-05-15T14:00:00Z",
            "provenance_refs": ["pap-artifact-alpha"],
            "conflict_resolution_log_id": "conflict-log-001",
        }
    )

    assert any("target_weights sum" in error for error in errors)
