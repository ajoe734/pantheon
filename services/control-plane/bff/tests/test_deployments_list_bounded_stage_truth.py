"""Deployment list parity, bounded real-port reads, and reproducible local profiling.

No composition root, cache, remote service, or timing sleep is used. The fixture
models the existing deployment owner DTOs, including nested saga/artifact data.
"""
from __future__ import annotations

from collections import Counter
from contextlib import ExitStack
import cProfile
import json
from pathlib import Path
import pstats
import statistics
import time
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from services.control_plane.bff.core.owner_reads import request_read_scope
from services.control_plane.bff.deployment.adapters import DeploymentReadSurfaceAdapter
from services.control_plane.bff.deployment.service import DeploymentService
from services.control_plane.bff.personas import service as persona_service
from services.control_plane.bff.personas.service import _aggregate_group_surface, _dataset_surface_status
from services.control_plane.bff.ports import create_in_memory_read_surface_ports
from services.control_plane.bff.tests.test_deployment_router import _build_router

TS = "2026-11-14T00:00:00Z"


def _fixture(count=200):
    plans, bindings, approvals, sessions, telemetry = [], [], [], [], []
    for i in range(count):
        plans.append({
            "plan_id": f"plan-{i}", "approval_decision_id": f"approval-{i}",
            "artifact_id": f"artifact-{i}", "artifact_version": "v1.0.0",
            "capital_pool_id": f"pool-{i % 4}", "strategy_id": f"strategy-{i % 8}",
            "current_stage": "none", "target_stage": "paper", "transition_type": "activate",
            "runtime_action": "deploy_new_binding", "status": "approved" if i % 4 else "draft",
            "created_at": TS, "runtime_binding_id": f"rb-{i}",
            "deployment_saga_id": f"saga-{i}",
            "saga_progress": {"saga_id": f"saga-{i}", "progress_status": "blocked" if i % 3 else "completed",
                              "saga_status": "awaiting_binding", "current_step": "binding_requested",
                              "blocked_reason": "runtime-manager unavailable" if i % 3 else None,
                              "dlq_count": i % 2, "pending_event_count": 0},
            "metadata": {"artifact": {"parameters": {f"parameter-{j}": "p" * 200 for j in range(24)}},
                         "evidence_refs": [f"evidence-{i}-{j}" for j in range(12)]},
        })
        approvals.append({"decision_id": f"approval-{i}", "outcome": "rejected" if i % 5 == 0 else "approved",
                          "state": "decided", "reviewer": "risk-committee", "decided_at": TS})
        bindings.append({"id": f"rb-{i}", "binding_id": f"rb-{i}", "runtime_binding_id": f"rb-{i}",
                         "runtime_id": f"runtime-{i}", "plan_id": f"plan-{i}", "deployment_mode": "paper",
                         "status": "active", "artifact_id": f"artifact-{i}", "artifact_version": "v1.0.0",
                         "metadata": {"parameters": {f"parameter-{j}": "b" * 100 for j in range(24)}}})
        if i % 3 == 0:
            sessions.append({"session_id": f"session-{i}", "runtime_id": f"runtime-{i}",
                             "binding_id": f"rb-{i}", "active": i % 2 == 0,
                             "last_heartbeat_at": TS, "terminal_reason": "stale" if i % 2 else None})
        if i % 3 == 1:
            telemetry.append({"runtime_id": f"runtime-{i}", "last_heartbeat_at": TS,
                              "health_summary": {"feed": "stale" if i % 2 else "ok"}})
    return plans, bindings, approvals, sessions, telemetry


def _store(rows):
    plans, bindings, approvals, sessions, telemetry = rows
    return create_in_memory_read_surface_ports(
        persona_capital_runtime_kwargs={"deployment_plans": plans, "runtime_bindings": bindings},
        ooda_management_kwargs={"approval_decisions": approvals},
        lifecycle_telemetry_governance_kwargs={"telemetry_summaries": telemetry},
        paper_runtime_monitoring_sessions_provider=lambda: sessions,
    )


def _router(store):
    return _build_router(
        DeploymentReadSurfaceAdapter(store), utc_now=lambda: TS,
        page_slice=lambda rows, token, size: (rows[int(token or 0):int(token or 0) + size],
                                             str(int(token or 0) + size) if int(token or 0) + size < len(rows) else None),
        dataset_surface_status=lambda dataset, **kw: _dataset_surface_status(dataset, read_store=store, **kw),
        aggregate_group_surface=_aggregate_group_surface,
    )


