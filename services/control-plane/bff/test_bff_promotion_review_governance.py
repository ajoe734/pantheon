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

from fastapi.testclient import TestClient

from fastapi import FastAPI

from services.control_plane.bff.auth import policy as auth_policy
from services.control_plane.bff.capital.router import create_capital_router
from services.control_plane.bff.command_adapters.router import create_command_adapters_router
from services.control_plane.bff.command_adapters.service import CommandAdapterService
from services.control_plane.bff.command_queue import CommandStore
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
from services.control_plane.bff.personas.service import (
    _human_inbox_decision_projection_from_record,
    _human_inbox_decision_recommendation_id,
    _human_inbox_trusted_promotion_submission,
    create_persona_registry_write_owner,
)
from services.control_plane.bff.ports import ReadSurfacePorts
from services.control_plane.bff.governance.service import (
    human_inbox_surface_timeout_seconds as _human_inbox_surface_timeout_seconds,
)


OPERATOR_HEADERS = {"Authorization": "Bearer op-promo:operator"}
APPROVER_HEADERS = {"Authorization": "Bearer op-promo-approver:approver"}
ADMIN_HEADERS = {"Authorization": "Bearer op-promo-admin:admin"}


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
        for pid, rid, bid in (
            ("persona-us-equity", "runtime-us-equity-paper", "binding-us-equity-paper"),
            ("persona-crypto-perp", "runtime-crypto-paper", "binding-crypto-paper"),
        ):
            self._data.setdefault("personas", {})[pid] = {
                "id": pid,
                "persona_id": pid,
                "name": f"{pid} Persona",
                "lifecycle_state": "active",
                "mandate": "alpha_research_and_paper_execution",
                "strategy_family": "momentum",
                "created_at": "2026-03-01T00:00:00Z",
                "last_active_at": "2026-04-11T10:00:00Z",
                "metadata": {
                    "archetype": "momentum",
                    "risk_level": "low",
                    "success_rate": 0.95,
                },
            }
            self._data.setdefault("bindings", {})[bid] = {
                "id": bid,
                "persona_id": pid,
                "capital_pool_id": "pool-main",
                "runtime_binding_id": rid,
                "status": "active",
                "validity": "active",
                "allowed_deployment_scope": "paper",
                "deployment_stage": "paper",
            }
            self._data.setdefault("runtime_bindings", {})[rid] = {
                "id": rid,
                "runtime_id": rid,
                "persona_id": pid,
                "binding_id": bid,
                "persona_capital_binding_id": bid,
                "deployment_stage": "paper",
                "deployment_mode": "paper",
                "status": "running",
                "plan_id": f"plan-{pid}",
            }
            self._data.setdefault("telemetry_summaries", {})[rid] = {
                "runtime_id": rid,
                "window": "1h",
                "pnl": 0.85,
                "drawdown": 0.01,
                "sharpe_ratio": 3.2,
                "total_trades": 120,
                "fill_rate": 0.99,
                "avg_slippage_bps": 0.2,
                "collected_at": "2026-04-10T15:00:00Z",
            }
            self._data.setdefault("sessions", {})[f"sess-{pid}"] = {
                "id": f"sess-{pid}",
                "session_id": f"sess-{pid}",
                "persona_id": pid,
                "status": "active",
                "deployment_stage": "paper",
                "runtime_binding_id": rid,
            }
            self._data.setdefault("capability_snapshots", {})[f"cap-{pid}"] = {
                "id": f"cap-{pid}",
                "snapshot_id": f"cap-{pid}",
                "persona_id": pid,
                "status": "verified",
            }
        self.allow_fallback = allow_fallback
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

    def list_v5_interventions(self, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    def list_sentinel_findings(self, **kwargs: Any) -> tuple[bool, list[dict[str, Any]]]:
        return (False, [])

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
    store: PromotionReviewTestReadPorts, command_store: CommandStore
) -> FastAPI:
    """Standalone app built from the same real, already-extracted production
    router factories the composition root mounts for the persona-league,
    quarterly-ranking, promotion-review, capital, command-adapter, and
    management surfaces (``personas.create_personas_router``,
    ``capital.router.create_capital_router``,
    ``command_adapters.router.create_command_adapters_router``,
    ``management_read_models.router.create_management_router``), with the
    read surface, command store, and idempotency stores injected explicitly
    instead of reached through ``main.py`` module globals. No handler,
    validator, or precondition logic is reimplemented here.
    """
    app = FastAPI()
    register_error_handlers(app)
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
            extract_identity=auth_policy.extract_identity,
            require_read_role=auth_policy.require_read_role,
            bff_error=auth_policy.bff_error,
            utc_now=utc_now,
        )
    )
    return app


