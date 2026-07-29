from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import threading
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient


SERVICE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVICE_DIR.parents[1]
FIXTURE_PATH = SERVICE_DIR / "fixtures" / "devloop-drift-telemetry-event.json"


def _load_service(data_dir: str, name: str):
    sys.modules.pop("consumer", None)
    sys.modules.pop("store", None)
    sys.modules.pop(name, None)
    for path in (str(SERVICE_DIR), str(REPO_ROOT)):
        if path not in sys.path:
            sys.path.insert(0, path)
    spec = importlib.util.spec_from_file_location(name, SERVICE_DIR / "main.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        with mock.patch.dict(
            "os.environ",
            {
                "RECONCILIATION_DRIFT_DATA_DIR": data_dir,
                "RECONCILIATION_DRIFT_STORE_BACKEND": "json",
                "RECONCILIATION_DRIFT_AUTH_MODE": "disabled",
                "PERSISTENCE_POSTURE": "lenient",
            },
        ):
            spec.loader.exec_module(module)
        return module
    finally:
        sys.modules.pop("consumer", None)
        sys.modules.pop("store", None)


def _load_worker(name: str, filename: str):
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, SERVICE_DIR / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _summary(binding_id: str = "binding-l12-rec-001") -> dict[str, object]:
    return {
        "tenant_id": "tenant-a",
        "binding_id": binding_id,
        "runtime_id": "runtime-l12-rec-001",
        "last_event_id": "event-l12-rec-001",
        "observed_metrics": {"pnl": 1.0},
        "baseline_metrics": {"pnl": 1.0},
        "state": "active",
        "health_summary": {"runtime": "ok"},
    }


def _auth_headers(tenant_id: str, token: str = "l12-secret") -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-Tenant-Id": tenant_id,
    }


def test_scheduler_window_is_deterministic_and_timeout_retry_preserves_identity() -> None:
    scheduler = _load_worker("l12_rec_scheduler", "scheduler_worker.py")
    now = datetime(2026, 7, 26, 10, 7, 31, tzinfo=timezone.utc)
    expected_window = "scheduled:tenant-a:2026-07-26T10:05:00Z:PT300S"
    assert (
        scheduler._window_id(
            tenant_id="tenant-a",
            window_seconds=300,
            now=now,
        )
        == expected_window
    )

    requests: list[dict[str, object]] = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(
                {
                    "status": "ok",
                    "window_id": expected_window,
                    "within_sla": True,
                }
            ).encode()

    def urlopen(request, *, timeout):
        assert timeout == 12.0
        assert request.headers["X-tenant-id"] == "tenant-a"
        assert request.headers["Authorization"] == "Bearer l12-secret"
        requests.append(json.loads(request.data))
        if len(requests) == 1:
            raise urllib.error.URLError("timed out after ambiguous commit")
        return Response()

    with (
        mock.patch.dict(
            "os.environ",
            {"RECONCILIATION_DRIFT_AUTH_TOKEN": "l12-secret"},
        ),
        mock.patch.object(scheduler.urllib.request, "urlopen", side_effect=urlopen),
    ):
        result = scheduler.run_tick(
            api_url="http://reconciliation",
            window_id=expected_window,
            tenant_id="tenant-a",
            worker_id="scheduler-a",
            window_seconds=300,
            sla_seconds=10,
            timeout_seconds=12,
            max_attempts=2,
        )

    assert result["controller_status"] == "healthy"
    assert result["attempt_count"] == 2
    assert requests[0] == requests[1]
    assert requests[0]["window_id"] == expected_window
    assert requests[0]["tenant_id"] == "tenant-a"
    assert requests[0]["worker_id"] == "scheduler-a"
    assert requests[0]["sla_seconds"] == 10


