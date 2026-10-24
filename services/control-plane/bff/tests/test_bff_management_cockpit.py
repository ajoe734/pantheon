from __future__ import annotations

import ast
import json
from pathlib import Path
import re
import tempfile
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.management_read_models import create_management_router
from services.control_plane.bff.management_read_models.service import ManagementService
from services.control_plane.bff.ports import create_in_memory_read_surface_ports


OPERATOR_HEADERS = {"Authorization": "Bearer op-b3:operator,reviewer"}

_COCKPIT_FUNCS = None


def _get_cockpit_funcs():
    global _COCKPIT_FUNCS
    if _COCKPIT_FUNCS is None:
        tree_mgmt = ast.parse(Path("services/control-plane/bff/assistant/management_service.py").read_text(encoding="utf-8"))
        tree_main = ast.parse(Path("services/control-plane/bff/main.py").read_text(encoding="utf-8"))
        target_names = {
            "_mgmt_nl_collect_context",
            "_mgmt_nl_filter_tenant_records",
            "_mgmt_nl_record_matches_tenant",
            "_mgmt_nl_record_tenant_ids",
            "_mgmt_nl_scope_values",
            "_mgmt_nl_trading_pulse_snippet",
            "_mgmt_nl_scoped_runtime_rows",
            "_project_operator_runtime_state_row",
            "_project_operator_runtime_state_row_impl",
            "_project_runtime_state_telemetry_summary",
            "_project_runtime_state_monitoring_session",
            "_runtime_state_monitoring_terminal_reason",
            "_project_runtime_state_latest_rollback",
            "_runtime_state_row_health_check",
            "_runtime_state_monitoring_health_check",
            "_derive_runtime_state_row_health",
            "_derive_runtime_state_last_updated_at",
            "_highest_ranked_value",
            "_max_alert_severity",
            "_deployment_review_href",
            "_management_number",
            "_management_avg",
            "_management_count_by",
            "_mgmt_nl_add_record_entities",
            "_mgmt_nl_add_entity",
            "_build_alert_summary",
            "_mgmt_nl_merge_owner_observations",
            "_mgmt_nl_payload_surface_observations",
            "_mgmt_nl_surface_owner_observation",
        }
        _COCKPIT_FUNCS = [
            n for n in tree_mgmt.body
            if isinstance(n, ast.FunctionDef) and n.name in target_names
        ] + [
            n for n in tree_main.body
            if isinstance(n, ast.FunctionDef) and n.name in target_names
        ]
    return _COCKPIT_FUNCS


class _MockContextHost:
    def __init__(self):
        self.store = None
        self._management_ai_context_service = None
        self._build_operator_alerts_payload = lambda _snapshot_at: {"alerts": [], "meta": {"surfaces": {}}}
        self._human_inbox_payload = lambda *args, **kwargs: {"data": {"items": []}, "meta": {"surfaces": {}}}
        self._build_management_anomalies_payload = lambda _snapshot_at: {"items": [], "meta": {"surfaces": {}}}

    def _mgmt_nl_collect_context(self, focus, snapshot_at, tenant_id=None):
        funcs = _get_cockpit_funcs()
        ns = dict(__import__("typing").__dict__)
        ns.update({
            "re": re,
            "json": json,
            "read_store": self.store,
            "_ALERT_SEVERITY_ORDER": {"critical": 4, "high": 3, "medium": 2, "low": 1},
            "_ALERT_CATEGORY_ORDER": {"incident": 4, "kill_switch": 3, "governance": 2, "runtime": 1},
            "_management_ai_context_service": self._management_ai_context_service,
            "_build_operator_alerts_payload": self._build_operator_alerts_payload,
            "_human_inbox_payload": self._human_inbox_payload,
            "_build_management_anomalies_payload": self._build_management_anomalies_payload,
            "utc_now": lambda: snapshot_at,
        })
        mod = ast.Module(body=funcs, type_ignores=[])
        exec(compile(mod, "main_cockpit.py", "exec"), ns)
        if "_project_operator_runtime_state_row_impl" in ns:
            ns["_project_operator_runtime_state_row"] = ns["_project_operator_runtime_state_row_impl"]
            ns["_PROJECT_OPERATOR_RUNTIME_STATE_ROW_FN"] = ns["_project_operator_runtime_state_row_impl"]
        return ns["_mgmt_nl_collect_context"](focus, snapshot_at, tenant_id)


mock_context_host = _MockContextHost()


