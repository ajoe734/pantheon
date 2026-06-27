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


def test_preview_eval_worker_tick_runs_queued_jobs(monkeypatch) -> None:
    module = _load_worker_module()
    requests = []

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
                "evaluation_proof_ref": "trainer-eval-proof:trn-1:teval-1",
            }
        )

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)

    result = module.run_tick(api_url="http://training-session-svc:8099", limit=5)

    assert result["jobs_found"] == 1
    assert result["job_ids"] == ["pvjob-001"]
    assert result["completed"] == 1
    assert result["failed"] == 0
    assert requests[0].full_url.endswith("/api/training/preview-jobs?status=queued&limit=5")
    assert requests[1].data is not None
