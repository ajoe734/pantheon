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
import sys
from datetime import date, timedelta
from pathlib import Path
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
    from agora.dataset_extraction.router import _default_store
    from agora.dataset_extraction.extractor import DatasetRecord
    from agora.dataset_extraction.models import DatasetKind, InteractionKind

    raw_ohlcv = []
    start = date(2026, 1, 1)
    for inst, base in (("AAA", 100.0), ("BBB", 50.0)):
        for i in range(35):
            d = (start + timedelta(days=i)).isoformat()
            p = base + i * 0.5
            raw_ohlcv.append({
                "instrument": inst,
                "date": d,
                "open": p,
                "high": p + 1.0,
                "low": p - 0.5,
                "close": p + 0.2,
                "volume": 1000.0,
            })
    _default_store().save_record(DatasetRecord(
        evidence_id="ev-review-governed",
        dataset_version_id="review-governed",
        dataset_kind=DatasetKind.OBSERVE,
        interaction_kind=InteractionKind.ASK,
        persona_id="persona-servant-agora",
        session_id="session-review",
        tenant_id="review-tenant",
        user_id="review-user",
        content={
            "dataset_id": "dataset:review-governed",
            "strategy_id": "review-strategy",
            "source_dataset_refs": ["dataset:review-governed"],
            "data_frequency": "daily",
            "records": raw_ohlcv,
        },
        source_refs=["dataset:review-governed"],
        learning_eligible=True,
        captured_at="2026-09-08T00:00:00Z",
        extracted_at="2026-09-08T00:00:00Z",
    ))

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
    owner.execute(
        stage=plan["stages"][0],
        plan=plan,
        context={"run_id": "review-run", "correlation_id": plan.get("correlation_id", "")},
        downstream_key="review-key",
    )
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


