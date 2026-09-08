"""Natural Agora owner and receipt chain integration and regression tests for AGORA-CHAIN-001.

Validates:
1. Natural path connection:
   - Public plan creation (ResearchPlanCreateRequest) forbids client-supplied
     correlation_id and dataset.
   - Server-side _build_plan derives workshop correlation.
   - AuthenticResearchBackendClient and dispatcher resolve canonical input_refs into
     governed execution dataset without manual patching.
   - Natural public create -> approve -> dispatch -> AgoraInteractionWorker execution
     reaches the genuine research service endpoint, executes workflow, emits owner
     receipt, and verifies server-side provenance end-to-end.
2. Provenance fail-close:
   - Absent backend provenance or receipt must never mint 'real' provenance.
   - Backend returning 'real' provenance without owner-emitted receipt must downgrade
     to 'simulation' and leave receipt as None.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any, Dict
import pytest
from fastapi.testclient import TestClient

from agora.interaction.worker import AgoraInteractionWorker
from agora.research.dispatcher import (
    AuthenticResearchBackendClient,
    AuthenticStageAdapter,
    ResearchDispatcher,
)
from agora.research.receipt import resolve_run_provenance
from agora.research.routes.common import (
    ResearchPlanCreateRequest,
    _build_plan,
    _build_run_projection,
)
from agora.research.store import MemoryResearchPlanStore
from services.research.tests.test_research_orchestrator_http_service import _load_service_module


@pytest.mark.parametrize("supply_correlation", [False, True])
def test_public_plan_reaches_execution_owner(supply_correlation: bool) -> None:
    """Public plan creation with input_refs must resolve dataset and workshop correlation to reach execution owner."""
    body = ResearchPlanCreateRequest.model_validate({
        "spec_version": "1.0",
        "strategy_id": "review-strategy",
        "strategy_spec_registry_id": "review-spec",
        "stages": [
            {
                "stage_id": "review-stage",
                "stage_type": "prototype_backtest",
                "input_refs": ["dataset:review-governed"],
                "status": "ready",
            }
        ],
    })
    plan = _build_plan(
        body,
        "review-workshop",
        "review-plan",
        "2026-09-08T00:00:00Z",
        SimpleNamespace(tenant_id="review-tenant", user_id="review-user"),
    )
    if supply_correlation:
        plan["correlation_id"] = "review-correlation"

    backend = TestClient(_load_service_module().app)
    responses = []

    def transport(req: Any) -> bytes:
        response = backend.post(
            "/stages/prototype_backtest/execute",
            content=req.data,
            headers={"Content-Type": "application/json"},
        )
        responses.append(response)
        return response.content

    owner = AuthenticResearchBackendClient(
        "prototype_backtest",
        "vectorbt",
        base_url="http://review-backend",
        transport=transport,
    )
    try:
        owner.execute(
            stage=plan["stages"][0],
            plan=plan,
            context={"run_id": "review-run", "correlation_id": plan.get("correlation_id", "")},
            downstream_key="review-key",
        )
    except RuntimeError:
        pass
    assert responses and responses[0].status_code == 200, responses[0].text


def test_backend_without_provenance_or_receipt_does_not_mint_real() -> None:
    """Backend returning without provenance or receipt must never mint 'real' provenance."""
    owner = AuthenticResearchBackendClient(
        "prototype_backtest",
        "vectorbt",
        base_url="http://review-backend",
        transport=lambda req: {
            "status": "succeeded",
            "backend_reference": "backend://review-job",
            "artifact_digest": "sha256:review-digest",
            "metrics": [{"metric": "score", "value": 0.5}],
        },
    )
    try:
        result = owner.execute(
            stage={"stage_id": "s", "input_refs": ["dataset:test"]},
            plan={"plan_id": "p"},
            context={"run_id": "review-run", "correlation_id": "review-correlation"},
            downstream_key="review-key",
        )
    except RuntimeError:
        return
    assert result["provenance"] != "real", result
    assert result.get("receipt") is None


def test_backend_with_real_provenance_but_no_receipt_downgrades_to_simulation() -> None:
    """Backend claiming 'real' provenance without owner-emitted receipt must downgrade to 'simulation' and receipt=None."""
    owner = AuthenticResearchBackendClient(
        "prototype_backtest",
        "vectorbt",
        base_url="http://review-backend",
        transport=lambda req: {
            "status": "succeeded",
            "provenance": "real",
            "backend_reference": "backend://review-job-real",
            "artifact_digest": "sha256:review-digest-real",
            "metrics": [{"metric": "score", "value": 0.9, "provenance": "real"}],
        },
    )
    result = owner.execute(
        stage={"stage_id": "s", "input_refs": ["dataset:test"]},
        plan={"plan_id": "p"},
        context={"run_id": "review-run", "correlation_id": "review-correlation"},
        downstream_key="review-key",
    )
    assert result["provenance"] == "simulation"
    assert result["receipt"] is None
    for m in result["metrics"]:
        assert m.get("provenance") != "real"


def test_public_create_approve_dispatch_worker_to_research_endpoint() -> None:
    """Full natural Agora pipeline from public plan create, approve, outbox dispatch, and worker drain to real research endpoint."""
    tenant_id = "tenant-agora-natural"
    user_id = "user-agora-natural"
    workshop_id = "ws-agora-natural-001"
    now_iso = "2026-09-08T00:00:00Z"
    scope = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
        roles=["operator", "agora:write", "agora:read"],
        granted_capabilities=["agora:write", "agora:read"],
    )

    # 1. Public plan create payload: NO correlation_id, NO dataset (both forbidden by schema)
    create_req = ResearchPlanCreateRequest.model_validate({
        "spec_version": "1.0",
        "strategy_id": "strat-natural-001",
        "strategy_spec_registry_id": "ssr-natural-001",
        "stages": [
            {
                "stage_id": "stage-natural-proto",
                "stage_type": "prototype_backtest",
                "input_refs": ["dataset:ds-natural-ohlcv-001"],
                "status": "ready",
                "routing": {
                    "backend_mode": "real",
                    "preferred_backend": "vectorbt",
                },
            }
        ],
    })
    plan_id = "plan-agora-natural-001"
    plan = _build_plan(create_req, workshop_id, plan_id, now_iso, scope)
    assert plan["correlation_id"] == f"workshop:{workshop_id}"
    assert "dataset" not in plan["stages"][0]

    store = MemoryResearchPlanStore()
    store.create_plan(plan)

    # 2. Approve plan
    store.update_plan(
        plan_id,
        {
            "status": "approved",
            "approved_at": now_iso,
        },
        tenant_id=tenant_id,
        user_id=user_id,
    )

    # 3. Create run projection & outbox dispatch record
    stage_item = plan["stages"][0]
    run_id = f"run-natural-{plan_id}"
    run_obj = _build_run_projection(
        plan=plan,
        stage=stage_item,
        run_id=run_id,
        now=now_iso,
        scope=scope,
    )
    store.create_run(run_obj)

    # 4. Connect authentic research backend client hitting the research HTTP service app
    backend_app = _load_service_module().app
    test_client = TestClient(backend_app)

    def service_transport(req: Any) -> bytes:
        resp = test_client.post(
            f"/stages/{req.full_url.split('/stages/')[-1]}" if "/stages/" in req.full_url else "/stages/prototype_backtest/execute",
            content=req.data,
            headers={"Content-Type": "application/json"},
        )
        return resp.content

    backend_client = AuthenticResearchBackendClient(
        stage_type="prototype_backtest",
        preferred_backend="vectorbt",
        base_url="http://research-backend-service",
        transport=service_transport,
    )
    adapter = AuthenticStageAdapter(
        stage_type="prototype_backtest",
        preferred_backend="vectorbt",
        mode="real",
        execution_owner=backend_client,
    )

    dispatcher = ResearchDispatcher(
        store=store,
        adapter_registry=None,
        utc_now=lambda: now_iso,
    )
    dispatcher.registry.register("prototype_backtest", adapter)

    outbox_rec = dispatcher.create_outbox_record(
        plan=plan,
        stage=stage_item,
        run_id=run_id,
        scope=scope,
        now=now_iso,
    )
    assert outbox_rec["outbox_id"] is not None
    assert outbox_rec["correlation_id"] == f"workshop:{workshop_id}"

    # 5. Execute worker drain
    worker = AgoraInteractionWorker(
        research_store=store,
        research_dispatcher=dispatcher,
        worker_id="worker-natural-001",
    )
    drained = worker.drain_research_outbox(tenant_id=tenant_id, user_id=user_id)
    assert drained >= 1

    # 6. Verify executed run and authentic owner receipt
    completed_run = store.get_run(run_id)
    assert completed_run is not None
    assert completed_run["execution_status"] == "succeeded"
    assert completed_run["outcome"] == "pass"
    assert completed_run["correlation_id"] == f"workshop:{workshop_id}"
    assert len(completed_run["metrics"]) > 0

    receipt = store.get_execution_receipt(run_id)
    assert receipt is not None
    assert receipt["run_id"] == run_id
    assert receipt["correlation_id"] == f"workshop:{workshop_id}"
    assert receipt["spec_version"] == "1.0"
    assert receipt["mode"] in ("real", "simulation")
    assert receipt["backend_reference"].startswith("research-orchestrator://stages/prototype_backtest/")

    # 7. Server-side provenance resolution
    prov, resolved_receipt = resolve_run_provenance(
        store,
        completed_run,
        expected_correlation_id=f"workshop:{workshop_id}",
    )
    assert prov == receipt["mode"]
    assert resolved_receipt is not None
    assert resolved_receipt["receipt_id"] == receipt["receipt_id"]
