from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Iterator
from unittest.mock import patch

from fastapi.testclient import TestClient

from fastapi import FastAPI

from services.control_plane.bff.auth import policy as auth_policy
from services.control_plane.bff.capital.router import create_capital_router
from services.control_plane.bff.command_adapters.router import create_command_adapters_router
from services.control_plane.bff.command_adapters.service import CommandAdapterService
from services.control_plane.bff.command_adapters.retired import RETIRED_COMMANDS
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.core.app_factory import create_core_router
from services.control_plane.bff.core.errors import register_error_handlers
from services.control_plane.bff.management_read_models.router import create_management_router
from services.control_plane.bff.models import (
    CommandStatus,
    CommandType,
    ObjectType,
    TargetObject,
    utc_now,
)
from services.control_plane.bff.personas import PersonaService, create_personas_router
from services.control_plane.bff.personas.routes.common import (
    run_management_read as _real_run_management_read,
)
from services.control_plane.bff.personas.service import (
    create_persona_registry_write_owner,
)
from services.control_plane.bff.ports import ReadSurfacePorts
from services.control_plane.bff.governance.service import (
    human_inbox_surface_timeout_seconds as _human_inbox_surface_timeout_seconds,
)

OPERATOR_HEADERS = {"Authorization": "Bearer op-promo:operator"}
APPROVER_HEADERS = {"Authorization": "Bearer op-promo-approver:approver"}
ADMIN_HEADERS = {"Authorization": "Bearer op-promo-admin:admin"}

# The default caller tenant this BFF resolves an operator identity to when no
# tenant claim is present on the token (see
# ``personas/service.py::_bff_me_tenant_payload``'s ``default_tenant``
# fallback chain, which ends in the literal "pantheon-dev"). A tenant-scoped
# persona read (``personas/service.py::_list_persona_records``, ~L2288)
# admits only records whose explicit ``tenant_id`` matches this value;
# tenantless rows are treated as catalog/malformed data and fail closed by
# design. Fixtures that want a persona to be readable through
# ``/bff/management/promotion-reviews`` must set this tenant_id explicitly.
_PM12_ELIGIBLE_TENANT_ID = "pantheon-dev"


def build_pm12_eligible_persona_records(
    persona_id: str,
    runtime_id: str,
    binding_id: str,
    *,
    tenant_id: str = _PM12_ELIGIBLE_TENANT_ID,
    lifecycle_state: str = "paper_running",
    pnl: float = 0.85,
    drawdown: float = 0.01,
    sharpe_ratio: float = 3.2,
    fill_rate: float = 0.99,
    avg_slippage_bps: float = 0.2,
) -> dict[str, dict[str, Any]]:
    """Canonical fixture builder for a persona that clears every PM12
    promotion-review eligibility gate.

    A promotion-review-eligible persona must simultaneously satisfy three
    independent production gates (see ``services/control-plane/bff/personas/service.py``):

    1. Tenant-scoped read admission (``_list_persona_records``, ~L2288):
       the record's ``tenant_id`` must match the caller's resolved tenant.
    2. League-row eligibility (``_pm12_persona_league_ranking_item``,
       ~L6270-6350, referenced in the task brief as ~L6033): requires an
       operational ``lifecycle_state`` (e.g. "paper_running"), a resolved
       active RuntimeBinding, a resolved active session joined to that
       RuntimeBinding, and non-empty telemetry coverage -- otherwise the row
       is marked ``eligible: False`` with explicit ``exclusion_reasons``.
    3. PM12 recommendation score gates (``_pm12_recommendation_action_ids``,
       ~L12344): a "promote_to_canary_candidate" recommendation requires
       overall_score >= 85, risk_score >= 70 (when present), and
       execution_score >= 65 (when present); those component scores are
       themselves derived from telemetry (pnl/drawdown/sharpe/fill_rate/
       slippage), so the default telemetry values here are tuned to clear
       that bar with headroom.

    Returns per-dataset record dicts ready to be merged into
    ``PromotionReviewTestReadPorts._data``.
    """
    return {
        "personas": {
            persona_id: {
                "id": persona_id,
                "persona_id": persona_id,
                "name": f"{persona_id} Persona",
                "lifecycle_state": lifecycle_state,
                "tenant_id": tenant_id,
                "mandate": "alpha_research_and_paper_execution",
                "strategy_family": "momentum",
                "created_at": "2026-03-01T00:00:00Z",
                "last_active_at": "2026-04-11T10:00:00Z",
                "metadata": {
                    "archetype": "momentum",
                    "risk_level": "low",
                    "success_rate": 0.95,
                },
            },
        },
        "bindings": {
            binding_id: {
                "id": binding_id,
                "persona_id": persona_id,
                "capital_pool_id": "pool-main",
                "runtime_binding_id": runtime_id,
                "status": "active",
                "validity": "active",
                "allowed_deployment_scope": "paper",
                "deployment_stage": "paper",
            },
        },
        "runtime_bindings": {
            runtime_id: {
                "id": runtime_id,
                "runtime_id": runtime_id,
                "persona_id": persona_id,
                "binding_id": binding_id,
                "persona_capital_binding_id": binding_id,
                "deployment_stage": "paper",
                "deployment_mode": "paper",
                "status": "running",
                "plan_id": f"plan-{persona_id}",
            },
        },
        "telemetry_summaries": {
            runtime_id: {
                "runtime_id": runtime_id,
                "window": "1h",
                "pnl": pnl,
                "drawdown": drawdown,
                "sharpe_ratio": sharpe_ratio,
                "total_trades": 120,
                "fill_rate": fill_rate,
                "avg_slippage_bps": avg_slippage_bps,
                "collected_at": "2026-04-10T15:00:00Z",
            },
        },
        "sessions": {
            f"sess-{persona_id}": {
                "id": f"sess-{persona_id}",
                "session_id": f"sess-{persona_id}",
                "persona_id": persona_id,
                "status": "active",
                "deployment_stage": "paper",
                "runtime_binding_id": runtime_id,
            },
        },
        "capability_snapshots": {
            f"cap-{persona_id}": {
                "id": f"cap-{persona_id}",
                "snapshot_id": f"cap-{persona_id}",
                "persona_id": persona_id,
                "status": "verified",
            },
        },
    }


