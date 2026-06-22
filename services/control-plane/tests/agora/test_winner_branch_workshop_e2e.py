"""Winner-branch workshop E2E acceptance for AG-E2E-SW-001.

This file covers the task-specific acceptance path:

  complex workshop description -> governed evidence -> StrategySpec draft
  -> StrategyCompleteness map

It intentionally uses existing StrategySpec conversion and completeness
helpers. It does not add Agora routes, capabilities, execution authority,
RuntimeBinding writes, capital binding, or broker order fields.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft7Validator

from services.knowledge.evidence.models import EvidenceBundle, EvidenceItem
from services.research.strategy_spec.completeness import (
    assess_blocking_items,
    assess_readiness,
    compute_overall_grade,
    select_next_best_question,
)
from services.research.strategy_spec.conversion import StrategySpecConversionService
from services.research.strategy_spec.models import validate_strategy_spec_payload
from services.source_ingestion.connectors import SourceRecord


ROOT = Path(__file__).resolve().parents[4]
AGORA_SCHEMA_DIR = ROOT / "services/control-plane/specs/agora"
STRATEGY_SPEC_SCHEMA = ROOT / "services/control-plane/specs/strategy_spec.schema.json"

NOW = "2026-06-22T11:00:00Z"
WORKSHOP_ID = "ws_winner_branch_workshop_001"
USER_A = "user_a_ref"
PRIVATE_CONTENT_REF = "private://agora/ws_winner_branch_workshop_001/events/evt_0001"

FORBIDDEN_EXECUTION_KEYS = frozenset(
    {
        "broker_order_id",
        "order_route",
        "order_id",
        "filled_qty",
        "runtime_binding_id",
        "runtime_binding_ref",
        "capital_binding_id",
        "capital_binding_ref",
    }
)

WINNER_BRANCH_TOPICS = (
    "related-party holdings",
    "candidate branch mapping",
    "historical branch entry/exit profitability",
    "branch migration to alternate sell branches",
    "unmatched large selling branches",
    "holding-change correlation",
    "event lead analysis over 3-6 months",
    "winner-branch scoring",
    "probability/EV",
    "position/add/leverage discussion",
    "similar Alpha research",
)


def test_winner_branch_description_materializes_schema_valid_strategy_spec_draft() -> None:
    result = _convert_winner_branch_workshop()
    spec = result.strategy_spec.to_dict()

    validate_strategy_spec_payload(spec)
    _validate_against_schema(spec, STRATEGY_SPEC_SCHEMA)

    assert result.strategy_spec_seed.status.value == "draft"
    assert spec["lifecycle_state"] == "draft"
    assert spec["provenance"]["source_kind"] == "note"
    assert spec["provenance"]["source_refs"] == ["src-winner-branch-workshop-001"]
    assert spec["metadata"]["source_seed_id"] == result.strategy_spec_seed.seed_id
    assert spec["metadata"]["research_only"] is True
    assert spec["metadata"]["registry_write_performed"] is False
    assert spec["metadata"]["execution_route"] == "none"
    assert spec["execution_profile"]["execution_mode_hint"] == "research"

    assert spec["market_scope"]["symbols"] == ["TW_EQUITY_RESEARCH_UNIVERSE"]
    assert spec["market_scope"]["frequency"] == "1d"
    assert "winner_branch_score" in spec["metadata"]["feature_hints"]
    assert "probability_ev_calibration" in spec["metadata"]["metrics"]
    assert _dependency_ref(spec, "src-winner-branch-workshop-001", "source_record")
    assert _dependency_ref(spec, "dataset:point-in-time-related-party-holdings", "dataset")
    assert _evidence_ref(spec, "evidence_bundle", "evbundle-winner-branch-workshop-001")

    _assert_no_forbidden_execution_keys(result.to_dict())


def test_winner_branch_completeness_map_is_schema_valid_and_research_ready() -> None:
    result = _convert_winner_branch_workshop()
    spec = result.strategy_spec.to_dict()
    state_map = _winner_branch_state_map()
    blocking_items = assess_blocking_items(state_map)
    research_ready, validation_ready, trading_room_ready = assess_readiness(blocking_items)
    next_question, suppressed, provisional = select_next_best_question(
        state_map,
        user_relevance_overrides={
            "data_identity_mapping_role": 0.90,
            "position_sizing": 0.95,
        },
    )

    completeness = {
        "spec_version": "1.0",
        "completeness_id": "complete-winner-branch-workshop-001",
        "strategy_ref": spec["strategy_id"],
        "workshop_id": WORKSHOP_ID,
        "assessed_by_persona_id": "agora-servant-user-a",
        "overall_grade": compute_overall_grade(state_map),
        "dimensions": [
            {"dimension": "hypothesis", "grade": "complete"},
            {"dimension": "data_dependencies", "grade": "complete"},
            {"dimension": "market_scope", "grade": "complete"},
            {"dimension": "evaluation_plan", "grade": "complete"},
            {
                "dimension": "risk_constraints",
                "grade": "partial",
                "gaps": ["Position sizing and leverage cap require trader confirmation."],
                "required_actions": ["Ask the next high-value risk or position question before promotion."],
            },
            {"dimension": "execution_profile", "grade": "complete"},
            {"dimension": "governance", "grade": "complete"},
        ],
        "blockers": [item.reason for item in blocking_items],
        "research_ready": research_ready,
        "assessed_at": NOW,
        "metadata": {
            "question_policy_version": "QuestionScoringPolicy.v1",
            "validation_ready": validation_ready,
            "trading_room_ready": trading_room_ready,
            "next_question": next_question.__dict__ if next_question else None,
            "suppressed_questions": [item.__dict__ for item in suppressed],
            "provisional_assumptions": [item.__dict__ for item in provisional],
        },
    }

    _validate_against_schema(completeness, AGORA_SCHEMA_DIR / "strategy_completeness.schema.json")

    dimensions = {item["dimension"]: item for item in completeness["dimensions"]}
    assert set(dimensions) == {
        "hypothesis",
        "data_dependencies",
        "market_scope",
        "evaluation_plan",
        "risk_constraints",
        "execution_profile",
        "governance",
    }
    assert dimensions["risk_constraints"]["grade"] == "partial"
    assert completeness["overall_grade"] == "mostly_complete"
    assert completeness["blockers"] == []
    assert completeness["research_ready"] is True
    assert completeness["metadata"]["validation_ready"] is True
    assert completeness["metadata"]["trading_room_ready"] is True
    assert next_question is not None
    assert next_question.target_fields[0] in {
        "data_identity_mapping_role",
        "position_sizing",
        "risk_constraints",
    }


def test_workshop_storage_uses_private_ref_and_registry_link_not_raw_strategy_truth() -> None:
    result = _convert_winner_branch_workshop()
    spec = result.strategy_spec.to_dict()
    registry_payload = result.registry_payload

    workshop_event_row = {
        "event_id": "evt_0001",
        "workshop_id": WORKSHOP_ID,
        "sequence_no": 1,
        "private_content_ref": PRIVATE_CONTENT_REF,
        "redacted_summary": "Winner-branch strategy description captured for private servant reconstruction.",
        "created_by": USER_A,
    }
    workshop_version_link = {
        "workshop_version_id": "wv-winner-branch-001",
        "workshop_id": WORKSHOP_ID,
        "strategy_id": spec["strategy_id"],
        "strategy_spec_registry_id": registry_payload["strategy_id"],
        "registry_storage_ref": registry_payload["storage_ref"],
    }
    workshop_storage = {
        "workshop_id": WORKSHOP_ID,
        "owner_ref": USER_A,
        "event_refs": [workshop_event_row["event_id"]],
        "active_version_ref": workshop_version_link,
    }

    event_json = json.dumps(workshop_event_row, sort_keys=True)
    assert PRIVATE_CONTENT_REF in event_json
    for topic in WINNER_BRANCH_TOPICS:
        assert topic not in event_json

    storage_json = json.dumps(workshop_storage, sort_keys=True)
    assert spec["strategy_id"] in storage_json
    assert registry_payload["strategy_spec"] != workshop_storage
    for forbidden_strategy_field in (
        "hypothesis",
        "objective",
        "market_scope",
        "data_dependencies",
        "evaluation_plan",
    ):
        assert forbidden_strategy_field not in workshop_storage
        assert forbidden_strategy_field not in workshop_storage["active_version_ref"]

    _assert_no_forbidden_execution_keys(workshop_storage)


def _convert_winner_branch_workshop():
    return StrategySpecConversionService(created_by="Codex").convert_source_material(
        _winner_branch_bundle(),
        source_records=[_winner_branch_source()],
        evidence_items=[_winner_branch_evidence_item()],
        strategy_id="strat-winner-branch-workshop-draft",
        title="Winner Branch Related-Party Selling Strategy",
        version="1.0.0",
        created_at=NOW,
        metadata={
            "workshop_id": WORKSHOP_ID,
            "symbols": ["TW_EQUITY_RESEARCH_UNIVERSE"],
            "frequency": "1d",
            "metrics": [
                "oos_hit_rate",
                "expected_value_net_cost",
                "probability_ev_calibration",
                "max_drawdown",
                "capacity",
            ],
        },
    )


def _winner_branch_source() -> SourceRecord:
    return SourceRecord(
        source_id="src-winner-branch-workshop-001",
        connector_id="conn-agora-workshop",
        source_type="internal_note",
        title="Winner branch workshop reconstruction",
        content_ref=PRIVATE_CONTENT_REF,
        trace_id="trace-winner-branch-workshop-source",
        metadata={
            "license_scope": "private",
            "access_scope": ["research", "owner_private"],
            "workshop_id": WORKSHOP_ID,
            "private_content_ref": PRIVATE_CONTENT_REF,
            "redacted_summary": "Private winner-branch strategy description reconstructed into governed seed hints.",
            "allowed_use": ["research", "strategy_seed"],
            "strategy_seed": {
                "hypothesis": (
                    "Related-party holdings, broker branch migration, and winner-branch scoring "
                    "can forecast 3-6 month Taiwan equity entry and exit opportunities."
                ),
                "asset_class": ["equity"],
                "market_scope": ["Taiwan", "TWSE", "TPEx"],
                "symbols": ["TW_EQUITY_RESEARCH_UNIVERSE"],
                "frequency": "1d",
                "holding_period": "3-6 months",
                "required_data": [
                    "point-in-time related-party holdings",
                    "candidate branch mapping",
                    "historical branch entry and exit profitability",
                    "branch migration to alternate sell branches",
                    "unmatched large selling branches",
                    "holding-change correlation",
                    "event lead/placebo labels",
                    "cost liquidity capacity data",
                ],
                "backend_hint": "statsmodels",
                "feature_hints": [
                    "candidate_branch_mapping",
                    "branch_entry_exit_profitability",
                    "branch_migration",
                    "unmatched_large_selling_branches",
                    "holding_change_correlation",
                    "event_lead_analysis",
                    "winner_branch_score",
                ],
                "label_hints": ["3_6m_forward_return", "winner_branch_entry"],
                "metrics": [
                    "oos_hit_rate",
                    "expected_value_net_cost",
                    "probability_ev_calibration",
                    "max_drawdown",
                    "capacity",
                ],
                "risk_notes": [
                    "position/add/leverage requires trader confirmation",
                    "cost/liquidity/capacity must be validated",
                    "no broker order route",
                ],
            },
            "governance": {
                "direct_execution_allowed": False,
                "canonical_sink": "EvidenceBundle/StrategySpecSeed",
            },
        },
    )


def _winner_branch_bundle() -> EvidenceBundle:
    return EvidenceBundle(
        evidence_bundle_id="evbundle-winner-branch-workshop-001",
        source_ids=["src-winner-branch-workshop-001"],
        evidence_item_ids=["evi-winner-branch-workshop-001"],
        summary="Evidence bundle for the private winner-branch workshop strategy description.",
        citation_refs=["agora-workshop:ws_winner_branch_workshop_001#evt_0001"],
        confidence=0.86,
        license_scope="private",
        access_scope=["research", "owner_private"],
        created_by="Codex",
        created_at=NOW,
        trace_refs=["trace-winner-branch-workshop-bundle"],
    )


def _winner_branch_evidence_item() -> EvidenceItem:
    return EvidenceItem(
        evidence_item_id="evi-winner-branch-workshop-001",
        source_id="src-winner-branch-workshop-001",
        item_type="redacted_workshop_summary",
        content_ref="agora-workshop://ws_winner_branch_workshop_001/events/evt_0001#redacted",
        citation_label="agora-workshop:ws_winner_branch_workshop_001#evt_0001",
        body="; ".join(WINNER_BRANCH_TOPICS),
        confidence=0.82,
        access_scope=["research", "owner_private"],
        trace_refs=["trace-winner-branch-workshop-evidence"],
    )


def _winner_branch_state_map() -> dict[str, str]:
    return {
        "hypothesis": "confirmed",
        "data_dependencies": "confirmed",
        "market_scope": "confirmed",
        "evaluation_plan": "confirmed",
        "execution_profile": "confirmed",
        "governance": "confirmed",
        "data_pit": "confirmed",
        "exit_invalidation": "confirmed",
        "liquidity": "confirmed",
        "data_identity_mapping_role": "confirmed",
        "position_sizing": "inferred_needs_confirmation",
        "risk_constraints": "weak",
    }


def _dependency_ref(spec: Mapping[str, Any], ref: str, kind: str) -> bool:
    return {"ref": ref, "kind": kind} in spec["data_dependencies"]


def _evidence_ref(spec: Mapping[str, Any], ref_type: str, ref_id: str) -> bool:
    return any(
        item.get("ref_type") == ref_type and item.get("ref_id") == ref_id
        for item in spec.get("evidence_refs", [])
    )


def _validate_against_schema(payload: Mapping[str, Any], schema_path: Path) -> None:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft7Validator(schema).validate(dict(payload))


def _assert_no_forbidden_execution_keys(value: Any) -> None:
    if isinstance(value, Mapping):
        forbidden = FORBIDDEN_EXECUTION_KEYS.intersection(value)
        assert forbidden == set()
        for item in value.values():
            _assert_no_forbidden_execution_keys(item)
    elif isinstance(value, list):
        for item in value:
            _assert_no_forbidden_execution_keys(item)