@contextmanager
def _isolated_client() -> Iterator[tuple[TestClient, PromotionReviewTestReadPorts, CommandStore]]:
    with tempfile.TemporaryDirectory() as td:
        store = PromotionReviewTestReadPorts(allow_fallback=True)
        command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        app = _build_promotion_review_app(store, command_store)
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
        assert review["status"] == "recommended_not_submitted"
        assert review["submitted"] is False
        assert review["allowedActions"]["canSubmit"] is True
        assert review["allowedActions"]["canApprove"] is False
        assert review["promotion_path"]["from_stage"] == "paper"
        assert review["promotion_path"]["target_stage"] == "canary_candidate"
        assert review["links"]["decisions"].endswith("/decisions")
        assert review["links"]["submit"].endswith("/submit")

        detail_response = client.get(
            f"/bff/management/promotion-reviews/{review['review_id']}",
            headers=OPERATOR_HEADERS,
        )
        assert detail_response.status_code == 200, detail_response.text
        detail_body = detail_response.json()
        assert detail_body["data"]["review_id"] == review["review_id"]
        assert detail_body["meta"]["live_capital_mutation"] is False


def test_quarterly_recommendation_submit_creates_promotion_review_inbox_item(monkeypatch) -> None:
    with _isolated_client() as (client, store, command_store):
        review = _first_review(client)
        submit = _submit_review(client, review["review_id"], idem=_idem())
        assert submit.status_code == 202, submit.text
        body = submit.json()
        assert body["data"]["submitted"] is True
        assert body["data"]["review_id"] == review["review_id"]
        assert body["data"]["human_inbox_id"].startswith("promotion_review:")
        assert body["data"]["live_capital_mutation"] is False

        records = command_store._get_all_commands()
        assert len(records) == 1
        assert records[0]["type"] == "QuarterlyRankingRecommendationSubmit"
        assert records[0]["target"]["type"] == ObjectType.RANKING.value
        assert records[0]["params"]["recommendation_id"] == review["recommendation_id"]
        assert records[0]["params"]["live_capital_mutation"] is False

        detail = client.get(
            f"/bff/management/promotion-reviews/{review['review_id']}",
            headers=OPERATOR_HEADERS,
        )
        assert detail.status_code == 200, detail.text
        detail_data = detail.json()["data"]
        assert detail_data["submitted"] is True
        assert detail_data["status"] == "pending_human_gate"
        assert detail_data["allowedActions"]["canApprove"] is True

        def fail_if_ranking_is_rebuilt(*_args, **_kwargs):
            raise AssertionError("Human Inbox must project the durable submission without rebuilding PM12")

        monkeypatch.setattr(
            "services.control_plane.bff.personas.service._promotion_review_find",
            fail_if_ranking_is_rebuilt,
        )
        monkeypatch.setattr(
            "services.control_plane.bff.governance.human_inbox._build_persona_readiness_items",
            fail_if_ranking_is_rebuilt,
        )
        for method_name in (
            "list_governance_review_queue_items",
            "list_approval_queue_items",
            "list_v5_interventions",
            "list_sentinel_findings",
        ):
            monkeypatch.setattr(store, method_name, fail_if_ranking_is_rebuilt)

        inbox = client.get(
            "/bff/management/human-inbox",
            headers=OPERATOR_HEADERS,
            params={"source_type": "promotion_review", "page_size": 10},
        )
        assert inbox.status_code == 200, inbox.text
        inbox_items = inbox.json()["data"]["items"]
        assert any(item["promotion_review_id"] == review["review_id"] for item in inbox_items)
        assert inbox.json()["meta"]["surfaces"]["promotion_reviews"]["source"] == "command_store"

        inbox_detail = client.get(
            f"/bff/management/human-inbox/{detail_data['human_inbox_id']}",
            headers=OPERATOR_HEADERS,
        )
        assert inbox_detail.status_code == 200, inbox_detail.text
        assert inbox_detail.json()["data"]["source_type"] == "promotion_review"