def _seeded_client(td: str, service_cls: type = ManagementService, **router_kwargs) -> TestClient:
    store = create_in_memory_read_surface_ports()
    store.list_incidents = lambda **kwargs: [
        {
            "incident_id": "inc-b3-001",
            "title": "Runtime drawdown breach",
            "severity": "high",
            "status": "open",
            "created_at": "2026-05-23T08:01:00Z",
            "runtime_id": "runtime-b3-001",
        }
    ]
    store.list_governance_review_queue_items = lambda **kwargs: [
        {
            "item_id": "review-b3-001",
            "item_type": "DeploymentPlan",
            "risk_level": "medium",
            "status": "pending",
            "priority": 2,
            "sla_expires_at": "2026-05-23T12:00:00Z",
            "submitted_at": "2026-05-23T08:00:00Z",
            "summary": "Review pending deployment plan",
            "actions": ["approve", "reject"],
            "stage": "paper",
        }
    ]
    store.list_approval_queue_items = lambda **kwargs: [
        {
            "item_id": "approval-b3-001",
            "decision_id": "approval-b3-001",
            "id": "approval-b3-001",
            "item_type": "Action",
            "risk_level": "high",
            "status": "pending",
            "priority": 1,
            "sla_expires_at": "2026-05-23T10:00:00Z",
            "submitted_at": "2026-05-23T07:30:00Z",
            "summary": "Approve risk threshold expansion",
            "actions": ["approve", "reject"],
        }
    ]
    store.get_kill_switch = lambda **kwargs: {
        "state": "active",
        "engaged": False,
        "mode": "normal",
        "actor": "system",
        "reason": "nominal",
        "updated_at": "2026-05-23T08:00:00Z",
    }
    store.list_runtime_bindings = lambda **kwargs: [
        {
            "binding_id": "binding-b3-001",
            "runtime_id": "runtime-b3-001",
            "persona_id": "persona-b3-001",
            "stage": "paper",
            "deployment_stage": "paper",
            "status": "active",
            "capital_pool_id": "pool-b3-001",
            "capital_allocated": 50000.0,
            "leverage_limit": 2.0,
            "created_at": "2026-05-23T08:00:00Z",
        }
    ]
    store.get_telemetry_summary = lambda runtime_id: {
        "runtime_id": runtime_id,
        "window": "24h",
        "collected_at": "2026-05-23T08:05:00Z",
        "pnl": 0.42,
        "drawdown": 0.11,
        "sharpe_ratio": 1.85,
        "fill_rate": 0.98,
        "avg_slippage_bps": 2.1,
        "total_trades": 120,
        "metrics": {
            "pnl": 0.42,
            "drawdown": 0.11,
            "sharpe_ratio": 1.85,
            "fill_rate": 0.98,
            "avg_slippage_bps": 2.1,
            "total_trades": 120,
        },
    }
    store.list_telemetry_summaries = lambda: [
        store.get_telemetry_summary("runtime-b3-001")
    ]
    store.get_paper_runtime_monitoring_session = lambda **kwargs: {
        "session_id": "session-b3-001",
        "runtime_id": "runtime-b3-001",
        "binding_id": "binding-b3-001",
        "status": "running",
        "active": True,
        "started_at": "2026-05-23T08:00:00Z",
        "drift_detected": False,
    }
    store.list_paper_runtime_monitoring_sessions = lambda: [
        store.get_paper_runtime_monitoring_session()
    ]
    store.get_paper_live_drift_report = lambda **kwargs: {
        "report_id": "drift-b3-001",
        "runtime_id": "runtime-b3-001",
        "status": "watch",
        "drift_detected": True,
        "generated_at": "2026-05-23T08:05:00Z",
        "paper_live_drift": {"available": True},
        "threshold_evaluation": {"overall_status": "watch"},
    }
    store.list_paper_live_drift_reports = lambda: [
        store.get_paper_live_drift_report()
    ]
    store.get_rollbacks = lambda runtime_id: [
        {
            "rollback_id": "rb-b3-001",
            "runtime_id": runtime_id,
            "status": "completed",
            "initiated_at": "2026-05-23T07:45:00Z",
            "completed_at": "2026-05-23T07:50:00Z",
        }
    ]
    store.dataset_source = lambda dataset: {
        "incidents": "service_store",
        "governance_review_queue_items": "service_store",
        "approval_queue_items": "service_store",
        "kill_switch": "service_store",
        "runtime_bindings": "canonical",
        "telemetry_summaries": "service_store",
        "paper_runtime_monitoring_sessions": "service_store",
        "paper_live_drift_reports": "service_store",
        "rollbacks": "service_store",
    }.get(dataset, "missing")

    app = FastAPI()
    service = service_cls(get_read_store=lambda: store)
    app.include_router(create_management_router(get_read_store=lambda: store, service=service, **router_kwargs))
    return TestClient(app)


def test_bff_management_cockpit_composes_required_sections() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _seeded_client(td)
        response = client.get("/bff/management/cockpit", headers=OPERATOR_HEADERS)
        assert response.status_code == 200, response.text
        payload = response.json()
        data = payload["data"]

        assert data["id"] == "management-cockpit"
        assert set(payload) == {"data", "meta"}
        assert "operator_home" not in payload
        assert "runtime_health" not in payload
        assert "operatorHome" not in data
        assert "runtimeHealth" not in data
        assert data["alerts"]["summary"]["total_active"] >= 1
        inbox_summary = data["human_inbox"]["data"]["summary"]
        assert inbox_summary["total"] >= 3
        assert inbox_summary["governance_review_count"] == 1
        assert inbox_summary["approval_count"] == 1
        assert inbox_summary["incident_count"] == 1
        assert data["trading_pulse"]["summary"]["runtime_count"] == 1
        assert data["trading_pulse"]["summary"]["total_pnl"] == 0.42
        assert data["trading_pulse"]["summary"]["baseline_comparison_count"] == 1
        assert data["trading_pulse"]["rankings"][0]["runtime_id"] == "runtime-b3-001"
        assert (
            data["trading_pulse"]["baseline_comparisons"][0]["status"]
            == "watch"
        )
        assert data["anomalies"]["summary"]["total"] >= 2
        assert payload["meta"]["surfaces"]["management_cockpit"]["status"] in {
            "ok",
            "degraded",
        }
        assert "management_human_inbox" not in payload["meta"]["surfaces"]
        assert payload["meta"]["surfaces"]["human_inbox"]["status"] in {
            "ok",
            "degraded",
        }
        assert payload["meta"]["surfaces"]["trading_pulse"]["status"] == "ok"


def test_bff_management_cockpit_requires_read_auth() -> None:
    app = FastAPI()
    app.include_router(create_management_router(get_read_store=lambda: create_in_memory_read_surface_ports()))
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/bff/management/cockpit")

    assert response.status_code == 401


@pytest.fixture
def _healthy_cockpit_context(monkeypatch):
    """MGMT-READ-001 seventh review: a healthy runtime/telemetry read with
    every alerts/human-inbox/anomalies payload's own meta.surfaces empty, so
    the only failure signal in each parametrized case below comes from the
    single contributing surface under test."""
    binding = dict(
        runtime_id="r1",
        owner="runtime-owner",
        status="ok",
        source_kind="live",
        source_version="rv1",
        tenant_id="tenant-a",
    )
    telemetry = dict(
        runtime_id="r1", owner="telemetry-owner", status="ok", source_kind="live", source_version="tv1", tenant_id="tenant-a"
    )
    store = SimpleNamespace(
        list_runtime_bindings=lambda: [binding],
        get_telemetry_summary=lambda _runtime_id: telemetry,
        get_paper_runtime_monitoring_session=lambda **_kwargs: None,
        get_rollbacks=lambda _runtime_id: [],
    )
    monkeypatch.setattr(mock_context_host, "store", store)
    monkeypatch.setattr(mock_context_host, "_management_ai_context_service", ManagementService(read_store=store))
    monkeypatch.setattr(mock_context_host, "_build_operator_alerts_payload", lambda _snapshot_at: {"alerts": [], "meta": {"surfaces": {}}})
    monkeypatch.setattr(mock_context_host, "_human_inbox_payload", lambda *args, **kwargs: {"data": {"items": []}, "meta": {"surfaces": {}}})
    monkeypatch.setattr(mock_context_host, "_build_management_anomalies_payload", lambda _snapshot_at: {"items": [], "meta": {"surfaces": {}}})


