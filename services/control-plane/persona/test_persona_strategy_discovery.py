from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft7Validator

from persona_registry import Persona
from persona_strategy_discovery import (
    PersonaStrategyDiscoveryService,
    extract_persona_strategy_profile,
)
from services.source_ingestion.strategy_seed_builder import (
    StrategySpecSeed,
    StrategySpecSeedStatus,
)


def _persona(**overrides) -> Persona:
    data = {
        "persona_id": "persona-momentum",
        "name": "Momentum Research Persona",
        "mandate": "Research TWSE equity momentum strategies",
        "strategy_family": "momentum",
        "lifecycle_state": "research_only",
        "status": "active",
        "created_at": "2026-06-10T00:00:00Z",
        "metadata": {
            "market_scope": ["TWSE"],
            "asset_classes": ["equity"],
            "holding_periods": ["swing"],
            "allowed_research_backends": ["qlib"],
            "allowed_source_scopes": ["open"],
            "data_availability_scope": ["ohlcv", "return labels"],
            "risk_level": "medium",
        },
    }
    data.update(overrides)
    return Persona(**data)


def _seed(**overrides) -> StrategySpecSeed:
    data = {
        "seed_id": "seed-twse-momentum",
        "source_id": "src-openalex-momentum",
        "evidence_bundle_id": "bundle-momentum-001",
        "hypothesis": "TWSE equity momentum persists over swing holding periods.",
        "asset_class": ["equity"],
        "market_scope": ["TWSE"],
        "holding_period": "swing",
        "required_data": ["ohlcv", "return labels"],
        "backend_hint": "qlib",
        "feature_hints": ["momentum"],
        "label_hints": ["forward returns"],
        "risk_notes": ["medium"],
        "confidence": 0.86,
        "status": StrategySpecSeedStatus.DRAFT,
        "source_ids": ["src-openalex-momentum"],
        "evidence_item_ids": ["evitem-momentum-001"],
        "citation_refs": ["doi:10/example"],
        "trace_refs": ["trace-momentum-001"],
        "created_at": "2026-06-10T00:00:00Z",
        "metadata": {
            "strategy_family": "momentum",
            "source_license_scope": "open",
            "access_scope": ["research", "strategy_seed"],
            "entitlement_tags": [],
            "source_status": "active",
            "execution_route": "none",
            "data_backfill_available": False,
        },
    }
    data.update(overrides)
    return StrategySpecSeed(**data)


def _schema() -> dict:
    root = Path(__file__).resolve().parents[3]
    return json.loads((root / "docs/contracts/persona_strategy_match.schema.json").read_text())


def test_profile_extraction_uses_persona_and_route_policy_fields() -> None:
    profile = extract_persona_strategy_profile(
        _persona(),
        route_policy={
            "policy_id": "route-policy-001",
            "allowed_source_scopes": ["open"],
            "allowed_research_backends": ["qlib"],
            "data_availability_scope": ["twse ohlcv"],
        },
    )

    assert profile.persona_id == "persona-momentum"
    assert "momentum" in profile.strategy_families
    assert "twse" in profile.preferred_markets
    assert "equity" in profile.asset_classes
    assert "qlib" in profile.backend_preferences
    assert profile.blockers == ()


def test_deterministic_seed_match_is_explainable_and_schema_valid() -> None:
    profile = extract_persona_strategy_profile(_persona())
    matches = PersonaStrategyDiscoveryService().match_candidates(
        profile,
        strategy_seeds=[_seed()],
        created_at="2026-06-10T00:00:00Z",
    )

    assert len(matches) == 1
    match = matches[0]
    payload = match.to_dict()
    assert payload["matched_object_type"] == "strategy_spec_seed"
    assert payload["score"] >= 80
    assert payload["rank"] == 1
    assert payload["recommended_action"]["type"] == "promote_seed_candidate"
    assert payload["metadata"]["blockers"] == []
    assert "research_discovery" in payload["allowed_use"]
    assert "live_runtime" not in payload["allowed_use"]
    assert any(field["field"] == "strategy_family" for field in payload["matched_fields"])
    assert payload["score_breakdown"]["total"] == match.score_breakdown.total

    Draft7Validator(_schema()).validate(payload)


def test_hard_blockers_are_reported_without_deployment_authority() -> None:
    profile = extract_persona_strategy_profile(
        _persona(
            lifecycle_state="retired",
            metadata={
                "market_scope": ["TWSE"],
                "asset_classes": ["equity"],
                "allowed_research_backends": ["qlib"],
                "allowed_source_scopes": ["open"],
                "data_availability_scope": ["ohlcv"],
                "risk_level": "low",
            },
        )
    )
    blocked_seed = _seed(
        seed_id="seed-blocked",
        required_data=["news events"],
        status=StrategySpecSeedStatus.REJECTED,
        metadata={
            "strategy_family": "momentum",
            "source_license_scope": "restricted",
            "access_scope": ["display_only"],
            "source_status": "disabled",
            "execution_route": "live",
            "data_backfill_available": False,
        },
    )

    match = PersonaStrategyDiscoveryService().match_candidates(
        profile,
        strategy_seeds=[blocked_seed],
        created_at="2026-06-10T00:00:00Z",
    )[0]
    payload = match.to_dict()

    assert payload["status"] == "ignored"
    assert payload["score"] <= 49
    assert "persona_lifecycle_not_research_capable" in payload["metadata"]["blockers"]
    assert "seed_status_rejected" in payload["metadata"]["blockers"]
    assert "license_does_not_allow_research_use" in payload["metadata"]["blockers"]
    assert "source_status_disabled" in payload["metadata"]["blockers"]
    assert "required_data_unavailable" in payload["metadata"]["blockers"]
    assert "strategy_requires_execution_route_during_discovery" in payload["metadata"]["blockers"]
    assert payload["recommended_action"]["type"] == "ignore"
    assert payload["metadata"]["execution_route"] == "none"


def test_strategy_spec_candidate_can_recommend_rapid_eval() -> None:
    profile = extract_persona_strategy_profile(_persona())
    spec = {
        "strategy_id": "strat-twse-momentum",
        "title": "TWSE Momentum Rotation",
        "hypothesis": "Momentum rotation remains effective on TWSE equities.",
        "lifecycle_state": "approved",
        "market_scope": {
            "symbols": ["2330", "2317"],
            "venues": ["TWSE"],
            "asset_classes": ["equity"],
            "frequency": "daily",
        },
        "data_dependencies": [
            {"ref": "ohlcv", "kind": "dataset"},
            {"ref": "return labels", "kind": "feature_set"},
        ],
        "execution_profile": {
            "signal_schema_version": "research-signal-v1",
            "quantity_type": "PERCENT_PORTFOLIO",
            "execution_mode_hint": "paper",
            "rebalance_cadence": "swing",
        },
        "governance": {"approval_required": True, "risk_profile": "medium"},
        "provenance": {
            "source_kind": "workflow",
            "source_refs": ["note-momentum"],
            "created_at": "2026-06-09T00:00:00Z",
        },
        "citation_bundle": {
            "evidence_refs": [{"ref_id": "evref-momentum-001"}],
        },
    }

    match = PersonaStrategyDiscoveryService().match_candidates(
        profile,
        strategy_specs=[spec],
        created_at="2026-06-10T00:00:00Z",
    )[0]

    assert match.matched_object_type == "strategy_spec"
    assert match.recommended_action.type == "run_rapid_eval"
    assert match.blockers == ()