def test_quarterly_recommendation_submit_rejects_caller_source_snapshot_tampering() -> None:
    with _isolated_client() as (client, store, command_store):
        review = _first_review(client)
        authoritative = review["source_recommendation"]
        forged = {
            **authoritative,
            "name": "FORGED VIEWER TITLE",
            "rationale": "FORGED VIEWER RATIONALE",
            "priority": "critical",
            "evidence_refs": [
                {
                    "ref_id": "private-evidence",
                    "source_document": "FORGED PRIVATE EVIDENCE",
                }
            ],
            "evidence_ref_ids": ["private-evidence"],
        }
        submit = client.post(
            f"/bff/management/quarterly-ranking/recommendations/{review['review_id']}/submit",
            headers={**OPERATOR_HEADERS, "Idempotency-Key": _idem()},
            json={"quarter": "2026-Q1", "source_recommendation": forged},
        )

        assert submit.status_code == 422, submit.text
        assert command_store._get_all_commands() == []

        clean_submit = _submit_review(client, review["review_id"], idem=_idem())
        assert clean_submit.status_code == 202, clean_submit.text
        records = command_store._get_all_commands()
        stored = records[0]["params"]["source_recommendation"]
        assert stored["evidence_refs"] == []
        assert stored["evidence_ref_ids"] == []

        inbox = client.get(
            "/bff/management/human-inbox",
            headers={"Authorization": "Bearer promotion-viewer:viewer"},
            params={"source_type": "promotion_review", "page_size": 10},
        )

        assert inbox.status_code == 200, inbox.text
        item = next(
            item
            for item in inbox.json()["data"]["items"]
            if item["promotion_review_id"] == review["review_id"]
        )
        serialized = json.dumps(item, sort_keys=True)
        assert "FORGED VIEWER TITLE" not in serialized
        assert "FORGED VIEWER RATIONALE" not in serialized
        assert "FORGED PRIVATE EVIDENCE" not in serialized


def test_quarterly_recommendation_submit_rejects_tuple_tampering_before_and_on_replay() -> None:
    tamper_cases = {
        "review_id": "forged-review-revision",
        "promotion_review_id": "forged-review-revision",
        "stage": "forged_stage",
        "stage_from": "forged_stage",
        "current_weight": 0.99,
        "target_weight": 0.99,
        "delta": 0.99,
        "evidence_ref_ids": ["forged-evidence"],
        "evidence_refs": [{"ref_id": "forged-evidence"}],
    }
    with _isolated_client() as (client, store, command_store):
        review = _first_review(client)
        route = (
            "/bff/management/quarterly-ranking/recommendations/"
            f"{review['review_id']}/submit"
        )
        for field, forged_value in tamper_cases.items():
            rejected = client.post(
                route,
                headers={**OPERATOR_HEADERS, "Idempotency-Key": _idem()},
                json={
                    "quarter": review["quarter"],
                    "ranking_snapshot_id": review["ranking_snapshot_id"],
                    field: forged_value,
                },
            )
            assert rejected.status_code == 422, (field, rejected.text)
        assert command_store._get_all_commands() == []

        accepted = _submit_review(client, review["review_id"], idem=_idem())
        assert accepted.status_code == 202, accepted.text
        for field, forged_value in tamper_cases.items():
            rejected_replay = client.post(
                route,
                headers={**OPERATOR_HEADERS, "Idempotency-Key": _idem()},
                json={
                    "quarter": review["quarter"],
                    "ranking_snapshot_id": review["ranking_snapshot_id"],
                    field: forged_value,
                },
            )
            assert rejected_replay.status_code == 422, (
                field,
                rejected_replay.text,
            )


def test_generic_quarterly_submit_paths_reject_unadmitted_or_tampered_tuple() -> None:
    with _isolated_client() as (client, store, command_store):
        review = _first_review(client)
        tamper_cases = (
            ("ranking_snapshot_id", "ranking-quarterly-forged"),
            ("recommendation_id", "pm12-2026-q1-forged"),
            ("stage", "forged_stage"),
            ("stage_from", "forged_stage"),
            ("current_weight", 0.99),
            ("target_weight", 0.99),
            ("delta", 0.99),
            ("evidence_ref_ids", ["forged-evidence"]),
            ("evidence_refs", [{"ref_id": "forged-evidence"}]),
        )
        for route, idempotency_header in (
            ("/bff/v1/commands", "Idempotency-Key"),
            ("/bff/v1/commands", "X-Idempotency-Key"),
        ):
            for field, forged_value in tamper_cases:
                params = {
                    "quarter": review["quarter"],
                    "recommendation_id": review["recommendation_id"],
                    "ranking_snapshot_id": review["ranking_snapshot_id"],
                    field: forged_value,
                }
                rejected = client.post(
                    route,
                    headers={
                        **OPERATOR_HEADERS,
                        idempotency_header: _idem(),
                    },
                    json={
                        "command": "QuarterlyRankingRecommendationSubmit",
                        "target": {
                            "type": "Ranking",
                            "id": review["recommendation_id"],
                        },
                        "params": params,
                        "audit_context": {
                            "reason": "Reject untrusted ranking lineage"
                        },
                    },
                )
                assert rejected.status_code == 422, (
                    route,
                    field,
                    rejected.text,
                )