@pytest.mark.parametrize(
    "helper,rows_key,surface_key",
    [
        ("_build_operator_alerts_payload", "alerts", "incident_feed"),
        ("_human_inbox_payload", "data", "approval_queue"),
        ("_build_management_anomalies_payload", "items", "incidents"),
    ],
)
def test_cockpit_preserves_non_runtime_owner_unavailability(
    _healthy_cockpit_context, monkeypatch, helper, rows_key, surface_key
) -> None:
    """MGMT-READ-001 seventh review: management_cockpit previously merged
    only runtime/telemetry owner observations, so an unavailable
    incident_feed/approval_queue/incidents surface was reported as
    status=ok while its failed owner and reason were silently dropped."""
    failure = dict(
        status="unavailable",
        source="missing",
        owner=surface_key + "-owner",
        message=surface_key + " offline",
        source_version="v-failed",
    )
    payload = {
        rows_key: {"items": []} if rows_key == "data" else [],
        "meta": {"surfaces": {surface_key: failure}},
    }
    monkeypatch.setattr(mock_context_host, helper, lambda *args, **kwargs: payload)

    result = mock_context_host._mgmt_nl_collect_context("cockpit", "2026-09-08T18:00:00Z", "tenant-a")

    surface = result["surfaces"]["management_cockpit"]
    assert surface["status"] != "ok", surface
    assert surface_key + " offline" in json.dumps(surface), surface


@pytest.mark.parametrize(
    "focus,surface_key",
    [
        ("trading_pulse", "management_trading_pulse"),
        ("cockpit", "management_cockpit"),
    ],
)
def test_nonraising_unavailable_rollback_owner_is_preserved(
    _healthy_cockpit_context, monkeypatch, focus, surface_key
) -> None:
    """MGMT-READ-001 eighth review: get_context_rollbacks previously
    returned status=ok/source_kind=live after any non-raising read,
    discarding a rollback record that itself reported
    status=unavailable/owner=rollback-owner. Both trading_pulse and cockpit
    surfaces must surface that failed owner's full provenance instead of
    reporting ok."""
    row = dict(
        runtime_id="r1",
        id="rollback-1",
        owner="rollback-owner",
        status="unavailable",
        source_kind="unavailable",
        source_version="rollback-v7",
        observed_at="2026-09-08T18:00:00Z",
        correlation_id="rollback-correlation",
        degradation_reason="rollback owner offline",
        tenant_id="tenant-a",
    )
    monkeypatch.setattr(mock_context_host.store, "get_rollbacks", lambda _runtime_id: [row])

    result = mock_context_host._mgmt_nl_collect_context(focus, "2026-09-08T18:00:00Z", "tenant-a")

    surface = result["surfaces"][surface_key]
    assert surface["status"] == "unavailable", surface
    encoded = json.dumps(surface)
    for value in ("rollback-owner", "rollback-v7", "rollback-correlation", "rollback owner offline"):
        assert value in encoded, surface


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_version", "incident-v7"),
        ("observed_at", "2026-09-08T18:00:00Z"),
        ("correlation_id", "incident-correlation"),
    ],
)
def test_cockpit_surface_owner_observation_preserves_provenance_fields(
    _healthy_cockpit_context, monkeypatch, field, value
) -> None:
    """MGMT-READ-001 eighth review: _mgmt_nl_surface_owner_observation
    reconstructed a partial untyped dict that dropped source_version,
    observed_at and correlation_id from an incident/approval
    surface observation. Every contributing owner observation must carry
    the full ManagementObservation provenance fields through cockpit
    conversion."""
    row = dict(
        status="unavailable",
        source="missing",
        owner="incident-owner",
        message="incident owner offline",
        source_version="incident-v7",
        observed_at="2026-09-08T18:00:00Z",
        correlation_id="incident-correlation",
    )
    payload = {"alerts": [], "meta": {"surfaces": {"incident_feed": row}}}
    monkeypatch.setattr(mock_context_host, "_build_operator_alerts_payload", lambda _snapshot_at: payload)

    result = mock_context_host._mgmt_nl_collect_context("cockpit", "2026-09-08T18:00:00Z", "tenant-a")

    surface = result["surfaces"]["management_cockpit"]
    observation = next(
        obs
        for obs in surface["owner_observation"]["contributing_observations"]
        if obs.get("owner") == "incident-owner"
    )
    assert observation[field] == value



def test_cockpit_computes_operator_health_once_and_keeps_operator_home() -> None:
    calls = []

    class _SpyService(ManagementService):
        def get_operator_health_status(self, *args, **kwargs):
            calls.append(1)
            return super().get_operator_health_status(*args, **kwargs)

    with tempfile.TemporaryDirectory() as td:
        response = _seeded_client(td, _SpyService).get("/bff/management/cockpit", headers=OPERATOR_HEADERS)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert len(calls) >= 1
    assert len(calls) == 1
    assert data["operator_home"]["meta"]["surfaces"]["operator_home"]
    assert data["runtime_health"]["groups"]


def test_cockpit_contributor_exception_degrades_only_that_section() -> None:
    class _BoomService(ManagementService):
        def get_management_anomalies(self, *args, **kwargs):
            raise RuntimeError("secret-token-abc")

    with tempfile.TemporaryDirectory() as td:
        response = _seeded_client(td, _BoomService).get("/bff/management/cockpit", headers=OPERATOR_HEADERS)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "secret-token-abc" not in response.text
    surfaces = payload["meta"]["surfaces"]
    assert surfaces["management_anomalies"]["status"] == "unavailable"
    assert surfaces["management_anomalies"]["reason"] == "section_error"
    assert payload["data"]["alerts"]["summary"]["total_active"] >= 1
    assert payload["data"]["trading_pulse"]["summary"]["runtime_count"] == 1
    assert surfaces["management_cockpit"].get("reason") != "read_timeout"


@pytest.mark.parametrize(
    "exc_name,reason",
    [
        ("ManagementReadTimeout", "read_timeout"),
        ("ManagementReadSaturated", "read_capacity_saturated"),
        ("RuntimeError", "read_error"),
    ],
)
def test_cockpit_failure_reason_is_not_always_timeout(exc_name, reason) -> None:
    from services.control_plane.bff.personas.routes import common

    exc_type = getattr(common, exc_name, RuntimeError)

    async def _failing_read(*_args, **_kwargs):
        raise exc_type()

    app = FastAPI()
    app.include_router(
        create_management_router(
            get_read_store=lambda: create_in_memory_read_surface_ports(),
            run_management_read=_failing_read,
        )
    )
    response = TestClient(app).get("/bff/management/cockpit", headers=OPERATOR_HEADERS)
    assert response.status_code == 200, response.text
    assert response.json()["meta"]["surfaces"]["management_cockpit"]["reason"] == reason


