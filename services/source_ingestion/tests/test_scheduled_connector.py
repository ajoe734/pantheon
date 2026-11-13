"""Tests for autonomous scheduled connector execution path."""

from __future__ import annotations

import importlib
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from services.source_ingestion.scheduler import JsonlIngestScheduleStore


def _read_headers(tenant: str = "tenant-a") -> dict[str, str]:
    from services.runtime_auth_inbound import encode_jwt_hs256

    token = encode_jwt_hs256(
        {"sub": "test-reader", "roles": ["operator"], "tenant_id": tenant, "exp": int(__import__("time").time()) + 600},
        secret=os.environ["PANTHEON_RUNTIME_JWT_SECRET"],
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def client():
    tempdir = tempfile.mkdtemp(prefix="source_ingest_scheduled_")
    env_backup = {
        "SOURCE_INGEST_DATA_DIR": os.environ.get("SOURCE_INGEST_DATA_DIR"),
        "SOURCE_INGEST_MAX_RECORDS": os.environ.get("SOURCE_INGEST_MAX_RECORDS"),
        "SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY": os.environ.get("SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY"),
        "SOURCE_INGEST_FRONTIER_MAX_ATTEMPTS": os.environ.get("SOURCE_INGEST_FRONTIER_MAX_ATTEMPTS"),
        "SOURCE_INGEST_FRONTIER_BACKOFF_SECONDS": os.environ.get("SOURCE_INGEST_FRONTIER_BACKOFF_SECONDS"),
        "PANTHEON_RUNTIME_JWT_SECRET": os.environ.get("PANTHEON_RUNTIME_JWT_SECRET"),
    }
    os.environ["SOURCE_INGEST_DATA_DIR"] = tempdir
    os.environ["SOURCE_INGEST_MAX_RECORDS"] = "10"
    os.environ["SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY"] = "1"
    os.environ["SOURCE_INGEST_FRONTIER_MAX_ATTEMPTS"] = "2"
    os.environ["SOURCE_INGEST_FRONTIER_BACKOFF_SECONDS"] = "300"
    os.environ["PANTHEON_RUNTIME_JWT_SECRET"] = "source-test-secret"

    sys.modules.pop("services.source_ingestion.main", None)
    module = importlib.import_module("services.source_ingestion.main")
    module = importlib.reload(module)

    try:
        yield TestClient(module.app, headers=_read_headers()), Path(tempdir), module
    finally:
        for key, value in env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _connector(**overrides):
    payload = {
        "connector_id": "conn-sched-notes",
        "source_type": "internal_note",
        "provider": "Test scheduled",
        "license_scope": "internal",
    }
    payload.update(overrides)
    payload["metadata"] = {"tenant_id": "tenant-a", **dict(payload.get("metadata") or {})}
    return payload


def _configure_with_records(test_client, connector_id="conn-sched-notes"):
    source_timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return test_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _connector(connector_id=connector_id),
            "fetch": {
                "mode": "static_records",
                "next_watermark": "2026-04-30T01:00:00Z",
                "records": [
                    {
                        "source_id": f"src-{connector_id}-note-1",
                        "title": "Scheduled note",
                        "content_ref": f"memory://scheduled/{connector_id}/note-1",
                        "metadata": {
                            "body": "Autonomous scheduled evidence",
                            "access_scope": ["operator"],
                            "available_time": source_timestamp,
                        },
                    }
                ],
            },
        },
    )


def test_set_and_get_connector_schedule(client) -> None:
    test_client, _, _ = client
    test_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _connector(),
            "fetch": {"mode": "static_records", "records": []},
        },
    )

    response = test_client.put(
        "/api/source-ingest/connectors/conn-sched-notes/schedule",
        json={"interval_seconds": 300, "enabled": True},
    )
    assert response.status_code == 200, response.text
    schedule = response.json()["schedule"]
    assert schedule["connector_id"] == "conn-sched-notes"
    assert schedule["interval_seconds"] == 300
    assert schedule["enabled"] is True

    get_response = test_client.get("/api/source-ingest/connectors/conn-sched-notes/schedule")
    assert get_response.status_code == 200, get_response.text
    assert get_response.json()["schedule"]["interval_seconds"] == 300


def test_schedule_replays_after_reload(client) -> None:
    test_client, _, module = client
    test_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _connector(),
            "fetch": {"mode": "static_records", "records": []},
        },
    )
    test_client.put(
        "/api/source-ingest/connectors/conn-sched-notes/schedule",
        json={"interval_seconds": 60, "enabled": True},
    )

    reloaded = importlib.reload(module)
    replay_client = TestClient(reloaded.app)
    get_response = replay_client.get("/api/source-ingest/connectors/conn-sched-notes/schedule")
    assert get_response.status_code == 200
    assert get_response.json()["schedule"]["interval_seconds"] == 60


def test_run_scheduled_runs_due_connector_and_persists_evidence(client) -> None:
    test_client, _, _ = client
    configured = _configure_with_records(test_client)
    assert configured.status_code == 201, configured.text

    test_client.put(
        "/api/source-ingest/connectors/conn-sched-notes/schedule",
        json={"interval_seconds": 1, "enabled": True},
    )

    response = test_client.post("/api/source-ingest/run-scheduled")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["summary"]["total_ran"] == 1
    assert body["summary"]["total_failed"] == 0
    assert body["ran"][0]["connector_id"] == "conn-sched-notes"
    assert body["ran"][0]["run"]["status"] == "completed"
    assert body["ran"][0]["evidence_refs"]["knowledge_object_ids"]
    receipt = body["ran"][0]["receipt"]
    assert receipt["schema_version"] == "source_ingest_receipt.v1"
    assert receipt["status"] == "completed"
    assert receipt["connector_id"] == "conn-sched-notes"
    assert receipt["normalized_count"] == 1
    assert receipt["source_timestamp"]
    assert receipt["source_timestamp_status"] == "valid"
    assert receipt["typed_failure"] is None

    receipt_readback = test_client.get(f"/api/source-ingest/receipts/{receipt['ingest_run_id']}")
    assert receipt_readback.status_code == 200
    assert receipt_readback.json()["receipt"] == receipt

    source = test_client.get("/api/source-ingest/source-records/src-conn-sched-notes-note-1", headers=_read_headers())
    assert source.status_code == 200


def test_run_scheduled_force_reconciles_changed_connector_before_cadence(client) -> None:
    test_client, _, _ = client
    configured = _configure_with_records(test_client)
    assert configured.status_code == 201, configured.text
    scheduled = test_client.put(
        "/api/source-ingest/connectors/conn-sched-notes/schedule",
        json={"interval_seconds": 3600, "enabled": True},
    )
    assert scheduled.status_code == 200, scheduled.text

    first = test_client.post("/api/source-ingest/run-scheduled")
    duplicate = test_client.post("/api/source-ingest/run-scheduled")
    forced = test_client.post(
        "/api/source-ingest/run-scheduled",
        json={"force_connector_ids": ["conn-sched-notes"]},
    )

    assert first.json()["summary"]["total_ran"] == 1
    assert duplicate.json()["summary"]["total_skipped"] == 1
    assert forced.json()["summary"]["total_ran"] == 1
    assert forced.json()["summary"]["forced_connector_count"] == 1


def test_two_scheduler_threads_create_one_run_and_one_source_record(client) -> None:
    test_client, _, module = client
    configured = _configure_with_records(test_client, connector_id="conn-two-workers")
    assert configured.status_code == 201, configured.text
    scheduled = test_client.put(
        "/api/source-ingest/connectors/conn-two-workers/schedule",
        json={"interval_seconds": 3600, "enabled": True},
    )
    assert scheduled.status_code == 200, scheduled.text

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(
            executor.map(
                lambda _worker: test_client.post(
                    "/api/source-ingest/run-scheduled",
                    json={"max_concurrency": 1},
                ),
                range(2),
            )
        )

    assert [response.status_code for response in responses] == [200, 200]
    payloads = [response.json() for response in responses]
    assert sum(payload["summary"]["total_ran"] for payload in payloads) == 1
    assert sum(payload["summary"]["total_skipped"] for payload in payloads) == 1
    assert len(module.runtime.store.list_runs()) == 1
    assert len(module.runtime.store.list_frontier(status="done")) == 1
    assert len(module.runtime.evidence_repository.list_source_records()) == 1
    assert len(module.runtime.schedule_config_store.list_schedules()) == 1
    assert module.runtime.connector_store.get_fetch_state("conn-two-workers")["attempts"] == 1


