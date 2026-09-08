"""Unit and contract tests for AuthenticStageAdapter negative controls and fail-closed validation.

Verifies:
  1. Missing backend result (execute_fn returning None) in real mode raises RuntimeError.
  2. Failed backend result (execute_fn returning dict with status='failed') raises RuntimeError.
  3. Non-terminal backend result (execute_fn returning dict with status='running') raises RuntimeError.
  4. Failed ResearchStageResult raises RuntimeError.
  5. Non-terminal ResearchStageResult raises RuntimeError.
  6. Simulation ResearchStageResult preserves provenance='simulation' and receipt mode='simulation',
     ensuring resolve_run_provenance never resolves 'real'.
  7. Missing backend artifacts in real mode raises RuntimeError.
  8. Invalid owner-emitted receipt (mismatched run_id, invalid spec_version, invalid mode) raises RuntimeError.
"""
from __future__ import annotations

import os
import uuid
from typing import Any, Dict
import pytest

from agora.research.dispatcher import (
    ALLOWLISTED_STAGE_BACKENDS,
    AuthenticResearchBackendClient,
    AuthenticStageAdapter,
    DefaultAllowlistedAdapter,
    ResearchDispatcher,
    ResearchStageResult,
    build_authentic_adapter_registry,
    build_canonical_research_backend_clients,
)
from agora.research.receipt import (
    ResearchExecutionReceipt,
    resolve_run_provenance,
)
from agora.research.store import MemoryResearchPlanStore


def _context(run_id: str = "run-neg-001", correlation_id: str = "corr-neg-001") -> Dict[str, Any]:
    return {
        "run_id": run_id,
        "correlation_id": correlation_id,
        "backend_mode": "real",
    }


def _stage(stage_type: str = "prototype_backtest") -> Dict[str, Any]:
    return {
        "stage_id": f"stage-{stage_type}",
        "stage_type": stage_type,
        "routing": {
            "backend_mode": "real",
            "preferred_backend": "vectorbt",
        },
    }


def _plan() -> Dict[str, Any]:
    return {
        "plan_id": "plan-neg-001",
        "strategy_id": "strat-neg-001",
        "correlation_id": "corr-neg-001",
    }


def test_missing_backend_output_raises_in_real_mode() -> None:
    """If execute_fn returns None in real mode, it must fail closed and raise RuntimeError."""
    adapter = AuthenticStageAdapter(
        stage_type="prototype_backtest",
        preferred_backend="vectorbt",
        mode="real",
        execute_fn=lambda *args, **kwargs: None,
    )
    with pytest.raises(RuntimeError, match="returned missing/empty result"):
        adapter.execute(
            stage=_stage(),
            plan=_plan(),
            context=_context(),
            downstream_key="key-1",
        )


def test_failed_status_dict_raises_in_real_mode() -> None:
    """If execute_fn returns a dict with status='failed', it must fail closed and raise RuntimeError."""
    adapter = AuthenticStageAdapter(
        stage_type="alpha_training",
        preferred_backend="qlib",
        mode="real",
        execute_fn=lambda *args, **kwargs: {"status": "failed", "error": "CUDA out of memory"},
    )
    with pytest.raises(RuntimeError, match="Authentic execution failed"):
        adapter.execute(
            stage=_stage("alpha_training"),
            plan=_plan(),
            context=_context(),
            downstream_key="key-2",
        )


def test_nonterminal_status_dict_raises_in_real_mode() -> None:
    """If execute_fn returns a dict with status='running', it must fail closed and raise RuntimeError."""
    adapter = AuthenticStageAdapter(
        stage_type="alpha_training",
        preferred_backend="qlib",
        mode="real",
        execute_fn=lambda *args, **kwargs: {"status": "running", "job_id": "job-qlib-99"},
    )
    with pytest.raises(RuntimeError, match="nonterminal status"):
        adapter.execute(
            stage=_stage("alpha_training"),
            plan=_plan(),
            context=_context(),
            downstream_key="key-3",
        )


def test_failed_stage_result_raises_in_real_mode() -> None:
    """If execute_fn returns a ResearchStageResult with outcome='failed', it must raise RuntimeError."""
    adapter = AuthenticStageAdapter(
        stage_type="prototype_backtest",
        preferred_backend="vectorbt",
        mode="real",
        execute_fn=lambda *args, **kwargs: ResearchStageResult(
            outcome="failed",
            provenance="real",
            error_message="Parameter sweep diverged",
        ),
    )
    with pytest.raises(RuntimeError, match="Authentic execution failed"):
        adapter.execute(
            stage=_stage(),
            plan=_plan(),
            context=_context(),
            downstream_key="key-4",
        )