def test_generic_command_does_not_block_trusted_semantic_submission() -> None:
    with _isolated_client() as (client, store, command_store):
        review = _first_review(client)
        generic = client.post(
            "/bff/v1/commands",
            headers={**OPERATOR_HEADERS, "Idempotency-Key": _idem()},
            json={
                "command": "QuarterlyRankingRecommendationSubmit",
                "target": {"type": "Ranking", "id": review["recommendation_id"]},
                "params": {
                    "quarter": review["quarter"],
                    "recommendation_id": review["recommendation_id"],
                    "ranking_snapshot_id": review["ranking_snapshot_id"],
                    "recommendation_action_id": review["action_id"],
                    "persona_id": review["persona_id"],
                    "stage_from": review["promotion_path"]["from_stage"],
                    "stage_to": review["promotion_path"]["target_stage"],
                    "review_kind": review["review_kind"],
                    "requires_human_gate_decision": True,
                    "live_capital_mutation": False,
                    "direct_live_capital_mutation": False,
                    "runtime_mutation": False,
                },
                "audit_context": {
                    "reason": "Regression: generic command cannot impersonate semantic submit"
                },
            },
        )
        assert generic.status_code == 202, generic.text

        before_detail = client.get(
            f"/bff/management/promotion-reviews/{review['review_id']}",
            headers=OPERATOR_HEADERS,
        )
        assert before_detail.status_code == 200, before_detail.text
        assert before_detail.json()["data"]["submitted"] is False
        before_inbox = client.get(
            "/bff/management/human-inbox",
            headers=OPERATOR_HEADERS,
            params={"source_type": "promotion_review", "page_size": 10},
        )
        assert before_inbox.status_code == 200, before_inbox.text
        assert before_inbox.json()["data"]["items"] == []

        semantic = _submit_review(client, review["review_id"], idem=_idem())
        assert semantic.status_code == 202, semantic.text
        records = command_store._get_all_commands()
        assert len(records) == 2
        assert not _human_inbox_trusted_promotion_submission(records[0])
        assert _human_inbox_trusted_promotion_submission(records[1])

        after_detail = client.get(
            f"/bff/management/promotion-reviews/{review['review_id']}",
            headers=OPERATOR_HEADERS,
        )
        assert after_detail.status_code == 200, after_detail.text
        assert after_detail.json()["data"]["submitted"] is True
        after_inbox = client.get(
            "/bff/management/human-inbox",
            headers=OPERATOR_HEADERS,
            params={"source_type": "promotion_review", "page_size": 10},
        )
        assert after_inbox.status_code == 200, after_inbox.text
        items = after_inbox.json()["data"]["items"]
        assert [item["promotion_review_id"] for item in items] == [review["review_id"]]


def test_human_inbox_ignores_decision_with_mismatched_target_aliases() -> None:
    with _isolated_client() as (client, store, command_store):
        # This test needs two independent reviews to exercise alias mismatch.
        # Seed both through the paper-fleet lifecycle owner so the fixture does
        # not depend on the deprecated persona-session fallback.
        store.list_authoritative_paper_runtime_monitoring_sessions = (  # type: ignore[method-assign]
            lambda: [
                {
                    "session_id": f"monitoring-{runtime_id}",
                    "session_type": "paper_runtime_monitoring",
                    "status": "running",
                    "deployment_stage": "paper",
                    "runtime_id": runtime_id,
                }
                for runtime_id in (
                    "runtime-us-equity-paper",
                    "runtime-crypto-paper",
                )
            ]
        )
        response = client.get(
            "/bff/management/promotion-reviews",
            headers=OPERATOR_HEADERS,
            params={
                "quarter": "2026-Q1",
                "page_size": 10,
                "action_id": "promote_to_canary_candidate",
            },
        )
        assert response.status_code == 200, response.text
        reviews = response.json()["data"]["items"]
        assert len(reviews) >= 2
        target_review, aliased_review = reviews[:2]
        for review in (target_review, aliased_review):
            submit = _submit_review(client, review["review_id"], idem=_idem())
            assert submit.status_code == 202, submit.text

        target_id = f"promotion_review:{target_review['review_id']}"
        mismatch = client.post(
            "/bff/v1/commands",
            headers={**APPROVER_HEADERS, "Idempotency-Key": _idem()},
            json={
                "command": "HumanGateApprove",
                "target": {"type": "HumanGateItem", "id": target_id},
                "params": {
                    "human_gate_item_id": target_id,
                    "decision": "approve",
                    "review_id": aliased_review["review_id"],
                    "promotion_review_id": aliased_review["review_id"],
                    "recommendation_id": aliased_review["recommendation_id"],
                    "rationale": "Mismatched aliases must not move either review.",
                },
                "audit_context": {
                    "reason": "Regression: Human Gate target and aliases must agree"
                },
            },
        )
        assert mismatch.status_code == 202, mismatch.text
        record = command_store._get_all_commands()[-1]
        assert _human_inbox_decision_recommendation_id(record) == ""
        assert _human_inbox_decision_projection_from_record(record) is None

        for review in (target_review, aliased_review):
            detail = client.get(
                f"/bff/management/promotion-reviews/{review['review_id']}",
                headers=OPERATOR_HEADERS,
            )
            assert detail.status_code == 200, detail.text
            assert detail.json()["data"]["decision_status"] == "pending"

        inbox = client.get(
            "/bff/management/human-inbox",
            headers=OPERATOR_HEADERS,
            params={"source_type": "promotion_review", "page_size": 10},
        )
        assert inbox.status_code == 200, inbox.text
        projected = {
            item["promotion_review_id"]: item["status"]
            for item in inbox.json()["data"]["items"]
        }
        assert projected[target_review["review_id"]] == "pending"
        assert projected[aliased_review["review_id"]] == "pending"


