from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from strict_test_support import (
    FIXED_TRUSTED_NOW,
    make_fake_real_vectorbt_workflow,
    make_fake_target_precondition_reader,
    materialize_strict_authority,
    seed_changed_supported_controls,
)


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


def _load_service_module(tmp_path: Path):
    fixture = materialize_strict_authority(tmp_path / "authority")
    os.environ.update(fixture.environment())
    with mock.patch.dict("os.environ", fixture.environment(), clear=False):
        sys.path.insert(0, str(SERVICE_DIR))
        spec = importlib.util.spec_from_file_location(
            "training_session_preview_worker_integration_main",
            SERVICE_DIR / "main.py",
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules["training_session_preview_worker_integration_main"] = module
        spec.loader.exec_module(module)
    module.store = module.TrainingSessionStore(fixture.data_dir)
    module._trusted_now = lambda: FIXED_TRUSTED_NOW
    module.run_vectorbt_workflow = make_fake_real_vectorbt_workflow()
    module._read_target_precondition = make_fake_target_precondition_reader()
    return module, fixture


class _Response:
    def __init__(self, payload: object) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return self._body


class _TestClientResponse(_Response):
    def __init__(self, response) -> None:  # noqa: ANN001
        self._body = response.content


def test_preview_eval_worker_tick_runs_claimable_jobs(monkeypatch) -> None:
    module = _load_worker_module()
    monkeypatch.setenv("TRAINING_SESSION_WORKER_TOKEN", "worker:training-service")
    monkeypatch.setenv("TRAINING_SESSION_TENANT_ID", "tenant-test")
    requests = []
    heartbeats = []

    def fake_urlopen(request, timeout):  # noqa: ANN001
        del timeout
        requests.append(request)
        if request.full_url.endswith("/api/training/preview-jobs?status=claimable&limit=5"):
            return _Response([{"job_id": "pvjob-001", "status": "queued"}])
        if request.full_url.endswith("/api/training/preview-jobs/pvjob-001/run"):
            assert request.get_method() == "POST"
            return _Response(
                {
                    "job_id": "pvjob-001",
                    "session_id": "trn-1",
                    "status": "completed",
                    "terminalize_session": True,
                    "reclaimed": True,
                    "retryable": False,
                    "evaluation_proof_ref": "trainer-eval-proof:trn-1:teval-1",
                    "governance_gate_state": "passed",
                }
            )
        if request.full_url.endswith("/api/training/sessions/trn-1/complete"):
            assert request.get_method() == "POST"
            return _Response(
                {
                    "session_id": "trn-1",
                    "status": "completed",
                    "ended_at": "2026-08-09T14:00:00Z",
                }
            )
        assert request.full_url.endswith("/api/training/sessions/trn-1")
        assert request.get_method() == "GET"
        return _Response(
            {
                "session_id": "trn-1",
                "status": "completed",
                "ended_at": "2026-08-09T14:00:00Z",
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
    assert result["terminal_session_ids"] == ["trn-1"]
    assert result["terminal_sessions"] == [
        {
            "session_id": "trn-1",
            "status": "completed",
            "ended_at": "2026-08-09T14:00:00Z",
            "job_id": "pvjob-001",
            "evaluation_proof_ref": "trainer-eval-proof:trn-1:teval-1",
            "governance_gate_state": "passed",
        }
    ]
    assert requests[0].full_url.endswith("/api/training/preview-jobs?status=claimable&limit=5")
    assert requests[0].get_header("Authorization") == "Bearer worker:training-service"
    assert requests[0].get_header("X-tenant-id") == "tenant-test"
    assert requests[0].get_header("X-pantheon-service") == "training-session-preview-worker"
    assert json.loads(requests[1].data) == {}
    assert json.loads(requests[2].data) == {}
    assert [request.get_method() for request in requests] == ["GET", "POST", "POST", "GET"]
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
    assert result["terminal_session_ids"] == []


def test_preview_eval_worker_rejects_nonterminal_session_readback(monkeypatch) -> None:
    module = _load_worker_module()
    monkeypatch.setenv("TRAINING_SESSION_WORKER_TOKEN", "worker:training-service")
    monkeypatch.setenv("TRAINING_SESSION_TENANT_ID", "tenant-test")

    def fake_urlopen(request, timeout):  # noqa: ANN001
        del timeout
        if "preview-jobs?" in request.full_url:
            return _Response([{"job_id": "pvjob-active", "status": "queued"}])
        if request.full_url.endswith("/preview-jobs/pvjob-active/run"):
            return _Response(
                {
                    "job_id": "pvjob-active",
                    "session_id": "trn-active",
                    "status": "completed",
                    "terminalize_session": True,
                }
            )
        if request.full_url.endswith("/sessions/trn-active/complete"):
            return _Response({"session_id": "trn-active", "status": "completed"})
        return _Response({"session_id": "trn-active", "status": "active", "ended_at": None})

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)

    result = module.run_tick(api_url="http://training-session-svc:8099", limit=1)

    assert result["completed"] == 1
    assert result["failed"] == 1
    assert result["terminal_session_ids"] == []
    assert result["errors"] == [
        "job_id=pvjob-active terminal_session_error=session_id=trn-active "
        "did not reach persisted terminal state: status='active' ended_at=''"
    ]


def test_preview_eval_worker_leaves_normal_completed_preview_session_active(monkeypatch) -> None:
    worker = _load_worker_module()
    monkeypatch.setenv("TRAINING_SESSION_WORKER_TOKEN", "worker:training-service")
    monkeypatch.setenv("TRAINING_SESSION_TENANT_ID", "tenant-test")
    requests = []

    def fake_urlopen(request, timeout):  # noqa: ANN001
        del timeout
        requests.append(request)
        if "preview-jobs?" in request.full_url:
            return _Response([{"job_id": "pvjob-preview-only", "status": "queued"}])
        assert request.full_url.endswith("/preview-jobs/pvjob-preview-only/run")
        return _Response(
            {
                "job_id": "pvjob-preview-only",
                "session_id": "trn-preview-only",
                "status": "completed",
                "terminalize_session": False,
            }
        )

    monkeypatch.setattr(worker.urllib.request, "urlopen", fake_urlopen)

    result = worker.run_tick(api_url="http://training-session-svc:8099", limit=1)

    assert result["completed"] == 1
    assert result["failed"] == 0
    assert result["terminal_session_ids"] == []
    assert [request.get_method() for request in requests] == ["GET", "POST"]


def test_preview_eval_worker_persists_terminal_session_for_learning_readback(
    monkeypatch,
    tmp_path,
) -> None:
    worker = _load_worker_module()
    service, fixture = _load_service_module(tmp_path)
    client = TestClient(service.app)
    monkeypatch.setenv("TRAINING_SESSION_WORKER_TOKEN", "worker:training-service")
    monkeypatch.setenv("TRAINING_SESSION_TENANT_ID", "tenant-test")

    created = client.post(
        "/api/training/sessions",
        json={
            "persona_id": "persona-alpha",
            "objective": "Teach the bounded momentum candidate",
            "actor_id": "operator-1",
        },
    )
    assert created.status_code == 201
    session_id = created.json()["session_id"]
    seed_changed_supported_controls(service, session_id)
    queued = client.post(
        f"/api/training/sessions/{session_id}/preview-jobs",
        json={
            "mode": "refresh",
            "requested_by": "operator-1",
            "terminalize_session": True,
        },
        headers={"Idempotency-Key": "minimum-teaching-command-001"},
    )
    assert queued.status_code == 201
    assert queued.json()["terminalize_session"] is True

    def service_urlopen(request, timeout):  # noqa: ANN001
        del timeout
        parsed_path = request.full_url.removeprefix("http://training-session-svc:8099")
        headers = dict(request.header_items())
        response = client.request(
            request.get_method(),
            parsed_path,
            content=request.data,
            headers=headers,
        )
        assert response.status_code < 400, response.text
        return _TestClientResponse(response)

    monkeypatch.setattr(worker.urllib.request, "urlopen", service_urlopen)

    result = worker.run_tick(api_url="http://training-session-svc:8099", limit=1)

    assert result["failed"] == 0
    assert result["terminal_session_ids"] == [session_id]
    assert result["terminal_sessions"][0]["evaluation_proof_ref"].startswith(
        f"trainer-eval-proof:{session_id}:"
    )
    persisted = service.TrainingSessionStore(fixture.data_dir).get_session(session_id)
    assert persisted is not None
    assert persisted["status"] == "completed"
    assert persisted["ended_at"] == FIXED_TRUSTED_NOW.isoformat().replace("+00:00", "Z")


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


class _FakeLoopWriter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def record_heartbeat(self, loop_id, **kwargs):  # noqa: ANN001
        self.calls.append(("record_heartbeat", {"loop_id": loop_id, **kwargs}))

    async def record_tick(self, loop_id, **kwargs):  # noqa: ANN001
        self.calls.append(("record_tick", {"loop_id": loop_id, **kwargs}))

    async def record_success(self, loop_id, **kwargs):  # noqa: ANN001
        self.calls.append(("record_success", {"loop_id": loop_id, **kwargs}))

    async def record_failure(self, loop_id, reason, **kwargs):  # noqa: ANN001
        self.calls.append(("record_failure", {"loop_id": loop_id, "reason": reason, **kwargs}))

    async def record_repair(self, loop_id, reason, **kwargs):  # noqa: ANN001
        self.calls.append(("record_repair", {"loop_id": loop_id, "reason": reason, **kwargs}))


def test_build_loop_writer_returns_none_without_database_url(monkeypatch) -> None:
    module = _load_worker_module()
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert module.build_loop_writer() is None


def test_preview_eval_worker_writes_gap_f05_observation_on_success(monkeypatch) -> None:
    module = _load_worker_module()
    monkeypatch.setenv("TRAINING_SESSION_WORKER_TOKEN", "worker:training-service")
    monkeypatch.setenv("TRAINING_SESSION_TENANT_ID", "tenant-test")
    loop_writer = _FakeLoopWriter()

    def fake_urlopen(request, timeout):  # noqa: ANN001
        del timeout
        if "preview-jobs?" in request.full_url:
            return _Response([{"job_id": "pvjob-obs", "status": "queued"}])
        assert request.full_url.endswith("/preview-jobs/pvjob-obs/run")
        return _Response(
            {
                "job_id": "pvjob-obs",
                "session_id": "trn-obs",
                "status": "completed",
                "terminalize_session": False,
                "preview": {
                    "evaluation_result": {
                        "consult_request_id": "creq-teach-obs",
                        "consultation_receipt": {
                            "consult_request_id": "creq-teach-obs",
                            "session_id": "trn-obs",
                            "status": "submitted",
                        },
                    }
                },
            }
        )

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)

    result = module.run_tick(
        api_url="http://training-session-svc:8099",
        limit=1,
        loop_writer=loop_writer,
    )

    assert result["failed"] == 0
    assert result["consult_request_ids"] == ["creq-teach-obs"]

    kinds = [call[0] for call in loop_writer.calls]
    assert kinds == ["record_heartbeat", "record_success"]
    heartbeat_call = loop_writer.calls[0][1]
    assert heartbeat_call["loop_id"] == module.LOOP_ID == "persona_teaching"
    success_call = loop_writer.calls[1][1]
    assert success_call["loop_id"] == "persona_teaching"
    assert success_call["evidence_refs"][1:] == ["consult-request:creq-teach-obs"]
    assert success_call["evidence_refs"][0].startswith("training-session://preview-eval-ticks/")
    assert success_call["payload"]["job_ids"] == ["pvjob-obs"]


def test_preview_eval_worker_writes_gap_f05_failure_truth(monkeypatch) -> None:
    module = _load_worker_module()
    monkeypatch.setenv("TRAINING_SESSION_WORKER_TOKEN", "worker:training-service")
    monkeypatch.setenv("TRAINING_SESSION_TENANT_ID", "tenant-test")
    loop_writer = _FakeLoopWriter()

    def fake_urlopen(request, timeout):  # noqa: ANN001
        del timeout
        if request.get_method() == "GET":
            return _Response([{"job_id": "pvjob-fail", "status": "failed"}])
        return _Response({"job_id": "pvjob-fail", "status": "failed", "retryable": True})

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)

    result = module.run_tick(
        api_url="http://training-session-svc:8099",
        limit=1,
        loop_writer=loop_writer,
    )

    assert result["failed"] == 1
    kinds = [call[0] for call in loop_writer.calls]
    assert kinds == ["record_heartbeat", "record_failure"]
    failure_call = loop_writer.calls[1][1]
    assert failure_call["loop_id"] == "persona_teaching"
    assert "pvjob-fail" in failure_call["reason"]


