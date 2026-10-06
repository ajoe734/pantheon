from __future__ import annotations

import json
import sys
import types

import pytest

from services.trade_journey import hosted_lifecycle_stimulus as stimulus


def _binding(**overrides):
    binding = {
        "binding_id": "rb-loop-prod-tel-002",
        "runtime_id": "runtime-loop-prod-tel-002",
        "tenant_id": "tenant-loop-prod-tel-002",
        "capital_pool_id": "pool-loop-prod-tel-002",
        "artifact_id": "artifact-loop-prod-tel-002",
        "artifact_version": "1.0.0",
        "deployment_mode": "paper",
        "plan_id": "plan-loop-prod-tel-002",
        "persona_capital_binding_id": "pcb-loop-prod-tel-002",
        "status": "active",
    }
    binding.update(overrides)
    return binding


class FakeStore:
    def __init__(self) -> None:
        self.enqueued: list[dict] = []

    def enqueue(self, payload: dict) -> None:
        self.enqueued.append(json.loads(json.dumps(payload)))

    def queue_depth(self) -> int:
        return len(self.enqueued)


def _summary(binding: dict, *, run_id: str) -> dict:
    return {
        "binding_id": binding["binding_id"],
        "runtime_id": binding["runtime_id"],
        "deployment_stage": "paper",
        "last_lifecycle_identity": {
            "event_id": "event-position-loop-prod-tel-002",
            "event_type": "position_snapshot",
            "run_id": run_id,
            "sequence_no": 5,
            "environment": "paper",
            "execution_mode": "paper",
            "deployment_stage": "paper",
            "source_mode": "live",
        },
    }


def _success_getter(binding: dict, *, now_iso: str):
    run_id = f"run-{binding['binding_id']}-{now_iso}-1"
    heartbeat_at = stimulus._utc_now()

    def get_json(url: str, **_kwargs):
        if "desired-state" in url:
            return {"bindings": [binding]}
        if "api/fleet/state" in url:
            return {
                "workers": [
                    {
                        "binding_id": binding["binding_id"],
                        "runtime_id": binding["runtime_id"],
                        "capital_pool_id": binding["capital_pool_id"],
                        "status": "running",
                        "started_at": heartbeat_at,
                        "last_heartbeat_at": heartbeat_at,
                        "heartbeat_status": "active",
                    }
                ]
            }
        if "runtime-summaries" in url:
            return {"summaries": [_summary(binding, run_id=run_id)]}
        raise AssertionError(f"unexpected GET {url}")

    return get_json


def _run(
    tmp_path,
    *,
    binding: dict | None = None,
    get_json=None,
    post_json=None,
    execute_kwargs=None,
):
    now_iso = "2026-07-18T14:00:00Z"
    binding = binding or _binding()
    store = FakeStore()
    posts: list[tuple[str, dict]] = []

    if get_json is None:
        get_json = _success_getter(binding, now_iso=now_iso)
    if post_json is None:
        def post_json(url: str, payload: dict, **_kwargs):
            posts.append((url, dict(payload)))
            return {
                "lifecycle_append_results": [
                    {
                        "binding_id": binding["binding_id"],
                        "event_id": "event-reconciliation-loop-prod-tel-002",
                        "status": "accepted",
                        "terminal": True,
                        "retryable": False,
                    }
                ]
            }

    execute_args = {
        "runtime_manager_url": "http://runtime-manager:8081",
        "runtime_manager_token": "test-token",
        "paper_fleet_reconciler_url": "http://paper-fleet-reconciler:8011",
        "telemetry_url": "http://telemetry:8083",
        "reconciliation_url": "http://reconciliation-drift-svc:8102",
        "signal_store_url": "redis://signal-store:6379",
        "timeout_seconds": 1,
        "poll_seconds": 0.001,
        "http_get_json": get_json,
        "http_post_json": post_json,
        "store_factory": lambda _binding: store,
        "sleeper": lambda _seconds: None,
        "now_factory": lambda: now_iso,
    }
    execute_args.update(execute_kwargs or {})
    code, artifact = stimulus.execute(
        output=tmp_path / "stimulus.json",
        **execute_args,
    )
    return code, artifact, store, posts


