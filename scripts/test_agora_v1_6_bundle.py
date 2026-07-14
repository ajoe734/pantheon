from __future__ import annotations

import hashlib
import json
from pathlib import Path

from jsonschema import Draft7Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
AGORA_SPECS = ROOT / "services/control-plane/specs/agora"

REQUIRED_DEFINITIONS = {
    "PersonaOpinionConsultationEvent",
    "GovernedActionAuthorityRequest",
    "WinnerBranchCompleteness",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_schema(name: str) -> dict:
    return json.loads((AGORA_SPECS / "v7" / name).read_text(encoding="utf-8"))


def _opinion_event(**overrides) -> dict:
    payload = {
        "spec_version": "1.0",
        "event_id": "evt-1",
        "event_type": "opinion_requested",
        "interaction_id": "int-1",
        "topic": "risk check",
        "requester": {
            "actor_type": "persona_session",
            "actor_id": "persona-a",
            "session_id": "sess-1",
        },
        "context_refs": [],
        "status": "open",
        "no_capital_authority_proof": "persona_interaction_event_no_capital_or_order_authority",
        "trace_id": "trace-1",
        "created_at": "2026-07-12T00:00:00Z",
    }
    payload.update(overrides)
    return payload


def _authority_request(**overrides) -> dict:
    payload = {
        "spec_version": "1.0",
        "authority_request_id": "gar-1",
        "action_type": "paper_promotion",
        "requested_by": {"actor_type": "human", "actor_id": "op-1"},
        "solo_eligibility": {"evaluated": True, "forbidden_solo_action": False},
        "decision": "authorized",
        "decision_by": {"actor_type": "human", "actor_id": "op-2"},
        "execution_authority_proof": "governed_action_authority_request_does_not_execute_command",
        "status": "resolved",
        "trace_id": "trace-2",
        "created_at": "2026-07-12T00:00:00Z",
        "resolved_at": "2026-07-12T01:00:00Z",
    }
    payload.update(overrides)
    return payload


def test_v1_6_bundle_extends_exact_v1_5_bytes() -> None:
    bundle = json.loads((AGORA_SPECS / "bundle_index.v1_6.json").read_text(encoding="utf-8"))

    assert bundle["bundle_version"] == "1.6"
    assert bundle["extends"] == {
        "bundle_path": "services/control-plane/specs/agora/bundle_index.v1_5.json",
        "bundle_version": "1.5",
        "bundle_index_sha256": _sha256(AGORA_SPECS / "bundle_index.v1_5.json"),
    }


def test_v1_6_bundle_file_hashes_match_exact_bytes() -> None:
    bundle = json.loads((AGORA_SPECS / "bundle_index.v1_6.json").read_text(encoding="utf-8"))

    for rel_path, expected_hash in bundle["files"].items():
        path = ROOT / "services/control-plane" / rel_path
        assert path.exists(), rel_path
        assert _sha256(path) == expected_hash


def test_v1_6_required_definition_checksums_match_schema_file_hashes() -> None:
    bundle = json.loads((AGORA_SPECS / "bundle_index.v1_6.json").read_text(encoding="utf-8"))

    assert set(bundle["required_definition_checksums"]) == REQUIRED_DEFINITIONS
    assert bundle["required_definition_checksums"]["PersonaOpinionConsultationEvent"] == _sha256(
        AGORA_SPECS / "v7/persona_opinion_consultation_event.schema.json"
    )
    assert bundle["required_definition_checksums"]["GovernedActionAuthorityRequest"] == _sha256(
        AGORA_SPECS / "v7/governed_action_authority_request.schema.json"
    )
    assert bundle["required_definition_checksums"]["WinnerBranchCompleteness"] == _sha256(
        AGORA_SPECS / "v7/winner_branch_completeness.schema.json"
    )


def test_v1_6_capability_manifest_wiring() -> None:
    manifest = json.loads((AGORA_SPECS / "v7/capability_manifest_v1_6.json").read_text(encoding="utf-8"))

    assert manifest["manifest_version"] == "1.6"
    assert manifest["schema_bundle_index"] == "services/control-plane/specs/agora/bundle_index.v1_6.json"
    assert set(manifest["required_definition_checksums"]) == REQUIRED_DEFINITIONS
    assert manifest["safety_boundary"] == {
        "direct_order_route": False,
        "capital_binding": False,
        "command_execution": False,
        "schema_validator_required": True,
    }


def test_opinion_event_accepts_canonical_persona_session_requester() -> None:
    schema = _load_schema("persona_opinion_consultation_event.schema.json")
    validator = Draft7Validator(schema, format_checker=FormatChecker())
    errors = list(validator.iter_errors(_opinion_event()))
    assert errors == []


def test_opinion_event_rejects_null_session_id_for_persona_session_requester() -> None:
    """PERSONA_RUNTIME_MODEL.md section 13: every persona_session actor must be
    mediated through an actual SessionPersona; a present-but-null session_id is
    not proof of mediation."""
    schema = _load_schema("persona_opinion_consultation_event.schema.json")
    validator = Draft7Validator(schema, format_checker=FormatChecker())
    bad = _opinion_event(
        requester={"actor_type": "persona_session", "actor_id": "persona-a", "session_id": None}
    )
    errors = list(validator.iter_errors(bad))
    assert errors, "null session_id on a persona_session actor must fail validation"


def test_opinion_event_rejects_null_session_id_for_persona_session_participant() -> None:
    schema = _load_schema("persona_opinion_consultation_event.schema.json")
    validator = Draft7Validator(schema, format_checker=FormatChecker())
    bad = _opinion_event(
        participants=[
            {
                "actor": {
                    "actor_type": "persona_session",
                    "actor_id": "persona-b",
                    "session_id": None,
                },
                "role": "responder",
            }
        ]
    )
    errors = list(validator.iter_errors(bad))
    assert errors, "null session_id on a persona_session participant must fail validation"


def test_authority_request_accepts_canonical_resolved_payload() -> None:
    schema = _load_schema("governed_action_authority_request.schema.json")
    validator = Draft7Validator(schema, format_checker=FormatChecker())
    errors = list(validator.iter_errors(_authority_request()))
    assert errors == []


def test_authority_request_rejects_resolved_with_null_decision_by() -> None:
    """PERSONA_RUNTIME_MODEL.md section 5: a resolved authority decision must
    carry an auditable governance-owner identity; a present-but-null
    decision_by is not proof of governance ownership."""
    schema = _load_schema("governed_action_authority_request.schema.json")
    validator = Draft7Validator(schema, format_checker=FormatChecker())
    bad = _authority_request(decision_by=None)
    errors = list(validator.iter_errors(bad))
    assert errors, "resolved status with null decision_by must fail validation"


def test_authority_request_rejects_resolved_with_null_resolved_at() -> None:
    schema = _load_schema("governed_action_authority_request.schema.json")
    validator = Draft7Validator(schema, format_checker=FormatChecker())
    bad = _authority_request(resolved_at=None)
    errors = list(validator.iter_errors(bad))
    assert errors, "resolved status with null resolved_at must fail validation"


def test_authority_request_rejects_resolved_with_null_decision_by_and_resolved_at() -> None:
    schema = _load_schema("governed_action_authority_request.schema.json")
    validator = Draft7Validator(schema, format_checker=FormatChecker())
    bad = _authority_request(decision_by=None, resolved_at=None)
    errors = list(validator.iter_errors(bad))
    assert len(errors) >= 2, "both null decision_by and null resolved_at must be flagged"


def test_authority_request_rejects_null_session_id_for_persona_session_decision_by() -> None:
    schema = _load_schema("governed_action_authority_request.schema.json")
    validator = Draft7Validator(schema, format_checker=FormatChecker())
    bad = _authority_request(
        decision_by={"actor_type": "persona_session", "actor_id": "persona-c", "session_id": None}
    )
    errors = list(validator.iter_errors(bad))
    assert errors, "null session_id on a persona_session decision_by must fail validation"


def test_authority_request_non_resolved_status_does_not_require_decision_by() -> None:
    schema = _load_schema("governed_action_authority_request.schema.json")
    validator = Draft7Validator(schema, format_checker=FormatChecker())
    draft = _authority_request(
        status="draft",
        decision="pending",
        decision_by=None,
        resolved_at=None,
    )
    errors = list(validator.iter_errors(draft))
    assert errors == []


def _winner_branch_completeness(**overrides) -> dict:
    payload = {
        "spec_version": "1.0",
        "completeness_id": "wc-1",
        "strategy_ref": "strat-1",
        "assessed_by_persona_id": "persona-a",
        "overall_grade": "mostly_complete",
        "dimensions": [
            {"dimension": "hypothesis", "grade": "complete"},
            {"dimension": "data_dependencies", "grade": "complete"},
            {"dimension": "market_scope", "grade": "complete"},
            {"dimension": "evaluation_plan", "grade": "complete"},
            {"dimension": "risk_constraints", "grade": "partial"},
            {"dimension": "execution_profile", "grade": "complete"},
            {"dimension": "governance", "grade": "complete"},
        ],
        "winner_branch_blocks": [
            {"block_name": "market_scope", "grade": "confirmed", "mapped_dimension": "market_scope"},
            {"block_name": "insider_branch_mapping", "grade": "confirmed", "mapped_dimension": "data_dependencies"},
            {"block_name": "winner_branch_scoring", "grade": "confirmed", "mapped_dimension": "data_dependencies"},
            {"block_name": "migration_reverse_flow", "grade": "confirmed", "mapped_dimension": "data_dependencies"},
            {"block_name": "event_lead", "grade": "confirmed", "mapped_dimension": "hypothesis"},
            {"block_name": "signal_formation", "grade": "confirmed", "mapped_dimension": "hypothesis"},
            {"block_name": "entry_holding", "grade": "confirmed", "mapped_dimension": "evaluation_plan"},
            {"block_name": "add_reduce_exit", "grade": "confirmed", "mapped_dimension": "evaluation_plan"},
            {"block_name": "sizing_leverage", "grade": "weak", "mapped_dimension": "risk_constraints"},
            {"block_name": "cost_liquidity_capacity", "grade": "confirmed", "mapped_dimension": "execution_profile"},
            {"block_name": "validation_backtest_refutation", "grade": "confirmed", "mapped_dimension": "execution_profile"},
            {"block_name": "monitoring_update", "grade": "confirmed", "mapped_dimension": "governance"},
        ],
        "assessed_at": "2026-07-12T00:00:00Z",
    }
    payload.update(overrides)
    return payload


def test_winner_branch_completeness_accepts_canonical() -> None:
    schema = _load_schema("winner_branch_completeness.schema.json")
    validator = Draft7Validator(schema, format_checker=FormatChecker())
    errors = list(validator.iter_errors(_winner_branch_completeness()))
    assert errors == []


def test_winner_branch_completeness_rejects_missing_required() -> None:
    schema = _load_schema("winner_branch_completeness.schema.json")
    validator = Draft7Validator(schema, format_checker=FormatChecker())
    bad = _winner_branch_completeness()
    del bad["winner_branch_blocks"]
    errors = list(validator.iter_errors(bad))
    assert errors


def test_winner_branch_completeness_rejects_duplicate_dimensions() -> None:
    schema = _load_schema("winner_branch_completeness.schema.json")
    validator = Draft7Validator(schema, format_checker=FormatChecker())

    # 7 items, but "governance" is replaced with a duplicate "hypothesis"
    bad = _winner_branch_completeness()
    bad["dimensions"] = [
        {"dimension": "hypothesis", "grade": "complete"},
        {"dimension": "data_dependencies", "grade": "complete"},
        {"dimension": "market_scope", "grade": "complete"},
        {"dimension": "evaluation_plan", "grade": "complete"},
        {"dimension": "risk_constraints", "grade": "partial"},
        {"dimension": "execution_profile", "grade": "complete"},
        {"dimension": "hypothesis", "grade": "complete"},  # duplicate
    ]
    errors = list(validator.iter_errors(bad))
    assert errors, "Duplicate dimensions (missing governance) must fail validation"


def test_winner_branch_completeness_rejects_incorrect_dimension_count() -> None:
    schema = _load_schema("winner_branch_completeness.schema.json")
    validator = Draft7Validator(schema, format_checker=FormatChecker())

    # 6 items (missing governance)
    bad_short = _winner_branch_completeness()
    bad_short["dimensions"] = bad_short["dimensions"][:-1]
    errors_short = list(validator.iter_errors(bad_short))
    assert errors_short, "Fewer than 7 dimensions must fail validation"

    # 8 items
    bad_long = _winner_branch_completeness()
    bad_long["dimensions"].append({"dimension": "governance", "grade": "complete"})
    errors_long = list(validator.iter_errors(bad_long))
    assert errors_long, "More than 7 dimensions must fail validation"


def test_winner_branch_completeness_rejects_duplicate_winner_branch_blocks() -> None:
    schema = _load_schema("winner_branch_completeness.schema.json")
    validator = Draft7Validator(schema, format_checker=FormatChecker())

    # 12 items, but "monitoring_update" is replaced with duplicate "market_scope"
    bad = _winner_branch_completeness()
    bad["winner_branch_blocks"][-1] = {
        "block_name": "market_scope",
        "grade": "confirmed",
        "mapped_dimension": "market_scope",
    }
    errors = list(validator.iter_errors(bad))
    assert errors, "Duplicate blocks (missing monitoring_update) must fail validation"


def test_winner_branch_completeness_rejects_incorrect_block_count() -> None:
    schema = _load_schema("winner_branch_completeness.schema.json")
    validator = Draft7Validator(schema, format_checker=FormatChecker())

    # 11 items
    bad_short = _winner_branch_completeness()
    bad_short["winner_branch_blocks"] = bad_short["winner_branch_blocks"][:-1]
    errors_short = list(validator.iter_errors(bad_short))
    assert errors_short, "Fewer than 12 blocks must fail validation"

    # 13 items
    bad_long = _winner_branch_completeness()
    bad_long["winner_branch_blocks"].append({
        "block_name": "monitoring_update",
        "grade": "confirmed",
        "mapped_dimension": "governance",
    })
    errors_long = list(validator.iter_errors(bad_long))
    assert errors_long, "More than 12 blocks must fail validation"