def test_nonterminal_stage_result_raises_in_real_mode() -> None:
    """If execute_fn returns a ResearchStageResult with non-terminal outcome, it must raise RuntimeError."""
    adapter = AuthenticStageAdapter(
        stage_type="prototype_backtest",
        preferred_backend="vectorbt",
        mode="real",
        execute_fn=lambda *args, **kwargs: ResearchStageResult(
            outcome="inconclusive",
            provenance="real",
        ),
    )
    with pytest.raises(RuntimeError, match="Authentic execution failed"):
        adapter.execute(
            stage=_stage(),
            plan=_plan(),
            context=_context(),
            downstream_key="key-5",
        )


def test_simulation_stage_result_preserves_provenance_and_receipt_mode() -> None:
    """If execute_fn returns a simulation ResearchStageResult, provenance must remain 'simulation',
    receipt mode must be 'simulation', and resolve_run_provenance must never accept 'real'.
    """
    store = MemoryResearchPlanStore()
    run_id = "run-sim-preserve-001"
    corr_id = "corr-sim-001"

    adapter = AuthenticStageAdapter(
        stage_type="prototype_backtest",
        preferred_backend="vectorbt",
        mode="real",
        execute_fn=lambda *args, **kwargs: ResearchStageResult(
            outcome="succeeded",
            provenance="simulation",
            checksums={"artifact": "sha256:simartifact123"},
            backend_job_id="sim-job-42",
        ),
    )
    result = adapter.execute(
        stage=_stage(),
        plan=_plan(),
        context=_context(run_id=run_id, correlation_id=corr_id),
        downstream_key="key-6",
    )
    assert result.provenance == "simulation"
    assert result.receipt is not None
    assert result.receipt.mode == "simulation"

    # Verify store provenance resolution never upgrades simulation to real
    receipt_dict = result.receipt.to_dict()
    store.record_execution_receipt(receipt_dict)

    resolved_prov, _ = resolve_run_provenance(
        store,
        {
            "run_id": run_id,
            "execution_status": "succeeded",
            "provenance": result.provenance,
            "executor": result.receipt.executor,
            "correlation_id": corr_id,
        },
        expected_correlation_id=corr_id,
        expected_owner=result.receipt.executor,
    )
    assert resolved_prov == "simulation"
    assert resolved_prov != "real"


def test_absent_status_dict_raises_in_real_mode() -> None:
    """Dict with absent status/outcome must fail closed and raise RuntimeError."""
    adapter = AuthenticStageAdapter(
        stage_type="prototype_backtest",
        preferred_backend="vectorbt",
        mode="real",
        execute_fn=lambda *args, **kwargs: {"backend_reference": "vectorbt://runs/1"},
    )
    with pytest.raises(RuntimeError, match="returned invalid or nonterminal status 'absent'"):
        adapter.execute(
            stage=_stage(),
            plan=_plan(),
            context=_context(),
            downstream_key="key-absent",
        )


def test_cancelled_status_dict_raises_in_real_mode() -> None:
    """Dict with status='cancelled' must fail closed and raise RuntimeError."""
    adapter = AuthenticStageAdapter(
        stage_type="prototype_backtest",
        preferred_backend="vectorbt",
        mode="real",
        execute_fn=lambda *args, **kwargs: {"status": "cancelled", "error": "Job cancelled by operator"},
    )
    with pytest.raises(RuntimeError, match="Authentic execution failed"):
        adapter.execute(
            stage=_stage(),
            plan=_plan(),
            context=_context(),
            downstream_key="key-cancelled",
        )


def test_timed_out_status_dict_raises_in_real_mode() -> None:
    """Dict with status='timed_out' must fail closed and raise RuntimeError."""
    adapter = AuthenticStageAdapter(
        stage_type="prototype_backtest",
        preferred_backend="vectorbt",
        mode="real",
        execute_fn=lambda *args, **kwargs: {"status": "timed_out", "error": "Execution deadline exceeded"},
    )
    with pytest.raises(RuntimeError, match="Authentic execution failed"):
        adapter.execute(
            stage=_stage(),
            plan=_plan(),
            context=_context(),
            downstream_key="key-timedout",
        )