def test_stimulus_enqueues_waits_for_runtime_lifecycle_and_reconciles(tmp_path):
    code, artifact, store, posts = _run(tmp_path)

    assert code == 0
    assert artifact["outcome"] == "passed"
    assert len(store.enqueued) == 1
    signal = store.enqueued[0]
    assert signal["binding_id"] == "rb-loop-prod-tel-002"
    assert signal["runtime_id"] == "runtime-loop-prod-tel-002"
    assert signal["run_id"] == artifact["stimulus"]["run_id"]
    assert signal["metadata"]["is_real_order"] is False
    assert signal["metadata"]["is_real_capital"] is False
    assert artifact["stimulus"]["queue_key"] == (
        "pantheon:signals:pending:rb-loop-prod-tel-002"
    )
    assert artifact["stimulus"]["queue_depth_after_enqueue"] == 1
    assert artifact["stimulus"]["lifecycle_summary_event_id"] == (
        "event-position-loop-prod-tel-002"
    )
    assert artifact["stimulus"]["lifecycle_confirmation_source"] == "runtime_summaries"
    assert artifact["stimulus"]["reconciliation_event_id"] == (
        "event-reconciliation-loop-prod-tel-002"
    )
    assert artifact["stimulus"]["reconciliation_status"] == "accepted"
    assert artifact["stimulus"]["reconciliation_ambiguous"] is False
    assert posts[0][0].endswith("/api/reconciliation-drift/scheduled-reconcile")
    assert posts[0][1]["tick_id"].startswith("loop-prod-tel-002-")
    assert posts[0][1]["binding_id"] == "rb-loop-prod-tel-002"
    assert posts[0][1]["dispatch_incidents"] is False
    assert posts[0][1]["lifecycle_only"] is True
    assert artifact["redaction"] == {
        "tokens_included": False,
        "credentials_included": False,
        "response_payloads_included": False,
    }


def test_stimulus_waits_for_fresh_worker_heartbeat_before_enqueue(tmp_path):
    binding = _binding()
    now_iso = "2026-07-18T14:00:00Z"
    run_id = f"run-{binding['binding_id']}-{now_iso}-1"
    heartbeat_at = stimulus._utc_now()
    fleet_calls = 0

    def get_json(url: str, **_kwargs):
        nonlocal fleet_calls
        if "desired-state" in url:
            return {"bindings": [binding]}
        if "api/fleet/state" in url:
            fleet_calls += 1
            worker = {
                "binding_id": binding["binding_id"],
                "runtime_id": binding["runtime_id"],
                "capital_pool_id": binding["capital_pool_id"],
                "status": "running",
            }
            if fleet_calls >= 2:
                worker.update(
                    {
                        "started_at": heartbeat_at,
                        "last_heartbeat_at": heartbeat_at,
                        "heartbeat_status": "active",
                    }
                )
            return {"workers": [worker]}
        if "runtime-summaries" in url:
            return {"summaries": [_summary(binding, run_id=run_id)]}
        raise AssertionError(f"unexpected GET {url}")

    code, artifact, store, _posts = _run(
        tmp_path,
        binding=binding,
        get_json=get_json,
        execute_kwargs={"now_factory": lambda: now_iso},
    )

    assert code == 0
    assert artifact["outcome"] == "passed"
    assert len(store.enqueued) == 1
    assert fleet_calls >= 2