def _service(store):
    return DeploymentService(
        queries=DeploymentReadSurfaceAdapter(store), bff_error=lambda *a, **kw: RuntimeError(),
        dataset_surface_status=lambda dataset, **kw: _dataset_surface_status(dataset, read_store=store, **kw),
        composed_surface_status=lambda *a, **kw: {}, aggregate_group_surface=_aggregate_group_surface,
        split_csv_query=lambda value: value.split(",") if value else None,
        snapshot_meta=lambda ts: {"snapshot_at": ts}, surface_degradation_reason=lambda *a, **kw: None,
    )


def _legacy_page(self, plans, *, snapshot_at):
    return [self.deployment_plan_with_stage_truth(plan, snapshot_at=snapshot_at) for plan in plans]


def _legacy_surfaces(self, truths, *, snapshot_at):
    """Pre-change collection algorithm: resolve dataset source for every entry."""
    collected = [self.deployment_stage_truth_surfaces(truth, snapshot_at=snapshot_at) for truth in truths if truth]
    if not collected:
        return {}
    if len(collected) == 1:
        return collected[0]
    surfaces = {}
    for stage in self.deployment_stage_truth_order:
        key = f"{stage}_stage"
        label = stage.replace("_", " ")
        surfaces[key] = self._aggregate_group_surface(
            key, [item[key] for item in collected], snapshot_at=snapshot_at,
            unavailable_message=f"{label} stage truth is unavailable across listed deployment plans.",
            degraded_message=f"{label} stage truth is degraded for one or more listed deployment plans.",
        )
    surfaces["deployment_stage_truth"] = self._aggregate_group_surface(
        "deployment_stage_truth", [surfaces[f"{stage}_stage"] for stage in self.deployment_stage_truth_order],
        snapshot_at=snapshot_at, unavailable_message="Deployment stage truth is unavailable.",
        degraded_message="Deployment stage truth is degraded because one or more stages need evidence or attention.",
    )
    return surfaces


@pytest.mark.parametrize("url", [
    "/bff/deployments?page_size=200", "/bff/deployments?page_size=1",
    "/bff/deployments?page_size=7&page_token=20&status=approved",
    "/bff/deployments?page_size=200&status=absent",
])
@pytest.mark.parametrize("surface_state", ["fresh", "degraded", "unavailable"])
def test_list_matches_legacy_response_field_for_field(monkeypatch, url, surface_state):
    monkeypatch.setenv("BFF_READ_SURFACE_STATE", surface_state)
    monkeypatch.setattr(persona_service, "utc_now", lambda: TS)
    rows = _fixture(200)
    # Exercise missing IDs, fallback matching, a missing binding, and a pending approval.
    rows[0][0].pop("runtime_binding_id")
    rows[0][1]["runtime_binding_id"] = "unknown"
    rows[0][2]["runtime_binding_id"] = " rb-3 "  # explicit ID wins over plan match
    rows[0][3]["approval_decision_id"] = "unknown"
    rows[1].pop(4)
    store = _store(rows)
    app = FastAPI()
    app.include_router(_router(store))
    with TestClient(app) as client:
        with request_read_scope():
            actual = client.get(url)
        with patch.object(DeploymentService, "deployment_plans_with_stage_truth", _legacy_page), patch.object(
            DeploymentService, "deployment_stage_truth_collection_surfaces", _legacy_surfaces,
        ), request_read_scope():
            expected = client.get(url)
        assert actual.status_code == expected.status_code == 200
        assert actual.json() == expected.json()
        assert actual.json()["data"] == actual.json()["items"]


