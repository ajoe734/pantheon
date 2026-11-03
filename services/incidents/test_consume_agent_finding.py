"""consume-agent-finding dedupes by fingerprint and merges into the open incident."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("PANTHEON_RUNTIME_MANAGER_URL", "http://127.0.0.1:9")
os.environ.setdefault("PANTHEON_RUNTIME_MANAGER_TOKEN", "incident-route-test-token")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import services.incidents.main as incidents_main  # noqa: E402
from services.incident.incident import IncidentStore  # noqa: E402

app = incidents_main.app

URL = "/api/incidents/consume-agent-finding"


@pytest.fixture(autouse=True)
def store(monkeypatch):
    fresh = IncidentStore()
    monkeypatch.setattr(incidents_main, "store", fresh)
    return fresh


def _body(**over):
    body = {
        "fingerprint": "drawdown:persona-a", "title": "Drawdown spike", "severity": "high",
        "rationale": "drawdown 9% with no incident", "snapshot_ref": "snap-1",
    }
    return {**body, **over}


def test_repeat_finding_updates_open_incident(store):
    client = TestClient(app)
    first = client.post(URL, json=_body(fingerprint="fp-repeat"))
    assert first.status_code == 201
    second = client.post(URL, json=_body(fingerprint="fp-repeat", snapshot_ref="snap-2", rationale="still"))
    assert second.status_code == 200
    assert second.json()["incident_id"] == first.json()["incident_id"]
    summary = store.get_incident(first.json()["incident_id"]).evidence_summary
    assert "snap-1" in summary and "snap-2" in summary and "rationale=" in summary


def test_different_fingerprint_opens_new_incident():
    client = TestClient(app)
    a = client.post(URL, json=_body(fingerprint="fp-a")).json()["incident_id"]
    b = client.post(URL, json=_body(fingerprint="fp-b")).json()["incident_id"]
    assert a != b


def test_missing_fields_rejected():
    assert TestClient(app).post(URL, json=_body(rationale="")).status_code == 422


def test_concurrent_same_fingerprint_creates_one_open_incident(store):
    import threading

    barrier = threading.Barrier(2)
    original = store.find_open_incidents

    def racing_find():
        found = original()
        try:
            barrier.wait(timeout=2)  # both threads see "no open incident" before either creates
        except threading.BrokenBarrierError:
            pass
        return found

    store.find_open_incidents = racing_find
    codes = []

    def post():
        codes.append(TestClient(app).post(URL, json=_body(fingerprint="fp-race")).status_code)

    threads = [threading.Thread(target=post) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(codes) == [200, 201]
    assert len([i for i in original() if "agent-" in i.incident_id]) == 1


def test_finding_naming_stable_reference_merges_and_never_opens_new_incident(store):
    client = TestClient(app)
    first = client.post(
        URL,
        json=_body(
            fingerprint="custom-anomaly-v1",
            title="Custom anomaly initial observation",
            rationale="initial observation",
            snapshot_ref="snap-1",
        ),
    )
    assert first.status_code == 201
    inc_id = first.json()["incident_id"]

    # Naming the stable reference via incident_id with a brand-new fingerprint merges into inc_id
    second = client.post(
        URL,
        json=_body(
            incident_id=inc_id,
            fingerprint="completely-different-fingerprint-v2",
            title="Custom anomaly reworded",
            rationale="second observation",
            snapshot_ref="snap-2",
        ),
    )
    assert second.status_code == 200
    assert second.json()["incident_id"] == inc_id

    # Naming the stable reference in fingerprint directly also merges into inc_id
    third = client.post(
        URL,
        json=_body(
            fingerprint=inc_id,
            title="Custom anomaly third wording",
            rationale="third observation",
            snapshot_ref="snap-3",
        ),
    )
    assert third.status_code == 200
    assert third.json()["incident_id"] == inc_id
    assert len(store.find_open_incidents()) == 1
    persisted = store.require_incident(inc_id)
    assert "snap-1" in (persisted.evidence_summary or "")
    assert "snap-2" in (persisted.evidence_summary or "")
    assert "snap-3" in (persisted.evidence_summary or "")


def test_twenty_differently_worded_reconciler_and_fleet_findings_keep_at_most_one_open_each(store):
    client = TestClient(app)

    reconciler_findings = [
        ("reconciler-generation-mismatch-gen78", "Reconciler generation jumped from 77 to 78", "generation advanced after restart"),
        ("reconciler-gen-advance-114", "Reconciler gen advance to 114", "observed generation 114 in control_loop_health"),
        ("reconciler-generation-jump-131", "Reconciler generation jump 131", "generation counter stepped to 131"),
        ("fence-token-advance-gen79", "Reconciler fence_token advanced to 79", "fence_token incremented on restart"),
        ("reconciler-fence-token-mismatch-80", "Fence token mismatch 80 on reconciler", "fence token moved from 79 to 80"),
        ("control-loop-reconciler-gen-81", "Control loop reconciler generation 81", "reconciler generation incremented"),
        ("paper-reconciler-generation-82", "Paper reconciler generation changed to 82", "generation counter 82"),
        ("reconciler-leader-epoch-advance-83", "Reconciler leader epoch advanced to 83", "epoch and fence_token stepped"),
        ("reconciler-monotonic-gen-84", "Monotonic generation advance on reconciler (84)", "generation 84 observed"),
        ("fence_token_step_85_reconciler", "Reconciler fence_token stepped to 85", "control loop fence_token=85"),
        ("reconciler-restart-generation-86", "Reconciler restart advanced generation to 86", "generation counter advanced"),
        ("reconciler-gen-rollover-87", "Reconciler gen rollover to 87", "generation advanced to 87"),
        ("reconciler-fence-88-jump", "Reconciler fence token jump to 88", "fence_token jumped from 87 to 88"),
        ("reconciliation-generation-drift-89", "Reconciliation generation drift to 89", "reconciler generation is now 89"),
        ("reconciler-counter-advance-90", "Reconciler counter advance 90", "generation counter=90"),
        ("fence-token-reconciler-91", "Fence token on paper reconciler is 91", "fence_token advanced to 91"),
        ("reconciler-gen-step-92", "Reconciler generation step 92", "observed generation step to 92"),
        ("reconciler-generation-change-93", "Reconciler generation change to 93", "generation changed from 92 to 93"),
        ("reconciler-fence-epoch-94", "Reconciler fence epoch 94", "fence_token and generation at 94"),
        ("reconciler-gen-95-observed", "Reconciler gen 95 observed in readyz", "generation advanced to 95"),
    ]

    fleet_findings = [
        ("paper-fleet-size-change-12", "Paper fleet size changed from 10 to 12", "desired-state active runtimes changed to 12"),
        ("fleet-runtime-count-drift-8", "Fleet runtime count drifted to 8", "runtime_count in desired-state is 8"),
        ("active-runtime-count-mismatch-11", "Active runtime count changed to 11", "active_runtimes count moved from 8 to 11"),
        ("paper-fleet-scaled-down-6", "Paper fleet scaled down to 6 runtimes", "fleet size decreased to 6"),
        ("runtime-count-drop-paper-5", "Paper runtime count dropped to 5", "number of active runtimes dropped to 5"),
        ("fleet-binding-count-increase-14", "Fleet binding count increased to 14", "binding count in paper fleet is 14"),
        ("total-runtimes-changed-15", "Total runtimes changed to 15", "total_runtimes in fleet desired state is 15"),
        ("paper-fleet-worker-count-9", "Paper fleet worker count changed to 9", "worker count shifted to 9"),
        ("fleet-size-drift-paper-7", "Fleet size drift in paper environment (7)", "paper fleet size is now 7"),
        ("active-runtimes-grew-to-13", "Active runtimes grew to 13", "fleet runtime count increased to 13"),
        ("paper-fleet-count-mismatch-10", "Paper fleet count mismatch (10 active)", "fleet count changed to 10"),
        ("runtime-fleet-size-step-16", "Runtime fleet size stepped to 16", "desired-state fleet size=16"),
        ("paper-strategy-count-change-4", "Paper strategy count changed in fleet to 4", "strategy count in paper fleet is 4"),
        ("fleet-active-bindings-18", "Fleet active bindings count is 18", "number of active bindings changed to 18"),
        ("paper-fleet-capacity-change-20", "Paper fleet capacity changed to 20 runtimes", "fleet runtime capacity is 20"),
        ("runtime-count-shift-17", "Runtime count shift to 17 in fleet", "runtime count changed to 17"),
        ("paper-fleet-shrank-to-3", "Paper fleet shrank to 3 active instances", "fleet instance count shrank to 3"),
        ("fleet-instance-count-19", "Fleet instance count changed to 19", "active instance count is 19"),
        ("paper-runtime-fleet-count-21", "Paper runtime fleet count moved to 21", "total runtimes in fleet=21"),
        ("fleet-size-adjustment-22", "Fleet size adjustment to 22 runtimes", "paper fleet size adjusted to 22"),
    ]

    assert len(reconciler_findings) == 20
    assert len(fleet_findings) == 20

    reconciler_ids = set()
    for idx, (fp, title, rationale) in enumerate(reconciler_findings):
        resp = client.post(
            URL,
            json=_body(
                fingerprint=fp,
                title=title,
                rationale=rationale,
                snapshot_ref=f"snap-rec-{idx}",
            ),
        )
        assert resp.status_code == (201 if idx == 0 else 200)
        reconciler_ids.add(resp.json()["incident_id"])

    fleet_ids = set()
    for idx, (fp, title, rationale) in enumerate(fleet_findings):
        resp = client.post(
            URL,
            json=_body(
                fingerprint=fp,
                title=title,
                rationale=rationale,
                snapshot_ref=f"snap-fleet-{idx}",
            ),
        )
        assert resp.status_code == (201 if idx == 0 else 200)
        fleet_ids.add(resp.json()["incident_id"])

    assert len(reconciler_ids) == 1
    assert len(fleet_ids) == 1
    assert reconciler_ids != fleet_ids

    open_incidents = store.find_open_incidents()
    assert len(open_incidents) == 2
    assert {inc.incident_id for inc in open_incidents} == reconciler_ids | fleet_ids


def test_stale_finding_resolved_with_not_reobserved_after_window(store, monkeypatch):
    monkeypatch.setenv("INCIDENTS_AGENT_FINDING_RESOLVE_AFTER_SECONDS", "3600")
    client = TestClient(app)

    monkeypatch.setattr(incidents_main, "_utc_now", lambda: "2026-11-01T10:00:00Z")
    r1 = client.post(
        URL,
        json=_body(
            fingerprint="fp-expire-check",
            title="Transient monitor finding",
            rationale="first seen at 10:00",
            snapshot_ref="monitor-snapshot-20261101T100000Z-aaaa11112222",
            observed_at="2026-11-01T10:00:00Z",
        ),
    )
    assert r1.status_code == 201
    inc_id = r1.json()["incident_id"]

    # Re-observed at 10:45 (within 1h window)
    monkeypatch.setattr(incidents_main, "_utc_now", lambda: "2026-11-01T10:45:00Z")
    r2 = client.post(
        URL,
        json=_body(
            incident_id=inc_id,
            fingerprint="fp-expire-check",
            title="Transient monitor finding",
            rationale="re-observed at 10:45",
            snapshot_ref="monitor-snapshot-20261101T104500Z-bbbb11112222",
            observed_at="2026-11-01T10:45:00Z",
        ),
    )
    assert r2.status_code == 200

    # At 11:30 (45m after 10:45 re-observation, even though 90m after creation), still open
    from datetime import datetime, timezone
    incidents_main.resolve_stale_agent_finding_incidents(
        now=datetime(2026, 11, 1, 11, 30, 0, tzinfo=timezone.utc),
    )
    assert store.require_incident(inc_id).status == "open"

    # At 11:46 (61m after 10:45 last observation >= 3600s window), resolved with not_reobserved
    resolved = incidents_main.resolve_stale_agent_finding_incidents(
        now=datetime(2026, 11, 1, 11, 46, 0, tzinfo=timezone.utc),
    )
    assert [inc.incident_id for inc in resolved] == [inc_id]
    persisted = store.require_incident(inc_id)
    assert persisted.status == "resolved"
    assert "resolution_reason=not_reobserved" in (persisted.evidence_summary or "")
    assert "resolution_reason:not_reobserved" in persisted.telemetry_event_ids


def test_not_reobserved_never_resolves_runtime_drift_infrastructure_or_deployment_incidents(store, monkeypatch):
    from datetime import datetime, timezone
    from services.incident.incident import IncidentCase
    from services.incidents.consumer import (
        build_incident_from_drift_report,
        build_incident_from_infrastructure_health_payload,
    )

    monkeypatch.setenv("INCIDENTS_AGENT_FINDING_RESOLVE_AFTER_SECONDS", "3600")
    client = TestClient(app)

    monkeypatch.setattr(incidents_main, "_utc_now", lambda: "2026-10-20T00:00:00Z")
    finding_resp = client.post(
        URL,
        json=_body(
            fingerprint="fp-agent-old",
            title="Old monitor finding",
            rationale="observed long ago",
            snapshot_ref="monitor-snapshot-20261020T000000Z-111122223333",
            observed_at="2026-10-20T00:00:00Z",
        ),
    )
    finding_id = finding_resp.json()["incident_id"]

    runtime_inc = IncidentCase(
        incident_id="inc-runtime-old-001",
        title="Runtime drawdown breach",
        status="open",
        severity="high",
        created_at="2026-10-20T00:00:00Z",
        binding_id="binding-rt-01",
        deployment_stage="paper",
        deployment_plan_id="plan-rt-01",
        capital_pool_id="pool-rt-01",
        persona_capital_binding_id="pcb-rt-01",
        artifact_id="artifact-rt-01",
        artifact_version="1.0.0",
        runtime_id="runtime-rt-01",
        trace_id="trace-rt-01",
        evidence_summary="Runtime drawdown exceeded threshold",
    )
    drift_inc = build_incident_from_drift_report(
        {
            "drift_report_id": "drift-old-001",
            "recon_run_id": "recon-old-001",
            "incident_cluster_id": "drift:rolling_drawdown_multiple",
            "drift_type": "pnl",
            "status": "open",
            "severity": "medium",
            "binding_id": "binding-drift-01",
            "runtime_id": "runtime-drift-01",
            "deployment_stage": "paper",
            "deployment_plan_id": "plan-drift-01",
            "capital_pool_id": "pool-drift-01",
            "persona_capital_binding_id": "pcb-drift-01",
            "artifact_id": "artifact-drift-01",
            "artifact_version": "1.0.0",
            "trace_id": "trace-drift-01",
            "telemetry_event_ids": ["evt-drift-01"],
            "generated_at": "2026-10-20T00:00:00Z",
        }
    )
    infra_inc = build_incident_from_infrastructure_health_payload(
        {
            "schema_version": "pantheon.infrastructure-incident/1",
            "incident_id": "infra-bff-old-001",
            "tenant_id": "default",
            "producer": "control-plane-bff",
            "source_event_id": "evt-infra-old-001",
            "title": "Downstream service degraded",
            "status": "open",
            "severity": "high",
            "created_at": "2026-10-20T00:00:00Z",
            "component": {
                "service_name": "telemetry",
                "component_kind": "fastapi-service",
                "endpoint": "http://telemetry:8083/__health__",
            },
        }
    )
    deployment_inc = IncidentCase(
        incident_id="inc-deployment-old-001",
        title="Deployment canary gate failure",
        status="open",
        severity="critical",
        created_at="2026-10-20T00:00:00Z",
        binding_id="binding-deploy-01",
        deployment_stage="canary",
        deployment_plan_id="plan-deploy-01",
        capital_pool_id="pool-deploy-01",
        persona_capital_binding_id="pcb-deploy-01",
        artifact_id="artifact-deploy-01",
        artifact_version="2.1.0",
        runtime_id="runtime-deploy-01",
        trace_id="trace-deploy-01",
        incident_cluster_id="deployment:canary:plan-deploy-01",
        evidence_summary="Deployment canary health gate failed",
    )
    store.create_incident(runtime_inc)
    store.create_incident(drift_inc)
    store.create_incident(infra_inc)
    store.create_incident(deployment_inc)

    monkeypatch.setattr(incidents_main, "_utc_now", lambda: "2026-10-20T03:00:00Z")
    open_resp = client.get("/api/incidents?open_only=true")
    assert open_resp.status_code == 200
    open_ids = {item["incident_id"] for item in open_resp.json()}
    assert finding_id not in open_ids
    assert {
        runtime_inc.incident_id,
        drift_inc.incident_id,
        infra_inc.incident_id,
        deployment_inc.incident_id,
    }.issubset(open_ids)
    assert store.require_incident(finding_id).status == "resolved"
    assert "resolution_reason=not_reobserved" in (store.require_incident(finding_id).evidence_summary or "")


def test_pre_existing_legacy_open_finding_incidents_resolve_without_manual_edits(store, monkeypatch):
    from services.incidents.consumer import build_incident_from_infrastructure_health_payload

    monkeypatch.setenv("INCIDENTS_AGENT_FINDING_RESOLVE_AFTER_SECONDS", "7200")
    client = TestClient(app)

    # Construct legacy monitor-agent finding incident exactly as stored before this change (no observed_at field)
    legacy = build_incident_from_infrastructure_health_payload(
        {
            "schema_version": "pantheon.infrastructure-incident/1",
            "incident_id": "agent-legacy123456-0000",
            "source_event_id": "monitor-snapshot-20261015T080000Z-1234567890ab",
            "tenant_id": "default",
            "producer": "monitor-agent",
            "created_at": "2026-10-15T08:00:00Z",
            "component": {
                "service_name": "agent-finding-legacy123456",
                "component_kind": "monitor-agent-finding",
                "endpoint": "monitor-agent://legacy123456",
            },
            "title": "Legacy open finding from 2026-10-15",
            "severity": "medium",
            "evidence_summary": "snapshot_ref=monitor-snapshot-20261015T080000Z-1234567890ab; rationale=legacy",
        }
    )
    store.create_incident(legacy)

    open_resp = client.get("/api/incidents?open_only=true")
    assert open_resp.status_code == 200
    assert [item["incident_id"] for item in open_resp.json()] == []

    resolved_resp = client.get("/api/incidents/agent-legacy123456-0000")
    assert resolved_resp.status_code == 200
    body = resolved_resp.json()
    assert body["status"] == "resolved"
    assert "resolution_reason=not_reobserved" in (body.get("evidence_summary") or "")