def test_preview_eval_worker_degrades_health_on_http_401(monkeypatch, tmp_path) -> None:
    import io
    import urllib.error

    module = _load_worker_module()
    monkeypatch.setenv("TRAINING_SESSION_WORKER_TOKEN", "worker:invalid-token")
    monkeypatch.setenv("TRAINING_SESSION_TENANT_ID", "tenant-test")

    def fake_urlopen_401(request, timeout):  # noqa: ANN001
        del timeout
        fp = io.BytesIO(b'{"error":{"code":"AUTH_UNAUTHORIZED","message":"Invalid token"}}')
        raise urllib.error.HTTPError(
            request.full_url,
            401,
            "Unauthorized",
            {"Content-Type": "application/json"},
            fp,
        )

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen_401)

    result = module.run_tick(api_url="http://training-session-svc:8099", limit=1)

    assert result["failed"] == 1
    assert result["jobs_found"] == 0
    assert len(result["errors"]) == 1
    assert "fetch_claimable_jobs http_error=401" in result["errors"][0]

    alive_path = tmp_path / "preview-worker-alive"
    assert not alive_path.exists()




def _write_worker_credential(path: Path, value: str) -> None:
    path.write_text(value, encoding="utf-8")
    path.chmod(0o600)


def test_preview_worker_headers_follow_refreshed_credential_file_without_restart(
    monkeypatch, tmp_path
) -> None:
    module = _load_worker_module()
    credential = tmp_path / "TRAINING_SESSION_WORKER_TOKEN"
    monkeypatch.setenv("TRAINING_SESSION_WORKER_TOKEN_FILE", str(credential))
    monkeypatch.setenv("TRAINING_SESSION_WORKER_TOKEN", "stale-env-token")
    monkeypatch.setenv("TRAINING_SESSION_TENANT_ID", "tenant-dev")
    monkeypatch.delenv("TRAINING_SESSION_WORKER_SERVICE_ID", raising=False)

    _write_worker_credential(credential, "aaa.bbb.first")
    first = module._authority_headers()
    # Atomic replacement as the issuer does; the same process sees the new value.
    replacement = tmp_path / ".rotating"
    _write_worker_credential(replacement, "aaa.bbb.second")
    os.replace(replacement, credential)
    second = module._authority_headers()

    assert first["Authorization"] == "Bearer aaa.bbb.first"
    assert second["Authorization"] == "Bearer aaa.bbb.second"
    assert second["X-Tenant-Id"] == "tenant-dev"
    assert second["X-Pantheon-Service"] == "training-session-preview-worker"