def test_incident_owner_list_is_fetched_once_per_request_and_not_across_requests() -> None:
    import json as _json
    from services.control_plane.bff.core.owner_reads import request_read_scope, selected_tenant
    from services.control_plane.bff.ports.lifecycle_telemetry_governance import DomainIncidentPort

    fetched = []

    class _Resp:
        def __init__(self, body):
            self._b = _json.dumps(body).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return self._b

    def _opener(req, timeout=None):
        fetched.append(req.full_url)
        return _Resp([{"incident_id": "inc-retired", "status": "closed"}, {"incident_id": "inc-active", "status": "open"}])

    port = DomainIncidentPort(incidents_api_url="http://incidents.test", opener=_opener)
    with request_read_scope():
        tok = selected_tenant.set("tenant-a")
        try:
            for _ in range(5):
                assert len(port.list_incidents()) == 2
            assert [i["incident_id"] for i in port.list_incidents(status="open")] == ["inc-active"]
            assert len(fetched) == 2  # unfiltered once, status=open once
            selected_tenant.set("tenant-b")
            port.list_incidents()
            assert len(fetched) == 3
        finally:
            selected_tenant.reset(tok)
    with request_read_scope():
        port.list_incidents()
    assert len(fetched) == 4


@pytest.mark.parametrize("blocked_section", ["human_inbox", "anomalies"])
def test_cockpit_timeout_keeps_completed_owner_sections(monkeypatch, blocked_section):
    import threading
    from services.control_plane.bff.personas.routes.common import run_management_read

    entered, release = threading.Event(), threading.Event()

    class SlowSectionService(ManagementService):
        def _block(self):
            entered.set()
            assert release.wait(3), "test must release the original worker"

        def get_human_inbox(self, *args, **kwargs):
            if blocked_section == "human_inbox":
                self._block()
            return super().get_human_inbox(*args, **kwargs)

        def get_management_anomalies(self, *args, **kwargs):
            if blocked_section == "anomalies":
                self._block()
            return super().get_management_anomalies(*args, **kwargs)

    monkeypatch.setenv("PANTHEON_BFF_COCKPIT_READ_TIMEOUT_SECONDS", "0.15")
    with tempfile.TemporaryDirectory() as td:
        client = _seeded_client(td, SlowSectionService, run_management_read=run_management_read)
        try:
            response = client.get("/bff/management/cockpit", headers=OPERATOR_HEADERS)
            assert entered.is_set(), "real composer must reach the blocked section"
            assert response.status_code == 200
            body = response.json()
            assert body["data"]["operator_home"]["cards"]
            assert body["data"]["runtime_health"]["groups"]
            assert body["data"]["trading_pulse"]["summary"]["runtime_count"] == 1
            assert body["meta"]["partial"] is True
            assert body["meta"]["surfaces"]["management_cockpit"]["reason"] == "read_timeout"
            assert body["meta"]["surfaces"]["management_cockpit"]["source"] == "partial_composition"
            missing_key = "human_inbox" if blocked_section == "human_inbox" else "management_anomalies"
            missing = body["meta"]["surfaces"][missing_key]
            assert missing["status"] == "unavailable"
            assert missing["reason"] == "read_timeout"
            before = json.dumps(body, sort_keys=True)
        finally:
            release.set()
        # Late completion must not rewrite the already returned snapshot.
        assert json.dumps(response.json(), sort_keys=True) == before


def test_cockpit_health_counts_canonical_active_binding_not_retired_or_paused():
    store = create_in_memory_read_surface_ports()
    store.list_runtime_bindings = lambda: [
        {"runtime_id": "current-paper", "status": "active"},
        {"runtime_id": "old-paper", "status": "retired"},
        {"runtime_id": "failed-paper", "status": "failed"},
        {"runtime_id": "paused-paper", "status": "paused"},
    ]
    health = ManagementService(read_store=store).get_operator_health_status()
    runtime = next(g for g in health["groups"] if g["group_id"] == "runtime")
    assert runtime["details"] == {"total_runtimes": 4, "active_runtimes": 1}
    assert runtime["summary"] == "1 active runtime(s) running."


def _build_reload_owner_store(monkeypatch, per_owner_delay: float, call_counts: dict[str, int]):
    """Production owner ports over fake owner HTTP reads with per-read latency."""
    import threading
    import time

    from services.control_plane.bff.core.owner_reads import (
        OwnerReadContextMiddleware,
        cached_owner_call,
        create_owner_domain_ports,
        selected_tenant,
    )
    from services.control_plane.bff.personas.routes.common import run_management_read
    from services.control_plane.bff.ports.lifecycle_telemetry_governance import (
        CompositeLifecycleTelemetryGovernancePort,
        DomainGovernancePort,
        DomainIncidentPort,
        DomainLifecyclePort,
        DomainLineagePort,
        DomainTelemetryPort,
    )
    from services.control_plane.bff.ports.ooda_management import (
        ManagementReviewQueuePort,
        OodaManagementDomainPort,
    )
    from services.control_plane.bff.ports.read_surface_ports import ReadSurfacePorts

    count_lock = threading.Lock()

    def _record_call(name: str, result: Any) -> Any:
        time.sleep(per_owner_delay)
        with count_lock:
            call_counts[name] = call_counts.get(name, 0) + 1
        return result

    def _fake_read_records(_url_builder, path: str, _key: Any = None):
        payloads = {
            "/api/runtime-bindings": [{"binding_id": "rb-1", "runtime_id": "rt-1", "persona_id": "p-1", "status": "active", "stage": "paper"}],
            "/api/capital-pools": [{"pool_id": "pool-1", "id": "pool-1", "status": "active"}],
            "/api/bindings": [{"binding_id": "cb-1", "id": "cb-1", "persona_id": "p-1", "pool_id": "pool-1", "status": "active"}],
            "/api/deployment/plans": [{"plan_id": "dp-1", "id": "dp-1", "persona_id": "p-1", "status": "pending", "stage": "paper"}],
            "/api/telemetry/runtime-summaries": [{"runtime_id": "rt-1", "pnl": 0.12, "drawdown": 0.03, "sharpe_ratio": 1.4, "fill_rate": 0.99}],
        }
        return cached_owner_call(
            ("http_records", path, selected_tenant.get()),
            lambda: _record_call(path, payloads.get(path, [])),
        )

    def _fake_read_evo(_url_builder, path: str, _key: Any = None):
        return cached_owner_call(
            ("evo_records", path, selected_tenant.get()),
            lambda: _record_call(path, [{"decision_id": "evo-1", "id": "evo-1", "status": "pending"}]),
        )

    def _fake_approval_records():
        return cached_owner_call(
            ("approval_records", selected_tenant.get()),
            lambda: _record_call(
                "/api/governance/approvals",
                [{"decision_id": "ap-1", "id": "ap-1", "decision_state": "pending", "status": "pending", "priority": "high"}],
            ),
        )

    monkeypatch.setattr("services.control_plane.bff.core.owner_reads.read_records", _fake_read_records)
    monkeypatch.setattr("services.control_plane.bff.core.owner_reads.read_evolution_records", _fake_read_evo)
    monkeypatch.setattr("services.control_plane.bff.core.owner_reads.approval_records", _fake_approval_records)
    monkeypatch.setenv("PANTHEON_EVOLUTION_API_URL", "http://evolution.test")

    class _PersonaOwnerStore:
        def list_personas(self):
            return _record_call(
                "/api/personas",
                [{"persona_id": "p-1", "id": "p-1", "name": "Alpha", "lifecycle_state": "active", "status": "active"}],
            )

    pcr = create_owner_domain_ports(persona_store=_PersonaOwnerStore())
    gov_port = DomainGovernancePort()

    class _IncResp:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def read(self):
            return json.dumps(
                _record_call(
                    "/api/incidents",
                    [{"incident_id": "inc-1", "id": "inc-1", "status": "open", "severity": "high", "title": "Drift alert"}],
                )
            ).encode("utf-8")

    inc_port = DomainIncidentPort(
        incidents_api_url="http://incidents.test",
        opener=lambda _req, timeout=None: _IncResp(),
    )
    tel_port = DomainTelemetryPort(
        telemetry_summaries_reader=lambda: _fake_read_records(None, "/api/telemetry/runtime-summaries", "summaries"),
    )
    ltg = CompositeLifecycleTelemetryGovernancePort(
        lifecycle_port=DomainLifecyclePort(),
        telemetry_port=tel_port,
        incident_port=inc_port,
        governance_port=gov_port,
        lineage_port=DomainLineagePort(),
    )
    ooda = OodaManagementDomainPort(
        review_queue_port=ManagementReviewQueuePort(
            deployment_plans_reader=pcr.list_deployment_plans,
            approval_decisions_reader=_fake_approval_records,
            evolution_decisions_reader=pcr.list_evolution_decisions,
        ),
    )
    store = ReadSurfacePorts(
        persona_capital_runtime=pcr,
        ooda_management=ooda,
        lifecycle_telemetry_governance=ltg,
        paper_runtime_monitoring_sessions_provider=lambda: [],
    )
    return store


