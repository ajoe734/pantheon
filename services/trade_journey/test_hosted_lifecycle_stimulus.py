from __future__ import annotations

import json

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
            return {"summaries": [_summary(binding, run_id=run_id)]}
        raise AssertionError(f"unexpected GET {url}")

    return get_json


def _run(
    tmp_path,
    *,
    binding: dict | None = None,
    get_json=None,
    post_json=None,
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

    code, artifact = stimulus.execute(
        output=tmp_path / "stimulus.json",
        runtime_manager_url="http://runtime-manager:8081",
        runtime_manager_token="test-token",
        paper_fleet_reconciler_url="http://paper-fleet-reconciler:8011",
        telemetry_url="http://telemetry:8083",
        reconciliation_url="http://reconciliation-drift-svc:8102",
        signal_store_url="redis://signal-store:6379",
        timeout_seconds=1,
        poll_seconds=0.001,
        http_get_json=get_json,
        http_post_json=post_json,
        store_factory=lambda _binding: store,
        sleeper=lambda _seconds: None,
        now_factory=lambda: now_iso,
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
    assert artifact["stimulus"]["lifecycle_summary_event_id"] == (
        "event-position-loop-prod-tel-002"
    )
    assert artifact["stimulus"]["reconciliation_event_id"] == (
        "event-reconciliation-loop-prod-tel-002"
    )
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
    assert len(store.enqueued) == 1