def test_preview_worker_credential_file_fails_closed_without_env_fallback(
    monkeypatch, tmp_path
) -> None:
    module = _load_worker_module()
    credential = tmp_path / "TRAINING_SESSION_WORKER_TOKEN"
    monkeypatch.setenv("TRAINING_SESSION_WORKER_TOKEN_FILE", str(credential))
    monkeypatch.setenv("TRAINING_SESSION_WORKER_TOKEN", "stale-env-token")
    monkeypatch.setenv("TRAINING_SESSION_TENANT_ID", "tenant-dev")

    def denied() -> None:
        try:
            module._authority_headers()
        except RuntimeError as exc:
            assert "credential unavailable" in str(exc)
        else:
            raise AssertionError("worker must fail closed")

    denied()  # absent / revoked (issuer unlinks the file)
    _write_worker_credential(credential, "has whitespace")  # malformed
    denied()
    _write_worker_credential(credential, "aaa.bbb.ccc")
    credential.chmod(0o644)  # unsafe mode
    denied()


def test_terminal_session_recovery_replays_after_lost_run_response(
    monkeypatch,
    tmp_path,
) -> None:
    import urllib.error

    worker = _load_worker_module()
    service, fixture = _load_service_module(tmp_path)
    client = TestClient(service.app)
    monkeypatch.setenv("TRAINING_SESSION_WORKER_TOKEN", "worker:training-service")
    monkeypatch.setenv("TRAINING_SESSION_TENANT_ID", "tenant-test")

    created = client.post(
        "/api/training/sessions",
        json={
            "persona_id": "persona-recovery-loss",
            "objective": "Prove lost run response recovery",
            "actor_id": "operator-1",
        },
    )
    assert created.status_code == 201
    session_id = created.json()["session_id"]
    seed_changed_supported_controls(service, session_id)
    queued = client.post(
        f"/api/training/sessions/{session_id}/preview-jobs",
        json={
            "mode": "refresh",
            "requested_by": "operator-1",
            "terminalize_session": True,
        },
        headers={"Idempotency-Key": "recovery-loss-key-001"},
    )
    assert queued.status_code == 201
    job_id = queued.json()["job_id"]

    drop_run_response = True

    def service_urlopen(request, timeout):  # noqa: ANN001
        del timeout
        nonlocal drop_run_response
        parsed_path = request.full_url.removeprefix("http://training-session-svc:8099")
        headers = dict(request.header_items())
        response = client.request(
            request.get_method(),
            parsed_path,
            content=request.data,
            headers=headers,
        )
        assert response.status_code < 400, response.text
        if drop_run_response and parsed_path.endswith(f"/api/training/preview-jobs/{job_id}/run"):
            drop_run_response = False
            raise urllib.error.URLError("synthetic response lost after owner persisted completed job")
        return _TestClientResponse(response)

    monkeypatch.setattr(worker.urllib.request, "urlopen", service_urlopen)

    # Tick 1: evaluation succeeds on server, but worker encounters lost response
    tick1 = worker.run_tick(api_url="http://training-session-svc:8099", limit=5)
    assert tick1["jobs_found"] == 1
    assert tick1["job_ids"] == [job_id]
    assert tick1["completed"] == 0
    assert tick1["replayed"] == 0
    assert tick1["failed"] == 1
    assert any("synthetic response lost after owner persisted completed job" in err for err in tick1["errors"])
    assert tick1["terminal_session_ids"] == []

    # Durable state after tick 1: job is completed in store, but session is still active
    store = service.TrainingSessionStore(fixture.data_dir)
    persisted_job = store.get_preview_job(job_id)
    assert persisted_job is not None
    assert persisted_job["status"] == "completed"
    assert persisted_job["terminalize_session"] is True
    persisted_session = store.get_session(session_id)
    assert persisted_session is not None
    assert persisted_session["status"] == "active"
    assert persisted_session.get("ended_at") is None

    # Tick 2: job is claimed as replayed recovery; worker completes the terminal session
    tick2 = worker.run_tick(api_url="http://training-session-svc:8099", limit=5)
    assert tick2["jobs_found"] == 1
    assert tick2["job_ids"] == [job_id]
    assert tick2["completed"] == 1
    assert tick2["replayed"] == 1
    assert tick2["failed"] == 0
    assert tick2["terminal_session_ids"] == [session_id]

    persisted_session_after = store.get_session(session_id)
    assert persisted_session_after["status"] == "completed"
    assert persisted_session_after.get("ended_at") == FIXED_TRUSTED_NOW.isoformat().replace("+00:00", "Z")

    # Tick 3: session is terminal, job is no longer claimable
    tick3 = worker.run_tick(api_url="http://training-session-svc:8099", limit=5)
    assert tick3["jobs_found"] == 0
    assert tick3["completed"] == 0
    assert tick3["failed"] == 0


