"""Tests for L12-MFC-R4-IMITATION-001 candidate handoff to Research authority.

Covers:
  1. Producer/consumer contract: Processed imitation candidate creates 1 ExperimentTask
     and 1 ExperimentRun preserving candidate/dataset/checksum lineage.
  2. Idempotent retry: Re-submitting handoff returns identical receipts and does not duplicate.
  3. Negative promotion assertion: POST /api/policy-learning/candidates/{id}/promote
     returns HTTP 409 Conflict with promotion_allowed=False and runtime_effect=none.
  4. Non-processed candidate rejection: Refuses handoff for candidates not in 'processed' state.
  5. HTTP endpoint integration tests for handoff and research intake.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from candidate_experiment_handoff import (
    CandidateHandoffError,
    handoff_candidate_to_experiment_authority,
)
from conftest import auth_headers, authorized_client
from services.research.experiment_candidate_intake import (
    ExperimentCandidateIntakeError,
    intake_imitation_candidate,
)
from services.research.experiments.models import (
    ExperimentRun,
    ExperimentRunStatus,
    ExperimentRuntimeEnv,
    ExperimentTask,
    ExperimentTaskPriority,
    ExperimentTaskStatus,
    ExperimentTaskType,
)
from services.research.main import app as res_app
from services.research.store import ResearchOrchestratorStore


PL_SERVICE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PL_SERVICE_DIR.parents[1]


def _load_pl_module(data_dir: str):
    for key in list(sys.modules):
        if "policy_learning_handoff_test" in key:
            del sys.modules[key]
    if str(PL_SERVICE_DIR) not in sys.path:
        sys.path.insert(0, str(PL_SERVICE_DIR))
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    spec = importlib.util.spec_from_file_location(
        "policy_learning_handoff_test_main",
        PL_SERVICE_DIR / "main.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["policy_learning_handoff_test_main"] = module
    environ = {
        "POLICY_LEARNING_DATA_DIR": data_dir,
        "POLICY_LEARNING_STORE_BACKEND": "json",
        "PERSISTENCE_POSTURE": "lenient",
    }
    with mock.patch.dict("os.environ", environ):
        spec.loader.exec_module(module)
    return module


def _sample_processed_candidate(candidate_id: str = "sic-test-handoff-001") -> dict:
    return {
        "candidate_id": candidate_id,
        "id": candidate_id,
        "tenant_id": "tenant-a",
        "status": "processed",
        "dataset_version_id": "ds-v20260813-001",
        "dataset_lineage": {
            "version_id": "ds-v20260813-001",
            "tenant_id": "tenant-a",
            "source": "agora_dataset_authority",
            "authoritative": True,
        },
        "artifact_checksum": "sha256:1111222233334444555566667777888899990000aaaabbbbccccddddeeeeffff",
        "dataset_mode": "agora",
        "metrics": {
            "action_match_rate": 0.942,
            "return_gap": 0.015,
            "kl_divergence": 0.038,
        },
        "evaluation_summary": {
            "action_match_rate": 0.942,
            "return_gap": 0.015,
            "kl_divergence": 0.038,
            "evaluator_id": "eval-bc-01",
            "evaluation_timestamp": "2026-08-13T12:00:00Z",
        },
        "training_evaluation": {
            "evaluator_id": "eval-bc-01",
            "action_match_rate": 0.942,
        },
        "strategy_id": "strat-imit-alpha",
        "strategy_spec_version": "1.2.0",
        "strategy_spec_id": "spec-strat-imit-alpha",
        "code_version": "git:dev@a1b2c3d",
        "trace_id": "tr-handoff-test-001",
    }


def test_producer_consumer_contract_lineage_preservation():
    """Verify processed candidate intake creates ExperimentTask and ExperimentRun with exact lineage."""
    with tempfile.TemporaryDirectory() as tmpdir:
        research_store = ResearchOrchestratorStore(tmpdir)
        candidate = _sample_processed_candidate("sic-contract-001")

        receipt = intake_imitation_candidate(candidate, store=research_store)

        assert receipt.candidate_id == "sic-contract-001"
        assert receipt.status == "intaken"
        assert receipt.task_id == "rtask-exp-sic-contract-001"
        assert receipt.run_id == "rrun-exp-sic-contract-001"

        # Verify ExperimentTask
        task = receipt.experiment_task
        assert isinstance(task, ExperimentTask)
        assert task.task_id == "rtask-exp-sic-contract-001"
        assert task.strategy_id == "strat-imit-alpha"
        assert task.strategy_spec_version == "1.2.0"
        assert task.dataset_version_id == "ds-v20260813-001"
        assert task.tenant_id == "tenant-a"
        assert task.strategy_spec_id == "spec-strat-imit-alpha"
        assert task.backend_id == "imitation"
        assert task.metadata["candidate_id"] == "sic-contract-001"
        assert task.metadata["artifact_checksum"] == "sha256:1111222233334444555566667777888899990000aaaabbbbccccddddeeeeffff"

        # Verify ExperimentRun
        run = receipt.experiment_run
        assert isinstance(run, ExperimentRun)
        assert run.run_id == "rrun-exp-sic-contract-001"
        assert run.task_id == task.task_id
        assert run.strategy_id == task.strategy_id
        assert run.strategy_spec_version == task.strategy_spec_version
        assert run.dataset_version_id == task.dataset_version_id
        assert run.tenant_id == task.tenant_id
        assert run.strategy_spec_id == task.strategy_spec_id
        assert run.backend_id == "imitation"
        assert run.status == ExperimentRunStatus.COMPLETED.value
        assert run.runtime_env == ExperimentRuntimeEnv.RESEARCH.value
        assert run.metadata["artifact_checksum"] == task.metadata["artifact_checksum"]
        assert run.metadata["metrics"]["action_match_rate"] == 0.942

        # Verify store persistence
        stored_task = research_store.get_task(task.task_id)
        stored_run = research_store.get_run(run.run_id)
        assert stored_task is not None
        assert stored_run is not None
        assert stored_task["task_id"] == task.task_id
        assert stored_run["run_id"] == run.run_id


def test_idempotent_retry_no_duplicate():
    """Verify repeated handoff calls return identical receipts without creating duplicates."""
    with tempfile.TemporaryDirectory() as tmpdir:
        research_store = ResearchOrchestratorStore(tmpdir)
        candidate = _sample_processed_candidate("sic-idempotent-001")

        result1 = handoff_candidate_to_experiment_authority(candidate, research_store=research_store)
        assert candidate["experiment_task_id"] == "rtask-exp-sic-idempotent-001"
        assert candidate["experiment_run_id"] == "rrun-exp-sic-idempotent-001"
        assert candidate["handoff_status"] == "completed"

        tasks_before = len(research_store.list_tasks())
        runs_before = len(research_store.list_runs())
        assert tasks_before == 1
        assert runs_before == 1

        # Second handoff attempt
        result2 = handoff_candidate_to_experiment_authority(candidate, research_store=research_store)

        tasks_after = len(research_store.list_tasks())
        runs_after = len(research_store.list_runs())
        assert tasks_after == 1
        assert runs_after == 1

        assert result1.experiment_task_id == result2.experiment_task_id
        assert result1.experiment_run_id == result2.experiment_run_id


def test_non_processed_candidate_rejection():
    """Verify handoff rejects non-processed candidates (proposed, claimed, failed, degraded)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        research_store = ResearchOrchestratorStore(tmpdir)

        for invalid_status in ("proposed", "claimed", "failed", "degraded"):
            candidate = _sample_processed_candidate(f"sic-invalid-{invalid_status}")
            candidate["status"] = invalid_status

            with pytest.raises(CandidateHandoffError) as exc_info:
                handoff_candidate_to_experiment_authority(candidate, research_store=research_store)

            assert "Candidate must be in 'processed' status" in str(exc_info.value)
            assert len(research_store.list_tasks()) == 0