def test_cockpit_reload_parallel_owner_reads_prevent_serial_waterfall_timeout(monkeypatch) -> None:
    """BFF-COCKPIT-RELOAD-20261021: get_operator_health_status and get_management_cockpit
    must execute independent owner reads in parallel within request_read_scope rather
    than in a 6-to-9-step serial waterfall that exceeds the cockpit timeout on reload."""
    from concurrent.futures import ThreadPoolExecutor
    import threading
    import time

    from services.control_plane.bff.core.owner_reads import OwnerReadContextMiddleware
    from services.control_plane.bff.personas.routes.common import run_management_read

    call_counts: dict[str, int] = {}
    store = _build_reload_owner_store(monkeypatch, 0.10, call_counts)

    # Serial execution of the 9 owner reads would take >= 0.90s (and health alone >= 0.60s).
    # With a 0.45s cockpit budget, serial execution always timed out with empty operator_home/runtime_health.
    monkeypatch.setenv("PANTHEON_BFF_COCKPIT_READ_TIMEOUT_SECONDS", "0.45")
    monkeypatch.setenv("PANTHEON_BFF_MANAGEMENT_READ_TIMEOUT_SECONDS", "0.45")

    app = FastAPI()
    app.add_middleware(OwnerReadContextMiddleware)
    svc = ManagementService(get_read_store=lambda: store)
    app.include_router(
        create_management_router(
            get_read_store=lambda: store,
            service=svc,
            run_management_read=run_management_read,
        )
    )
    client = TestClient(app)

    # 1. Single cockpit request completes well within 0.45s and deduplicates every owner read.
    resp = client.get("/bff/management/cockpit", headers=OPERATOR_HEADERS)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["meta"]["surfaces"]["management_cockpit"].get("reason") != "read_timeout"
    assert body["data"]["operator_home"]["cards"]
    assert len(body["data"]["runtime_health"]["groups"]) == 5
    assert body["data"]["trading_pulse"]["summary"]["runtime_count"] == 1
    assert body["data"]["human_inbox"]["data"]["summary"]["total"] >= 2
    assert body["data"]["alerts"]["summary"]["total_active"] >= 1
    for owner_path, count in call_counts.items():
        assert count == 1, f"Expected {owner_path} to be fetched once per request, got {count}"

    # 2. Simulate normal-browser initial mount fanout followed immediately by page.reload() fanout
    # through the production app_factory (compose_bff_app) with main.py's build_cockpit_payload wiring.
    from services.control_plane.bff.bootstrap.dependencies import AppDependencies
    from services.control_plane.bff.core.app_factory import (
        _resolve_default_dependency,
        compose_bff_app,
    )

    from collections import defaultdict, deque

    sse_bufs: dict[str, Any] = defaultdict(lambda: deque(maxlen=100))
    sse_subs: dict[str, Any] = defaultdict(list)

    def _test_dep_resolver(name: str, deps: Any) -> Any:
        if name == "_sse_buffers":
            return sse_bufs
        if name == "_sse_subscribers":
            return sse_subs
        try:
            return _resolve_default_dependency(name, deps)
        except Exception:
            return None

    app_deps = AppDependencies.create_default(read_surface=store)
    composed_app = compose_bff_app(
        app_deps=app_deps,
        dependency_resolver=_test_dep_resolver,
        run_management_read=run_management_read,
        build_cockpit_payload=lambda snapshot_at=None, human_inbox=None, **kw: ManagementService(
            get_read_store=lambda: app_deps.read_surface,
        ).get_management_cockpit(snapshot_at=snapshot_at, human_inbox=human_inbox, **kw),
    )
    composed_client = TestClient(composed_app)
    paths = [
        "/bff/management/shell-summary",
        "/bff/management/cockpit",
        "/bff/management/human-inbox",
        "/bff/management/trading-pulse",
    ]
    for _wave in range(2):
        with ThreadPoolExecutor(max_workers=len(paths)) as pool:
            futures = {path: pool.submit(composed_client.get, path, headers=OPERATOR_HEADERS) for path in paths}
            results = {path: fut.result() for path, fut in futures.items()}
        for path, r in results.items():
            assert r.status_code == 200, f"{path} failed: {r.text}"
        cockpit_payload = results["/bff/management/cockpit"].json()
        assert cockpit_payload["meta"]["surfaces"]["management_cockpit"].get("reason") != "read_timeout"
        assert cockpit_payload["data"]["operator_home"]["cards"]
        assert len(cockpit_payload["data"]["runtime_health"]["groups"]) == 5
        inbox_payload = results["/bff/management/human-inbox"].json()
        assert inbox_payload["meta"]["surfaces"]["human_inbox"].get("reason") != "read_timeout"
        assert inbox_payload["data"]["summary"]["total"] >= 2
    assert "_resolve_store" not in svc.__dict__


