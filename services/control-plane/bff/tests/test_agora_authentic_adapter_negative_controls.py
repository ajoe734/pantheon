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

import uuid
from typing import Any, Dict
import pytest

from agora.research.dispatcher import (
    ALLOWLISTED_STAGE_BACKENDS,
    AuthenticStageAdapter,
    DefaultAllowlistedAdapter,
    ResearchDispatcher,
    ResearchStageResult,
    build_authentic_adapter_registry,
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


def test_missing_backend_artifacts_in_real_mode_raises() -> None:
    """Authentic real execution without backend reference or artifact digest must raise."""
    adapter = AuthenticStageAdapter(
        stage_type="prototype_backtest",
        preferred_backend="vectorbt",
        mode="real",
        backend_reference=None,
        execute_fn=lambda *args, **kwargs: {"metrics": [{"name": "sharpe", "value": 1.5}]},
    )
    with pytest.raises(RuntimeError, match="missing backend reference and artifact digest"):
        adapter.execute(
            stage=_stage(),
            plan=_plan(),
            context=_context(),
            downstream_key="key-7",
        )


def test_invalid_owner_emitted_receipt_raises() -> None:
    """An owner-emitted receipt with mismatched run_id or invalid spec_version must raise."""
    adapter = AuthenticStageAdapter(
        stage_type="prototype_backtest",
        preferred_backend="vectorbt",
        mode="real",
        execute_fn=lambda *args, **kwargs: {
            "backend_reference": "vectorbt://runs/1",
            "artifact_digest": "sha256:abc",
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