def test_unknown_status_dict_raises_in_real_mode() -> None:
    """Dict with status='unknown' must fail closed and raise RuntimeError."""
    adapter = AuthenticStageAdapter(
        stage_type="prototype_backtest",
        preferred_backend="vectorbt",
        mode="real",
        execute_fn=lambda *args, **kwargs: {"status": "unknown"},
    )
    with pytest.raises(RuntimeError, match="returned invalid or nonterminal status 'unknown'"):
        adapter.execute(
            stage=_stage(),
            plan=_plan(),
            context=_context(),
            downstream_key="key-unknown",
        )


def test_missing_backend_reference_in_real_mode_raises() -> None:
    """Authentic real execution without backend reference must raise."""
    adapter = AuthenticStageAdapter(
        stage_type="prototype_backtest",
        preferred_backend="vectorbt",
        mode="real",
        backend_reference=None,
        execute_fn=lambda *args, **kwargs: {
            "status": "succeeded",
            "artifact_digest": "sha256:abc123digest",
            "metrics": [{"name": "sharpe", "value": 1.5}],
        },
    )
    with pytest.raises(RuntimeError, match="missing backend reference"):
        adapter.execute(
            stage=_stage(),
            plan=_plan(),
            context=_context(),
            downstream_key="key-noref",
        )


def test_missing_artifact_digest_in_real_mode_raises() -> None:
    """Authentic real execution without artifact digest must raise."""
    adapter = AuthenticStageAdapter(
        stage_type="prototype_backtest",
        preferred_backend="vectorbt",
        mode="real",
        backend_reference="vectorbt://runs/1",
        execute_fn=lambda *args, **kwargs: {
            "status": "succeeded",
            "metrics": [{"name": "sharpe", "value": 1.5}],
        },
    )
    with pytest.raises(RuntimeError, match="missing genuine backend artifact digest"):
        adapter.execute(
            stage=_stage(),
            plan=_plan(),
            context=_context(),
            downstream_key="key-nodigest",
        )


def test_missing_metrics_in_real_mode_raises() -> None:
    """Authentic real execution without genuine backend metrics must raise."""
    adapter = AuthenticStageAdapter(
        stage_type="prototype_backtest",
        preferred_backend="vectorbt",
        mode="real",
        backend_reference="vectorbt://runs/1",
        execute_fn=lambda *args, **kwargs: {
            "status": "succeeded",
            "artifact_digest": "sha256:abc123digest",
            "metrics": [],
        },
    )
    with pytest.raises(RuntimeError, match="missing genuine backend metrics"):
        adapter.execute(
            stage=_stage(),
            plan=_plan(),
            context=_context(),
            downstream_key="key-nometrics",
        )


def test_synthetic_score_metrics_never_retained_from_super() -> None:
    """AuthenticStageAdapter must not retain synthetic score=1.0 metrics from DefaultAllowlistedAdapter."""
    adapter = AuthenticStageAdapter(
        stage_type="prototype_backtest",
        preferred_backend="vectorbt",
        mode="real",
        backend_reference="vectorbt://runs/1",
        execute_fn=lambda *args, **kwargs: {
            "status": "succeeded",
            "artifact_digest": "sha256:abc123digest",
            "metrics": [{"name": "custom_metric", "value": 42.0}],
        },
    )
    result = adapter.execute(
        stage=_stage(),
        plan=_plan(),
        context=_context(),
        downstream_key="key-metrics-clean",
    )
    assert len(result.metrics) == 1
    assert result.metrics[0]["name"] == "custom_metric"
    assert not any("score" in m.get("metric_name", "") for m in result.metrics)


def test_valid_owner_emitted_receipt_dict_succeeds() -> None:
    """A valid owner-emitted receipt dictionary must parse without NameError (VALID_MODES) and succeed."""
    run_id = "run-valid-receipt-001"
    corr_id = "corr-valid-receipt-001"
    adapter = AuthenticStageAdapter(
        stage_type="prototype_backtest",
        preferred_backend="vectorbt",
        mode="real",
        execute_fn=lambda *args, **kwargs: {
            "status": "succeeded",
            "backend_reference": "vectorbt://runs/42",
            "artifact_digest": "sha256:digest42",
            "metrics": [{"name": "sharpe", "value": 2.1}],
            "receipt": {
                "receipt_id": "rcpt-owner-42",
                "run_id": run_id,
                "executor": "vectorbt_executor",
                "mode": "real",
                "spec_version": "1.0",
                "correlation_id": corr_id,
                "completed_at": "2026-09-08T00:00:00Z",
                "backend_reference": "vectorbt://runs/42",
                "artifact_digest": "sha256:digest42",
            },
        },
    )
    result = adapter.execute(
        stage=_stage(),
        plan=_plan(),
        context=_context(run_id=run_id, correlation_id=corr_id),
        downstream_key="key-valid-receipt",
    )
    assert result.receipt is not None
    assert result.receipt.receipt_id == "rcpt-owner-42"
    assert result.receipt.mode == "real"
    assert result.receipt.run_id == run_id


