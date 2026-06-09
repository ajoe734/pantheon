"""Tests for autonomous scheduled connector execution path."""

from __future__ import annotations

import importlib
import os
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    tempdir = tempfile.mkdtemp(prefix="source_ingest_scheduled_")
    env_backup = {
        "SOURCE_INGEST_DATA_DIR": os.environ.get("SOURCE_INGEST_DATA_DIR"),
        "SOURCE_INGEST_MAX_RECORDS": os.environ.get("SOURCE_INGEST_MAX_RECORDS"),
        "SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY": os.environ.get("SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY"),
        "SOURCE_INGEST_FRONTIER_MAX_ATTEMPTS": os.environ.get("SOURCE_INGEST_FRONTIER_MAX_ATTEMPTS"),
        "SOURCE_INGEST_FRONTIER_BACKOFF_SECONDS": os.environ.get("SOURCE_INGEST_FRONTIER_BACKOFF_SECONDS"),
    }
    os.environ["SOURCE_INGEST_DATA_DIR"] = tempdir
    os.environ["SOURCE_INGEST_MAX_RECORDS"] = "10"
    os.environ["SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY"] = "1"
    os.environ["SOURCE_INGEST_FRONTIER_MAX_ATTEMPTS"] = "2"
    os.environ["SOURCE_INGEST_FRONTIER_BACKOFF_SECONDS"] = "300"

    sys.modules.pop("services.source_ingestion.main", None)
    module = importlib.import_module("services.source_ingestion.main")
    module = importlib.reload(module)

    try:
        yield TestClient(module.app), Path(tempdir), module
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
    return payload


def _configure_with_records(test_client, connector_id="conn-sched-notes"):
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

    source = test_client.get("/api/source-ingest/source-records/src-conn-sched-notes-note-1")
    assert source.status_code == 200


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

    replay = test_client.post(
        "/api/source-ingest/dlq/replay",
        json={"tag": "retry_exhausted", "reason": "test scheduled frontier replay"},
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["summary"]["applied"] == 1

    done_frontier = test_client.get("/api/source-ingest/frontier?status=done")
    assert done_frontier.status_code == 200
    assert len(done_frontier.json()["frontier"]) == 1
    assert done_frontier.json()["frontier"][0]["trigger_type"] == "dlq_replay"
    source = test_client.get("/api/source-ingest/source-records/src-conn-sched-replay-note-1")
    assert source.status_code == 200

    reloaded = importlib.reload(module)
    replay_client = TestClient(reloaded.app)
    durable_frontier = replay_client.get("/api/source-ingest/frontier?status=done")
    assert durable_frontier.status_code == 200
    assert len(durable_frontier.json()["frontier"]) == 1


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
    assert freshness["schema_version"] == "source_connector_freshness.v1"
    assert freshness["status"] == "fresh"
    assert freshness["is_due"] is False
    assert freshness["last_ingest_run_id"]
    assert freshness["latest_run"]["status"] == "completed"
    assert freshness["seconds_until_due"] > 0
    health = entry["health_metrics"]
    assert health["schema_version"] == "source_connector_health_metrics.v1"
    assert health["last_success_at"] == freshness["last_success_at"]
    assert health["row_count"] == 1
    assert health["expected_rows"] == 1
    assert health["schema_hash"]
    assert health["source_error"] is None


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
    assert broker_update["metadata"]["fallback_connector_id"] == "tw-yahoo-broker-top15"
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
