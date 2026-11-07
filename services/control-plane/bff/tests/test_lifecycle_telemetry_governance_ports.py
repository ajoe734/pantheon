"""Tests for Lifecycle, Telemetry, Incident, Governance, and Lineage Typed Domain Ports.

Verifies:
- ACG-02-015: Lifecycle, Telemetry, Incident, Governance, Lineage reads are converged to domain ports.
- Protocol conformance for all 5 domain reader interfaces.
- Exact identity, filtering, sorting, and freshness semantics.
- Degradation and source fallback behavior (e.g. Telemetry event store vs summary fallback).
- In-memory / test-fake usability without ReadSurfaceStore god-class dependency.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
import sys
from typing import Any, Dict, List

import pytest


from ports.lifecycle_telemetry_governance import (
    IncidentReaderPort,
    LifecycleReaderPort,
    GovernanceReaderPort,
    LineageReaderPort,
    TelemetryReaderPort,
    LifecycleTelemetryGovernancePort,
    DomainIncidentPort,
    DomainLifecyclePort,
    DomainGovernancePort,
    DomainLineagePort,
    InMemoryLineagePort,
    DomainTelemetryPort,
    CompositeLifecycleTelemetryGovernancePort,
    InMemoryLifecycleTelemetryGovernancePort,
    create_lifecycle_telemetry_governance_port,
    create_in_memory_lifecycle_telemetry_governance_port,
    _parse_rfc3339,
    _is_fixture_pack_record,
)


# =====================================================================
# Fixtures
# =====================================================================

@pytest.fixture
def sample_incidents_data() -> Dict[str, Dict[str, Any]]:
    return {
        "inc-20260410-001": {
            "incident_id": "inc-20260410-001",
            "id": "inc-20260410-001",
            "title": "Historical Anchor Incident",
            "severity": "P1",
            "status": "resolved",
            "capital_pool_id": "pool-alpha",
            "created_at": "2026-04-10T10:00:00Z",
        },
        "inc-20260501-002": {
            "incident_id": "inc-20260501-002",
            "id": "inc-20260501-002",
            "title": "Later Incident",
            "severity": "P2",
            "status": "active",
            "capital_pool_id": "pool-alpha",
            "created_at": "2026-05-01T12:00:00Z",
        },
        "inc-20260505-003": {
            "incident_id": "inc-20260505-003",
            "id": "inc-20260505-003",
            "title": "Newest Incident",
            "severity": "P3",
            "status": "mitigated",
            "capital_pool_id": "pool-beta",
            "created_at": "2026-05-05T08:00:00Z",
        },
    }


@pytest.fixture
def sample_postmortems_data() -> Dict[str, Dict[str, Any]]:
    return {
        "pm-001": {
            "postmortem_id": "pm-001",
            "report_id": "pm-001",
            "id": "pm-001",
            "incident_id": "inc-20260410-001",
            "title": "Postmortem draft: Historical Anchor Incident",
            "summary": "Root cause analysis for anchor incident",
            "status": "draft",
            "created_at": "2026-04-12T00:00:00Z",
            "binding_id": "bind-alpha-001",
            "deployment_stage": "paper",
            "deployment_plan_id": "plan-alpha-001",
            "capital_pool_id": "pool-alpha",
            "persona_capital_binding_id": "pcb-alpha-001",
            "artifact_id": "art-001",
            "artifact_version": "v1.0.0",
            "runtime_id": "rt-001",
            "trace_id": "trace-001",
            "telemetry_event_ids": ["tel-001"],
            "incident_evidence_summary": "Root cause analysis for anchor incident",
            "lineage_ref": "art-001@v1.0.0",
            "incident_cluster_id": None,
            "root_cause": "Root cause analysis for anchor incident",
            "contributing_factors": ["downstream latency"],
            "timeline": [{"ts": "2026-04-12T00:00:00Z", "event": "drafted"}],
            "action_items": ["tighten timeout budget"],
            "author_ids": ["system"],
        },
        "pm-002": {
            "postmortem_id": "pm-002",
            "report_id": "pm-002",
            "id": "pm-002",
            "incident_id": "inc-20260501-002",
            "title": "Postmortem draft: Later Incident",
            "summary": "Root cause analysis for later incident",
            "status": "draft",
            "created_at": "2026-05-02T00:00:00Z",
            "binding_id": "bind-alpha-002",
            "deployment_stage": "paper",
            "deployment_plan_id": "plan-alpha-002",
            "capital_pool_id": "pool-alpha",
            "persona_capital_binding_id": "pcb-alpha-002",
            "artifact_id": "art-002",
            "artifact_version": "v1.1.0",
            "runtime_id": "rt-002",
            "trace_id": "trace-002",
            "telemetry_event_ids": ["tel-002"],
            "incident_evidence_summary": "Root cause analysis for later incident",
            "lineage_ref": "art-002@v1.1.0",
            "incident_cluster_id": None,
            "root_cause": "Root cause analysis for later incident",
            "contributing_factors": ["feed jitter"],
            "timeline": [{"ts": "2026-05-02T00:00:00Z", "event": "drafted"}],
            "action_items": ["add circuit breaker"],
            "author_ids": ["system"],
        },
    }


@pytest.fixture
def sample_governance_data() -> Dict[str, Any]:
    return {
        "evolution_decisions": {
            "dec-001": {
                "decision_id": "dec-001",
                "action_type": "rollback",
                "risk_level": "high",
                "status": "approved",
                "linked_incident_id": "inc-20260410-001",
                "created_at": "2026-04-11T09:00:00Z",
                "target_id": "art-001",
            },
            "dec-002": {
                "decision_id": "dec-002",
                "action_type": "parameter_update",
                "risk_level": "low",
                "status": "pending",
                "linked_incident_id": "inc-20260501-002",
                "created_at": "2026-05-02T10:00:00Z",
                "target_id": "art-002",
            },
        },
        "freeze_orders": {
            "fo-001": {
                "freeze_order_id": "fo-001",
                "scope": "pool-alpha",
                "status": "active",
                "created_at": "2026-05-01T13:00:00Z",
            },
            "fo-002": {
                "freeze_order_id": "fo-002",
                "scope": "pool-beta",
                "status": "released",
                "created_at": "2026-05-02T14:00:00Z",
            },
        },
        "all_rollbacks": [
            {
                "rollback_id": "rb-001",
                "runtime_id": "rt-001",
                "action_type": "code_revert",
                "initiated_at": "2026-04-11T10:00:00Z",
            },
            {
                "rollback_id": "rb-002",
                "runtime_id": "rt-002",
                "action_type": "config_restore",
                "initiated_at": "2026-05-01T15:00:00Z",
            },
        ],
        "rollback_reviews": {
            "rb-001": {
                "rollback_id": "rb-001",
                "reviewer": "Alice",
                "decision": "approved",
            }
        },
        "governance_audit_events": [
            {
                "id": "aud-001",
                "actor": "operator_alice",
                "action_type": "freeze",
                "target_type": "capital_pool",
                "timestamp": "2026-05-01T13:00:00Z",
            },
            {
                "id": "aud-002",
                "actor": "operator_bob",
                "action_type": "unfreeze",
                "target_type": "capital_pool",
                "timestamp": "2026-05-02T14:00:00Z",
            },
            {
                "id": "aud-fixture",
                "actor": "system",
                "action_type": "seed",
                "target_type": "fixture",
                "timestamp": "2026-05-01T00:00:00Z",
                "_fixture_pack": True,
            },
        ],
    }


@pytest.fixture
def sample_lineage_data() -> Dict[str, Any]:
    return {
        "lineage_edges": [
            {
                "id": "edge-001",
                "from_artifact_id": "art-parent",
                "from_artifact_version": "v1.0.0",
                "from_artifact_type": "strategy",
                "to_artifact_id": "art-child",
                "to_artifact_version": "v1.1.0",
                "to_artifact_type": "strategy",
                "created_at": "2026-05-01T00:00:00Z",
            },
            {
                "id": "edge-002",
                "from_artifact_id": "art-parent",
                "from_artifact_version": "v1.0.0",
                "from_artifact_type": "strategy",
                "to_artifact_id": "art-sibling",
                "to_artifact_version": "v1.0.1",
                "to_artifact_type": "strategy",
                "created_at": "2026-05-02T00:00:00Z",
            },
        ],
        "inspiration_graphs": {
            "art-child": {
                "artifact_id": "art-child",
                "inspiration_edges": [
                    {
                        "source_artifact_id": "art-parent",
                        "relationship_type": "mutation",
                        "influence_weight": 0.8543,
                    },
                    {
                        "source_artifact_id": "art-ref",
                        "relationship_type": "cross_reference",
                        "influence_weight": 1.5,
                    },
                ],
                "strategy_tags": ["momentum ", " intraday"],
                "meta": {
                    "surfaces": {"inspiration": "fresh"},
                },
                "page_info": {"next_page_token": "token-123"},
            }
        },
        "artifact_registry_entries": [
            {"artifact_id": "art-parent", "artifact_version": "v1.0.0", "artifact_type": "strategy"},
            {"artifact_id": "art-child", "artifact_version": "v1.1.0", "artifact_type": "strategy"},
        ],
    }


@pytest.fixture
def sample_telemetry_data() -> Dict[str, Any]:
    return {
        "telemetry_events": [
            {
                "event_id": "evt-001",
                "runtime_id": "rt-001",
                "pool_id": "pool-alpha",
                "artifact_id": "art-child",
                "type": "trade_execution",
                "timestamp": "2026-05-01T10:00:00Z",
                "metrics": {"fill_rate": 0.99},
            },
            {
                "event_id": "evt-002",
                "runtime_id": "rt-002",
                "pool_id": "pool-beta",
                "artifact_id": "art-parent",
                "type": "heartbeat",
                "timestamp": "2026-05-02T11:00:00Z",
                "metrics": {"fill_rate": 1.0},
            },
        ],
        "telemetry_summaries": [
            {
                "runtime_id": "rt-001",
                "collected_at": "2026-05-01T12:00:00Z",
                "pnl": 1500.0,
                "drawdown": 0.05,
                "sharpe_ratio": 2.1,
                "total_trades": 45,
                "fill_rate": 0.98,
                "avg_slippage_bps": 1.2,
            }
        ],
        "telemetry_performance": {
            "art-child": {
                "artifact_id": "art-child",
                "win_rate": 0.65,
                "sharpe": 2.2,
            }
        },
        "paper_live_drift_reports": [
            {
                "runtime_id": "rt-001",
                "drift_score": 0.02,
                "status": "acceptable",
            }
        ],
    }


# =====================================================================
# Tests: Protocol Conformance
# =====================================================================

def test_protocol_conformance():
    """Verify that concrete and in-memory ports conform to runtime_checkable protocols."""
    in_memory = create_in_memory_lifecycle_telemetry_governance_port()

    assert isinstance(in_memory, IncidentReaderPort)
    assert isinstance(in_memory, LifecycleReaderPort)
    assert isinstance(in_memory, GovernanceReaderPort)
    assert isinstance(in_memory, LineageReaderPort)
    assert isinstance(in_memory, TelemetryReaderPort)
    assert isinstance(in_memory, LifecycleTelemetryGovernancePort)


# =====================================================================
# Tests: Incident and Postmortem Reads
# =====================================================================

def test_incident_port_anchor_sort(sample_incidents_data):
    """IN-01: Verify anchor incident (inc-20260410-001) is pinned first despite date."""
    port = DomainIncidentPort(incidents=sample_incidents_data)
    incidents = port.list_incidents()

    assert len(incidents) == 3
    # Anchor must be first
    assert incidents[0]["incident_id"] == "inc-20260410-001"
    # Remainder sorted descending by created_at: inc-20260505-003 then inc-20260501-002
    assert incidents[1]["incident_id"] == "inc-20260505-003"
    assert incidents[2]["incident_id"] == "inc-20260501-002"


def test_incident_port_filtering(sample_incidents_data):
    """IN-01: Verify filtering by status (comma-separated), severity, and pool."""
    port = DomainIncidentPort(incidents=sample_incidents_data)

    # Comma-separated status
    active_or_mitigated = port.list_incidents(status="active, mitigated")
    assert len(active_or_mitigated) == 2
    assert {i["incident_id"] for i in active_or_mitigated} == {"inc-20260501-002", "inc-20260505-003"}

    # Severity
    p1_only = port.list_incidents(severity="P1")
    assert len(p1_only) == 1
    assert p1_only[0]["incident_id"] == "inc-20260410-001"

    # Pool ID
    pool_beta = port.list_incidents(affected_pool_id="pool-beta")
    assert len(pool_beta) == 1
    assert pool_beta[0]["incident_id"] == "inc-20260505-003"


def test_incident_port_detail_and_correlations(sample_incidents_data, sample_governance_data):
    """IN-02: Verify incident detail, evolution decisions by incident, and rollbacks by incident."""
    port = DomainIncidentPort(
        incidents=sample_incidents_data,
        evolution_decisions=sample_governance_data["evolution_decisions"],
        rollbacks_by_incident={
            "inc-20260410-001": [{"rollback_id": "rb-001", "status": "executed"}]
        },
    )

    inc = port.get_incident("inc-20260410-001")
    assert inc is not None
    assert inc["title"] == "Historical Anchor Incident"

    assert port.get_incident("inc-missing") is None

    # Evolution decisions correlated by incident
    decisions = port.get_evolution_decisions_by_incident("inc-20260410-001")
    assert len(decisions) == 1
    assert decisions[0]["decision_id"] == "dec-001"

    # Rollbacks correlated by incident
    rollbacks = port.get_rollbacks_by_incident("inc-20260410-001")
    assert len(rollbacks) == 1
    assert rollbacks[0]["rollback_id"] == "rb-001"

    assert port.get_rollbacks_by_incident("inc-missing") == []


def test_postmortem_port_reads(sample_incidents_data, sample_postmortems_data, tmp_path, monkeypatch):
    """PM-01 / PM-02: Verify postmortem listing, detail, and incident correlation via the owner API."""
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import threading
    from fastapi.testclient import TestClient
    from services.incident.incident import IncidentStore, Postmortem
    monkeypatch.setenv("PANTHEON_RUNTIME_MANAGER_URL", "http://127.0.0.1:8012")
    import services.postmortems.main as pm_main

    store = IncidentStore(tmp_path / "incidents.json")
    for pm_id, raw in sample_postmortems_data.items():
        store._postmortems[pm_id] = Postmortem.from_dict(raw)

    old_store = pm_main.store
    pm_main.store = store
    owner_client = TestClient(pm_main.app)

    class _OwnerBridgeHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def do_GET(self) -> None:
            resp = owner_client.get(self.path)
            self.send_response(resp.status_code)
            self.send_header("Content-Type", "application/json")
            if "x-total-count" in resp.headers:
                self.send_header("X-Total-Count", resp.headers["x-total-count"])
            self.end_headers()
            self.wfile.write(resp.content)

    httpd = HTTPServer(("127.0.0.1", 0), _OwnerBridgeHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        port = DomainIncidentPort(
            incidents=sample_incidents_data,
            postmortems_api_url=f"http://127.0.0.1:{httpd.server_port}",
        )

        all_pm = port.list_postmortems()
        assert len(all_pm) == 2

        pm1 = port.get_postmortem("pm-001")
        assert pm1 is not None
        assert pm1["postmortem_id"] == "pm-001"

        correlated = port.get_postmortem_by_incident("inc-20260410-001")
        assert correlated is not None
        assert correlated["postmortem_id"] == "pm-001"

        assert port.get_postmortem_by_incident("inc-nonexistent") is None
    finally:
        httpd.shutdown()
        httpd.server_close()
        pm_main.store = old_store


# =====================================================================
# Tests: Lifecycle, Loop Runs, and Kill Switch
# =====================================================================

def test_lifecycle_loop_runs():
    """Verify loop runs listing."""
    port = DomainLifecyclePort(
        loop_runs={
            "lr-001": {
                "loop_run_id": "lr-001",
                "loop_id": "loop-execution",
                "status": "completed",
            }
        },
    )

    avail, runs = port.list_loop_runs()
    assert avail is True
    assert len(runs) == 1
    assert runs[0]["loop_run_id"] == "lr-001"

    avail, run = port.get_loop_run("lr-001")
    assert avail is True
    assert run["loop_run_id"] == "lr-001"


def test_lifecycle_fallback_from_incidents(sample_incidents_data):
    """Verify loop runs derivation from incidents when primary store is None."""
    port = DomainLifecyclePort(
        loop_runs=None,
        incidents=sample_incidents_data,
    )

    avail, runs = port.list_loop_runs()
    assert avail is True
    assert len(runs) == 3



def test_lifecycle_loop_health_and_projection_reader():
    """Verify loop health records and projection reader override."""
    sentinel_obj = object()
    port = DomainLifecyclePort(
        loop_health_records={
            "loop-01": {"loop_id": "loop-01", "status": "healthy"}
        },
        projection_reader_override=sentinel_obj,
    )

    avail, records = port.list_loop_health_records()
    assert avail is True
    assert len(records) == 1
    assert records[0]["loop_id"] == "loop-01"

    avail, rec = port.get_loop_health_record("loop-01")
    assert avail is True
    assert rec["status"] == "healthy"

    assert port.trade_journey_projection_reader() is sentinel_obj


def test_kill_switch_status_normalization():
    """Verify kill switch status normalization into armed, triggered, cooling_down."""
    port_triggered = DomainLifecyclePort(
        kill_switch={
            "active": True,
            "status": "custom_active",
            "active_freeze_orders": ["fo-001", {"command_id": "freeze-all"}, {"id": "cmd-002"}],
            "last_checked_at": "2026-05-01T12:00:00Z",
        }
    )
    status_t = port_triggered.get_kill_switch_status()
    assert status_t["active"] is True
    assert status_t["status"] == "triggered"
    assert "fo-001" in status_t["active_commands"]
    assert "freeze-all" in status_t["active_commands"]
    assert "cmd-002" in status_t["active_commands"]

    port_cooling = DomainLifecyclePort(
        kill_switch={
            "active": False,
            "safe_mode_status": "cooling_down",
        }
    )
    status_c = port_cooling.get_kill_switch_status()
    assert status_c["status"] == "cooling_down"

    port_armed = DomainLifecyclePort(kill_switch={})
    status_a = port_armed.get_kill_switch_status()
    assert status_a["status"] == "armed"
    assert status_a["active"] is False


# =====================================================================
# Tests: Governance and Evolution Reads
# =====================================================================

def test_governance_evolution_decisions(sample_governance_data):
    """EV-01 / EV-02: Verify evolution decisions projection and filtering."""
    port = DomainGovernancePort(
        evolution_decisions=sample_governance_data["evolution_decisions"],
        freeze_orders=sample_governance_data["freeze_orders"],
        all_rollbacks=sample_governance_data["all_rollbacks"],
        rollback_reviews=sample_governance_data["rollback_reviews"],
        governance_audit_events=sample_governance_data["governance_audit_events"],
    )

    decisions = port.list_evolution_decisions(action_type="rollback")
    assert len(decisions) == 1
    assert decisions[0]["decision_id"] == "dec-001"
    assert decisions[0]["incident_ref"] == "inc-20260410-001"
    assert decisions[0]["artifact_id"] == "art-001"

    single = port.get_evolution_decision_by_id("dec-002")
    assert single is not None
    assert single["action_type"] == "parameter_update"
    assert port.get_evolution_decision("dec-002") == single


def test_governance_freeze_and_rollbacks(sample_governance_data):
    """Verify freeze orders, rollbacks, and rollback reviews."""
    port = DomainGovernancePort(
        freeze_orders=sample_governance_data["freeze_orders"],
        all_rollbacks=sample_governance_data["all_rollbacks"],
        rollback_reviews=sample_governance_data["rollback_reviews"],
    )

    active_fo = port.list_freeze_orders(status="active")
    assert len(active_fo) == 1
    assert active_fo[0]["freeze_order_id"] == "fo-001"

    scope_fo = port.list_freeze_orders(scope="pool-beta")
    assert len(scope_fo) == 1
    assert scope_fo[0]["freeze_order_id"] == "fo-002"

    rollbacks = port.list_all_rollbacks(runtime_id="rt-001")
    assert len(rollbacks) == 1
    assert rollbacks[0]["rollback_id"] == "rb-001"

    review = port.get_rollback_review("rb-001")
    assert review is not None
    assert review["reviewer"] == "Alice"

    assert port.get_rollback_review(None) is None
    assert port.get_rollback_review("rb-nonexistent") is None


def test_governance_audit_events_filtering(sample_governance_data):
    """AUD-01: Verify audit event filtering by actor, action types, RFC3339 time range, fixture pack."""
    port = DomainGovernancePort(
        governance_audit_events=sample_governance_data["governance_audit_events"]
    )

    # Actor filter
    alice_events = port.list_governance_audit_events(actor="operator_alice")
    assert len(alice_events) == 1
    assert alice_events[0]["id"] == "aud-001"

    # Action types filter
    unfreeze_events = port.list_governance_audit_events(action_types=["unfreeze"])
    assert len(unfreeze_events) == 1
    assert unfreeze_events[0]["id"] == "aud-002"

    # Target type filter
    cp_events = port.list_governance_audit_events(target_type="capital_pool")
    assert len(cp_events) == 2

    # Time range filter
    from_dt = datetime(2026, 5, 2, 0, 0, 0, tzinfo=timezone.utc)
    filtered_time = port.list_governance_audit_events(from_ts=from_dt)
    assert len(filtered_time) == 1
    assert filtered_time[0]["id"] == "aud-002"

    to_dt = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    filtered_to = port.list_governance_audit_events(to_ts=to_dt, include_fixture_pack=True)
    assert len(filtered_to) == 1
    assert filtered_to[0]["id"] == "aud-fixture"

    # Fixture pack exclusion
    no_fixtures = port.list_governance_audit_events(include_fixture_pack=False)
    assert len(no_fixtures) == 2
    assert all(e["id"] != "aud-fixture" for e in no_fixtures)


# =====================================================================
# Tests: Lineage and Inspiration Reads
# =====================================================================

def test_lineage_edges_and_records(sample_lineage_data):
    """LN-01 / LN-02: Verify lineage edges and aggregate lineage records."""
    port = InMemoryLineagePort(
        lineage_edges=sample_lineage_data["lineage_edges"],
        inspiration_graphs=sample_lineage_data["inspiration_graphs"],
        artifact_registry_entries=sample_lineage_data["artifact_registry_entries"],
    )

    edges = port.list_lineage_edges(artifact_id="art-parent")
    assert len(edges) == 2

    records = port.list_lineage_records(artifact_id="art-parent")
    assert len(records) == 1
    assert records[0]["artifact_id"] == "art-parent"
    assert records[0]["edge_count"] == 2
    assert records[0]["last_edge_at"] == "2026-05-02T00:00:00Z"

    all_records = port.list_lineage_records()
    assert len(all_records) == 3

    assert port.get_lineage_edge("edge-001") is not None
    assert port.get_lineage_edge("nonexistent") is None

    graph = port.get_lineage_graph(root_id="art-parent")
    assert len(graph) == 2

    nodes = port.get_lineage_graph_nodes(graph)
    assert len(nodes) >= 2
    node_ids = {n["artifact_id"] for n in nodes}
    assert "art-parent" in node_ids
    assert "art-child" in node_ids

    assert port.artifact_exists("art-parent") is True
    assert port.artifact_exists("non-existent") is False


def test_lineage_inspiration_graph_projection(sample_lineage_data):
    """LN-03: Verify inspiration graph projection, weight clamping [0.0, 1.0], and metadata."""
    port = InMemoryLineagePort(
        inspiration_graphs=sample_lineage_data["inspiration_graphs"]
    )

    graph = port.get_inspiration_graph("art-child")
    assert graph is not None
    assert graph["artifact_id"] == "art-child"
    assert graph["strategy_tags"] == ["momentum", "intraday"]

    edges = graph["inspiration_edges"]
    assert len(edges) == 2
    # First edge rounded to 3 decimal places
    assert edges[0]["influence_weight"] == 0.854
    # Second edge clamped to 1.0
    assert edges[1]["influence_weight"] == 1.0

    assert graph["page_info"]["next_page_token"] == "token-123"

    assert port.get_inspiration_graph("art-missing") is None


# =====================================================================
# Tests: Telemetry and Drift Reads
# =====================================================================

def test_telemetry_events_with_authoritative_source(sample_telemetry_data):
    """TL-01: Verify authoritative telemetry events reading and filtering."""
    port = DomainTelemetryPort(
        telemetry_events=sample_telemetry_data["telemetry_events"],
        telemetry_summaries=sample_telemetry_data["telemetry_summaries"],
        telemetry_performance=sample_telemetry_data["telemetry_performance"],
        paper_live_drift_reports=sample_telemetry_data["paper_live_drift_reports"],
    )

    source, events = port.list_telemetry_events_with_source(pool_id="pool-alpha")
    assert source == "telemetry_events"
    assert len(events) == 1
    assert events[0]["runtime_id"] == "rt-001"

    # Artifact filtering
    events_art = port.list_telemetry_events(artifact_id="art-child")
    assert len(events_art) == 1
    assert events_art[0]["event_id"] == "evt-001"


def test_telemetry_summary_fallback():
    """TL-01 degradation: Verify summary projection events fallback when event store is empty."""
    port = DomainTelemetryPort(
        telemetry_events=[],
        telemetry_summaries=[
            {
                "runtime_id": "rt-001",
                "collected_at": "2026-05-01T12:00:00Z",
                "pnl": 1200.0,
                "drawdown": 0.04,
                "sharpe_ratio": 2.0,
                "total_trades": 30,
                "fill_rate": 0.95,
                "avg_slippage_bps": 1.5,
            }
        ],
    )

    source, events = port.list_telemetry_events_with_source()
    assert source == "telemetry_summary_fallback"
    assert len(events) == 1
    assert events[0]["id"] == "tl-evt-rt-001"
    assert events[0]["type"] == "telemetry_snapshot"
    assert events[0]["metrics"]["sharpe_ratio"] == 2.0


def test_telemetry_empty_missing():
    """Verify missing source when both events and summaries are empty."""
    port = DomainTelemetryPort(telemetry_events=[], telemetry_summaries=[])
    source, events = port.list_telemetry_events_with_source()
    assert source == "missing"
    assert events == []


def test_telemetry_performance_and_drift(sample_telemetry_data):
    """TL-03 / DR-01: Verify performance metrics and paper live drift reports."""
    port = DomainTelemetryPort(
        telemetry_performance=sample_telemetry_data["telemetry_performance"],
        paper_live_drift_reports=sample_telemetry_data["paper_live_drift_reports"],
    )

    perf = port.get_telemetry_performance("art-child")
    assert perf is not None
    assert perf["win_rate"] == 0.65

    drift = port.get_paper_live_drift_report("rt-001")
    assert drift is not None
    assert drift["drift_score"] == 0.02

    all_drift = port.list_paper_live_drift_reports()
    assert len(all_drift) == 1

    assert port.get_paper_live_drift_report(None) is None
    assert port.get_paper_live_drift_report("rt-missing") is None


# =====================================================================
# Tests: Composite and In-Memory Ports
# =====================================================================

def test_composite_lifecycle_telemetry_governance_port(
    sample_incidents_data,
    sample_postmortems_data,
    sample_governance_data,
    sample_lineage_data,
    sample_telemetry_data,
):
    """Verify combined port bundles all 5 domains cleanly."""
    port = create_in_memory_lifecycle_telemetry_governance_port(
        incidents=sample_incidents_data,
        postmortems=sample_postmortems_data,
        evolution_decisions=sample_governance_data["evolution_decisions"],
        freeze_orders=sample_governance_data["freeze_orders"],
        all_rollbacks=sample_governance_data["all_rollbacks"],
        rollback_reviews=sample_governance_data["rollback_reviews"],
        governance_audit_events=sample_governance_data["governance_audit_events"],
        lineage_edges=sample_lineage_data["lineage_edges"],
        inspiration_graphs=sample_lineage_data["inspiration_graphs"],
        artifact_registry_entries=sample_lineage_data["artifact_registry_entries"],
        telemetry_events=sample_telemetry_data["telemetry_events"],
        telemetry_summaries=sample_telemetry_data["telemetry_summaries"],
        telemetry_performance=sample_telemetry_data["telemetry_performance"],
        paper_live_drift_reports=sample_telemetry_data["paper_live_drift_reports"],
    )

    # Test each domain via composite port
    assert len(port.list_incidents()) == 3
    assert len(port.list_postmortems()) == 2
    assert len(port.list_evolution_decisions()) == 2
    assert len(port.list_lineage_edges()) == 2
    assert len(port.list_telemetry_events()) == 2
    assert port.get_kill_switch_status()["status"] == "armed"
    assert len(port.list_telemetry_summaries()) == 1


def test_factory_function_with_individual_ports():
    """Verify create_lifecycle_telemetry_governance_port factory function."""
    inc_port = DomainIncidentPort()
    life_port = DomainLifecyclePort()
    gov_port = DomainGovernancePort()
    lin_port = InMemoryLineagePort()
    tel_port = DomainTelemetryPort()

    composite = create_lifecycle_telemetry_governance_port(
        incident_port=inc_port,
        lifecycle_port=life_port,
        governance_port=gov_port,
        lineage_port=lin_port,
        telemetry_port=tel_port,
    )

    assert composite.incidents is inc_port
    assert composite.lifecycle is life_port
    assert composite.governance is gov_port
    assert composite.lineage is lin_port
    assert composite.telemetry is tel_port


# =====================================================================
# Tests: Helpers
# =====================================================================

def test_helpers():
    """Verify internal parsing and fixture detection helpers."""
    assert _parse_rfc3339("2026-05-01T12:00:00Z") is not None
    assert _parse_rfc3339("2026-05-01T12:00:00+00:00") is not None
    assert _parse_rfc3339("invalid") is None
    assert _parse_rfc3339(None) is None

    assert _is_fixture_pack_record({"_fixture_pack": True}) is True
    assert _is_fixture_pack_record({"meta": {"fixture_pack": True}}) is True
    assert _is_fixture_pack_record({"normal": "record"}) is False
    assert _is_fixture_pack_record(None) is False


# ---------------------------------------------------------------------------
# Journal freeze / rollback reads from the canonical Governance owner
# ---------------------------------------------------------------------------

class _GovOwner:
    """Fake Governance owner behind the real configured transport (command_executor._get_json)."""

    def __init__(self, monkeypatch, payloads: Dict[str, Any], tenant: str = "t-own", decisions: Any = None) -> None:
        import urllib.error

        from services.control_plane.bff import command_executor
        from services.control_plane.bff.command_adapters import base
        from services.control_plane.bff.core import owner_reads

        self.calls: List[Dict[str, Any]] = []
        monkeypatch.setenv("PANTHEON_GOVERNANCE_APPROVAL_API_URL", "http://governance.test")
        monkeypatch.setattr(owner_reads, "verified_caller_tenant", lambda payload=None, tenant_id=None: tenant)

        def _get_json(url, auth_token=None, mfa_token=None, tenant_id=None, timeout=None):
            self.calls.append({"url": url, "auth": auth_token, "tenant": tenant_id})
            value = payloads[url.rsplit("/api/governance/", 1)[1]]
            if isinstance(value, Exception):
                raise value
            return value

        monkeypatch.setattr(command_executor, "_get_json", _get_json)

        # Evolution owner: tenant-guarded decision read (foreign/unknown -> 404).
        # Default: every decision id "d-<tenant>-*" belongs to that tenant.
        self.decision_calls: List[Dict[str, Any]] = []
        monkeypatch.setenv("PANTHEON_EVOLUTION_API_URL", "http://evolution.test")

        def _evolution(url, *, caller_token=None, method="GET", tenant_id=None, **_kw):
            did = url.rsplit("/", 1)[1]
            self.decision_calls.append({"id": did, "tenant": tenant_id, "auth": caller_token})
            table = decisions if decisions is not None else {}
            d = table.get(did)
            if isinstance(d, Exception):
                raise d
            if d is None or d.get("tenant_id") != tenant_id:
                raise urllib.error.HTTPError(url, 404, "nf", {}, None)
            return d

        monkeypatch.setattr(base, "evolution_owner_request", _evolution)
        monkeypatch.setattr(owner_reads, "evolution_owner_request", _evolution)
        monkeypatch.setattr(owner_reads, "bound_tenant", lambda payload=None, tenant_id=None, auth_token=None: tenant)
        monkeypatch.setenv("EVOLUTION_AUTH_TOKEN", "svc-token")


_DECISIONS = {
    "d-own": {"decision_id": "d-own", "tenant_id": "t-own", "target_id": "a1", "target_version": "v1", "action_type": "freeze"},
    "d-own2": {"decision_id": "d-own2", "tenant_id": "t-own", "target_id": "a2", "target_version": "v1", "action_type": "freeze"},
    "d-foreign": {"decision_id": "d-foreign", "tenant_id": "t-other", "target_id": "a9", "target_version": "v1", "action_type": "freeze"},
    "d-rb": {"decision_id": "d-rb", "tenant_id": "t-own", "target_id": "rt1", "action_type": "rollback"},
    "d-rb-foreign": {"decision_id": "d-rb-foreign", "tenant_id": "t-other", "target_id": "rt1", "action_type": "rollback"},
}


def _journal_port():
    return create_lifecycle_telemetry_governance_port()


def _with_caller():
    from services.control_plane.bff.core.owner_reads import authorization, request_read_scope

    token = authorization.set("Bearer caller-token")
    return token, authorization, request_read_scope


def test_default_port_reads_governance_owner_scoped_to_verified_tenant(monkeypatch):
    owner = _GovOwner(monkeypatch, {
        "freeze-orders": [
            {"freeze_order_id": "f-own", "status": "active", "tenant_id": "t-own", "evolution_decision_id": "d-own",
             "target_id": "a1", "target_version": "v1", "created_at": "2026-11-02T00:00:00Z"},
            {"freeze_order_id": "f-foreign", "status": "active", "tenant_id": "t-other", "evolution_decision_id": "d-foreign"},
            # Caller-written body claims the caller's tenant on a foreign decision: not proof.
            {"freeze_order_id": "f-forged", "status": "active", "tenant_id": "t-own", "evolution_decision_id": "d-foreign",
             "target_id": "a9", "target_version": "v1"},
            # Claims own tenant but no decision binding: unverifiable, never visible.
            {"freeze_order_id": "f-claim-only", "status": "active", "tenant_id": "t-own"},
            {"freeze_order_id": "f-legacy", "status": "active"},
        ],
        "rollbacks": [
            {"rollback_id": "r-own", "runtime_id": "rt1", "status": "completed", "evolution_decision_id": "d-rb"},
            {"rollback_id": "r-foreign", "runtime_id": "rt1", "tenant_id": "t-other", "evolution_decision_id": "d-rb-foreign"},
        ],
    }, decisions=_DECISIONS)
    port = _journal_port()
    token, authorization, scope = _with_caller()
    try:
        with scope():
            orders = port.list_freeze_orders()
            rollbacks = port.list_all_rollbacks(runtime_id="rt1")
            src_freeze = port.dataset_source("freeze_orders")
            src_rb = port.dataset_source("all_rollbacks")
            assert len(owner.calls) == 2  # rows + source share one read per dataset
    finally:
        authorization.reset(token)
    assert [o["freeze_order_id"] for o in orders] == ["f-own"]
    assert [r["rollback_id"] for r in rollbacks] == ["r-own"]
    assert rollbacks[0]["status"] == "completed"
    assert {c["id"] for c in owner.decision_calls} == {"d-own", "d-foreign", "d-rb", "d-rb-foreign"}  # one read per decision
    assert all(c["tenant"] == "t-own" and c["auth"] == "caller-token" for c in owner.decision_calls)
    # Unresolved legacy row never fabricated into the tenant; source stays unverified.
    assert src_freeze == "unavailable"
    assert src_rb == "service"
    assert all(c["auth"] == "caller-token" and c["tenant"] == "t-own" for c in owner.calls)


def test_default_port_healthy_empty_vs_error_vs_unconfigured(monkeypatch):
    port = _journal_port()
    token, authorization, scope = _with_caller()
    try:
        for env in ("PANTHEON_GOVERNANCE_API_URL", "PANTHEON_GOVERNANCE_APPROVAL_API_URL", "PANTHEON_GOVERNANCE_SERVICE_URL"):
            monkeypatch.delenv(env, raising=False)
        assert port.list_freeze_orders() == []
        assert port.dataset_source("freeze_orders") == "missing"

        _GovOwner(monkeypatch, {"freeze-orders": [], "rollbacks": TimeoutError("owner timeout")})
        with scope():
            assert port.list_freeze_orders() == []
            assert port.dataset_source("freeze_orders") == "service"  # healthy empty
            assert port.list_all_rollbacks() == []
            assert port.dataset_source("all_rollbacks") == "unavailable"  # error is not zero

        _GovOwner(monkeypatch, {"freeze-orders": {"not": "a list"}, "rollbacks": []})
        with scope():
            assert port.list_freeze_orders() == []
            assert port.dataset_source("freeze_orders") == "unavailable"
    finally:
        authorization.reset(token)


def test_default_port_requires_caller_authorization(monkeypatch):
    _GovOwner(monkeypatch, {"freeze-orders": [{"freeze_order_id": "f", "evolution_decision_id": "d-own"}], "rollbacks": []}, decisions=_DECISIONS)
    port = _journal_port()
    assert port.list_freeze_orders() == []
    assert port.dataset_source("freeze_orders") == "unavailable"


def test_injected_store_mode_unchanged():
    port = DomainGovernancePort(freeze_orders={"f": {"freeze_order_id": "f"}})
    assert [o["freeze_order_id"] for o in port.list_freeze_orders()] == ["f"]


def test_default_port_uses_canonical_owner_url_not_legacy_alias(monkeypatch):
    owner = _GovOwner(monkeypatch, {
        "freeze-orders": [{"freeze_order_id": "f-own", "evolution_decision_id": "d-own", "target_id": "a1", "target_version": "v1"}],
        "rollbacks": [{"rollback_id": "r-own", "evolution_decision_id": "d-rb", "runtime_id": "rt1"}],
    }, decisions=_DECISIONS)
    monkeypatch.setenv("PANTHEON_GOVERNANCE_API_URL", "http://evolution.test")
    port = _journal_port()
    token, authorization, scope = _with_caller()
    try:
        with scope():
            assert [o["freeze_order_id"] for o in port.list_freeze_orders()] == ["f-own"]
            assert port.dataset_source("freeze_orders") == "service"
    finally:
        authorization.reset(token)
    assert owner.calls and all(c["url"].startswith("http://governance.test/") for c in owner.calls)


def test_default_port_service_url_only_is_configured(monkeypatch):
    owner = _GovOwner(monkeypatch, {"freeze-orders": [{"freeze_order_id": "f", "evolution_decision_id": "d-own", "target_id": "a1", "target_version": "v1"}], "rollbacks": []}, decisions=_DECISIONS)
    monkeypatch.delenv("PANTHEON_GOVERNANCE_APPROVAL_API_URL")
    monkeypatch.setenv("PANTHEON_GOVERNANCE_SERVICE_URL", "http://svc.test")
    port = _journal_port()
    token, authorization, scope = _with_caller()
    try:
        with scope():
            assert [o["freeze_order_id"] for o in port.list_freeze_orders()] == ["f"]
            assert port.dataset_source("freeze_orders") == "service"
    finally:
        authorization.reset(token)
    assert owner.calls[0]["url"].startswith("http://svc.test/")


def test_decision_read_error_is_unavailable_not_hidden_or_healthy(monkeypatch):
    import urllib.error

    err = urllib.error.HTTPError("http://evolution.test/x", 503, "down", {}, None)
    _GovOwner(
        monkeypatch,
        {"freeze-orders": [{"freeze_order_id": "f", "evolution_decision_id": "d-own"}], "rollbacks": []},
        decisions={"d-own": err},
    )
    port = _journal_port()
    token, authorization, scope = _with_caller()
    try:
        with scope():
            assert port.list_freeze_orders() == []
            assert port.dataset_source("freeze_orders") == "unavailable"
    finally:
        authorization.reset(token)


def test_freeze_row_target_mismatch_with_decision_is_unresolved(monkeypatch):
    _GovOwner(
        monkeypatch,
        {"freeze-orders": [
            {"freeze_order_id": "f-ok", "evolution_decision_id": "d-own", "target_id": "a1", "target_version": "v1"},
            {"freeze_order_id": "f-bad", "evolution_decision_id": "d-own", "target_id": "zzz", "target_version": "v1"},
        ], "rollbacks": []},
        decisions=_DECISIONS,
    )
    port = _journal_port()
    token, authorization, scope = _with_caller()
    try:
        with scope():
            assert [o["freeze_order_id"] for o in port.list_freeze_orders()] == ["f-ok"]  # verified row stays visible
            assert port.dataset_source("freeze_orders") == "unavailable"  # unresolved sibling keeps it partial
    finally:
        authorization.reset(token)


def _run(monkeypatch, payloads, decisions):
    owner = _GovOwner(monkeypatch, payloads, decisions=decisions)
    port = _journal_port()
    token, authorization, scope = _with_caller()
    try:
        with scope():
            out = (
                [o["freeze_order_id"] for o in port.list_freeze_orders()],
                [r["rollback_id"] for r in port.list_all_rollbacks()],
                port.dataset_source("freeze_orders"),
                port.dataset_source("all_rollbacks"),
            )
    finally:
        authorization.reset(token)
    return out, owner


def _frz(i, **kw):
    return {"freeze_order_id": i, "status": "active", "tenant_id": "t-own", "evolution_decision_id": "d-own",
            "target_id": "a1", "target_version": "v1", **kw}


def test_executed_decision_reference_to_other_freeze_is_not_borrowed(monkeypatch):
    dec = {"d-own": {**_DECISIONS["d-own"], "decision_state": "executed", "execution_result": {"execution_ref_id": "f-real"}}}
    (f, _, sf, _), _o = _run(monkeypatch, {"freeze-orders": [_frz("f-real"), _frz("f-copied")], "rollbacks": []}, dec)
    assert f == ["f-real"]
    assert sf == "unavailable"  # contradicted sibling stays unresolved, not healthy


def test_requested_history_decision_without_execution_ref_still_binds(monkeypatch):
    dec = {"d-own": {**_DECISIONS["d-own"], "decision_state": "pending"}}
    (f, _, sf, _), _o = _run(monkeypatch, {"freeze-orders": [_frz("f-req")], "rollbacks": []}, dec)
    assert f == ["f-req"] and sf == "service"


def test_rollback_cannot_borrow_unrelated_freeze_decision_or_foreign_runtime(monkeypatch):
    rows = [
        {"rollback_id": "r-unbound", "runtime_id": "runtime-foreign", "evolution_decision_id": "d-own"},
        {"rollback_id": "r-wrong-rt", "runtime_id": "runtime-foreign", "evolution_decision_id": "d-rb"},
        {"rollback_id": "r-ok", "runtime_id": "rt1", "evolution_decision_id": "d-rb"},
    ]
    (_, rb, _, srb), _o = _run(monkeypatch, {"freeze-orders": [], "rollbacks": rows}, _DECISIONS)
    assert rb == ["r-ok"] and srb == "unavailable"


def test_unknown_404_and_malformed_decision_are_not_healthy_empty(monkeypatch):
    rows = [_frz("f-missing", evolution_decision_id="nope"), _frz("f-bad", evolution_decision_id="d-bad")]
    dec = {**_DECISIONS, "d-bad": ["not", "a-dict"]}
    (f, _, sf, _), _o = _run(monkeypatch, {"freeze-orders": rows, "rollbacks": []}, dec)
    assert f == [] and sf == "unavailable"


def test_later_decision_error_keeps_previously_verified_rows(monkeypatch):
    import urllib.error

    err = urllib.error.HTTPError("http://evolution.test/x", 503, "down", {}, None)
    rows = [_frz("f-ok"), _frz("f-2", evolution_decision_id="d-sec")]
    (f, _, sf, _), _o = _run(monkeypatch, {"freeze-orders": rows, "rollbacks": []}, {**_DECISIONS, "d-sec": err})
    assert f == ["f-ok"] and sf == "unavailable"


def test_each_decision_get_recomputes_budget_and_stops_after_deadline(monkeypatch):
    from services.control_plane.bff.core import owner_reads

    rows = [_frz("f1"), _frz("f2", evolution_decision_id="d-own2", target_id="a2")]
    owner = _GovOwner(monkeypatch, {"freeze-orders": rows, "rollbacks": []}, decisions=_DECISIONS)
    clock = [0.0]
    monkeypatch.setattr(owner_reads.time, "monotonic", lambda: clock[0])
    timeouts = []
    real = owner_reads.evolution_owner_request

    def _timed(url, **kw):
        timeouts.append(kw.get("timeout"))
        clock[0] += 1.1
        return real(url, **kw)

    monkeypatch.setattr(owner_reads, "evolution_owner_request", _timed)
    port = _journal_port()
    token, authorization, scope = _with_caller()
    dtoken = owner_reads.management_read_deadline.set(1.0)
    try:
        with scope():
            f = [o["freeze_order_id"] for o in port.list_freeze_orders()]
            src = port.dataset_source("freeze_orders")
    finally:
        owner_reads.management_read_deadline.reset(dtoken)
        authorization.reset(token)
    assert f == ["f1"] and src == "unavailable"
    assert timeouts == [1.0]  # second decision GET never issued past the deadline
