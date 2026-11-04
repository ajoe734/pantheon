"""Regression and cost tests for INCIDENT-READ-NOOP-SWEEP-20261104.

Covers:
1. GET routes calling resolve_stale_agent_finding_incidents when no stale
   monitor-agent finding exists do not enter _write_guard and perform zero
   _load calls and zero whole-store deepcopy calls against a JSON store with
   2600 incidents and 2000 postmortems built from the real record shape.
2. POST /api/incidents/reconcile-infrastructure-health with no incident to
   resolve does not enter _write_guard and performs zero _load calls and zero
   whole-store deepcopy calls.
3. When a stale finding exists, the read resolves it once before building the
   response so the same response no longer lists it as open, and subsequent
   reads perform zero reloads and zero deepcopies.
4. The write path re-evaluates its candidates inside _write_guard; a finding
   re-observed or already resolved by another process between the read-only
   check and the write is not changed.
5. A change written by another process (second IncidentStore instance on the
   same file) is seen by the read-only check through the existing mtime
   refresh and resolved on read.
6. PostgresIncidentStore behaviour and owner table read counts per GET before
   and after.
7. 50 sequential GET /api/incidents?open_only=true requests through the real
   FastAPI app against the 2600-incident / 2000-postmortem JSON fixture achieve
   median latency well under 50 ms.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy as stdlib_deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import statistics
import sys
import time
from typing import Any, Dict, List, Tuple

from fastapi.testclient import TestClient
import pytest

_PREV_RUNTIME_MANAGER_URL = os.environ.get("PANTHEON_RUNTIME_MANAGER_URL")
_PREV_RUNTIME_MANAGER_TOKEN = os.environ.get("PANTHEON_RUNTIME_MANAGER_TOKEN")
os.environ.setdefault("PANTHEON_RUNTIME_MANAGER_URL", "http://127.0.0.1:9")
os.environ.setdefault("PANTHEON_RUNTIME_MANAGER_TOKEN", "incident-read-sweep-cost-token")
try:
    import services.incident.incident as incident_mod
    from services.incident.incident import (
        MONITOR_AGENT_ARTIFACT_PREFIX,
        MONITOR_AGENT_AUTHORITY_ID,
        MONITOR_AGENT_BINDING_ID,
        MONITOR_AGENT_CLUSTER_PREFIX,
        MONITOR_AGENT_COMPONENT_KIND,
        MONITOR_AGENT_PLAN_ID,
        MONITOR_AGENT_POOL_ID,
        MONITOR_AGENT_RUNTIME_ID,
        NOT_REOBSERVED_RESOLUTION_REASON,
        ORPHAN_RECOVERED_RESOLUTION_REASON,
        IncidentCase,
        IncidentStatus,
        IncidentStore,
        Postmortem,
        extract_incident_resolution_reason,
        validate_incident_case,
        validate_postmortem,
    )
    import services.incidents.main as incidents_main
    from services.incidents.main import app, outbox_store
finally:
    if _PREV_RUNTIME_MANAGER_URL is None:
        os.environ.pop("PANTHEON_RUNTIME_MANAGER_URL", None)
    else:
        os.environ["PANTHEON_RUNTIME_MANAGER_URL"] = _PREV_RUNTIME_MANAGER_URL
    if _PREV_RUNTIME_MANAGER_TOKEN is None:
        os.environ.pop("PANTHEON_RUNTIME_MANAGER_TOKEN", None)
    else:
        os.environ["PANTHEON_RUNTIME_MANAGER_TOKEN"] = _PREV_RUNTIME_MANAGER_TOKEN


REAL_POSTMORTEM_23_KEYS = frozenset(
    {
        "action_items",
        "artifact_id",
        "artifact_version",
        "author_ids",
        "binding_id",
        "capital_pool_id",
        "contributing_factors",
        "created_at",
        "deployment_plan_id",
        "deployment_stage",
        "incident_cluster_id",
        "incident_evidence_summary",
        "incident_id",
        "lineage_ref",
        "persona_capital_binding_id",
        "postmortem_id",
        "reconciliation_ids",
        "root_cause",
        "runtime_id",
        "status",
        "telemetry_event_ids",
        "timeline",
        "title",
        "trace_id",
    }
)


def _build_real_incident_dict(idx: int, *, now_dt: datetime) -> Dict[str, Any]:
    """Build a canonical IncidentCase dict matching the hosted 16.5MB store record shapes.

    Indices 0..2591 are resolved historical incidents across monitor-agent findings,
    infrastructure-health incidents, drift incidents, and runtime threshold incidents.
    Indices 2592..2599 (8 incidents) are open incidents:
      - 2592, 2593: fresh open monitor-agent findings observed 10m ago (not stale)
      - 2594, 2595: open infrastructure-health incidents for 'telemetry' and 'execution'
      - 2596..2599: open runtime / drift incidents
    """
    created_dt = datetime(2026, 10, 15, 0, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=idx * 5)
    created_iso = created_dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    resolved_iso = (created_dt + timedelta(minutes=25)).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    if idx in (2592, 2593):
        obs_dt = now_dt - timedelta(minutes=10)
        obs_iso = obs_dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        snap_ts = obs_dt.strftime("%Y%m%dT%H%M%SZ")
        key = "reconciler-generation-change" if idx == 2592 else "fleet-size-change"
        snap_ref = f"monitor-snapshot-{snap_ts}-{idx:012d}"
        return {
            "incident_id": f"agent-open{idx:08d}-0000",
            "title": f"Active monitor-agent finding {key} ({idx})",
            "status": "open",
            "severity": "medium",
            "created_at": obs_iso,
            "binding_id": MONITOR_AGENT_BINDING_ID,
            "deployment_stage": "paper",
            "deployment_plan_id": MONITOR_AGENT_PLAN_ID,
            "capital_pool_id": MONITOR_AGENT_POOL_ID,
            "persona_capital_binding_id": MONITOR_AGENT_AUTHORITY_ID,
            "artifact_id": f"{MONITOR_AGENT_ARTIFACT_PREFIX}{key}",
            "artifact_version": "1.0.0",
            "runtime_id": MONITOR_AGENT_RUNTIME_ID,
            "trace_id": snap_ref,
            "telemetry_event_ids": [snap_ref],
            "reconciliation_ids": [f"recon-agent-{idx:04d}"],
            "incident_cluster_id": f"{MONITOR_AGENT_CLUSTER_PREFIX}{key}",
            "evidence_summary": (
                f"component_kind={MONITOR_AGENT_COMPONENT_KIND}; "
                f"component=agent-finding-{key}; fingerprint={key}; "
                f"canonical_key={key}; observed_at={obs_iso}; "
                f"snapshot_ref={snap_ref}; "
                f"rationale=Fresh monitor observation within active window ({idx})"
            ),
            "lineage_ref": f"{MONITOR_AGENT_ARTIFACT_PREFIX}{key}@1.0.0",
        }

    if idx in (2594, 2595):
        comp = "telemetry" if idx == 2594 else "execution"
        evt_id = f"evt-infra-open-{idx:04d}"
        return {
            "incident_id": f"infra-bff-open-{idx:04d}",
            "title": f"BFF downstream degradation: {comp}",
            "status": "open",
            "severity": "high",
            "created_at": created_iso,
            "binding_id": f"infra-subject-default-control-plane-bff-{comp}",
            "deployment_stage": "paper",
            "deployment_plan_id": "infra-plan-control-plane-bff",
            "capital_pool_id": "infra-pool-default",
            "persona_capital_binding_id": "infra-authority-control-plane-bff",
            "artifact_id": f"infra-component-{comp}",
            "artifact_version": "endpoint-1234567890ab",
            "runtime_id": "infra-endpoint-1234567890ab",
            "trace_id": evt_id,
            "telemetry_event_ids": [evt_id],
            "reconciliation_ids": [f"recon-infra-{idx:04d}"],
            "incident_cluster_id": f"infrastructure:default:control-plane-bff:{comp}",
            "evidence_summary": (
                f"non_trading_infrastructure_incident=true; tenant_id=default; "
                f"producer=control-plane-bff; service_name={comp}; "
                f"component_kind=http_service; endpoint=http://{comp}:8080/__health__; "
                f"source_event_id={evt_id}"
            ),
            "lineage_ref": f"infra-component-{comp}@endpoint-1234567890ab",
        }

    if idx >= 2596:
        return {
            "incident_id": f"inc-open-runtime-{idx:04d}",
            "title": f"Rolling drawdown threshold breach ({idx})",
            "status": "open",
            "severity": "high",
            "created_at": created_iso,
            "binding_id": f"bind-paper-{idx % 25:03d}",
            "deployment_stage": "paper",
            "deployment_plan_id": f"plan-202610-{idx % 40:03d}",
            "capital_pool_id": f"pool-alpha-{idx % 5:02d}",
            "persona_capital_binding_id": f"pcb-alpha-{idx % 25:03d}",
            "artifact_id": f"art-strat-{idx % 30:03d}",
            "artifact_version": f"v1.{idx % 10}.0",
            "runtime_id": f"rt-paper-{idx % 25:03d}",
            "trace_id": f"trace-inc-{idx:04d}-9f8e7d6c5b4a",
            "telemetry_event_ids": [f"tel-{idx:04d}-a", f"tel-{idx:04d}-b"],
            "reconciliation_ids": [f"recon-{idx:04d}-01"],
            "incident_cluster_id": f"drift:rolling_drawdown_multiple:{idx % 12:02d}",
            "evidence_summary": (
                f"threshold_metric=rolling_drawdown_multiple; observed=1.42; "
                f"comparator=gt; threshold_value=1.25; window=paper-daily-sweep; "
                f"binding=bind-paper-{idx % 25:03d}"
            ),
            "lineage_ref": f"art-strat-{idx % 30:03d}@v1.{idx % 10}.0",
            "threshold_identity": "rolling_drawdown_multiple/paper-daily-sweep/EVOLUTION_REVIEW_AND_THRESHOLDS.md#7.1",
        }

    if idx % 3 == 0:
        snap_ts = created_dt.strftime("%Y%m%dT%H%M%SZ")
        key = f"historical-finding-{idx % 19:02d}"
        snap_ref = f"monitor-snapshot-{snap_ts}-{idx:012d}"
        return {
            "incident_id": f"agent-hist{idx:08d}-0000",
            "title": f"Historical monitor-agent finding {key} ({idx})",
            "status": "resolved",
            "severity": "medium",
            "created_at": created_iso,
            "resolved_at": resolved_iso,
            "binding_id": MONITOR_AGENT_BINDING_ID,
            "deployment_stage": "paper",
            "deployment_plan_id": MONITOR_AGENT_PLAN_ID,
            "capital_pool_id": MONITOR_AGENT_POOL_ID,
            "persona_capital_binding_id": MONITOR_AGENT_AUTHORITY_ID,
            "artifact_id": f"{MONITOR_AGENT_ARTIFACT_PREFIX}{key}",
            "artifact_version": "1.0.0",
            "runtime_id": MONITOR_AGENT_RUNTIME_ID,
            "trace_id": snap_ref,
            "telemetry_event_ids": [
                snap_ref,
                f"resolution_reason:{NOT_REOBSERVED_RESOLUTION_REASON}",
            ],
            "reconciliation_ids": [f"recon-hist-{idx:04d}"],
            "incident_cluster_id": f"{MONITOR_AGENT_CLUSTER_PREFIX}{key}",
            "evidence_summary": (
                f"component_kind={MONITOR_AGENT_COMPONENT_KIND}; "
                f"component=agent-finding-{key}; fingerprint={key}; "
                f"canonical_key={key}; observed_at={created_iso}; "
                f"snapshot_ref={snap_ref}; "
                + ("rationale=" + ("m" * 1650))
                + f"; resolution_reason={NOT_REOBSERVED_RESOLUTION_REASON}"
            ),
            "lineage_ref": f"{MONITOR_AGENT_ARTIFACT_PREFIX}{key}@1.0.0",
        }

    return {
        "incident_id": f"inc-20261104-{idx:04d}",
        "title": f"BFF downstream degradation: surface-{idx % 17} ({idx})",
        "status": "resolved",
        "severity": "high" if idx % 2 == 0 else "medium",
        "created_at": created_iso,
        "resolved_at": resolved_iso,
        "binding_id": f"bind-paper-{idx % 25:03d}",
        "deployment_stage": "paper",
        "deployment_plan_id": f"plan-202610-{idx % 40:03d}",
        "capital_pool_id": f"pool-alpha-{idx % 5:02d}",
        "persona_capital_binding_id": f"pcb-alpha-{idx % 25:03d}",
        "artifact_id": f"art-strat-{idx % 30:03d}",
        "artifact_version": f"v1.{idx % 10}.0",
        "runtime_id": f"rt-paper-{idx % 25:03d}",
        "trace_id": f"trace-inc-{idx:04d}-9f8e7d6c5b4a",
        "telemetry_event_ids": [
            f"tel-{idx:04d}-a",
            f"tel-{idx:04d}-b",
            f"tel-{idx:04d}-c",
        ],
        "reconciliation_ids": [f"recon-{idx:04d}-01", f"drift-{idx:04d}-01"],
        "incident_cluster_id": f"cluster-deg-{idx % 12:02d}",
        "evidence_summary": (
            f"Observed downstream degradation on surface-{idx % 17} with p99 latency > 5000ms "
            f"for binding bind-paper-{idx % 25:03d} in pool pool-alpha-{idx % 5:02d}; "
            + ("forensic_context=" + ("e" * 1650))
        ),
        "lineage_ref": f"art-strat-{idx % 30:03d}@v1.{idx % 10}.0",
        "threshold_identity": f"metric_{idx % 7}/paper-daily-sweep/EVOLUTION_REVIEW_AND_THRESHOLDS.md#7.1",
    }


def _build_real_postmortem_dict(idx: int, parent_inc: Dict[str, Any]) -> Dict[str, Any]:
    pm_id = f"pm-20261104-{idx:04d}"
    inc_id = parent_inc["incident_id"]
    created_at = parent_inc["created_at"]
    record = {
        "postmortem_id": pm_id,
        "incident_id": inc_id,
        "title": f"Postmortem draft: {parent_inc['title']} ({pm_id})",
        "status": "draft",
        "created_at": created_at,
        "binding_id": parent_inc["binding_id"],
        "deployment_stage": parent_inc["deployment_stage"],
        "deployment_plan_id": parent_inc["deployment_plan_id"],
        "capital_pool_id": parent_inc["capital_pool_id"],
        "persona_capital_binding_id": parent_inc["persona_capital_binding_id"],
        "artifact_id": parent_inc["artifact_id"],
        "artifact_version": parent_inc["artifact_version"],
        "runtime_id": parent_inc["runtime_id"],
        "trace_id": parent_inc["trace_id"],
        "telemetry_event_ids": list(parent_inc["telemetry_event_ids"]),
        "reconciliation_ids": list(parent_inc["reconciliation_ids"]),
        "incident_cluster_id": parent_inc["incident_cluster_id"],
        "incident_evidence_summary": parent_inc["evidence_summary"],
        "lineage_ref": parent_inc["lineage_ref"],
        "root_cause": (
            f"Downstream read timeout on surface-{idx % 17} during concurrent fanout window {idx}; "
            f"upstream HTTP budget exceeded while reading canonical owner store for {inc_id}. "
            + ("Detailed forensic trace segment: " + ("x" * 950))
        ),
        "contributing_factors": [
            f"Unpaged owner read on surface-{idx % 17}",
            "Burst of concurrent operator cockpit polling requests",
            f"Retry storm on runtime {parent_inc['runtime_id']}",
        ],
        "timeline": [
            {"ts": created_at, "description": f"Incident {inc_id} opened automatically by health monitor", "actor": "monitor"},
            {"ts": created_at, "description": f"Draft postmortem {pm_id} initialized from incident evidence", "actor": "postmortem-svc"},
        ],
        "action_items": [
            f"Enforce bounded page_size on owner list endpoint for surface-{idx % 17}",
            f"Verify circuit breaker thresholds on {parent_inc['runtime_id']}",
        ],
        "author_ids": ["system-auto-postmortem", "ops-oncall"],
    }
    assert set(record.keys()) == REAL_POSTMORTEM_23_KEYS
    return record


@pytest.fixture(scope="module")
def seeded_large_json_template(tmp_path_factory) -> Tuple[Path, datetime]:
    """Build the 2600-incident + 2000-postmortem JSON store once per test module."""
    base_dir = tmp_path_factory.mktemp("incident_sweep_fixture")
    template_path = base_dir / "incidents_template.json"
    now_dt = datetime(2026, 11, 4, 3, 30, 0, tzinfo=timezone.utc)

    incidents = [_build_real_incident_dict(i, now_dt=now_dt) for i in range(2600)]
    postmortems = [_build_real_postmortem_dict(i, incidents[i]) for i in range(2000)]

    # Validate sample records against domain validators
    sample_inc = IncidentCase.from_dict(incidents[0])
    sample_pm = Postmortem.from_dict(postmortems[0])
    assert validate_incident_case(sample_inc) == []
    assert validate_postmortem(sample_pm) == []
    IncidentStore._validate_postmortem_against_incident(sample_pm, sample_inc)

    with template_path.open("w", encoding="utf-8") as handle:
        json.dump({"incidents": incidents, "postmortems": postmortems}, handle, indent=2)

    return template_path, now_dt


@pytest.fixture
def large_json_store(tmp_path, monkeypatch, seeded_large_json_template):
    template_path, now_dt = seeded_large_json_template
    store_path = tmp_path / "incidents.json"
    store_path.write_bytes(template_path.read_bytes())

    store = IncidentStore(path=store_path)
    assert len(store._incidents) == 2600
    assert len(store._postmortems) == 2000

    now_iso = now_dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    monkeypatch.setattr(incidents_main, "store", store)
    monkeypatch.setattr(incidents_main, "_utc_now", lambda: now_iso)
    monkeypatch.setenv("INCIDENTS_AGENT_FINDING_RESOLVE_AFTER_SECONDS", "7200")

    outbox_path = tmp_path / "incidents_outbox.json"
    if hasattr(outbox_store, "impl") and hasattr(outbox_store.impl, "path"):
        monkeypatch.setattr(outbox_store.impl, "path", outbox_path)

    return store, store_path, now_dt


@contextmanager
def _count_store_ops(store: IncidentStore, monkeypatch):
    counts = {"write_guard": 0, "load": 0, "deepcopy": 0, "save": 0}
    orig_write_guard = store._write_guard
    orig_load = store._load
    orig_save = store._save
    orig_deepcopy = incident_mod.deepcopy

    @contextmanager
    def counting_write_guard():
        counts["write_guard"] += 1
        with orig_write_guard():
            yield

    def counting_load(path: Path):
        counts["load"] += 1
        return orig_load(path)

    def counting_save(**kwargs):
        counts["save"] += 1
        return orig_save(**kwargs)

    def counting_deepcopy(obj, *args, **kwargs):
        if obj is store._incidents or obj is store._postmortems:
            counts["deepcopy"] += 1
        return orig_deepcopy(obj, *args, **kwargs)

    monkeypatch.setattr(store, "_write_guard", counting_write_guard)
    monkeypatch.setattr(store, "_load", counting_load)
    monkeypatch.setattr(store, "_save", counting_save)
    monkeypatch.setattr(incident_mod, "deepcopy", counting_deepcopy)
    yield counts


def test_ac1_get_routes_with_no_stale_finding_skip_write_guard_load_and_deepcopy(
    large_json_store, monkeypatch
):
    store, store_path, _ = large_json_store
    assert store_path.stat().st_size >= 15_000_000
    client = TestClient(app)

    with _count_store_ops(store, monkeypatch) as counts:
        r_open = client.get("/api/incidents?open_only=true")
        assert r_open.status_code == 200
        open_items = r_open.json()
        assert len(open_items) == 8

        r_single = client.get("/api/incidents/agent-open00002592-0000")
        assert r_single.status_code == 200
        assert r_single.json()["status"] == "open"

        r_op = client.get("/api/incidents/inc-20261104-0001/operator-payload")
        assert r_op.status_code == 200
        assert r_op.json()["postmortem_id"] == "pm-20261104-0001"

        r_all = client.get("/api/incidents?binding_id=bind-paper-000")
        assert r_all.status_code == 200
        assert len(r_all.json()) > 0

    assert counts["write_guard"] == 0
    assert counts["load"] == 0
    assert counts["deepcopy"] == 0
    assert counts["save"] == 0


def test_ac2_reconcile_infrastructure_health_noop_skips_write_guard_load_and_deepcopy(
    large_json_store, monkeypatch
):
    store, _, _ = large_json_store
    client = TestClient(app)

    with _count_store_ops(store, monkeypatch) as counts:
        # Components with no open unreferenced infrastructure incident:
        # governance/capital/deployment have no open incident; telemetry/execution are referenced
        resp = client.post(
            "/api/incidents/reconcile-infrastructure-health",
            json={
                "healthy_components": ["governance", "capital", "deployment", "telemetry", "execution"],
                "referenced_incident_ids": ["infra-bff-open-2594", "infra-bff-open-2595"],
                "observed_at": "2026-11-04T03:30:00Z",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["resolved_count"] == 0
        assert body["resolved_incident_ids"] == []

    assert counts["write_guard"] == 0
    assert counts["load"] == 0
    assert counts["deepcopy"] == 0
    assert counts["save"] == 0


def test_ac3_stale_finding_resolved_once_before_response_then_subsequent_reads_noop(
    tmp_path, monkeypatch
):
    store_path = tmp_path / "incidents.json"
    store = IncidentStore(path=store_path)
    monkeypatch.setattr(incidents_main, "store", store)
    monkeypatch.setenv("INCIDENTS_AGENT_FINDING_RESOLVE_AFTER_SECONDS", "7200")

    now_dt = datetime(2026, 11, 4, 3, 30, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(incidents_main, "_utc_now", lambda: "2026-11-04T03:30:00Z")

    # Seed one stale monitor-agent finding (3h old > 7200s) and one fresh finding (10m old)
    stale_dict = _build_real_incident_dict(2592, now_dt=now_dt - timedelta(hours=3))
    stale_dict["incident_id"] = "agent-stale-on-read-0000"
    fresh_dict = _build_real_incident_dict(2593, now_dt=now_dt)
    fresh_dict["incident_id"] = "agent-fresh-on-read-0000"

    store.create_incident(IncidentCase.from_dict(stale_dict))
    store.create_incident(IncidentCase.from_dict(fresh_dict))

    client = TestClient(app)

    # First read resolves the stale finding once before building the response
    with _count_store_ops(store, monkeypatch) as first_counts:
        r1 = client.get("/api/incidents?open_only=true")
        assert r1.status_code == 200
        open_ids = [item["incident_id"] for item in r1.json()]
        assert open_ids == ["agent-fresh-on-read-0000"]

    assert first_counts["write_guard"] == 1
    assert first_counts["save"] == 1
    resolved_inc = store.require_incident("agent-stale-on-read-0000")
    assert resolved_inc.status == IncidentStatus.RESOLVED.value
    assert extract_incident_resolution_reason(resolved_inc) == NOT_REOBSERVED_RESOLUTION_REASON

    # Subsequent read finds no stale finding and performs 0 _load and 0 deepcopy calls
    with _count_store_ops(store, monkeypatch) as second_counts:
        r2 = client.get("/api/incidents?open_only=true")
        assert r2.status_code == 200
        assert [item["incident_id"] for item in r2.json()] == ["agent-fresh-on-read-0000"]

    assert second_counts["write_guard"] == 0
    assert second_counts["load"] == 0
    assert second_counts["deepcopy"] == 0
    assert second_counts["save"] == 0


def test_ac4_write_path_reevaluates_candidates_inside_write_guard_on_concurrent_change(
    tmp_path, monkeypatch
):
    now_dt = datetime(2026, 11, 4, 3, 30, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(incidents_main, "_utc_now", lambda: "2026-11-04T03:30:00Z")
    monkeypatch.setenv("INCIDENTS_AGENT_FINDING_RESOLVE_AFTER_SECONDS", "7200")

    # Subcase A: Finding re-observed by another process between read-only check and write guard
    path_reobs = tmp_path / "incidents_reobs.json"
    reader_store = IncidentStore(path=path_reobs)
    writer_store = IncidentStore(path=path_reobs)

    stale_dict = _build_real_incident_dict(2592, now_dt=now_dt - timedelta(hours=3))
    stale_dict["incident_id"] = "agent-race-reobserved-0000"
    stale_inc = IncidentCase.from_dict(stale_dict)
    reader_store.create_incident(stale_inc)

    orig_has_stale = reader_store.has_stale_monitor_agent_findings

    def has_stale_then_other_process_reobserves(*, resolve_after_seconds: int, now=None) -> bool:
        result = orig_has_stale(resolve_after_seconds=resolve_after_seconds, now=now)
        assert result is True
        # Another process re-observes the finding before reader_store enters _write_guard
        fresh_obs = replace(
            stale_inc,
            telemetry_event_ids=["monitor-snapshot-20261104T032500Z-freshreobs"],
            evidence_summary=(
                f"component_kind={MONITOR_AGENT_COMPONENT_KIND}; "
                "component=agent-finding-reconciler-generation-change; "
                "fingerprint=reconciler-generation-change; "
                "observed_at=2026-11-04T03:25:00Z; "
                "snapshot_ref=monitor-snapshot-20261104T032500Z-freshreobs; "
                "rationale=re-observed by concurrent process"
            ),
        )
        writer_store.merge_incident_evidence("agent-race-reobserved-0000", fresh_obs)
        return result

    monkeypatch.setattr(incidents_main, "store", reader_store)
    monkeypatch.setattr(
        reader_store,
        "has_stale_monitor_agent_findings",
        has_stale_then_other_process_reobserves,
    )

    resolved_reobs = incidents_main.resolve_stale_agent_finding_incidents(now=now_dt)
    assert resolved_reobs == []
    after_reobs = IncidentStore(path=path_reobs).require_incident("agent-race-reobserved-0000")
    assert after_reobs.status == IncidentStatus.OPEN.value
    assert "monitor-snapshot-20261104T032500Z-freshreobs" in after_reobs.telemetry_event_ids
    assert extract_incident_resolution_reason(after_reobs) is None

    # Subcase B: Finding already resolved by another process between read-only check and write guard
    path_resolved = tmp_path / "incidents_resolved.json"
    reader_store_b = IncidentStore(path=path_resolved)
    writer_store_b = IncidentStore(path=path_resolved)

    stale_dict_b = _build_real_incident_dict(2592, now_dt=now_dt - timedelta(hours=3))
    stale_dict_b["incident_id"] = "agent-race-already-resolved-0000"
    reader_store_b.create_incident(IncidentCase.from_dict(stale_dict_b))

    orig_has_stale_b = reader_store_b.has_stale_monitor_agent_findings

    def has_stale_then_other_process_resolves(*, resolve_after_seconds: int, now=None) -> bool:
        result = orig_has_stale_b(resolve_after_seconds=resolve_after_seconds, now=now)
        assert result is True
        writer_store_b.update_incident_status(
            "agent-race-already-resolved-0000",
            IncidentStatus.CLOSED.value,
            resolved_at="2026-11-04T02:00:00Z",
            resolution_reason="operator_closed",
        )
        return result

    monkeypatch.setattr(incidents_main, "store", reader_store_b)
    monkeypatch.setattr(
        reader_store_b,
        "has_stale_monitor_agent_findings",
        has_stale_then_other_process_resolves,
    )

    resolved_b = incidents_main.resolve_stale_agent_finding_incidents(now=now_dt)
    assert resolved_b == []
    after_b = IncidentStore(path=path_resolved).require_incident("agent-race-already-resolved-0000")
    assert after_b.status == IncidentStatus.CLOSED.value
    assert after_b.resolved_at == "2026-11-04T02:00:00Z"
    assert extract_incident_resolution_reason(after_b) == "operator_closed"

    # Subcase C: Orphaned infrastructure incident already resolved between read-only check and write guard
    path_orphan = tmp_path / "incidents_orphan.json"
    reader_store_c = IncidentStore(path=path_orphan)
    writer_store_c = IncidentStore(path=path_orphan)

    orphan_dict = _build_real_incident_dict(2594, now_dt=now_dt)
    orphan_dict["incident_id"] = "infra-bff-race-orphan-0001"
    reader_store_c.create_incident(IncidentCase.from_dict(orphan_dict))

    orig_has_orphan = reader_store_c.has_orphaned_infrastructure_incidents

    def has_orphan_then_other_process_resolves(*, healthy_components, referenced_incident_ids=None) -> bool:
        result = orig_has_orphan(
            healthy_components=healthy_components,
            referenced_incident_ids=referenced_incident_ids,
        )
        assert result is True
        writer_store_c.update_incident_status(
            "infra-bff-race-orphan-0001",
            IncidentStatus.RESOLVED.value,
            resolved_at="2026-11-04T02:15:00Z",
            resolution_reason="direct_probe_recovery",
        )
        return result

    monkeypatch.setattr(incidents_main, "store", reader_store_c)
    monkeypatch.setattr(
        reader_store_c,
        "has_orphaned_infrastructure_incidents",
        has_orphan_then_other_process_resolves,
    )

    resolved_c = incidents_main.reconcile_orphaned_infrastructure_incidents(
        healthy_components=["telemetry"],
        referenced_incident_ids=[],
        resolved_at="2026-11-04T03:30:00Z",
    )
    assert resolved_c == []
    after_c = IncidentStore(path=path_orphan).require_incident("infra-bff-race-orphan-0001")
    assert after_c.status == IncidentStatus.RESOLVED.value
    assert after_c.resolved_at == "2026-11-04T02:15:00Z"
    assert extract_incident_resolution_reason(after_c) == "direct_probe_recovery"


def test_ac5_cross_process_mtime_refresh_sees_and_resolves_stale_finding_from_other_instance(
    tmp_path, monkeypatch
):
    shared_path = tmp_path / "shared_incidents.json"
    now_dt = datetime(2026, 11, 4, 3, 30, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(incidents_main, "_utc_now", lambda: "2026-11-04T03:30:00Z")
    monkeypatch.setenv("INCIDENTS_AGENT_FINDING_RESOLVE_AFTER_SECONDS", "7200")

    store_app = IncidentStore(path=shared_path)
    store_other = IncidentStore(path=shared_path)
    monkeypatch.setattr(incidents_main, "store", store_app)

    # Seed an open runtime incident via store_app
    runtime_inc = IncidentCase.from_dict(_build_real_incident_dict(2596, now_dt=now_dt))
    store_app.create_incident(runtime_inc)

    client = TestClient(app)
    r_before = client.get("/api/incidents?open_only=true")
    assert r_before.status_code == 200
    assert [i["incident_id"] for i in r_before.json()] == [runtime_inc.incident_id]

    # Second instance (other process) writes a stale monitor-agent finding to the same file
    stale_dict = _build_real_incident_dict(2592, now_dt=now_dt - timedelta(hours=4))
    stale_dict["incident_id"] = "agent-written-by-other-proc-0000"
    store_other.create_incident(IncidentCase.from_dict(stale_dict))

    # Next GET on store_app refreshes via mtime in has_stale_monitor_agent_findings and resolves it
    r_after = client.get("/api/incidents?open_only=true")
    assert r_after.status_code == 200
    assert [i["incident_id"] for i in r_after.json()] == [runtime_inc.incident_id]

    resolved_in_app = store_app.require_incident("agent-written-by-other-proc-0000")
    assert resolved_in_app.status == IncidentStatus.RESOLVED.value
    assert extract_incident_resolution_reason(resolved_in_app) == NOT_REOBSERVED_RESOLUTION_REASON

    resolved_in_other = store_other.require_incident("agent-written-by-other-proc-0000")
    assert resolved_in_other.status == IncidentStatus.RESOLVED.value
    assert extract_incident_resolution_reason(resolved_in_other) == NOT_REOBSERVED_RESOLUTION_REASON


def test_ac6_postgres_incident_store_owner_table_reads_per_get_before_and_after(
    monkeypatch,
):
    import services.incident.pg_store as pg_store_mod
    from services.incident.pg_store import PostgresIncidentStore

    now_dt = datetime(2026, 11, 4, 3, 30, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(incidents_main, "_utc_now", lambda: "2026-11-04T03:30:00Z")
    monkeypatch.setenv("INCIDENTS_AGENT_FINDING_RESOLVE_AFTER_SECONDS", "7200")

    inc_rows = [
        _build_real_incident_dict(1, now_dt=now_dt),
        _build_real_incident_dict(2592, now_dt=now_dt),
    ]
    pm_rows = [_build_real_postmortem_dict(1, inc_rows[0])]
    read_counts = {"incident.incident_cases": 0, "incident.postmortems": 0}

    class _CountingOwnerTable:
        def __init__(self, *, dsn: str, table: str, owner_service: str, bootstrap: bool = True) -> None:
            self.dsn = dsn
            self.table = table
            self.owner_service = owner_service

        def list_all(self) -> List[Dict[str, Any]]:
            read_counts[self.table] += 1
            if self.table == "incident.incident_cases":
                return [dict(r) for r in inc_rows]
            return [dict(r) for r in pm_rows]

    monkeypatch.setattr(pg_store_mod, "PostgresJsonOwnerStore", _CountingOwnerTable)
    pg_store = PostgresIncidentStore(dsn="postgresql://postgres:t@127.0.0.1:55432/postgres")
    monkeypatch.setattr(incidents_main, "store", pg_store)
    client = TestClient(app)

    # Reset after initial bootstrap refresh (1 read of each owner table)
    assert read_counts == {"incident.incident_cases": 1, "incident.postmortems": 1}
    read_counts["incident.incident_cases"] = 0
    read_counts["incident.postmortems"] = 0

    # After path (read-only predicate has_stale_monitor_agent_findings + route read):
    # GET /api/incidents?open_only=true -> 2 refreshes = 4 owner table reads (2 incidents + 2 postmortems), 0 deepcopies
    with _count_store_ops(pg_store, monkeypatch) as after_list_ops:
        r_list = client.get("/api/incidents?open_only=true")
        assert r_list.status_code == 200
        assert [i["incident_id"] for i in r_list.json()] == ["agent-open00002592-0000"]
    assert read_counts == {"incident.incident_cases": 2, "incident.postmortems": 2}
    assert sum(read_counts.values()) == 4
    assert after_list_ops["deepcopy"] == 0

    # GET /api/incidents/{incident_id} -> 2 refreshes = 4 owner table reads, 0 deepcopies
    read_counts["incident.incident_cases"] = 0
    read_counts["incident.postmortems"] = 0
    with _count_store_ops(pg_store, monkeypatch) as after_get_ops:
        r_get = client.get("/api/incidents/inc-20261104-0001")
        assert r_get.status_code == 200
    assert read_counts == {"incident.incident_cases": 2, "incident.postmortems": 2}
    assert sum(read_counts.values()) == 4
    assert after_get_ops["deepcopy"] == 0

    # GET /api/incidents/{incident_id}/operator-payload -> 3 refreshes = 6 owner table reads, 0 deepcopies
    read_counts["incident.incident_cases"] = 0
    read_counts["incident.postmortems"] = 0
    with _count_store_ops(pg_store, monkeypatch) as after_op_ops:
        r_op = client.get("/api/incidents/inc-20261104-0001/operator-payload")
        assert r_op.status_code == 200
        assert r_op.json()["postmortem_id"] == "pm-20261104-0001"
    assert read_counts == {"incident.incident_cases": 3, "incident.postmortems": 3}
    assert sum(read_counts.values()) == 6
    assert after_op_ops["deepcopy"] == 0

    # Compare against legacy before-path (unconditional resolve_stale_monitor_agent_findings write-guard call):
    # Same 4 owner table reads on GET /api/incidents?open_only=true, but 2 whole-store deepcopy calls.
    monkeypatch.delattr(IncidentStore, "has_stale_monitor_agent_findings")
    read_counts["incident.incident_cases"] = 0
    read_counts["incident.postmortems"] = 0
    with _count_store_ops(pg_store, monkeypatch) as before_list_ops:
        r_before = client.get("/api/incidents?open_only=true")
        assert r_before.status_code == 200
    assert read_counts == {"incident.incident_cases": 2, "incident.postmortems": 2}
    assert sum(read_counts.values()) == 4
    assert before_list_ops["deepcopy"] == 2


def test_ac7_fifty_sequential_open_only_requests_under_50ms_median(
    large_json_store, monkeypatch
):
    store, store_path, _ = large_json_store
    assert len(store._incidents) == 2600
    assert len(store._postmortems) == 2000
    assert store_path.stat().st_size >= 15_000_000

    client = TestClient(app)
    latencies_ms: List[float] = []

    with _count_store_ops(store, monkeypatch) as counts:
        for _ in range(50):
            t0 = time.perf_counter()
            resp = client.get("/api/incidents?open_only=true")
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            assert resp.status_code == 200
            assert len(resp.json()) == 8
            latencies_ms.append(elapsed_ms)

    assert counts["write_guard"] == 0
    assert counts["load"] == 0
    assert counts["deepcopy"] == 0
    assert counts["save"] == 0

    median_ms = statistics.median(latencies_ms)
    sorted_lat = sorted(latencies_ms)
    p95_ms = sorted_lat[int(0.95 * (len(sorted_lat) - 1))]
    assert median_ms < 50.0, f"Expected median < 50ms, got {median_ms:.2f}ms (p95={p95_ms:.2f}ms)"