def test_valid_owner_emitted_receipt_object_succeeds() -> None:
    """A valid ResearchExecutionReceipt object emitted by the backend must succeed without NameError."""
    run_id = "run-valid-obj-001"
    corr_id = "corr-valid-obj-001"
    receipt_obj = ResearchExecutionReceipt(
        receipt_id="rcpt-obj-99",
        run_id=run_id,
        executor="vectorbt_executor",
        mode="real",
        correlation_id=corr_id,
        completed_at="2026-09-08T00:00:00Z",
        backend_reference="vectorbt://runs/99",
        artifact_digest="sha256:digest99",
        spec_version="1.0",
    )
    adapter = AuthenticStageAdapter(
        stage_type="prototype_backtest",
        preferred_backend="vectorbt",
        mode="real",
        execute_fn=lambda *args, **kwargs: {
            "status": "succeeded",
            "backend_reference": "vectorbt://runs/99",
            "artifact_digest": "sha256:digest99",
            "metrics": [{"name": "sharpe", "value": 2.5}],
            "receipt": receipt_obj,
        },
    )
    result = adapter.execute(
        stage=_stage(),
        plan=_plan(),
        context=_context(run_id=run_id, correlation_id=corr_id),
        downstream_key="key-valid-obj",
    )
    assert result.receipt is not None
    assert result.receipt.receipt_id == "rcpt-obj-99"
    assert result.receipt.mode == "real"


def test_invalid_owner_emitted_receipt_mode_raises() -> None:
    """An owner-emitted receipt with invalid mode must fail closed and raise RuntimeError."""
    run_id = "run-invalid-mode-001"
    adapter = AuthenticStageAdapter(
        stage_type="prototype_backtest",
        preferred_backend="vectorbt",
        mode="real",
        execute_fn=lambda *args, **kwargs: {
            "status": "succeeded",
            "backend_reference": "vectorbt://runs/1",
            "artifact_digest": "sha256:abc",
            "metrics": [{"name": "sharpe", "value": 1.5}],
            "receipt": {
                "receipt_id": "rcpt-invalid-mode",
                "run_id": run_id,
                "executor": "vectorbt_executor",
                "mode": "unauthorized_mode",
                "spec_version": "1.0",
                "completed_at": "2026-09-08T00:00:00Z",
            },
        },
    )
    with pytest.raises(RuntimeError, match="invalid mode"):
        adapter.execute(
            stage=_stage(),
            plan=_plan(),
            context=_context(run_id=run_id),
            downstream_key="key-invalid-mode",
        )


def test_invalid_owner_emitted_receipt_run_id_raises() -> None:
    """An owner-emitted receipt with mismatched run_id must raise."""
    adapter = AuthenticStageAdapter(
        stage_type="prototype_backtest",
        preferred_backend="vectorbt",
        mode="real",
        execute_fn=lambda *args, **kwargs: {
            "status": "succeeded",
            "backend_reference": "vectorbt://runs/1",
            "artifact_digest": "sha256:abc",
            "metrics": [{"name": "sharpe", "value": 1.5}],
            "receipt": {
                "receipt_id": "rcpt-invalid",
                "run_id": "wrong-run-id",
                "executor": "vectorbt_executor",
                "mode": "real",
                "spec_version": "1.0",
                "completed_at": "2026-09-08T00:00:00Z",
            },
        },
    )
    with pytest.raises(RuntimeError, match="run_id mismatch"):
        adapter.execute(
            stage=_stage(),
            plan=_plan(),
            context=_context(run_id="expected-run-id"),
            downstream_key="key-8",
        )