def _read_counts(count, *, targeted=False):
    store = _store(_fixture(200))
    if targeted:
        approvals = _fixture(200)[2]
        store.ooda_management.review_queue._approval_decisions_by_id_reader = (
            lambda ids: [row for row in approvals if row["decision_id"] in ids]
        )
    counts = Counter()
    methods = {
        "store": (store, ("list_deployment_plans", "get_approval_decisions", "list_runtime_bindings",
                           "list_paper_runtime_monitoring_sessions", "list_telemetry_summaries", "dataset_source",
                           "get_approval_decision", "get_runtime_binding", "get_paper_runtime_monitoring_session",
                           "get_telemetry_summary")),
        "runtime": (store.persona_capital_runtime.runtime, ("_get_raw_bindings",)),
        "approval": (store.ooda_management.review_queue, ("_approval_decisions_reader",)),
    }
    if targeted:
        methods["approval"] = (store.ooda_management.review_queue,
                               ("_approval_decisions_reader", "_approval_decisions_by_id_reader"))
    def counted(name, fn):
        def wrapped(*args, **kwargs):
            counts[name] += 1
            return fn(*args, **kwargs)
        return wrapped
    with ExitStack() as stack:
        for prefix, (obj, names) in methods.items():
            for name in names:
                stack.enter_context(patch.object(obj, name, counted(f"{prefix}.{name}", getattr(obj, name))))
        app = FastAPI()
        app.include_router(_router(store))
        with TestClient(app) as client, request_read_scope():
            response = client.get(f"/bff/deployments?page_size={count}")
            assert response.status_code == 200
            assert len(response.json()["items"]) == count
    return counts


@pytest.mark.parametrize("targeted", [False, True])
def test_200_unique_plans_have_constant_owner_and_store_reads(targeted):
    small, large = _read_counts(20, targeted=targeted), _read_counts(200, targeted=targeted)
    assert small == large
    assert large["store.get_approval_decisions"] == 1
    for name in ("get_approval_decision", "get_runtime_binding", "get_paper_runtime_monitoring_session", "get_telemetry_summary"):
        assert large[f"store.{name}"] == 0
    assert large["store.list_runtime_bindings"] == 1
    assert large["runtime._get_raw_bindings"] == 2  # data + one source-status observation
    assert large["store.dataset_source"] == 7  # list source plus six distinct stage datasets
    if targeted:
        assert large["approval._approval_decisions_by_id_reader"] == 1


def test_binding_and_monitoring_precedence_and_telemetry_fallback():
    rows = _fixture(8)
    # Earlier runtime match must beat a later binding match, even if binding is explicit.
    rows[3][:] = [
        {"session_id": "first", "runtime_id": "runtime-0", "active": False},
        {"session_id": "second", "binding_id": "rb-0", "active": True},
    ]
    rows[1].insert(0, dict(rows[1][0], id="first-alias", runtime_binding_id="rb-0"))
    service = _service(_store(rows))
    before = json.dumps(rows, sort_keys=True)
    actual = service.deployment_plans_with_stage_truth(rows[0], snapshot_at=TS)
    expected = _legacy_page(service, rows[0], snapshot_at=TS)
    assert actual == expected
    assert actual[0]["stage_truth"]["runtime_fleet"]["source_id"] == "first"
    assert actual[1]["stage_truth"]["runtime_fleet"]["source_dataset"] == "telemetry_summaries"
    assert actual[2]["stage_truth"]["runtime_fleet"]["available"] is False
    assert json.dumps(rows, sort_keys=True) == before


def test_batch_decisions_preserve_targeted_raw_then_queue_first_match_precedence():
    store = _store(_fixture(0))
    queue = store.ooda_management.review_queue
    queue._approval_decisions_by_id_reader = lambda ids: [{"decision_id": "exact", "outcome": "rejected"}]
    queue._approval_decisions_reader = lambda: [{"decision_id": "exact", "outcome": "approved"},
                                              {"id": "raw", "outcome": "approved"}]
    store.ooda_management.list_approval_queue_items = lambda: [
        {"item_id": "review-queued", "outcome": "rejected"},
        {"decision_id": "queued", "outcome": "approved"},
    ]
    ids = ["exact", "raw", "queued", "review-queued", "missing", ""]
    expected = {key: row for key in ids if (row := store.get_approval_decision(key)) is not None}
    assert store.get_approval_decisions(ids) == expected
    def fail(ids):
        raise TimeoutError("owner read failed")
    queue._approval_decisions_by_id_reader = fail
    expected = {key: row for key in ids if (row := store.get_approval_decision(key)) is not None}
    assert store.get_approval_decisions(ids) == expected


def test_empty_and_unlinked_pages_skip_unneeded_runtime_evidence():
    store = _store(_fixture(1))
    store.persona_capital_runtime.runtime._runtime_bindings_provider = lambda: []
    service = _service(store)
    with patch.object(store, "list_paper_runtime_monitoring_sessions", side_effect=AssertionError("unexpected monitoring read")), patch.object(
        store, "list_telemetry_summaries", side_effect=AssertionError("unexpected telemetry read"),
    ):
        assert service.deployment_plans_with_stage_truth([], snapshot_at=TS) == []
        assert service.deployment_plans_with_stage_truth(_fixture(1)[0], snapshot_at=TS)[0]["stage_truth"]["binding"]["status"] == "missing"