def test_run_scheduled_rejects_concurrency_above_supervised_limit(client) -> None:
    test_client, _, _ = client

    response = test_client.post(
        "/api/source-ingest/run-scheduled",
        json={"max_concurrency": 2},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "max_concurrency exceeds SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY=1"


def test_run_scheduled_exclusive_scope_never_enqueues_or_runs_unrelated_due_connector(client) -> None:
    test_client, _, module = client
    target_id = "conn-exclusive-target"
    unrelated_id = "conn-exclusive-unrelated"
    for connector_id in (target_id, unrelated_id):
        configured = _configure_with_records(test_client, connector_id=connector_id)
        assert configured.status_code == 201, configured.text
        scheduled = test_client.put(
            f"/api/source-ingest/connectors/{connector_id}/schedule",
            json={"interval_seconds": 1, "enabled": True},
        )
        assert scheduled.status_code == 200, scheduled.text
    unrelated_frontier = module.runtime.store.enqueue_frontier(
        connector_id=unrelated_id,
        available_at="2020-01-01T00:00:00Z",
    )

    response = test_client.post(
        "/api/source-ingest/run-scheduled",
        json={
            "max_concurrency": 1,
            "force_connector_ids": [target_id],
            "exclusive_connector_ids": [target_id],
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["summary"]["total_enqueued"] == 1
    assert body["summary"]["total_ran"] == 1
    assert body["summary"]["total_failed"] == 0
    assert body["summary"]["exclusive_connector_count"] == 1
    assert body["summary"]["total_excluded"] == 1
    assert [item["connector_id"] for item in body["enqueued"]] == [target_id]
    assert [item["connector_id"] for item in body["ran"]] == [target_id]
    assert body["excluded"] == [unrelated_id]
    assert module.runtime.store.get_frontier(unrelated_frontier.frontier_id).status == "queued"
    assert module.runtime.store.list_receipts(connector_id=unrelated_id) == []


@pytest.mark.parametrize(
    ("setup", "expected_error", "expected_enqueued"),
    [
        ("missing", "schedule not found", 0),
        ("disabled_schedule", "schedule is disabled", 0),
        ("disabled_connector", "connector is disabled", 0),
        ("fetch_failure", "selected connector fetch failed", 1),
    ],
)
def test_run_scheduled_exclusive_scope_fails_closed_when_target_is_unavailable(
    client,
    setup: str,
    expected_error: str,
    expected_enqueued: int,
) -> None:
    test_client, _, _ = client
    connector_id = f"conn-exclusive-{setup}"
    if setup != "missing":
        fetch = {"mode": "static_records", "records": []}
        if setup == "fetch_failure":
            fetch.update(
                {
                    "fail_until_attempt": 2,
                    "failure_reason": "selected connector fetch failed",
                }
            )
        configured = test_client.post(
            "/api/source-ingest/connectors",
            json={
                "connector": _connector(connector_id=connector_id),
                "fetch": fetch,
            },
        )
        assert configured.status_code == 201, configured.text
        scheduled = test_client.put(
            f"/api/source-ingest/connectors/{connector_id}/schedule",
            json={"interval_seconds": 60, "enabled": setup != "disabled_schedule"},
        )
        assert scheduled.status_code == 200, scheduled.text
        if setup == "disabled_connector":
            lifecycle = test_client.put(
                f"/api/source-ingest/connectors/{connector_id}/lifecycle",
                json={"status": "disabled", "reason": "operator test hold"},
            )
            assert lifecycle.status_code == 200, lifecycle.text

    response = test_client.post(
        "/api/source-ingest/run-scheduled",
        json={"exclusive_connector_ids": [connector_id]},
    )

    assert response.status_code == 200, response.text
    assert response.json()["summary"]["total_ran"] == 0
    assert response.json()["summary"]["total_enqueued"] == expected_enqueued
    assert response.json()["summary"]["total_failed"] == 1
    assert expected_error in response.json()["failed"][0]["error"]


def test_stale_running_frontier_is_durably_recovered_after_restart(client) -> None:
    _, _, module = client
    item = module.runtime.store.enqueue_frontier(
        connector_id="conn-restart-recovery",
        max_attempts=2,
        available_at="2026-07-14T00:00:00Z",
    )
    claimed = module.runtime.store.claim_frontier(item.frontier_id, now="2026-07-14T00:00:00Z")
    future = (datetime.now(timezone.utc) + timedelta(seconds=600)).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    recovered = module.runtime.store.recover_stale_running(timeout_seconds=300, now=future)
    reloaded = JsonlIngestScheduleStore(module.runtime.SCHEDULE_STORE_PATH)

    assert claimed.status == "running"
    assert recovered[0].status == "retry"
    assert reloaded.get_frontier(item.frontier_id).status == "retry"


def test_frontier_append_failure_does_not_publish_phantom_work(client, monkeypatch: pytest.MonkeyPatch) -> None:
    _, _, module = client
    monkeypatch.setattr(module.runtime.store, "_append", lambda *args: (_ for _ in ()).throw(OSError("disk full")))

    with pytest.raises(OSError, match="disk full"):
        module.runtime.store.enqueue_frontier(connector_id="conn-phantom")

    assert module.runtime.store.list_frontier() == []


def test_run_scheduled_honors_bounded_concurrency(client) -> None:
    test_client, _, _ = client
    configured_a = _configure_with_records(test_client, connector_id="conn-sched-a")
    configured_b = _configure_with_records(test_client, connector_id="conn-sched-b")
    assert configured_a.status_code == 201, configured_a.text
    assert configured_b.status_code == 201, configured_b.text

    for connector_id in ("conn-sched-a", "conn-sched-b"):
        scheduled = test_client.put(
            f"/api/source-ingest/connectors/{connector_id}/schedule",
            json={"interval_seconds": 1, "enabled": True},
        )
        assert scheduled.status_code == 200, scheduled.text

    first = test_client.post("/api/source-ingest/run-scheduled", json={"max_concurrency": 1})
    assert first.status_code == 200, first.text
    assert first.json()["summary"]["total_ran"] == 1
    frontier = test_client.get("/api/source-ingest/frontier")
    assert frontier.status_code == 200
    statuses = [item["status"] for item in frontier.json()["frontier"]]
    assert statuses.count("done") == 1
    assert statuses.count("queued") == 1

    second = test_client.post("/api/source-ingest/run-scheduled", json={"max_concurrency": 1})
    assert second.status_code == 200, second.text
    assert second.json()["summary"]["total_ran"] == 1
    done_frontier = test_client.get("/api/source-ingest/frontier?status=done")
    assert done_frontier.status_code == 200
    assert len(done_frontier.json()["frontier"]) == 2


def test_blocked_host_records_typed_denial_without_outbound_request(client, monkeypatch) -> None:
    test_client, _, _ = client
    outbound_called = False

    def forbidden_transport(*args, **kwargs):
        nonlocal outbound_called
        outbound_called = True
        raise AssertionError("outbound transport must not be built for a denied host")

    monkeypatch.setattr("urllib.request.build_opener", forbidden_transport)
    configured = test_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _connector(connector_id="conn-blocked-host"),
            "fetch": {
                "mode": "external_feed",
                "url": "https://blocked.example/source.json",
                "allowed_url_prefixes": ["https://blocked.example/"],
                "respect_robots_txt": False,
                "max_records": 1,
            },
        },
    )
    assert configured.status_code == 201, configured.text
    test_client.put(
        "/api/source-ingest/connectors/conn-blocked-host/schedule",
        json={"interval_seconds": 60, "enabled": True},
    )

    response = test_client.post("/api/source-ingest/run-scheduled", json={"max_concurrency": 1})

    assert response.status_code == 200, response.text
    assert response.json()["summary"]["total_failed"] == 1
    receipt = response.json()["failed"][0]["receipt"]
    assert receipt["typed_failure"]["category"] == "external_egress"
    assert receipt["typed_failure"]["code"] == "host_not_allowlisted"
    assert receipt["typed_failure"]["retryable"] is False
    assert outbound_called is False


def test_provider_failure_matrix_is_isolated_and_projected_to_source_health(
    client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_client, _, module = client
    connector_ids = {
        "success": "conn-00-success",
        "policy": "conn-10-policy",
        "credential": "conn-20-credential",
        "provider": "conn-30-provider",
    }
    for classification in ("success", "credential", "provider"):
        configured = _configure_with_records(test_client, connector_id=connector_ids[classification])
        assert configured.status_code == 201, configured.text
    configured_policy = test_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _connector(connector_id=connector_ids["policy"]),
            "fetch": {
                "mode": "external_feed",
                "url": "https://blocked.example/source.json",
                "allowed_url_prefixes": ["https://blocked.example/"],
                "respect_robots_txt": False,
                "max_records": 1,
            },
        },
    )
    assert configured_policy.status_code == 201, configured_policy.text
    for connector_id in connector_ids.values():
        scheduled = test_client.put(
            f"/api/source-ingest/connectors/{connector_id}/schedule",
            json={"interval_seconds": 60, "enabled": True},
        )
        assert scheduled.status_code == 200, scheduled.text

    class CredentialUnavailableError(RuntimeError):
        pass

    class ProviderServiceFailure(RuntimeError):
        pass

    original_fetch = module.runtime.configured_fetcher.fetch_batch

    def classified_fetch(connector_id, watermark, **kwargs):
        if connector_id == connector_ids["credential"]:
            raise CredentialUnavailableError("credential reference unavailable")
        if connector_id == connector_ids["provider"]:
            raise ProviderServiceFailure("provider unavailable")
        return original_fetch(connector_id, watermark, **kwargs)

    monkeypatch.setattr(module.runtime.configured_fetcher, "fetch_batch", classified_fetch)

    payloads = [
        test_client.post(
            "/api/source-ingest/run-scheduled",
            json={"max_concurrency": 1},
        ).json()
        for _ in connector_ids
    ]

    assert sum(payload["summary"]["total_ran"] for payload in payloads) == 1
    assert sum(payload["summary"]["total_failed"] for payload in payloads) == 3
    expected_outcomes = {
        "success": ("success", "success", "completed"),
        "policy": ("policy_denial", "external_egress", "host_not_allowlisted"),
        "credential": ("credential_unavailable", "credential", "credential_unavailable"),
        "provider": ("provider_failure", "provider", "provider_fetch_failed"),
    }
    for label, connector_id in connector_ids.items():
        response = test_client.get(f"/api/source-ingest/health/{connector_id}")
        assert response.status_code == 200, response.text
        health = response.json()
        outcome = health["metadata"]["last_outcome"]
        classification, category, code = expected_outcomes[label]
        assert outcome["classification"] == classification
        assert outcome["category"] == category
        assert outcome["code"] == code
        if label == "success":
            assert health["status"] == "ok"
            assert health["last_success_at"]
        else:
            assert health["status"] == "failed"
            assert health["last_failure_at"]
    assert len(module.runtime.evidence_repository.list_source_records()) == 1


def test_post_processing_failure_keeps_durable_typed_receipt_after_reload(
    client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_client, _, module = client
    configured = _configure_with_records(test_client, connector_id="conn-post-processing-failure")
    assert configured.status_code == 201, configured.text
    scheduled = test_client.put(
        "/api/source-ingest/connectors/conn-post-processing-failure/schedule",
        json={"interval_seconds": 60, "enabled": True},
    )
    assert scheduled.status_code == 200, scheduled.text

    def fail_health_write(*args, **kwargs):
        raise OSError("simulated health-store failure")

    monkeypatch.setattr(module.runtime.source_health_store, "upsert", fail_health_write)
    response = test_client.post("/api/source-ingest/run-scheduled", json={"max_concurrency": 1})

    assert response.status_code == 200, response.text
    assert response.json()["summary"]["total_failed"] == 1
    terminal_runs = [run for run in module.runtime.store.list_runs() if run.status.value == "completed"]
    assert len(terminal_runs) == 1
    receipt = module.runtime.store.get_receipt(terminal_runs[0].ingest_run_id)
    assert receipt is not None
    assert receipt.status == "failed"
    assert receipt.typed_failure == {
        "schema_version": "source_ingest_typed_failure.v1",
        "category": "persistence",
        "code": "post_processing_failed",
        "error_type": "OSError",
        "retryable": True,
        "stage": "source_health_usage",
    }

    reloaded_store = JsonlIngestScheduleStore(module.runtime.SCHEDULE_STORE_PATH)
    durable_run = reloaded_store.get_run(terminal_runs[0].ingest_run_id)
    durable_receipt = reloaded_store.get_receipt(terminal_runs[0].ingest_run_id)
    assert durable_run is not None and durable_run.status.value == "completed"
    assert durable_receipt is not None and durable_receipt.status == "failed"
    assert durable_receipt.typed_failure["code"] == "post_processing_failed"


def test_final_receipt_append_failure_is_rewritten_as_terminal_typed_failure(
    client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_client, _, module = client
    connector_id = "conn-final-receipt-retry"
    assert _configure_with_records(test_client, connector_id=connector_id).status_code == 201
    assert test_client.put(
        f"/api/source-ingest/connectors/{connector_id}/schedule",
        json={"interval_seconds": 60, "enabled": True},
    ).status_code == 200

    original_upsert = module.runtime.store.upsert_receipt
    receipt_writes = 0

    def fail_final_receipt_once(receipt):
        nonlocal receipt_writes
        receipt_writes += 1
        if receipt_writes == 2:
            raise OSError("simulated final receipt append failure")
        return original_upsert(receipt)

    monkeypatch.setattr(module.runtime.store, "upsert_receipt", fail_final_receipt_once)
    response = test_client.post("/api/source-ingest/run-scheduled", json={"max_concurrency": 1})

    assert response.status_code == 200, response.text
    assert response.json()["summary"]["total_failed"] == 1
    run = next(run for run in module.runtime.store.list_runs() if run.connector_id == connector_id)
    receipt = module.runtime.store.get_receipt(run.ingest_run_id)
    assert run.status.value == "completed"
    assert receipt is not None and receipt.status == "failed"
    assert receipt.typed_failure["code"] == "post_processing_failed"
    assert receipt.typed_failure["stage"] == "receipt_finalize"
    assert receipt_writes == 3

    durable_receipt = JsonlIngestScheduleStore(module.runtime.SCHEDULE_STORE_PATH).get_receipt(run.ingest_run_id)
    assert durable_receipt is not None and durable_receipt.status == "failed"
    assert durable_receipt.typed_failure["stage"] == "receipt_finalize"


def test_restart_recovers_processing_receipt_when_final_and_fallback_appends_fail(
    client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_client, _, module = client
    connector_id = "conn-final-receipt-restart"
    assert _configure_with_records(test_client, connector_id=connector_id).status_code == 201
    assert test_client.put(
        f"/api/source-ingest/connectors/{connector_id}/schedule",
        json={"interval_seconds": 60, "enabled": True},
    ).status_code == 200

    original_upsert = module.runtime.store.upsert_receipt

    def fail_terminal_receipts(receipt):
        if receipt.status != "processing":
            raise OSError("simulated persistent terminal receipt failure")
        return original_upsert(receipt)

    monkeypatch.setattr(module.runtime.store, "upsert_receipt", fail_terminal_receipts)
    response = test_client.post("/api/source-ingest/run-scheduled", json={"max_concurrency": 1})

    assert response.status_code == 200, response.text
    assert response.json()["summary"]["total_failed"] == 1
    run = next(run for run in module.runtime.store.list_runs() if run.connector_id == connector_id)
    stranded = module.runtime.store.get_receipt(run.ingest_run_id)
    assert run.status.value == "completed"
    assert stranded is not None and stranded.status == "processing"

    reloaded = JsonlIngestScheduleStore(module.runtime.SCHEDULE_STORE_PATH)
    recovered = reloaded.get_receipt(run.ingest_run_id)
    assert recovered is not None and recovered.status == "failed"
    assert recovered.typed_failure == {
        "schema_version": "source_ingest_typed_failure.v1",
        "category": "persistence",
        "code": "post_processing_interrupted",
        "error_type": "IncompleteReceiptRecovered",
        "retryable": True,
        "stage": "restart_recovery",
    }
    replayed = JsonlIngestScheduleStore(module.runtime.SCHEDULE_STORE_PATH).get_receipt(run.ingest_run_id)
    assert replayed is not None and replayed.to_dict() == recovered.to_dict()


def test_run_scheduled_skips_disabled_connector(client) -> None:
    test_client, _, _ = client
    test_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _connector(),
            "fetch": {"mode": "static_records", "records": []},
        },
    )
    test_client.put(
        "/api/source-ingest/connectors/conn-sched-notes/schedule",
        json={"interval_seconds": 1, "enabled": False},
    )

    response = test_client.post("/api/source-ingest/run-scheduled")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["summary"]["total_ran"] == 0
    assert body["summary"]["total_skipped"] == 1


def test_run_scheduled_frontier_retry_backoff_and_dlq_replay_are_durable(client) -> None:
    test_client, _, module = client
    configured = test_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _connector(connector_id="conn-sched-replay", source_type="internal_note"),
            "fetch": {
                "mode": "static_records",
                "next_watermark": "2026-04-30T02:00:00Z",
                "fail_until_attempt": 2,
                "failure_reason": "scheduled feed unavailable",
                "records": [
                    {
                        "source_id": "src-conn-sched-replay-note-1",
                        "title": "Replay scheduled note",
                        "content_ref": "memory://scheduled/replay/note-1",
                    }
                ],
            },
        },
    )
    assert configured.status_code == 201, configured.text
    scheduled = test_client.put(
        "/api/source-ingest/connectors/conn-sched-replay/schedule",
        json={"interval_seconds": 1, "enabled": True},
    )
    assert scheduled.status_code == 200, scheduled.text

    first = test_client.post("/api/source-ingest/run-scheduled", json={"max_concurrency": 1})
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["summary"]["total_failed"] == 1
    assert first_body["failed"][0]["frontier"]["status"] == "retry"
    assert first_body["failed"][0]["run"]["status"] == "failed"
    assert first_body["failed"][0]["run"]["ingest_run_id"]
    assert first_body["failed"][0]["frontier"]["ingest_run_id"] == first_body["failed"][0]["run"]["ingest_run_id"]

    retry_frontier = test_client.get("/api/source-ingest/frontier?status=retry")
    assert retry_frontier.status_code == 200
    assert len(retry_frontier.json()["frontier"]) == 1
    assert retry_frontier.json()["frontier"][0]["last_error"] == "scheduled feed unavailable"

    immediate_retry = test_client.post("/api/source-ingest/run-scheduled", json={"max_concurrency": 1})
    assert immediate_retry.status_code == 200, immediate_retry.text
    assert immediate_retry.json()["summary"]["total_ran"] == 0

    original_dlq_entry = module.runtime.dead_letter_queue.pending_entries(tag_filter="retry_exhausted")[0]
    correlated_dlq_entry = module.runtime.dead_letter_queue.reject(
        original_dlq_entry.event,
        reason="correlated duplicate failure receipt",
        tags=original_dlq_entry.tags,
        source_ref=original_dlq_entry.source_ref,
    )
    replay = test_client.post(
        "/api/source-ingest/dlq/replay",
        json={
            "tag": "retry_exhausted",
            "entry_ids": [original_dlq_entry.entry_id, correlated_dlq_entry.entry_id],
            "reason": "test scheduled frontier replay",
        },
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["summary"]["applied"] == 1
    assert replay.json()["summary"]["correlated_resolution_count"] == 2
    assert len(replay.json()["selected_entry_ids"]) == 1
    assert {
        item["entry_id"]
        for item in replay.json()["correlated_resolutions"]
    } == {original_dlq_entry.entry_id, correlated_dlq_entry.entry_id}
    assert all(item["status"] == "replayed" for item in replay.json()["correlated_resolutions"])
    assert all(item["explicitly_requested"] is True for item in replay.json()["correlated_resolutions"])

    done_frontier = test_client.get("/api/source-ingest/frontier?status=done")
    assert done_frontier.status_code == 200
    assert len(done_frontier.json()["frontier"]) == 1
    assert done_frontier.json()["frontier"][0]["trigger_type"] == "dlq_replay"
    source = test_client.get("/api/source-ingest/source-records/src-conn-sched-replay-note-1", headers=_read_headers())
    assert source.status_code == 200

    reloaded = importlib.reload(module)
    replay_client = TestClient(reloaded.app)
    durable_frontier = replay_client.get("/api/source-ingest/frontier?status=done")
    assert durable_frontier.status_code == 200
    assert len(durable_frontier.json()["frontier"]) == 1


def test_frontier_recovery_resolves_correlated_dlq_and_survives_reload(client) -> None:
    test_client, _, module = client
    configured = test_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _connector(connector_id="conn-auto-recovery", source_type="internal_note"),
            "fetch": {
                "mode": "static_records",
                "next_watermark": "2026-07-14T10:00:00Z",
                "fail_until_attempt": 2,
                "failure_reason": "bounded recovery fixture unavailable",
                "records": [
                    {
                        "source_id": "src-auto-recovery-note-1",
                        "title": "Recovered scheduled note",
                        "content_ref": "memory://scheduled/auto-recovery/note-1",
                    }
                ],
            },
        },
    )
    assert configured.status_code == 201, configured.text
    scheduled = test_client.put(
        "/api/source-ingest/connectors/conn-auto-recovery/schedule",
        json={"interval_seconds": 60, "enabled": True},
    )
    assert scheduled.status_code == 200, scheduled.text

    failed = test_client.post("/api/source-ingest/run-scheduled", json={"max_concurrency": 1})
    assert failed.status_code == 200, failed.text
    assert failed.json()["summary"]["total_failed"] == 1
    frontier_id = failed.json()["failed"][0]["frontier"]["frontier_id"]
    pending = test_client.get("/api/source-ingest/dlq")
    assert pending.json()["pending_count"] == 1
    assert pending.json()["unresolved_count"] == 1

    recovered = test_client.post(f"/api/source-ingest/frontier/{frontier_id}/replay")
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["run"]["status"] == "completed"
    assert recovered.json()["frontier"]["status"] == "done"
    resolved = test_client.get("/api/source-ingest/dlq").json()
    assert resolved["entry_count"] == 1
    assert resolved["pending_count"] == 0
    assert resolved["unresolved_count"] == 0
    assert resolved["status_counts"]["replayed"] == 1
    readback = test_client.get("/api/source-ingest/controller/readback").json()
    assert readback["dlq_count"] == 1
    assert readback["pending_dlq_count"] == 0
    assert readback["unresolved_dlq_count"] == 0

    reloaded = importlib.reload(module)
    replay_client = TestClient(reloaded.app)
    durable = replay_client.get("/api/source-ingest/dlq").json()
    assert durable["entry_count"] == 1
    assert durable["pending_count"] == 0
    assert durable["unresolved_count"] == 0
    assert durable["status_counts"]["replayed"] == 1