def test_authentic_research_backend_client_absent_backend_fails_closed() -> None:
    """When base_url and backend_fn are absent, AuthenticResearchBackendClient must fail closed."""
    client = AuthenticResearchBackendClient(
        stage_type="prototype_backtest",
        preferred_backend="vectorbt",
        base_url=None,
        backend_fn=None,
    )
    with pytest.raises(RuntimeError, match="is absent"):
        client.execute(
            stage=_stage(),
            plan=_plan(),
            context=_context(),
            downstream_key="key-absent",
        )


def test_authentic_research_backend_client_unreachable_backend_fails_closed() -> None:
    """When base_url is unreachable, AuthenticResearchBackendClient must fail closed."""
    client = AuthenticResearchBackendClient(
        stage_type="prototype_backtest",
        preferred_backend="vectorbt",
        base_url="http://127.0.0.1:59998",
        backend_fn=None,
    )
    with pytest.raises(RuntimeError, match="submission/readback failed"):
        client.execute(
            stage=_stage(),
            plan=_plan(),
            context=_context(),
            downstream_key="key-unreachable",
        )


def test_authentic_research_backend_client_recording_transport_records_and_succeeds() -> None:
    """Recording transport captures actual submission, delivers genuine output, and validates receipt."""
    recorded_calls = []

    def recording_transport(req):
        import json
        body = json.loads(req.data.decode("utf-8")) if req.data else {}
        recorded_calls.append({
            "url": req.full_url,
            "headers": dict(req.headers),
            "body": body,
        })
        return {
            "status": "succeeded",
            "outcome": "succeeded",
            "backend_reference": f"vectorbt://runs/{body.get('run_id')}",
            "artifact_digest": "sha256:digest_vectorbt_genuine_12345",
            "metrics": [{"name": "sharpe_ratio", "value": 2.34, "category": "performance", "provenance": "real"}],
        }

    client = AuthenticResearchBackendClient(
        stage_type="prototype_backtest",
        preferred_backend="vectorbt",
        base_url="http://vectorbt-service:8000",
        transport=recording_transport,
    )
    run_id = "run-recorded-001"
    corr_id = "corr-recorded-001"
    result = client.execute(
        stage=_stage(),
        plan=_plan(),
        context=_context(run_id=run_id, correlation_id=corr_id),
        downstream_key="key-recorded",
    )

    assert len(recorded_calls) == 1
    assert recorded_calls[0]["url"] == "http://vectorbt-service:8000/stages/prototype_backtest/execute"
    assert recorded_calls[0]["body"]["run_id"] == run_id
    assert recorded_calls[0]["body"]["correlation_id"] == corr_id
    assert result["status"] == "succeeded"
    assert result["artifact_digest"] == "sha256:digest_vectorbt_genuine_12345"
    assert result["receipt"].artifact_digest == "sha256:digest_vectorbt_genuine_12345"
    assert result["receipt"].mode == "real"


def test_authentic_research_backend_client_empty_metrics_fails_closed() -> None:
    """If backend returns empty metrics, AuthenticResearchBackendClient must fail closed."""
    def empty_metrics_transport(req):
        return {
            "status": "succeeded",
            "outcome": "succeeded",
            "backend_reference": "vectorbt://runs/123",
            "artifact_digest": "sha256:digest123",
            "metrics": [],
        }

    client = AuthenticResearchBackendClient(
        stage_type="prototype_backtest",
        preferred_backend="vectorbt",
        base_url="http://vectorbt-service:8000",
        transport=empty_metrics_transport,
    )
    with pytest.raises(RuntimeError, match="missing genuine metrics"):
        client.execute(
            stage=_stage(),
            plan=_plan(),
            context=_context(),
            downstream_key="key-empty-metrics",
        )


def test_authentic_research_backend_client_failed_status_raises() -> None:
    """AuthenticResearchBackendClient must reject conflicting status='failed' even if outcome='succeeded'."""
    client = AuthenticResearchBackendClient(
        stage_type="prototype_backtest",
        preferred_backend="vectorbt",
        base_url="http://vectorbt-service:8000",
        transport=lambda req: {
            "status": "failed",
            "outcome": "succeeded",
            "backend_reference": "vectorbt://runs/failed",
            "artifact_digest": "sha256:digest_failed",
            "metrics": [{"name": "sharpe", "value": 0.0}],
            "error": "Execution aborted due to division by zero",
        },
    )
    with pytest.raises(RuntimeError, match="returned failure outcome"):
        client.execute(
            stage=_stage(),
            plan=_plan(),
            context=_context(),
            downstream_key="key-fail-status",
        )