def test_reads_refresh_between_requests_and_do_not_cross_instances():
    rows = _fixture(2)
    service = _service(_store(rows))
    first = service.deployment_plans_with_stage_truth(rows[0], snapshot_at=TS)
    rows[3][0]["active"] = False
    second = service.deployment_plans_with_stage_truth(rows[0], snapshot_at=TS)
    other = _service(_store(_fixture(2))).deployment_plans_with_stage_truth(rows[0], snapshot_at=TS)
    assert first[0]["stage_truth"]["runtime_fleet"]["status"] == "active"
    assert second[0]["stage_truth"]["runtime_fleet"]["status"] == "degraded"
    assert other == first


@pytest.mark.parametrize("owner", ["monitoring", "telemetry"])
def test_owner_failures_propagate_like_the_item_path(owner):
    rows = _fixture(2)
    store = _store(rows)
    def fail():
        raise TimeoutError(f"{owner} unavailable")
    if owner == "monitoring":
        store._paper_runtime_monitoring_sessions_provider = fail
    else:
        telemetry = store.lifecycle_telemetry_governance.telemetry
        telemetry._telemetry_summaries = None
        telemetry._telemetry_summaries_reader = fail
    service = _service(store)
    for compose in (_legacy_page, DeploymentService.deployment_plans_with_stage_truth):
        with pytest.raises(TimeoutError, match=f"{owner} unavailable"), request_read_scope():
            compose(service, rows[0], snapshot_at=TS)


def write_performance_evidence(path):
    """Run explicitly, not in CI: profile 162 real-shaped DTOs and full HTTP p200."""
    rows = _fixture(162)
    store = _store(rows)
    service = _service(store)
    profiler = cProfile.Profile()
    with request_read_scope():
        profiler.runcall(_legacy_page, service, rows[0], snapshot_at=TS)
    stats = pstats.Stats(profiler)
    top = sorted(stats.stats.items(), key=lambda item: item[1][3], reverse=True)[:18]
    profile = [{"function": f"{Path(key[0]).name}:{key[1]}:{key[2]}", "calls": value[1],
                "self_seconds": value[2], "cumulative_seconds": value[3]} for key, value in top]
    app = FastAPI()
    app.include_router(_router(store))
    samples = {"before": [], "after": []}
    with TestClient(app) as client:
        for mode in samples:
            with ExitStack() as stack:
                if mode == "before":
                    stack.enter_context(patch.object(DeploymentService, "deployment_plans_with_stage_truth", _legacy_page))
                    stack.enter_context(patch.object(DeploymentService, "deployment_stage_truth_collection_surfaces", _legacy_surfaces))
                for iteration in range(8):
                    with request_read_scope():
                        start = time.perf_counter()
                        response = client.get("/bff/deployments?page_size=200")
                        assert response.status_code == 200
                        elapsed = time.perf_counter() - start
                    if iteration:
                        samples[mode].append(elapsed)
                    if mode == "before":
                        expected = response.json()
                    else:
                        assert response.json() == expected
        size = len(response.content)
    latency = {mode: {"p50_seconds": statistics.median(values), "p95_seconds": sorted(values)[-1],
                      "samples_seconds": values} for mode, values in samples.items()}
    assert latency["after"]["p95_seconds"] < 2
    evidence = {
        "task_id": "BFF-DEPLOYMENTS-LIST-STAGE-TRUTH-20261114", "owner": "Codex", "reviewer": "Claude2",
        "scope": "Bound deployment list stage-truth reads without changing response contracts or release budgets",
        "measurement": {"kind": "local real-port fixture, not hosted acceptance", "plans": 162, "page_size": 200,
                        "fixture": "_fixture(162) in test_deployments_list_bounded_stage_truth.py; nested owner-shaped DTOs, no artificial latency",
                        "http_response_bytes": size, "warmups_per_mode": 1, "measured_requests_per_mode": 7,
                        "p95_method": "nearest rank (maximum of seven)", "latency": latency,
                        "profile_legacy_per_plan_path": profile},
        "bounded_reads_20": dict(_read_counts(20)), "bounded_reads_200": dict(_read_counts(200)),
        "verification": [], "limitations": ["Local owner-shaped fixtures do not prove hosted latency or deployment acceptance."],
    }
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(evidence, indent=2) + "\n")
    return evidence
