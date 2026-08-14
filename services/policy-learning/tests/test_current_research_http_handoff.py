"""Tests for Current Imitation HTTP Research Handoff.

Verifies:
1. Candidate HTTP intake call to Research HTTP service and exact readback of task_id and run_id.
2. Direct ResearchOrchestratorStore import removal / independence in HTTP handoff path.
3. Replay / re-submitting candidate HTTP intake is idempotent and returns exact same receipt.
4. Error handling when Research HTTP intake returns an error or readback fails.
"""

from __future__ import annotations

import tempfile
from unittest import mock

import pytest
from fastapi.testclient import TestClient

import sys
from pathlib import Path

PL_SERVICE_DIR = Path(__file__).resolve().parents[1]
if str(PL_SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(PL_SERVICE_DIR))

from candidate_experiment_handoff import (
    CandidateHandoffError,
    handoff_candidate_to_experiment_authority,
)
from research_candidate_client import (
    ResearchCandidateClientError,
    post_imitation_candidate_intake_http,
)
from services.research.main import app as research_app
def test_direct_import_removal():
    """Assert candidate_experiment_handoff has no direct imports from services.research."""
    import candidate_experiment_handoff
    for attr in ("ResearchOrchestratorStore", "intake_imitation_candidate", "ExperimentCandidateIntakeReceipt"):
        assert not hasattr(candidate_experiment_handoff, attr), f"candidate_experiment_handoff still contains direct import {attr}"


def test_research_service_url_routing():
    """Verify default URL resolves to http://research-orchestrator-svc:8101 and respects env overrides."""
    from research_candidate_client import get_research_service_url

    with mock.patch.dict("os.environ", {}, clear=True):
        assert get_research_service_url() == "http://research-orchestrator-svc:8101"

    with mock.patch.dict("os.environ", {"RESEARCH_ORCHESTRATOR_URL": "http://custom-orchestrator:8101"}):
        assert get_research_service_url() == "http://custom-orchestrator:8101"

    with mock.patch.dict("os.environ", {"RESEARCH_SERVICE_URL": "http://override-svc:8200"}):
        assert get_research_service_url() == "http://override-svc:8200"


def _sample_processed_candidate(candidate_id: str = "sic-http-test-001") -> dict:
    return {
        "candidate_id": candidate_id,
        "id": candidate_id,
        "tenant_id": "tenant-a",
        "status": "processed",
        "dataset_version_id": "ds-v20260814-001",
        "dataset_lineage": {
            "version_id": "ds-v20260814-001",
            "tenant_id": "tenant-a",
            "source": "agora_dataset_authority",
            "authoritative": True,
        },
        "artifact_checksum": "sha256:1111222233334444555566667777888899990000aaaabbbbccccddddeeeeffff",
        "dataset_mode": "agora",
        "metrics": {
            "action_match_rate": 0.95,
            "return_gap": 0.01,
        },
        "evaluation_summary": {
            "action_match_rate": 0.95,
            "evaluator_id": "eval-01",
        },
        "strategy_id": "strat-http-test",
        "strategy_spec_version": "1.0.0",
        "strategy_spec_id": "spec-strat-http-test",
        "code_version": "git:dev",
        "trace_id": "tr-http-test-001",
    }


def test_candidate_experiment_handoff_via_http():
    """Verify handoff_candidate_to_experiment_authority works via HTTP."""
    client = TestClient(research_app)

    def _mock_urlopen(req, timeout=None):
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

    candidate = _sample_processed_candidate("sic-handoff-http-001")
    with mock.patch("urllib.request.urlopen", side_effect=_mock_urlopen):
        res = handoff_candidate_to_experiment_authority(
            candidate,
            research_url="http://research-svc:8200",
        )

    assert candidate["experiment_task_id"] == "rtask-exp-sic-handoff-http-001"
    assert candidate["experiment_run_id"] == "rrun-exp-sic-handoff-http-001"
    assert candidate["handoff_status"] == "completed"
    assert res.experiment_task_id == "rtask-exp-sic-handoff-http-001"
    assert res.experiment_run_id == "rrun-exp-sic-handoff-http-001"


def test_idempotent_replay_http_intake():
    """Verify re-submitting candidate HTTP intake returns exact same receipt (idempotent)."""
    client = TestClient(research_app)

    def _mock_urlopen(req, timeout=None):
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

    candidate = _sample_processed_candidate("sic-replay-http-001")
    with mock.patch("urllib.request.urlopen", side_effect=_mock_urlopen):
        receipt1 = post_imitation_candidate_intake_http(candidate, research_url="http://research-svc:8200")
        receipt2 = post_imitation_candidate_intake_http(candidate, research_url="http://research-svc:8200")

    assert receipt1.task_id == receipt2.task_id == "rtask-exp-sic-replay-http-001"
    assert receipt1.run_id == receipt2.run_id == "rrun-exp-sic-replay-http-001"


def test_http_intake_and_readback_error_handling():
    """Assert HTTP connection/status errors and readback mismatches raise ResearchCandidateClientError and CandidateHandoffError."""
    import urllib.error
    candidate = _sample_processed_candidate("sic-err-001")

    # 1. Connection error
    with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Connection refused")):
        with pytest.raises(CandidateHandoffError) as exc_info:
            handoff_candidate_to_experiment_authority(candidate, research_url="http://research-svc:8200")
        assert "HTTP intake failed" in str(exc_info.value)
        assert candidate.get("handoff_status") != "completed"

    # 2. Readback identity mismatch error
    def _mock_urlopen_mismatch(req, timeout=None):
        url = req.full_url
        method = req.get_method()
        if method == "POST":
            class MockResp:
                status = 201
                def read(self):
                    return b'{"task_id": "t1", "run_id": "r1", "candidate_id": "sic-err-001", "status": "intaken", "created_at": "2026-08-14T00:00:00Z"}'
                def __enter__(self):
                    return self
                def __exit__(self, *args):
                    pass
            return MockResp()
        elif method == "GET":
            class MockResp:
                status = 200
                def read(self):
                    return b'{"task_id": "t1", "run_id": "MISMATCHED_RUN_ID"}'
                def __enter__(self):
                    return self
                def __exit__(self, *args):
                    pass
            return MockResp()

    with mock.patch("urllib.request.urlopen", side_effect=_mock_urlopen_mismatch):
        with pytest.raises(ResearchCandidateClientError) as exc_info:
            post_imitation_candidate_intake_http(candidate, research_url="http://research-svc:8200")
        assert "readback identity mismatch" in str(exc_info.value)