def test_public_create_approve_dispatch_worker_to_research_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RANKING_STORE_DSN", "postgresql://test:test@localhost:5432/test")
    monkeypatch.setenv("RANKING_STORE_BOOTSTRAP", "0")
    sys.path.insert(0, str(Path.cwd() / "services/control-plane/bff/tests"))
    from test_agora_strategy_workshop import _workshop_client, _create_workshop, _get_current_etag
    import main as bff_main
    from agora.dataset_extraction.extractor import DatasetRecord
    from agora.dataset_extraction.models import DatasetKind, InteractionKind
    from agora.dataset_extraction.router import _default_store

    client = _workshop_client(monkeypatch)
    workshop_id = _create_workshop(client, "ws-agora-natural-001")
    auth = {"Authorization": "Bearer agora-test-user:operator"}

    # 1. Establish originating interaction trace via workshop message
    msg_resp = client.post(
        f"/bff/agora/workshops/{workshop_id}/messages",
        headers={
            **auth,
            "Idempotency-Key": "msg-natural-001",
            "If-Match": _get_current_etag(client, workshop_id),
            "X-Trace-Id": "trace-natural-interaction-001",
        },
        json={"content": "Run natural research backtest on governed dataset"},
    )
    assert msg_resp.status_code == 202, msg_resp.text

    # 2. Register canonical governed dataset in the canonical dataset owner
    ds_store = getattr(bff_main, "dataset_store", None) or _default_store()
    raw_ohlcv = []
    start = date(2026, 1, 1)
    for inst, base in (("AAA", 100.0), ("BBB", 50.0)):
        for i in range(35):
            d = (start + timedelta(days=i)).isoformat()
            p = base + i * 0.5
            raw_ohlcv.append({
                "instrument": inst,
                "date": d,
                "open": p,
                "high": p + 1.0,
                "low": p - 0.5,
                "close": p + 0.2,
                "volume": 1000.0,
            })
    ds_store.save_record(DatasetRecord(
        evidence_id="ev-natural-001",
        dataset_version_id="ds-natural-ohlcv-001",
        dataset_kind=DatasetKind.OBSERVE,
        interaction_kind=InteractionKind.ASK,
        persona_id="persona-servant-agora",
        session_id="session-natural-001",
        tenant_id="pantheon-dev",
        user_id="agora-test-user",
        content={
            "dataset_id": "dataset:ds-natural-ohlcv-001",
            "strategy_id": "strat-natural-001",
            "source_dataset_refs": ["dataset:ds-natural-ohlcv-001"],
            "data_frequency": "daily",
            "records": raw_ohlcv,
        },
        source_refs=["dataset:ds-natural-ohlcv-001"],
        learning_eligible=True,
        captured_at="2026-09-08T00:00:00Z",
        extracted_at="2026-09-08T00:00:00Z",
    ))

    # 3. Create plan via public route POST /bff/agora/workshops/{workshop_id}/research-plans
    create_resp = client.post(
        f"/bff/agora/workshops/{workshop_id}/research-plans",
        headers={
            **auth,
            "Idempotency-Key": "plan-natural-create-001",
        },
        json={
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
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    plan_data = create_resp.json()["data"]
    plan_id = plan_data["plan_id"]
    plan_etag = create_resp.headers.get("etag") or create_resp.headers.get("ETag") or create_resp.json()["meta"]["etag"]
    assert plan_data["correlation_id"] == "trace-natural-interaction-001"
    assert "dataset" not in plan_data["stages"][0]

    # 4. Approve plan via public route POST /bff/agora/research-plans/{plan_id}/approve
    approve_resp = client.post(
        f"/bff/agora/research-plans/{plan_id}/approve",
        headers={
            **auth,
            "Idempotency-Key": "plan-natural-approve-001",
            "If-Match": plan_etag,
        },
    )
    assert approve_resp.status_code == 200, approve_resp.text
    approved_data = approve_resp.json()["data"]
    assert approved_data["status"] == "approved"
    approved_etag = approve_resp.headers.get("etag") or approve_resp.headers.get("ETag") or approve_resp.json().get("meta", {}).get("etag")

    # 5. Connect authentic research backend adapter to bff_main.research_dispatcher
    backend_app = _load_service_module().app
    test_backend_client = TestClient(backend_app)

    def service_transport(req: Any) -> bytes:
        resp = test_backend_client.post(
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
    dispatcher = bff_main.research_dispatcher
    assert dispatcher is not None
    dispatcher.registry.register("prototype_backtest", adapter)

    # 6. Dispatch plan run via public route POST /bff/agora/research-plans/{plan_id}/runs
    run_resp = client.post(
        f"/bff/agora/research-plans/{plan_id}/runs",
        headers={
            **auth,
            "Idempotency-Key": "plan-natural-run-001",
            "If-Match": approved_etag,
        },
    )
    assert run_resp.status_code == 202, run_resp.text
    run_resp_json = run_resp.json()
    assert run_resp_json["status"] == "queued"
    run_id = run_resp_json["data"]["run_id"]
    initial_run = bff_main.research_store.get_run(run_id)
    assert initial_run is not None
    assert initial_run["execution_status"] == "queued"
    assert initial_run["correlation_id"] == "trace-natural-interaction-001"

    # 7. Execute worker drain via AgoraInteractionWorker
    worker = AgoraInteractionWorker(
        research_store=bff_main.research_store,
        research_dispatcher=dispatcher,
        worker_id="worker-natural-001",
    )
    drained = worker.drain_research_outbox()
    assert drained >= 1

    # 8. Verify executed run and authentic owner receipt
    completed_run = bff_main.research_store.get_run(run_id)
    assert completed_run is not None
    assert completed_run["execution_status"] == "succeeded"
    assert completed_run["outcome"] == "pass"
    assert completed_run["correlation_id"] == "trace-natural-interaction-001"
    assert len(completed_run["metrics"]) > 0

    receipt = bff_main.research_store.get_execution_receipt(run_id)
    assert receipt is not None
    assert receipt["run_id"] == run_id
    assert receipt["correlation_id"] == "trace-natural-interaction-001"
    assert receipt["spec_version"] == "1.0"
    assert receipt["mode"] in ("real", "simulation")
    assert receipt["backend_reference"].startswith("research-orchestrator://stages/prototype_backtest/")

    # 9. Server-side provenance resolution
    prov, resolved_receipt = resolve_run_provenance(
        bff_main.research_store,
        completed_run,
        expected_correlation_id="trace-natural-interaction-001",
    )
    assert prov == receipt["mode"]
    assert resolved_receipt is not None
    assert resolved_receipt["receipt_id"] == receipt["receipt_id"]

    # 10. Assert public candidate admission of the exact natural owner artifact
    from agora.research.routes.common import (
        AgoraResearchRouteContext,
        CandidatePoolCreateRequest,
        _extract_run_artifact_identities,
    )
    from types import SimpleNamespace

    ids, digests = _extract_run_artifact_identities(completed_run)
    owner_artifact_id = None
    for r in completed_run.get("artifact_refs") or []:
        if isinstance(r, dict) and r.get("artifact_id"):
            owner_artifact_id = r["artifact_id"]
            break
        elif isinstance(r, str) and r.startswith("rart-"):
            owner_artifact_id = r
            break
    assert owner_artifact_id is not None, f"Owner artifact_id not found in run artifact_refs: {completed_run.get('artifact_refs')}"
    assert owner_artifact_id in ids, f"Owner artifact_id '{owner_artifact_id}' must be in projected artifact IDs: {sorted(ids)}"

    owner_digest = digests.get(owner_artifact_id)
    assert owner_digest == receipt["artifact_digest"], f"Owner digest '{owner_digest}' must match receipt '{receipt['artifact_digest']}'"

    scope = SimpleNamespace(tenant_id="pantheon-dev", user_id="agora-test-user", auth_stub=False)
    research_ctx = AgoraResearchRouteContext(
        store=bff_main.research_store,
        extract_identity=lambda *a, **k: None,
        require_read_role=lambda *a, **k: None,
        require_write_role=lambda *a, **k: None,
        bff_error=lambda status, code, msg, *a: Exception(f"{code}: {msg}"),
        utc_now=lambda: "2026-09-08T07:00:00Z",
    )

    # Candidate admission with genuine natural owner artifact
    pool = research_ctx.build_candidate_pool(
        CandidatePoolCreateRequest(
            operator_id="agora-test-user",
            profile="production",
            candidates=[
                {
                    "artifact_id": owner_artifact_id,
                    "run_id": run_id,
                    "lifecycle_state": "candidate",
                    "artifact_digest": owner_digest,
                }
            ],
            metrics_by_artifact={
                owner_artifact_id: {"mean_total_return": 999.0}  # Client attempting to substitute authentic score
            },
        ),
        scope,
        "2026-09-08T07:00:00Z",
    )
    assert len(pool["candidates"]) == 1
    cand = pool["candidates"][0]
    assert cand["artifact_id"] == owner_artifact_id
    assert cand["provenance"] == receipt["mode"]
    assert cand["has_real_receipt"] == (receipt["mode"] == "real")
    assert cand["receipt_id"] == receipt["receipt_id"]
    assert cand["artifact_digest"] == receipt["artifact_digest"]

    # Assert client cannot substitute authentic owner metrics
    cand_metrics = bff_main.research_store.get_candidate_metrics(pool["pool_id"], owner_artifact_id)
    assert cand_metrics.get("mean_total_return") != 999.0

    # Negative control: synthetic artifact IDs fail admission
    synth_pool = research_ctx.build_candidate_pool(
        CandidatePoolCreateRequest(
            operator_id="agora-test-user",
            profile="production",
            candidates=[
                {
                    "artifact_id": "art:stage-natural-proto:vectorbt",
                    "run_id": run_id,
                    "lifecycle_state": "candidate",
                },
                {
                    "artifact_id": "artifact",
                    "run_id": run_id,
                    "lifecycle_state": "candidate",
                },
            ],
        ),
        scope,
        "2026-09-08T07:00:00Z",
    )
    for c in synth_pool["candidates"]:
        assert c["has_real_receipt"] is False
        assert c["provenance"] != "real"


def test_unknown_dataset_fails_closed_on_public_route_dispatch_and_drain(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unknown dataset reference must fail closed end-to-end through public route dispatch and drain."""
    monkeypatch.setenv("RANKING_STORE_DSN", "postgresql://test:test@localhost:5432/test")
    monkeypatch.setenv("RANKING_STORE_BOOTSTRAP", "0")
    sys.path.insert(0, str(Path.cwd() / "services/control-plane/bff/tests"))
    from test_agora_strategy_workshop import _workshop_client, _create_workshop
    import main as bff_main

    client = _workshop_client(monkeypatch)
    workshop_id = _create_workshop(client, "ws-agora-negative-001")
    auth = {"Authorization": "Bearer agora-test-user:operator"}

    create_resp = client.post(
        f"/bff/agora/workshops/{workshop_id}/research-plans",
        headers={
            **auth,
            "Idempotency-Key": "plan-negative-create-001",
        },
        json={
            "spec_version": "1.0",
            "strategy_id": "strat-negative-001",
            "strategy_spec_registry_id": "ssr-negative-001",
            "stages": [
                {
                    "stage_id": "stage-negative-proto",
                    "stage_type": "prototype_backtest",
                    "input_refs": ["dataset:codex-does-not-exist"],
                    "status": "ready",
                    "routing": {
                        "backend_mode": "real",
                        "preferred_backend": "vectorbt",
                    },
                }
            ],
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    plan_data = create_resp.json()["data"]
    plan_id = plan_data["plan_id"]
    plan_etag = create_resp.headers.get("etag") or create_resp.headers.get("ETag") or create_resp.json()["meta"]["etag"]

    approve_resp = client.post(
        f"/bff/agora/research-plans/{plan_id}/approve",
        headers={
            **auth,
            "Idempotency-Key": "plan-negative-approve-001",
            "If-Match": plan_etag,
        },
    )
    assert approve_resp.status_code == 200, approve_resp.text
    approved_etag = approve_resp.headers.get("etag") or approve_resp.headers.get("ETag") or approve_resp.json().get("meta", {}).get("etag")

    # Connect authentic research backend adapter
    backend_app = _load_service_module().app
    test_backend_client = TestClient(backend_app)

    def service_transport(req: Any) -> bytes:
        resp = test_backend_client.post(
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
    dispatcher = bff_main.research_dispatcher
    assert dispatcher is not None
    dispatcher.registry.register("prototype_backtest", adapter)

    run_resp = client.post(
        f"/bff/agora/research-plans/{plan_id}/runs",
        headers={
            **auth,
            "Idempotency-Key": "plan-negative-run-001",
            "If-Match": approved_etag,
        },
    )
    assert run_resp.status_code == 202, run_resp.text
    run_id = run_resp.json()["data"]["run_id"]

    worker = AgoraInteractionWorker(
        research_store=bff_main.research_store,
        research_dispatcher=dispatcher,
        worker_id="worker-negative-001",
    )
    # Drain will fail closed because dataset is unknown
    worker.drain_research_outbox()

    # The run must NOT be successful, receipt must NOT exist, provenance must NOT be 'real'
    run_record = bff_main.research_store.get_run(run_id)
    assert run_record["execution_status"] != "succeeded"
    assert bff_main.research_store.get_execution_receipt(run_id) is None
    prov, _ = resolve_run_provenance(bff_main.research_store, run_record)
    assert prov != "real"


def test_compose_agora_interaction_worker_dataset_wiring_and_separate_process() -> None:
    """Compose service agora-interaction-worker wires dataset store and resolves in a separate process."""
    import subprocess
    import yaml
    root = Path(__file__).resolve().parents[4]
    compose = yaml.safe_load((root / "docker-compose.yml").read_text())
    declared = compose["services"]["agora-interaction-worker"]["environment"]
    bff = compose["services"]["operator-bff"]["environment"]
    assert "AGORA_DATASET_STORE_BACKEND" in bff
    assert "AGORA_DATASET_STORE_BACKEND" in declared
    assert "AGORA_DATASET_STORE_DSN" in declared
    assert "AGORA_DATASET_STORE_SCHEMA" in declared

    worker_env = {k: v for k, v in os.environ.items() if not k.startswith("AGORA_DATASET_STORE_")}
    for key, val in declared.items():
        worker_env[key] = val.split(":-", 1)[-1].rstrip("}") if val.startswith("${") else val

    code = "import sys; sys.path.insert(0, 'services/control-plane/bff'); from agora.dataset_extraction.router import _default_store; print(_default_store().backend)"
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=root,
        env=worker_env,
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    assert result.stdout.strip() == "postgres", result.stdout

    health_res = subprocess.run(
        [sys.executable, "scripts/run_agora_interaction_worker.py", "--healthcheck"],
        cwd=root,
        env=worker_env,
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    assert health_res.returncode == 0


def test_public_candidate_admission_receipt_provenance_and_trust_flag_negative_controls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Public candidate admission must fail closed for forged, missing, or mismatched receipts, and verify genuine real receipts."""
    import main as bff_main
    from agora.research.receipt import ResearchExecutionReceipt

    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    monkeypatch.setenv("AGORA_CANDIDATE_POOL_PROFILE", "production")

    store = bff_main.research_store
    assert store is not None

    tenant_id = "pantheon-dev"
    user_id = "agora-user-a"
    operator_auth = f"Bearer {user_id}:operator"

    # 1. Non-existent run: client claims real provenance and has_real_receipt=True
    client = TestClient(bff_main.app, raise_server_exceptions=False)
    resp_absent = client.post(
        "/bff/agora/candidate-pools",
        headers={
            "Authorization": operator_auth,
            "X-Tenant-Id": tenant_id,
            "Idempotency-Key": "cpool-absent-run-001",
        },
        json={
            "operator_id": user_id,
            "profile": "production",
            "candidates": [
                {
                    "artifact_id": "cand-absent-run",
                    "run_id": "run-does-not-exist",
                    "provenance": "real",
                    "has_real_receipt": True,
                    "trusted": True,
                    "is_real": True,
                    "lifecycle_state": "candidate",
                }
            ],
        },
    )
    assert resp_absent.status_code == 201, resp_absent.text
    cand = resp_absent.json()["data"]["candidates"][0]
    assert cand["provenance"] != "real"
    assert cand["provenance"] == "simulation"
    assert cand.get("has_real_receipt") is False
    assert "trusted" not in cand
    assert "is_real" not in cand

    # 2. Non-terminal run in store (e.g. execution_status="running")
    run_running = {
        "run_id": "run-running-001",
        "tenant_id": tenant_id,
        "user_id": user_id,
        "plan_id": "plan-001",
        "execution_status": "running",
        "executor": "vectorbt_executor",
        "correlation_id": "corr-running-001",
    }
    store.create_run(run_running)
    resp_running = client.post(
        "/bff/agora/candidate-pools",
        headers={
            "Authorization": operator_auth,
            "X-Tenant-Id": tenant_id,
            "Idempotency-Key": "cpool-running-run-001",
        },
        json={
            "operator_id": user_id,
            "profile": "production",
            "candidates": [
                {
                    "artifact_id": "cand-running",
                    "run_id": "run-running-001",
                    "provenance": "real",
                    "has_real_receipt": True,
                    "lifecycle_state": "candidate",
                }
            ],
        },
    )
    assert resp_running.status_code == 201, resp_running.text
    cand_running = resp_running.json()["data"]["candidates"][0]
    assert cand_running["provenance"] != "real"
    assert cand_running["provenance"] == "unavailable"
    assert cand_running.get("has_real_receipt") is False

    # 3. Terminal run without receipt in store
    run_no_rec = {
        "run_id": "run-no-rec-001",
        "tenant_id": tenant_id,
        "user_id": user_id,
        "plan_id": "plan-001",
        "execution_status": "succeeded",
        "executor": "vectorbt_executor",
        "correlation_id": "corr-no-rec-001",
    }
    store.create_run(run_no_rec)
    resp_no_rec = client.post(
        "/bff/agora/candidate-pools",
        headers={
            "Authorization": operator_auth,
            "X-Tenant-Id": tenant_id,
            "Idempotency-Key": "cpool-no-rec-001",
        },
        json={
            "operator_id": user_id,
            "profile": "production",
            "candidates": [
                {
                    "artifact_id": "cand-no-rec",
                    "run_id": "run-no-rec-001",
                    "provenance": "real",
                    "has_real_receipt": True,
                    "lifecycle_state": "candidate",
                }
            ],
        },
    )
    assert resp_no_rec.status_code == 201, resp_no_rec.text
    cand_no_rec = resp_no_rec.json()["data"]["candidates"][0]
    assert cand_no_rec["provenance"] != "real"
    assert cand_no_rec["provenance"] == "simulation"
    assert cand_no_rec.get("has_real_receipt") is False

    # 4. Wrong owner receipt
    run_wrong_owner = {
        "run_id": "run-wrong-owner-001",
        "tenant_id": tenant_id,
        "user_id": user_id,
        "plan_id": "plan-001",
        "execution_status": "succeeded",
        "executor": "vectorbt_executor",
        "correlation_id": "corr-wrong-owner-001",
    }
    store.create_run(run_wrong_owner)
    rec_wrong_owner = ResearchExecutionReceipt(
        receipt_id="rec-wrong-owner-001",
        run_id="run-wrong-owner-001",
        executor="unauthorized_rogue_executor",
        mode="real",
        correlation_id="corr-wrong-owner-001",
        completed_at="2026-09-08T07:00:00Z",
    )
    store.record_execution_receipt(rec_wrong_owner.to_dict())
    resp_wrong_owner = client.post(
        "/bff/agora/candidate-pools",
        headers={
            "Authorization": operator_auth,
            "X-Tenant-Id": tenant_id,
            "Idempotency-Key": "cpool-wrong-owner-001",
        },
        json={
            "operator_id": user_id,
            "profile": "production",
            "candidates": [
                {
                    "artifact_id": "cand-wrong-owner",
                    "run_id": "run-wrong-owner-001",
                    "provenance": "real",
                    "has_real_receipt": True,
                    "lifecycle_state": "candidate",
                }
            ],
        },
    )
    assert resp_wrong_owner.status_code == 201, resp_wrong_owner.text
    cand_wrong_owner = resp_wrong_owner.json()["data"]["candidates"][0]
    assert cand_wrong_owner["provenance"] != "real"
    assert cand_wrong_owner.get("has_real_receipt") is False

    # 5. Wrong correlation receipt
    run_wrong_corr = {
        "run_id": "run-wrong-corr-001",
        "tenant_id": tenant_id,
        "user_id": user_id,
        "plan_id": "plan-001",
        "execution_status": "succeeded",
        "executor": "vectorbt_executor",
        "correlation_id": "expected-corr-123",
    }
    store.create_run(run_wrong_corr)
    rec_wrong_corr = ResearchExecutionReceipt(
        receipt_id="rec-wrong-corr-001",
        run_id="run-wrong-corr-001",
        executor="vectorbt_executor",
        mode="real",
        correlation_id="mismatched-corr-999",
        completed_at="2026-09-08T07:00:00Z",
    )
    store.record_execution_receipt(rec_wrong_corr.to_dict())
    resp_wrong_corr = client.post(
        "/bff/agora/candidate-pools",
        headers={
            "Authorization": operator_auth,
            "X-Tenant-Id": tenant_id,
            "Idempotency-Key": "cpool-wrong-corr-001",
        },
        json={
            "operator_id": user_id,
            "profile": "production",
            "candidates": [
                {
                    "artifact_id": "cand-wrong-corr",
                    "run_id": "run-wrong-corr-001",
                    "provenance": "real",
                    "has_real_receipt": True,
                    "lifecycle_state": "candidate",
                }
            ],
        },
    )
    assert resp_wrong_corr.status_code == 201, resp_wrong_corr.text
    cand_wrong_corr = resp_wrong_corr.json()["data"]["candidates"][0]
    assert cand_wrong_corr["provenance"] != "real"
    assert cand_wrong_corr.get("has_real_receipt") is False

    # 6. Foreign tenant run
    run_foreign = {
        "run_id": "run-foreign-001",
        "tenant_id": "other-tenant-999",
        "user_id": "other-user-999",
        "plan_id": "plan-001",
        "execution_status": "succeeded",
        "executor": "vectorbt_executor",
        "correlation_id": "corr-foreign-001",
    }
    store.create_run(run_foreign)
    rec_foreign = ResearchExecutionReceipt(
        receipt_id="rec-foreign-001",
        run_id="run-foreign-001",
        executor="vectorbt_executor",
        mode="real",
        correlation_id="corr-foreign-001",
        completed_at="2026-09-08T07:00:00Z",
    )
    store.record_execution_receipt(rec_foreign.to_dict())
    resp_foreign = client.post(
        "/bff/agora/candidate-pools",
        headers={
            "Authorization": operator_auth,
            "X-Tenant-Id": tenant_id,
            "Idempotency-Key": "cpool-foreign-run-001",
        },
        json={
            "operator_id": user_id,
            "profile": "production",
            "candidates": [
                {
                    "artifact_id": "cand-foreign",
                    "run_id": "run-foreign-001",
                    "provenance": "real",
                    "has_real_receipt": True,
                    "lifecycle_state": "candidate",
                }
            ],
        },
    )
    assert resp_foreign.status_code == 201, resp_foreign.text
    cand_foreign = resp_foreign.json()["data"]["candidates"][0]
    assert cand_foreign["provenance"] != "real"
    assert cand_foreign.get("has_real_receipt") is False

    # 7. Authentic terminal run with matching owner, correlation, and real receipt
    run_real = {
        "run_id": "run-authentic-real-001",
        "tenant_id": tenant_id,
        "user_id": user_id,
        "plan_id": "plan-001",
        "execution_status": "succeeded",
        "executor": "vectorbt_executor",
        "correlation_id": "corr-authentic-001",
        "artifact_refs": [
            {
                "artifact_id": "cand-authentic-real",
                "ref": "research-artifact://vectorbt_executor/cand-authentic-real",
            }
        ],
        "metrics": {"sharpe_ratio": 1.85, "max_drawdown": 0.08},
    }
    store.create_run(run_real)
    rec_real = ResearchExecutionReceipt(
        receipt_id="rec-authentic-001",
        run_id="run-authentic-real-001",
        executor="vectorbt_executor",
        mode="real",
        correlation_id="corr-authentic-001",
        completed_at="2026-09-08T07:00:00Z",
        artifact_digest="sha256:cand-authentic-real",
    )
    store.record_execution_receipt(rec_real.to_dict())
    resp_real = client.post(
        "/bff/agora/candidate-pools",
        headers={
            "Authorization": operator_auth,
            "X-Tenant-Id": tenant_id,
            "Idempotency-Key": "cpool-authentic-real-001",
        },
        json={
            "operator_id": user_id,
            "profile": "production",
            "candidates": [
                {
                    "artifact_id": "cand-authentic-real",
                    "run_id": "run-authentic-real-001",
                    "correlation_id": "corr-authentic-001",
                    "lifecycle_state": "candidate",
                }
            ],
        },
    )
    assert resp_real.status_code == 201, resp_real.text
    cand_real = resp_real.json()["data"]["candidates"][0]
    assert cand_real["provenance"] == "real"
    assert cand_real["has_real_receipt"] is True
    assert cand_real["receipt_id"] == "rec-authentic-001"
    assert cand_real.get("artifact_digest") == "sha256:cand-authentic-real"


def test_unrelated_artifact_cannot_borrow_run_receipt_regression() -> None:
    """Unrelated candidate artifact_id referencing a valid real run fails closed."""
    from agora.research.routes.common import AgoraResearchRouteContext, CandidatePoolCreateRequest
    from types import SimpleNamespace

    store = MemoryResearchPlanStore()
    store.create_run({
        "run_id": "review-run-unrelated",
        "plan_id": "review-plan-1",
        "tenant_id": "review-tenant",
        "user_id": "review-user",
        "execution_status": "succeeded",
        "executor": "vectorbt_executor",
        "correlation_id": "review-corr-1",
        "provenance": "real",
        "artifact_refs": [{"artifact_id": "actual-artifact", "ref": "artifact://actual-artifact"}],
        "metrics": {"sharpe_ratio": 2.1},
    })
    store.record_execution_receipt({
        "receipt_id": "review-receipt-1",
        "run_id": "review-run-unrelated",
        "executor": "vectorbt_executor",
        "mode": "real",
        "correlation_id": "review-corr-1",
        "artifact_digest": "sha256:actual",
        "spec_version": "1.0",
        "completed_at": "2026-09-08T07:00:00Z",
    })
    context = AgoraResearchRouteContext(
        store=store,
        extract_identity=lambda *a, **k: None,
        require_read_role=lambda *a, **k: None,
        bff_error=lambda *a, **k: RuntimeError(str(a)),
        utc_now=lambda: "2026-09-08T07:00:00Z",
    )
    scope = SimpleNamespace(tenant_id="review-tenant", user_id="review-user", auth_stub=False)

    # Positive control: matching artifact and digest
    pos_res = context.build_candidate_pool(
        CandidatePoolCreateRequest(
            operator_id="review-user",
            profile="production",
            candidates=[{
                "artifact_id": "actual-artifact",
                "run_id": "review-run-unrelated",
                "artifact_digest": "sha256:actual",
                "lifecycle_state": "candidate",
            }],
        ),
        scope,
        "2026-09-08T07:00:00Z",
    )
    assert pos_res["candidates"][0]["provenance"] == "real"
    assert pos_res["candidates"][0]["has_real_receipt"] is True

    # Negative control: unrelated artifact fails closed
    neg_res = context.build_candidate_pool(
        CandidatePoolCreateRequest(
            operator_id="review-user",
            profile="production",
            candidates=[{
                "artifact_id": "unrelated-client-artifact",
                "run_id": "review-run-unrelated",
                "lifecycle_state": "candidate",
            }],
        ),
        scope,
        "2026-09-08T07:00:00Z",
    )
    assert neg_res["candidates"][0]["provenance"] != "real"
    assert neg_res["candidates"][0]["has_real_receipt"] is False


def test_multiple_mismatches_fail_closed_without_crashing_regression() -> None:
    """Candidate with multiple mismatched fields fails closed without crashing."""
    from agora.research.routes.common import AgoraResearchRouteContext, CandidatePoolCreateRequest
    from types import SimpleNamespace

    store = MemoryResearchPlanStore()
    store.create_run({
        "run_id": "review-run-multi",
        "plan_id": "review-plan-1",
        "tenant_id": "review-tenant",
        "user_id": "review-user",
        "execution_status": "succeeded",
        "executor": "vectorbt_executor",
        "correlation_id": "review-corr-1",
        "provenance": "real",
        "artifact_refs": [{"artifact_id": "actual-artifact", "ref": "artifact://actual-artifact"}],
    })
    store.record_execution_receipt({
        "receipt_id": "review-receipt-1",
        "run_id": "review-run-multi",
        "executor": "vectorbt_executor",
        "mode": "real",
        "correlation_id": "review-corr-1",
        "artifact_digest": "sha256:actual",
        "spec_version": "1.0",
        "completed_at": "2026-09-08T07:00:00Z",
    })
    context = AgoraResearchRouteContext(
        store=store,
        extract_identity=lambda *a, **k: None,
        require_read_role=lambda *a, **k: None,
        bff_error=lambda *a, **k: RuntimeError(str(a)),
        utc_now=lambda: "2026-09-08T07:00:00Z",
    )
    scope = SimpleNamespace(tenant_id="review-tenant", user_id="review-user", auth_stub=False)

    res = context.build_candidate_pool(
        CandidatePoolCreateRequest(
            operator_id="review-user",
            profile="production",
            candidates=[{
                "artifact_id": "actual-artifact",
                "run_id": "review-run-multi",
                "correlation_id": "wrong-correlation",
                "executor": "wrong-executor",
                "receipt_id": "wrong-receipt",
                "artifact_digest": "sha256:wrong",
                "lifecycle_state": "candidate",
            }],
        ),
        scope,
        "2026-09-08T07:00:00Z",
    )
    assert res["candidates"][0]["provenance"] != "real"
    assert res["candidates"][0]["has_real_receipt"] is False


def test_postgres_dataset_owner_bootstrap_read_restart_regression() -> None:
    """Postgres AgoraDatasetStore bootstraps, reads absent record, writes, restarts, and reads back."""
    import uuid
    from agora.dataset_extraction.extractor import AgoraDatasetStore, DatasetRecord
    from agora.dataset_extraction.models import DatasetKind, InteractionKind

    dsn = os.environ.get("REVIEW_TEST_DSN") or os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Neither REVIEW_TEST_DSN nor TEST_DATABASE_URL is set")

    schema = f"agora_reg_{uuid.uuid4().hex[:12]}"
    store = AgoraDatasetStore(backend="postgres", dsn=dsn, schema=schema)
    try:
        assert store._bootstrapped is True
        # Read absent ref: must return None without AttributeError
        assert store.get_by_ref("absent-ref-123", tenant_id="t-reg", user_id="u-reg") is None

        # Save record
        record = DatasetRecord(
            evidence_id="ev-reg-1",
            dataset_version_id="dsv-reg-1",
            dataset_kind=DatasetKind.OBSERVE,
            interaction_kind=InteractionKind.ASK,
            persona_id="persona-reg",
            tenant_id="t-reg",
            user_id="u-reg",
            content={"test": "data"},
            source_refs=["ref:reg-1"],
            learning_eligible=True,
            captured_at="2026-09-08T07:00:00Z",
            extracted_at="2026-09-08T07:00:00Z",
        )
        store.save_record(record)
        fetched = store.get_by_ref("dsv-reg-1", tenant_id="t-reg", user_id="u-reg")
        assert fetched is not None
        assert fetched.dataset_version_id == "dsv-reg-1"

        # Restart: instantiate a new AgoraDatasetStore on the same schema
        store_restarted = AgoraDatasetStore(backend="postgres", dsn=dsn, schema=schema)
        assert store_restarted._bootstrapped is True
        fetched_after_restart = store_restarted.get_by_ref("dsv-reg-1", tenant_id="t-reg", user_id="u-reg")
        assert fetched_after_restart is not None
        assert fetched_after_restart.dataset_version_id == "dsv-reg-1"
    finally:
        with store._connect() as conn:
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


def test_owner_metric_list_cannot_be_replaced_by_client() -> None:
    from agora.research.routes.common import AgoraResearchRouteContext, CandidatePoolCreateRequest
    from agora.research.store import MemoryResearchPlanStore
    from types import SimpleNamespace

    store = MemoryResearchPlanStore()
    artifact = {"artifact_id": "actual-artifact", "ref": "artifact://actual-artifact"}
    store.create_run(dict(
        run_id="run-review", plan_id="plan-review", tenant_id="tenant",
        user_id="user", execution_status="succeeded", executor="vectorbt_executor",
        correlation_id="corr-review", provenance="real", artifact_refs=[artifact],
        metrics=[{"metric": "sharpe_ratio", "value": 0.1, "provenance": "real"}],
    ))
    store.record_execution_receipt(dict(
        receipt_id="receipt-review", run_id="run-review",
        executor="vectorbt_executor", mode="real", correlation_id="corr-review",
        artifact_digest="sha256:actual", spec_version="1.0", completed_at="2026-09-08T07:00:00Z",
    ))
    ctx = AgoraResearchRouteContext(
        store=store,
        extract_identity=lambda *a, **k: None,
        require_read_role=lambda *a, **k: None,
        require_write_role=lambda *a, **k: None,
        bff_error=lambda *a, **k: RuntimeError(str(a)),
        utc_now=lambda: "2026-09-08T07:00:00Z",
    )
    pool = ctx.build_candidate_pool(
        CandidatePoolCreateRequest(
            operator_id="user",
            profile="production",
            candidates=[dict(artifact_id="actual-artifact", run_id="run-review", lifecycle_state="candidate")],
            metrics_by_artifact={"actual-artifact": {"sharpe_ratio": 999}},
        ),
        SimpleNamespace(tenant_id="tenant", user_id="user", auth_stub=False),
        "2026-09-08T07:00:00Z",
    )
    assert pool["candidates"][0]["has_real_receipt"] is True
    assert ctx.store.get_candidate_metrics(pool["pool_id"], "actual-artifact")["sharpe_ratio"] == 0.1


def test_artifact_dict_digest_mismatch_fails_closed() -> None:
    from agora.research.routes.common import AgoraResearchRouteContext, CandidatePoolCreateRequest
    from agora.research.store import MemoryResearchPlanStore
    from types import SimpleNamespace

    store = MemoryResearchPlanStore()
    artifact = {"artifact_id": "actual-artifact", "ref": "artifact://actual-artifact", "digest": "sha256:wrong"}
    store.create_run(dict(
        run_id="run-review", plan_id="plan-review", tenant_id="tenant",
        user_id="user", execution_status="succeeded", executor="vectorbt_executor",
        correlation_id="corr-review", provenance="real", artifact_refs=[artifact],
        metrics=[],
    ))
    store.record_execution_receipt(dict(
        receipt_id="receipt-review", run_id="run-review",
        executor="vectorbt_executor", mode="real", correlation_id="corr-review",
        artifact_digest="sha256:actual", spec_version="1.0", completed_at="2026-09-08T07:00:00Z",
    ))
    ctx = AgoraResearchRouteContext(
        store=store,
        extract_identity=lambda *a, **k: None,
        require_read_role=lambda *a, **k: None,
        require_write_role=lambda *a, **k: None,
        bff_error=lambda *a, **k: RuntimeError(str(a)),
        utc_now=lambda: "2026-09-08T07:00:00Z",
    )
    candidate = ctx.build_candidate_pool(
        CandidatePoolCreateRequest(
            operator_id="user",
            profile="production",
            candidates=[dict(artifact_id="actual-artifact", run_id="run-review", lifecycle_state="candidate")],
        ),
        SimpleNamespace(tenant_id="tenant", user_id="user", auth_stub=False),
        "2026-09-08T07:00:00Z",
    )["candidates"][0]
    assert candidate["has_real_receipt"] is False, candidate


def test_public_natural_chain_preserves_owner_artifact(monkeypatch: pytest.MonkeyPatch) -> None:
    from agora.research.routes.common import _extract_run_artifact_identities
    modules = []
    original_loader = _load_service_module
    def load():
        mod = original_loader()
        modules.append(mod)
        return mod
    monkeypatch.setattr(sys.modules[__name__], "_load_service_module", load)
    test_public_create_approve_dispatch_worker_to_research_endpoint(monkeypatch)
    import main as bff_main
    artifacts = modules[0].store.list_artifacts()
    assert artifacts
    artifact = artifacts[-1]
    run = bff_main.research_store.get_run(artifact["run_id"])
    ids, digests = _extract_run_artifact_identities(run)
    assert artifact["artifact_id"] in ids, dict(owner_artifact=artifact["artifact_id"], projected_ids=sorted(ids))