def test_terminal_session_recovery_retries_transient_complete_failure_across_restart(
    monkeypatch,
    tmp_path,
) -> None:
    import io
    import urllib.error

    worker = _load_worker_module()
    service, fixture = _load_service_module(tmp_path)
    client = TestClient(service.app)
    monkeypatch.setenv("TRAINING_SESSION_WORKER_TOKEN", "worker:training-service")
    monkeypatch.setenv("TRAINING_SESSION_TENANT_ID", "tenant-test")

    created = client.post(
        "/api/training/sessions",
        json={
            "persona_id": "persona-transient-fail",
            "objective": "Prove transient complete failure restart recovery",
            "actor_id": "operator-1",
        },
    )
    assert created.status_code == 201
    session_id = created.json()["session_id"]
    seed_changed_supported_controls(service, session_id)
    queued = client.post(
        f"/api/training/sessions/{session_id}/preview-jobs",
        json={
            "mode": "refresh",
            "requested_by": "operator-1",
            "terminalize_session": True,
        },
        headers={"Idempotency-Key": "recovery-transient-key-001"},
    )
    assert queued.status_code == 201
    job_id = queued.json()["job_id"]

    fail_complete = True

    def service_urlopen(request, timeout):  # noqa: ANN001
        del timeout
        nonlocal fail_complete
        parsed_path = request.full_url.removeprefix("http://training-session-svc:8099")
        headers = dict(request.header_items())
        if fail_complete and parsed_path.endswith(f"/api/training/sessions/{session_id}/complete"):
            fail_complete = False
            fp = io.BytesIO(b'{"detail":"transient backend 503"}')
            raise urllib.error.HTTPError(
                request.full_url,
                503,
                "Service Unavailable",
                {"Content-Type": "application/json"},
                fp,
            )
        response = client.request(
            request.get_method(),
            parsed_path,
            content=request.data,
            headers=headers,
        )
        assert response.status_code < 400, response.text
        return _TestClientResponse(response)

    monkeypatch.setattr(worker.urllib.request, "urlopen", service_urlopen)

    # Tick 1: run_job succeeds, complete_session encounters transient 503
    tick1 = worker.run_tick(api_url="http://training-session-svc:8099", limit=5)
    assert tick1["jobs_found"] == 1
    assert tick1["completed"] == 1
    assert tick1["failed"] == 1
    assert tick1["terminal_session_ids"] == []
    assert any("503" in err for err in tick1["errors"])

    store = service.TrainingSessionStore(fixture.data_dir)
    session_after_tick1 = store.get_session(session_id)
    assert session_after_tick1["status"] == "active"

    # Simulate worker restart by reloading worker module
    restarted_worker = _load_worker_module()
    monkeypatch.setattr(restarted_worker.urllib.request, "urlopen", service_urlopen)

    # Tick 2: recovered across restart; complete_session now succeeds without re-evaluating
    tick2 = restarted_worker.run_tick(api_url="http://training-session-svc:8099", limit=5)
    assert tick2["jobs_found"] == 1
    assert tick2["completed"] == 1
    assert tick2["replayed"] == 1
    assert tick2["failed"] == 0
    assert tick2["terminal_session_ids"] == [session_id]

    session_after_tick2 = store.get_session(session_id)
    assert session_after_tick2["status"] == "completed"
    assert session_after_tick2.get("ended_at") == FIXED_TRUSTED_NOW.isoformat().replace("+00:00", "Z")