def test_replay_failed_entry_can_be_retried_to_terminal_recovery(client) -> None:
    test_client, _, _ = client
    configured = test_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _connector(connector_id="conn-replay-retry", source_type="internal_note"),
            "fetch": {
                "mode": "static_records",
                "next_watermark": "2026-07-14T10:15:00Z",
                "fail_until_attempt": 4,
                "failure_reason": "replay retry fixture unavailable",
                "records": [
                    {
                        "source_id": "src-replay-retry-note-1",
                        "title": "Replay retry recovered note",
                        "content_ref": "memory://scheduled/replay-retry/note-1",
                    }
                ],
            },
        },
    )
    assert configured.status_code == 201, configured.text
    test_client.put(
        "/api/source-ingest/connectors/conn-replay-retry/schedule",
        json={"interval_seconds": 60, "enabled": True},
    )
    failed = test_client.post("/api/source-ingest/run-scheduled", json={"max_concurrency": 1})
    entry_id = failed.json()["failed"][0]["run"]["ingest_run_id"]
    dlq_entry_id = test_client.get("/api/source-ingest/dlq").json()["entries"][0]["entry_id"]
    assert entry_id

    first_replay = test_client.post(
        "/api/source-ingest/dlq/replay",
        json={"entry_ids": [dlq_entry_id], "reason": "first replay remains unavailable"},
    )
    assert first_replay.status_code == 200, first_replay.text
    assert first_replay.json()["summary"]["failed"] == 1
    after_failure = test_client.get("/api/source-ingest/dlq").json()
    assert after_failure["unresolved_count"] == 2
    assert after_failure["status_counts"]["replay_failed"] == 1
    assert after_failure["status_counts"]["pending"] == 1

    second_replay = test_client.post(
        "/api/source-ingest/dlq/replay",
        json={"entry_ids": [dlq_entry_id], "reason": "upstream recovered for second replay"},
    )
    assert second_replay.status_code == 200, second_replay.text
    assert second_replay.json()["summary"]["applied"] == 1
    assert second_replay.json()["summary"]["correlated_resolution_count"] == 2
    terminal = test_client.get("/api/source-ingest/dlq").json()
    assert terminal["unresolved_count"] == 0
    assert terminal["status_counts"]["replayed"] == 2


def test_completed_frontier_sweep_repairs_crash_before_dlq_resolution(
    client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_client, _, module = client
    configured = test_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _connector(connector_id="conn-recovery-saga", source_type="internal_note"),
            "fetch": {
                "mode": "static_records",
                "next_watermark": "2026-07-14T10:30:00Z",
                "fail_until_attempt": 2,
                "failure_reason": "saga recovery fixture unavailable",
                "records": [
                    {
                        "source_id": "src-recovery-saga-note-1",
                        "title": "Saga recovered note",
                        "content_ref": "memory://scheduled/recovery-saga/note-1",
                    }
                ],
            },
        },
    )
    assert configured.status_code == 201, configured.text
    test_client.put(
        "/api/source-ingest/connectors/conn-recovery-saga/schedule",
        json={"interval_seconds": 60, "enabled": True},
    )
    failed = test_client.post("/api/source-ingest/run-scheduled", json={"max_concurrency": 1})
    frontier_id = failed.json()["failed"][0]["frontier"]["frontier_id"]
    original_append_audit = module.runtime._append_audit_actions

    def fail_recovery_audit(actions):
        if any(action.action_type == "source_ingestion.scheduled_run.recovered" for action in actions):
            raise OSError("simulated recovery audit fsync failure")
        return original_append_audit(actions)

    monkeypatch.setattr(module.runtime, "_append_audit_actions", fail_recovery_audit)
    with pytest.raises(OSError, match="simulated recovery audit fsync failure"):
        test_client.post(f"/api/source-ingest/frontier/{frontier_id}/replay")

    assert module.runtime.store.get_frontier(frontier_id).status == "done"
    assert test_client.get("/api/source-ingest/dlq").json()["pending_count"] == 1
    monkeypatch.setattr(module.runtime, "_append_audit_actions", original_append_audit)

    swept = test_client.post("/api/source-ingest/run-scheduled", json={"max_concurrency": 1})
    assert swept.status_code == 200, swept.text
    assert swept.json()["summary"]["resolved_dlq_count"] == 1
    assert test_client.get("/api/source-ingest/dlq").json()["unresolved_count"] == 0
    audit_actions = test_client.get("/api/source-ingest/audit").json()["actions"]
    recovery_actions = [
        action
        for action in audit_actions
        if action["action_type"] == "source_ingestion.scheduled_run.recovered"
    ]
    assert len(recovery_actions) == 1

    duplicate_sweep = test_client.post("/api/source-ingest/run-scheduled", json={"max_concurrency": 1})
    assert duplicate_sweep.status_code == 200, duplicate_sweep.text
    assert duplicate_sweep.json()["summary"]["resolved_dlq_count"] == 0
    assert len(
        [
            action
            for action in test_client.get("/api/source-ingest/audit").json()["actions"]
            if action["action_type"] == "source_ingestion.scheduled_run.recovered"
        ]
    ) == 1


def test_rejected_scheduled_run_is_reported_as_failed_not_ran(client) -> None:
    test_client, _, _ = client
    configured = test_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _connector(connector_id="conn-rejected-scheduled", source_type="internal_note"),
            "fetch": {
                "mode": "static_records",
                "records": [
                    {
                        "source_id": "src-rejected-scheduled-1",
                        "title": "Rejected scheduled note",
                        "content_ref": "memory://scheduled/rejected/note-1",
                        "status": "rejected",
                    }
                ],
            },
        },
    )
    assert configured.status_code == 201, configured.text
    test_client.put(
        "/api/source-ingest/connectors/conn-rejected-scheduled/schedule",
        json={"interval_seconds": 60, "enabled": True},
    )

    response = test_client.post("/api/source-ingest/run-scheduled", json={"max_concurrency": 1})

    assert response.status_code == 200, response.text
    assert response.json()["summary"]["total_ran"] == 0
    assert response.json()["summary"]["total_failed"] == 1
    assert response.json()["failed"][0]["run"]["status"] == "rejected"
    assert response.json()["failed"][0]["frontier"]["status"] == "retry"


def test_run_scheduled_skips_not_due_connector(client) -> None:
    test_client, _, _ = client
    configured = _configure_with_records(test_client)
    assert configured.status_code == 201, configured.text

    test_client.put(
        "/api/source-ingest/connectors/conn-sched-notes/schedule",
        json={"interval_seconds": 86400, "enabled": True},
    )
    # First run makes the connector not due for another 86400 seconds.
    first_run = test_client.post("/api/source-ingest/run-scheduled")
    assert first_run.status_code == 200, first_run.text
    assert first_run.json()["summary"]["total_ran"] == 1

    # Second run immediately - interval not elapsed.
    response = test_client.post("/api/source-ingest/run-scheduled")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["summary"]["total_ran"] == 0
    assert body["summary"]["total_skipped"] == 1