def test_stimulus_uses_committed_telemetry_when_latest_summary_has_advanced(tmp_path):
    binding = _binding()
    now_iso = "2026-07-18T14:00:00Z"
    queried: list[tuple[str, str]] = []

    def get_json(url: str, **_kwargs):
        if "desired-state" in url:
            return {"bindings": [binding]}
        if "api/fleet/state" in url:
            heartbeat_at = stimulus._utc_now()
            return {
                "workers": [
                    {
                        "binding_id": binding["binding_id"],
                        "runtime_id": binding["runtime_id"],
                        "capital_pool_id": binding["capital_pool_id"],
                        "status": "running",
                        "started_at": heartbeat_at,
                        "last_heartbeat_at": heartbeat_at,
                        "heartbeat_status": "active",
                    }
                ]
            }
        if "runtime-summaries" in url:
            return {"summaries": [_summary(binding, run_id="newer-different-run")]}
        raise AssertionError(f"unexpected GET {url}")

    def committed_identity_getter(_dsn: str, *, binding: dict, run_id: str):
        queried.append((binding["binding_id"], run_id))
        return {
            "event_id": "event-committed-position-loop-prod-tel-002",
            "event_type": "position_snapshot",
            "run_id": run_id,
            "sequence_no": 5,
            "environment": "paper",
            "execution_mode": "paper",
            "deployment_stage": "paper",
            "source_mode": "live",
            "binding_id": binding["binding_id"],
            "runtime_id": binding["runtime_id"],
            "capital_pool_id": binding["capital_pool_id"],
        }

    code, artifact, store, posts = _run(
        tmp_path,
        binding=binding,
        get_json=get_json,
        execute_kwargs={
            "now_factory": lambda: now_iso,
            "telemetry_db_dsn": "postgresql://unit-test-redacted",
            "committed_identity_getter": committed_identity_getter,
        },
    )

    assert code == 0
    assert artifact["outcome"] == "passed"
    assert queried == [
        (
            binding["binding_id"],
            f"run-{binding['binding_id']}-{now_iso}-1",
        )
    ]
    assert artifact["stimulus"]["lifecycle_summary_event_id"] == (
        "event-committed-position-loop-prod-tel-002"
    )
    assert artifact["stimulus"]["lifecycle_confirmation_source"] == "telemetry_events"
    assert len(store.enqueued) == 1
    assert len(posts) == 1


class _FakeAsyncpgConnection:
    def __init__(self, row: dict) -> None:
        self.row = row
        self.args: tuple = ()

    async def fetchrow(self, _query: str, *args):
        self.args = args
        return self.row

    async def close(self) -> None:
        return None


@pytest.mark.parametrize("encode", [json.dumps, dict], ids=["jsonb-text", "mapping"])
def test_committed_identity_reads_the_stored_position_snapshot_row(monkeypatch, encode):
    binding = _binding()
    run_id = f"run-{binding['binding_id']}-2026-10-05T22:14:45Z-1"
    event = {
        "event_id": "event-committed-position-loop-prod-tel-002",
        "event_type": "position_snapshot",
        "binding_id": binding["binding_id"],
        "runtime_id": binding["runtime_id"],
        "run_id": run_id,
        "environment": "paper",
        "execution_mode": "paper",
        "deployment_stage": "paper",
        "source_mode": "live",
        "metadata": {"run_id": run_id, "sequence_no": 7},
    }
    # asyncpg decodes jsonb columns to JSON text unless a codec is registered.
    connection = _FakeAsyncpgConnection(
        {
            "event_id": event["event_id"],
            "event_type": "position_snapshot",
            "created_at": "2026-10-05T22:14:51Z",
            "payload": encode(event),
        }
    )

    async def connect(_dsn: str):
        return connection

    monkeypatch.setitem(sys.modules, "asyncpg", types.SimpleNamespace(connect=connect))

    identity = stimulus.fetch_committed_lifecycle_identity(
        "postgresql://unit-test-redacted", binding=binding, run_id=run_id
    )

    assert connection.args == (run_id, binding["binding_id"], binding["runtime_id"])
    assert identity is not None
    assert identity["event_id"] == event["event_id"]
    assert identity["sequence_no"] == 7