def test_negative_promotion_assertion():
    """Verify policy-learning promote endpoint returns HTTP 409 and promotion_allowed=False."""
    with tempfile.TemporaryDirectory() as tmpdir:
        pl_mod = _load_pl_module(tmpdir)
        client = authorized_client(pl_mod.app, tenant_id="tenant-a")

        candidate = _sample_processed_candidate("sic-negative-001")
        pl_mod.store.put_candidate(candidate)

        resp = client.post(f"/api/policy-learning/candidates/{candidate['candidate_id']}/promote")
        assert resp.status_code == 409
        detail = resp.json().get("detail", {})
        assert detail.get("promotion_allowed") is False
        assert detail.get("runtime_effect") == "none"
        assert detail.get("production_training") == "fail_closed"


def test_http_endpoint_candidate_handoff_and_research_intake():
    """Verify policy-learning handoff and research intake HTTP endpoints."""
    with tempfile.TemporaryDirectory() as tmpdir:
        pl_mod = _load_pl_module(tmpdir)
        pl_client = authorized_client(pl_mod.app, tenant_id="tenant-a")
        res_client = TestClient(res_app)

        # 1. Test Research intake HTTP endpoint
        candidate = _sample_processed_candidate("sic-http-intake-001")
        intake_resp = res_client.post(
            "/api/research-orchestrator/intake/imitation-candidate",
            json=candidate,
        )
        assert intake_resp.status_code == 201
        intake_data = intake_resp.json()
        assert intake_data["candidate_id"] == "sic-http-intake-001"
        assert intake_data["task_id"] == "rtask-exp-sic-http-intake-001"
        assert intake_data["run_id"] == "rrun-exp-sic-http-intake-001"
        assert intake_data["status"] == "intaken"

        # 2. Test intake error for non-processed
        candidate_bad = _sample_processed_candidate("sic-http-intake-bad")
        candidate_bad["status"] = "proposed"
        intake_bad_resp = res_client.post(
            "/api/research-orchestrator/intake/imitation-candidate",
            json=candidate_bad,
        )
        assert intake_bad_resp.status_code == 400

        # 3. Test Policy Learning handoff HTTP endpoint
        # Seed candidate in PL store
        candidate_pl = _sample_processed_candidate("sic-http-pl-001")
        pl_mod.store.put_candidate(candidate_pl)

        handoff_resp = pl_client.post("/api/policy-learning/candidates/sic-http-pl-001/handoff")
        assert handoff_resp.status_code == 200
        handoff_data = handoff_resp.json()
        assert handoff_data["candidate_id"] == "sic-http-pl-001"
        assert handoff_data["experiment_task_id"] == "rtask-exp-sic-http-pl-001"
        assert handoff_data["experiment_run_id"] == "rrun-exp-sic-http-pl-001"
        assert handoff_data["status"] == "completed"
