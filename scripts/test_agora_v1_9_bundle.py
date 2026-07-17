from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml
from jsonschema import Draft7Validator, FormatChecker, RefResolver


ROOT = Path(__file__).resolve().parents[1]
AGORA_SPECS = ROOT / "services/control-plane/specs/agora"
SCHEMA_PATH = AGORA_SPECS / "v10/persona_interaction_daily.schema.json"
MANIFEST_PATH = AGORA_SPECS / "v10/capability_manifest_v1_9.json"
OPENAPI_PATH = ROOT / "services/control-plane/openapi/agora_v1_9.openapi.yaml"
BUNDLE_PATH = AGORA_SPECS / "bundle_index.v1_9.json"


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


def _authority() -> dict:
    return {
        "execution_authority": "none",
        "order_submitted": False,
        "broker_called": False,
        "capital_changed": False,
        "runtime_bound": False,
        "lifecycle_promoted": False,
        "policy_mutated": False,
        "persona_memory_mutated": False,
    }


def test_v1_9_bundle_extends_v1_8_without_rewriting_parent() -> None:
    bundle = json.loads(BUNDLE_PATH.read_text(encoding="utf-8"))

    assert bundle["bundle_version"] == "1.9"
    assert bundle["extends"] == {
        "bundle_path": "services/control-plane/specs/agora/bundle_index.v1_8.json",
        "bundle_version": "1.8",
        "bundle_index_sha256": _sha256(AGORA_SPECS / "bundle_index.v1_8.json"),
    }