def test_terminal_session_recovery_negative_preview_only_stays_active(
    monkeypatch,
    tmp_path,
) -> None:
    worker = _load_worker_module()
    service, fixture = _load_service_module(tmp_path)
    client = TestClient(service.app)
    monkeypatch.setenv("TRAINING_SESSION_WORKER_TOKEN", "worker:training-service")
    monkeypatch.setenv("TRAINING_SESSION_TENANT_ID", "tenant-test")

    created = client.post(
        "/api/training/sessions",
        json={
            "persona_id": "persona-preview-only",
            "objective": "Preview only should remain active",
            "actor_id": "operator-1",
        },
    )
    assert created.status_code == 201
    session_id = created.json()["session_id"]
    seed_changed_supported_controls(service, session_id)
    queued = client.post(
        f"/api/training/sessions/{session_id}/preview-jobs",
        json={
            "mode": "refresh",
            "requested_by": "operator-1",
            "terminalize_session": False,
        },
        headers={"Idempotency-Key": "preview-only-key-001"},
    )
    assert queued.status_code == 201

    def service_urlopen(request, timeout):  # noqa: ANN001
        del timeout
        parsed_path = request.full_url.removeprefix("http://training-session-svc:8099")
        headers = dict(request.header_items())
        response = client.request(
            request.get_method(),
            parsed_path,
            content=request.data,
            headers=headers,
        )
        assert response.status_code < 400, response.text
        return _TestClientResponse(response)

    monkeypatch.setattr(worker.urllib.request, "urlopen", service_urlopen)

    tick1 = worker.run_tick(api_url="http://training-session-svc:8099", limit=5)
    assert tick1["jobs_found"] == 1
    assert tick1["completed"] == 1
    assert tick1["terminal_session_ids"] == []
    assert tick1["failed"] == 0

    store = service.TrainingSessionStore(fixture.data_dir)
    assert store.get_session(session_id)["status"] == "active"

    # Next tick: completed job with terminalize_session=False is NOT claimable
    tick2 = worker.run_tick(api_url="http://training-session-svc:8099", limit=5)
    assert tick2["jobs_found"] == 0
    assert store.get_session(session_id)["status"] == "active"


def test_terminal_session_recovery_tenant_isolation(
    tmp_path,
) -> None:
    service, _fixture = _load_service_module(tmp_path)
    client = TestClient(service.app)

    # Create session as tenant-alpha
    created = client.post(
        "/api/training/sessions",
        json={
            "persona_id": "persona-tenant-iso",
            "objective": "Tenant isolation test",
            "actor_id": "operator-alpha",
        },
        headers={"X-Tenant-Id": "tenant-alpha"},
    )
    assert created.status_code == 201
    session_id = created.json()["session_id"]
    seed_changed_supported_controls(service, session_id)
    queued = client.post(
        f"/api/training/sessions/{session_id}/preview-jobs",
        json={
            "mode": "refresh",
            "requested_by": "operator-alpha",
            "terminalize_session": True,
        },
        headers={"X-Tenant-Id": "tenant-alpha", "Idempotency-Key": "iso-key-001"},
    )
    assert queued.status_code == 201
    job_id = queued.json()["job_id"]

    # Run the job as tenant-alpha to completion
    ran = client.post(
        f"/api/training/preview-jobs/{job_id}/run",
        json={},
        headers={"X-Tenant-Id": "tenant-alpha"},
    )
    assert ran.status_code == 200
    assert ran.json()["status"] == "completed"

    # Worker from tenant-beta cannot see the claimable job
    claimable_beta = client.get(
        "/api/training/preview-jobs",
        params={"status": "claimable"},
        headers={"X-Tenant-Id": "tenant-beta"},
    )
    assert claimable_beta.status_code == 200
    assert len(claimable_beta.json()) == 0

    # Worker from tenant-beta cannot run the job
    run_beta = client.post(
        f"/api/training/preview-jobs/{job_id}/run",
        json={},
        headers={"X-Tenant-Id": "tenant-beta"},
    )
    assert run_beta.status_code == 404

    # Worker from tenant-beta cannot complete the session
    complete_beta = client.post(
        f"/api/training/sessions/{session_id}/complete",
        json={},
        headers={"X-Tenant-Id": "tenant-beta"},
    )
    assert complete_beta.status_code == 404