def test_cached_owner_call_deduplicates_concurrent_same_key_and_expires_inner_deadline() -> None:
    """Concurrent threads sharing a request_read_scope must deduplicate identical
    owner keys without races, and expired management_read_deadline must fail fast."""
    from concurrent.futures import ThreadPoolExecutor
    import threading
    import time

    from services.control_plane.bff.core.owner_reads import cached_owner_call, request_read_scope
    from services.control_plane.bff.personas.routes.common import (
        management_read_deadline,
        remaining_management_read_budget,
    )

    invocations = 0
    lock = threading.Lock()

    def _expensive():
        nonlocal invocations
        time.sleep(0.05)
        with lock:
            invocations += 1
        return {"ok": True}

    with request_read_scope():
        with ThreadPoolExecutor(max_workers=6) as pool:
            futs = [
                pool.submit(__import__("contextvars").copy_context().run, cached_owner_call, ("same-key", "t1"), _expensive)
                for _ in range(6)
            ]
            vals = [f.result() for f in futs]
    assert vals == [{"ok": True}] * 6
    assert invocations == 1

    store = create_in_memory_read_surface_ports()
    persona_called = False

    def _unexpected_persona_call(**_kwargs):
        nonlocal persona_called
        persona_called = True
        return []

    store.list_personas = _unexpected_persona_call
    svc = ManagementService(read_store=store)
    tok = management_read_deadline.set(time.monotonic() - 0.01)
    try:
        assert remaining_management_read_budget(1.0) == 0.0
        rows, reason = svc._bounded_persona_readiness_rows("2026-10-21T00:00:00Z", store)
        assert rows == []
        assert reason == "read_timeout"
        assert persona_called is False
    finally:
        management_read_deadline.reset(tok)


def test_plain_dict_request_read_cache_supports_nested_and_concurrent_cached_owner_call() -> None:
    """When request_read_cache is set to a plain dict, nested cached_owner_call with
    different or identical keys must not deadlock, must survive >64 concurrent plain-dict
    caches during an in-flight read without evicting the active lock table, and must not
    retain plain-dict caches or cached payloads by module-global strong reference."""
    from concurrent.futures import ThreadPoolExecutor
    import gc
    import threading
    import time
    import weakref

    from services.control_plane.bff.core import owner_reads
    from services.control_plane.bff.core.owner_reads import (
        _key_lock,
        cached_owner_call,
        evict_request_read_keys,
        request_read_cache,
    )

    assert not hasattr(owner_reads, "_PLAIN_DICT_LOCKS")

    class _CachedPayload:
        pass

    payload_ref: weakref.ReferenceType[_CachedPayload] | None = None

    def _run_scoped_plain_cache() -> None:
        nonlocal payload_ref
        plain_cache: dict[Any, Any] = {}
        tok = request_read_cache.set(plain_cache)
        try:
            with ThreadPoolExecutor(max_workers=4) as pool:
                nested_fut = pool.submit(
                    __import__("contextvars").copy_context().run,
                    lambda: cached_owner_call(
                        "outer",
                        lambda: cached_owner_call("inner", lambda: 42),
                    ),
                )
                assert nested_fut.result(timeout=1.0) == 42
            assert plain_cache["inner"] == (True, 42)
            assert plain_cache["outer"] == (True, 42)

            entered = threading.Event()
            release = threading.Event()
            invocations = 0
            lock = threading.Lock()

            def _slow_val() -> _CachedPayload:
                nonlocal invocations
                with lock:
                    invocations += 1
                entered.set()
                assert release.wait(timeout=2.0)
                return _CachedPayload()

            inflight_key = ("concurrent", "k1")
            initial_lock = _key_lock(plain_cache, inflight_key)
            with ThreadPoolExecutor(max_workers=4) as pool:
                fut1 = pool.submit(
                    __import__("contextvars").copy_context().run,
                    cached_owner_call,
                    inflight_key,
                    _slow_val,
                )
                assert entered.wait(timeout=1.0)

                # Churn >64 other plain-dict request caches while fut1 is in-flight.
                for idx in range(80):
                    other_cache: dict[Any, Any] = {}
                    other_tok = request_read_cache.set(other_cache)
                    try:
                        assert cached_owner_call(("other", idx), lambda i=idx: i) == idx
                    finally:
                        request_read_cache.reset(other_tok)

                assert _key_lock(plain_cache, inflight_key) is initial_lock
                evict_request_read_keys(("other",))
                assert _key_lock(plain_cache, inflight_key) is initial_lock

                fut2 = pool.submit(
                    __import__("contextvars").copy_context().run,
                    cached_owner_call,
                    inflight_key,
                    _slow_val,
                )
                time.sleep(0.02)
                with lock:
                    assert invocations == 1
                release.set()
                res1 = fut1.result(timeout=1.0)
                res2 = fut2.result(timeout=1.0)
                assert res1 is res2
                payload_ref = weakref.ref(res1)
                del res1, res2
            assert invocations == 1
            evict_request_read_keys(("concurrent",))
            assert inflight_key not in plain_cache
            assert _key_lock(plain_cache, inflight_key) is initial_lock
        finally:
            request_read_cache.reset(tok)

    _run_scoped_plain_cache()
    gc.collect()
    assert payload_ref is not None
    assert payload_ref() is None, "plain-dict request cache payload must not be retained across requests"


