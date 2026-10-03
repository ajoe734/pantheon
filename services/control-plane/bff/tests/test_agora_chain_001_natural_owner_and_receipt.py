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
from agora.research.receipt import resolve_run_provenance
from agora.research.routes.common import (
    ResearchPlanCreateRequest,
    _build_plan,
    _build_run_projection,
)
from agora.research.store import MemoryResearchPlanStore
try:
    from agora.strategy_workshop import MemoryWorkshopStore
except ImportError:
    from services.control_plane.bff.agora.strategy_workshop import MemoryWorkshopStore
from services.control_plane.bff.agora.strategy_workshop.operations import (
    WorkshopCanonicalOperations,
    CanonicalOperationError,
)
from services.research.tests.test_research_orchestrator_http_service import _load_service_module


def test_build_plan_with_injected_workshop_store_makes_no_main_import_attempts() -> None:
    """Verify _build_plan with injected workshop_store makes no BFF main import or execution attempts."""
    import sys
    attempts: list[str] = []

    def audit_hook(event: str, args: tuple[Any, ...]) -> None:
        if event == "import":
            mod_name = args[0]
            if mod_name == "main" or "bff.main" in mod_name:
                attempts.append(mod_name)

    sys.addaudithook(audit_hook)
    body = ResearchPlanCreateRequest.model_validate({
        "spec_version": "1.0",
        "strategy_id": "audit-strategy",
        "strategy_spec_registry_id": "audit-spec",
        "stages": [
            {
                "stage_id": "audit-stage",
                "stage_type": "prototype_backtest",
                "input_refs": ["dataset:audit-ref"],
                "status": "ready",
            }
        ],
    })
    plan = _build_plan(
        body,
        "audit-workshop",
        "audit-plan",
        "2026-09-08T00:00:00Z",
        SimpleNamespace(tenant_id="audit-tenant", user_id="audit-user"),
        workshop_store=MemoryWorkshopStore(),
    )
    assert plan["correlation_id"] == "workshop:audit-workshop"
    assert not attempts, f"Unexpected main import attempts: {attempts}"