def test_terminal_session_recovery_invalid_proof_fails_closed(
    tmp_path,
) -> None:
    service, fixture = _load_service_module(tmp_path)
    client = TestClient(service.app)

    created = client.post(
        "/api/training/sessions",
        json={
            "persona_id": "persona-invalid-proof",
            "objective": "Invalid proof must fail closed",
            "actor_id": "operator-1",
        },
    )
    assert created.status_code == 201
    session_id = created.json()["session_id"]
    seed_changed_supported_controls(service, session_id)

    # Clear preview bundle so complete has no proof
    store = service.TrainingSessionStore(fixture.data_dir)
    store.put_preview_bundle(session_id, {"session_id": session_id, "preview": {}})

    # Attempting to complete session with invalid / missing proof must fail 409
    res = client.post(f"/api/training/sessions/{session_id}/complete")
    assert res.status_code == 409
    assert "passing worker evaluation proof required" in res.json()["detail"]
    assert store.get_session(session_id)["status"] == "active"


def test_terminal_session_recovery_duplicate_completion_is_idempotent(
    tmp_path,
) -> None:
    service, fixture = _load_service_module(tmp_path)
    client = TestClient(service.app)

    created = client.post(
        "/api/training/sessions",
        json={
            "persona_id": "persona-dup-comp",
            "objective": "Duplicate completion idempotency",
            "actor_id": "operator-1",
        },
    )
    assert created.status_code == 201
    session_id = created.json()["session_id"]
    seed_changed_supported_controls(service, session_id)

    queued = client.post(
        f"/api/training/sessions/{session_id}/preview-jobs",
        json={"mode": "refresh", "requested_by": "operator-1", "terminalize_session": True},
        headers={"Idempotency-Key": "dup-comp-001"},
    )
    assert queued.status_code == 201
    job_id = queued.json()["job_id"]

    ran = client.post(f"/api/training/preview-jobs/{job_id}/run", json={})
    assert ran.status_code == 200

    comp1 = client.post(f"/api/training/sessions/{session_id}/complete")
    assert comp1.status_code == 201

    store = service.TrainingSessionStore(fixture.data_dir)
    sess1 = store.get_session(session_id)
    assert sess1["status"] == "completed"

    # Second complete returns existing replay idempotently
    comp2 = client.post(f"/api/training/sessions/{session_id}/complete")
    assert comp2.status_code == 201
    assert comp2.json()["session_id"] == session_id

    # Claimable jobs endpoint does not return the job since session is already completed
    claimable = client.get("/api/training/preview-jobs", params={"status": "claimable"})
    assert claimable.status_code == 200
    assert job_id not in [j["job_id"] for j in claimable.json()]