def test_registry_reports_connector_freshness_after_scheduled_run(client) -> None:
    test_client, _, _ = client
    configured = _configure_with_records(test_client)
    assert configured.status_code == 201, configured.text

    test_client.put(
        "/api/source-ingest/connectors/conn-sched-notes/schedule",
        json={"interval_seconds": 86400, "enabled": True},
    )

    before = test_client.get("/api/source-ingest/registry")
    assert before.status_code == 200, before.text
    before_entry = next(
        connector
        for connector in before.json()["connectors"]
        if connector["connector_id"] == "conn-sched-notes"
    )
    assert before_entry["freshness"]["status"] == "never_ingested"
    assert before_entry["freshness"]["is_due"] is True

    run = test_client.post("/api/source-ingest/run-scheduled")
    assert run.status_code == 200, run.text
    assert run.json()["summary"]["total_ran"] == 1

    after = test_client.get("/api/source-ingest/registry")
    assert after.status_code == 200, after.text
    entry = next(
        connector
        for connector in after.json()["connectors"]
        if connector["connector_id"] == "conn-sched-notes"
    )
    freshness = entry["freshness"]
    assert freshness["schema_version"] == "source_connector_freshness.v2"
    assert freshness["status"] == "fresh"
    assert freshness["stale"] is False
    assert freshness["is_due"] is False
    assert freshness["last_ingest_run_id"]
    assert freshness["source_timestamp"]
    assert freshness["age_seconds"] >= 0
    assert freshness["stale_threshold_seconds"] >= 86400
    assert freshness["next_run_at"] == freshness["next_due_at"]
    assert freshness["last_typed_failure"] is None
    assert freshness["latest_receipt"]["status"] == "completed"
    receipt_summary = freshness["latest_receipt"]
    assert receipt_summary["schema_version"] == "source_ingest_receipt_summary.v1"
    assert receipt_summary["normalized_count"] == 1
    assert receipt_summary["evidence_refs"]["source_id_count"] == 1
    assert "source_ids" not in receipt_summary["evidence_refs"]
    full_receipt = test_client.get(
        f"/api/source-ingest/receipts/{receipt_summary['ingest_run_id']}"
    )
    assert full_receipt.status_code == 200, full_receipt.text
    assert len(full_receipt.json()["receipt"]["evidence_refs"]["source_ids"]) == 1
    assert freshness["latest_run"]["status"] == "completed"
    assert freshness["seconds_until_due"] > 0
    health = entry["health_metrics"]
    assert health["schema_version"] == "source_connector_health_metrics.v1"
    assert health["last_success_at"] == freshness["last_success_at"]
    assert health["row_count"] == 1
    assert health["expected_rows"] == 1
    assert health["schema_hash"]
    assert health["source_error"] is None


def test_stale_source_remains_readable_with_explicit_readiness_truth(client) -> None:
    test_client, _, _ = client
    configured = test_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _connector(
                connector_id="conn-stale-readable",
                metadata={"stale_threshold_seconds": 60},
            ),
            "fetch": {
                "mode": "static_records",
                "records": [
                    {
                        "source_id": "src-stale-readable-1",
                        "title": "Persisted stale source",
                        "content_ref": "memory://scheduled/stale/source-1",
                        "metadata": {"available_time": "2020-01-01T00:00:00Z"},
                    }
                ],
            },
        },
    )
    assert configured.status_code == 201, configured.text
    test_client.put(
        "/api/source-ingest/connectors/conn-stale-readable/schedule",
        json={"interval_seconds": 60, "enabled": True},
    )
    run = test_client.post("/api/source-ingest/run-scheduled", json={"max_concurrency": 1})
    assert run.status_code == 200, run.text

    registry = test_client.get("/api/source-ingest/registry").json()
    entry = next(item for item in registry["connectors"] if item["connector_id"] == "conn-stale-readable")
    assert entry["freshness"]["status"] == "stale"
    assert entry["freshness"]["stale"] is True
    assert entry["freshness"]["age_seconds"] > entry["freshness"]["stale_threshold_seconds"]

    persisted = test_client.get("/api/source-ingest/source-records/src-stale-readable-1", headers=_read_headers())
    assert persisted.status_code == 200


@pytest.mark.parametrize(
    ("connector_id", "metadata", "expected_status"),
    [
        ("conn-source-time-missing", {}, "missing"),
        (
            "conn-source-time-future",
            {"available_time": "2099-01-01T00:00:00Z"},
            "future",
        ),
        (
            "conn-source-time-invalid",
            {"available_time": "not-a-provider-timestamp"},
            "invalid",
        ),
    ],
)
def test_unknown_or_future_source_time_is_never_reported_fresh(
    client,
    connector_id: str,
    metadata: dict[str, str],
    expected_status: str,
) -> None:
    test_client, _, _ = client
    configured = test_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _connector(connector_id=connector_id),
            "fetch": {
                "mode": "static_records",
                "records": [
                    {
                        "source_id": f"src-{connector_id}",
                        "title": "Source-time truth fixture",
                        "content_ref": f"memory://scheduled/{connector_id}",
                        "metadata": metadata,
                    }
                ],
            },
        },
    )
    assert configured.status_code == 201, configured.text
    test_client.put(
        f"/api/source-ingest/connectors/{connector_id}/schedule",
        json={"interval_seconds": 86400, "enabled": True},
    )

    run = test_client.post("/api/source-ingest/run-scheduled", json={"max_concurrency": 1})

    assert run.status_code == 200, run.text
    receipt = run.json()["ran"][0]["receipt"]
    assert receipt["source_timestamp_status"] == expected_status
    registry = test_client.get("/api/source-ingest/registry").json()
    entry = next(item for item in registry["connectors"] if item["connector_id"] == connector_id)
    freshness = entry["freshness"]
    assert freshness["status"] == "stale"
    assert freshness["stale"] is True
    assert freshness["source_timestamp_status"] == expected_status
    assert freshness["age_seconds"] is None
    ready = test_client.get("/readyz")
    assert ready.status_code == 200
    # Readiness is a bounded controller-state probe.  Detailed source
    # freshness remains available through the connector registry above rather
    # than replaying journals from a health endpoint.
    assert ready.json()["dependencies"]["source_freshness"]["status"] == "not_observed"
    assert ready.json()["dependencies"]["source_freshness"]["data_ready"] is False


def test_active_universe_plan_endpoint_uses_default_low_cost_rules(client) -> None:
    test_client, _, _ = client

    response = test_client.post(
        "/api/source-ingest/active-universe/plan",
        json={
            "members": [
                {"symbol": "2330", "tier": "core_universe", "reason": "holding"},
                {"symbol": "2317", "tier": "candidate_universe", "reason": "watchlist"},
                {"symbol": "6488", "tier": "archive_universe", "reason": "removed"},
            ]
        },
    )

    assert response.status_code == 200, response.text
    plan = response.json()
    broker_update = next(
        update for update in plan["connector_updates"] if update["connector_id"] == "tw-finmind-broker-daily-report"
    )
    assert broker_update["symbols"] == ["2330", "2317"]
    assert "fallback_connector_id" not in broker_update["metadata"]
    assert "6488" not in broker_update["symbols"]
    assert plan["summary"]["archive_detail_updates_skipped"] == ["6488"]


def test_policy_registry_reports_crawler_guards_and_rate_limits(client) -> None:
    test_client, _, _ = client
    configured = test_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _connector(
                connector_id="conn-news-policy",
                source_type="news",
                license_scope="vendor",
                rate_limit_policy={
                    "requests_per_minute": 30,
                    "burst": 5,
                    "retry_after_seconds": 60,
                    "policy_ref": "source-ingest://policy/news-policy",
                },
                license_policy={
                    "license_scope": "vendor",
                    "allowed_use": ["research", "search_index"],
                    "policy_ref": "source-ingest://license/news-policy",
                },
                metadata={"entitlement_tags": ["news-policy-research"], "access_scope": ["research"]},
            ),
            "fetch": {
                "mode": "external_feed",
                "url": "https://feeds.example.test/news.json",
                "allowed_url_prefixes": ["https://feeds.example.test/"],
                "timeout_seconds": 3,
                "max_bytes": 32768,
                "max_records": 7,
                "default_access_scope": ["research"],
            },
        },
    )
    assert configured.status_code == 201, configured.text
    scheduled = test_client.put(
        "/api/source-ingest/connectors/conn-news-policy/schedule",
        json={"interval_seconds": 300, "enabled": True},
    )
    assert scheduled.status_code == 200, scheduled.text

    response = test_client.get("/api/source-ingest/policy-registry")
    assert response.status_code == 200, response.text
    registry = response.json()
    policy = next(
        policy for policy in registry["connector_policies"] if policy["connector_id"] == "conn-news-policy"
    )

    assert registry["schema_version"] == "source_crawler_indexer_policy_registry.v1"
    assert registry["default_guards"]["bounded_crawler_adapters_only"] is True
    assert policy["crawler"]["adapter_type"] == "bounded_external_feed_crawler"
    assert policy["crawler"]["allowlist_enforced"] is True
    assert policy["crawler"]["allowed_url_hosts"] == ["feeds.example.test"]
    assert policy["crawler"]["max_records"] == 7
    assert policy["crawler"]["request_documents_compat"] is False
    assert policy["guards"]["rate_limit"]["requests_per_minute"] == 30
    assert policy["guards"]["license"]["allowed_use"] == ["research", "search_index"]
    assert policy["guards"]["pit_required"] is True
    assert policy["lifecycle"]["ready_for_scheduled_crawl"] is True
    assert policy["indexer"]["normal_search_path"] == "durable_index"
    assert registry["summary"]["external_allowlist_policy_count"] >= 1
    assert registry["summary"]["pit_policy_count"] >= 1


def test_connector_lifecycle_disable_blocks_runs_and_records_audit(client) -> None:
    test_client, _, _ = client
    configured = _configure_with_records(test_client, connector_id="conn-lifecycle-notes")
    assert configured.status_code == 201, configured.text
    scheduled = test_client.put(
        "/api/source-ingest/connectors/conn-lifecycle-notes/schedule",
        json={"interval_seconds": 1, "enabled": True},
    )
    assert scheduled.status_code == 200, scheduled.text

    lifecycle = test_client.put(
        "/api/source-ingest/connectors/conn-lifecycle-notes/lifecycle",
        json={
            "status": "disabled",
            "reason": "license review hold",
            "actor_id": "operator-test",
            "trace_id": "trace-lifecycle-test",
        },
    )
    assert lifecycle.status_code == 200, lifecycle.text
    body = lifecycle.json()
    assert body["connector"]["status"] == "disabled"
    assert body["lifecycle"]["reason"] == "license review hold"
    assert body["audit_action"]["action_type"] == "source_ingestion.connector_lifecycle.updated"

    manual = test_client.post(
        "/api/source-ingest/jobs",
        json={"connector_id": "conn-lifecycle-notes", "trace_id": "trace-disabled-manual"},
    )
    assert manual.status_code == 400
    assert "disabled rejects ingest runs" in manual.json()["detail"]

    scheduled_run = test_client.post("/api/source-ingest/run-scheduled")
    assert scheduled_run.status_code == 200, scheduled_run.text
    assert scheduled_run.json()["summary"]["total_ran"] == 0
    assert scheduled_run.json()["summary"]["total_skipped"] == 1

    registry = test_client.get("/api/source-ingest/registry").json()
    entry = next(conn for conn in registry["connectors"] if conn["connector_id"] == "conn-lifecycle-notes")
    assert entry["status"] == "disabled"
    assert entry["crawler_policy"]["lifecycle"]["ready_for_scheduled_crawl"] is False

    audit = test_client.get("/api/source-ingest/audit")
    assert audit.status_code == 200
    assert any(
        action["action_type"] == "source_ingestion.connector_lifecycle.updated"
        and action["metadata"]["connector_id"] == "conn-lifecycle-notes"
        and action["metadata"]["next_status"] == "disabled"
        and action["payload_checksum"]
        for action in audit.json()["actions"]
    )


def test_set_schedule_returns_404_for_unknown_connector(client) -> None:
    test_client, _, _ = client

    response = test_client.put(
        "/api/source-ingest/connectors/conn-unknown/schedule",
        json={"interval_seconds": 60, "enabled": True},
    )
    assert response.status_code == 404


def test_get_schedule_returns_404_when_not_configured(client) -> None:
    test_client, _, _ = client
    test_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _connector(),
            "fetch": {"mode": "static_records", "records": []},
        },
    )

    response = test_client.get("/api/source-ingest/connectors/conn-sched-notes/schedule")
    assert response.status_code == 404


def test_run_scheduled_returns_empty_when_no_schedules_configured(client) -> None:
    test_client, _, _ = client

    response = test_client.post("/api/source-ingest/run-scheduled")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["summary"]["total_ran"] == 0
    assert body["summary"]["total_skipped"] == 0
    assert body["summary"]["total_failed"] == 0
    assert body["ran"] == []