def test_stimulus_fails_before_enqueue_when_worker_heartbeat_is_not_fresh(tmp_path):
    binding = _binding()

    def get_json(url: str, **_kwargs):
        if "desired-state" in url:
            return {"bindings": [binding]}
        if "api/fleet/state" in url:
            return {
                "workers": [
                    {
                        "binding_id": binding["binding_id"],
                        "runtime_id": binding["runtime_id"],
                        "capital_pool_id": binding["capital_pool_id"],
                        "status": "running",
                    }
                ]
            }
        if "runtime-summaries" in url:
            raise AssertionError("stimulus must not enqueue before worker readiness")
        raise AssertionError(f"unexpected GET {url}")

    code, artifact, store, posts = _run(
        tmp_path,
        binding=binding,
        get_json=get_json,
        execute_kwargs={"worker_ready_timeout_seconds": 0},
    )

    assert code == 1
    assert artifact["failure"]["code"] == "paper_worker_not_ready"
    assert artifact["failure"]["timed_out"] is True
    assert artifact["failure"]["details"]["binding_id"] == binding["binding_id"]
    assert artifact["failure"]["details"]["last_worker"]["runtime_id"] == binding["runtime_id"]
    assert store.enqueued == []
    assert posts == []


def test_stimulus_timeout_reports_worker_queue_and_last_summary_details(tmp_path):
    binding = _binding()
    now_iso = "2026-07-18T14:00:00Z"
    heartbeat_at = stimulus._utc_now()
    ticks = iter([0.0, 0.0, 0.0, 0.0, 1.0])

    def monotonic() -> float:
        return next(ticks, 1.0)

    def get_json(url: str, **_kwargs):
        if "desired-state" in url:
            return {"bindings": [binding]}
        if "api/fleet/state" in url:
            return {
                "workers": [
                    {
                        "binding_id": binding["binding_id"],
                        "runtime_id": binding["runtime_id"],
                        "capital_pool_id": binding["capital_pool_id"],
                        "status": "running",
                        "started_at": heartbeat_at,
                        "last_heartbeat_at": heartbeat_at,
                        "heartbeat_status": "active",
                    }
                ]
            }
        if "runtime-summaries" in url:
            return {"summaries": [_summary(binding, run_id="different-run")]}
        raise AssertionError(f"unexpected GET {url}")

    code, artifact, store, posts = _run(
        tmp_path,
        binding=binding,
        get_json=get_json,
        execute_kwargs={
            "now_factory": lambda: now_iso,
            "timeout_seconds": 0.01,
            "worker_ready_timeout_seconds": 0,
            "monotonic": monotonic,
        },
    )

    assert code == 1
    assert artifact["failure"]["code"] == "lifecycle_signal_timeout"
    details = artifact["failure"]["details"]
    assert details["worker"]["binding_id"] == binding["binding_id"]
    assert details["queue_key"] == "pantheon:signals:pending:rb-loop-prod-tel-002"
    assert details["queue_depth_after_timeout"] == 1
    assert details["summary_seen"] is True
    assert details["last_lifecycle_identity"]["run_id"] == "different-run"
    assert len(store.enqueued) == 1
    assert posts == []


def test_stimulus_fails_when_no_active_paper_binding_is_available(tmp_path):
    def get_json(url: str, **_kwargs):
        if "desired-state" in url:
            return {"bindings": [_binding(status="paused")]}
        if "api/fleet/state" in url:
            return {"workers": []}
        raise AssertionError(f"unexpected GET {url}")

    code, artifact, store, posts = _run(tmp_path, get_json=get_json)

    assert code == 1
    assert artifact["failure"]["code"] == "no_active_paper_binding"
    assert store.enqueued == []
    assert posts == []


def test_stimulus_fails_when_no_running_worker_can_consume_the_binding(tmp_path):
    binding = _binding()

    def get_json(url: str, **_kwargs):
        if "desired-state" in url:
            return {"bindings": [binding]}
        if "api/fleet/state" in url:
            return {
                "workers": [
                    {
                        "binding_id": binding["binding_id"],
                        "runtime_id": binding["runtime_id"],
                        "capital_pool_id": binding["capital_pool_id"],
                        "status": "dead",
                    }
                ]
            }
        raise AssertionError(f"unexpected GET {url}")

    code, artifact, store, posts = _run(
        tmp_path,
        binding=binding,
        get_json=get_json,
    )

    assert code == 1
    assert artifact["failure"]["code"] == "no_running_paper_worker"
    assert store.enqueued == []
    assert posts == []


