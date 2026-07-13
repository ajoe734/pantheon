from __future__ import annotations

import os
import sys
import tempfile
from typing import Any

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main as bff_main
from read_store import ReadSurfaceStore


HEADERS = {"Authorization": "Bearer codex2-ppl-alloc:operator,reviewer"}
LIVE_PERSONA_ID = "persona-ppl-alloc-012-live"
LIVE_BINDING_ID = "binding-ppl-alloc-012-live"
LIVE_RUNTIME_ID = "runtime-ppl-alloc-012-live"


def _client(td: str, *, fallback: bool = True) -> TestClient:
    bff_main.read_store = ReadSurfaceStore(
        os.path.join(td, "read-surfaces.json"),
        allow_local_snapshot_fallback=fallback,
    )
    bff_main._CAPITAL_BFF_IDEMPOTENCY.clear()
    return TestClient(bff_main.app, raise_server_exceptions=False)


def _seed_live_persona(store: ReadSurfaceStore) -> None:
    store.create_persona(
        persona_id=LIVE_PERSONA_ID,
        name="PPL Alloc Live",
        actor_id="Codex2",
        lifecycle_state="live_running",
        metadata={"capital_mode": "live", "deployment_stage": "live"},
    )
    store.create_persona_binding(
        binding_id=LIVE_BINDING_ID,
        persona_id=LIVE_PERSONA_ID,
        capital_pool_id="pool-real",
        actor_id="Codex2",
        role="primary",
        validity="active",
        metadata={
            "allowed_deployment_scope": "live",
            "capital_mode": "live",
            "capital_sleeve_id": "sleeve-live",
            "current_weight": 0.04,
        },
    )
    store.create_runtime_binding(
        runtime_id=LIVE_RUNTIME_ID,
        name="PPL Alloc Live",
        persona_id=LIVE_PERSONA_ID,
        binding_id=LIVE_BINDING_ID,
        deployment_plan_id="plan-ppl-alloc-012-live",
        runtime_kind="live",
        actor_id="Codex2",
        state="running",
        params={
            "capital_pool_id": "pool-real",
            "capital_sleeve_id": "sleeve-live",
            "current_weight": 0.04,
        },
    )

    original_sessions = store.get_sessions_for_persona
    original_telemetry = store.get_telemetry_summary

    def sessions_for_persona(persona_id: str | None) -> list[dict[str, Any]] | None:
        if persona_id == LIVE_PERSONA_ID:
            return [
                {
                    "id": "session-ppl-alloc-012-live",
                    "status": "active",
                    "runtime_binding_id": LIVE_RUNTIME_ID,
                    "capital_pool_id": "pool-real",
                    "last_heartbeat_at": "2026-07-10T00:00:00Z",
                }
            ]
        return original_sessions(persona_id)

    def telemetry_for_runtime(runtime_id: str) -> dict[str, Any] | None:
        if runtime_id == LIVE_RUNTIME_ID:
            return {
                "runtime_id": LIVE_RUNTIME_ID,
                "pnl": 0.12,
                "drawdown": 0.03,
                "fill_rate": 0.98,
                "avg_slippage_bps": 1.2,
                "total_trades": 48,
                "collected_at": "2026-07-10T00:00:00Z",
            }
        return original_telemetry(runtime_id)

    store.get_sessions_for_persona = sessions_for_persona  # type: ignore[method-assign]
    store.get_telemetry_summary = telemetry_for_runtime  # type: ignore[method-assign]


def _item_by_persona(items: list[dict[str, Any]], persona_id: str) -> dict[str, Any]:
    return next(item for item in items if item.get("persona_id") == persona_id)