def test_freshness_summary_taiwan_official_weekend_accepts_friday_close() -> None:
    from datetime import datetime, timezone
    from services.source_ingestion.runtime import SourceIngestionRuntime
    from services.source_ingestion.scheduler import IngestReceipt
    from services.source_ingestion.connectors.base import SourceRecord

    runtime = SourceIngestionRuntime()
    now = datetime(2026, 8, 30, 1, 0, 0, tzinfo=timezone.utc)  # Sunday
    receipts = [
        IngestReceipt(
            ingest_run_id="run-tw-weekend",
            connector_id="tw-twse-tpex-official-market",
            status="completed",
            trigger_type="manual_one_shot",
            trace_id="trace-test",
            started_at="2026-08-30T00:59:55Z",
            finished_at="2026-08-30T01:00:00Z",
            raw_count=1,
            normalized_count=1,
            rejected_count=0,
            watermark=None,
            source_timestamp="2026-08-28T05:30:00Z",  # Friday close
            source_timestamp_status="valid",
        )
    ]
    record = SourceRecord(
        source_id="tw-official:tw_price_daily:TWSE:2330:weekend",
        connector_id="tw-twse-tpex-official-market",
        source_type="market",
        title="2330 Daily Close",
        content_ref="tw-official://tw_price_daily/TWSE/2330/2026-08-28",
        status="normalized",
        trace_id="trace-test",
        created_at="2026-08-30T01:00:00Z",
        metadata={
            "provider": "TWSE OpenAPI",
            "dataset": "tw_price_daily",
            "available_time": "2026-08-28T05:30:00Z",
        },
    )

    summary = runtime._connector_freshness_summary_from_snapshot(
        "tw-twse-tpex-official-market",
        connector_metadata={"market": "TW", "venue": "TWSE"},
        schedule=None,
        watermark=None,
        runs=[],
        receipts=receipts,
        now=now,
        latest_record=record,
    )

    assert summary["stale"] is False
    assert summary["status"] == "unscheduled"
    assert summary["source_timestamp_status"] == "valid"


def test_freshness_summary_taiwan_official_rejects_stale_weekday() -> None:
    from datetime import datetime, timezone
    from services.source_ingestion.runtime import SourceIngestionRuntime
    from services.source_ingestion.scheduler import IngestReceipt
    from services.source_ingestion.connectors.base import SourceRecord

    runtime = SourceIngestionRuntime()
    now = datetime(2026, 8, 28, 6, 0, 0, tzinfo=timezone.utc)  # Friday afternoon after close
    receipts = [
        IngestReceipt(
            ingest_run_id="run-tw-stale",
            connector_id="tw-twse-tpex-official-market",
            status="completed",
            trigger_type="manual_one_shot",
            trace_id="trace-test",
            started_at="2026-08-28T05:59:55Z",
            finished_at="2026-08-28T06:00:00Z",
            raw_count=1,
            normalized_count=1,
            rejected_count=0,
            watermark=None,
            source_timestamp="2026-08-25T05:30:00Z",  # Tuesday close (stale without holiday proof)
            source_timestamp_status="valid",
        )
    ]
    record = SourceRecord(
        source_id="tw-official:tw_price_daily:TWSE:2330:tuesday",
        connector_id="tw-twse-tpex-official-market",
        source_type="market",
        title="2330 Daily Close",
        content_ref="tw-official://tw_price_daily/TWSE/2330/2026-08-25",
        status="normalized",
        trace_id="trace-test",
        created_at="2026-08-28T06:00:00Z",
        metadata={
            "provider": "TWSE OpenAPI",
            "dataset": "tw_price_daily",
            "available_time": "2026-08-25T05:30:00Z",
        },
    )

    summary = runtime._connector_freshness_summary_from_snapshot(
        "tw-twse-tpex-official-market",
        connector_metadata={"market": "TW", "venue": "TWSE"},
        schedule=None,
        watermark=None,
        runs=[],
        receipts=receipts,
        now=now,
        latest_record=record,
    )

    assert summary["stale"] is True


def test_freshness_summary_taiwan_official_rejects_non_official_lineage() -> None:
    from datetime import datetime, timezone
    from services.source_ingestion.runtime import SourceIngestionRuntime
    from services.source_ingestion.scheduler import IngestReceipt
    from services.source_ingestion.connectors.base import SourceRecord

    runtime = SourceIngestionRuntime()
    now = datetime(2026, 8, 30, 1, 0, 0, tzinfo=timezone.utc)
    receipts = [
        IngestReceipt(
            ingest_run_id="run-tw-mock",
            connector_id="tw-twse-tpex-official-market",
            status="completed",
            trigger_type="manual_one_shot",
            trace_id="trace-test",
            started_at="2026-08-30T00:59:55Z",
            finished_at="2026-08-30T01:00:00Z",
            raw_count=1,
            normalized_count=1,
            rejected_count=0,
            watermark=None,
            source_timestamp="2026-08-28T05:30:00Z",
            source_timestamp_status="valid",
        )
    ]
    record = SourceRecord(
        source_id="mock-vendor:tw_price_daily:TWSE:2330:mock",  # non-official lineage
        connector_id="tw-twse-tpex-official-market",
        source_type="market",
        title="2330 Daily Close",
        content_ref="mock-vendor://tw_price_daily/TWSE/2330/2026-08-28",
        status="normalized",
        trace_id="trace-test",
        created_at="2026-08-30T01:00:00Z",
        metadata={
            "provider": "Mock Provider",
            "dataset": "tw_price_daily",
            "available_time": "2026-08-28T05:30:00Z",
        },
    )

    summary = runtime._connector_freshness_summary_from_snapshot(
        "tw-twse-tpex-official-market",
        connector_metadata={"market": "TW", "venue": "TWSE"},
        schedule=None,
        watermark=None,
        runs=[],
        receipts=receipts,
        now=now,
        latest_record=record,
    )

    assert summary["stale"] is True


def test_freshness_summary_taiwan_official_rejects_missing_latest_record() -> None:
    from datetime import datetime, timezone
    from services.source_ingestion.runtime import SourceIngestionRuntime
    from services.source_ingestion.scheduler import IngestReceipt

    runtime = SourceIngestionRuntime()
    now = datetime(2026, 8, 30, 1, 0, 0, tzinfo=timezone.utc)
    receipts = [
        IngestReceipt(
            ingest_run_id="run-tw-weekend",
            connector_id="tw-twse-tpex-official-market",
            status="completed",
            trigger_type="manual_one_shot",
            trace_id="trace-test",
            started_at="2026-08-30T00:59:55Z",
            finished_at="2026-08-30T01:00:00Z",
            raw_count=1,
            normalized_count=1,
            rejected_count=0,
            watermark=None,
            source_timestamp="2026-08-28T05:30:00Z",
            source_timestamp_status="valid",
        )
    ]

    summary = runtime._connector_freshness_summary_from_snapshot(
        "tw-twse-tpex-official-market",
        connector_metadata={"market": "TW", "venue": "TWSE"},
        schedule=None,
        watermark=None,
        runs=[],
        receipts=receipts,
        now=now,
        latest_record=None,
    )

    assert summary["stale"] is True


def test_freshness_summary_taiwan_official_rejects_missing_source_id() -> None:
    from datetime import datetime, timezone
    from services.source_ingestion.runtime import SourceIngestionRuntime
    from services.source_ingestion.scheduler import IngestReceipt
    from services.source_ingestion.connectors.base import SourceRecord

    runtime = SourceIngestionRuntime()
    now = datetime(2026, 8, 30, 1, 0, 0, tzinfo=timezone.utc)
    receipts = [
        IngestReceipt(
            ingest_run_id="run-tw-weekend",
            connector_id="tw-twse-tpex-official-market",
            status="completed",
            trigger_type="manual_one_shot",
            trace_id="trace-test",
            started_at="2026-08-30T00:59:55Z",
            finished_at="2026-08-30T01:00:00Z",
            raw_count=1,
            normalized_count=1,
            rejected_count=0,
            watermark=None,
            source_timestamp="2026-08-28T05:30:00Z",
            source_timestamp_status="valid",
        )
    ]
    record = type(
        "MockRecord",
        (),
        {
            "source_id": "",
            "connector_id": "tw-twse-tpex-official-market",
            "metadata": {},
        },
    )()

    summary = runtime._connector_freshness_summary_from_snapshot(
        "tw-twse-tpex-official-market",
        connector_metadata={"market": "TW", "venue": "TWSE"},
        schedule=None,
        watermark=None,
        runs=[],
        receipts=receipts,
        now=now,
        latest_record=record,
    )

    assert summary["stale"] is True


def test_freshness_summary_taiwan_official_rejects_missing_receipt() -> None:
    from datetime import datetime, timezone
    from services.source_ingestion.runtime import SourceIngestionRuntime
    from services.source_ingestion.connectors.base import SourceRecord

    runtime = SourceIngestionRuntime()
    now = datetime(2026, 8, 30, 1, 0, 0, tzinfo=timezone.utc)
    record = SourceRecord(
        source_id="tw-official:tw_price_daily:TWSE:2330:weekend",
        connector_id="tw-twse-tpex-official-market",
        source_type="market",
        title="2330 Daily Close",
        content_ref="tw-official://tw_price_daily/TWSE/2330/2026-08-28",
        status="normalized",
        trace_id="trace-test",
        created_at="2026-08-30T01:00:00Z",
        metadata={
            "provider": "TWSE OpenAPI",
            "dataset": "tw_price_daily",
            "available_time": "2026-08-28T05:30:00Z",
        },
    )

    summary = runtime._connector_freshness_summary_from_snapshot(
        "tw-twse-tpex-official-market",
        connector_metadata={"market": "TW", "venue": "TWSE"},
        schedule=None,
        watermark=None,
        runs=[],
        receipts=[],
        now=now,
        latest_record=record,
    )

    assert summary["stale"] is True


def test_freshness_summary_taiwan_official_rejects_unparsable_receipt() -> None:
    from datetime import datetime, timezone
    from services.source_ingestion.runtime import SourceIngestionRuntime
    from services.source_ingestion.scheduler import IngestReceipt
    from services.source_ingestion.connectors.base import SourceRecord

    runtime = SourceIngestionRuntime()
    now = datetime(2026, 8, 30, 1, 0, 0, tzinfo=timezone.utc)
    receipts = [
        IngestReceipt(
            ingest_run_id="run-tw-weekend",
            connector_id="tw-twse-tpex-official-market",
            status="completed",
            trigger_type="manual_one_shot",
            trace_id="trace-test",
            started_at="2026-08-30T00:59:55Z",
            finished_at="not-a-valid-timestamp",
            raw_count=1,
            normalized_count=1,
            rejected_count=0,
            watermark=None,
            source_timestamp="2026-08-28T05:30:00Z",
            source_timestamp_status="valid",
        )
    ]
    record = SourceRecord(
        source_id="tw-official:tw_price_daily:TWSE:2330:weekend",
        connector_id="tw-twse-tpex-official-market",
        source_type="market",
        title="2330 Daily Close",
        content_ref="tw-official://tw_price_daily/TWSE/2330/2026-08-28",
        status="normalized",
        trace_id="trace-test",
        created_at="2026-08-30T01:00:00Z",
        metadata={
            "provider": "TWSE OpenAPI",
            "dataset": "tw_price_daily",
            "available_time": "2026-08-28T05:30:00Z",
        },
    )

    summary = runtime._connector_freshness_summary_from_snapshot(
        "tw-twse-tpex-official-market",
        connector_metadata={"market": "TW", "venue": "TWSE"},
        schedule=None,
        watermark=None,
        runs=[],
        receipts=receipts,
        now=now,
        latest_record=record,
    )

    assert summary["stale"] is True


# --- COLLECTOR-STEADY-PULL-RESTORE-20261112: real controller -> scheduler -> Collector adapter ---

_STEADY_HOST = "collector.example.test"
_STEADY_COLLECTOR_ID = "collector-tw-ptt-stock"


def _steady_collector_config(connector_id: str, *, definition_id: str = _STEADY_COLLECTOR_ID) -> dict:
    from services.source_ingestion.connectors.collector_bundle import CollectorBundleAdapter

    adapter = CollectorBundleAdapter(
        connector_id=connector_id, source_id=_STEADY_COLLECTOR_ID, source_type="social", platform="ptt",
        host=_STEADY_HOST, max_records=10,
    )
    full = adapter.connector().to_dict()
    allowed = ("connector_id", "source_type", "provider", "license_scope", "auth_type", "supported_modes", "auth_policy",
               "rate_limit_policy", "license_policy", "source_metadata", "metadata")
    connector = {key: full[key] for key in allowed if key in full}
    connector["metadata"] = {**connector["metadata"], "tenant_id": "tenant-a", "definition_id": definition_id}
    fetch = dict(adapter.fetch_config())
    fetch["adapter_config"] = {
        **fetch["adapter_config"], "source_type": "social", "platform": "ptt", "host": _STEADY_HOST,
    }
    return {"connector": connector, "fetch": fetch}


