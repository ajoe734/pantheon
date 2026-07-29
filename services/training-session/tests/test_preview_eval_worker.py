from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


SERVICE_DIR = Path(__file__).resolve().parents[1]


def _load_worker_module():
    spec = importlib.util.spec_from_file_location(
        "training_session_preview_eval_worker_test",
        SERVICE_DIR / "preview_eval_worker.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["training_session_preview_eval_worker_test"] = module
    spec.loader.exec_module(module)
    return module


class _Response:
    def __init__(self, payload: object) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def test_preview_eval_worker_tick_runs_claimable_jobs(monkeypatch) -> None:
    module = _load_worker_module()
    monkeypatch.setenv("TRAINING_SESSION_WORKER_TOKEN", "worker:training-service")
    monkeypatch.setenv("TRAINING_SESSION_TENANT_ID", "tenant-test")
    requests = []
    heartbeats = []

    def fake_urlopen(request, timeout):  # noqa: ANN001
        del timeout
        requests.append(request)
        if request.get_method() == "GET":
            return _Response([{"job_id": "pvjob-001", "status": "queued"}])
        assert request.get_method() == "POST"
        assert request.full_url.endswith("/api/training/preview-jobs/pvjob-001/run")
        return _Response(
            {
                "job_id": "pvjob-001",
                "status": "completed",
                "reclaimed": True,
                "retryable": False,
                "evaluation_proof_ref": "trainer-eval-proof:trn-1:teval-1",
            }
        )

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)

    result = module.run_tick(
        api_url="http://training-session-svc:8099",
        limit=5,
        heartbeat=lambda: heartbeats.append("alive"),
    )

    assert result["jobs_found"] == 1
    assert result["job_ids"] == ["pvjob-001"]
    assert result["completed"] == 1
    assert result["reclaimed"] == 1
    assert result["retryable"] == 0
    assert result["failed"] == 0
    assert requests[0].full_url.endswith("/api/training/preview-jobs?status=claimable&limit=5")
    assert requests[0].get_header("Authorization") == "Bearer worker:training-service"
    assert requests[0].get_header("X-tenant-id") == "tenant-test"
    assert requests[0].get_header("X-pantheon-service") == "training-session-preview-worker"
    assert json.loads(requests[1].data) == {}
    assert heartbeats == ["alive", "alive"]


def test_preview_eval_worker_reports_retryable_failures(monkeypatch) -> None:
    module = _load_worker_module()
    monkeypatch.setenv("TRAINING_SESSION_WORKER_TOKEN", "worker:training-service")
    monkeypatch.setenv("TRAINING_SESSION_TENANT_ID", "tenant-test")

    def fake_urlopen(request, timeout):  # noqa: ANN001
        del timeout
        if request.get_method() == "GET":
            return _Response([{"job_id": "pvjob-retry", "status": "failed"}])
        return _Response(
            {
                "job_id": "pvjob-retry",
                "status": "failed",
                "reclaimed": False,
                "retryable": True,
            }
        )

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)

    result = module.run_tick(api_url="http://training-session-svc:8099", limit=5)

    assert result["jobs_found"] == 1
    assert result["completed"] == 0
    assert result["reclaimed"] == 0
    assert result["retryable"] == 1
    assert result["failed"] == 1
    assert result["errors"] == ["job_id=pvjob-retry unexpected_status='failed'"]


def test_preview_eval_worker_alive_marker_is_written(tmp_path) -> None:
    module = _load_worker_module()
    alive_path = tmp_path / "preview-worker-alive"

    module._write_alive(str(alive_path))

    marker = json.loads(alive_path.read_text(encoding="utf-8"))
    assert marker["status"] == "ok"
    assert marker["completed_at"].endswith("Z")
    assert module.DEFAULT_ALIVE_PATH == "/data/training-session/preview-worker-alive"


def test_preview_eval_worker_fails_closed_without_authority(monkeypatch) -> None:
    module = _load_worker_module()
    monkeypatch.delenv("TRAINING_SESSION_WORKER_TOKEN", raising=False)
    monkeypatch.delenv("TRAINING_SESSION_TENANT_ID", raising=False)

    try:
        module.fetch_claimable_jobs(api_url="http://training-session-svc:8099", limit=1)
    except RuntimeError as exc:
        assert "inbound authority is incomplete" in str(exc)
    else:
        raise AssertionError("worker request must fail closed without service/tenant authority")