def test_ranking_tuple_and_snapshot_round_trip_into_rebalance_proposal() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        try:
            client = _client(td)
            store = bff_main.read_store
            assert isinstance(store, ReadSurfaceStore)
            _seed_live_persona(store)

            quarterly = client.get(
                "/bff/management/quarterly-ranking",
                headers=HEADERS,
                params={"quarter": "2026-Q3", "page_size": 200},
            )
            assert quarterly.status_code == 200, quarterly.text
            quarterly_body = quarterly.json()
            snapshot_id = quarterly_body["data"]["ranking_snapshot_id"]
            assert snapshot_id.startswith("ranking-quarterly-2026-q3-")
            assert quarterly_body["data"]["summary"]["ranking_snapshot_id"] == snapshot_id
            assert quarterly_body["meta"]["ranking_snapshot_id"] == snapshot_id
            assert {
                item["ranking_snapshot_id"]
                for item in quarterly_body["data"]["items"]
            } == {snapshot_id}

            live_row = _item_by_persona(
                quarterly_body["data"]["items"],
                LIVE_PERSONA_ID,
            )
            assert live_row["stage"] == "live_running"
            assert live_row["capital_scope"] == "live_sleeve"
            assert live_row["capital_scope_id"] == "sleeve-live"
            assert live_row["capital_pool_id"] == "pool-real"
            assert live_row["capital_sleeve_id"] == "sleeve-live"
            assert live_row["current_weight"] == 0.04
            assert live_row["current_weight_source"] == "persona_binding"
            assert live_row["eligible"] is True
            assert live_row["exclusion_reasons"] == []
            assert "telemetry-summary:runtime-ppl-alloc-012-live" in {
                ref["ref_id"] for ref in live_row["evidence_refs"]
            }

            paper_row = _item_by_persona(
                quarterly_body["data"]["items"],
                "persona-alpha",
            )
            assert paper_row["stage"] == "paper_running"
            assert paper_row["current_weight"] is None
            assert paper_row["current_weight_source"] == "not_applicable_paper_ledger"
            assert paper_row["capital_scope"] == "paper_ledger"

            filtered = client.get(
                "/bff/management/quarterly-ranking",
                headers=HEADERS,
                params={"quarter": "2026-Q3", "q": "PPL Alloc Live", "page_size": 1},
            )
            repeated = client.get(
                "/bff/management/quarterly-ranking",
                headers=HEADERS,
                params={"quarter": "2026-Q3", "page_size": 200},
            )
            assert filtered.status_code == repeated.status_code == 200
            assert filtered.json()["data"]["ranking_snapshot_id"] == snapshot_id
            assert repeated.json()["data"]["ranking_snapshot_id"] == snapshot_id
            admin_view = client.get(
                "/bff/management/quarterly-ranking",
                headers={"Authorization": "Bearer ppl-alloc-admin:admin"},
                params={"quarter": "2026-Q3", "page_size": 200},
            )
            assert admin_view.status_code == 200, admin_view.text
            assert admin_view.json()["data"]["ranking_snapshot_id"] == snapshot_id

            drilldown = client.get(
                "/bff/management/quarterly-ranking/drilldown",
                headers=HEADERS,
                params={"quarter": "2026-Q3", "persona_id": LIVE_PERSONA_ID},
            )
            assert drilldown.status_code == 200, drilldown.text
            drilldown_body = drilldown.json()
            assert drilldown_body["data"]["ranking_snapshot_id"] == snapshot_id
            assert drilldown_body["data"]["ranking_item"]["ranking_snapshot_id"] == snapshot_id
            assert drilldown_body["summary"]["ranking_snapshot_id"] == snapshot_id
            assert drilldown_body["meta"]["ranking_snapshot_id"] == snapshot_id

            recommendations = client.get(
                "/bff/management/quarterly-ranking/recommendations",
                headers=HEADERS,
                params={
                    "quarter": "2026-Q3",
                    "personaId": LIVE_PERSONA_ID,
                    "page_size": 200,
                },
            )
            assert recommendations.status_code == 200, recommendations.text
            recommendation_body = recommendations.json()
            assert recommendation_body["data"]["ranking_snapshot_id"] == snapshot_id
            assert recommendation_body["meta"]["ranking_snapshot_id"] == snapshot_id
            assert recommendation_body["data"]["items"]
            for recommendation in recommendation_body["data"]["items"]:
                assert recommendation["ranking_snapshot_id"] == snapshot_id
                assert recommendation["stage"] == live_row["stage"]
                assert recommendation["current_weight"] == live_row["current_weight"]
                assert recommendation["capital_scope"] == live_row["capital_scope"]
                assert recommendation["evidence_refs"] == live_row["evidence_refs"][:5]

            rolling = client.get(
                "/bff/management/persona-league",
                headers=HEADERS,
                params={"page_size": 200},
            )
            rolling_rankings = client.get(
                "/bff/management/persona-league/rankings",
                headers=HEADERS,
                params={"criteria": "overall", "limit": 200},
            )
            assert rolling.status_code == rolling_rankings.status_code == 200
            rolling_snapshot_id = rolling.json()["data"]["ranking_snapshot_id"]
            assert rolling_snapshot_id.startswith("ranking-rolling-short-cycle-")
            assert rolling_rankings.json()["data"]["ranking_snapshot_id"] == rolling_snapshot_id
            assert rolling_snapshot_id != snapshot_id
            rolling_row = _item_by_persona(rolling.json()["data"]["items"], LIVE_PERSONA_ID)
            rolling_rank = _item_by_persona(
                rolling_rankings.json()["data"]["items"][0]["items"],
                LIVE_PERSONA_ID,
            )
            assert rolling_row["stage"] == rolling_rank["stage"] == "live_running"
            assert rolling_row["current_weight"] == rolling_rank["current_weight"] == 0.04
            assert rolling_rank["ranking_snapshot_id"] == rolling_snapshot_id

            evaluation = client.post(
                "/bff/management/allocation-policy/evaluate",
                headers=HEADERS,
                json={"ranking_snapshot_id": snapshot_id, "rows": [live_row]},
            )
            assert evaluation.status_code == 200, evaluation.text
            evaluation_body = evaluation.json()
            line = evaluation_body["data"]["lines"][0]
            assert evaluation_body["meta"]["ranking_snapshot_id"] == snapshot_id
            for field in (
                "ranking_snapshot_id",
                "persona_id",
                "stage",
                "capital_scope",
                "capital_pool_id",
                "capital_sleeve_id",
                "current_weight",
                "evidence_refs",
            ):
                assert line[field] == live_row[field]

            proposal = client.post(
                "/bff/rebalances",
                headers={**HEADERS, "Idempotency-Key": "ppl-alloc-012-proposal"},
                json={
                    "capital_pool_id": "pool-real",
                    "ranking_snapshot_id": snapshot_id,
                    "reason": "PPL-ALLOC-012 round trip",
                    "lines": evaluation_body["data"]["lines"],
                    "simulation": {"status": "passed"},
                    "constraints": {"pool_total_max": 1},
                    "rollback_target": {"snapshot_id": "allocation-before-ppl-alloc-012"},
                },
            )
            assert proposal.status_code == 202, proposal.text
            assert proposal.json()["ranking_snapshot_id"] == snapshot_id
            detail = client.get(
                f"/bff/rebalances/{proposal.json()['rebalance_id']}",
                headers=HEADERS,
            )
            assert detail.status_code == 200, detail.text
            detail_data = detail.json()["data"]
            assert detail_data["ranking_snapshot_id"] == snapshot_id
            assert detail_data["lines"][0]["ranking_snapshot_id"] == snapshot_id
            assert detail_data["lines"][0]["evidence_refs"] == live_row["evidence_refs"]

            missing_snapshot = client.post(
                "/bff/management/allocation-policy/evaluate",
                headers=HEADERS,
                json={"rows": [live_row]},
            )
            mixed_snapshot = client.post(
                "/bff/management/allocation-policy/evaluate",
                headers=HEADERS,
                json={
                    "ranking_snapshot_id": snapshot_id,
                    "rows": [{**live_row, "ranking_snapshot_id": "ranking-quarterly-other"}],
                },
            )
            mismatched_proposal = client.post(
                "/bff/rebalances",
                headers={**HEADERS, "Idempotency-Key": "ppl-alloc-012-mismatch"},
                json={
                    "capital_pool_id": "pool-real",
                    "ranking_snapshot_id": snapshot_id,
                    "lines": [{**line, "ranking_snapshot_id": "ranking-quarterly-other"}],
                    "simulation": {"status": "passed"},
                    "constraints": {"pool_total_max": 1},
                    "rollback_target": {"snapshot_id": "allocation-before-ppl-alloc-012"},
                },
            )
            assert missing_snapshot.status_code == 422
            assert mixed_snapshot.status_code == 422
            assert mismatched_proposal.status_code == 422
        finally:
            bff_main.read_store = original_store