def test_public_candidate_admission_receipt_provenance_and_trust_flag_negative_controls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Public candidate admission must fail closed for forged, missing, or mismatched receipts, and verify genuine real receipts."""
    try:
        from services.control_plane.bff.tests.test_agora_strategy_workshop import _workshop_client
    except ImportError:
        from test_agora_strategy_workshop import _workshop_client
    from agora.research.receipt import ResearchExecutionReceipt

    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    monkeypatch.setenv("AGORA_CANDIDATE_POOL_PROFILE", "production")

    client = _workshop_client(monkeypatch)
    router = getattr(client, "router", None)
    app = getattr(client, "app_instance", None)
    store = getattr(router, "research_store", None) or getattr(app, "research_store", None)
    assert store is not None

    tenant_id = "pantheon-dev"
    user_id = "agora-user-a"
    operator_auth = f"Bearer {user_id}:operator"

    # 1. Non-existent run: client claims real provenance and has_real_receipt=True
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


def test_real_candidate_cannot_acquire_client_only_scoring_metrics() -> None:
    from agora.research.routes.common import (
        AgoraResearchRouteContext,
        CandidatePoolCreateRequest,
        _load_default_scoring_recipe,
        _score_candidate,
    )
    from agora.research.store import MemoryResearchPlanStore

    store = MemoryResearchPlanStore()
    artifact = {"artifact_id": "actual-artifact", "ref": "artifact://actual-artifact"}
    store.create_run(dict(
        run_id="run-review",
        plan_id="plan-review",
        tenant_id="tenant",
        user_id="user",
        execution_status="succeeded",
        executor="vectorbt_executor",
        correlation_id="corr-review",
        provenance="real",
        artifact_refs=[artifact],
        metrics=[{"metric": "mean_sharpe_ratio", "value": 0.1, "provenance": "real"}],
    ))
    store.record_execution_receipt(dict(
        receipt_id="receipt-review",
        run_id="run-review",
        executor="vectorbt_executor",
        mode="real",
        correlation_id="corr-review",
        artifact_digest="sha256:actual",
        spec_version="1.0",
        completed_at="2026-09-08T07:00:00Z",
    ))
    ctx = AgoraResearchRouteContext(
        store=store,
        extract_identity=lambda *a, **k: None,
        require_read_role=lambda *a, **k: None,
        require_write_role=lambda *a, **k: None,
        bff_error=lambda *a, **k: RuntimeError(str(a)),
        utc_now=lambda: "2026-09-08T07:00:00Z",
    )
    recipe = _load_default_scoring_recipe()
    invented = {c["component_id"]: 1.0 for c in recipe["positive_components"]}
    invented.update({c["component_id"]: 0.0 for c in recipe["penalty_components"]})
    invented_payload = {"components": invented, "evidence_confidence": 1.0}

    pool = ctx.build_candidate_pool(
        CandidatePoolCreateRequest(
            operator_id="user",
            profile="production",
            candidates=[dict(artifact_id="actual-artifact", run_id="run-review", lifecycle_state="candidate")],
            metrics_by_artifact={"actual-artifact": invented_payload},
        ),
        SimpleNamespace(tenant_id="tenant", user_id="user", auth_stub=False),
        "2026-09-08T07:00:00Z",
    )
    candidate = pool["candidates"][0]
    metrics = ctx.store.get_candidate_metrics(pool["pool_id"], "actual-artifact")
    score = _score_candidate(
        pool_id=pool["pool_id"],
        candidate=candidate,
        metrics=metrics,
        recipe=recipe,
        data_cutoff="2026-09-08",
        scored_at="2026-09-08",
    )
    assert candidate["has_real_receipt"] is True
    assert "components" not in metrics, {"stored_metrics": metrics, "score": score["effective_score"], "band": score["band"]}
    assert "evidence_confidence" not in metrics
    assert score["band"] != "priority_review"


def test_negative_controls_for_absent_owner_keys_and_nested_components() -> None:
    """Absent owner keys, nested components, and evidence_refs must not be filled by client inputs."""
    from agora.research.routes.common import (
        AgoraResearchRouteContext,
        CandidatePoolCreateRequest,
        _load_default_scoring_recipe,
        _score_candidate,
    )
    from agora.research.store import MemoryResearchPlanStore

    store = MemoryResearchPlanStore()
    artifact = {"artifact_id": "actual-artifact", "ref": "artifact://actual-artifact"}
    store.create_run(dict(
        run_id="run-review-neg",
        plan_id="plan-review-neg",
        tenant_id="tenant",
        user_id="user",
        execution_status="succeeded",
        executor="vectorbt_executor",
        correlation_id="corr-review-neg",
        provenance="real",
        artifact_refs=[artifact],
        metrics=[{"metric": "mean_sharpe_ratio", "value": 0.1, "provenance": "real"}],
    ))
    store.record_execution_receipt(dict(
        receipt_id="receipt-review-neg",
        run_id="run-review-neg",
        executor="vectorbt_executor",
        mode="real",
        correlation_id="corr-review-neg",
        artifact_digest="sha256:actual",
        spec_version="1.0",
        completed_at="2026-09-08T07:00:00Z",
    ))
    ctx = AgoraResearchRouteContext(
        store=store,
        extract_identity=lambda *a, **k: None,
        require_read_role=lambda *a, **k: None,
        require_write_role=lambda *a, **k: None,
        bff_error=lambda *a, **k: RuntimeError(str(a)),
        utc_now=lambda: "2026-09-08T07:00:00Z",
    )
    recipe = _load_default_scoring_recipe()

    # Client passes absent keys and nested structures in both candidate._metrics and metrics_by_artifact
    client_metrics = {
        "absent_metric_key": 999.0,
        "components": {
            "branch_historical_profitability": 1.0,
            "branch_identity_confidence": 1.0,
        },
        "evidence_refs": {
            "branch_historical_profitability": ["mock://forged-evidence"],
        },
        "evidence_confidence": 0.99,
    }
    candidate_input = {
        "artifact_id": "actual-artifact",
        "run_id": "run-review-neg",
        "lifecycle_state": "candidate",
        "_metrics": client_metrics,
    }
    pool = ctx.build_candidate_pool(
        CandidatePoolCreateRequest(
            operator_id="user",
            profile="production",
            candidates=[candidate_input],
            metrics_by_artifact={"actual-artifact": client_metrics},
        ),
        SimpleNamespace(tenant_id="tenant", user_id="user", auth_stub=False),
        "2026-09-08T07:00:00Z",
    )
    cand = pool["candidates"][0]
    assert cand["has_real_receipt"] is True
    metrics = ctx.store.get_candidate_metrics(pool["pool_id"], "actual-artifact")

    # Authoritative resolution: absent owner fields remain absent
    assert "absent_metric_key" not in metrics
    assert "components" not in metrics
    assert "evidence_refs" not in metrics
    assert "evidence_confidence" not in metrics
    assert metrics == {"mean_sharpe_ratio": 0.1}

    score = _score_candidate(
        pool_id=pool["pool_id"],
        candidate=cand,
        metrics=metrics,
        recipe=recipe,
        data_cutoff="2026-09-08",
        scored_at="2026-09-08",
    )
    # Without forged components, score does not inflate
    assert score["effective_score"] < 50.0
    assert score["band"] != "priority_review"








def test_missing_identities_fail_closed_through_candidate_admission() -> None:
    """AgoraResearchRouteContext.build_candidate_pool must fail closed when run lacks owner artifact identities."""
    from agora.research.routes.common import AgoraResearchRouteContext, CandidatePoolCreateRequest
    from agora.research.store import MemoryResearchPlanStore

    store = MemoryResearchPlanStore()
    store.create_run(dict(
        run_id="run-no-art",
        plan_id="plan-no-art",
        tenant_id="tenant",
        user_id="user",
        execution_status="succeeded",
        executor="vectorbt_executor",
        correlation_id="corr-no-art",
        provenance="real",
        artifact_refs=[],
        metrics=[{"metric": "mean_sharpe_ratio", "value": 0.1, "provenance": "real"}],
    ))
    store.record_execution_receipt(dict(
        receipt_id="receipt-no-art",
        run_id="run-no-art",
        executor="vectorbt_executor",
        mode="real",
        correlation_id="corr-no-art",
        artifact_digest="sha256:actual",
        spec_version="1.0",
        completed_at="2026-09-08T07:00:00Z",
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
            candidates=[dict(artifact_id="some-synthetic-artifact", run_id="run-no-art", lifecycle_state="candidate")],
        ),
        SimpleNamespace(tenant_id="tenant", user_id="user", auth_stub=False),
        "2026-09-08T07:00:00Z",
    )
    cand = pool["candidates"][0]
    assert cand["provenance"] == "unavailable"
    assert cand["has_real_receipt"] is False