def test_stimulus_fails_when_reconciliation_append_is_not_accepted(tmp_path):
    binding = _binding()

    def post_json(_url: str, _payload: dict, **_kwargs):
        return {
            "lifecycle_append_results": [
                {
                    "binding_id": binding["binding_id"],
                    "event_id": None,
                    "status": "retryable_error",
                    "terminal": False,
                    "retryable": True,
                }
            ]
        }

    code, artifact, store, _posts = _run(
        tmp_path,
        binding=binding,
        post_json=post_json,
    )

    assert code == 1
    assert artifact["failure"]["code"] == "reconciliation_append_not_accepted"
    assert artifact["failure"]["details"]["reconciliation_receipt"] == {
        "binding_id": binding["binding_id"],
        "status": "retryable_error",
        "terminal": False,
        "retryable": True,
    }
    assert len(store.enqueued) == 1


def test_stimulus_can_continue_after_ambiguous_reconciliation_receipt(tmp_path):
    binding = _binding()

    def post_json(_url: str, _payload: dict, **_kwargs):
        return {
            "lifecycle_append_results": [
                {
                    "binding_id": binding["binding_id"],
                    "event_id": "event-reconciliation-loop-prod-tel-002",
                    "status": "retryable_error",
                    "terminal": False,
                    "retryable": True,
                    "outcome": "ambiguous",
                    "http_status": None,
                    "error": "timed out waiting for telemetry ingest acknowledgement",
                }
            ]
        }

    code, artifact, store, _posts = _run(
        tmp_path,
        binding=binding,
        post_json=post_json,
        execute_kwargs={"allow_ambiguous_reconciliation": True},
    )

    assert code == 0
    assert artifact["outcome"] == "passed"
    assert artifact["stimulus"]["reconciliation_event_id"] == (
        "event-reconciliation-loop-prod-tel-002"
    )
    assert artifact["stimulus"]["reconciliation_status"] == "retryable_error"
    assert artifact["stimulus"]["reconciliation_ambiguous"] is True
    assert len(store.enqueued) == 1


def test_stimulus_rejects_terminal_reconciliation_receipt_even_when_ambiguous_allowed(tmp_path):
    binding = _binding()

    def post_json(_url: str, _payload: dict, **_kwargs):
        return {
            "lifecycle_append_results": [
                {
                    "binding_id": binding["binding_id"],
                    "event_id": "event-reconciliation-loop-prod-tel-002",
                    "status": "terminal_rejected",
                    "terminal": True,
                    "retryable": False,
                    "outcome": "failed",
                    "http_status": 400,
                    "error": "telemetry ingest returned HTTP 400",
                }
            ]
        }

    code, artifact, store, _posts = _run(
        tmp_path,
        binding=binding,
        post_json=post_json,
        execute_kwargs={"allow_ambiguous_reconciliation": True},
    )

    assert code == 1
    assert artifact["failure"]["code"] == "reconciliation_append_not_accepted"
    assert artifact["failure"]["details"]["reconciliation_receipt"] == {
        "binding_id": binding["binding_id"],
        "event_id": "event-reconciliation-loop-prod-tel-002",
        "status": "terminal_rejected",
        "terminal": True,
        "retryable": False,
        "outcome": "failed",
        "http_status": 400,
        "error": "telemetry ingest returned HTTP 400",
    }
    assert len(store.enqueued) == 1


def test_stimulus_can_continue_after_ambiguous_reconciliation_timeout(tmp_path):
    def post_json(_url: str, _payload: dict, **_kwargs):
        raise TimeoutError("timed out")

    code, artifact, store, _posts = _run(
        tmp_path,
        post_json=post_json,
        execute_kwargs={
            "allow_ambiguous_reconciliation": True,
            "reconciliation_timeout_seconds": 0.01,
        },
    )

    assert code == 0
    assert artifact["outcome"] == "passed"
    assert artifact["stimulus"]["reconciliation_event_id"] == ""
    assert artifact["stimulus"]["reconciliation_status"] == "ambiguous_timeout"
    assert artifact["stimulus"]["reconciliation_ambiguous"] is True
    assert len(store.enqueued) == 1