@pytest.fixture()
def steady_stack(client, monkeypatch: pytest.MonkeyPatch):
    from services.source_ingestion import controller_worker
    from services.source_ingestion.connectors import collector_bundle
    from services.source_ingestion.connectors.collector_bundle import CollectorBundleAdapter
    from services.source_ingestion.tests.test_collector_bundle_connector import FakeTransport, make_bundle

    test_client, tempdir, module = client
    monkeypatch.setenv("PANTHEON_COLLECTOR_HOST", _STEADY_HOST)
    monkeypatch.setenv("SOURCE_INGEST_MARKET_DATA_STORAGE_ROOT", str(tempdir / "market"))
    files, entry, _ = make_bundle(source_id=_STEADY_COLLECTOR_ID)
    files["bundles/index.json"] = __import__("json").dumps({"bundles": [entry]}).encode()
    files["status.json"] = __import__("json").dumps({"sources": {_STEADY_COLLECTOR_ID: {
        "status": "ok", "last_run_at": entry["finished_at"],
    }}}).encode()
    observed = collector_bundle.observed_from_collector_status
    monkeypatch.setattr(collector_bundle, "observed_from_collector_status", lambda *a, **kw: observed(
        *a, **{**kw, "now": datetime(2026, 11, 10, 3, tzinfo=timezone.utc)}))
    reads: list[str] = []

    class RecordingTransport(FakeTransport):
        def read(self, relative_path: str, *, max_bytes: int) -> bytes:
            reads.append(relative_path)
            return super().read(relative_path, max_bytes=max_bytes)

    monkeypatch.setattr(CollectorBundleAdapter, "_transport", lambda self: RecordingTransport(files))

    def local_request(url, *, method="GET", payload=None, bearer_token=None, timeout_seconds=30.0, allow_list=False):
        path = "/" + url.split("//", 1)[1].split("/", 1)[1]
        headers = {"Authorization": f"Bearer {module.runtime.controller_token}"} if bearer_token else None
        response = test_client.request(method, path, json=payload, headers=headers)
        assert response.status_code < 400, (path, response.text)
        return response.json()

    monkeypatch.setattr(controller_worker, "_request_json", local_request)
    return test_client, module, reads


def _steady_reconcile(connector_ids):
    from services.source_ingestion.tests import test_controller_worker as tcw

    return tcw._collector_reconcile(*connector_ids)


def _steady_step(reconcile):
    """The steady branch of run_controller_tick, using its real selection, scheduler and readback calls."""
    from services.source_ingestion import controller_worker as cw

    api = "http://source-ingest.test:8097"
    actual = cw.read_actual_state_with_fetch_bindings(api_url=api)
    selected = cw.select_collector_pull_connectors(actual, reconcile)
    schedule = (
        cw.run_schedule_tick(
            api_url=api, max_concurrency=1, force_connector_ids=[], exclusive_connector_ids=selected,
            controller_token="controller-test-token-that-is-at-least-32-characters",
        )
        if selected
        else None
    )
    return selected, schedule


def test_steady_controller_calls_drive_real_scheduler_and_collector_adapter(steady_stack) -> None:
    import time

    test_client, module, reads = steady_stack
    cid = "conn-collector-ptt"
    created = test_client.post("/api/source-ingest/connectors", json=_steady_collector_config(cid))
    assert created.status_code == 201, created.text
    assert test_client.put(f"/api/source-ingest/connectors/{cid}/schedule", json={"interval_seconds": 6, "enabled": True}).status_code == 200
    # A due, scheduled, enabled direct-provider connector must never be pulled by the steady loop.
    assert _configure_with_records(test_client, connector_id="conn-direct-provider").status_code == 201
    assert test_client.put("/api/source-ingest/connectors/conn-direct-provider/schedule", json={"interval_seconds": 1, "enabled": True}).status_code == 200
    reconcile = _steady_reconcile([cid, "conn-direct-provider"])

    # Tick 1: due -> the real scheduler runs the real registered Collector adapter via the fake transport.
    selected, schedule = _steady_step(reconcile)
    assert selected == [cid]
    assert [item["connector_id"] for item in schedule["ran"]] == [cid]
    assert schedule["excluded"] == ["conn-direct-provider"]
    assert schedule["summary"]["total_failed"] == 0
    pulled_after_first = len(reads)
    assert pulled_after_first > 0
    assert schedule["ran"][0]["run"]["status"] == "completed"
    assert schedule["ran"][0]["receipt"]["normalized_count"] == 2

    # Readback after the pull: record, PIT time, provenance and persisted cursor, with no direct-provider record.
    readback = test_client.get("/api/source-ingest/controller/readback").json()
    item = next(c for c in readback["connectors"] if c["connector_id"] == cid)
    record = item["latest_source_record"]
    assert record["connector_id"] == cid and record["source_id"].startswith(f"collector:{_STEADY_COLLECTOR_ID}:")
    assert record["provenance"]["available_time"] or record["metadata"].get("available_time")
    assert item["freshness"]["last_success_at"]
    direct = next(c for c in readback["connectors"] if c["connector_id"] == "conn-direct-provider")
    assert not direct.get("latest_source_record")
    assert test_client.get("/api/source-ingest/source-records/src-conn-direct-provider-note-1", headers=_read_headers()).status_code == 404

    # Tick 2 within the cadence: nothing is selected, no scheduler call and no adapter read.
    selected, schedule = _steady_step(reconcile)
    assert selected == [] and schedule is None
    assert len(reads) == pulled_after_first

    # Tick 3 after the interval: due again, the real adapter is invoked again.
    time.sleep(6.2)
    selected, schedule = _steady_step(reconcile)
    assert selected == [cid]
    assert schedule["summary"]["total_failed"] == 0
    assert len(reads) > pulled_after_first
    assert schedule["ran"][0]["run"]["raw_count"] == 0
    assert schedule["ran"][0]["receipt"]["normalized_count"] == 0
    final = test_client.get("/api/source-ingest/controller/readback").json()
    item = next(c for c in final["connectors"] if c["connector_id"] == cid)
    assert item["latest_source_record"] == record
    assert item["fetch_state"]["collector_outcome"]["no_new_data"] is True
    assert final["source_record_count"] == 2


def test_collector_empty_then_paused_preserves_source_records_freshness_and_health(client, monkeypatch):
    import json
    from services.source_ingestion.tests.test_collector_outcome_truth import CURSOR, SOURCE, setup_path

    http, data_dir, module = client
    files, _, _, store, _ = setup_path(data_dir, monkeypatch)
    config = store.get_config("conn-truth")
    connector = config.connector.to_dict()
    connector.pop("schema_version")
    connector["metadata"] = {**connector["metadata"], "tenant_id": "tenant-a"}
    response = http.post("/api/source-ingest/connectors", json={"connector": connector, "fetch": config.fetch})
    assert response.status_code == 201, response.text
    assert http.put("/api/source-ingest/connectors/conn-truth/schedule",
                    json={"interval_seconds": 86400, "enabled": True}).status_code == 200

    def tick():
        response = http.post("/api/source-ingest/run-scheduled", json={"force_connector_ids": ["conn-truth"]})
        assert response.status_code == 200, response.text
        return response.json()

    first = tick()
    assert first["ran"][0]["receipt"]["normalized_count"] == 2
    before = [r.to_dict() for r in module.runtime.evidence_repository.list_source_records()]
    second = tick()
    receipt = second["ran"][0]["receipt"]
    assert receipt["raw_count"] == receipt["normalized_count"] == 0
    assert receipt["source_timestamp"] is None
    assert [r.to_dict() for r in module.runtime.evidence_repository.list_source_records()] == before
    readback = http.get("/api/source-ingest/controller/readback").json()
    item = next(c for c in readback["connectors"] if c["connector_id"] == "conn-truth")
    assert item["fetch_state"]["collector_outcome"]["no_new_data"] is True
    assert item["freshness"]["stale"] is True
    assert item["source_health"]["row_count_last_run"] == 0
    assert item["source_health"]["latest_watermark"] == CURSOR
    latest = item["latest_source_record"]

    files["status.json"] = json.dumps({"sources": {SOURCE: {
        "status": "paused", "last_run_at": "2026-11-10T02:05:00Z", "paused_reason": "login_required",
    }}}).encode()
    failed = tick()
    assert failed["summary"]["total_failed"] == 1 and failed["summary"]["total_ran"] == 0
    assert failed["failed"][0]["receipt"]["normalized_count"] == 0
    assert [r.to_dict() for r in module.runtime.evidence_repository.list_source_records()] == before
    item = next(c for c in http.get("/api/source-ingest/controller/readback").json()["connectors"]
                if c["connector_id"] == "conn-truth")
    assert item["latest_source_record"] == latest
    assert item["source_health"]["status"] == "failed"
    assert "login_required" in item["source_health"]["metadata"]["source_error"]
    assert item["source_health"]["latest_watermark"] == CURSOR
    assert item["fetch_state"]["collector_outcome"]["observed"]["health_state"] == "degraded"


def test_steady_controller_calls_exclude_disabled_collector(steady_stack) -> None:
    test_client, module, reads = steady_stack
    cid = "conn-collector-disabled"
    assert test_client.post("/api/source-ingest/connectors", json=_steady_collector_config(cid)).status_code == 201
    assert test_client.put(f"/api/source-ingest/connectors/{cid}/schedule", json={"interval_seconds": 1, "enabled": True}).status_code == 200
    lifecycle = test_client.put(
        f"/api/source-ingest/connectors/{cid}/lifecycle",
        json={"status": "disabled", "reason": "hold", "actor_id": "op", "trace_id": "t-disable"},
    )
    assert lifecycle.status_code == 200, lifecycle.text
    selected, schedule = _steady_step(_steady_reconcile([cid]))
    assert selected == [] and schedule is None
    assert reads == []


def _full_tick_setup(steady_stack, tmp_path, monkeypatch):
    from dataclasses import replace

    from services.source_ingestion import controller_worker as cw
    from services.source_ingestion.tests import test_controller_worker as tcw

    http, module, reads = steady_stack
    cid = "conn-collector-ptt"
    assert http.post("/api/source-ingest/connectors", json=_steady_collector_config(cid)).status_code == 201
    assert http.put(f"/api/source-ingest/connectors/{cid}/schedule", json={"interval_seconds": 6, "enabled": True}).status_code == 200
    personas = ({"persona_id": "steady-persona", "required_data_sources": [{
        "dataset": "collector_social_posts", "market": "TW", "cadence": "daily",
        "source_class": "live_pull", "connector_candidates": [cid], "policy_gates": [],
    }]},)
    digest = module.runtime._desired_state_digest(list(personas))
    config = replace(
        tcw._config(tmp_path, truth_level="scheduled_tick", mode=cw.COLLECTOR_STEADY_PULL_MODE),
        state_path=module.runtime.CONTROLLER_STATE_PATH, max_ticks=0, max_concurrency=1,
    )
    metadata = {**tcw._desired_meta(), "sha256": digest}
    monkeypatch.setattr(cw, "load_desired_state", lambda **kwargs: (personas, metadata))

    def tick():
        return cw.run_controller_tick(
            config=config, state=tcw._state(), store=cw.ControllerStateStore(config.state_path),
            writer=tcw.RecordingWriter([]),
        )

    return http, reads, tick


def test_full_controller_tick_completes_real_collector_pull_and_suppresses_next_in_cadence(
    steady_stack, tmp_path, monkeypatch
) -> None:
    http, reads, tick = _full_tick_setup(steady_stack, tmp_path, monkeypatch)
    assert tick()["status"] == "ok"
    pulled = len(reads)
    assert pulled > 0
    # Second tick is inside the (reconciled, daily) cadence: still accepted, no adapter read.
    assert tick()["status"] == "ok"
    assert len(reads) == pulled


@pytest.mark.parametrize("corruption", ["tenant", "future_pit", "bundle_incomplete"])
def test_full_controller_tick_fails_closed_on_collector_readback_violation(
    steady_stack, tmp_path, monkeypatch, corruption
) -> None:
    from services.source_ingestion import controller_worker as cw

    http, reads, tick = _full_tick_setup(steady_stack, tmp_path, monkeypatch)
    original = cw.read_actual_state

    def corrupted(**kwargs):
        actual = original(**kwargs)
        for connector in actual.get("connectors") or []:
            record = connector.get("latest_source_record")
            if not isinstance(record, dict):
                continue
            if corruption == "tenant":
                record["provenance"]["tenant_id"] = "tenant-other"
            elif corruption == "future_pit":
                record["provenance"]["available_time"] = "2999-01-01T00:00:00Z"
            else:
                record["metadata"]["collector"]["bundle_status"] = "partial"
        return actual

    monkeypatch.setattr(cw, "read_actual_state", corrupted)
    with pytest.raises(cw.ControllerTickError, match="terminal source readback failed closed"):
        tick()