def test_two_scheduler_requests_cannot_execute_one_window_concurrently() -> None:
    with tempfile.TemporaryDirectory() as data_dir:
        service = _load_service(data_dir, "l12_rec_two_schedulers")
        client = TestClient(service.app)
        fetch_started = threading.Event()
        release_fetch = threading.Event()
        fetch_count = 0

        def fetch(_url):
            nonlocal fetch_count
            fetch_count += 1
            fetch_started.set()
            assert release_fetch.wait(timeout=5)
            return [_summary()]

        payload = {
            "tenant_id": "tenant-a",
            "tick_id": "window-l12-rec-001",
            "window_id": "window-l12-rec-001",
            "sla_seconds": 5,
        }
        with (
            mock.patch.dict("os.environ", {"PANTHEON_TENANT_ID": "tenant-a"}),
            mock.patch.object(
                service,
                "_fetch_telemetry_runtime_summaries",
                side_effect=fetch,
            ),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            first = pool.submit(
                client.post,
                "/api/reconciliation-drift/scheduled-reconcile",
                json={**payload, "worker_id": "scheduler-a"},
            )
            assert fetch_started.wait(timeout=5)
            second = pool.submit(
                client.post,
                "/api/reconciliation-drift/scheduled-reconcile",
                json={**payload, "worker_id": "scheduler-b"},
            )
            second_payload = second.result(timeout=5).json()
            release_fetch.set()
            first_payload = first.result(timeout=5).json()

        assert fetch_count == 1
        assert first_payload["duplicate_window"] is False
        assert first_payload["evaluated_binding_count"] == 1
        assert second_payload["status"] == "deferred"
        assert second_payload["lease_status"] == "lease_active"
        assert len(service.store.list_evaluations()) == 1


def test_two_consumer_requests_cannot_duplicate_report_or_incident() -> None:
    with tempfile.TemporaryDirectory() as data_dir:
        service = _load_service(data_dir, "l12_rec_two_consumers")
        client = TestClient(service.app)
        fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        fixture["tenant_id"] = "tenant-a"
        build_started = threading.Event()
        release_build = threading.Event()
        original_builder = service.build_drift_report_from_event

        def build(*args, **kwargs):
            build_started.set()
            assert release_build.wait(timeout=5)
            return original_builder(*args, **kwargs)

        payload = {"tenant_id": "tenant-a", "events": [fixture]}
        with (
            mock.patch.dict("os.environ", {"PANTHEON_TENANT_ID": "tenant-a"}),
            mock.patch.object(
                service,
                "build_drift_report_from_event",
                side_effect=build,
            ) as builder,
            mock.patch.object(
                service,
                "_classify_drift_report_incident",
                return_value={"incident_id": "incident-l12-rec-001"},
            ) as classify,
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            first = pool.submit(
                client.post,
                "/api/reconciliation-drift/telemetry-events/consume",
                json={**payload, "worker_id": "consumer-a"},
            )
            assert build_started.wait(timeout=5)
            second = pool.submit(
                client.post,
                "/api/reconciliation-drift/telemetry-events/consume",
                json={**payload, "worker_id": "consumer-b"},
            )
            second_payload = second.result(timeout=5).json()
            release_build.set()
            first_payload = first.result(timeout=5).json()

        assert first_payload["drift_report_count"] == 1
        assert first_payload["incident_case_count"] == 1
        assert second_payload["status"] == "deferred"
        assert second_payload["deferred_event_ids"] == [fixture["event_id"]]
        assert builder.call_count == 1
        assert classify.call_count == 1
        assert len(service.store.list_drift_reports()) == 1


def test_corrupt_consumer_state_fails_closed_and_active_lease_defers_peer(
    tmp_path: Path,
) -> None:
    consumer = _load_worker("l12_rec_consumer_state", "consumer.py")
    corrupt_path = tmp_path / "corrupt-consumer-state.json"
    corrupt_path.write_text("{not-json", encoding="utf-8")
    corrupt_state = consumer.ConsumerWorkerState(corrupt_path)
    with mock.patch.object(consumer, "fetch_runtime_summaries") as fetch:
        corrupt_result = consumer.run_runtime_summary_consumer_once(
            service_url="http://reconciliation",
            telemetry_url="http://telemetry",
            state=corrupt_state,
            worker_id="consumer-corrupt",
        )
    assert corrupt_result["status"] == "failure"
    assert corrupt_result["lease_status"] == "state_error"
    assert corrupt_path.read_text(encoding="utf-8") == "{not-json"
    fetch.assert_not_called()

    state_path = tmp_path / "leased-consumer-state.json"
    owner = consumer.ConsumerWorkerState(state_path)
    assert owner.acquire_lease(
        worker_id="consumer-owner",
        lease_seconds=60,
        now=datetime(2026, 7, 26, 10, 0, tzinfo=timezone.utc),
    )
    peer = consumer.ConsumerWorkerState(state_path)
    with mock.patch.object(consumer, "fetch_runtime_summaries") as peer_fetch:
        deferred = consumer.run_runtime_summary_consumer_once(
            service_url="http://reconciliation",
            telemetry_url="http://telemetry",
            state=peer,
            worker_id="consumer-peer",
            now_fn=lambda: datetime(2026, 7, 26, 10, 0, 1, tzinfo=timezone.utc),
        )
    assert deferred["status"] == "deferred"
    assert deferred["lease_owner"] == "consumer-owner"
    peer_fetch.assert_not_called()
    owner.release_lease(worker_id="consumer-owner")


def test_consumer_checkpoint_and_release_reject_stale_lease_token(
    tmp_path: Path,
) -> None:
    consumer = _load_worker("l12_rec_consumer_lease_cas", "consumer.py")
    state_path = tmp_path / "consumer-lease-cas.json"
    acquired_at = datetime.now(timezone.utc)

    stale = consumer.ConsumerWorkerState(state_path)
    assert stale.acquire_lease(
        worker_id="shared-worker-id",
        lease_seconds=1,
        now=acquired_at,
    )
    stale.pending["stale-checkpoint"] = {
        "event": {"event_id": "stale-checkpoint", "tenant_id": "tenant-a"},
        "attempt_count": 1,
        "first_seen_at": acquired_at.isoformat(),
        "last_attempt_at": acquired_at.isoformat(),
        "last_error": None,
    }

    successor = consumer.ConsumerWorkerState(state_path)
    assert successor.acquire_lease(
        worker_id="shared-worker-id",
        lease_seconds=60,
        now=acquired_at + timedelta(seconds=2),
    )
    successor_token = successor.lease_token
    successor.pending["successor-checkpoint"] = {
        "event": {"event_id": "successor-checkpoint", "tenant_id": "tenant-a"},
        "attempt_count": 0,
        "first_seen_at": acquired_at.isoformat(),
        "last_attempt_at": None,
        "last_error": None,
    }
    successor.save()

    with pytest.raises(consumer.ConsumerStateError, match="lease token"):
        stale.save()
    with pytest.raises(consumer.ConsumerStateError, match="lease token"):
        stale.release_lease(worker_id="shared-worker-id")

    persisted = consumer.ConsumerWorkerState(state_path)
    assert persisted.lease_token == successor_token
    assert set(persisted.pending) == {"successor-checkpoint"}


def test_consumer_delivery_identity_is_tenant_plus_event(
    tmp_path: Path,
) -> None:
    consumer = _load_worker("l12_rec_consumer_tenant_event", "consumer.py")
    state = consumer.ConsumerWorkerState(tmp_path / "tenant-event-state.json")
    summaries = [
        {"tenant_id": "tenant-a", "event_id": "shared-event-id"},
        {"tenant_id": "tenant-b", "event_id": "shared-event-id"},
    ]
    delivered_tenants: list[str] = []

    def to_event(summary):
        return {
            "tenant_id": summary["tenant_id"],
            "event_id": summary["event_id"],
            "created_at": "2026-07-26T10:00:00Z",
        }

    def deliver(_service_url, events):
        delivered_tenants.append(events[0]["tenant_id"])
        return {"drift_report_count": 1, "incident_case_count": 1}

    with (
        mock.patch.object(
            consumer,
            "fetch_runtime_summaries",
            return_value=summaries,
        ),
        mock.patch.object(
            consumer,
            "runtime_summary_to_event",
            side_effect=to_event,
        ),
        mock.patch.object(consumer, "post_events", side_effect=deliver),
    ):
        result = consumer.run_runtime_summary_consumer_once(
            service_url="http://reconciliation",
            telemetry_url="http://telemetry",
            state=state,
            worker_id="tenant-event-worker",
            now_fn=lambda: datetime(
                2026, 7, 26, 10, 0, tzinfo=timezone.utc
            ),
        )

    assert result["enqueued_event_count"] == 2
    assert result["delivered_event_count"] == 2
    assert delivered_tenants == ["tenant-a", "tenant-b"]
    assert len(state.completed) == 2


def test_json_legacy_raw_key_fallback_requires_exact_tenant(
    tmp_path: Path,
) -> None:
    store_module = _load_worker("l12_rec_json_tenant_fallback", "store.py")
    store = store_module.ReconciliationDriftStore(tmp_path)
    cases = [
        (
            store.evaluations_path,
            store.get_evaluation,
            "evaluation_id",
            "legacy-evaluation-id",
        ),
        (
            store.alerts_path,
            store.get_alert_handoff,
            "alert_id",
            "legacy-alert-id",
        ),
        (
            store.reconciliation_records_path,
            store.get_reconciliation_record,
            "record_id",
            "legacy-record-id",
        ),
        (
            store.drift_reports_path,
            store.get_drift_report,
            "drift_report_id",
            "legacy-report-id",
        ),
        (
            store.worker_states_path,
            store.get_worker_state,
            "state_id",
            "legacy-state-id",
        ),
    ]

    for path, getter, id_field, record_id in cases:
        record = {
            id_field: record_id,
            "id": record_id,
            "tenant_id": "tenant-a",
        }
        path.write_text(
            json.dumps({record_id: record}),
            encoding="utf-8",
        )
        assert getter(record_id, tenant_id="tenant-a") == record
        assert getter(record_id, tenant_id="tenant-b") is None


def test_postgres_legacy_raw_key_fallback_requires_exact_tenant(
    tmp_path: Path,
) -> None:
    store_module = _load_worker("l12_rec_postgres_tenant_fallback", "store.py")

    class FakeOwnerStore:
        tables: dict[str, dict[str, dict]] = {}

        def __init__(self, *, table, **_kwargs):
            self.records = self.tables.setdefault(table, {})

        def list_all(self):
            return list(self.records.values())

        def get(self, record_id):
            record = self.records.get(record_id)
            return dict(record) if record is not None else None

        def put(self, record_id, payload):
            self.records[record_id] = dict(payload)

        def compare_and_set(self, record_id, expected, payload):
            current = self.records.get(record_id)
            if current != expected:
                return False, dict(current) if current is not None else None
            self.records[record_id] = dict(payload)
            return True, dict(payload)

    FakeOwnerStore.tables = {}
    with mock.patch.object(
        store_module,
        "PostgresJsonOwnerStore",
        FakeOwnerStore,
    ):
        store = store_module.PostgresReconciliationDriftStore(
            tmp_path,
            dsn="postgresql://unused",
        )

    cases = [
        (
            store._evaluation_records,
            store.get_evaluation,
            "evaluation_id",
            "legacy-evaluation-id",
        ),
        (
            store._alert_records,
            store.get_alert_handoff,
            "alert_id",
            "legacy-alert-id",
        ),
        (
            store._reconciliation_records,
            store.get_reconciliation_record,
            "record_id",
            "legacy-record-id",
        ),
        (
            store._drift_reports,
            store.get_drift_report,
            "drift_report_id",
            "legacy-report-id",
        ),
        (
            store._worker_state_records,
            store.get_worker_state,
            "state_id",
            "legacy-state-id",
        ),
    ]

    for owner_store, getter, id_field, record_id in cases:
        record = {
            id_field: record_id,
            "id": record_id,
            "tenant_id": "tenant-a",
        }
        owner_store.put(record_id, record)
        assert getter(record_id, tenant_id="tenant-a") == record
        assert getter(record_id, tenant_id="tenant-b") is None


def test_scheduled_response_measures_configured_sla_and_persists_worker_state() -> None:
    with tempfile.TemporaryDirectory() as data_dir:
        service = _load_service(data_dir, "l12_rec_sla")
        client = TestClient(service.app)
        with (
            mock.patch.dict("os.environ", {"PANTHEON_TENANT_ID": "tenant-a"}),
            mock.patch.object(
                service,
                "_fetch_telemetry_runtime_summaries",
                return_value=[_summary()],
            ),
            mock.patch.object(service, "_monotonic", side_effect=[10.0, 10.25]),
        ):
            response = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={
                    "tenant_id": "tenant-a",
                    "tick_id": "window-sla-001",
                    "window_id": "window-sla-001",
                    "worker_id": "scheduler-sla",
                    "sla_seconds": 1.0,
                },
            )

        payload = response.json()
        assert payload["duration_seconds"] == 0.25
        assert payload["sla_seconds"] == 1.0
        assert payload["within_sla"] is True
        assert payload["sla_status"] == "met"
        worker_states = service.store.list_worker_states()
        assert len(worker_states) == 1
        assert worker_states[0]["tenant_id"] == "tenant-a"
        assert worker_states[0]["last_window_id"] == "window-sla-001"


def test_token_auth_tenant_isolation_and_incident_correlation() -> None:
    with tempfile.TemporaryDirectory() as data_dir:
        service = _load_service(data_dir, "l12_rec_tenant_auth")
        client = TestClient(service.app)
        fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        auth_env = {
            "RECONCILIATION_DRIFT_AUTH_MODE": "token",
            "RECONCILIATION_DRIFT_AUTH_TOKEN": "l12-secret",
        }
        with mock.patch.dict("os.environ", auth_env):
            missing = client.get("/api/reconciliation-drift/drift-reports")
            assert missing.status_code == 401

            for tenant_id in ("tenant-a", "tenant-b"):
                tenant_fixture = dict(fixture)
                tenant_fixture["tenant_id"] = tenant_id
                consumed = client.post(
                    "/api/reconciliation-drift/telemetry-events/consume",
                    headers=_auth_headers(tenant_id),
                    json={
                        "tenant_id": tenant_id,
                        "worker_id": f"consumer-{tenant_id}",
                        "events": [tenant_fixture],
                    },
                )
                assert consumed.status_code == 201, consumed.text
                assert consumed.json()["drift_report_count"] == 1

            tenant_a_reports = client.get(
                "/api/reconciliation-drift/drift-reports",
                headers=_auth_headers("tenant-a"),
            ).json()
            tenant_b_reports = client.get(
                "/api/reconciliation-drift/drift-reports",
                headers=_auth_headers("tenant-b"),
            ).json()
            assert len(tenant_a_reports) == len(tenant_b_reports) == 1
            assert tenant_a_reports[0]["tenant_id"] == "tenant-a"
            assert tenant_b_reports[0]["tenant_id"] == "tenant-b"

            incident = {
                "tenant_id": "tenant-a",
                "incident_id": "incident-tenant-a-001",
                "binding_id": "binding-tenant-a-001",
                "runtime_id": "runtime-tenant-a-001",
                "source_event_id": "event-tenant-a-001",
                "telemetry_event_ids": ["event-tenant-a-001"],
                "severity": "high",
                "title": "heartbeat loss",
            }
            triggered = client.post(
                "/api/reconciliation-drift/incident-triggers/consume",
                headers=_auth_headers("tenant-a"),
                json={"tenant_id": "tenant-a", "incident": incident},
            )
            assert triggered.status_code == 201, triggered.text
            trigger_payload = triggered.json()
            assert trigger_payload["tenant_id"] == "tenant-a"
            assert trigger_payload["incident_id"] == "incident-tenant-a-001"
            assert trigger_payload["source_event_id"] == "event-tenant-a-001"

            evaluations = client.get(
                "/api/reconciliation-drift/evaluations",
                headers=_auth_headers("tenant-a"),
                params={"binding_id": "binding-tenant-a-001"},
            ).json()
            assert len(evaluations) == 1
            assert evaluations[0]["tenant_id"] == "tenant-a"
            assert evaluations[0]["telemetry_event_ids"] == ["event-tenant-a-001"]

            mismatch = client.post(
                "/api/reconciliation-drift/incident-triggers/consume",
                headers=_auth_headers("tenant-b"),
                json={"tenant_id": "tenant-b", "incident": incident},
            )
            assert mismatch.status_code == 403
