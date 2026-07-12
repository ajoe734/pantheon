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