def test_multiple_active_bindings_fail_closed_without_seed_weight() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        try:
            client = _client(td, fallback=False)
            store = bff_main.read_store
            assert isinstance(store, ReadSurfaceStore)
            persona_id = "persona-ppl-alloc-012-ambiguous"
            store.create_persona(
                persona_id=persona_id,
                name="PPL Alloc Ambiguous",
                actor_id="Codex2",
                lifecycle_state="live_running",
                metadata={"capital_mode": "live", "deployment_stage": "live"},
            )
            for suffix, weight in (("a", 0.03), ("b", 0.07)):
                store.create_persona_binding(
                    binding_id=f"binding-ppl-alloc-012-{suffix}",
                    persona_id=persona_id,
                    capital_pool_id=f"pool-{suffix}",
                    actor_id="Codex2",
                    validity="active",
                    metadata={
                        "capital_mode": "live",
                        "capital_sleeve_id": f"sleeve-{suffix}",
                        "current_weight": weight,
                    },
                )

            response = client.get(
                "/bff/management/quarterly-ranking",
                headers=HEADERS,
                params={"quarter": "2026-Q3", "page_size": 200},
            )
            assert response.status_code == 200, response.text
            row = _item_by_persona(response.json()["data"]["items"], persona_id)
            assert row["stage"] == "live_running"
            assert row["current_weight"] is None
            assert row["current_weight_source"] == "unavailable"
            assert row["capital_pool_id"] is None
            assert row["capital_sleeve_id"] is None
            assert row["eligible"] is False
            assert {
                "binding_mismatch",
                "missing_current_weight",
                "missing_capital_binding",
            }.issubset(set(row["exclusion_codes"]))
        finally:
            bff_main.read_store = original_store