def test_full_tick_excludes_definition_fetch_adapter_mismatch(steady_stack, tmp_path, monkeypatch):
    from dataclasses import replace
    from services.source_ingestion import controller_worker
    from services.source_ingestion import provider_adapters as pa
    http, reads, tick = _full_tick_setup(steady_stack, tmp_path, monkeypatch)
    token = 'TaiwanOfficialMarketDatasetAdapter.records_from_payload'
    calls = []
    def fake_direct_transport(adapter, request, trace_id):
        calls.append(token)
        return ()
    monkeypatch.setitem(pa.ALLOWED_PROVIDER_ADAPTERS, token, replace(pa.ALLOWED_PROVIDER_ADAPTERS[token], handler=fake_direct_transport))
    cfg = _steady_collector_config('conn-collector-ptt')
    cfg['fetch'] = {'mode': 'provider_owned_adapter', 'adapter': token,
                    'adapter_config': {}, 'request': {}, 'max_records': 10,
                    'allow_empty': True, 'empty_reason': 'review local fake direct transport'}
    response = http.post('/api/source-ingest/connectors', json=cfg)
    assert response.status_code == 201, response.text
    try:
        result = tick()
        print('full_tick_status', result['status'])
    except controller_worker.ControllerTickError as exc:
        print('full_tick_terminal_error_after_adapter_execution', str(exc))
    print('full_tick_direct_provider_adapter_calls', calls, 'collector_reads', reads)
    assert calls == []
    assert reads == []


# --- COLLECTOR-HOSTED-ADMISSION-20261112: desired state -> reconcile -> provision -> select -> schedule -> adapter ---

_ADMISSION_HOST = "collector.admission.test"
_ADMISSION_HELD = {"collector-library-books"}


def _admission_bundle_files(source_ids, *, item_counts=None):
    """One complete bundle per admitted source with source-specific content; market rows carry trade dates."""
    import hashlib
    import json

    from services.source_ingestion.connector_definitions import get_connector_definition
    from services.source_ingestion.connectors.collector_bundle import compute_bundle_sha256

    files: dict[str, bytes] = {}
    entries = []
    for source_id in source_ids:
        collector = get_connector_definition(source_id).metadata["collector"]
        cid, stype = collector["source_id"], collector["source_type"]
        run_id, date, finished = "run1", "2026-11-10", "2026-11-10T02:05:00Z"
        base = f"bundles/{cid}/{date}/{run_id}"
        manifest_items, rows = [], []
        for n, item_id in enumerate(f"i{n:03d}" for n in range((item_counts or {}).get(source_id, 2))):
            body = f"<html>{cid} {item_id}</html>".encode()
            sha = hashlib.sha256(body).hexdigest()
            manifest_items.append({"item_id": item_id, "url": f"https://example.test/{cid}/{item_id}", "http_status": 200,
                                   "content_sha256": sha, "raw_path": f"raw/{sha}.html", "bytes": len(body)})
            row = {"id": item_id, "url": f"https://example.test/{cid}/{item_id}", "fetched_at": "2026-11-10T02:00:00Z",
                   "text": f"text {cid} {item_id}", "lang": "zh-TW", "title": f"t {item_id}", "metrics": {"likes": 1}}
            if stype == "market":
                row.update(symbol=f"23{30 + n}", trade_date=f"2024-11-{12 + n}", close=100.0 + n, dataset=collector["dataset"])
            else:
                row["published_at"] = "2026-11-10T01:00:00Z"
            if stype == "social":
                row["hashed_author"] = "hmac-abc"
            rows.append(row)
            files[f"{base}/raw/{sha}.html"] = body
        manifest = {
            "schema_version": "collector_bundle.v1", "source_id": cid, "run_id": run_id, "collector_version": "1.0.0",
            "started_at": "2026-11-10T02:00:00Z", "finished_at": finished, "fetch_policy_hash": "fph",
            "robots_decision": "allowed", "terms_ref": "https://example.test/terms", "items": manifest_items,
            "status": "complete", "bundle_sha256": compute_bundle_sha256([m["content_sha256"] for m in manifest_items]),
        }
        if collector["attribution_required"]:
            manifest["attribution"] = "attribution text"
        files[f"{base}/manifest.json"] = json.dumps(manifest).encode()
        files[f"{base}/items.jsonl"] = "\n".join(json.dumps(r) for r in rows).encode()
        entries.append({"source_id": cid, "date": date, "run_id": run_id, "finished_at": finished})
    files["bundles/index.json"] = json.dumps({"bundles": entries}).encode()
    return files, entries


@pytest.fixture()
def admission_stack(client, monkeypatch: pytest.MonkeyPatch, tmp_path):
    from dataclasses import replace

    from services.source_ingestion import controller_worker as cw
    from services.source_ingestion.connectors.collector_bundle import CollectorBundleAdapter
    from services.source_ingestion.tests import test_controller_worker as tcw
    from services.source_ingestion.tests.test_collector_bundle_connector import FakeTransport

    http, tempdir, module = client
    monkeypatch.setenv("PANTHEON_COLLECTOR_HOST", _ADMISSION_HOST)
    monkeypatch.setenv("SOURCE_INGEST_MARKET_DATA_STORAGE_ROOT", str(tempdir / "market"))
    monkeypatch.delenv("SOURCE_INGEST_DESIRED_STATE_PATH", raising=False)
    monkeypatch.delenv("SOURCE_INGEST_DESIRED_STATE_URL", raising=False)

    personas, meta = cw.load_desired_state()  # the real default desired state
    wanted = [
        req["connector_candidates"][0]
        for persona in personas
        for req in persona["required_data_sources"]
        if req["connector_candidates"][0].startswith(("collector-", "strategy-seed-"))
    ]
    # PTT publishes 60 posts in one bundle, above the 50-record community test volume.
    files, _entries = _admission_bundle_files(wanted, item_counts={"collector-tw-ptt-stock": 60})
    reads: list[str] = []

    class RecordingTransport(FakeTransport):
        def read(self, relative_path: str, *, max_bytes: int) -> bytes:
            reads.append(relative_path)
            return super().read(relative_path, max_bytes=max_bytes)

    transport = RecordingTransport(files)
    monkeypatch.setattr(CollectorBundleAdapter, "_transport", lambda self: transport)

    def local_request(url, *, method="GET", payload=None, bearer_token=None, timeout_seconds=30.0, allow_list=False):
        path = "/" + url.split("//", 1)[1].split("/", 1)[1]
        headers = {"Authorization": f"Bearer {module.runtime.controller_token}"} if bearer_token else None
        response = http.request(method, path, json=payload, headers=headers)
        assert response.status_code < 400, (path, response.text)
        return response.json()

    monkeypatch.setattr(cw, "_request_json", local_request)
    monkeypatch.setattr(
        cw, "load_controller_token", lambda **kwargs: "controller-test-token-that-is-at-least-32-characters"
    )

    def tick(mode):
        config = replace(
            tcw._config(tmp_path, truth_level="scheduled_tick", mode=mode),
            state_path=module.runtime.CONTROLLER_STATE_PATH, max_ticks=0, max_concurrency=1,
        )
        return cw.run_controller_tick(
            config=config, state=tcw._state(), store=cw.ControllerStateStore(config.state_path),
            writer=tcw.RecordingWriter([]),
        )

    return http, module, reads, wanted, files, transport, tick


def test_desired_state_declares_one_live_pull_per_admitted_collector_source_and_holds_the_rest() -> None:
    from services.source_ingestion import controller_worker as cw
    from services.source_ingestion.persona_source_reconciler import deployed_collector_definitions

    personas, _ = cw.load_desired_state()
    holder = next(p for p in personas if p["persona_id"] == cw.DEPLOYMENT_REQUIREMENT_HOLDER_ID)
    requirements = holder["required_data_sources"]
    collector_reqs = [r for r in requirements if r["connector_candidates"][0] != "tw-twse-tpex-official-market"]
    assert all(r["source_class"] == "live_pull" and len(r["connector_candidates"]) == 1 for r in collector_reqs)
    admitted = {r["connector_candidates"][0] for r in collector_reqs}
    deployed = {d.definition_id for d in deployed_collector_definitions()}
    assert admitted == deployed - _ADMISSION_HELD
    # The existing official requirement is unchanged.
    assert [r for r in requirements if r["connector_candidates"] == ["tw-twse-tpex-official-market"]] == [
        {
            "cadence": "daily", "connector_candidates": ["tw-twse-tpex-official-market"], "dataset": "tw_price_daily",
            "market": "TW", "source_class": "live_pull",
            "policy_gates": ["require_connector_approved", "require_schedule_active", "require_source_health_ok", "no_live_capital"],
        }
    ]


def test_full_collector_steady_tick_from_default_desired_state_pulls_every_admitted_source(admission_stack) -> None:
    from services.source_ingestion import controller_worker as cw
    from services.source_ingestion.connector_definitions import get_connector_definition

    http, module, reads, wanted, files, transport, tick = admission_stack

    # reconcile_only: provisions and verifies, but nothing is pulled.
    only = tick(cw.RECONCILE_ONLY_MODE)
    assert only["provider_egress_attempted"] is False
    assert reads == []
    provisioned = {c["connector_id"] for c in http.get("/api/source-ingest/controller/readback").json()["connectors"]}
    assert set(wanted) <= provisioned
    assert _ADMISSION_HELD.isdisjoint(provisioned)

    # collector_steady_pull: every admitted source is selected and pulled through the real scheduler.
    # The scheduler claims max_concurrency (1 here) connectors per tick; the controller spreads the
    # admitted sources over ticks instead of over-selecting, least recently successful first.
    ticks = 0
    while True:
        result = tick(cw.COLLECTOR_STEADY_PULL_MODE)
        assert result["status"] == "ok"
        ticks += 1
        if not result["provider_egress_attempted"]:
            break
        assert result["schedule_summary"]["total_ran"] == 1
        assert ticks <= len(wanted), "every admitted source is pulled once, then nothing is due"
    assert ticks == len(wanted) + 1
    assert reads, "the Collector adapter must read bundles"
    readback = http.get("/api/source-ingest/controller/readback").json()
    by_id = {c["connector_id"]: c for c in readback["connectors"]}
    for source_id in wanted:
        item = by_id[source_id]
        record = item["latest_source_record"]
        collector = get_connector_definition(source_id).metadata["collector"]
        assert record["source_id"].startswith(f"collector:{collector['source_id']}:"), source_id
        assert item["connector"]["metadata"]["definition_id"] == source_id
        assert item["schedule"]["interval_seconds"] == collector["cadence_seconds"]
        fetch = module.runtime.connector_store.get_config(source_id).fetch
        assert fetch["mode"] == "provider_owned_adapter" and fetch["adapter"] == "CollectorBundleAdapter.records_from_bundle"
        assert fetch["adapter_config"]["host"] == _ADMISSION_HOST
        assert fetch["adapter_config"]["source_id"] == collector["source_id"]
    # Five social platforms share one dataset but are five connectors.
    social = [s for s in wanted if get_connector_definition(s).metadata["collector"]["dataset"] == "collector_social_posts"]
    assert len(social) == 5 and len({by_id[s]["connector_id"] for s in social}) == 5
    # Community sources run at test volume.
    for source_id in social:
        assert module.runtime.connector_store.get_config(source_id).fetch["max_records"] == 50

    # Resume: the first pull stopped at the 50-record test volume and persisted its cursor; a fresh
    # adapter built from the stored fetch config continues with the remaining 10, not the head again.
    from services.source_ingestion.provider_adapters import execute_provider_owned_adapter

    ptt = by_id["collector-tw-ptt-stock"]
    assert ptt["latest_source_record"]["source_id"].startswith("collector:collector-tw-ptt-stock:")
    stored = module.runtime.connector_store.get_config("collector-tw-ptt-stock")
    resumed = execute_provider_owned_adapter(connector=stored.connector, fetch=stored.fetch, trace_id="resume")
    assert len(resumed) == 10
    assert {r.metadata["collector"]["run_id"] for r in resumed} == {"run1"}