def test_operator_health_status_distinguishes_timeout_from_error() -> None:
    """get_operator_health_status must report read_timeout / management_read_timeout for
    expired deadlines, pending group futures, or TimeoutError, and read_error / error for RuntimeError."""
    import threading
    import time

    from services.control_plane.bff.personas.routes.common import management_read_deadline

    # 1. Pre-expired deadline must not invoke store methods and must surface read_timeout.
    expired_store = create_in_memory_read_surface_ports()
    runtime_called = False

    def _unexpected_runtime() -> list[dict[str, Any]]:
        nonlocal runtime_called
        runtime_called = True
        return []

    expired_store.list_runtime_bindings = _unexpected_runtime
    svc_expired = ManagementService(read_store=expired_store)
    tok = management_read_deadline.set(time.monotonic() - 0.01)
    try:
        expired_health = svc_expired.get_operator_health_status(snapshot_at="2026-10-21T20:39:09Z")
    finally:
        management_read_deadline.reset(tok)
    assert runtime_called is False
    assert expired_health["meta"]["surfaces"]["runtime"] == {
        "status": "unavailable",
        "source": "management_read_timeout",
        "reason": "read_timeout",
    }
    assert expired_health["meta"]["surfaces"]["health_status"]["reason"] == "read_timeout"
    assert expired_health["meta"]["surfaces"]["health_status"]["source"] == "management_read_timeout"

    # 2. Pending future timeout vs RuntimeError vs TimeoutError within the same health evaluation.
    release = threading.Event()
    mixed_store = create_in_memory_read_surface_ports()

    def _hanging_runtime() -> list[dict[str, Any]]:
        release.wait(1.0)
        return [{"runtime_id": "rt-late", "status": "active"}]

    def _timeout_telemetry() -> list[dict[str, Any]]:
        raise TimeoutError("owner telemetry timed out")

    def _boom_incidents() -> list[dict[str, Any]]:
        raise RuntimeError("owner incident failure")

    mixed_store.list_runtime_bindings = _hanging_runtime
    mixed_store.list_telemetry_summaries = _timeout_telemetry
    mixed_store.list_incidents = _boom_incidents
    svc_mixed = ManagementService(read_store=mixed_store)
    tok2 = management_read_deadline.set(time.monotonic() + 0.08)
    try:
        mixed_health = svc_mixed.get_operator_health_status(snapshot_at="2026-10-21T20:39:09Z")
    finally:
        release.set()
        management_read_deadline.reset(tok2)

    surfaces = mixed_health["meta"]["surfaces"]
    assert surfaces["runtime"] == {
        "status": "unavailable",
        "source": "management_read_timeout",
        "reason": "read_timeout",
    }
    assert surfaces["telemetry"] == {
        "status": "unavailable",
        "source": "management_read_timeout",
        "reason": "read_timeout",
    }
    assert surfaces["incident"] == {
        "status": "unavailable",
        "source": "error",
        "reason": "read_error",
    }
    assert surfaces["runtime"] != surfaces["incident"]
    assert surfaces["health_status"]["reason"] == "read_timeout"



def test_submit_owner_read_dedupes_warmers_per_request_and_skips_expired_reads() -> None:
    """BFF-COCKPIT-PARTIAL-READ-20261022: duplicate prefetches and reads queued by an
    abandoned (expired) request must not keep occupying the shared owner-read pool."""
    from concurrent.futures import ThreadPoolExecutor
    import time

    from services.control_plane.bff.core.owner_reads import (
        _RequestReadCache,
        management_read_deadline,
        request_read_cache,
        submit_owner_read,
    )

    ran: list[str] = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        first_token = request_read_cache.set(_RequestReadCache())
        try:
            first = submit_owner_read(pool, lambda: ran.append("a") or "a", warm_key="list_personas")
            assert first is not None and first.result(timeout=2) == "a"
            assert submit_owner_read(pool, lambda: ran.append("dup"), warm_key="list_personas") is None
        finally:
            request_read_cache.reset(first_token)
        # A new request starts with an empty warm marker set (nothing crosses requests).
        second_token = request_read_cache.set(_RequestReadCache())
        try:
            second = submit_owner_read(pool, lambda: ran.append("b") or "b", warm_key="list_personas")
            assert second is not None and second.result(timeout=2) == "b"
        finally:
            request_read_cache.reset(second_token)

        deadline_token = management_read_deadline.set(time.monotonic() - 0.01)
        try:
            expired = submit_owner_read(pool, lambda: ran.append("late"))
        finally:
            management_read_deadline.reset(deadline_token)
        with pytest.raises(TimeoutError):
            expired.result(timeout=2)
    assert ran == ["a", "b"]


def test_cockpit_overlapping_reload_waves_keep_every_section_complete(monkeypatch) -> None:
    """BFF-COCKPIT-PARTIAL-READ-20261022: with production owner ports, request context and
    app factory, overlapping reload waves (cockpit + shell-summary + human-inbox +
    trading-pulse + health + home + alerts) share the 16-worker owner pool. Duplicate
    per-request prefetch tasks queued 100+ reads there and starved the cockpit into
    partial_composition read_timeout with an empty human_inbox while health/home populated."""
    from collections import defaultdict, deque
    from concurrent.futures import ThreadPoolExecutor
    import time

    from services.control_plane.bff.bootstrap.dependencies import AppDependencies
    from services.control_plane.bff.core.app_factory import _resolve_default_dependency, compose_bff_app
    from services.control_plane.bff.personas.routes.common import run_management_read

    store = _build_reload_owner_store(monkeypatch, 0.5, {})
    sse_bufs: dict[str, Any] = defaultdict(lambda: deque(maxlen=100))
    sse_subs: dict[str, Any] = defaultdict(list)

    def _dep_resolver(name: str, deps: Any) -> Any:
        if name == "_sse_buffers":
            return sse_bufs
        if name == "_sse_subscribers":
            return sse_subs
        try:
            return _resolve_default_dependency(name, deps)
        except Exception:
            return None

    app_deps = AppDependencies.create_default(read_surface=store)
    client = TestClient(compose_bff_app(
        app_deps=app_deps,
        dependency_resolver=_dep_resolver,
        run_management_read=run_management_read,
        build_cockpit_payload=lambda snapshot_at=None, human_inbox=None, **kw: ManagementService(
            get_read_store=lambda: app_deps.read_surface,
        ).get_management_cockpit(snapshot_at=snapshot_at, human_inbox=human_inbox, **kw),
    ))
    paths = [
        "/bff/management/shell-summary",
        "/bff/management/cockpit",
        "/bff/management/human-inbox",
        "/bff/management/trading-pulse",
        "/api/v1/operator/health-status",
        "/api/v1/operator/home",
        "/bff/alerts",
    ]
    cockpit_futures = []
    with ThreadPoolExecutor(max_workers=40) as pool:
        for _wave in range(5):
            for path in paths:
                fut = pool.submit(client.get, path, headers=OPERATOR_HEADERS)
                if path.endswith("/cockpit"):
                    cockpit_futures.append(fut)
            time.sleep(0.1)
        responses = [f.result() for f in cockpit_futures]
    for resp in responses:
        assert resp.status_code == 200, resp.text
        body = resp.json()
        surface = body["meta"]["surfaces"]["management_cockpit"]
        assert surface.get("reason") != "read_timeout", surface
        assert surface.get("source") != "partial_composition", surface
        assert body["data"]["operator_home"]["cards"]
        assert len(body["data"]["runtime_health"]["groups"]) == 5
        assert body["data"]["human_inbox"]["data"]["summary"]["total"] >= 2
        assert body["data"]["alerts"]["summary"]["total_active"] >= 1