def test_binding_runtime_and_stage_mismatches_fail_closed() -> None:
    binding = {
        "id": "binding-current-live",
        "binding_id": "binding-current-live",
        "persona_id": "persona-mismatch",
        "status": "active",
        "metadata": {"capital_mode": "live", "current_weight": 0.08},
    }
    mismatched_runtime = {
        "id": "runtime-old-paper",
        "runtime_id": "runtime-old-paper",
        "persona_id": "persona-mismatch",
        "binding_id": "binding-old-paper",
        "state": "running",
        "runtime_kind": "paper",
    }
    selected_binding, selected_runtime, resolution = bff_main._pm12_binding_runtime_context(
        persona_id="persona-mismatch",
        item={
            "binding_id": "binding-current-live",
            "runtime_ids": ["runtime-old-paper"],
        },
        bindings=[binding],
        runtimes=[mismatched_runtime],
    )
    assert selected_binding["binding_id"] == "binding-current-live"
    assert selected_runtime == {}
    assert resolution == "binding_mismatch"

    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        try:
            client = _client(td, fallback=False)
            store = bff_main.read_store
            assert isinstance(store, ReadSurfaceStore)
            store.create_persona(
                persona_id="persona-stage-mismatch",
                name="PPL Stage Mismatch",
                actor_id="Codex2",
                lifecycle_state="live_running",
                metadata={"capital_mode": "live", "deployment_stage": "live"},
            )
            store.create_persona_binding(
                binding_id="binding-stage-mismatch",
                persona_id="persona-stage-mismatch",
                capital_pool_id="pool-paper-wrong",
                actor_id="Codex2",
                validity="active",
                metadata={"capital_mode": "paper", "current_weight": 0.09},
            )
            store.create_persona(
                persona_id="persona-inactive-binding",
                name="PPL Inactive Binding",
                actor_id="Codex2",
                lifecycle_state="live_running",
                metadata={"capital_mode": "live", "deployment_stage": "live"},
            )
            store.create_persona_binding(
                binding_id="binding-inactive",
                persona_id="persona-inactive-binding",
                capital_pool_id="pool-expired",
                actor_id="Codex2",
                validity="expired",
                metadata={"capital_mode": "live", "current_weight": 0.11},
            )

            response = client.get(
                "/bff/management/quarterly-ranking",
                headers=HEADERS,
                params={"quarter": "2026-Q3", "page_size": 200},
            )
            assert response.status_code == 200, response.text
            rows = response.json()["data"]["items"]
            stage_mismatch = _item_by_persona(rows, "persona-stage-mismatch")
            assert stage_mismatch["stage"] == "live_running"
            assert stage_mismatch["current_weight"] is None
            assert stage_mismatch["capital_scope"] == "unbound"
            assert "binding_mismatch" in stage_mismatch["exclusion_codes"]

            inactive = _item_by_persona(rows, "persona-inactive-binding")
            assert inactive["stage"] == "live_running"
            assert inactive["current_weight"] is None
            assert inactive["capital_scope"] == "unbound"
            assert inactive["binding_resolution"] == "inactive"
            assert inactive["eligible"] is False
        finally:
            bff_main.read_store = original_store