def _load_reconciliation_drift_module(monkeypatch, tmp_path):
    import importlib.util
    from pathlib import Path

    service_dir = Path(__file__).resolve().parents[1] / "reconciliation-drift"
    monkeypatch.setenv("RECONCILIATION_DRIFT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RECONCILIATION_DRIFT_AUTH_MODE", "token")
    monkeypatch.setenv("RECONCILIATION_DRIFT_AUTH_TOKEN", "owner-token")
    monkeypatch.syspath_prepend(str(service_dir))
    monkeypatch.delitem(sys.modules, "store", raising=False)
    spec = importlib.util.spec_from_file_location(
        "reconciliation_drift_stimulus_auth_main", service_dir / "main.py"
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def _reconcile_through(client):
    """Adapt the stimulus POST helper onto the real reconciliation app."""

    seen: list[int] = []

    def post_json(url: str, payload: dict, *, headers=None, timeout=10.0):
        path = url.split("8102", 1)[1]
        response = client.post(path, json=payload, headers=dict(headers or {}))
        seen.append(response.status_code)
        if response.status_code in (401, 403):
            raise stimulus.StimulusError("outbound_auth_rejected", "rejected")
        response.raise_for_status()
        return response.json()

    return post_json, seen


def test_scheduled_reconciliation_requires_credentials_and_tenant(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    client = TestClient(_load_reconciliation_drift_module(monkeypatch, tmp_path).app)
    path = "/api/reconciliation-drift/scheduled-reconcile"
    body = {"tick_id": "t-1", "binding_id": "rb-1", "lifecycle_only": True}
    good = stimulus._headers("owner-token", "tenant-a")

    assert client.post(path, json=body).status_code == 401
    assert client.post(path, json=body, headers=stimulus._headers(None, "tenant-a")).status_code == 401
    assert client.post(path, json=body, headers=stimulus._headers("owner-token")).status_code == 400
    assert client.post(path, json=body, headers=stimulus._headers("wrong", "tenant-a")).status_code == 401
    accepted = client.post(path, json=body, headers=good)
    assert accepted.status_code == 201

    post_json, seen = _reconcile_through(client)
    with pytest.raises(stimulus.StimulusError) as excinfo:
        stimulus.trigger_reconciliation(
            reconciliation_url="http://reconciliation-drift-svc:8102",
            binding_id="rb-1",
            tick_id="t-2",
            http_post_json=post_json,
        )
    assert excinfo.value.code == "outbound_auth_rejected"
    assert seen == [400] or seen == [401]


def test_wait_for_lifecycle_summary_sends_credentials_and_tenant():
    binding = _binding()
    calls: list[dict] = []
    getter = _success_getter(binding, now_iso="2026-07-18T14:00:00Z")

    def get_json(url, **kwargs):
        calls.append(kwargs)
        return getter(url, **kwargs)

    stimulus.wait_for_lifecycle_summary(
        telemetry_url="http://telemetry:8083",
        binding=binding,
        run_id=f"run-{binding['binding_id']}-2026-07-18T14:00:00Z-1",
        timeout_seconds=1,
        poll_seconds=0.001,
        headers=stimulus._headers("tel-token", "tenant-a"),
        http_get_json=get_json,
    )
    assert calls[0]["headers"]["Authorization"] == "Bearer tel-token"
    assert calls[0]["headers"]["X-Tenant-Id"] == "tenant-a"


@pytest.mark.parametrize("code", [401, 403])
def test_http_auth_rejection_is_terminal_and_redacted(monkeypatch, code):
    import io
    import urllib.error

    def deny(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, code, "no", {}, io.BytesIO(b"secret"))

    monkeypatch.setattr(stimulus.urllib.request, "urlopen", deny)
    with pytest.raises(stimulus.StimulusError) as excinfo:
        stimulus._http_get_json("http://telemetry:8083/api/telemetry/runtime-summaries")
    assert excinfo.value.code == "outbound_auth_rejected"
    assert excinfo.value.safe_details == {"http_status": code}
    assert "secret" not in excinfo.value.safe_message