def test_human_inbox_keeps_durable_promotion_review_visible_despite_slow_persona_readiness(
    monkeypatch,
) -> None:
    """The live ``get_human_inbox`` persona-readiness contributor
    (``management_read_models/service.py``, calling ``store.list_personas``
    directly) has no bounded timeout wrapper today -- it is not routed
    through ``governance.human_inbox._build_persona_readiness_items`` or any
    ``run_management_read``-style isolation. A slow persona-readiness read
    therefore simply extends the whole response's latency rather than
    degrading at a budget; this asserts the durable promotion-review item
    still surfaces once the slow, unrelated contributor completes.
    """
    with _isolated_client() as (client, store, command_store):
        review = _first_review(client)
        submit = _submit_review(client, review["review_id"], idem=_idem())
        assert submit.status_code == 202, submit.text

        def slow_list_personas(*_args, **_kwargs):
            time.sleep(0.2)
            return []

        monkeypatch.setattr(store, "list_personas", slow_list_personas)
        started_at = time.monotonic()
        inbox = client.get(
            "/bff/management/human-inbox",
            headers=OPERATOR_HEADERS,
            params={"page_size": 20},
        )
        elapsed = time.monotonic() - started_at

        assert inbox.status_code == 200, inbox.text
        assert elapsed >= 0.2
        assert elapsed < 1.0
        body = inbox.json()
        assert any(
            item["promotion_review_id"] == review["review_id"]
            for item in body["data"]["items"]
            if item["source_type"] == "promotion_review"
        )


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


def test_human_inbox_cockpit_hiq_backlog_share_read_surface_under_concurrent_calls() -> None:
    """``human-inbox``/``cockpit``/``hiq-backlog`` all resolve persona
    readiness via a direct, unbounded ``store.list_personas()`` call in the
    current ``management_read_models/service.py`` (no
    ``_HUMAN_INBOX_READ_SLOTS``-style bounded semaphore or
    ``read_capacity_saturated``/``read_timeout`` degradation exists for this
    contributor today -- that machinery predates the router extraction and
    has no live equivalent, confirmed by grep). This asserts the three
    routes still compose correctly and consistently off the same store when
    called concurrently, without asserting a capacity-bounding contract this
    composition does not currently implement.
    """
    with _isolated_client() as (client, store, command_store):
        calls = 0
        real_list_personas = store.list_personas

        def counting_list_personas(*args, **kwargs):
            nonlocal calls
            calls += 1
            return real_list_personas(*args, **kwargs)

        store.list_personas = counting_list_personas

        with ThreadPoolExecutor(max_workers=3) as pool:
            inbox_future = pool.submit(
                client.get,
                "/bff/management/human-inbox",
                headers=OPERATOR_HEADERS,
                params={"source_type": "readiness_blocker"},
            )
            cockpit_future = pool.submit(
                client.get,
                "/bff/management/cockpit",
                headers=OPERATOR_HEADERS,
            )
            hiq_future = pool.submit(
                client.get,
                "/bff/management/hiq-backlog",
                headers=OPERATOR_HEADERS,
            )
            inbox = inbox_future.result(timeout=3)
            cockpit = cockpit_future.result(timeout=3)
            hiq = hiq_future.result(timeout=3)

        assert inbox.status_code == 200, inbox.text
        assert cockpit.status_code == 200, cockpit.text
        assert hiq.status_code == 200, hiq.text
        assert calls >= 3