class PromotionReviewTestReadPorts(ReadSurfacePorts):
    def __init__(self, seed_data: dict[str, Any] | None = None, *, allow_fallback: bool = True) -> None:
        super().__init__()
        if seed_data is not None:
            self._data = seed_data
        else:
            self._data = {}
        self._data.setdefault("personas", {})["persona-alpha"] = {
            "id": "persona-alpha",
            "persona_id": "persona-alpha",
            "name": "Alpha Persona",
            "lifecycle_state": "active",
            "mandate": "systematic_crypto_trading",
            "strategy_family": "momentum",
            "created_at": "2026-03-01T00:00:00Z",
            "last_active_at": "2026-04-11T10:00:00Z",
            "metadata": {
                "archetype": "momentum",
                "risk_level": "low",
                "success_rate": 0.95,
            },
        }
        self._data.setdefault("bindings", {})["binding-alpha"] = {
            "id": "binding-alpha",
            "persona_id": "persona-alpha",
            "capital_pool_id": "pool-main",
            "status": "active",
            "validity": "active",
            "allowed_deployment_scope": "paper",
            "deployment_stage": "paper",
        }
        self._data.setdefault("runtime_bindings", {})["runtime-042"] = {
            "id": "runtime-042",
            "runtime_id": "runtime-042",
            "persona_id": "persona-alpha",
            "deployment_stage": "paper",
            "deployment_mode": "paper",
            "status": "running",
            "plan_id": "plan-F-042",
        }
        self._data.setdefault("telemetry_summaries", {})["runtime-042"] = {
            "runtime_id": "runtime-042",
            "window": "1h",
            "pnl": 0.85,
            "drawdown": 0.01,
            "sharpe_ratio": 3.2,
            "total_trades": 120,
            "fill_rate": 0.99,
            "avg_slippage_bps": 0.2,
            "collected_at": "2026-04-10T15:00:00Z",
        }
        self._data.setdefault("sessions", {})["sess-001"] = {
            "id": "sess-001",
            "session_id": "sess-001",
            "persona_id": "persona-alpha",
            "status": "active",
            "deployment_stage": "paper",
            "runtime_binding_id": "runtime-042",
        }
        self._data.setdefault("capability_snapshots", {})["cap-001"] = {
            "id": "cap-001",
            "snapshot_id": "cap-001",
            "persona_id": "persona-alpha",
            "status": "verified",
        }
        # persona-us-equity / persona-crypto-perp are seeded as PM12-eligible,
        # tenant-scoped personas via the canonical builder above. Several
        # tests (e.g. test_human_inbox_ignores_decision_with_mismatched_target_aliases)
        # require *two* independently eligible promotion-review candidates
        # driven off exactly these runtime ids
        # ("runtime-us-equity-paper" / "runtime-crypto-paper"), so both stay
        # eligible rather than collapsing to a single fixture persona.
        for pid, rid, bid in (
            ("persona-us-equity", "runtime-us-equity-paper", "binding-us-equity-paper"),
            ("persona-crypto-perp", "runtime-crypto-paper", "binding-crypto-paper"),
        ):
            records = build_pm12_eligible_persona_records(pid, rid, bid)
            for collection, entries in records.items():
                self._data.setdefault(collection, {}).update(entries)
        self.allow_fallback = allow_fallback
        # Note: ReadSurfacePorts.__setattr__ retired the name "_ranking_snapshots"
        # (canonical ranking write owner/projection ports replaced it in
        # production). This fixture keeps its own test-local snapshot store
        # under a non-colliding name.
        self._promo_ranking_snapshots: dict[str, Any] = {}

    def dataset_source(self, dataset: str, **kwargs: Any) -> str:
        return "local_snapshot"

    def dataset_surface_status(self, dataset: str, *, snapshot_at: str, **kwargs: Any) -> dict[str, Any]:
        return {
            "status": "ok",
            "source": "local_snapshot",
            "snapshot_at": snapshot_at,
            "freshness": "fresh",
            "observed_time": snapshot_at,
            "coverage": 1.0,
            "missing_bindings": False,
        }

    def _get_dataset(self, name: str) -> dict[str, Any] | list[Any]:
        return self._data.setdefault(name, [])

    def get_persona(self, persona_id: str | None) -> dict[str, Any] | None:
        ds = self._data.get("personas", {})
        if isinstance(ds, dict):
            return ds.get(str(persona_id or ""))
        return next((p for p in ds if p.get("id") == persona_id or p.get("persona_id") == persona_id), None)

    def list_personas(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._data.get("personas", {})
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def list_capital_pools(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._data.get("capital_pools", {})
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def list_bindings(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._data.get("bindings", {})
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def list_deployment_plans(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._data.get("deployment_plans", {})
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def list_runtime_bindings(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._data.get("runtime_bindings") or self._data.get("runtime_instances") or self._data.get("runtimes") or {}
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def list_persona_league(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._data.get("persona_league", [])
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def list_governance_review_queue_items(self, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    def list_approval_queue_items(self, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    def list_authoritative_paper_runtime_monitoring_sessions(self) -> list[dict[str, Any]]:
        return []

    def get_bindings_for_persona(self, persona_id: str | None) -> list[dict[str, Any]]:
        ds = self._data.get("bindings", {})
        items = list(ds.values()) if isinstance(ds, dict) else list(ds)
        if persona_id:
            items = [b for b in items if b.get("persona_id") == persona_id]
        return items

    def get_bindings_for_pool(self, pool_id: str | None) -> list[dict[str, Any]]:
        ds = self._data.get("bindings", {})
        items = list(ds.values()) if isinstance(ds, dict) else list(ds)
        if pool_id:
            items = [b for b in items if b.get("capital_pool_id") == pool_id or b.get("pool_id") == pool_id]
        return items

    def get_capital_pool(self, pool_id: str | None) -> dict[str, Any] | None:
        ds = self._data.get("capital_pools", {})
        if isinstance(ds, dict):
            return ds.get(str(pool_id or ""))
        return next((p for p in ds if p.get("id") == pool_id or p.get("pool_id") == pool_id), None)

    def get_sessions_for_persona(self, persona_id: str | None) -> list[dict[str, Any]]:
        ds = self._data.get("sessions", {})
        items = list(ds.values()) if isinstance(ds, dict) else list(ds)
        if persona_id:
            items = [s for s in items if s.get("persona_id") == persona_id]
        return items

    def list_sessions_for_persona(self, persona_id: str | None, **kwargs: Any) -> list[dict[str, Any]]:
        return self.get_sessions_for_persona(persona_id)

    def get_teaching_sessions_for_persona(self, persona_id: str | None) -> list[dict[str, Any]]:
        return []

    def list_teaching_sessions_for_persona(self, persona_id: str | None, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    def get_persona_route_summary(self, persona_id: str | None) -> dict[str, Any]:
        return {}

    def get_telemetry_summary(self, runtime_id: str | None) -> dict[str, Any] | None:
        ds = self._data.get("telemetry_summaries", {})
        if isinstance(ds, dict):
            return ds.get(str(runtime_id or ""))
        return next((t for t in ds if t.get("runtime_id") == runtime_id), None)

    def get_runtime_binding(self, binding_id: str | None) -> dict[str, Any] | None:
        ds = self._data.get("runtime_bindings") or self._data.get("runtime_instances") or self._data.get("runtimes") or {}
        if isinstance(ds, dict):
            return ds.get(str(binding_id or ""))
        return next((r for r in ds if r.get("id") == binding_id or r.get("binding_id") == binding_id), None)

    def get_runtime_binding_by_runtime_id(self, runtime_id: str | None) -> dict[str, Any] | None:
        ds = self._data.get("runtime_bindings") or self._data.get("runtime_instances") or self._data.get("runtimes") or {}
        if isinstance(ds, dict):
            return ds.get(str(runtime_id or ""))
        return next((r for r in ds if r.get("id") == runtime_id or r.get("runtime_id") == runtime_id), None)

    def get_capability_snapshot_for_persona(self, persona_id: str | None) -> dict[str, Any] | None:
        ds = self._data.get("capability_snapshots", {})
        if isinstance(ds, dict):
            for item in ds.values():
                if isinstance(item, dict) and item.get("persona_id") == persona_id:
                    return item
        return None

    def get_persona_capabilities(self, persona_id: str | None) -> dict[str, Any] | None:
        return {}

    def put_ranking_snapshot(self, payload: dict[str, Any]) -> dict[str, Any]:
        snapshot_id = payload.get("id") or payload.get("ranking_snapshot_id") or "snap-1"
        self._promo_ranking_snapshots[snapshot_id] = payload
        return payload

    def get_ranking_snapshot(self, snapshot_id: str | None) -> dict[str, Any] | None:
        return self._promo_ranking_snapshots.get(str(snapshot_id or ""))


def _build_promotion_review_app(
    store: PromotionReviewTestReadPorts,
    command_store: CommandStore,
    *,
    run_management_read=_real_run_management_read,
) -> FastAPI:
    """Standalone app built from the same real, already-extracted production
    router factories the composition root mounts for the core health,
    persona-league, quarterly-ranking, promotion-review, capital,
    command-adapter, and management surfaces
    (``core.app_factory.create_core_router``,
    ``personas.create_personas_router``,
    ``capital.router.create_capital_router``,
    ``command_adapters.router.create_command_adapters_router``,
    ``management_read_models.router.create_management_router``), with the
    read surface, command store, and idempotency stores injected explicitly
    instead of reached through ``main.py`` module globals. No handler,
    validator, or precondition logic is reimplemented here.
    """
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(create_core_router({}))
    app.include_router(
        create_personas_router(
            service=PersonaService(
                write_owner=create_persona_registry_write_owner(),
                read_store=store,
                ranking_write_owner=store,
                command_store=command_store,
            ),
            extract_identity_fn=auth_policy.extract_identity,
            require_read_role_fn=auth_policy.require_read_role,
            require_operator_role_fn=auth_policy.require_operator_role,
            bff_error_fn=auth_policy.bff_error,
            utc_now_fn=utc_now,
        )
    )
    app.include_router(
        create_capital_router(
            read_surface=lambda: store,
            extract_identity=auth_policy.extract_identity,
            require_read_role=auth_policy.require_read_role,
            require_operator_role=auth_policy.require_operator_role,
            bff_error=auth_policy.bff_error,
            utc_now=utc_now,
        )
    )
    app.include_router(
        create_command_adapters_router(
            service=CommandAdapterService(
                command_store=lambda: command_store,
                read_surface=lambda: store,
                extract_identity=auth_policy.extract_identity,
                require_operator_role=auth_policy.require_operator_role,
                require_read_role=auth_policy.require_read_role,
                bff_error=auth_policy.bff_error,
                utc_now_fn=utc_now,
            )
        )
    )
    app.include_router(
        create_management_router(
            get_read_store=lambda: store,
            get_command_store=lambda: command_store,
            extract_identity=auth_policy.extract_identity,
            require_read_role=auth_policy.require_read_role,
            bff_error=auth_policy.bff_error,
            utc_now=utc_now,
            run_management_read=run_management_read,
        )
    )
    return app


@contextmanager
def _isolated_client(
    *, run_management_read=_real_run_management_read
) -> Iterator[tuple[TestClient, PromotionReviewTestReadPorts, CommandStore]]:
    with tempfile.TemporaryDirectory() as td:
        store = PromotionReviewTestReadPorts(allow_fallback=True)
        command_store = CommandStore(os.path.join(td, "commands.jsonl"))

        app = _build_promotion_review_app(
            store, command_store, run_management_read=run_management_read
        )
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client, store, command_store


def _idem() -> str:
    return f"promo-review-{uuid.uuid4().hex[:12]}"


def _first_review(client: TestClient) -> dict:
    response = client.get(
        "/bff/management/promotion-reviews",
        headers=OPERATOR_HEADERS,
        params={
            "quarter": "2026-Q1",
            "page_size": 5,
            "action_id": "promote_to_canary_candidate",
        },
    )
    assert response.status_code == 200, response.text
    items = response.json()["data"]["items"]
    assert items
    return items[0]


def _post_decision(
    client: TestClient,
    review_id: str,
    payload: dict,
    *,
    headers: dict,
    idem: str | None = None,
):
    request_headers = dict(headers)
    if idem is not None:
        request_headers["Idempotency-Key"] = idem
    return client.post(
        f"/bff/management/promotion-reviews/{review_id}/decisions",
        headers=request_headers,
        json=payload,
    )


def _submit_review(
    client: TestClient,
    review_id: str,
    *,
    headers: dict = OPERATOR_HEADERS,
    idem: str | None = None,
):
    request_headers = dict(headers)
    if idem is not None:
        request_headers["Idempotency-Key"] = idem
    return client.post(
        f"/bff/management/quarterly-ranking/recommendations/{review_id}/submit",
        headers=request_headers,
        json={"quarter": "2026-Q1"},
    )


def _legacy_promotion_submission_params(
    recommendation_id: str,
    *,
    persona_id: str,
) -> dict:
    return {
        "quarter": "2026-Q3",
        "review_id": recommendation_id,
        "promotion_review_id": recommendation_id,
        "recommendation_id": recommendation_id,
        "recommendationId": recommendation_id,
        "recommendation_action_id": "promote_to_canary_candidate",
        "recommendationActionId": "promote_to_canary_candidate",
        "persona_id": persona_id,
        "stage_from": "paper",
        "stage_to": "canary_candidate",
        "review_kind": "paper_to_canary_review",
        "requires_human_gate_decision": True,
        "live_capital_mutation": False,
        "direct_live_capital_mutation": False,
        "runtime_mutation": False,
    }


def _append_command(
    command_store: CommandStore,
    *,
    command_id: str,
    command_type: CommandType,
    target_type: ObjectType,
    target_id: str,
    params: dict,
    status: CommandStatus = CommandStatus.SUBMITTED,
) -> None:
    command_store.submit_command(
        command_id=command_id,
        command_type=command_type,
        target=TargetObject(type=target_type, id=target_id),
        submitted_at="2026-07-13T00:00:00Z",
        params=params,
        audit_context={"operator_id": "op-promo", "reason": "PPL-ALLOC-015 regression fixture"},
    )
    if status != CommandStatus.SUBMITTED:
        assert command_store.update_status(command_id, status)


def test_promotion_reviews_list_and_detail_are_readable_by_operator() -> None:
    with _isolated_client() as (client, store, command_store):
        list_response = client.get(
            "/bff/management/promotion-reviews",
            headers=OPERATOR_HEADERS,
            params={
                "quarter": "2026-Q1",
                "page_size": 5,
                "action_id": "promote_to_canary_candidate",
            },
        )
        assert list_response.status_code == 200, list_response.text
        list_body = list_response.json()
        assert list_body["meta"]["live_capital_mutation"] is False
        assert list_body["meta"]["requires_human_gate_decision"] is True
        review = list_body["data"]["items"][0]
        assert review["requires_human_gate_decision"] is True
        assert review["live_capital_mutation"] is False
        assert review["status"] == "advisory_report"
        assert review["submitted"] is False
        assert review["allowedActions"]["canSubmit"] is False
        assert review["allowedActions"]["canApprove"] is False
        assert review["promotion_path"]["from_stage"] == "paper"
        assert review["promotion_path"]["target_stage"] == "canary_candidate"
        assert "decisions" not in review["links"]
        assert "allowed_decisions" not in review

        detail_response = client.get(
            f"/bff/management/promotion-reviews/{review['review_id']}",
            headers=OPERATOR_HEADERS,
        )
        assert detail_response.status_code == 200, detail_response.text
        detail_body = detail_response.json()
        assert detail_body["data"]["review_id"] == review["review_id"]
        assert detail_body["meta"]["live_capital_mutation"] is False


def test_promotion_review_decision_route_is_retired_without_stored_command() -> None:
    with _isolated_client() as (client, store, command_store):
        review = _first_review(client)
        for headers in (OPERATOR_HEADERS, APPROVER_HEADERS, ADMIN_HEADERS):
            response = _post_decision(
                client,
                review["review_id"],
                {"decision": "approve", "rationale": "Decide through the Governance proposal."},
                headers=headers,
                idem=_idem(),
            )
            assert response.status_code == 410, response.text
            error = response.json()["error"]
            assert error["code"] == "ACTION_RETIRED"
            assert error["details"]["replacement"] == RETIRED_COMMANDS["PromotionReviewDecision"]
            assert error["details"]["replacement"] == "/bff/approvals/{decision_id}/decide"
        assert command_store._get_all_commands() == []


def test_quarterly_recommendation_submit_route_is_retired_without_stored_command() -> None:
    with _isolated_client() as (client, store, command_store):
        review = _first_review(client)
        response = _submit_review(client, review["review_id"], idem=_idem())
        assert response.status_code == 410, response.text
        error = response.json()["error"]
        assert error["code"] == "ACTION_RETIRED"
        assert error["details"]["replacement"] == RETIRED_COMMANDS["QuarterlyRankingRecommendationSubmit"]
        assert command_store._get_all_commands() == []


def test_human_inbox_has_no_promotion_review_contributor_for_command_log_rows() -> None:
    with _isolated_client() as (client, store, command_store):
        recommendation_id = "pm12-2026-q3-persona-legacy-promote_to_canary_candidate"
        _append_command(
            command_store,
            command_id="cmd-promotion-legacy",
            command_type="QuarterlyRankingRecommendationSubmit",
            target_type=ObjectType.RANKING,
            target_id=recommendation_id,
            params=_legacy_promotion_submission_params(
                recommendation_id,
                persona_id="persona-legacy",
            ),
        )

        response = client.get(
            "/bff/management/human-inbox",
            headers=OPERATOR_HEADERS,
            params={"page_size": 20},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert all(item["source_type"] != "promotion_review" for item in body["data"]["items"])
        assert "promotion_reviews" not in body["meta"]["surfaces"]


def test_human_inbox_degrades_cleanly_when_persona_readiness_blocks(monkeypatch) -> None:
    """A genuinely *blocked* (not raising) ``store.list_personas`` read is
    not individually timed -- ``_StoreTimeoutProxy`` only wraps
    ``list_governance_review_queue_items``/``list_approval_queue_items``/
    ``list_approval_records`` (``management_read_models/router.py``), and
    ``ManagementService.get_human_inbox`` has no internal timeout of its
    own for the persona-readiness section (``management_read_models/
    service.py``, "5. Persona Readiness"). So the whole synchronous
    aggregate just runs long, and once it exceeds the outer
    ``run_management_read`` wait budget the router discards the entire
    in-flight result and returns the generic degraded envelope -- confirmed
    by direct read of both modules and by independent reproduction (a
    blocked contributor across repeated sequential calls each returns this
    same degraded envelope, with the contributor call count rising once per
    call, proving each request is genuinely retried rather than cached or
    hung). This is the real, currently-guaranteed contract for a merely
    slow persona-readiness read: the route degrades cleanly (200, bounded
    wait, well-formed envelope) instead of hanging or 5xx-ing; it does not
    preserve durable items composed after persona readiness in
    ``get_human_inbox`` (see the docstring on
    ``test_human_inbox_keeps_durable_promotion_review_visible_despite_persona_readiness_timeout``
    above for why).
    """
    monkeypatch.setenv("PANTHEON_BFF_MANAGEMENT_READ_TIMEOUT_SECONDS", "0.2")
    with _isolated_client() as (client, store, command_store):

        def blocked_list_personas(*_args, **_kwargs):
            time.sleep(1.0)
            return []

        monkeypatch.setattr(store, "list_personas", blocked_list_personas)
        started_at = time.monotonic()
        inbox = client.get(
            "/bff/management/human-inbox",
            headers=OPERATOR_HEADERS,
            params={"page_size": 20},
        )
        elapsed = time.monotonic() - started_at

        assert inbox.status_code == 200, inbox.text
        assert elapsed < 1.0, "the route must return once its own wait budget elapses"
        body = inbox.json()
        assert body["meta"]["surfaces"]["human_inbox"]["status"] == "degraded"
        assert body["meta"]["surfaces"]["human_inbox"]["reason"] == "read_timeout"
        assert body["data"]["items"] == []


def test_human_inbox_surface_timeout_has_a_hard_one_second_ceiling(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_HUMAN_INBOX_SURFACE_TIMEOUT_SECONDS", "9.5")
    assert _human_inbox_surface_timeout_seconds() == 1.0

    monkeypatch.setenv("PANTHEON_BFF_HUMAN_INBOX_SURFACE_TIMEOUT_SECONDS", "0.17")
    assert _human_inbox_surface_timeout_seconds() == 0.17

    monkeypatch.setenv("PANTHEON_BFF_HUMAN_INBOX_SURFACE_TIMEOUT_SECONDS", "invalid")
    assert _human_inbox_surface_timeout_seconds() == 1.0


def test_persona_readiness_uses_two_batched_reads_without_fleet_n_plus_one(monkeypatch) -> None:
    with _isolated_client() as (client, store, command_store):
        calls = {"personas": 0, "league": 0}

        def list_personas(*_args, **_kwargs):
            calls["personas"] += 1
            return [
                {
                    "persona_id": "persona-batched-review",
                    "name": "Batched Review",
                    "lifecycle_state": "active",
                    "updated_at": "2026-07-13T12:00:00Z",
                    "metadata": {
                        "persona_status": "needs_human_approval",
                        "current_work": "Review the bounded readiness packet",
                        "research_status": {
                            "summary": "Research admission awaits review.",
                            "pending_task_ids": ["PPL-ALLOC-015"],
                            "can_deploy": False,
                        },
                        "current_research_projects": [{"project_id": "research-batched"}],
                        "data_source_status": {"state": "read_ok"},
                    },
                },
                {
                    "persona_id": "persona-no-review",
                    "name": "No Review",
                    "lifecycle_state": "active",
                    "metadata": {},
                },
            ]

        def list_persona_league(*_args, **_kwargs):
            calls["league"] += 1
            return [
                {
                    "persona_id": "persona-batched-review",
                    "governance_required": True,
                    "recommendation": "hold_for_risk_owner_review",
                    "status": "needs_human_approval",
                },
                {
                    "persona_id": "persona-no-review",
                    "governance_required": True,
                    "recommendation": "no_change",
                    "status": "active",
                },
            ]

        def forbidden_subread(*_args, **_kwargs):
            raise AssertionError("Human Inbox readiness must not enter the full Fleet N+1 chain")

        monkeypatch.setattr(store, "list_personas", list_personas)
        monkeypatch.setattr(store, "list_persona_league", list_persona_league)
        for method_name in (
            "list_bindings",
            "list_runtime_bindings",
            "list_incidents",
            "list_evolution_decisions",
            "list_strategy_specs",
        ):
            monkeypatch.setattr(store, method_name, forbidden_subread)
        # Migrated by BFF-LOOPS-PAPER-V5-PROJECTION-SEAM-CORRECTIVE-001: the
        # source-ingest truth loader is no longer a bare module function on
        # main.py; it is owned by the shared persona_service instance and
        # reads through these two read-port methods (same object as
        # store, since persona_service was constructed with it).
        monkeypatch.setattr(store, "get_source_connector_registry", forbidden_subread)
        monkeypatch.setattr(store, "get_source_health_usage_snapshot", forbidden_subread)

        response = client.get(
            "/bff/management/human-inbox",
            headers=OPERATOR_HEADERS,
            params={"source_type": "readiness_blocker"},
        )

        assert response.status_code == 200, response.text
        items = response.json()["data"]["items"]
        assert [item["persona_id"] for item in items] == ["persona-batched-review"]
        assert "PPL-ALLOC-015" in " ".join(items[0]["blocking_reasons"])
        assert items[0]["research_context"]["current_research_projects"] == [
            {"project_id": "research-batched"}
        ]
        assert calls == {"personas": 1, "league": 1}


def test_human_inbox_capacity_bound_rejects_late_submission_while_occupied(
    monkeypatch,
) -> None:
    """``run_management_read`` (``personas/routes/common.py``) accepts a
    real ``capacity``/``executor`` pair that makes a bounded read raise
    ``ManagementReadSaturated`` -- rejected before it is even submitted to
    the worker pool -- instead of queuing a second call while the first
    worker thread is still occupied (MGMT-LOAD-005). The
    ``management_read_models`` router never supplies that pair for
    ``/bff/management/human-inbox`` or ``/bff/management/cockpit`` today
    (confirmed by grep: no ``capacity=`` call site in
    ``management_read_models/router.py``); the only live ``capacity=``
    caller is the unrelated ``/bff/management/data-sources`` contributor in
    ``main.py``, whose ``"read_capacity_saturated"`` reason string
    (``ManagementService.get_human_inbox_degraded_payload`` /
    ``get_management_cockpit_degraded_payload`` in
    ``management_read_models/router.py`` both hardcode
    ``"reason": "read_timeout"`` for every exception, including
    ``ManagementReadSaturated``, confirmed by direct read) is not reachable
    from these routes without a production change, out of scope for this
    test-only migration. This test wires the same real ``run_management_read``
    function with a real bounded semaphore/executor pair the way ``main.py``
    already does for data-sources -- exercising real extracted code, not a
    fake -- and proves the genuine, currently-available guarantee: a
    concurrent submission while capacity is occupied is rejected before
    running (no late/duplicate contributor call, confirmed by call count),
    both human-inbox and cockpit degrade cleanly instead of queuing behind
    the occupied slot, and the contributor is called again -- and succeeds
    -- once released. ``/bff/management/hiq-backlog`` reads
    ``store.list_personas`` directly with no isolation wrapper at all
    (confirmed by grep), so calling it concurrently with a blocked
    contributor blocks the single test-client event loop instead of
    degrading; it is exercised separately, after release, documenting that
    confirmed, out-of-scope gap rather than faking a degraded response for
    it or hanging the suite.
    """
    monkeypatch.setenv("PANTHEON_BFF_MANAGEMENT_READ_TIMEOUT_SECONDS", "0.1")
    capacity = threading.BoundedSemaphore(1)
    executor = ThreadPoolExecutor(max_workers=2)

    def bounded_run_management_read(func, *args, **kwargs):
        return _real_run_management_read(
            func, *args, capacity=capacity, executor=executor, **kwargs
        )

    try:
        with _isolated_client(run_management_read=bounded_run_management_read) as (
            client,
            store,
            command_store,
        ):
            release_worker = threading.Event()
            worker_finished = threading.Event()
            calls = 0

            def blocked_list_personas(*_args, **_kwargs):
                nonlocal calls
                calls += 1
                release_worker.wait(timeout=10)
                worker_finished.set()
                return []

            monkeypatch.setattr(store, "list_personas", blocked_list_personas)

            first = client.get(
                "/bff/management/human-inbox",
                headers=OPERATOR_HEADERS,
                params={"source_type": "readiness_blocker"},
            )
            assert first.status_code == 200, first.text
            assert first.json()["meta"]["surfaces"]["human_inbox"]["reason"] == "read_timeout"
            assert calls == 1

            cockpit = client.get("/bff/management/cockpit", headers=OPERATOR_HEADERS)
            repeated = client.get(
                "/bff/management/human-inbox",
                headers=OPERATOR_HEADERS,
                params={"source_type": "readiness_blocker"},
            )
            assert cockpit.status_code == 200, cockpit.text
            assert repeated.status_code == 200, repeated.text
            assert calls == 1, "saturated contributors must not be submitted for late execution"
            assert (
                repeated.json()["meta"]["surfaces"]["human_inbox"]["reason"] == "read_timeout"
            )
            assert cockpit.json()["data"]["human_inbox"] == {
                "items": [],
                "summary": {},
                "meta": {},
            }

            release_worker.set()
            assert worker_finished.wait(timeout=2)
            deadline = time.monotonic() + 2
            recovered = None
            while time.monotonic() < deadline:
                recovered = client.get(
                    "/bff/management/human-inbox",
                    headers=OPERATOR_HEADERS,
                    params={"source_type": "readiness_blocker"},
                )
                surfaces = recovered.json()["meta"]["surfaces"]
                if surfaces.get("human_inbox", {}).get("reason") != "read_timeout":
                    break
                time.sleep(0.01)

            assert recovered is not None
            assert recovered.status_code == 200, recovered.text
            assert calls == 2

            hiq = client.get("/bff/management/hiq-backlog", headers=OPERATOR_HEADERS)
            assert hiq.status_code == 200, hiq.text
            assert calls == 3
    finally:
        executor.shutdown(wait=False)


def test_cockpit_composition_completes_after_slow_contributor_read() -> None:
    """``/bff/management/cockpit`` (``management_read_models.router``)
    carries the real MGMT-LOAD-005 isolation wrapper via
    ``run_management_read`` (wired in ``_build_promotion_review_app``):
    ``ManagementService.get_management_cockpit`` (which composes
    ``get_human_inbox`` and so ``store.list_personas`` inline) is offloaded
    to a worker thread and bounded by the wait budget. A slow contributor
    read within that budget (default 0.6s) still completes with the real,
    non-degraded data.
    """
    with _isolated_client() as (client, store, command_store):
        def slow_list_personas(*_args, **_kwargs):
            time.sleep(0.2)
            return []

        store.list_personas = slow_list_personas
        started_at = time.monotonic()
        cockpit = client.get("/bff/management/cockpit", headers=OPERATOR_HEADERS)
        elapsed = time.monotonic() - started_at

        assert cockpit.status_code == 200, cockpit.text
        assert elapsed >= 0.2
        assert elapsed < 1.0
        cockpit_surface = cockpit.json()["meta"]["surfaces"]["management_cockpit"]
        assert cockpit_surface["status"] in {"ok", "degraded"}
        assert cockpit_surface.get("reason") != "read_timeout", (
            "the composed cockpit surface may be 'degraded' from unrelated "
            "contributor surfaces (e.g. trading_pulse) in this test store, "
            "but must not be the isolation wrapper's timeout fallback"
        )


def test_cockpit_timeout_degrades_without_blocking_health() -> None:
    """A slow cockpit contributor read that exceeds the wait budget must
    degrade to an explicit timeout envelope, and the offload to a worker
    thread must not block a concurrent, unrelated request from completing
    promptly -- the same MGMT-LOAD-005 event-loop-responsiveness contract
    restored for ``/bff/alerts`` in ``test_mgmt_load_005_read_concurrency.py``.
    """
    with _isolated_client() as (client, store, command_store):
        # Warm up route and schema cache on isolated client before measuring concurrent responsiveness
        client.get("/health")
        client.get("/bff/management/cockpit", headers=OPERATOR_HEADERS)

        def slow_list_personas(*_args, **_kwargs):
            time.sleep(0.6)
            return []

        store.list_personas = slow_list_personas
        with patch.dict(
            os.environ,
            {
                "PANTHEON_BFF_MANAGEMENT_READ_TIMEOUT_SECONDS": "0.05",
                "PANTHEON_BFF_COCKPIT_READ_TIMEOUT_SECONDS": "0.05",
                "PANTHEON_BFF_HUMAN_INBOX_SURFACE_TIMEOUT_SECONDS": "0.05",
            },
        ):
            with ThreadPoolExecutor(max_workers=2) as pool:
                cockpit_future = pool.submit(
                    client.get, "/bff/management/cockpit", headers=OPERATOR_HEADERS
                )
                time.sleep(0.02)  # let the slow cockpit request start first
                health_started = time.monotonic()
                health = client.get("/health")
                health_elapsed = time.monotonic() - health_started
                cockpit = cockpit_future.result(timeout=3)

        assert health.status_code == 200, health.text
        assert health_elapsed < 0.55, (
            f"/health took {health_elapsed:.3f}s while a slow cockpit contributor read was in "
            "flight; the event loop must not be blocked by the offloaded synchronous read work"
        )
        assert cockpit.status_code == 200, cockpit.text
        cockpit_surface = cockpit.json()["meta"]["surfaces"]["management_cockpit"]
        assert cockpit_surface["status"] == "degraded"
        assert cockpit_surface["reason"] == "read_timeout"


def test_human_inbox_filtered_local_snapshot_empty_remains_degraded(monkeypatch) -> None:
    with _isolated_client() as (client, store, command_store):
        monkeypatch.setattr(
            store,
            "list_approval_queue_items",
            lambda **_: [
                {
                    "decision_id": "approval-local-snapshot",
                    "decision_type": "DeploymentPlan",
                    "decision_state": "pending",
                    "risk_level": "high",
                    "submitted_at": "2026-07-13T00:00:00Z",
                }
            ],
        )
        original_dataset_source = store.dataset_source

        def local_snapshot_source(dataset: str, **kwargs):
            if dataset == "approval_queue_items":
                return "local_snapshot"
            return original_dataset_source(dataset, **kwargs)

        monkeypatch.setattr(store, "dataset_source", local_snapshot_source)

        response = client.get(
            "/bff/management/human-inbox",
            headers=OPERATOR_HEADERS,
            params={"source_type": "approval", "status": "no-such-status"},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["data"]["items"] == []
        approval_surface = body["meta"]["surfaces"]["approval_queue"]
        assert approval_surface["source"] == "local_snapshot"
        assert approval_surface["status"] == "degraded"
        assert body["meta"]["surfaces"]["human_inbox"]["status"] == "degraded"


def test_hiq_backlog_remains_available_after_human_inbox_surface_extension(monkeypatch) -> None:
    with _isolated_client() as (client, store, command_store):
        monkeypatch.setattr(store, "list_governance_review_queue_items", lambda **_: [])
        monkeypatch.setattr(store, "list_approval_queue_items", lambda **_: [])
        monkeypatch.setattr(store, "list_incidents", lambda **_: [])
        monkeypatch.setattr(store, "list_personas", lambda *_args, **_kwargs: [])

        response = client.get(
            "/bff/management/hiq-backlog",
            headers=OPERATOR_HEADERS,
            params={"page_size": 10},
        )

        assert response.status_code == 200, response.text
        assert response.json()["data"]["id"] == "management-hiq-backlog"


def test_command_store_caching(tmp_path) -> None:
    """Production behavior (services/control-plane/bff/command_queue.py lines 27-34, 80-84):
    CommandStore lazily populates _cache on first read and re-reads from disk on every
    call while the file exists. When the backing file does not exist, CommandStore resets
    _cache = [] rather than serving a prior in-memory snapshot, preventing resurrection
    or overwrite of commands once the file disappears. The old test expectation that
    renaming the file would return cached items without reading disk was stale and required
    an unsafe missing-file fallback. This test asserts the safe lazy-cache contract:
    lazy initialization, in-memory cache update on submit and status update, and safe reset
    to empty when the backing file is missing.
    """
    db_file = tmp_path / "commands_test.jsonl"
    store = CommandStore(str(db_file))
    assert store._cache is None

    # First read lazily initializes cache from empty file
    cmds1 = store._get_all_commands()
    assert cmds1 == []
    assert store._cache == []

    # submit_command updates cache
    target = TargetObject(type=ObjectType.RANKING, id="rec-1")
    store.submit_command(
        command_id="cmd-1",
        command_type="QuarterlyRankingRecommendationSubmit",
        target=target,
        submitted_at="2026-07-13T12:00:00Z",
        params={},
        audit_context={},
    )
    assert len(store._cache) == 1
    assert store._cache[0]["command_id"] == "cmd-1"

    # Reading while file exists returns the command and maintains cache
    cmds2 = store._get_all_commands()
    assert len(cmds2) == 1
    assert cmds2[0]["command_id"] == "cmd-1"

    # update_status updates cache
    store.update_status("cmd-1", CommandStatus.EXECUTED)
    assert store._cache[0]["status"] == CommandStatus.EXECUTED.value

    # Safe missing-file contract: removing the file resets cache to empty
    # rather than serving a stale in-memory snapshot
    db_file.unlink()
    cmds_missing = store._get_all_commands()
    assert cmds_missing == []
    assert store._cache == []
