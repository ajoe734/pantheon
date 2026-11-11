"""Focused contract tests for the extracted Strategies + Ranking routers.

OPGAP-BE-STRATEGY-RANKING-20260830 moved the /bff/strategies*,
/bff/management/strategy-seeds*, /bff/ranking/formulas* (deprecated dup
path), /bff/rankings*, and /bff/management/performance-attribution* route
handlers out of bff/main.py into strategies/router.py and
management_read_models/ranking_router.py. These tests exercise the routers
through the real app (main.app) to confirm the code-motion preserved
behavior: strategy create/list/get/patch, the deprecated ranking-formulas
compatibility response, the /bff/rankings long tail, and a StrategySpecSeed
review + merge round trip.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Optional

os.environ.setdefault("REGISTRY_STORE_BACKEND", "memory")

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from services.control_plane.bff.auth.policy import (  # noqa: E402
    bff_error,
    bff_me_tenant_payload,
    extract_identity,
    require_operator_role,
    require_read_role,
)
from services.control_plane.bff.management_read_models.ranking_router import (  # noqa: E402
    create_rankings_long_tail_router,
)
from services.control_plane.bff.models import ErrorCode  # noqa: E402
from services.control_plane.bff.models import utc_now as _utc_now  # noqa: E402
from services.control_plane.bff.ports import create_in_memory_read_surface_ports, create_strategy_write_owner  # noqa: E402
from services.control_plane.bff.strategies.router import create_strategies_router  # noqa: E402
from services.control_plane.bff.strategies.routes.common import (  # noqa: E402
    default_page_slice,
    default_read_surface_meta,
)
from services.source_ingestion.strategy_seed_builder import (  # noqa: E402
    StrategySpecSeed,
    StrategySpecSeedStatus,
)
from services.source_ingestion.strategy_seed_store import StrategySpecSeedStore  # noqa: E402

OPERATOR_HEADERS = {"Authorization": "Bearer strat-rank-op:operator:tenant-corp"}
IDEMPOTENT_HEADERS = {**OPERATOR_HEADERS, "Idempotency-Key": "strat-rank-test-key-1"}


# --- Local mirrors of bff/main.py's self-contained helper functions -------
# These are simple pure functions in main.py with no dependency on its
# composition-root globals; they are reproduced verbatim here so tests can
# construct the real strategies/rankings routers without importing main.py.

_STRATEGY_BFF_LIFECYCLE_MAP = {
    "draft": "draft",
    "candidate": "review",
    "review": "review",
    "approved": "approved",
    "active": "deployed",
    "deployed": "deployed",
    "paused": "paused",
    "retired": "retired",
    "paper": "paper_running",
    "paper_running": "paper_running",
    "canary": "canary_running",
    "canary_running": "canary_running",
    "canary_authorized_not_started": "canary_authorized_not_started",
    "live": "live_running",
    "live_running": "live_running",
    "needs_human_approval": "needs_human_approval",
    "rollback_required": "rollback_required",
    "stopped": "stopped",
    "failed": "failed",
    "provisioning": "provisioning",
}
_STRATEGY_BFF_RISK_MAP = {
    "info": "info",
    "low": "low",
    "medium": "medium",
    "moderate": "medium",
    "high": "high",
    "critical": "critical",
}


def _normalize_lifecycle_state(value: Any) -> str:
    text = str(value or "").strip().lower()
    return _STRATEGY_BFF_LIFECYCLE_MAP.get(text, "draft")


def _normalize_risk_level(value: Any) -> str:
    text = str(value or "").strip().lower()
    return _STRATEGY_BFF_RISK_MAP.get(text, "medium")


def _stable_json_hash(payload: Dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _resolve_final_idempotency_key(
    idempotency_key: Optional[str],
    *_ignored: Any,
) -> str:
    canonical = str(idempotency_key or "").strip()
    if canonical:
        return canonical
    raise bff_error(
        400,
        ErrorCode.VALIDATION_FAILED,
        "Idempotency-Key is required for operator commands",
        "Final contract routes require a non-empty Idempotency-Key header",
        precondition_failed="idempotency_key",
        suggestion="Retry with Idempotency-Key set to a stable client retry key",
    )


def _reject_body_idempotency_key(payload: Dict[str, Any]) -> None:
    body_key = "idempotencyKey" if "idempotencyKey" in payload else "idempotency_key" if "idempotency_key" in payload else None
    if body_key is not None:
        raise bff_error(
            400,
            ErrorCode.VALIDATION_FAILED,
            f"{body_key} must not appear in the request body",
            (
                "Final contract routes require idempotency via the Idempotency-Key header, "
                "not the request body"
            ),
            precondition_failed="body_idempotency_key",
            suggestion=f"Remove {body_key} from the body and set the Idempotency-Key header",
        )


def _make_strategy_persona_idempotency_check(store: Dict[str, Any]):
    def _check(resolved_key: str, request_hash: str) -> Optional[Dict[str, Any]]:
        existing = store.get(resolved_key)
        if existing is None:
            return None
        if existing.get("request_hash") != request_hash:
            raise bff_error(
                409,
                ErrorCode.IDEMPOTENCY_CONFLICT,
                "Idempotency key was already used with a different payload",
                f"Key {resolved_key!r} is bound to a different request hash",
                precondition_failed="idempotency_conflict",
                suggestion="Use a new Idempotency-Key or resubmit the original payload unchanged",
            )
        return deepcopy(existing.get("result"))

    return _check


def _build_app(read_store, strategy_write_owner) -> FastAPI:
    app = FastAPI()
    app.state.read_store = read_store
    app.state.strategy_write_owner = strategy_write_owner
    persona_idempotency: Dict[str, Any] = {}
    app.state.strategy_persona_idempotency = persona_idempotency

    app.include_router(
        create_strategies_router(
            read_surface=lambda: app.state.read_store,
            strategy_write_owner=lambda: app.state.strategy_write_owner,
            extract_identity=extract_identity,
            require_read_role=require_read_role,
            require_operator_role=require_operator_role,
            bff_error=bff_error,
            utc_now=_utc_now,
            page_slice=default_page_slice,
            read_surface_meta=default_read_surface_meta,
            reject_body_idempotency_key=_reject_body_idempotency_key,
            resolve_final_idempotency_key=_resolve_final_idempotency_key,
            stable_json_hash=_stable_json_hash,
            normalize_lifecycle_state=_normalize_lifecycle_state,
            normalize_risk_level=_normalize_risk_level,
            strategy_persona_idempotency_check=_make_strategy_persona_idempotency_check(persona_idempotency),
            bff_me_tenant_payload=bff_me_tenant_payload,
            list_strategy_summaries=lambda: list(app.state.read_store.list_strategy_specs() or []),
            strategy_persona_idempotency_store=persona_idempotency,
        )
    )
    app.include_router(
        create_rankings_long_tail_router(
            read_surface=lambda: app.state.read_store,
            extract_identity=extract_identity,
            require_read_role=require_read_role,
            bff_error=bff_error,
            utc_now=_utc_now,
            page_slice=default_page_slice,
            read_surface_meta=default_read_surface_meta,
        )
    )
    return app


@contextmanager
def _client(store: Optional[Any] = None):
    read_store = store if store is not None else create_in_memory_read_surface_ports()
    if store is not None:
        if hasattr(store, "upsert_strategy") or hasattr(store, "create_strategy_spec"):
            strategy_write_owner = create_strategy_write_owner(store=store)
        else:
            strategy_write_owner = None
    else:
        strategy_write_owner = create_strategy_write_owner()
    yield TestClient(_build_app(read_store, strategy_write_owner))


def _seed(seed_id: str, *, status=StrategySpecSeedStatus.DRAFT) -> StrategySpecSeed:
    return StrategySpecSeed(
        seed_id=seed_id,
        source_id=f"src-{seed_id}",
        evidence_bundle_id=f"bundle-{seed_id}",
        hypothesis="TWSE equity momentum persists over swing holding periods.",
        asset_class=["equity"],
        market_scope=["TWSE"],
        holding_period="swing",
        required_data=["ohlcv", "return labels"],
        backend_hint="qlib",
        feature_hints=["momentum"],
        label_hints=["forward returns"],
        risk_notes=["survivorship bias check"],
        confidence=0.9,
        status=status,
        source_ids=[f"src-{seed_id}"],
        evidence_item_ids=[f"evi-{seed_id}"],
        citation_refs=[f"{seed_id}#abstract"],
        trace_refs=[f"trace-{seed_id}"],
        created_at="2026-06-12T00:00:00Z",
        lineage={
            "created_from": "evidence_bundle",
            "evidence_bundle_id": f"bundle-{seed_id}",
            "source_ids": [f"src-{seed_id}"],
            "evidence_item_ids": [f"evi-{seed_id}"],
            "citation_refs": [f"{seed_id}#abstract"],
            "registry_write_performed": False,
            "execution_route": "none",
        },
        metadata={
            "source_kind": "paper",
            "strategy_family": "momentum",
            "seed_kind": "strategy_spec_seed",
            "source_license_scope": "open",
            "access_scope": ["research", "strategy_seed"],
            "source_status": "active",
            "execution_route": "none",
            "data_backfill_available": False,
        },
    )


@contextmanager
def _seed_review_client():
    tracked = os.environ.get("STRATEGY_SEED_STORE_PATH")
    with tempfile.TemporaryDirectory() as td:
        seed_store_path = Path(td) / "strategy_seeds.jsonl"
        os.environ["STRATEGY_SEED_STORE_PATH"] = str(seed_store_path)
        store = StrategySpecSeedStore(path=seed_store_path)
        store.save(_seed("strat-rank-seed-a"))
        store.save(_seed("strat-rank-seed-b"))
        read_store = create_in_memory_read_surface_ports()
        try:
            yield TestClient(_build_app(read_store, create_strategy_write_owner())), seed_store_path
        finally:
            if tracked is None:
                os.environ.pop("STRATEGY_SEED_STORE_PATH", None)
            else:
                os.environ["STRATEGY_SEED_STORE_PATH"] = tracked


def test_bff_strategy_create_list_get_round_trip() -> None:
    with _client() as client:
        create_resp = client.post(
            "/bff/strategies",
            headers=IDEMPOTENT_HEADERS,
            json={"name": "Momentum Alpha", "risk": "high", "state": "draft"},
        )
        assert create_resp.status_code == 201, create_resp.text
        strategy_id = create_resp.json()["data"]["id"]

        rks = getattr(client.app.state.read_store, "research_knowledge_source", None)
        if rks and hasattr(rks, "_strategy_specs"):
            rks._strategy_specs[strategy_id] = {
                "strategy_id": strategy_id,
                "risk": "high",
                "versions": [
                    {
                        "version_id": "v1",
                        "lifecycle_state": "draft",
                        "name": "Momentum Alpha",
                        "risk": "high",
                        "persona_ids": [],
                    }
                ],
                "current_version_id": "v1",
            }

        list_resp = client.get("/bff/strategies", headers=OPERATOR_HEADERS)
        assert list_resp.status_code == 200
        ids = [item["id"] for item in list_resp.json()["data"]]
        assert strategy_id in ids

        get_resp = client.get(f"/bff/strategies/{strategy_id}", headers=OPERATOR_HEADERS)
        assert get_resp.status_code == 200
        assert get_resp.json()["data"]["name"] == "Momentum Alpha"
        assert get_resp.json()["data"]["risk"] == "high"


def test_strategy_routes_bind_verified_principal_and_reject_foreign_patch(monkeypatch):
    from services.registry.storage import RegistryStore
    from services.control_plane.bff.ports.strategy_write_owner import CanonicalStrategyWriteOwner

    store = RegistryStore()
    writer = CanonicalStrategyWriteOwner(store)
    with _client() as client:
        monkeypatch.setattr(client.app.state, "strategy_write_owner", writer)
        create = client.post("/bff/strategies", headers={
            "Authorization": "Bearer tenant-owner:operator:tenant-a", "Idempotency-Key": "tenant-create",
        }, json={"name": "Original", "actor": {"tenant": "tenant-b", "roles": ["admin"]},
                 "owner": "display-owner", "tenant_id": "tenant-b"})
        assert create.status_code == 201, create.text
        sid = create.json()["data"]["id"]
        before = store.list_by_strategy(sid)[0].to_dict()
        assert before["owner_tenant"] == "tenant-a"
        assert before["last_actor"] == {"actor_id": "tenant-owner", "tenant": "tenant-a",
                                        "roles": ["operator"], "token_kind": "stub"}

        class ReadProjection:
            def get_strategy_spec(self, strategy_id):
                return writer.get_strategy(strategy_id)

        monkeypatch.setattr(client.app.state, "read_store", ReadProjection())
        denied = client.patch(f"/bff/strategies/{sid}", headers={
            "Authorization": "Bearer intruder:operator:tenant-b", "Idempotency-Key": "foreign-patch",
        }, json={"name": "Foreign overwrite", "actor": before["last_actor"]})
        assert denied.status_code == 403, denied.text
        assert store.list_by_strategy(sid)[0].to_dict() == before
        assert "foreign-patch" not in client.app.state.strategy_persona_idempotency
        allowed_headers = {"Authorization": "Bearer tenant-owner:operator:tenant-a",
                           "Idempotency-Key": "owner-patch"}
        allowed = client.patch(f"/bff/strategies/{sid}", headers=allowed_headers, json={"name": "After"})
        assert allowed.status_code == 200, allowed.text
        assert store.list_by_strategy(sid)[0].metadata["name"] == "After"
        replay = client.patch(f"/bff/strategies/{sid}", headers=allowed_headers, json={"name": "After"})
        assert replay.json() == allowed.json()
        foreign_replay = client.patch(f"/bff/strategies/{sid}", headers={
            **allowed_headers, "Authorization": "Bearer intruder:operator:tenant-b",
        }, json={"name": "After"})
        assert foreign_replay.status_code == 409, foreign_replay.text


def test_strategy_create_rejects_tenantless_principal():
    with _client() as client:
        response = client.post("/bff/strategies", headers={
            "Authorization": "Bearer tenantless:operator", "Idempotency-Key": "tenantless-create",
        }, json={"name": "Untrusted", "tenant_id": "tenant-a"})
        assert response.status_code == 403, response.text
        assert "tenantless-create" not in client.app.state.strategy_persona_idempotency


def test_bff_strategy_get_missing_returns_404() -> None:
    with _client() as client:
        resp = client.get("/bff/strategies/does-not-exist", headers=OPERATOR_HEADERS)
        assert resp.status_code == 404
        body = resp.json()
        error = body["detail"]["error"] if isinstance(body.get("detail"), dict) else body["error"]
        assert error["code"] == "RESOURCE_NOT_FOUND"


def test_bff_strategy_patch_updates_overlay_fields() -> None:
    with _client() as client:
        create_resp = client.post(
            "/bff/strategies",
            headers=IDEMPOTENT_HEADERS,
            json={"name": "Momentum Alpha"},
        )
        assert create_resp.status_code == 201, create_resp.text
        strategy_id = create_resp.json()["data"]["id"]
        rks = getattr(client.app.state.read_store, "research_knowledge_source", None)
        if rks and hasattr(rks, "_strategy_specs"):
            rks._strategy_specs[strategy_id] = {
                "strategy_id": strategy_id,
                "versions": [{"version_id": "v1", "lifecycle_state": "draft", "name": "Momentum Alpha", "persona_ids": []}],
                "current_version_id": "v1",
            }

        patch_resp = client.patch(
            f"/bff/strategies/{strategy_id}",
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "strat-rank-test-key-2"},
            json={"risk": "critical", "state": "active"},
        )
        assert patch_resp.status_code == 200, patch_resp.text
        data = patch_resp.json()["data"]
        assert data["risk"] == "critical"
        assert data["state"] == "deployed"  # lifecycle map: active -> deployed


def test_bff_retired_ranking_formulas_wrappers_removed_and_action_guard_kept() -> None:
    with _client() as client:
        for method, path in (
            ("get", "/bff/ranking/formulas"),
            ("post", "/bff/ranking/formulas"),
            ("get", "/bff/ranking/formulas/formula-1"),
            ("patch", "/bff/ranking/formulas/formula-1"),
        ):
            resp = getattr(client, method)(path, headers=OPERATOR_HEADERS)
            assert resp.status_code in {404, 405}, resp.text

        action_resp = client.post(
            "/bff/ranking/formulas/formula-1/actions/promote",
            headers=OPERATOR_HEADERS,
        )
        assert action_resp.status_code == 410, action_resp.text
        detail = action_resp.json()["detail"]
        assert detail["error"]["code"] == "ACTION_RETIRED"


def test_bff_rankings_list_returns_200() -> None:
    with _client() as client:
        resp = client.get("/bff/rankings", headers=OPERATOR_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert body["page_info"]["total"] == len(body["data"])


def test_bff_strategy_seed_review_and_merge_round_trip() -> None:
    with _seed_review_client() as (client, _seed_store_path):
        review_resp = client.post(
            "/bff/management/strategy-seeds/strat-rank-seed-a/review",
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "strat-rank-review-key-1"},
            json={"action": "accept", "reason": "meets bar"},
        )
        assert review_resp.status_code == 202, review_resp.text
        review_data = review_resp.json()["data"]
        assert review_data["seed_id"] == "strat-rank-seed-a"
        assert review_data["status"] == "accepted"

        merge_resp = client.post(
            "/bff/management/strategy-seeds/strat-rank-seed-b/merge",
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "strat-rank-merge-key-1"},
            json={"target_seed_id": "strat-rank-seed-a", "reason": "duplicate hypothesis"},
        )
        assert merge_resp.status_code == 202, merge_resp.text
        merge_data = merge_resp.json()["data"]
        assert merge_data["seed_id"] == "strat-rank-seed-b"
        assert merge_data["status"] == "merged"

        card_resp = client.get(
            "/bff/management/strategy-seeds/strat-rank-seed-a",
            headers=OPERATOR_HEADERS,
        )
        assert card_resp.status_code == 200
        assert card_resp.json()["data"]["status"] == "accepted"


def test_bff_strategy_write_fails_closed_without_canonical_writer() -> None:
    """Strategy write must fail closed with 503 and never fall back to mutating read_store._data."""
    class OnlyDataReadStore:
        def __init__(self) -> None:
            self._data: dict = {"strategies": {}}

        def list_strategy_specs(self) -> list:
            return []

    only_data_store = OnlyDataReadStore()
    with _client(store=only_data_store) as client:
        resp = client.post(
            "/bff/strategies",
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "strat-fail-closed-test-1"},
            json={"name": "Fail Closed Strategy", "risk": "medium"},
        )
        assert resp.status_code == 503, resp.text
        # Crucially: _data must NOT have been written to
        assert len(only_data_store._data["strategies"]) == 0


def test_bff_create_strategies_router_rejects_strategy_overlay() -> None:
    from services.control_plane.bff.strategies.router import create_strategies_router

    with pytest.raises(AttributeError, match="strategy_overlay is retired"):
        create_strategies_router(strategy_overlay={"s-1": {"name": "illegal"}})


def test_project_strategy_dto_rejects_overlay() -> None:
    from services.control_plane.bff.strategies.routes.common import StrategyRouteContext

    ctx = StrategyRouteContext(
        read_surface=None,
        get_read_store=lambda: None,
        extract_identity=lambda *a: {},
        require_read_role=lambda *a: None,
        require_operator_role=lambda *a: None,
        bff_error=lambda *a, **k: Exception(),
        utc_now=lambda: "2026-09-07T00:00:00Z",
        page_slice=lambda items, *a: (items, None),
        read_surface_meta=lambda *a, **k: {},
        reject_body_idempotency_key=lambda *a: None,
        resolve_final_idempotency_key=lambda *a: "",
        stable_json_hash=lambda *a: "",
        request_dry_run_requested=lambda: False,
        dry_run_success_response=lambda *a, **k: {},
        normalize_lifecycle_state=lambda s: str(s or "draft"),
        normalize_risk_level=lambda r: str(r or "medium"),
        strategy_persona_idempotency_check=lambda *a: None,
        strategy_persona_idempotency={},
        strategy_seed_replication_idempotency={},
        strategy_seed_review_idempotency={},
        list_governance_audit_events=None,
        ooda_packet_list_payload=None,
        require_ooda_packet_routes_enabled=None,
        bff_me_tenant_payload=None,
        list_persona_records=None,
        list_strategy_summaries=None,
    )

    with pytest.raises(AttributeError, match="strategy overlay is retired"):
        ctx.project_strategy_dto({"id": "s-1"}, overlay={"name": "illegal"})


def test_bff_strategy_detail_single_owner_read_and_alpha_never_uses_source_kind() -> None:
    with _client() as client:
        rks = getattr(client.app.state.read_store, "research_knowledge_source", None)
        assert rks is not None
        rks._strategy_specs["strat-detail-single-01"] = {
            "strategy_id": "strat-detail-single-01",
            "title": "Single Read Detail Strategy",
            "lifecycle_state": "approved",
            "risk": "medium",
            "versions": [
                {
                    "spec_version_id": "reg-strat-detail-single-01-v1",
                    "spec_version": "1.0.0",
                    "title": "Single Read Detail Strategy",
                    "lifecycle_state": "approved",
                    "risk": "medium",
                    "source_kind": "workflow",
                    "hypothesis": "Never leak hypothesis into alpha.",
                    "market_scope": {"markets": ["TWSE"]},
                    "persona_ids": ["persona-01"],
                }
            ],
            "current_spec_version_id": "reg-strat-detail-single-01-v1",
            "current_spec_version": "1.0.0",
        }
        detail_calls: list[str] = []
        list_calls: list[str] = []
        orig_detail = rks.get_strategy_spec_detail
        orig_list = rks.list_strategy_specs

        def _spy_detail(sid: str, **kwargs: Any):
            detail_calls.append(sid)
            return orig_detail(sid, **kwargs)

        def _spy_list(**kwargs: Any):
            list_calls.append("list_strategy_specs")
            return orig_list(**kwargs)

        rks.get_strategy_spec_detail = _spy_detail  # type: ignore[method-assign]
        rks.list_strategy_specs = _spy_list  # type: ignore[method-assign]

        detail_resp = client.get("/bff/strategies/strat-detail-single-01", headers=OPERATOR_HEADERS)
        assert detail_resp.status_code == 200, detail_resp.text
        assert detail_calls == ["strat-detail-single-01"]
        assert list_calls == []
        assert detail_resp.json()["data"]["alpha"] == ""


def test_bff_strategy_detail_spec_projection_real_shape_absent_fields_and_tenant_isolation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import urllib.parse
    import services.control_plane.bff.command_adapters.base as adapter_base
    from services.control_plane.bff.core.owner_reads import OwnerReadContextMiddleware

    sid_real = "strategy-persona-298e138c136c9840b0e4"
    sid_absent = "strategy-minimal-absent-spec"
    sid_foreign = "strategy-foreign-tenant-b"

    registry_rows = {
        sid_real: [
            {
                "entry": {
                    "registry_id": f"reg-{sid_real}-v2",
                    "artifact_type": "strategy_spec",
                    "strategy_id": sid_real,
                    "version": "1.1.0",
                    "artifact_state": "approved",
                    "owner_tenant": "tenant-corp",
                    "created_at": "2026-11-05T15:00:00Z",
                    "updated_at": "2026-11-05T15:30:00Z",
                    "metadata": {
                        "tenant_id": "tenant-corp",
                        "persona_id": "persona-298e138c136c9840b0e4",
                        "capital_pool_id": "pool-persona-paper-298e138c136c9840b0e4",
                        "execution_context": "paper",
                        "provisioning_request_hash": "hash-do-not-expose",
                        "rollback_target_registry_id": f"reg-{sid_real}-v1",
                        "strategy_spec": {
                            "spec_version": "1.0",
                            "strategy_id": sid_real,
                            "title": "TW Semiconductor Momentum",
                            "hypothesis": "Close-to-close momentum over 2330.TWSE",
                            "objective": "Admit Persona to paper-only evaluation",
                            "lifecycle_state": "approved",
                            "market_scope": {
                                "symbols": ["2330.TWSE"],
                                "asset_classes": ["equity"],
                                "frequency": "1d",
                            },
                            "execution_profile": {
                                "signal_schema_version": "1.0",
                                "quantity_type": "SHARES",
                                "rebalance_cadence": "1d bar close, paper only",
                                "execution_mode_hint": "paper",
                            },
                            "evaluation_plan": {
                                "metrics": [
                                    "paper_pnl",
                                    "max_drawdown",
                                    "turnover_rate",
                                    "no_live_capital_side_effects",
                                ],
                                "paper_gate": "ApprovalDecision may approve this target for paper-stage deployment only, with capital_scale_pct=0.",
                                "live_gate": "Canary and live activation remain fail-closed; this revision never authorizes live capital.",
                            },
                            "governance": {
                                "approval_required": True,
                                "policy_id": "persona-paper-admission",
                                "risk_profile": "low: paper-only Persona admission, zero live capital",
                            },
                            "provenance": {
                                "source_kind": "workflow",
                                "created_at": "2026-11-05T15:00:00Z",
                                "created_by": "op-user",
                            },
                            "metadata": {
                                "market": "TW",
                                "execution_context": "paper",
                                "traits": {
                                    "instruments": ["2330.TWSE"],
                                    "time_horizon": "1d",
                                    "hard_rules": ["zero_capital"],
                                    "persona_voice": "concise",
                                },
                                "internal_secret": "must-not-leak",
                            },
                        },
                    },
                },
                "deployment_stage": "none",
            }
        ],
        sid_absent: [
            {
                "entry": {
                    "registry_id": f"reg-{sid_absent}-v1",
                    "artifact_type": "strategy_spec",
                    "strategy_id": sid_absent,
                    "version": "1.0.0",
                    "artifact_state": "draft",
                    "owner_tenant": "tenant-corp",
                    "created_at": "2026-11-05T12:00:00Z",
                    "updated_at": "2026-11-05T12:00:00Z",
                    "metadata": {
                        "tenant_id": "tenant-corp",
                        "unlisted_field": "drop-me",
                        "strategy_spec": {
                            "spec_version": "1.0.0",
                            "strategy_id": sid_absent,
                            "title": "Minimal Absent Spec Strategy",
                            "lifecycle_state": "draft",
                            "metadata": {
                                "market": "",
                                "traits": {"instruments": [], "hard_rules": ["drop"]},
                                "arbitrary_blob": {"nested": True},
                            },
                        },
                    },
                },
                "deployment_stage": "none",
            }
        ],
        sid_foreign: [
            {
                "entry": {
                    "registry_id": f"reg-{sid_foreign}-v1",
                    "artifact_type": "strategy_spec",
                    "strategy_id": sid_foreign,
                    "version": "1.0.0",
                    "artifact_state": "approved",
                    "owner_tenant": "tenant-b",
                    "created_at": "2026-11-05T12:00:00Z",
                    "updated_at": "2026-11-05T12:00:00Z",
                    "metadata": {
                        "tenant_id": "tenant-b",
                        "strategy_spec": {
                            "spec_version": "1.0.0",
                            "strategy_id": sid_foreign,
                            "title": "Foreign Strategy",
                            "lifecycle_state": "approved",
                            "metadata": {"market": "US"},
                        },
                    },
                },
                "deployment_stage": "none",
            }
        ],
    }

    def _http_stub(url: str, **kwargs: Any) -> Any:
        parsed = urllib.parse.urlparse(url)
        if parsed.path.startswith("/api/registry/strategies/") and parsed.path.endswith("/strategy-specs"):
            target_sid = urllib.parse.unquote(parsed.path.split("/")[4])
            return list(registry_rows.get(target_sid, []))
        raise AssertionError(f"Unexpected owner HTTP call: {url}")

    monkeypatch.setenv("PANTHEON_REGISTRY_API_URL", "http://registry.local")
    monkeypatch.setattr(adapter_base, "http_request_json", _http_stub)

    read_store = create_in_memory_read_surface_ports()
    strategy_write_owner = create_strategy_write_owner()
    app = _build_app(read_store, strategy_write_owner)
    app.add_middleware(OwnerReadContextMiddleware)
    with TestClient(app) as client:
        # 1. Real supplied shape for strategy-persona-298e138c136c9840b0e4
        resp_real = client.get(f"/bff/strategies/{sid_real}", headers=OPERATOR_HEADERS)
        assert resp_real.status_code == 200, resp_real.text
        data_real = resp_real.json()["data"]
        assert data_real["id"] == sid_real
        assert data_real["alpha"] == ""  # source_kind and hypothesis are never alpha
        spec_real = data_real["spec"]
        assert spec_real == {
            "spec_version_id": f"reg-{sid_real}-v2",
            "spec_version": "1.1.0",
            "market_scope": {
                "symbols": ["2330.TWSE"],
                "asset_classes": ["equity"],
                "frequency": "1d",
            },
            "execution_profile": {
                "signal_schema_version": "1.0",
                "quantity_type": "SHARES",
                "rebalance_cadence": "1d bar close, paper only",
                "execution_mode_hint": "paper",
            },
            "evaluation_plan": {
                "metrics": [
                    "paper_pnl",
                    "max_drawdown",
                    "turnover_rate",
                    "no_live_capital_side_effects",
                ],
                "paper_gate": "ApprovalDecision may approve this target for paper-stage deployment only, with capital_scale_pct=0.",
                "live_gate": "Canary and live activation remain fail-closed; this revision never authorizes live capital.",
            },
            "metadata": {
                "market": "TW",
                "execution_context": "paper",
                "traits": {
                    "instruments": ["2330.TWSE"],
                    "time_horizon": "1d",
                },
            },
        }
        assert "benchmark" not in spec_real["evaluation_plan"]
        assert "provisioning_request_hash" not in spec_real["metadata"]
        assert "internal_secret" not in spec_real["metadata"]
        assert "hard_rules" not in spec_real["metadata"]["traits"]

        # 2. Absent/empty spec fields remain None and arbitrary metadata is not exposed
        resp_absent = client.get(f"/bff/strategies/{sid_absent}", headers=OPERATOR_HEADERS)
        assert resp_absent.status_code == 200, resp_absent.text
        spec_absent = resp_absent.json()["data"]["spec"]
        assert spec_absent == {
            "spec_version_id": f"reg-{sid_absent}-v1",
            "spec_version": "1.0.0",
            "market_scope": None,
            "execution_profile": None,
            "evaluation_plan": None,
            "metadata": None,
        }

        # 3. Tenant-negative case: foreign tenant row is rejected (403 FORBIDDEN)
        resp_foreign = client.get(f"/bff/strategies/{sid_foreign}", headers=OPERATOR_HEADERS)
        assert resp_foreign.status_code == 403, resp_foreign.text