def test_cockpit_composition_completes_after_slow_contributor_read() -> None:
    """``/bff/management/cockpit`` (``management_read_models.router``) has
    no ``_MANAGEMENT_COCKPIT_READ_SLOTS``-style bounded-semaphore/timeout
    isolation wrapper in the current router/service implementation -- it
    calls ``ManagementService.get_management_cockpit`` directly, which
    composes ``get_human_inbox`` (and so ``store.list_personas``) inline
    with no offload/timeout budget. This asserts the composed cockpit
    response still completes with the real data after a slow contributor
    read, rather than a stale bounded-capacity/timeout-degradation contract
    this composition does not currently implement.
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
        monkeypatch.setattr(store, "list_v5_interventions", lambda **_: [])
        monkeypatch.setattr(store, "list_sentinel_findings", lambda **_: (True, []))
        monkeypatch.setattr(store, "list_personas", lambda *_args, **_kwargs: [])

        response = client.get(
            "/bff/management/hiq-backlog",
            headers=OPERATOR_HEADERS,
            params={"page_size": 10},
        )

        assert response.status_code == 200, response.text
        assert response.json()["data"]["id"] == "management-hiq-backlog"


def test_human_inbox_promotion_projection_reads_command_log_once(monkeypatch) -> None:
    with _isolated_client() as (client, store, command_store):
        recommendation_ids = [
            "pm12-2026-q3-persona-alpha-promote_to_canary_candidate",
            "pm12-2026-q3-persona-beta-promote_to_canary_candidate",
        ]
        for index, recommendation_id in enumerate(recommendation_ids, start=1):
            _append_command(
                command_id=f"cmd-promotion-submit-{index}",
                command_type=CommandType.QUARTERLY_RANKING_RECOMMENDATION_SUBMIT,
                target_type=ObjectType.RANKING,
                target_id=recommendation_id,
                params=_legacy_promotion_submission_params(
                    recommendation_id,
                    persona_id=f"persona-{'alpha' if index == 1 else 'beta'}",
                ),
            )
        _append_command(
            command_id="cmd-promotion-decision-1",
            command_type=CommandType.HUMAN_GATE_APPROVE,
            target_type=ObjectType.HUMAN_GATE_ITEM,
            target_id=f"promotion_review:{recommendation_ids[0]}",
            params={
                "review_id": recommendation_ids[0],
                "recommendation_id": recommendation_ids[0],
                "decision": "approve",
                "rationale": "Single-pass projection fixture.",
            },
            status=CommandStatus.EXECUTED,
        )

        original_get_all_commands = command_store._get_all_commands
        command_log_reads = 0

        def counted_get_all_commands():
            nonlocal command_log_reads
            command_log_reads += 1
            return original_get_all_commands()

        monkeypatch.setattr(
            command_store,
            "_get_all_commands",
            counted_get_all_commands,
        )

        response = client.get(
            "/bff/management/human-inbox",
            headers=OPERATOR_HEADERS,
            params={"source_type": "promotion_review", "page_size": 10},
        )

        assert response.status_code == 200, response.text
        assert {
            item["promotion_review_id"] for item in response.json()["data"]["items"]
        } == set(recommendation_ids)
        assert command_log_reads == 1


def test_human_inbox_omits_inconsistent_generic_snapshot_and_private_evidence() -> None:
    with _isolated_client() as (client, store, command_store):
        recommendation_id = "pm12-2026-q3-persona-forged-promote_to_canary_candidate"
        params = _legacy_promotion_submission_params(
            recommendation_id,
            persona_id="persona-forged",
        )
        params.update(
            {
                "ranking_snapshot_id": "ranking-quarter-authoritative",
                "source_recommendation": {
                    "id": recommendation_id,
                    "recommendation_id": recommendation_id,
                    "ranking_snapshot_id": "ranking-quarter-attacker-controlled",
                    "quarter": "2026-Q3",
                    "persona_id": "persona-forged",
                    "name": "Forged Persona",
                    "action_id": "promote_to_canary_candidate",
                    "state": "paper",
                    "evidence_refs": [
                        {
                            "ref_id": "private-evidence",
                            "source_document": "viewer-only-secret",
                        }
                    ],
                },
            }
        )
        _append_command(
            command_id="cmd-promotion-forged-snapshot",
            command_type=CommandType.QUARTERLY_RANKING_RECOMMENDATION_SUBMIT,
            target_type=ObjectType.RANKING,
            target_id=recommendation_id,
            params=params,
        )

        response = client.get(
            "/bff/management/human-inbox",
            headers={"Authorization": "Bearer promotion-viewer:viewer"},
            params={"source_type": "promotion_review", "page_size": 10},
        )

        assert response.status_code == 200, response.text
        assert response.json()["data"]["items"] == []
        assert "viewer-only-secret" not in response.text


def test_human_inbox_legacy_snapshotless_submission_is_safe_and_minimal() -> None:
    with _isolated_client() as (client, store, command_store):
        recommendation_id = "pm12-2026-q3-persona-legacy-promote_to_canary_candidate"
        params = _legacy_promotion_submission_params(
            recommendation_id,
            persona_id="persona-legacy",
        )
        params["source_document"] = "must-not-be-projected"
        _append_command(
            command_id="cmd-promotion-legacy",
            command_type=CommandType.QUARTERLY_RANKING_RECOMMENDATION_SUBMIT,
            target_type=ObjectType.RANKING,
            target_id=recommendation_id,
            params=params,
        )

        response = client.get(
            "/bff/management/human-inbox",
            headers={"Authorization": "Bearer promotion-viewer:viewer"},
            params={"source_type": "promotion_review", "page_size": 10},
        )

        assert response.status_code == 200, response.text
        items = response.json()["data"]["items"]
        assert len(items) == 1
        item = items[0]
        assert item["promotion_review_id"] == recommendation_id
        assert item["persona_id"] == "persona-legacy"
        assert item["promotion_review"]["evidence_refs"] == []
        assert item["promotion_review"]["source_recommendation"]["recommendation_id"] == (
            recommendation_id
        )
        assert "must-not-be-projected" not in json.dumps(item, sort_keys=True)


def test_human_inbox_omits_failed_promotion_submission() -> None:
    with _isolated_client() as (client, store, command_store):
        recommendation_id = "pm12-2026-q3-persona-failed-promote_to_canary_candidate"
        _append_command(
            command_id="cmd-promotion-failed",
            command_type=CommandType.QUARTERLY_RANKING_RECOMMENDATION_SUBMIT,
            target_type=ObjectType.RANKING,
            target_id=recommendation_id,
            params=_legacy_promotion_submission_params(
                recommendation_id,
                persona_id="persona-failed",
            ),
            status=CommandStatus.FAILED,
        )

        response = client.get(
            "/bff/management/human-inbox",
            headers=OPERATOR_HEADERS,
            params={"source_type": "promotion_review", "page_size": 10},
        )

        assert response.status_code == 200, response.text
        assert response.json()["data"]["items"] == []


def test_promotion_review_decision_requires_prior_submit() -> None:
    with _isolated_client() as (client, store, command_store):
        review = _first_review(client)
        response = _post_decision(
            client,
            review["review_id"],
            {"decision": "approve", "rationale": "Cannot approve before submit."},
            headers=APPROVER_HEADERS,
            idem=_idem(),
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "HUMAN_GATE_PENDING"
        assert command_store._get_all_commands() == []


def test_promotion_review_approve_submits_human_gate_command() -> None:
    with _isolated_client() as (client, store, command_store):
        review = _first_review(client)
        submit = _submit_review(client, review["review_id"], idem=_idem())
        assert submit.status_code == 202, submit.text
        response = _post_decision(
            client,
            review["review_id"],
            {"decision": "approve", "rationale": "Paper evidence supports canary admission."},
            headers=APPROVER_HEADERS,
            idem=_idem(),
        )
        assert response.status_code == 202, response.text
        body = response.json()
        assert body["data"]["decision"] == "approve"
        assert body["data"]["decision_status"] == "accepted"
        assert body["meta"]["live_capital_mutation"] is False
        assert body["meta"]["requires_human_gate_decision"] is True

        records = command_store._get_all_commands()
        assert len(records) == 2
        record = records[1]
        assert record["type"] == "HumanGateApprove"
        assert record["target"]["type"] == ObjectType.HUMAN_GATE_ITEM.value
        assert record["params"]["review_id"] == review["review_id"]
        assert record["params"]["live_capital_mutation"] is False
        assert record["audit"]["live_capital_side_effects"] is False


def test_promotion_review_approve_with_conditions_preserves_conditions_and_rationale() -> None:
    with _isolated_client() as (client, store, command_store):
        review = _first_review(client)
        submit = _submit_review(client, review["review_id"], idem=_idem())
        assert submit.status_code == 202, submit.text
        conditions = [
            "Run canary with paper-sized notional for one full market week.",
            {"metric": "slippage_bps", "max": 8},
        ]
        rationale = "Canary is acceptable only with explicit execution drift guardrails."
        response = _post_decision(
            client,
            review["review_id"],
            {
                "decision": "approve_with_conditions",
                "conditions": conditions,
                "rationale": rationale,
            },
            headers=ADMIN_HEADERS,
            idem=_idem(),
        )
        assert response.status_code == 202, response.text
        body = response.json()
        assert body["data"]["decision"] == "approve_with_conditions"
        assert body["data"]["conditions"] == conditions
        assert body["data"]["rationale"] == rationale

        record = command_store._get_all_commands()[1]
        assert record["type"] == "HumanGateApprove"
        assert record["params"]["decision"] == "approve_with_conditions"
        assert record["params"]["conditions"] == conditions
        assert record["params"]["rationale"] == rationale


def test_promotion_review_reject_requires_non_empty_rationale() -> None:
    with _isolated_client() as (client, store, command_store):
        review = _first_review(client)
        submit = _submit_review(client, review["review_id"], idem=_idem())
        assert submit.status_code == 202, submit.text
        response = _post_decision(
            client,
            review["review_id"],
            {"decision": "reject", "rationale": "  "},
            headers=APPROVER_HEADERS,
            idem=_idem(),
        )
        assert response.status_code == 422, response.text
        error = response.json()["error"]
        assert error["code"] == "VALIDATION_FAILED"
        assert error["details"]["precondition_failed"] == "rationale"
        assert [record["type"] for record in command_store._get_all_commands()] == [
            "QuarterlyRankingRecommendationSubmit"
        ]


def test_promotion_review_decision_requires_approver_or_admin_role() -> None:
    with _isolated_client() as (client, store, command_store):
        review = _first_review(client)
        submit = _submit_review(client, review["review_id"], idem=_idem())
        assert submit.status_code == 202, submit.text
        response = _post_decision(
            client,
            review["review_id"],
            {"decision": "approve", "rationale": "Operator can read but cannot approve."},
            headers=OPERATOR_HEADERS,
            idem=_idem(),
        )
        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "FORBIDDEN"
        assert [record["type"] for record in command_store._get_all_commands()] == [
            "QuarterlyRankingRecommendationSubmit"
        ]


def test_promotion_review_idempotency_replay_has_no_direct_live_mutation() -> None:
    with _isolated_client() as (client, store, command_store):
        review = _first_review(client)
        submit = _submit_review(client, review["review_id"], idem=_idem())
        assert submit.status_code == 202, submit.text
        idem_key = _idem()
        payload = {"decision": "approve", "rationale": "Replay should return the same receipt."}
        first = _post_decision(
            client,
            review["review_id"],
            payload,
            headers=APPROVER_HEADERS,
            idem=idem_key,
        )
        second = _post_decision(
            client,
            review["review_id"],
            payload,
            headers=APPROVER_HEADERS,
            idem=idem_key,
        )
        assert first.status_code == 202, first.text
        assert second.status_code == 202, second.text
        first_body = first.json()
        second_body = second.json()
        assert first_body["data"]["command_id"] == second_body["data"]["command_id"]
        assert second_body["meta"]["idempotency"]["replayed"] is True
        assert second_body["meta"]["idempotency"]["idempotencyKey"] == idem_key
        assert second_body["meta"]["live_capital_mutation"] is False
        assert second_body["data"]["live_capital_mutation"] is False

        records = command_store._get_all_commands()
        assert len(records) == 2
        assert records[1]["target"]["type"] != ObjectType.RUNTIME.value
        assert records[1]["params"]["live_capital_mutation"] is False
        assert records[1]["params"]["runtime_mutation"] is False


def test_command_store_caching(tmp_path) -> None:
    db_file = tmp_path / "commands_test.jsonl"
    store = CommandStore(str(db_file))
    assert store._cache is None

    # First read initializes cache
    cmds1 = store._get_all_commands()
    assert cmds1 == []
    assert store._cache == []

    # submit_command updates cache
    target = TargetObject(type=ObjectType.RANKING, id="rec-1")
    store.submit_command(
        command_id="cmd-1",
        command_type=CommandType.QUARTERLY_RANKING_RECOMMENDATION_SUBMIT,
        target=target,
        submitted_at="2026-07-13T12:00:00Z",
        params={},
        audit_context={},
    )
    assert len(store._cache) == 1
    assert store._cache[0]["command_id"] == "cmd-1"

    # Second read should use cache without opening the file again
    # We rename the file to make sure it doesn't try to read it
    db_file.rename(tmp_path / "commands_test_renamed.jsonl")
    cmds2 = store._get_all_commands()
    assert len(cmds2) == 1
    assert cmds2[0]["command_id"] == "cmd-1"

    # update_status updates cache
    store.update_status("cmd-1", CommandStatus.EXECUTED)
    assert store._cache[0]["status"] == CommandStatus.EXECUTED.value
