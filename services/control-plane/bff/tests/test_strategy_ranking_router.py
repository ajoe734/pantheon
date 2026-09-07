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

import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

os.environ.setdefault("REGISTRY_STORE_BACKEND", "memory")

from fastapi.testclient import TestClient
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main as bff_main  # noqa: E402
from ports import create_in_memory_read_surface_ports, create_strategy_write_owner  # noqa: E402
from services.source_ingestion.strategy_seed_builder import (  # noqa: E402
    StrategySpecSeed,
    StrategySpecSeedStatus,
)
from services.source_ingestion.strategy_seed_store import StrategySpecSeedStore  # noqa: E402

OPERATOR_HEADERS = {"Authorization": "Bearer strat-rank-op:operator"}
IDEMPOTENT_HEADERS = {**OPERATOR_HEADERS, "Idempotency-Key": "strat-rank-test-key-1"}


@contextmanager
def _client(store: Optional[Any] = None):
    original_store = bff_main.read_store
    original_writer = getattr(bff_main, "strategy_write_owner", None)
    read_store = store if store is not None else create_in_memory_read_surface_ports()
    bff_main.read_store = read_store
    if store is not None:
        if hasattr(store, "upsert_strategy") or hasattr(store, "create_strategy_spec"):
            bff_main.strategy_write_owner = create_strategy_write_owner(store=store)
        else:
            bff_main.strategy_write_owner = None
    else:
        bff_main.strategy_write_owner = create_strategy_write_owner()
    bff_main._STRATEGY_PERSONA_BFF_IDEMPOTENCY.clear()
    try:
        yield TestClient(bff_main.app)
    finally:
        bff_main.read_store = original_store
        bff_main.strategy_write_owner = original_writer
        bff_main._STRATEGY_PERSONA_BFF_IDEMPOTENCY.clear()


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
    original_store = bff_main.read_store
    tracked = os.environ.get("STRATEGY_SEED_STORE_PATH")
    with tempfile.TemporaryDirectory() as td:
        seed_store_path = Path(td) / "strategy_seeds.jsonl"
        os.environ["STRATEGY_SEED_STORE_PATH"] = str(seed_store_path)
        store = StrategySpecSeedStore(path=seed_store_path)
        store.save(_seed("strat-rank-seed-a"))
        store.save(_seed("strat-rank-seed-b"))
        bff_main.read_store = create_in_memory_read_surface_ports()
        bff_main._STRATEGY_SEED_REVIEW_BFF_IDEMPOTENCY.clear()
        try:
            yield TestClient(bff_main.app), seed_store_path
        finally:
            bff_main.read_store = original_store
            bff_main._STRATEGY_SEED_REVIEW_BFF_IDEMPOTENCY.clear()
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

        rks = getattr(bff_main.read_store, "research_knowledge_source", None)
        if rks and hasattr(rks, "_strategy_specs"):
            rks._strategy_specs[strategy_id] = {
                "strategy_id": strategy_id,
                "versions": [{"version_id": "v1", "lifecycle_state": "draft", "name": "Momentum Alpha", "persona_ids": []}],
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

        patch_resp = client.patch(
            f"/bff/strategies/{strategy_id}",
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "strat-rank-test-key-2"},
            json={"risk": "critical", "state": "active"},
        )
        assert patch_resp.status_code == 200, patch_resp.text
        data = patch_resp.json()["data"]
        assert data["risk"] == "critical"
        assert data["state"] == "deployed"  # lifecycle map: active -> deployed


def test_bff_deprecated_ranking_formulas_list_returns_410() -> None:
    with _client() as client:
        resp = client.get("/bff/ranking/formulas", headers=OPERATOR_HEADERS)
        assert resp.status_code == 410
        detail = resp.json()["detail"]
        assert detail["error"]["details"]["route"] == "/bff/ranking/formulas"
        assert detail["error"]["details"]["replacement"] == "/bff/ranking-formulas"
        assert resp.headers.get("X-Deprecated") == "true"


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
        strategy_persona_action_command=None,
        strategy_persona_idempotency={},
        strategy_seed_replication_idempotency={},
        strategy_seed_review_idempotency={},
        list_governance_audit_events=None,
        ooda_packet_list_payload=None,
        require_ooda_packet_routes_enabled=None,
        deprecated_bff_path_response=None,
        bff_me_tenant_payload=None,
        list_persona_records=None,
        list_strategy_summaries=None,
    )

    with pytest.raises(AttributeError, match="strategy overlay is retired"):
        ctx.project_strategy_dto({"id": "s-1"}, overlay={"name": "illegal"})
