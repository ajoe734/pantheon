"""AG-CAND-TRUTH-001-BE — Agora v1.12 candidate-truth bundle integrity tests.

Locks the additive candidate member truth projection contract:
  - bundle extends v1.11 without rewriting the parent index
  - file and per-definition hashes match exact contract bytes
  - OpenAPI and capability manifest publish the same routes
  - field states are available-with-provenance or typed-unavailable only
  - Sharpe-derived scores can never claim to be a generic confidence score
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml
from jsonschema import Draft7Validator, FormatChecker, RefResolver


ROOT = Path(__file__).resolve().parents[1]
AGORA_SPECS = ROOT / "services/control-plane/specs/agora"
SCHEMA_PATH = AGORA_SPECS / "v13/candidate_member_truth_projection.schema.json"
MANIFEST_PATH = AGORA_SPECS / "v13/capability_manifest_v1_12.json"
OPENAPI_PATH = ROOT / "services/control-plane/openapi/agora_v1_12.openapi.yaml"
BUNDLE_PATH = AGORA_SPECS / "bundle_index.v1_12.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stable_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _validator(name: str) -> Draft7Validator:
    schema = _schema()
    return Draft7Validator(
        schema["definitions"][name],
        resolver=RefResolver.from_schema(schema),
        format_checker=FormatChecker(),
    )


def _assert_valid(name: str, value: object) -> None:
    assert list(_validator(name).iter_errors(value)) == []


def _assert_invalid(name: str, value: object) -> None:
    assert list(_validator(name).iter_errors(value)) != []


def test_v1_12_bundle_extends_v1_11_without_rewriting_parent() -> None:
    bundle = json.loads(BUNDLE_PATH.read_text(encoding="utf-8"))

    assert bundle["bundle_version"] == "1.12"
    assert bundle["extends"] == {
        "bundle_path": "services/control-plane/specs/agora/bundle_index.v1_11.json",
        "bundle_version": "1.11",
        "bundle_index_sha256": _sha256(AGORA_SPECS / "bundle_index.v1_11.json"),
    }


def test_v1_12_bundle_hashes_lock_exact_contract_bytes() -> None:
    bundle = json.loads(BUNDLE_PATH.read_text(encoding="utf-8"))

    for relative_path, expected_hash in bundle["files"].items():
        path = ROOT / "services/control-plane" / relative_path
        assert path.exists(), relative_path
        assert _sha256(path) == expected_hash
    assert bundle["openapi"] == {
        "path": "services/control-plane/openapi/agora_v1_12.openapi.yaml",
        "sha256": _sha256(OPENAPI_PATH),
    }


def test_required_definition_checksums_are_complete_and_locked() -> None:
    bundle = json.loads(BUNDLE_PATH.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    definitions = _schema()["definitions"]

    assert set(bundle["required_definition_checksums"]) == set(definitions)
    assert set(manifest["required_definition_checksums"]) == set(definitions)
    for name, expected_hash in bundle["required_definition_checksums"].items():
        actual = hashlib.sha256(_stable_json(definitions[name]).encode()).hexdigest()
        assert actual == expected_hash, name


def test_openapi_and_manifest_publish_the_same_route_statuses() -> None:
    spec = yaml.safe_load(OPENAPI_PATH.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    openapi_routes = {
        f"{method.upper()} {path}"
        for path, path_item in spec["paths"].items()
        for method in path_item
        if method.lower() in {"get", "post", "put", "patch", "delete"}
    }
    manifest_routes = {
        route
        for capability in manifest["capabilities"]
        for route in capability["routes"]
    }

    assert openapi_routes == manifest_routes
    assert openapi_routes == {
        "GET /bff/agora/candidate-pools/{pool_id}/members",
        "GET /bff/agora/candidate-pools/{pool_id}/members/{artifact_id}",
    }
    assert spec["info"]["x-implementation-status"] == "implemented"
    assert {item["implementation_status"] for item in manifest["capabilities"]} == {"implemented"}


def test_openapi_defaults_to_the_current_replacement_dev_bff() -> None:
    spec = yaml.safe_load(OPENAPI_PATH.read_text(encoding="utf-8"))
    bff_default = spec["servers"][0]["variables"]["bff_base"]["default"]

    assert bff_default == "https://pantheon-lupin-dev-bff.35.201.204.12.sslip.io"
    assert "35.201.239.38" not in bff_default


def test_field_states_are_available_with_provenance_or_typed_unavailable() -> None:
    provenance = {
        "source_type": "candidate_pool_member",
        "source_ref": "candidate-pool-member:cpool-1:artifact-1",
        "as_of": "2026-07-22T00:00:00Z",
    }
    field_values = {
        "CandidateRationaleFieldState": {
            "kind": "score_component_attribution",
            "band": "discuss",
            "effective_score": 55.0,
            "top_components": [],
        },
        "CandidateConcernsFieldState": {
            "kind": "score_risk_attribution",
            "blockers": [],
            "penalty_components": [],
        },
        "CandidateNextEventFieldState": {
            "kind": "monitoring_schedule",
            "monitoring_state": "active",
            "review_due_at": None,
            "trigger_conditions": [],
        },
        "CandidateEvidenceFieldState": {
            "kind": "score_evidence_refs",
            "items": [{
                "component_id": "expected_value",
                "evidence_refs": ["artifact://evidence/expected-value"],
                "summary": None,
                "summary_redacted": True,
                "redaction_reason": "list_response",
            }],
            "total_refs": 1,
        },
        "CandidateDetailsFieldState": {
            "kind": "candidate_identity",
            "strategy_ref": "strategy://x",
            "lifecycle_state": "candidate",
            "created_at": "2026-07-22T00:00:00Z",
        },
    }
    for state_name, value in field_values.items():
        payload = {
            "availability": "available",
            "value": value,
            "provenance": provenance,
        }
        _assert_valid(state_name, payload)
        _assert_valid("CandidateFieldState", payload)
        _assert_invalid(state_name, {**payload, "value": None})

    # A field-specific state cannot accept another field's typed value.
    _assert_invalid("CandidateRationaleFieldState", {
        "availability": "available",
        "value": field_values["CandidateDetailsFieldState"],
        "provenance": provenance,
    })

    for reason in ("score_not_run", "no_governed_source", "not_recorded"):
        _assert_valid("CandidateFieldState", {"availability": "unavailable", "reason": reason})
        for state_name in field_values:
            _assert_valid(state_name, {"availability": "unavailable", "reason": reason})

    # Available without provenance, unavailable without a typed reason, and
    # free-text reasons are all contract violations.
    _assert_invalid("CandidateFieldState", {"availability": "available", "value": {}})
    _assert_invalid("CandidateFieldState", {"availability": "unavailable"})
    _assert_invalid("CandidateFieldState", {"availability": "unavailable", "reason": "static_sample"})


def test_score_semantics_reject_generic_confidence_presentation() -> None:
    _assert_valid("ScoreSemanticsEntry", {
        "kind": "sharpe_ratio",
        "availability": "available",
        "is_confidence_score": False,
        "transformation": "sharpe_ratio_from_producing_research_run",
        "source_ref": "research-run://x/1",
        "as_of": "2026-07-22T00:00:00Z",
    })
    _assert_invalid("ScoreSemanticsEntry", {
        "kind": "sharpe_ratio",
        "availability": "available",
        "is_confidence_score": True,
        "transformation": "sharpe_ratio_from_producing_research_run",
    })
    _assert_invalid("ScoreSemanticsEntry", {
        "kind": "confidence",
        "availability": "available",
        "is_confidence_score": False,
    })


def test_evidence_items_require_redaction_typing() -> None:
    _assert_valid("CandidateEvidenceItem", {
        "component_id": "expected_value",
        "label": "Expected value",
        "evidence_refs": ["evidence://artifact-1/expected_value"],
        "summary": None,
        "summary_redacted": True,
        "redaction_reason": "list_response",
    })
    _assert_invalid("CandidateEvidenceItem", {
        "component_id": "expected_value",
        "evidence_refs": [],
        "summary": None,
        "summary_redacted": True,
    })
    _assert_invalid("CandidateEvidenceItem", {
        "component_id": "expected_value",
        "evidence_refs": ["evidence://artifact-1/expected_value"],
        "summary": None,
        "summary_redacted": True,
        "redaction_reason": "because",
    })
    _assert_invalid("CandidateEvidenceItem", {
        "component_id": "expected_value",
        "evidence_refs": ["artifact://evidence/expected-value"],
        "summary": "raw private component explanation",
        "summary_redacted": True,
        "redaction_reason": "list_response",
    })
    _assert_invalid("CandidateEvidenceItem", {
        "component_id": "expected_value",
        "evidence_refs": ["artifact://evidence/expected-value"],
        "summary": None,
        "summary_redacted": False,
    })
    _assert_invalid("CandidateEvidenceValue", {
        "kind": "score_evidence_refs",
        "items": [],
        "total_refs": 0,
    })


def test_member_page_info_locks_stable_order() -> None:
    _assert_valid("CandidateMemberPageInfo", {
        "next_page_token": "cpm-offset-1",
        "page_size": 1,
        "has_more": True,
        "total": 2,
        "order_by": "created_at,artifact_id",
    })
    _assert_invalid("CandidateMemberPageInfo", {
        "next_page_token": None,
        "page_size": 1,
        "has_more": False,
        "total": 1,
        "order_by": "score",
    })