def test_v1_9_bundle_hashes_lock_exact_contract_bytes() -> None:
    bundle = json.loads(BUNDLE_PATH.read_text(encoding="utf-8"))

    for relative_path, expected_hash in bundle["files"].items():
        path = ROOT / "services/control-plane" / relative_path
        assert path.exists(), relative_path
        assert _sha256(path) == expected_hash
    assert bundle["openapi"] == {
        "path": "services/control-plane/openapi/agora_v1_9.openapi.yaml",
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
    assert spec["info"]["x-implementation-status"] == "partially_implemented"
    assert {item["implementation_status"] for item in manifest["capabilities"]} == {
        "implemented", "contract_only"
    }
    implemented = {
        item["id"] for item in manifest["capabilities"]
        if item["implementation_status"] == "implemented"
    }
    assert implemented == {
        "persona_interaction_daily_contract", "persona_provider_content_contract",
    }


def test_lifecycle_and_partial_failure_states_are_exact() -> None:
    status = _schema()["definitions"]["InteractionResource"]["properties"]["status"]
    assert status["enum"] == ["queued", "running", "completed", "degraded", "failed"]


def test_production_provenance_rejects_canned_magic_topic_and_simulation() -> None:
    valid = {
        "content_origin": "selected_persona_provider_response",
        "provider_kind": "openclaw",
        "provider_invocation_id": "invoke-1",
        "request_correlated": True,
        "response_correlated": True,
        "canned_template": False,
        "magic_topic_trigger": False,
        "simulation": False,
    }
    _assert_valid("ProductionContentProvenance", valid)

    for forbidden_flag in ("canned_template", "magic_topic_trigger", "simulation"):
        assert list(_validator("ProductionContentProvenance").iter_errors({
            **valid,
            forbidden_flag: True,
        }))
    assert list(_validator("ProductionContentProvenance").iter_errors({
        **valid,
        "content_origin": "deterministic_topic_branch",
    }))
    assert list(_validator("ProductionContentProvenance").iter_errors({
        **valid,
        "provider_invocation_id": "",
    }))


def test_every_typed_opinion_requires_provider_and_persona_provenance() -> None:
    participant = {
        "persona_id": "persona-risk",
        "persona_version": "v7",
        "session_persona_id": "session-persona-risk",
        "provider_agent_id": "agent-risk",
        "workspace_id": "workspace-tenant-a",
        "environment_ceiling": "paper",
        "capability_snapshot": ["persona_opinion"],
        "captured_at": "2026-07-17T12:00:00Z",
    }
    opinion = {
        "opinion_id": "opinion-1",
        "interaction_id": "interaction-1",
        "participant": participant,
        "provider_invocation_id": "invoke-1",
        "conclusion": "oppose",
        "rationale": "The risk budget is inconsistent with the cited regime.",
        "confidence": 0.73,
        "uncertainty": ["Forward volatility is not fully observed."],
        "risks": ["Drawdown may exceed the review limit."],
        "invalidation_conditions": ["Observed volatility falls below the threshold."],
        "evidence_refs": [],
        "recommended_measures": [],
        "provenance": {
            "content_origin": "selected_persona_provider_response",
            "provider_kind": "openclaw",
            "provider_invocation_id": "invoke-1",
            "request_correlated": True,
            "response_correlated": True,
            "canned_template": False,
            "magic_topic_trigger": False,
            "simulation": False,
        },
        "created_at": "2026-07-17T12:00:02Z",
        "authority": _authority(),
    }
    _assert_valid("TypedPersonaOpinion", opinion)

    without_provenance = {key: value for key, value in opinion.items() if key != "provenance"}
    assert list(_validator("TypedPersonaOpinion").iter_errors(without_provenance))
    without_invocation = {key: value for key, value in opinion.items() if key != "provider_invocation_id"}
    assert list(_validator("TypedPersonaOpinion").iter_errors(without_invocation))


def test_recommended_measure_is_structured_versioned_and_non_executing() -> None:
    measure = {
        "measure_id": "measure-risk-budget",
        "measure_type": "risk_limit_recommendation",
        "target": {
            "kind": "strategy",
            "id": "strategy-1",
            "version": "v12",
            "path": "/risk/max_drawdown",
        },
        "current_value": 0.12,
        "proposed_value": 0.08,
        "rationale": "Reduce the risk budget while the volatility regime remains elevated.",
        "expected_benefit": "Bound drawdown during the reviewed regime.",
        "adverse_scenarios": ["The lower risk ceiling may miss a recovery rally."],
        "confidence": 0.71,
        "evidence_refs": [{
            "ref_type": "risk_snapshot",
            "ref_id": "risk-1",
            "version": "v3",
            "observed_at": "2026-07-17T11:59:00Z",
            "data_cutoff": "2026-07-17T11:58:00Z",
            "freshness": "fresh",
            "summary": "Volatility and drawdown snapshot.",
        }],
        "environment_ceiling": "paper",
        "validation_plan": {
            "validator": "risk-validation-service",
            "required_checks": ["paper_replay", "risk_review"],
        },
        "rollback_trigger": "Validation fails or evidence expires.",
        "rollback_action": "Retain strategy v12 unchanged.",
        "authority": _authority(),
    }
    _assert_valid("RecommendedMeasure", measure)

    missing_version = {
        **measure,
        "target": {key: value for key, value in measure["target"].items() if key != "version"},
    }
    assert list(_validator("RecommendedMeasure").iter_errors(missing_version))
    assert list(_validator("RecommendedMeasure").iter_errors({
        **measure,
        "authority": {**_authority(), "capital_changed": True},
    }))


def test_accept_for_review_is_not_formal_approval() -> None:
    decision = {
        "decision_id": "decision-1",
        "proposal_id": "proposal-1",
        "interaction_id": "interaction-1",
        "measure_id": "measure-1",
        "action": "accepted_for_review",
        "actor_id": "operator-1",
        "reason": "Ready for an independent risk review.",
        "revision": 2,
        "proposal_digest": "a" * 64,
        "review_request_id": "review-1",
        "decided_at": "2026-07-17T12:10:00Z",
        "formal_approval": False,
        "execution_authority": "none",
        "audit_ref": "audit-1",
    }
    _assert_valid("CandidateDecisionRecord", decision)
    assert list(_validator("CandidateDecisionRecord").iter_errors({
        **decision,
        "formal_approval": True,
    }))
    assert list(_validator("CandidateDecisionRecord").iter_errors({
        **decision,
        "execution_authority": "execute",
    }))

    formal_approval = {
        "approval_decision_id": "approval-1",
        "authority": "canonical_approval_decision_store",
        "tenant_id": "tenant-a",
        "proposal_id": "proposal-1",
        "revision": 2,
        "proposal_digest": "a" * 64,
        "validation_receipt_id": "validation-1",
        "validation_receipt_sha256": "b" * 64,
        "proposer_id": "operator-1",
        "reviewer_id": "risk-reviewer-2",
        "outcome": "approved",
        "self_approval": False,
        "decided_at": "2026-07-17T12:20:00Z",
        "expires_at": "2026-07-18T12:20:00Z",
        "receipt_sha256": "c" * 64,
        "execution_authority": "none",
    }
    _assert_valid("FormalApprovalReceipt", formal_approval)
    assert list(_validator("FormalApprovalReceipt").iter_errors({
        **formal_approval,
        "self_approval": True,
    }))
    missing_validation_digest = {
        key: value
        for key, value in formal_approval.items()
        if key != "validation_receipt_sha256"
    }
    assert list(_validator("FormalApprovalReceipt").iter_errors(missing_validation_digest))


def test_browser_cannot_supply_authoritative_validation_result() -> None:
    valid = {
        "proposal_id": "proposal-1",
        "revision": 3,
        "proposal_digest": "d" * 64,
        "validation_plan_ref": "validation-plan-1",
    }
    _assert_valid("AuthoritativeValidationRequest", valid)
    assert list(_validator("AuthoritativeValidationRequest").iter_errors({
        **valid,
        "validation_result": {"passed": True},
    }))


def test_authority_boundary_rejects_every_forbidden_side_effect() -> None:
    _assert_valid("AuthorityBoundary", _authority())
    for forbidden in (
        "order_submitted",
        "broker_called",
        "capital_changed",
        "runtime_bound",
        "lifecycle_promoted",
        "policy_mutated",
        "persona_memory_mutated",
    ):
        assert list(_validator("AuthorityBoundary").iter_errors({
            **_authority(),
            forbidden: True,
        })), forbidden


def test_manifest_storage_ownership_and_content_policy_are_fail_closed() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    assert manifest["storage_ownership"] == {
        "interaction_request": "agora_interaction_postgres",
        "provider_invocation": "agora_interaction_postgres",
        "persona_opinion": "agora_interaction_postgres",
        "synthesis": "agora_interaction_postgres",
        "interaction_outbox": "agora_interaction_postgres",
        "workshop_timeline": "workshop_projection_not_authority",
        "candidate_revision": "agora_governance_postgres",
        "candidate_decision": "agora_governance_postgres",
        "validation_receipt": "canonical_validation_service",
        "formal_approval": "canonical_approval_decision_store",
        "persona_identity_version": "persona_registry",
        "provider_response": "openclaw_provider_provenance_not_business_authority",
        "frontend": "cache_only_no_authority",
    }
    policy = manifest["production_content_policy"]
    assert policy["independent_invocation_per_selected_persona"] is True
    assert policy["request_response_correlation_required"] is True
    for key in (
        "canned_template_allowed",
        "keyword_branch_allowed",
        "magic_topic_trigger_allowed",
        "simulation_allowed",
        "fixture_allowed",
        "forged_opinion_on_provider_failure_allowed",
    ):
        assert policy[key] is False