def test_operator_health_failed_reads_report_unknown_not_zero() -> None:
    """BFF-COCKPIT-HEALTH-SEMANTICS-20261023: failed/timed-out owner reads are unknown, never factual zero."""
    store = create_in_memory_read_surface_ports()

    def _boom(*_a, **_k):
        raise RuntimeError("owner failure")

    def _timeout(*_a, **_k):
        raise TimeoutError("owner timed out")

    store.list_runtime_bindings = _boom
    store.list_telemetry_summaries = _timeout
    store.list_incidents = _boom
    store.list_approval_queue_items = _timeout
    store.get_kill_switch_status = _boom
    health = ManagementService(read_store=store).get_operator_health_status(snapshot_at="2026-10-23T00:00:00Z")
    groups = {g["group_id"]: g for g in health["groups"]}
    assert all(g["status"] == "unavailable" for g in groups.values())
    assert groups["runtime"]["details"]["active_runtimes"] is None
    assert groups["runtime"]["details"]["total_runtimes"] is None
    assert groups["runtime"]["details"]["unavailable_reason"] == "read_error"
    assert "unknown" in groups["runtime"]["summary"]
    assert "No active" not in groups["runtime"]["summary"]
    assert groups["telemetry"]["details"]["telemetry_count"] is None
    assert groups["telemetry"]["details"]["unavailable_reason"] == "read_timeout"
    assert groups["incident"]["details"]["open_incidents"] is None
    assert "No active incidents" not in groups["incident"]["summary"]
    assert groups["governance"]["details"]["pending_items"] is None
    assert "clear" not in groups["governance"]["summary"]
    assert groups["kill_switch"]["details"]["status"] == "unknown"
    assert "armed" not in groups["kill_switch"]["summary"]
    assert health["safe_mode_state"]["active"] is None
    assert health["overall_status"] == "unavailable"
    assert health["group_counts"] == {"ok": 0, "degraded": 0, "unavailable": 5}
    assert health["meta"]["surfaces"]["runtime"]["reason"] == "read_error"


def test_operator_health_successful_empty_reads_are_genuine_zero() -> None:
    store = create_in_memory_read_surface_ports()
    store.list_runtime_bindings = lambda: []
    store.list_telemetry_summaries = lambda: []
    store.list_incidents = lambda: []
    store.list_approval_queue_items = lambda: []
    store.get_kill_switch_status = lambda: {"status": "armed", "safe_mode_status": "off", "active": False}
    health = ManagementService(read_store=store).get_operator_health_status()
    groups = {g["group_id"]: g for g in health["groups"]}
    assert groups["runtime"]["summary"] == "No active runtime bindings."
    assert groups["runtime"]["details"] == {"total_runtimes": 0, "active_runtimes": 0}
    assert groups["telemetry"]["details"] == {"telemetry_count": 0}
    assert groups["incident"]["details"] == {"total_incidents": 0, "open_incidents": 0}
    assert groups["governance"]["summary"] == "Governance queues clear."
    assert groups["governance"]["details"] == {"total_items": 0, "pending_items": 0}
    assert health["overall_status"] == "ok"
    assert health["group_counts"] == {"ok": 5, "degraded": 0, "unavailable": 0}


def test_operator_health_overall_and_counts_match_returned_groups() -> None:
    store = create_in_memory_read_surface_ports()
    store.list_runtime_bindings = lambda: [{"runtime_id": "r1", "status": "active"}]
    store.list_telemetry_summaries = lambda: [{"runtime_id": "r1"}]
    store.list_incidents = lambda: [{"incident_id": "i1", "status": "open"}]
    store.list_approval_queue_items = lambda: [{"id": "a1", "status": "pending"}]
    store.get_kill_switch_status = lambda: {"status": "armed", "safe_mode_status": "off", "active": False}
    health = ManagementService(read_store=store).get_operator_health_status()
    by_status: dict[str, int] = {"ok": 0, "degraded": 0, "unavailable": 0}
    for g in health["groups"]:
        by_status[g["status"]] += 1
    assert by_status == {"ok": 3, "degraded": 2, "unavailable": 0}
    assert health["group_counts"] == by_status
    # #6744 contract: overall status/headline follow the returned groups.
    assert health["overall_status"] == "degraded"
    assert health["headline"] == "Control plane degraded"
    assert health["message"] == "2 of 5 health groups need attention."
    # meta.surfaces stays availability-only.
    assert health["meta"]["surfaces"]["health_status"]["status"] == "ok"
    # Source availability stays separate from business state.
    assert health["meta"]["surfaces"]["incident"] == {"status": "ok", "source": "store"}
    assert health["meta"]["surfaces"]["governance"] == {"status": "ok", "source": "store"}


def test_operator_health_triggered_kill_switch_or_hard_safe_mode_is_not_healthy() -> None:
    for state in (
        {"status": "triggered", "safe_mode_status": "soft", "active": True},
        {"status": "armed", "safe_mode_status": "hard", "active": False},
    ):
        store = create_in_memory_read_surface_ports()
        store.list_runtime_bindings = lambda: []
        store.list_telemetry_summaries = lambda: []
        store.list_incidents = lambda: []
        store.list_approval_queue_items = lambda: []
        store.get_kill_switch_status = lambda state=state: dict(state)
        health = ManagementService(read_store=store).get_operator_health_status()
        assert health["overall_status"] != "ok", state
        assert health["headline"] != "Control plane healthy"
        assert health["secondary_control_path"]["mode"] == "advisory"
        assert health["secondary_control_path"]["targets"]
        groups = {g["group_id"]: g for g in health["groups"]}
        assert groups["kill_switch"]["status"] == "degraded"
        assert health["meta"]["surfaces"]["health_status"]["status"] == "ok"


def test_operator_health_empty_kill_switch_read_is_unknown_not_armed() -> None:
    store = create_in_memory_read_surface_ports()
    store.list_runtime_bindings = lambda: []
    store.list_telemetry_summaries = lambda: []
    store.list_incidents = lambda: []
    store.list_approval_queue_items = lambda: []
    store.get_kill_switch_status = lambda: {}
    health = ManagementService(read_store=store).get_operator_health_status()
    group = {g["group_id"]: g for g in health["groups"]}["kill_switch"]
    assert group["details"]["status"] == "unknown"
    assert "armed" not in group["summary"]
    assert health["safe_mode_state"]["active"] is None
    assert health["safe_mode_state"]["status"] == "unknown"
    assert health["overall_status"] != "ok"