def test_terminal_session_recovery_retry_exhaustion_visibly_failed(
    monkeypatch,
    tmp_path,
) -> None:
    import io
    import urllib.error

    worker = _load_worker_module()
    service, fixture = _load_service_module(tmp_path)
    client = TestClient(service.app)
    monkeypatch.setenv("TRAINING_SESSION_WORKER_TOKEN", "worker:training-service")
    monkeypatch.setenv("TRAINING_SESSION_TENANT_ID", "tenant-test")

    created = client.post(
        "/api/training/sessions",
        json={
            "persona_id": "persona-exhaustion-test",
            "objective": "Verify retry budget exhaustion visibly fails preview job and worker result",
            "actor_id": "operator-1",
        },
    )
    assert created.status_code == 201
    session_id = created.json()["session_id"]
    seed_changed_supported_controls(service, session_id)
    queued = client.post(
        f"/api/training/sessions/{session_id}/preview-jobs",
        json={
            "mode": "refresh",
            "requested_by": "operator-1",
            "terminalize_session": True,
        },
        headers={"Idempotency-Key": "exhaustion-key-001"},
    )
    assert queued.status_code == 201
    job_id = queued.json()["job_id"]

    def service_urlopen(request, timeout):  # noqa: ANN001
        del timeout
        parsed_path = request.full_url.removeprefix("http://training-session-svc:8099")
        headers = dict(request.header_items())
        if parsed_path.endswith(f"/api/training/sessions/{session_id}/complete"):
            fp = io.BytesIO(b'{"detail":"synthetic repeated 503"}')
            raise urllib.error.HTTPError(
                request.full_url,
                503,
                "Service Unavailable",
                {"Content-Type": "application/json"},
                fp,
            )
        response = client.request(
            request.get_method(),
            parsed_path,
            content=request.data,
            headers=headers,
        )
        assert response.status_code < 400, response.text
        return _TestClientResponse(response)

    monkeypatch.setattr(worker.urllib.request, "urlopen", service_urlopen)

    store = service.TrainingSessionStore(fixture.data_dir)
    from datetime import timedelta
    current_time = FIXED_TRUSTED_NOW
    service._trusted_now = lambda: current_time

    # Tick 1: attempt 1/3 fails complete
    tick1 = worker.run_tick(api_url="http://training-session-svc:8099", limit=5)
    assert tick1["jobs_found"] == 1
    assert tick1["completed"] == 1
    assert tick1["failed"] == 1
    assert tick1["named_failures"] == []
    job1 = store.get_preview_job(job_id)
    assert job1["status"] == "completed"
    assert job1["attempt_count"] == 1
    assert job1["retryable"] is True
    assert job1.get("error_code") is None

    # Tick 2: attempt 2/3 fails complete
    tick2 = worker.run_tick(api_url="http://training-session-svc:8099", limit=5)
    assert tick2["jobs_found"] == 1
    assert tick2["completed"] == 1
    assert tick2["replayed"] == 1
    assert tick2["failed"] == 1
    assert tick2["named_failures"] == []
    job2 = store.get_preview_job(job_id)
    assert job2["status"] == "completed"
    assert job2["attempt_count"] == 2
    assert job2["retryable"] is True
    assert job2.get("error_code") is None
    assert job2.get("lease_expires_at") is not None

    # Duplicate tick before lease expiry finds no claimable jobs
    dup_tick = worker.run_tick(api_url="http://training-session-svc:8099", limit=5)
    assert dup_tick["jobs_found"] == 0

    # Advance injected clock past the lease for genuine recovery retry
    current_time += timedelta(seconds=121)

    # Tick 3: attempt 3/3 fails complete
    tick3 = worker.run_tick(api_url="http://training-session-svc:8099", limit=5)
    assert tick3["jobs_found"] == 1
    assert tick3["completed"] == 1
    assert tick3["replayed"] == 1
    assert tick3["failed"] == 1
    assert tick3["named_failures"] == []
    job3 = store.get_preview_job(job_id)
    assert job3["status"] == "completed"
    assert job3["attempt_count"] == 3
    assert job3["retryable"] is False
    assert job3.get("error_code") is None

    # Duplicate tick before lease expiry finds no claimable jobs
    dup_tick2 = worker.run_tick(api_url="http://training-session-svc:8099", limit=5)
    assert dup_tick2["jobs_found"] == 0

    # Advance injected clock past the lease for exhaustion
    current_time += timedelta(seconds=121)

    # Tick 4: retry budget exhausted, owner /run persists named failure and worker reads it
    tick4 = worker.run_tick(api_url="http://training-session-svc:8099", limit=5)
    assert tick4["jobs_found"] == 1
    assert tick4["completed"] == 0
    assert tick4["replayed"] == 1
    assert tick4["reclaimed"] == 1
    assert tick4["failed"] == 1
    assert tick4["named_failures"] == ["terminal_session_completion_exhausted"]
    assert any("error_code=terminal_session_completion_exhausted" in err for err in tick4["errors"])

    # Job is visibly marked failed with named failure
    job4 = store.get_preview_job(job_id)
    assert job4["status"] == "failed"
    assert job4["attempt_count"] == 3
    assert job4["retryable"] is False
    assert job4["error_code"] == "terminal_session_completion_exhausted"
    assert "terminal session completion retry budget exhausted" in str(job4["failure_reason"])
    assert job4["failed_at"] == current_time.isoformat().replace("+00:00", "Z")

    # Session stays active
    session_after = store.get_session(session_id)
    assert session_after["status"] == "active"
    assert session_after.get("ended_at") is None

    # Tick 5: job is no longer claimable
    tick5 = worker.run_tick(api_url="http://training-session-svc:8099", limit=5)
    assert tick5["jobs_found"] == 0
    assert tick5["completed"] == 0
    assert tick5["failed"] == 0
    assert tick5["errors"] == []

    # Job remains failed in store
    job5 = store.get_preview_job(job_id)
    assert job5["status"] == "failed"
    assert job5["error_code"] == "terminal_session_completion_exhausted"