def test_authentic_research_backend_client_running_status_raises() -> None:
    """AuthenticResearchBackendClient must reject non-terminal status='running' even if outcome='succeeded'."""
    client = AuthenticResearchBackendClient(
        stage_type="prototype_backtest",
        preferred_backend="vectorbt",
        base_url="http://vectorbt-service:8000",
        transport=lambda req: {
            "status": "running",
            "outcome": "succeeded",
            "backend_reference": "vectorbt://runs/running",
            "artifact_digest": "sha256:digest_running",
            "metrics": [{"name": "sharpe", "value": 0.5}],
        },
    )
    with pytest.raises(RuntimeError, match="returned nonterminal status"):
        client.execute(
            stage=_stage(),
            plan=_plan(),
            context=_context(),
            downstream_key="key-running-status",
        )


def test_authentic_research_backend_client_failed_outcome_raises() -> None:
    """AuthenticResearchBackendClient must reject outcome='failed' even if status='succeeded'."""
    client = AuthenticResearchBackendClient(
        stage_type="prototype_backtest",
        preferred_backend="vectorbt",
        base_url="http://vectorbt-service:8000",
        transport=lambda req: {
            "status": "succeeded",
            "outcome": "failed",
            "backend_reference": "vectorbt://runs/outcome-fail",
            "artifact_digest": "sha256:digest_outcome_fail",
            "metrics": [{"name": "sharpe", "value": 0.0}],
            "error": "Parameter constraints violated",
        },
    )
    with pytest.raises(RuntimeError, match="returned failure outcome"):
        client.execute(
            stage=_stage(),
            plan=_plan(),
            context=_context(),
            downstream_key="key-fail-outcome",
        )


def test_authentic_research_backend_client_running_outcome_raises() -> None:
    """AuthenticResearchBackendClient must reject non-terminal outcome='running' even if status='succeeded'."""
    client = AuthenticResearchBackendClient(
        stage_type="prototype_backtest",
        preferred_backend="vectorbt",
        base_url="http://vectorbt-service:8000",
        transport=lambda req: {
            "status": "succeeded",
            "outcome": "running",
            "backend_reference": "vectorbt://runs/outcome-running",
            "artifact_digest": "sha256:digest_outcome_running",
            "metrics": [{"name": "sharpe", "value": 0.5}],
        },
    )
    with pytest.raises(RuntimeError, match="returned nonterminal status"):
        client.execute(
            stage=_stage(),
            plan=_plan(),
            context=_context(),
            downstream_key="key-running-outcome",
        )


def test_authentic_research_backend_client_preserves_simulation_provenance_and_receipt_mode() -> None:
    """AuthenticResearchBackendClient and AuthenticStageAdapter must preserve reported provenance='simulation'."""
    body = {
        "status": "succeeded",
        "outcome": "succeeded",
        "provenance": "simulation",
        "backend_reference": "vectorbt://run-sim-test",
        "artifact_digest": "a" * 64,
        "metrics": [{"name": "sharpe", "value": 0.3}],
    }
    client = AuthenticResearchBackendClient(
        "prototype_backtest",
        "vectorbt",
        base_url="http://vectorbt-service:8000",
        transport=lambda req: body,
    )
    adapter = AuthenticStageAdapter("prototype_backtest", "vectorbt", execution_owner=client)
    result = adapter.execute(
        stage=_stage(),
        plan=_plan(),
        context=_context(),
        downstream_key="key-sim-prov",
    )
    assert result.outcome == "succeeded"
    assert result.provenance == "simulation"
    assert result.receipt is not None
    assert result.receipt.mode == "simulation"


def test_build_canonical_research_backend_clients_rejects_missing_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    """build_canonical_research_backend_clients must fail fast when mode='real' and no endpoints are configured."""
    for key in list(os.environ):
        if key.startswith("AGORA_RESEARCH_") and (key.endswith("_URL") or key == "AGORA_RESEARCH_BACKEND_URL"):
            monkeypatch.delenv(key, raising=False)

    with pytest.raises(RuntimeError, match="Backend execution owner for stage .* is absent"):
        build_canonical_research_backend_clients(mode="real")

    # With allow_missing_endpoints=True, it constructs clients without error
    clients = build_canonical_research_backend_clients(mode="real", allow_missing_endpoints=True)
    assert len(clients) == len(ALLOWLISTED_STAGE_BACKENDS)