def test_without_collector_host_nothing_is_provisioned_and_reason_is_in_readback(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    from services.source_ingestion import controller_worker as cw

    http, tempdir, module = client
    monkeypatch.delenv("PANTHEON_COLLECTOR_HOST", raising=False)
    personas, meta = cw.load_desired_state()
    response = http.post(
        "/api/source-ingest/persona-source-provisioning/reconcile",
        json={
            "personas": list(personas), "authoritative_snapshot": True, "dry_run": False,
            "desired_state_sha256": meta["sha256"], "source_authority": meta["authority"],
        },
        headers={"Authorization": f"Bearer {module.runtime.controller_token}"},
    )
    assert response.status_code < 400, response.text
    actions = [a for r in response.json()["results"] for a in r["actions"]]
    collector_actions = [a for a in actions if str(a["connector_id"] or "").startswith(("collector-", "strategy-seed-"))]
    assert collector_actions and all(
        a["status"] == "skipped" and a["connector_action"] == "collector_host_not_configured" for a in collector_actions
    )
    assert response.json()["summary"]["unsupported"] == 0  # does not fail the controller tick closed
    assert all("PANTHEON_COLLECTOR_HOST" in a["details"]["reason"] for a in collector_actions)
    ids = {c["connector_id"] for c in http.get("/api/source-ingest/controller/readback").json()["connectors"]}
    assert not any(i.startswith("collector-") or i.startswith("strategy-seed-") for i in ids)




def test_one_failing_collector_does_not_starve_other_admitted_sources(admission_stack, monkeypatch):
    from services.source_ingestion import controller_worker as cw
    from services.source_ingestion.connectors import SourceEvidenceError

    http, module, reads, wanted, files, transport, tick = admission_stack
    tick(cw.RECONCILE_ONLY_MODE)
    original_read = transport.read

    def fail_first_source(relative_path, *, max_bytes):
        if '/collector-gdelt/' in relative_path:
            raise SourceEvidenceError('review injected unavailable GDELT bundle')
        return original_read(relative_path, max_bytes=max_bytes)

    monkeypatch.setattr(transport, 'read', fail_first_source)
    batches = []
    errors = []
    original_limit = cw.limit_collector_pull_batch

    def record_batch(actual, selected, *, limit):
        batch = original_limit(actual, selected, limit=limit)
        batches.append(batch)
        return batch

    monkeypatch.setattr(cw, 'limit_collector_pull_batch', record_batch)
    for _ in range(4):
        try:
            tick(cw.COLLECTOR_STEADY_PULL_MODE)
        except cw.ControllerTickError as exc:
            errors.append(str(exc))
    readback = http.get('/api/source-ingest/controller/readback').json()
    succeeded = [c['connector_id'] for c in readback['connectors']
                 if c['connector_id'] in wanted and c.get('latest_source_record')]
    assert succeeded, f'No other admitted source ran after four ticks; batches={batches}; errors={errors}'


# --- COLLECTOR-CLOSE-CONTINUATION-20261113: configured handler -> controller selection -> scheduler -> snapshot store ---

_CLOSE_SOURCE = "collector-tw-twse-price-history"
_CLOSE_DATE = "2026-11-13"
_CLOSE_ROWS = 600
_CLOSE_HELD = {531: "2317", 538: "2330"}  # captured positions beyond the first ~55-row verified prefix
_CLOSE_READ_SECONDS = 0.27  # finite per-read latency: the 15s whole-request budget verifies ~55 rows


def _close_files(*, tamper_code: str | None = None, wrong_date_code: str | None = None):
    import hashlib
    import json

    from services.source_ingestion.connectors.collector_bundle import compute_bundle_sha256

    run_id, finished = f"close-{_CLOSE_DATE}", "2026-11-13T07:30:00Z"
    base = f"bundles/{_CLOSE_SOURCE}/{_CLOSE_DATE}/{run_id}"
    items, rows, files = [], [], {}
    for n in range(_CLOSE_ROWS):
        code = _CLOSE_HELD.get(n, f"{1000 + n}")
        item_id = f"twse-price-history-{_CLOSE_DATE}-{code}"
        body = f"<r>{_CLOSE_DATE}-{code}</r>".encode()
        sha = hashlib.sha256(body).hexdigest()
        items.append({"item_id": item_id, "url": "https://www.twse.com.tw/exchangeReport/MI_INDEX", "http_status": 200,
                      "content_sha256": sha, "raw_path": f"raw/{sha}.html", "bytes": len(body)})
        rows.append({
            "id": item_id, "url": "https://www.twse.com.tw/exchangeReport/MI_INDEX", "fetched_at": "2026-11-13T07:00:00Z",
            "symbol": code, "security_name": f"name {code}", "exchange": "TWSE", "dataset": "tw_price_daily",
            "trade_date": "2026-11-12" if code == wrong_date_code else _CLOSE_DATE,
            "open": 940.0, "high": 955.0, "low": 938.0, "close": 950.0 + n, "volume_shares": 1000, "transactions": 10,
        })
        files[f"{base}/raw/{sha}.html"] = b"tampered" if code == tamper_code else body
    manifest = {
        "schema_version": "collector_bundle.v1", "source_id": _CLOSE_SOURCE, "run_id": run_id, "collector_version": "1.0.0",
        "started_at": "2026-11-13T07:00:00Z", "finished_at": finished, "fetch_policy_hash": "fph",
        "robots_decision": "allowed", "terms_ref": "https://example.test/terms", "attribution": "TWSE", "items": items,
        "status": "complete", "bundle_sha256": compute_bundle_sha256([m["content_sha256"] for m in items]),
    }
    files[f"{base}/manifest.json"] = json.dumps(manifest).encode()
    files[f"{base}/items.jsonl"] = "\n".join(json.dumps(r) for r in rows).encode()
    files["bundles/index.json"] = json.dumps({"bundles": [
        {"source_id": _CLOSE_SOURCE, "date": _CLOSE_DATE, "run_id": run_id, "finished_at": finished}]}).encode()
    files["status.json"] = json.dumps({"sources": {_CLOSE_SOURCE: {"status": "ok", "last_run_at": finished}}}).encode()
    return files


def _close_config(connector_id: str) -> dict:
    from services.source_ingestion.connectors.collector_bundle import CollectorBundleAdapter

    adapter = CollectorBundleAdapter(
        connector_id=connector_id, source_id=_CLOSE_SOURCE, source_type="market", host=_STEADY_HOST,
        attribution_required=True, dataset="tw_price_daily", max_records=1000, cadence_seconds=600,
    )
    full = adapter.connector().to_dict()
    allowed = ("connector_id", "source_type", "provider", "license_scope", "auth_type", "supported_modes", "auth_policy",
               "rate_limit_policy", "license_policy", "source_metadata", "metadata")
    connector = {key: full[key] for key in allowed if key in full}
    connector["metadata"] = {**connector["metadata"], "tenant_id": "tenant-a", "definition_id": _CLOSE_SOURCE}
    fetch = dict(adapter.fetch_config())
    fetch["adapter_config"] = {
        **fetch["adapter_config"], "source_type": "market", "host": _STEADY_HOST, "dataset": "tw_price_daily",
        "attribution_required": True, "cadence_seconds": 600,
    }
    return {"connector": connector, "fetch": fetch}


def _close_stack(steady_stack, monkeypatch, **file_kwargs):
    import types

    from services.source_ingestion.connectors import collector_bundle
    from services.source_ingestion.connectors.collector_bundle import CollectorBundleAdapter
    from services.source_ingestion.tests.test_collector_bundle_connector import FakeTransport

    http, module, _reads = steady_stack
    monkeypatch.setenv("SOURCE_INGEST_ACTIVE_PAPER_SYMBOLS", "2330.TWSE,2317.TWSE")
    clock = [0.0]
    reads: list[str] = []

    class LatencyTransport(FakeTransport):
        def read(self, relative_path: str, *, max_bytes: int) -> bytes:
            clock[0] += _CLOSE_READ_SECONDS  # every Collector read costs finite time against the request budget
            reads.append(relative_path)
            return super().read(relative_path, max_bytes=max_bytes)

    transport = LatencyTransport(_close_files(**file_kwargs))
    monkeypatch.setattr(CollectorBundleAdapter, "_transport", lambda self: transport)
    monkeypatch.setattr(collector_bundle, "time", types.SimpleNamespace(monotonic=lambda: clock[0]))
    cid = "conn-close-twse"
    assert http.post("/api/source-ingest/connectors", json=_close_config(cid)).status_code == 201
    assert http.put(f"/api/source-ingest/connectors/{cid}/schedule", json={"interval_seconds": 600, "enabled": True}).status_code == 200
    return http, module, cid, reads, _steady_reconcile([cid])


def _snapshot_date(module, symbol: str):
    module.runtime.latest_market_snapshot_store.reload()
    snap = module.runtime.latest_market_snapshot_store.get(symbol)
    return snap.points[-1].event_time[:10] if snap and snap.points else None


def test_held_symbols_reach_snapshot_store_on_first_tick_and_session_completes_across_ticks(steady_stack, monkeypatch) -> None:
    http, module, cid, reads, reconcile = _close_stack(steady_stack, monkeypatch)
    ticks, snapshot_after_first, ack_ids = 0, None, []
    while True:
        selected, schedule = _steady_step(reconcile)
        if not selected:
            break
        ticks += 1
        assert selected == [cid] and schedule["summary"]["total_failed"] == 0
        run = schedule["ran"][0]
        assert run["run"]["status"] == "completed"
        # Bounded by the finite-latency whole-request budget: never the whole 600-row session in one pull.
        if ticks == 1:
            assert 0 < run["receipt"]["normalized_count"] < 100
            snapshot_after_first = {s: _snapshot_date(module, s) for s in ("2330.TW", "2317.TW", "1000.TW")}
        ack_ids.append(run["run"]["ingest_run_id"])
        assert ticks <= 30, "newest session must finish inside the 30-minute window at one tick per minute"
    # Held symbols beyond the first prefix were published on the very first tick (old code: ~10 pulls later).
    assert snapshot_after_first["2330.TW"] == _CLOSE_DATE and snapshot_after_first["2317.TW"] == _CLOSE_DATE
    assert ticks > 1, "continuation happened across ticks, not within one request"
    assert len(set(ack_ids)) == len(ack_ids), "no duplicate acknowledgement for one run"
    # Whole session delivered; cadence is restored afterwards (nothing selected, no new reads).
    assert _snapshot_date(module, "1599.TW") == _CLOSE_DATE
    seen = len(reads)
    assert _steady_step(reconcile) == ([], None) and len(reads) == seen
    readback = http.get("/api/source-ingest/controller/readback").json()
    item = next(c for c in readback["connectors"] if c["connector_id"] == cid)
    assert "continuation_pending" not in item["fetch_state"]["collector_outcome"]["observed"]


def test_interrupted_close_pull_acknowledges_nothing_and_retry_resumes(steady_stack, monkeypatch) -> None:
    from services.source_ingestion.connectors import SourceEvidenceError

    http, module, cid, reads, reconcile = _close_stack(steady_stack, monkeypatch)
    from services.source_ingestion.connectors.collector_bundle import CollectorBundleAdapter

    inner = CollectorBundleAdapter._transport
    state = {"fail": True}

    def flaky(self):
        transport = inner(self)

        class Flaky(type(transport)):
            def read(self_inner, relative_path, *, max_bytes):
                if state["fail"] and "/raw/" in relative_path:
                    raise SourceEvidenceError("injected collector outage")
                return transport.read(relative_path, max_bytes=max_bytes)

        return Flaky(transport.files)

    monkeypatch.setattr(CollectorBundleAdapter, "_transport", flaky)
    monkeypatch.setattr(module.runtime, "FRONTIER_BACKOFF_SECONDS", 0)  # retry immediately instead of sleeping
    selected, schedule = _steady_step(reconcile)
    assert _snapshot_date(module, "2330.TW") is None  # nothing published, nothing acknowledged
    assert not (schedule and schedule["summary"]["total_failed"] == 0)
    state["fail"] = False
    # The retry replays from the unacknowledged start with held symbols first and publishes them.
    from services.source_ingestion import controller_worker as cw

    retry = cw.run_schedule_tick(
        api_url="http://source-ingest.test:8097", max_concurrency=1, force_connector_ids=[cid], exclusive_connector_ids=[cid],
        controller_token="controller-test-token-that-is-at-least-32-characters",
    )
    assert retry["summary"]["total_failed"] == 0 and [r["connector_id"] for r in retry["ran"]] == [cid], retry
    assert _snapshot_date(module, "2330.TW") == _CLOSE_DATE
    assert _snapshot_date(module, "2317.TW") == _CLOSE_DATE


@pytest.mark.parametrize("fault", ["hash", "date"])
def test_held_close_with_invalid_raw_or_wrong_date_never_publishes_a_snapshot(steady_stack, monkeypatch, fault) -> None:
    kwargs = {"tamper_code": "2330"} if fault == "hash" else {"wrong_date_code": "2330"}
    http, module, cid, reads, reconcile = _close_stack(steady_stack, monkeypatch, **kwargs)
    try:
        _steady_step(reconcile)
    except Exception:  # truthful failure is acceptable; a published snapshot is not
        pass
    assert _snapshot_date(module, "2330.TW") != _CLOSE_DATE
