"""BFF-STRATEGY-ALPHA-ROUNDTRIP-20261110: alpha persisted top-level reads back."""
from __future__ import annotations

from services.control_plane.bff.strategies.routes.common import StrategyRouteContext


def _ctx() -> StrategyRouteContext:
    return StrategyRouteContext(
        read_surface=None, get_read_store=lambda: None,
        extract_identity=lambda *a: {}, require_read_role=lambda *a: None,
        require_operator_role=lambda *a: None, bff_error=lambda *a, **k: Exception(),
        utc_now=lambda: "2026-11-10T00:00:00Z", page_slice=lambda items, *a: (items, None),
        read_surface_meta=lambda *a, **k: {}, reject_body_idempotency_key=lambda *a: None,
        resolve_final_idempotency_key=lambda *a: "", stable_json_hash=lambda *a: "",
        request_dry_run_requested=lambda: False, dry_run_success_response=lambda *a, **k: {},
        normalize_lifecycle_state=lambda s: str(s or "draft"),
        normalize_risk_level=lambda r: str(r or "medium"),
        strategy_persona_idempotency_check=lambda *a: None,
        strategy_persona_idempotency={}, strategy_seed_replication_idempotency={},
        strategy_seed_review_idempotency={}, list_governance_audit_events=None,
        ooda_packet_list_payload=None, require_ooda_packet_routes_enabled=None,
        deprecated_bff_path_response=None, bff_me_tenant_payload=None,
        list_persona_records=None, list_strategy_summaries=None,
    )


def _create_record() -> dict:
    # Shape written by StrategyWriteOwner create path (metadata=dict(record)).
    return {"id": "s-1", "strategy_id": "s-1", "name": "n", "state": "draft",
            "alpha": "stage-isolated-draft-metadata-acceptance"}


def test_alpha_round_trips_from_top_level_record_in_detail_and_list() -> None:
    rec = _create_record()
    ctx = _ctx()
    assert ctx.project_strategy_dto(dict(rec), detail=dict(rec))["alpha"] == rec["alpha"]
    assert ctx.project_strategy_dto(dict(rec))["alpha"] == rec["alpha"]


def test_alpha_round_trips_from_metadata() -> None:
    rec = {"strategy_id": "s-2", "metadata": {"alpha": "slug-a"}}
    ctx = _ctx()
    assert ctx.project_strategy_dto(dict(rec), detail=dict(rec))["alpha"] == "slug-a"
    assert ctx.project_strategy_dto(dict(rec))["alpha"] == "slug-a"


def test_market_scope_alpha_still_wins_and_missing_alpha_is_empty() -> None:
    ctx = _ctx()
    rec = {"strategy_id": "s-3", "alpha": "top", "market_scope": {"alpha": "scope"}}
    assert ctx.project_strategy_dto(dict(rec))["alpha"] == "scope"
    assert ctx.project_strategy_dto({"strategy_id": "s-4"})["alpha"] == ""