def test_lost_response_from_final_owner_run_leaves_failure_discoverable(
    monkeypatch,
    tmp_path,
) -> None:
    import io
    import urllib.error

    worker = _load_worker_module()
    service, fixture = _load_service_module(tmp_path)
    client = TestClient(service.app)
    monkeypatch.setenv("TRAINING_SESSION_WORKER_TOKEN", "worker:training-service")
    monkeypatch.setenv("TRAINING_SESSION_TENANT_ID", "tenant-test")

    created = client.post(
        "/api/training/sessions",
        json={
            "persona_id": "persona-lost-response-test",
            "objective": "Verify lost response from final owner /run leaves failure discoverable",
            "actor_id": "operator-1",
        },
    )
    assert created.status_code == 201
    session_id = created.json()["session_id"]
    seed_changed_supported_controls(service, session_id)
    queued = client.post(
        f"/api/training/sessions/{session_id}/preview-jobs",
        json={
            "mode": "refresh",
            "requested_by": "operator-1",
            "terminalize_session": True,
        },
        headers={"Idempotency-Key": "lost-resp-key-001"},
    )
    assert queued.status_code == 201
    job_id = queued.json()["job_id"]

    drop_final_run_response = False

    def service_urlopen(request, timeout):  # noqa: ANN001
        del timeout
        nonlocal drop_final_run_response
        parsed_path = request.full_url.removeprefix("http://training-session-svc:8099")
        headers = dict(request.header_items())
        if parsed_path.endswith(f"/api/training/sessions/{session_id}/complete"):
            fp = io.BytesIO(b'{"detail":"synthetic complete failure"}')
            raise urllib.error.HTTPError(
                request.full_url,
                503,
                "Service Unavailable",
                {"Content-Type": "application/json"},
                fp,
            )
        response = client.request(
            request.get_method(),
            parsed_path,
            content=request.data,
            headers=headers,
        )
        assert response.status_code < 400, response.text
        if drop_final_run_response and parsed_path.endswith(f"/api/training/preview-jobs/{job_id}/run"):
            # Server processed the /run mutation, but client experiences lost response
            fp = io.BytesIO(b'{"detail":"lost response on final run"}')
            raise urllib.error.HTTPError(
                request.full_url,
                503,
                "Service Unavailable",
                {"Content-Type": "application/json"},
                fp,
            )
        return _TestClientResponse(response)

    monkeypatch.setattr(worker.urllib.request, "urlopen", service_urlopen)

    store = service.TrainingSessionStore(fixture.data_dir)
    from datetime import timedelta
    current_time = FIXED_TRUSTED_NOW
    service._trusted_now = lambda: current_time

    # Tick 1: attempt 1 fails complete
    worker.run_tick(api_url="http://training-session-svc:8099", limit=5)

    # Ticks 2-3: advance clock past lease before each retry
    for _ in range(2):
        current_time += timedelta(seconds=121)
        worker.run_tick(api_url="http://training-session-svc:8099", limit=5)

    # Tick 4: advance clock past lease, simulate lost response from final owner /run
    current_time += timedelta(seconds=121)
    drop_final_run_response = True
    tick4 = worker.run_tick(api_url="http://training-session-svc:8099", limit=5)
    assert tick4["jobs_found"] == 1
    assert tick4["failed"] == 1
    assert any("http_error=503" in err for err in tick4["errors"])

    # Despite lost response, the owner durably committed the named failure!
    job4 = store.get_preview_job(job_id)
    assert job4["status"] == "failed"
    assert job4["error_code"] == "terminal_session_completion_exhausted"
    assert job4["retryable"] is False
    assert job4["failed_at"] == current_time.isoformat().replace("+00:00", "Z")

    # Session remains active
    session_after = store.get_session(session_id)
    assert session_after["status"] == "active"
    assert session_after.get("ended_at") is None

    # Subsequent tick: job is no longer claimable
    tick5 = worker.run_tick(api_url="http://training-session-svc:8099", limit=5)
    assert tick5["jobs_found"] == 0
    assert tick5["failed"] == 0



def _project_published_row(calls, *, lease_seconds: int) -> dict:
    import importlib
    from datetime import datetime, timedelta, timezone

    projector = importlib.import_module("services.loop-control").project_controller_record_to_bff
    now = datetime.now(timezone.utc)
    row = {
        "loop_id": "persona_teaching",
        "controller_id": "c1",
        "controller_name": "training-session-preview-eval-worker",
        "last_heartbeat_at": now,
        "last_success_at": now,
        "lease_token": "tok",
        "lease_expires_at": now + timedelta(seconds=lease_seconds),
    }
    for _kind, kwargs in calls:
        for key in (
            "desired_state",
            "downstream_actual_state",
            "evidence_refs",
            "desired_state_query",
            "actual_state_query",
        ):
            if kwargs.get(key) is not None:
                row[key] = kwargs[key]
    return projector(row, now=now)


def test_idle_tick_publishes_admissible_controller_truth(monkeypatch) -> None:
    import jsonschema

    module = _load_worker_module()
    monkeypatch.setenv("TRAINING_SESSION_WORKER_TOKEN", "worker:training-service")
    monkeypatch.setenv("TRAINING_SESSION_TENANT_ID", "tenant-test")
    loop_writer = _FakeLoopWriter()
    monkeypatch.setattr(
        module.urllib.request, "urlopen", lambda request, timeout: _Response([])
    )

    module.run_tick(api_url="http://training-session-svc:8099", limit=1, loop_writer=loop_writer)

    assert [c[0] for c in loop_writer.calls] == ["record_heartbeat", "record_success"]
    schema = json.loads(
        (Path(__file__).resolve().parents[3] / "schemas/loop-controller-record.schema.json").read_text()
    )
    for _kind, kwargs in loop_writer.calls:
        jsonschema.validate(kwargs["desired_state"], schema["properties"]["desired_state"])
        jsonschema.validate(
            kwargs["downstream_actual_state"], schema["properties"]["downstream_actual_state"]
        )
    projected = _project_published_row(loop_writer.calls, lease_seconds=61)
    assert projected["desired_state_presence"]["authoritative"] is True
    assert projected["downstream_actual_state"]["authoritative"] is True
    assert projected["evidence_refs"]
    assert projected["controller_health"]["status"] == "healthy"


def test_main_requests_lease_covering_interval_plus_timeout(monkeypatch) -> None:
    module = _load_worker_module()
    captured = {}
    monkeypatch.setenv("TRAINING_SESSION_PREVIEW_WORKER_INTERVAL_SECONDS", "30")
    monkeypatch.setenv("TRAINING_SESSION_PREVIEW_WORKER_TIMEOUT_SECONDS", "20")
    monkeypatch.setenv("TRAINING_SESSION_PREVIEW_WORKER_MAX_TICKS", "1")
    monkeypatch.setattr(
        module, "build_loop_writer", lambda **kw: captured.update(kw) or None
    )
    monkeypatch.setattr(module, "run_tick", lambda **kw: {"failed": 0})
    monkeypatch.setattr(module, "_write_alive", lambda *a, **k: None)
    module.main()
    assert captured["lease_duration_seconds"] >= 50
