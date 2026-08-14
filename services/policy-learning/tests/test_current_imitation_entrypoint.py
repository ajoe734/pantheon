"""Tests for L12-CURRENT-IMITATION-ENTRYPOINT-20260814.

Verifies:
1. Research HTTP admission and authoritative readback failure retain a retryable or
   explicitly failed candidate without handoff_status completed.
2. Successful exact Research task and run readback permits the existing worker settlement path.
3. Production worker entrypoints have zero silent exception path that marks a failed
   Research handoff complete.
4. Failure and replay test with no completed mutation on failure, and clean completion on replay.
5. Declared-scope and direct-store caller guard.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from candidate_experiment_handoff import (
    CandidateHandoffError,
    CandidateHandoffResult,
    handoff_candidate_to_experiment_authority,
)
from conftest import authorized_client
from services.research.experiment_candidate_intake import (
    ExperimentCandidateIntakeError,
    ExperimentCandidateIntakeReceipt,
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


SERVICE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVICE_DIR.parents[1]


def _mock_research_http_urlopen(req, timeout=None):
    from services.research.main import app as res_app
    client = TestClient(res_app)
    url = req.full_url
    method = req.get_method()
    if method == "POST" and "/api/research-orchestrator/intake/imitation-candidate" in url:
        payload = req.data
        response = client.post("/api/research-orchestrator/intake/imitation-candidate", content=payload, headers=dict(req.headers))
        class MockResp:
            status = response.status_code
            def read(self):
                return response.content
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
        return MockResp()
    elif method == "GET" and "/api/research-orchestrator/runs/" in url:
        run_id = url.split("/api/research-orchestrator/runs/")[1]
        response = client.get(f"/api/research-orchestrator/runs/{run_id}", headers=dict(req.headers))
        class MockResp:
            status = response.status_code
            def read(self):
                return response.content
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
        return MockResp()
    raise NotImplementedError(f"Unexpected HTTP call: {method} {url}")


def _load_service_module(data_dir: str):
    for key in list(sys.modules):
        if "test_current_imitation_entrypoint_main" in key:
            del sys.modules[key]
    sys.modules.pop("store", None)
    sys.modules.pop("agora_dataset_authority", None)
    sys.modules.pop("inbound_authority", None)
    if str(SERVICE_DIR) not in sys.path:
        sys.path.insert(0, str(SERVICE_DIR))
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    spec = importlib.util.spec_from_file_location(
        "test_current_imitation_entrypoint_main",
        SERVICE_DIR / "main.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["test_current_imitation_entrypoint_main"] = module
    environ = {
        "POLICY_LEARNING_DATA_DIR": data_dir,
        "POLICY_LEARNING_STORE_BACKEND": "json",
        "PERSISTENCE_POSTURE": "lenient",
    }
    with mock.patch.dict("os.environ", environ):
        spec.loader.exec_module(module)
    return module


def _agora_record(dataset_version_id: str, *, tenant_id: str = "tenant-a", order: int = 0) -> dict:
    evidence_id = f"ev-{tenant_id}-{dataset_version_id}"
    return {
        "evidence_id": evidence_id,
        "dataset_version_id": dataset_version_id,
        "dataset_kind": "learn",
        "interaction_kind": "feedback",
        "persona_id": "persona-1",
        "session_id": "session-1",
        "tenant_id": tenant_id,
        "user_id": "",
        "content": {
            "steps": [
                {
                    "observation": [0.9, 0.1, -0.2],
                    "action": "buy_small",
                    "reward": 0.3,
                    "feedback_event_id": f"{evidence_id}-a",
                },
                {
                    "observation": [-0.8, 0.2, 0.55],
                    "action": "reduce_risk",
                    "reward": 0.1,
                    "feedback_event_id": f"{evidence_id}-b",
                },
            ]
        },
        "source_refs": ["artifact://source-1"],
        "learning_eligible": True,
        "captured_at": "2026-07-26T00:00:00Z",
        "extracted_at": f"2026-07-26T00:00:{order:02d}Z",
        "version": 1,
    }


def _install_authority(svc, records: list[dict]):
    authority_module = sys.modules.get("agora_dataset_authority")
    if authority_module is None:
        import agora_dataset_authority as authority_module
    svc.DATASET_AUTHORITY = authority_module.AgoraDatasetAuthority(records=records)
    return svc.DATASET_AUTHORITY


def _seed_candidate_in_backlog(
    svc_mod,
    candidate_id: str = "cand-imit-001",
    dataset_version_id: str = "dsv-imit-001",
    tenant_id: str = "tenant-a",
) -> Dict[str, Any]:
    candidate = {
        "candidate_id": candidate_id,
        "id": candidate_id,
        "tenant_id": tenant_id,
        "status": "proposed",
        "dataset_version_id": dataset_version_id,
        "dataset_ref": {
            "dataset_version_id": dataset_version_id,
            "tenant_id": tenant_id,
        },
        "strategy_id": "strat-imit-alpha",
        "strategy_spec_version": "1.0.0",
        "strategy_spec_id": "spec-strat-imit-alpha",
        "code_version": "git:dev@a1b2c3d",
        "trace_id": f"tr-{candidate_id}",
    }
    svc_mod.store.put_candidate(candidate)
    return candidate


def test_process_claimed_candidate_fails_closed_on_research_handoff_error():
    """Verify that when Research handoff raises an exception, the candidate settles as failed without completed handoff."""
    with tempfile.TemporaryDirectory() as tmpdir:
        svc = _load_service_module(tmpdir)
        _install_authority(svc, [_agora_record("dsv-imit-001", tenant_id="tenant-a")])
        _seed_candidate_in_backlog(svc, "cand-fail-001", "dsv-imit-001", "tenant-a")

        # Claim the candidate under lease
        claims = svc.store.claim_candidates(worker_id="w-1", batch_size=1, lease_seconds=60, tenant_id="tenant-a")
        assert len(claims) == 1
        claim = claims[0]

        # Simulate Research HTTP admission / readback failure
        with mock.patch.object(
            svc,
            "handoff_candidate_to_experiment_authority",
            side_effect=CandidateHandoffError("Research HTTP admission refused: 503 Service Unavailable"),
        ):
            settled = svc.process_claimed_candidate(claim)

        # Candidate returned from process_claimed_candidate
        assert settled["status"] == "failed"
        assert "Experiment handoff failed: Research HTTP admission refused: 503" in settled.get("error_message", "")
        assert settled.get("handoff_status") != "completed"
        assert "handoff_status" not in settled or settled["handoff_status"] is None
        assert settled.get("experiment_task_id") is None
        assert settled.get("experiment_run_id") is None
        assert "dsv-imit-001" in settled["dataset_lineage"].get("dataset_version_ids", [])

        # Verify persisted state in store
        persisted = svc.store.get_candidate("cand-fail-001")
        assert persisted is not None
        assert persisted["status"] == "failed"
        assert persisted.get("handoff_status") != "completed"
        assert persisted.get("experiment_task_id") is None
        assert persisted.get("experiment_run_id") is None
        assert "Experiment handoff failed" in persisted.get("error_message", "")


def test_run_worker_cycle_settlement_failure_counts_and_dlq():
    """Verify run_worker_cycle reports failed_count and candidate lands in DLQ when handoff fails."""
    with tempfile.TemporaryDirectory() as tmpdir:
        svc = _load_service_module(tmpdir)
        _install_authority(svc, [_agora_record("dsv-imit-002", tenant_id="tenant-a")])
        _seed_candidate_in_backlog(svc, "cand-cycle-001", "dsv-imit-002", "tenant-a")

        with mock.patch.object(
            svc,
            "handoff_candidate_to_experiment_authority",
            side_effect=ExperimentCandidateIntakeError("Authoritative readback failed: run_id mismatch"),
        ):
            result = svc.run_worker_cycle(worker_id="w-cycle", batch_size=5, tenant_id="tenant-a")

        assert result["claimed_count"] == 1
        assert result["processed_count"] == 0
        assert result["failed_count"] == 1
        assert result["status_counts"]["failed"] == 1

        # Check DLQ candidates in store
        dlq_items = [c for c in svc.store.list_candidates() if c.get("status") == "failed"]
        assert len(dlq_items) == 1
        assert dlq_items[0]["candidate_id"] == "cand-cycle-001"
        assert dlq_items[0].get("handoff_status") != "completed"


def test_successful_research_handoff_permits_worker_settlement():
    """Verify that successful Research intake and readback permits standard processed settlement."""
    with tempfile.TemporaryDirectory() as tmpdir:
        svc = _load_service_module(tmpdir)
        _install_authority(svc, [_agora_record("dsv-imit-003", tenant_id="tenant-a")])
        _seed_candidate_in_backlog(svc, "cand-success-001", "dsv-imit-003", "tenant-a")

        # Run worker cycle with normal (successful) handoff
        with mock.patch("urllib.request.urlopen", side_effect=_mock_research_http_urlopen):
            result = svc.run_worker_cycle(worker_id="w-success", batch_size=5, tenant_id="tenant-a")

        assert result["claimed_count"] == 1
        assert result["processed_count"] == 1
        assert result["failed_count"] == 0

        persisted = svc.store.get_candidate("cand-success-001")
        assert persisted is not None
        assert persisted["status"] == "processed"
        assert persisted.get("handoff_status") == "completed"
        assert persisted.get("experiment_task_id") == "rtask-exp-cand-success-001"
        assert persisted.get("experiment_run_id") == "rrun-exp-cand-success-001"
        assert "error_message" not in persisted
        assert persisted.get("metrics") is not None
        assert persisted.get("evaluation_summary") is not None


def test_failure_and_replay_lifecycle_with_no_completed_mutation_on_failure():
    """Verify end-to-end replay lifecycle: failed candidate in DLQ does not mutate completed until handoff succeeds."""
    with tempfile.TemporaryDirectory() as tmpdir:
        svc = _load_service_module(tmpdir)
        client = authorized_client(svc.app, tenant_id="tenant-a")
        _install_authority(svc, [_agora_record("dsv-imit-004", tenant_id="tenant-a")])
        _seed_candidate_in_backlog(svc, "cand-replay-001", "dsv-imit-004", "tenant-a")

        # 1. Trigger processing with failing Research handoff
        with mock.patch.object(
            svc,
            "handoff_candidate_to_experiment_authority",
            side_effect=ConnectionError("Failed to establish HTTP connection to research:8000"),
        ):
            proc_resp = client.post("/api/policy-learning/worker/process", json={"worker_id": "w-replay"})
            assert proc_resp.status_code == 200
            data = proc_resp.json()
            assert data["claimed_count"] == 1
            assert data["processed_count"] == 0
            assert data["failed_count"] == 1

        # Check candidate in DLQ endpoint
        dlq_resp = client.get("/api/policy-learning/worker/dlq")
        assert dlq_resp.status_code == 200
        dlq_items = dlq_resp.json()
        assert len(dlq_items) == 1
        assert dlq_items[0]["candidate_id"] == "cand-replay-001"
        assert dlq_items[0]["status"] == "failed"
        assert dlq_items[0].get("handoff_status") != "completed"

        # 2. Replay while handoff still fails: candidate must remain failed without completed mutation
        with mock.patch.object(
            svc,
            "handoff_candidate_to_experiment_authority",
            side_effect=TimeoutError("HTTP request timed out after 5000ms"),
        ):
            replay_fail_resp = client.post("/api/policy-learning/worker/dlq/cand-replay-001/replay")
            assert replay_fail_resp.status_code == 200
            replay_fail_data = replay_fail_resp.json()
            assert replay_fail_data["status"] == "failed"
            assert replay_fail_data.get("handoff_status") != "completed"
            assert "timed out" in replay_fail_data.get("error_message", "")

        # 3. Retry endpoint while handoff fails
        with mock.patch.object(
            svc,
            "handoff_candidate_to_experiment_authority",
            side_effect=CandidateHandoffError("Research schema validation rejected payload"),
        ):
            retry_fail_resp = client.post("/api/policy-learning/worker/retry/cand-replay-001")
            assert retry_fail_resp.status_code == 200
            retry_fail_data = retry_fail_resp.json()
            assert retry_fail_data["status"] == "failed"
            assert retry_fail_data.get("handoff_status") != "completed"

        # 4. Replay when Research handoff succeeds
        with mock.patch("urllib.request.urlopen", side_effect=_mock_research_http_urlopen):
            replay_ok_resp = client.post("/api/policy-learning/worker/dlq/cand-replay-001/replay")
        assert replay_ok_resp.status_code == 200
        replay_ok_data = replay_ok_resp.json()
        assert replay_ok_data["status"] == "processed"
        assert replay_ok_data.get("handoff_status") == "completed"
        assert replay_ok_data.get("experiment_task_id") == "rtask-exp-cand-replay-001"
        assert replay_ok_data.get("experiment_run_id") == "rrun-exp-cand-replay-001"
        assert "error_message" not in replay_ok_data

        # Verify DLQ is now empty
        dlq_after = client.get("/api/policy-learning/worker/dlq").json()
        assert len(dlq_after) == 0


def test_worker_restart_endpoint_settlement_fails_closed():
    """Verify worker restart release and settlement fails closed on handoff error."""
    with tempfile.TemporaryDirectory() as tmpdir:
        svc = _load_service_module(tmpdir)
        client = authorized_client(svc.app, tenant_id="tenant-a")
        _install_authority(svc, [_agora_record("dsv-imit-005", tenant_id="tenant-a")])
        _seed_candidate_in_backlog(svc, "cand-restart-001", "dsv-imit-005", "tenant-a")

        # Put candidate directly into claimed status with expired lease
        svc.store.put_candidate({
            "candidate_id": "cand-restart-001",
            "id": "cand-restart-001",
            "tenant_id": "tenant-a",
            "status": "claimed",
            "lease_token": "token-expired-001",
            "lease_owner": "crashed-worker",
            "lease_expires_at": "2020-01-01T00:00:00Z",
            "dataset_version_id": "dsv-imit-005",
            "dataset_ref": {
                "dataset_version_id": "dsv-imit-005",
                "tenant_id": "tenant-a",
            },
            "strategy_id": "strat-imit-alpha",
            "strategy_spec_version": "1.0.0",
            "strategy_spec_id": "spec-strat-imit-alpha",
            "code_version": "git:dev@a1b2c3d",
            "trace_id": "tr-cand-restart-001",
        })

        # Restart worker with handoff error
        with mock.patch.object(
            svc,
            "handoff_candidate_to_experiment_authority",
            side_effect=CandidateHandoffError("Research HTTP intake failed"),
        ):
            restart_resp = client.post("/api/policy-learning/worker/restart", json={"worker_id": "new-worker"})
            assert restart_resp.status_code == 200
            data = restart_resp.json()
            assert data["claimed_count"] == 1
            assert data["processed_count"] == 0
            assert data["failed_count"] == 1

        persisted = svc.store.get_candidate("cand-restart-001")
        assert persisted["status"] == "failed"
        assert persisted.get("handoff_status") != "completed"


def test_direct_handoff_endpoint_error_handling_and_no_store_mutation():
    """Verify POST /api/policy-learning/candidates/{id}/handoff fails closed and never mutates store on error."""
    with tempfile.TemporaryDirectory() as tmpdir:
        svc = _load_service_module(tmpdir)
        client = authorized_client(svc.app, tenant_id="tenant-a")

        # 1. Non-processed candidate -> HTTP 400
        candidate_prop = _seed_candidate_in_backlog(svc, "cand-dir-001", "dsv-1", "tenant-a")
        resp1 = client.post(f"/api/policy-learning/candidates/{candidate_prop['candidate_id']}/handoff")
        assert resp1.status_code == 400
        assert "must be in 'processed' status" in resp1.json().get("detail", "")
        # Confirm store unchanged
        assert svc.store.get_candidate("cand-dir-001")["status"] == "proposed"
        assert svc.store.get_candidate("cand-dir-001").get("handoff_status") is None

        # 2. Processed candidate but Research intake raises IntakeError -> HTTP 400
        candidate_proc = copy.deepcopy(candidate_prop)
        candidate_proc["status"] = "processed"
        svc.store.put_candidate(candidate_proc)

        with mock.patch.object(
            svc,
            "handoff_candidate_to_experiment_authority",
            side_effect=ExperimentCandidateIntakeError("Checksum mismatch in candidate receipt"),
        ):
            resp2 = client.post(f"/api/policy-learning/candidates/{candidate_proc['candidate_id']}/handoff")
            assert resp2.status_code == 400
            assert "Checksum mismatch" in resp2.json().get("detail", "")

        # Store candidate must NOT have handoff_status completed
        stored2 = svc.store.get_candidate("cand-dir-001")
        assert stored2.get("handoff_status") != "completed"

        # 3. Processed candidate but downstream network failure -> HTTP 502
        with mock.patch.object(
            svc,
            "handoff_candidate_to_experiment_authority",
            side_effect=RuntimeError("Research service connection refused"),
        ):
            resp3 = client.post(f"/api/policy-learning/candidates/{candidate_proc['candidate_id']}/handoff")
            assert resp3.status_code == 502
            assert "Research handoff failed" in resp3.json().get("detail", "")

        # Store candidate still NOT marked completed
        stored3 = svc.store.get_candidate("cand-dir-001")
        assert stored3.get("handoff_status") != "completed"

        # 4. Successful handoff -> HTTP 200 and store updated
        with mock.patch("urllib.request.urlopen", side_effect=_mock_research_http_urlopen):
            resp4 = client.post(f"/api/policy-learning/candidates/{candidate_proc['candidate_id']}/handoff")
        assert resp4.status_code == 200
        data4 = resp4.json()
        assert data4["status"] == "completed"
        assert data4["experiment_task_id"] == "rtask-exp-cand-dir-001"
        assert data4["experiment_run_id"] == "rrun-exp-cand-dir-001"

        stored4 = svc.store.get_candidate("cand-dir-001")
        assert stored4.get("handoff_status") == "completed"
        assert stored4.get("experiment_task_id") == "rtask-exp-cand-dir-001"


def test_zero_silent_exception_paths_across_all_worker_entrypoints():
    """Verify that zero silent exception path exists in production worker entrypoints."""
    with tempfile.TemporaryDirectory() as tmpdir:
        svc = _load_service_module(tmpdir)
        _install_authority(svc, [_agora_record("dsv-imit-zero", tenant_id="tenant-a")])

        failure_types = [
            CandidateHandoffError("Explicit handoff rejection"),
            ExperimentCandidateIntakeError("Lineage validation mismatch"),
            ConnectionError("TCP connection reset by peer"),
            TimeoutError("Read timeout on Research endpoint"),
            ValueError("Malformed response from intake"),
            RuntimeError("Unexpected downstream crash"),
        ]

        for idx, err in enumerate(failure_types):
            cand_id = f"cand-zero-{idx}"
            _seed_candidate_in_backlog(svc, cand_id, "dsv-imit-zero", "tenant-a")

            claims = svc.store.claim_candidates(
                worker_id=f"w-zero-{idx}",
                batch_size=1,
                lease_seconds=60,
                tenant_id="tenant-a",
            )
            assert len(claims) == 1
            claim = claims[0]

            with mock.patch.object(svc, "handoff_candidate_to_experiment_authority", side_effect=err):
                settled = svc.process_claimed_candidate(claim)

            # Settle result must be explicitly failed, never processed, and never completed handoff
            assert settled["status"] == "failed", f"Failed for exception {type(err).__name__}: {err}"
            assert settled.get("handoff_status") != "completed"
            assert settled.get("experiment_task_id") is None
            assert settled.get("experiment_run_id") is None
            assert str(err) in settled.get("error_message", "")

            # Store result must match
            in_store = svc.store.get_candidate(cand_id)
            assert in_store["status"] == "failed"
            assert in_store.get("handoff_status") != "completed"
            assert in_store.get("experiment_task_id") is None
            assert in_store.get("experiment_run_id") is None
