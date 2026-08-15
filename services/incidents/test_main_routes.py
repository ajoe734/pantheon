"""
Route tests for services/incidents — Incident Evidence Service (BP5-SVC-011).

Covers:
- POST /api/incidents — create, duplicate rejection, missing-field rejection
- GET  /api/incidents — list, filter by binding_id, status, open_only
- GET  /api/incidents/{id} — get, 404 on missing
- POST /api/incidents/{id}/status — lifecycle transitions, auto resolved_at
- GET  /api/incidents/{id}/operator-payload — enriched view, is_open flag
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

import pytest

# Allow import of parent package
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi.testclient import TestClient

from services.incident.incident import IncidentConcurrencyError, IncidentStore
from services.incident.reference_validation import CanonicalReferenceError
from services.incidents.consumer import (
    IncidentConsumerError,
    ThresholdTelemetryIncidentConsumer,
    build_incident_from_drift_report,
)

# The route module constructs CanonicalReferenceValidator at import time.  These
# tests replace that validator with an in-memory accept/reject double before
# exercising routes, but import itself still needs an explicit non-local
# RuntimeManager endpoint so RuntimeManagerClient does not fail closed.  Restore
# the process environment immediately after import so later tests in the same
# pytest process do not discover a fake runtime-manager target.
_PREV_RUNTIME_MANAGER_URL = os.environ.get("PANTHEON_RUNTIME_MANAGER_URL")
_PREV_RUNTIME_MANAGER_TOKEN = os.environ.get("PANTHEON_RUNTIME_MANAGER_TOKEN")
os.environ.setdefault("PANTHEON_RUNTIME_MANAGER_URL", "http://127.0.0.1:9")
os.environ.setdefault("PANTHEON_RUNTIME_MANAGER_TOKEN", "incident-route-test-token")
try:
    from services.incidents.main import app, outbox_store, store
finally:
    if _PREV_RUNTIME_MANAGER_URL is None:
        os.environ.pop("PANTHEON_RUNTIME_MANAGER_URL", None)
    else:
        os.environ["PANTHEON_RUNTIME_MANAGER_URL"] = _PREV_RUNTIME_MANAGER_URL
    if _PREV_RUNTIME_MANAGER_TOKEN is None:
        os.environ.pop("PANTHEON_RUNTIME_MANAGER_TOKEN", None)
    else:
        os.environ["PANTHEON_RUNTIME_MANAGER_TOKEN"] = _PREV_RUNTIME_MANAGER_TOKEN


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_store(monkeypatch):
    """Give each test an isolated in-memory IncidentStore, plus a clean outbox.

    The module-level `store` defaults to a persistent store backed by the
    developer/runtime-shared `/tmp/pantheon/incidents/incidents.json` file.
    Clearing and unlinking that real file before/after every test (the
    previous approach) mutated shared state outside the test's own sandbox.
    Instead, inject a fresh in-memory `IncidentStore(path=None)` for the
    route module *and* this test module's own `store` name (both must point
    at the same object: route handlers read the module global directly, and
    tests assert against the locally-imported name), so incident data never
    touches disk (round-7 review point 5). `outbox_store` has no in-memory
    mode (`AtomicJsonRecordStore` always requires a real path — see
    EVOCHAIN-003's crash-safe delivery admission), so its JSON file is still
    reset between tests the old way.
    """
    class _AcceptAllValidator:
        def validate_incident(self, incident):
            return None

    monkeypatch.setattr("services.incidents.main.reference_validator", _AcceptAllValidator())
    fresh_store = IncidentStore(path=None)
    monkeypatch.setattr("services.incidents.main.store", fresh_store)
    monkeypatch.setattr(sys.modules[__name__], "store", fresh_store)
    _reset_outbox()
    yield
    _reset_outbox()


def _reset_outbox():
    outbox_path = getattr(outbox_store.impl, "path", None)
    if outbox_path is not None and outbox_path.exists():
        outbox_path.unlink()


client = TestClient(app)

_BINDING_ID = "binding-abc"
_INCIDENT_ID = f"inc-test-{uuid.uuid4().hex[:8]}"
_THRESHOLD_FIXTURE_PATH = Path(__file__).with_name("fixtures") / "threshold_breach_telemetry.json"

_BASE_PAYLOAD = {
    "incident_id": _INCIDENT_ID,
    "title": "Drawdown threshold breached",
    "status": "open",
    "severity": "high",
    "binding_id": _BINDING_ID,
    "deployment_stage": "live",
    "deployment_plan_id": "plan-001",
    "capital_pool_id": "pool-001",
    "persona_capital_binding_id": "pcb-001",
    "artifact_id": "artifact-001",
    "artifact_version": "1.0.0",
    "runtime_id": "runtime-001",
    "trace_id": "trace-001",
}


def _threshold_fixture():
    return json.loads(_THRESHOLD_FIXTURE_PATH.read_text(encoding="utf-8"))


def _drift_report_payload(**overrides):
    payload = {
        "drift_report_id": "drift-report-001",
        "recon_run_id": "recon-run-001",
        "incident_cluster_id": "drift:rolling_drawdown_multiple",
        "drift_type": "pnl",
        "status": "open",
        "severity": "medium",
        "scope_ref": "binding-drift-001",
        "binding_id": "binding-drift-001",
        "runtime_id": "runtime-drift-001",
        "deployment_stage": "paper",
        "deployment_plan_id": "plan-drift-001",
        "capital_pool_id": "pool-drift-001",
        "persona_capital_binding_id": "pcb-drift-001",
        "artifact_id": "artifact-drift-001",
        "artifact_version": "1.0.0",
        "trace_id": "trace-drift-001",
        "telemetry_event_ids": ["evt-drift-001"],
        "metrics": {
            "worst_metric": "rolling_drawdown_multiple",
            "breached_metric_ids": ["rolling_drawdown_multiple"],
        },
        "evidence_refs": [
            "telemetry_event:evt-drift-001",
            "drift_report:drift-report-001",
            "reconciliation_record:recon-run-001",
        ],
        "generated_at": "2026-06-27T15:00:00Z",
    }
    payload.update(overrides)
    return payload


def _infrastructure_incident_payload(**overrides):
    payload = {
        "schema_version": "pantheon.infrastructure-incident/1",
        "incident_id": f"infra-bff-{uuid.uuid4().hex[:12]}",
        "tenant_id": "tenant-test",
        "producer": "control-plane-bff",
        "source_event_id": f"infra-event-{uuid.uuid4().hex[:12]}",
        "title": "BFF downstream degradation: incidents",
        "status": "open",
        "severity": "high",
        "component": {
            "service_name": "incidents",
            "component_kind": "fastapi-service",
            "endpoint": "http://incidents:8090/__health__",
        },
        "telemetry_event_ids": ["infra-event-extra"],
        "evidence_summary": "health_probe observed 3/3 failures",
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

def test_health():
    r = client.get("/__health__")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# POST /api/incidents — create
# ---------------------------------------------------------------------------

def test_create_incident():
    r = client.post("/api/incidents", json=_BASE_PAYLOAD)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["incident_id"] == _INCIDENT_ID
    assert body["severity"] == "high"
    assert body["status"] == "open"
    assert body["binding_id"] == _BINDING_ID


def test_create_incident_auto_id():
    payload = {k: v for k, v in _BASE_PAYLOAD.items() if k != "incident_id"}
    r = client.post("/api/incidents", json=payload)
    assert r.status_code == 201
    body = r.json()
    assert body["incident_id"].startswith("inc-")


def test_create_incident_duplicate_rejected():
    client.post("/api/incidents", json=_BASE_PAYLOAD)
    r = client.post("/api/incidents", json=_BASE_PAYLOAD)
    assert r.status_code == 409


def test_create_incident_invalid_severity():
    payload = {**_BASE_PAYLOAD, "incident_id": f"inc-{uuid.uuid4().hex[:8]}", "severity": "extreme"}
    r = client.post("/api/incidents", json=payload)
    assert r.status_code == 422


def test_create_incident_invalid_stage():
    payload = {**_BASE_PAYLOAD, "incident_id": f"inc-{uuid.uuid4().hex[:8]}", "deployment_stage": "staging"}
    r = client.post("/api/incidents", json=payload)
    assert r.status_code == 422


def test_create_incident_reference_error_rejected(monkeypatch):
    class _RejectingValidator:
        def validate_incident(self, incident):
            raise CanonicalReferenceError(["binding_id 'binding-abc' does not resolve"])

    monkeypatch.setattr("services.incidents.main.reference_validator", _RejectingValidator())
    payload = {**_BASE_PAYLOAD, "incident_id": f"inc-{uuid.uuid4().hex[:8]}"}
    r = client.post("/api/incidents", json=payload)
    assert r.status_code == 422
    assert "reference_errors" in r.text


def test_create_incident_missing_binding_id():
    payload = {k: v for k, v in _BASE_PAYLOAD.items() if k != "binding_id"}
    payload["incident_id"] = f"inc-{uuid.uuid4().hex[:8]}"
    r = client.post("/api/incidents", json=payload)
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/incidents/consume-threshold — telemetry threshold consumer
# ---------------------------------------------------------------------------

def test_threshold_consumer_fixture_creates_incident_case():
    payload = _threshold_fixture()
    result = ThresholdTelemetryIncidentConsumer(incident_store=store).consume(payload)

    assert result.created is True
    incident = result.incident
    telemetry = payload["telemetry_event"]
    assert incident.incident_id == payload["incident_id"]
    assert incident.binding_id == telemetry["runtime_binding_id"]
    assert incident.deployment_stage == "paper"
    assert incident.deployment_plan_id == telemetry["deployment_plan_id"]
    assert incident.capital_pool_id == telemetry["capital_pool_id"]
    assert incident.persona_capital_binding_id == telemetry["persona_capital_binding_id"]
    assert incident.artifact_id == telemetry["artifact_id"]
    assert incident.artifact_version == telemetry["artifact_version"]
    assert incident.runtime_id == telemetry["runtime_id"]
    assert incident.trace_id == telemetry["trace_id"]
    assert incident.severity == "high"
    assert incident.telemetry_event_ids == [telemetry["event_id"]]
    assert "threshold_metric=rolling_drawdown_multiple" in (incident.evidence_summary or "")
    assert store.get_incident(payload["incident_id"]) is not None


def test_threshold_consumer_preserves_dedupe_key_note_in_evidence_summary():
    """The producer's dedupe_key audit note must survive into canonical
    incident evidence, not be dropped (round-2 review point 4)."""
    payload = _threshold_fixture()
    payload["incident_id"] = "inc-dedupe-key-evidence"
    payload["threshold_snapshot"]["note"] = "dedupe_key=rb-evochain-001:rolling_drawdown_multiple:paper-daily-sweep:2026-07-13"
    result = ThresholdTelemetryIncidentConsumer(incident_store=store).consume(payload)

    assert result.created is True
    assert "dedupe_key=rb-evochain-001:rolling_drawdown_multiple:paper-daily-sweep:2026-07-13" in (
        result.incident.evidence_summary or ""
    )


def test_consume_threshold_route_creates_incident_case():
    payload = _threshold_fixture()
    r = client.post("/api/incidents/consume-threshold", json=payload)
    assert r.status_code == 201, r.text

    body = r.json()
    telemetry = payload["telemetry_event"]
    assert body["incident_id"] == payload["incident_id"]
    assert body["binding_id"] == telemetry["runtime_binding_id"]
    assert body["deployment_stage"] == "paper"
    assert body["telemetry_event_ids"] == [telemetry["event_id"]]
    assert "threshold_metric=rolling_drawdown_multiple" in body["evidence_summary"]


def test_consume_threshold_route_idempotent_replay_returns_existing():
    payload = _threshold_fixture()
    first = client.post("/api/incidents/consume-threshold", json=payload)
    second = client.post("/api/incidents/consume-threshold", json=payload)

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["incident_id"] == payload["incident_id"]
    assert len(store.list_incidents()) == 1


def test_consume_threshold_route_rejects_unbreached_threshold():
    payload = _threshold_fixture()
    payload["incident_id"] = "inc-unbreached-threshold"
    payload["threshold_snapshot"]["breached"] = False

    r = client.post("/api/incidents/consume-threshold", json=payload)

    assert r.status_code == 422
    assert store.get_incident("inc-unbreached-threshold") is None


def test_consume_threshold_route_rejects_explicit_incident_id_collision_across_identities():
    """A producer-supplied explicit `incident_id` must not let a second
    payload for an unrelated event/binding/metric silently masquerade as a
    duplicate of the first (round-8 review point 4). Reusing the same id for
    a different binding_id/runtime_id/telemetry_event_id must be rejected,
    not treated as created=false against the unrelated first incident."""
    shared_id = "inc-collision-001"
    first = _threshold_fixture()
    first["incident_id"] = shared_id

    r1 = client.post("/api/incidents/consume-threshold", json=first)
    assert r1.status_code == 201, r1.text

    second = _threshold_fixture()
    second["incident_id"] = shared_id
    second["telemetry_event"]["event_id"] = "tel-threshold-fixture-002-unrelated"
    second["telemetry_event"]["runtime_binding_id"] = "rb-threshold-fixture-paper-002-unrelated"
    second["telemetry_event"]["runtime_id"] = "runtime-threshold-fixture-002-unrelated"
    second["threshold_snapshot"]["metric_name"] = "rolling_pnl_floor"

    r2 = client.post("/api/incidents/consume-threshold", json=second)

    assert r2.status_code == 422
    assert "conflicts with an existing incident" in r2.text
    # The first incident must remain exactly as originally created — not
    # overwritten and not falsely reported as a dedupe target.
    assert store.get_incident(shared_id).binding_id == "rb-threshold-fixture-paper-001"


def test_consume_threshold_route_rejects_explicit_incident_id_collision_across_metric_only():
    """Same explicit incident_id, same binding/runtime/event_id, but a
    *different metric* must not be treated as a dedupe of the first breach
    (round-9 review point 4): reusing all identity fields except metric_name
    previously returned created=false against the unrelated first incident."""
    shared_id = "inc-collision-metric-001"
    first = _threshold_fixture()
    first["incident_id"] = shared_id

    r1 = client.post("/api/incidents/consume-threshold", json=first)
    assert r1.status_code == 201, r1.text

    second = _threshold_fixture()
    second["incident_id"] = shared_id
    second["threshold_snapshot"]["metric_name"] = "rolling_pnl_floor"

    r2 = client.post("/api/incidents/consume-threshold", json=second)

    assert r2.status_code == 422
    assert "conflicts with an existing incident" in r2.text


def test_consume_threshold_route_rejects_explicit_incident_id_collision_via_supplemental_id_injection():
    """Changing the real primary event while injecting the *old* event_id as
    a supplemental ``telemetry_event_ids`` entry must not satisfy the
    collision guard's shared-evidence check (round-9 review point 4): only
    the canonical primary event id counts, not an arbitrary set
    intersection."""
    shared_id = "inc-collision-supplemental-001"
    first = _threshold_fixture()
    first["incident_id"] = shared_id
    old_event_id = first["telemetry_event"]["event_id"]

    r1 = client.post("/api/incidents/consume-threshold", json=first)
    assert r1.status_code == 201, r1.text

    second = _threshold_fixture()
    second["incident_id"] = shared_id
    second["telemetry_event"]["event_id"] = "tel-threshold-fixture-001-genuinely-new"
    # Inject the old primary event id as a supplemental top-level id.
    second["telemetry_event_ids"] = [old_event_id]

    r2 = client.post("/api/incidents/consume-threshold", json=second)

    assert r2.status_code == 422
    assert "conflicts with an existing incident" in r2.text


def test_consume_threshold_route_rejects_non_string_window():
    payload = _threshold_fixture()
    payload["incident_id"] = "inc-bad-window-type"
    payload["threshold_snapshot"]["window"] = ["daily"]

    r = client.post("/api/incidents/consume-threshold", json=payload)

    assert r.status_code == 422
    assert "window must be a string" in r.text
    assert store.get_incident("inc-bad-window-type") is None


def test_consume_threshold_route_rejects_non_string_note():
    payload = _threshold_fixture()
    payload["incident_id"] = "inc-bad-note-type"
    payload["threshold_snapshot"]["note"] = {"bad": True}

    r = client.post("/api/incidents/consume-threshold", json=payload)

    assert r.status_code == 422
    assert "note must be a string" in r.text
    assert store.get_incident("inc-bad-note-type") is None


def test_consume_threshold_route_rejects_non_boolean_breached():
    """The governance schema (evolution_decision.schema.json threshold_snapshots[]
    .breached) declares this field boolean-typed. A truthy non-bool value (e.g.
    the string "yes") must be rejected rather than coerced into an open
    incident (round-8 review point 3)."""
    payload = _threshold_fixture()
    payload["incident_id"] = "inc-non-bool-breached"
    payload["threshold_snapshot"]["breached"] = "yes"

    r = client.post("/api/incidents/consume-threshold", json=payload)

    assert r.status_code == 422
    assert "breached" in r.text
    assert store.get_incident("inc-non-bool-breached") is None


def test_consume_threshold_route_rejects_empty_metric_name():
    payload = _threshold_fixture()
    payload["incident_id"] = "inc-empty-metric"
    payload["threshold_snapshot"]["metric_name"] = ""

    r = client.post("/api/incidents/consume-threshold", json=payload)

    assert r.status_code == 422
    assert "metric_name is required" in r.text
    assert store.get_incident("inc-empty-metric") is None


def test_consume_threshold_route_rejects_empty_policy_source():
    payload = _threshold_fixture()
    payload["incident_id"] = "inc-empty-policy-source"
    payload["threshold_snapshot"]["policy_source"] = "   "

    r = client.post("/api/incidents/consume-threshold", json=payload)

    assert r.status_code == 422
    assert "policy_source is required" in r.text
    assert store.get_incident("inc-empty-policy-source") is None


def test_consume_threshold_route_rejects_missing_signal_type():
    """The canonical ThresholdSnapshot requires signal_type
    (evolution_decision.schema.json); dropping it must not still create an
    IncidentCase (round-7 review point 4)."""
    payload = _threshold_fixture()
    payload["incident_id"] = "inc-missing-signal-type"
    del payload["threshold_snapshot"]["signal_type"]

    r = client.post("/api/incidents/consume-threshold", json=payload)

    assert r.status_code == 422
    assert "signal_type is required" in r.text
    assert store.get_incident("inc-missing-signal-type") is None


def test_consume_threshold_route_rejects_unknown_signal_type():
    payload = _threshold_fixture()
    payload["incident_id"] = "inc-bad-signal-type"
    payload["threshold_snapshot"]["signal_type"] = "not_a_canonical_signal_type"

    r = client.post("/api/incidents/consume-threshold", json=payload)

    assert r.status_code == 422
    assert "signal_type is required" in r.text
    assert store.get_incident("inc-bad-signal-type") is None


def test_consume_threshold_route_rejects_missing_comparator():
    payload = _threshold_fixture()
    payload["incident_id"] = "inc-missing-comparator"
    del payload["threshold_snapshot"]["comparator"]

    r = client.post("/api/incidents/consume-threshold", json=payload)

    assert r.status_code == 422
    assert "comparator is required" in r.text
    assert store.get_incident("inc-missing-comparator") is None


def test_consume_threshold_route_rejects_missing_observed_value():
    payload = _threshold_fixture()
    payload["incident_id"] = "inc-missing-observed-value"
    del payload["threshold_snapshot"]["observed_value"]

    r = client.post("/api/incidents/consume-threshold", json=payload)

    assert r.status_code == 422
    assert "observed_value is required" in r.text
    assert store.get_incident("inc-missing-observed-value") is None


def test_consume_threshold_route_rejects_missing_threshold_value():
    payload = _threshold_fixture()
    payload["incident_id"] = "inc-missing-threshold-value"
    del payload["threshold_snapshot"]["threshold_value"]

    r = client.post("/api/incidents/consume-threshold", json=payload)

    assert r.status_code == 422
    assert "threshold_value is required" in r.text
    assert store.get_incident("inc-missing-threshold-value") is None


def test_consume_drift_report_route_creates_incident_case():
    payload = _drift_report_payload()
    r = client.post("/api/incidents/consume-drift-report", json={"drift_report": payload})

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["incident_id"].startswith("inc-drift-")
    assert body["binding_id"] == "binding-drift-001"
    assert body["runtime_id"] == "runtime-drift-001"
    assert body["telemetry_event_ids"] == ["evt-drift-001"]
    assert body["reconciliation_ids"] == ["drift-report-001", "recon-run-001"]
    assert body["incident_cluster_id"] == "drift:rolling_drawdown_multiple"
    assert len(store.list_incidents()) == 1


def test_drift_report_rejects_multiple_telemetry_events():
    payload = _drift_report_payload(
        telemetry_event_ids=["evt-drift-001", "evt-drift-002"],
        evidence_refs=[
            "telemetry_event:evt-drift-001",
            "telemetry_event:evt-drift-002",
            "drift_report:drift-report-001",
            "reconciliation_record:recon-run-001",
        ],
    )

    with pytest.raises(
        IncidentConsumerError,
        match="exactly one telemetry_event_id",
    ):
        build_incident_from_drift_report(payload)


def test_consume_drift_report_route_dedupes_by_binding_runtime_cluster():
    first_payload = _drift_report_payload()
    second_payload = _drift_report_payload(
        drift_report_id="drift-report-002",
        recon_run_id="recon-run-002",
        severity="critical",
        telemetry_event_ids=["evt-drift-002"],
        evidence_refs=[
            "telemetry_event:evt-drift-002",
            "drift_report:drift-report-002",
            "reconciliation_record:recon-run-002",
        ],
        generated_at="2026-06-27T15:05:00Z",
    )

    first = client.post("/api/incidents/consume-drift-report", json={"drift_report": first_payload})
    second = client.post("/api/incidents/consume-drift-report", json={"drift_report": second_payload})

    assert first.status_code == 201
    assert second.status_code == 200, second.text
    assert second.json()["incident_id"] == first.json()["incident_id"]
    assert second.json()["severity"] == "critical"
    assert second.json()["telemetry_event_ids"] == ["evt-drift-001", "evt-drift-002"]
    assert second.json()["reconciliation_ids"] == [
        "drift-report-001",
        "recon-run-001",
        "drift-report-002",
        "recon-run-002",
    ]
    assert len(store.list_incidents()) == 1


def test_consume_drift_report_retries_owner_store_cas_conflict(monkeypatch):
    first_payload = _drift_report_payload()
    second_payload = _drift_report_payload(
        drift_report_id="drift-report-cas-retry",
        recon_run_id="recon-run-cas-retry",
        telemetry_event_ids=["evt-drift-cas-retry"],
        evidence_refs=[
            "telemetry_event:evt-drift-cas-retry",
            "drift_report:drift-report-cas-retry",
            "reconciliation_record:recon-run-cas-retry",
        ],
        generated_at="2026-06-27T15:10:00Z",
    )
    first = client.post(
        "/api/incidents/consume-drift-report",
        json={"drift_report": first_payload},
    )
    assert first.status_code == 201, first.text

    real_merge = store.merge_incident_evidence
    attempts = 0

    def conflict_once(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise IncidentConcurrencyError("injected owner-store CAS race")
        return real_merge(*args, **kwargs)

    monkeypatch.setattr(store, "merge_incident_evidence", conflict_once)
    retried = client.post(
        "/api/incidents/consume-drift-report",
        json={"drift_report": second_payload},
    )

    assert retried.status_code == 200, retried.text
    assert attempts == 2
    assert retried.json()["telemetry_event_ids"] == [
        "evt-drift-001",
        "evt-drift-cas-retry",
    ]


def test_consume_drift_report_returns_retryable_after_cas_budget(monkeypatch):
    payload = _drift_report_payload()
    created = client.post(
        "/api/incidents/consume-drift-report",
        json={"drift_report": payload},
    )
    assert created.status_code == 201, created.text

    attempts = 0

    def always_conflicts(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        raise IncidentConcurrencyError("injected persistent owner-store CAS race")

    monkeypatch.setattr(store, "merge_incident_evidence", always_conflicts)
    retried = client.post(
        "/api/incidents/consume-drift-report",
        json={"drift_report": payload},
    )

    assert retried.status_code == 503, retried.text
    assert attempts == 3
    assert "retry budget" in retried.json()["detail"]


def test_consume_infrastructure_health_creates_non_runtime_incident():
    payload = _infrastructure_incident_payload(
        incident_id="infra-bff-incidents-down-001",
        source_event_id="infra-event-incidents-down-001",
    )

    r = client.post("/api/incidents/consume-infrastructure-health", json=payload)

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["incident_id"] == "infra-bff-incidents-down-001"
    assert body["status"] == "open"
    assert body["severity"] == "high"
    assert body["binding_id"].startswith("infra-subject-")
    assert body["deployment_stage"] == "paper"
    assert body["runtime_id"].startswith("infra-endpoint-")
    assert body["trace_id"] == "infra-event-incidents-down-001"
    assert body["telemetry_event_ids"][0] == "infra-event-incidents-down-001"
    assert body["incident_cluster_id"].startswith("infrastructure:")
    assert "non_trading_infrastructure_incident=true" in body["evidence_summary"]
    assert store.get_incident("infra-bff-incidents-down-001") is not None


def test_consume_infrastructure_health_idempotent_replay_returns_existing():
    payload = _infrastructure_incident_payload(
        incident_id="infra-bff-telemetry-down-001",
        source_event_id="infra-event-telemetry-down-001",
        component={
            "service_name": "telemetry",
            "component_kind": "flask-service",
            "endpoint": "http://telemetry:8080/healthz",
        },
    )

    first = client.post("/api/incidents/consume-infrastructure-health", json=payload)
    replay = client.post("/api/incidents/consume-infrastructure-health", json=payload)

    assert first.status_code == 201, first.text
    assert replay.status_code == 200, replay.text
    assert replay.json() == first.json()
    assert len(store.list_incidents()) == 1


def test_consume_infrastructure_health_conflicting_replay_rejected():
    first_payload = _infrastructure_incident_payload(
        incident_id="infra-bff-conflict-001",
        source_event_id="infra-event-conflict-a",
    )
    conflicting_payload = _infrastructure_incident_payload(
        incident_id="infra-bff-conflict-001",
        source_event_id="infra-event-conflict-b",
    )

    first = client.post("/api/incidents/consume-infrastructure-health", json=first_payload)
    conflict = client.post("/api/incidents/consume-infrastructure-health", json=conflicting_payload)

    assert first.status_code == 201, first.text
    assert conflict.status_code == 409, conflict.text
    assert "different subject or source_event_id" in conflict.json()["detail"]
    assert store.get_incident("infra-bff-conflict-001").trace_id == "infra-event-conflict-a"


def test_consume_infrastructure_health_ignores_runtime_binding_payload_shape():
    payload = _infrastructure_incident_payload(
        binding_id="rb-fake-runtime-binding",
        deployment_stage="live",
    )

    r = client.post("/api/incidents/consume-infrastructure-health", json=payload)

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["binding_id"] != "rb-fake-runtime-binding"
    assert body["deployment_stage"] == "paper"
    assert body["binding_id"].startswith("infra-subject-")


def test_infrastructure_health_incident_resolves_through_status_route():
    payload = _infrastructure_incident_payload(
        incident_id="infra-bff-resolve-001",
        source_event_id="infra-event-resolve-open",
    )
    created = client.post("/api/incidents/consume-infrastructure-health", json=payload)
    assert created.status_code == 201, created.text

    resolved = client.post(
        "/api/incidents/infra-bff-resolve-001/status",
        json={
            "status": "resolved",
            "resolved_at": "2026-07-27T19:45:00Z",
            "source_event_id": "infra-event-resolve-ok",
            "telemetry_event_ids": ["infra-event-resolve-ok"],
        },
    )

    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "resolved"
    assert resolved.json()["resolved_at"] == "2026-07-27T19:45:00Z"


# ---------------------------------------------------------------------------
# GET /api/incidents — list
# ---------------------------------------------------------------------------

def test_list_incidents_empty():
    r = client.get("/api/incidents")
    assert r.status_code == 200
    assert r.json() == []


def test_list_incidents_returns_created():
    client.post("/api/incidents", json=_BASE_PAYLOAD)
    r = client.get("/api/incidents")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    assert items[0]["incident_id"] == _INCIDENT_ID


def test_list_filter_by_binding_id():
    client.post("/api/incidents", json=_BASE_PAYLOAD)
    other = {
        **_BASE_PAYLOAD,
        "incident_id": f"inc-other-{uuid.uuid4().hex[:6]}",
        "binding_id": "binding-other",
    }
    client.post("/api/incidents", json=other)
    r = client.get(f"/api/incidents?binding_id={_BINDING_ID}")
    assert r.status_code == 200
    items = r.json()
    assert all(i["binding_id"] == _BINDING_ID for i in items)
    assert len(items) == 1


def test_list_filter_by_status():
    client.post("/api/incidents", json=_BASE_PAYLOAD)
    r = client.get("/api/incidents?status=open")
    assert r.status_code == 200
    assert all(i["status"] == "open" for i in r.json())


def test_list_filter_open_only():
    client.post("/api/incidents", json=_BASE_PAYLOAD)
    resolved_payload = {
        **_BASE_PAYLOAD,
        "incident_id": f"inc-resolved-{uuid.uuid4().hex[:6]}",
        "status": "resolved",
    }
    # Can't create resolved without resolved_at via store directly — create open then transition
    r2 = client.post("/api/incidents", json={**_BASE_PAYLOAD, "incident_id": f"inc-r-{uuid.uuid4().hex[:6]}"})
    inc2_id = r2.json()["incident_id"]
    client.post(f"/api/incidents/{inc2_id}/status", json={"status": "resolved"})

    r = client.get("/api/incidents?open_only=true")
    assert r.status_code == 200
    items = r.json()
    assert all(i["status"] in ("open", "investigating") for i in items)


# ---------------------------------------------------------------------------
# GET /api/incidents/{id} — get single
# ---------------------------------------------------------------------------

def test_get_incident():
    client.post("/api/incidents", json=_BASE_PAYLOAD)
    r = client.get(f"/api/incidents/{_INCIDENT_ID}")
    assert r.status_code == 200
    assert r.json()["incident_id"] == _INCIDENT_ID


def test_get_incident_not_found():
    r = client.get("/api/incidents/inc-missing")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/incidents/{id}/status — lifecycle transitions
# ---------------------------------------------------------------------------

def test_status_transition_open_to_investigating():
    client.post("/api/incidents", json=_BASE_PAYLOAD)
    r = client.post(
        f"/api/incidents/{_INCIDENT_ID}/status",
        json={"status": "investigating"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "investigating"


def test_status_transition_to_resolved_auto_sets_resolved_at():
    client.post("/api/incidents", json=_BASE_PAYLOAD)
    r = client.post(
        f"/api/incidents/{_INCIDENT_ID}/status",
        json={"status": "resolved"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "resolved"
    assert body["resolved_at"] is not None


def test_status_transition_invalid_status():
    client.post("/api/incidents", json=_BASE_PAYLOAD)
    r = client.post(
        f"/api/incidents/{_INCIDENT_ID}/status",
        json={"status": "invalid_status"},
    )
    assert r.status_code == 400


def test_status_transition_404():
    r = client.post(
        "/api/incidents/inc-missing/status",
        json={"status": "resolved"},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/incidents/{id}/operator-payload
# ---------------------------------------------------------------------------

def test_operator_payload_basic():
    client.post("/api/incidents", json=_BASE_PAYLOAD)
    r = client.get(f"/api/incidents/{_INCIDENT_ID}/operator-payload")
    assert r.status_code == 200
    body = r.json()
    assert body["incident_id"] == _INCIDENT_ID
    assert body["is_open"] is True
    assert body["postmortem_id"] is None
    assert body["linked_evolution_decision_id"] is None


def test_operator_payload_is_open_false_when_resolved():
    client.post("/api/incidents", json=_BASE_PAYLOAD)
    client.post(f"/api/incidents/{_INCIDENT_ID}/status", json={"status": "resolved"})
    r = client.get(f"/api/incidents/{_INCIDENT_ID}/operator-payload")
    assert r.status_code == 200
    assert r.json()["is_open"] is False


def test_operator_payload_not_found():
    r = client.get("/api/incidents/inc-missing/operator-payload")
    assert r.status_code == 404


def test_operator_payload_evidence_fields_present():
    client.post("/api/incidents", json=_BASE_PAYLOAD)
    r = client.get(f"/api/incidents/{_INCIDENT_ID}/operator-payload")
    body = r.json()
    for field in (
        "binding_id", "deployment_stage", "deployment_plan_id",
        "capital_pool_id", "persona_capital_binding_id",
        "artifact_id", "artifact_version", "runtime_id", "trace_id",
    ):
        assert field in body, f"Missing evidence field: {field}"
        assert body[field], f"Evidence field should not be empty: {field}"
