"""Regression tests for INFRA-INCIDENT-ORPHAN-RESOLVE-20261103.

Covers:
1. At-most-one open infrastructure incident per component with merge on second open event.
2. Component recovery resolving every open infrastructure incident for that component,
   including incidents opened before a BFF health ledger reset or mapping overwrite.
3. Orphan recovery within one probe interval after deployment for healthy components
   unreferenced by any active BFF health ledger row, recording ``orphan_recovered``.
4. Exact hosted dev regression reproduction (2026-10-22 orphaned incidents alongside
   later 2026-10-27 / 2026-11-02 incidents for ``governance``, ``capital``,
   ``deployment``, ``promotion``, ``consultation``, and ``research-orchestrator``).
5. Strict non-interference with monitor-agent finding, runtime, drift, and
   deployment incidents.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from services.control_plane.bff.downstream_health_monitor import (
    DownstreamHealthMonitor,
    DownstreamProbeResult,
    DownstreamTarget,
)
from services.incident.incident import (
    ORPHAN_RECOVERED_RESOLUTION_REASON,
    IncidentCase,
    IncidentStatus,
    IncidentStore,
    extract_incident_resolution_reason,
    extract_infrastructure_incident_component,
    extract_infrastructure_incident_referenced_ids,
    is_infrastructure_health_incident,
    is_monitor_agent_finding_incident,
)

_PREV_RUNTIME_MANAGER_URL = os.environ.get("PANTHEON_RUNTIME_MANAGER_URL")
_PREV_RUNTIME_MANAGER_TOKEN = os.environ.get("PANTHEON_RUNTIME_MANAGER_TOKEN")
os.environ.setdefault("PANTHEON_RUNTIME_MANAGER_URL", "http://127.0.0.1:9")
os.environ.setdefault("PANTHEON_RUNTIME_MANAGER_TOKEN", "incident-orphan-test-token")
try:
    import services.incidents.main as incidents_main
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


def _reset_outbox(tmp_outbox_path: Path) -> None:
    if hasattr(outbox_store, "impl") and hasattr(outbox_store.impl, "path"):
        outbox_store.impl.path = tmp_outbox_path
    if tmp_outbox_path.exists():
        tmp_outbox_path.unlink()


@pytest.fixture(autouse=True)
def _reset_incidents_store(monkeypatch, tmp_path):
    class _AcceptAllValidator:
        def validate_incident(self, incident):
            return None

    monkeypatch.setattr("services.incidents.main.reference_validator", _AcceptAllValidator())
    fresh_store = IncidentStore(path=None)
    monkeypatch.setattr("services.incidents.main.store", fresh_store)
    monkeypatch.setattr(sys.modules[__name__], "store", fresh_store)
    outbox_path = tmp_path / "incidents_outbox.json"
    _reset_outbox(outbox_path)
    yield
    _reset_outbox(outbox_path)


client = TestClient(app)


def _infra_payload(
    *,
    incident_id: str,
    service_name: str,
    source_event_id: str,
    created_at: str = "2026-10-22T08:00:00Z",
    tenant_id: str = "default",
    producer: str = "control-plane-bff",
    status: str = "open",
    severity: str = "high",
    detail: str = "HTTP 503",
) -> Dict[str, Any]:
    return {
        "schema_version": "pantheon.infrastructure-incident/1",
        "incident_id": incident_id,
        "tenant_id": tenant_id,
        "producer": producer,
        "source_event_id": source_event_id,
        "created_at": created_at,
        "title": f"BFF downstream degradation: {service_name}",
        "status": status,
        "severity": severity,
        "component": {
            "service_name": service_name,
            "component_kind": "http_service",
            "endpoint": f"http://{service_name}:8080/__health__",
        },
        "telemetry_event_ids": [source_event_id],
        "evidence_summary": (
            f"interval_probe observed 3/3 failures (rate=1.000); "
            f"consecutive_failures=3; detail={detail}"
        ),
    }


def _runtime_incident(
    incident_id: str = "inc-runtime-trading-001",
    *,
    cluster_id: str = "runtime:binding-alpha:rt-001:drawdown",
    binding_id: str = "binding-alpha",
) -> IncidentCase:
    return IncidentCase(
        incident_id=incident_id,
        title="Runtime drawdown breach",
        status="open",
        severity="high",
        created_at="2026-10-22T08:00:00Z",
        binding_id=binding_id,
        deployment_stage="paper",
        deployment_plan_id="plan-alpha",
        capital_pool_id="pool-alpha",
        persona_capital_binding_id="pcb-alpha",
        artifact_id="art-alpha",
        artifact_version="1.0.0",
        runtime_id="rt-001",
        trace_id="trace-runtime-001",
        telemetry_event_ids=["evt-runtime-001"],
        incident_cluster_id=cluster_id,
        evidence_summary="threshold_metric=max_drawdown; observed=0.12",
        lineage_ref="art-alpha@1.0.0",
    )


def test_single_open_infrastructure_incident_per_component_merges_second_open_event():
    """AC1: At most one open infrastructure incident per component; second open merges."""
    first_payload = _infra_payload(
        incident_id="infra-bff-0847594c513bbe71b6aedcd3",
        service_name="governance",
        source_event_id="evt-gov-20261022",
        created_at="2026-10-22T08:00:00Z",
        detail="timeout on 2026-10-22",
    )
    second_payload = _infra_payload(
        incident_id="infra-bff-2b8541b81f8d5796101ad57e",
        service_name="governance",
        source_event_id="evt-gov-20261027",
        created_at="2026-10-27T01:32:00Z",
        detail="connection refused on 2026-10-27",
    )

    r1 = client.post("/api/incidents/consume-infrastructure-health", json=first_payload)
    assert r1.status_code == 201, r1.text
    assert r1.json()["incident_id"] == "infra-bff-0847594c513bbe71b6aedcd3"

    r2 = client.post("/api/incidents/consume-infrastructure-health", json=second_payload)
    assert r2.status_code == 200, r2.text
    merged_body = r2.json()
    assert merged_body["incident_id"] == "infra-bff-0847594c513bbe71b6aedcd3"
    assert merged_body["status"] == "open"
    assert "evt-gov-20261022" in merged_body["telemetry_event_ids"]
    assert "evt-gov-20261027" in merged_body["telemetry_event_ids"]
    assert (
        "merged_incident_id:infra-bff-2b8541b81f8d5796101ad57e"
        in merged_body["telemetry_event_ids"]
    )
    assert (
        "merged_incident_id=infra-bff-2b8541b81f8d5796101ad57e"
        in merged_body["evidence_summary"]
    )

    open_gov = [
        inc
        for inc in store.find_open_incidents()
        if extract_infrastructure_incident_component(inc) == "governance"
    ]
    assert len(open_gov) == 1
    assert open_gov[0].incident_id == "infra-bff-0847594c513bbe71b6aedcd3"
    assert (
        "infra-bff-2b8541b81f8d5796101ad57e"
        in extract_infrastructure_incident_referenced_ids(open_gov[0])
    )


def test_component_recovery_resolves_every_open_incident_including_before_ledger_reset():
    """AC2 & AC4: Recovery resolves all open infrastructure incidents for that component."""
    # Seed pre-fix state where an earlier 2026-10-22 incident lost its ledger mapping
    # and a later 2026-10-27 incident for the same component was also opened.
    first_inc = incidents_main.build_incident_from_infrastructure_health_payload(
        _infra_payload(
            incident_id="infra-bff-0847594c513bbe71b6aedcd3",
            service_name="governance",
            source_event_id="evt-gov-20261022",
            created_at="2026-10-22T08:00:00Z",
        )
    )
    second_inc = incidents_main.build_incident_from_infrastructure_health_payload(
        _infra_payload(
            incident_id="infra-bff-2b8541b81f8d5796101ad57e",
            service_name="governance",
            source_event_id="evt-gov-20261027",
            created_at="2026-10-27T01:32:00Z",
        )
    )
    other_component_inc = incidents_main.build_incident_from_infrastructure_health_payload(
        _infra_payload(
            incident_id="infra-bff-other-unhealthy-001",
            service_name="telemetry",
            source_event_id="evt-tel-20261103",
            created_at="2026-11-03T01:00:00Z",
        )
    )
    store.create_incident(first_inc)
    store.create_incident(second_inc)
    store.create_incident(other_component_inc)

    # Resolve only the later incident ID (what BFF monitor sends after ledger overwrite/reset).
    r = client.post(
        "/api/incidents/infra-bff-2b8541b81f8d5796101ad57e/status",
        json={
            "status": "resolved",
            "resolved_at": "2026-10-27T01:36:00Z",
            "source_event_id": "evt-gov-recovery-20261027",
            "telemetry_event_ids": ["evt-gov-recovery-20261027"],
        },
    )
    assert r.status_code == 200, r.text

    inc_earlier = store.require_incident("infra-bff-0847594c513bbe71b6aedcd3")
    inc_later = store.require_incident("infra-bff-2b8541b81f8d5796101ad57e")
    assert inc_later.status == IncidentStatus.RESOLVED.value
    assert inc_earlier.status == IncidentStatus.RESOLVED.value
    assert (
        extract_incident_resolution_reason(inc_earlier)
        == ORPHAN_RECOVERED_RESOLUTION_REASON
    )
    assert "resolution_reason=orphan_recovered" in (inc_earlier.evidence_summary or "")
    assert "resolution_reason:orphan_recovered" in inc_earlier.telemetry_event_ids

    # Unrelated unhealthy component incident remains open.
    assert (
        store.require_incident("infra-bff-other-unhealthy-001").status
        == IncidentStatus.OPEN.value
    )


def test_hosted_dev_reproduction_end_to_end_within_one_probe_interval(tmp_path):
    """AC3 & AC4: Reproduces the exact hosted dev state across all 6 components.

    - 6 open 2026-10-22 incidents:
      * governance: infra-bff-0847594c513bbe71b6aedcd3
      * capital: infra-bff-d9ca3db4cb8d640ac3b73380
      * deployment: infra-bff-0ddbece9a628d5148155a9c1
      * promotion: infra-bff-a9d35b2ed20265279da456db
      * consultation: infra-bff-f5e425f1f89df533f83cb3c8
      * research-orchestrator: infra-bff-ab79a26787ddde970745d9b6
    - Later incidents in store & BFF SQLite ledger:
      * governance -> infra-bff-2b8541b81f8d5796101ad57e (resolved in store,
        mapping status stuck in 'resolving' in BFF ledger with no active outbox row)
      * capital -> infra-bff-13ec9edd5660b5545f4260fb (resolved in both store and ledger)
    - Within one probe interval of DownstreamHealthMonitor._probe_all(), all 6
      2026-10-22 orphaned incidents are resolved with ``orphan_recovered`` and
      governance's stuck 'resolving' ledger mapping transitions to 'resolved'.
    """
    hosted_orphans = {
        "governance": "infra-bff-0847594c513bbe71b6aedcd3",
        "capital": "infra-bff-d9ca3db4cb8d640ac3b73380",
        "deployment": "infra-bff-0ddbece9a628d5148155a9c1",
        "promotion": "infra-bff-a9d35b2ed20265279da456db",
        "consultation": "infra-bff-f5e425f1f89df533f83cb3c8",
        "research-orchestrator": "infra-bff-ab79a26787ddde970745d9b6",
    }
    for comp, inc_id in hosted_orphans.items():
        inc = incidents_main.build_incident_from_infrastructure_health_payload(
            _infra_payload(
                incident_id=inc_id,
                service_name=comp,
                source_event_id=f"evt-{comp}-20261022",
                created_at="2026-10-22T08:00:00Z",
            )
        )
        store.create_incident(inc)

    # Later incidents for governance and capital that opened and resolved after 10/22
    gov_later = incidents_main.build_incident_from_infrastructure_health_payload(
        _infra_payload(
            incident_id="infra-bff-2b8541b81f8d5796101ad57e",
            service_name="governance",
            source_event_id="evt-governance-20261027",
            created_at="2026-10-27T01:32:00Z",
        )
    )
    store.create_incident(gov_later)
    store.update_incident_status(
        "infra-bff-2b8541b81f8d5796101ad57e",
        "resolved",
        resolved_at="2026-10-27T01:36:00Z",
    )

    cap_later = incidents_main.build_incident_from_infrastructure_health_payload(
        _infra_payload(
            incident_id="infra-bff-13ec9edd5660b5545f4260fb",
            service_name="capital",
            source_event_id="evt-capital-20261102",
            created_at="2026-11-02T04:00:00Z",
        )
    )
    store.create_incident(cap_later)
    store.update_incident_status(
        "infra-bff-13ec9edd5660b5545f4260fb",
        "resolved",
        resolved_at="2026-11-02T04:05:00Z",
    )

    # Set up BFF DownstreamHealthMonitor with the exact hosted SQLite incident_mappings state
    monitor = DownstreamHealthMonitor(
        telemetry_url="",
        incidents_url="http://incidents.local:8090",
        tenant_id="default",
        probe_interval_seconds=60,
        failure_threshold=3,
        state_path=str(tmp_path / "bff-health-state.sqlite3"),
    )
    with monitor._store._connect() as conn:
        conn.execute(
            """
            INSERT INTO incident_mappings(
                target_name, incident_id, status, opening_event_id,
                create_delivery_id, last_event_id, updated_at
            ) VALUES
            ('governance', 'infra-bff-2b8541b81f8d5796101ad57e', 'resolving',
             'evt-governance-20261027', 'incident-open:evt-governance-20261027',
             'evt-governance-resolve-20261027', '2026-10-27T01:36:00Z'),
            ('capital', 'infra-bff-13ec9edd5660b5545f4260fb', 'resolved',
             'evt-capital-20261102', 'incident-open:evt-capital-20261102',
             'evt-capital-resolve-20261102', '2026-11-02T04:05:00Z')
            """
        )

    monitor._resolve_target_registry = lambda: {  # type: ignore[assignment]
        comp: DownstreamTarget(
            name=comp,
            base_url=f"http://{comp}:8080",
            health_path="/__health__",
        )
        for comp in hosted_orphans
    }

    def route_to_incidents_app(url: str, body: Dict[str, Any], timeout: float):
        path = "/" + url.split("://", 1)[1].split("/", 1)[1]
        resp = client.post(path, json=body)
        return 200 <= resp.status_code < 300, resp.status_code

    with (
        patch(
            "services.control_plane.bff.downstream_health_monitor._probe_http",
            return_value=(True, 200, ""),
        ),
        patch(
            "services.control_plane.bff.downstream_health_monitor._post_json",
            side_effect=route_to_incidents_app,
        ),
    ):
        asyncio.run(monitor._probe_all())

    # All 6 2026-10-22 orphaned incidents are now resolved with orphan_recovered
    for comp, inc_id in hosted_orphans.items():
        resolved_inc = store.require_incident(inc_id)
        assert resolved_inc.status == IncidentStatus.RESOLVED.value, (
            f"Expected {comp} incident {inc_id} to be resolved"
        )
        assert (
            extract_incident_resolution_reason(resolved_inc)
            == ORPHAN_RECOVERED_RESOLUTION_REASON
        )
        assert "resolution_reason=orphan_recovered" in (resolved_inc.evidence_summary or "")
        assert "resolution_reason:orphan_recovered" in resolved_inc.telemetry_event_ids

    # Both the earlier and later incidents for governance and capital are resolved
    assert (
        store.require_incident("infra-bff-2b8541b81f8d5796101ad57e").status
        == IncidentStatus.RESOLVED.value
    )
    assert (
        store.require_incident("infra-bff-13ec9edd5660b5545f4260fb").status
        == IncidentStatus.RESOLVED.value
    )
    # And the stuck 'resolving' mapping in BFF SQLite ledger is now 'resolved'
    assert monitor._store.get_incident("governance")["status"] == "resolved"


def test_active_ledger_referenced_incident_is_not_prematurely_orphan_resolved(tmp_path):
    """An open incident still actively referenced as 'open' in the BFF ledger is kept until recovery."""
    inc_active = incidents_main.build_incident_from_infrastructure_health_payload(
        _infra_payload(
            incident_id="infra-bff-active-ref-001",
            service_name="governance",
            source_event_id="evt-gov-active-001",
        )
    )
    inc_orphan = incidents_main.build_incident_from_infrastructure_health_payload(
        _infra_payload(
            incident_id="infra-bff-orphan-unreferenced-001",
            service_name="governance",
            source_event_id="evt-gov-orphan-001",
        )
    )
    store.create_incident(inc_active)
    store.create_incident(inc_orphan)

    resp = client.post(
        "/api/incidents/reconcile-infrastructure-health",
        json={
            "healthy_components": ["governance"],
            "referenced_incident_ids": ["infra-bff-active-ref-001"],
            "observed_at": "2026-11-03T02:00:00Z",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["resolved_incident_ids"] == ["infra-bff-orphan-unreferenced-001"]
    assert (
        store.require_incident("infra-bff-orphan-unreferenced-001").status
        == IncidentStatus.RESOLVED.value
    )
    assert (
        store.require_incident("infra-bff-active-ref-001").status
        == IncidentStatus.OPEN.value
    )


def test_monitor_agent_runtime_drift_and_deployment_incidents_are_unaffected():
    """AC5: Monitor-agent finding, runtime, drift, and deployment incidents are untouched."""
    # 1. Monitor-agent finding incident
    r_agent = client.post(
        "/api/incidents/consume-agent-finding",
        json={
            "fingerprint": "telemetry-lag-finding",
            "snapshot_ref": "monitor-snapshot-20261103T010000Z-112233445566",
            "title": "Telemetry lag detected",
            "rationale": "Lag exceeded threshold",
            "observed_at": "2026-11-03T01:00:00Z",
        },
    )
    assert r_agent.status_code == 201, r_agent.text
    agent_id = r_agent.json()["incident_id"]
    agent_inc = store.require_incident(agent_id)
    assert is_monitor_agent_finding_incident(agent_inc) is True
    assert is_infrastructure_health_incident(agent_inc) is False

    # 2. Runtime incident
    runtime_inc = _runtime_incident("inc-runtime-001")
    store.create_incident(runtime_inc)
    assert is_infrastructure_health_incident(runtime_inc) is False

    # 3. Drift incident
    drift_inc = _runtime_incident(
        "inc-drift-001",
        cluster_id="drift:binding-alpha:rt-001:position_drift",
    )
    store.create_incident(drift_inc)
    assert is_infrastructure_health_incident(drift_inc) is False

    # 4. Deployment incident
    deploy_inc = _runtime_incident(
        "inc-deploy-001",
        cluster_id="deployment:plan-alpha:rollout_failed",
    )
    store.create_incident(deploy_inc)
    assert is_infrastructure_health_incident(deploy_inc) is False

    # Run orphan reconciliation across all component names including 'deployment'
    reconcile_resp = client.post(
        "/api/incidents/reconcile-infrastructure-health",
        json={
            "healthy_components": [
                "governance",
                "deployment",
                "telemetry-lag-finding",
                "agent-finding-telemetry-lag-finding",
                "binding-alpha",
            ],
            "referenced_incident_ids": [],
            "observed_at": "2026-11-03T02:00:00Z",
        },
    )
    assert reconcile_resp.status_code == 200, reconcile_resp.text
    assert reconcile_resp.json()["resolved_count"] == 0

    for protected_id in (agent_id, "inc-runtime-001", "inc-drift-001", "inc-deploy-001"):
        assert store.require_incident(protected_id).status == IncidentStatus.OPEN.value
