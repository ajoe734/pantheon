"""Real read-port shapes; native composition is tested in its owning suite."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.control_plane.bff.management_read_models.router import get_paper_telemetry_read_model
from services.control_plane.bff.management_read_models.service import (
    _build_trading_pulse_baseline_comparison,
    _project_operator_runtime_state_row,
)
from services.control_plane.bff.ports import create_in_memory_read_surface_ports
from services.control_plane.bff.ports.persona_capital_runtime import RuntimePort
from services.control_plane.bff.ports.reconciliation_drift_reads import (
    map_reconciliation_record_to_drift_report,
)


def test_owner_binding_metadata_survives_to_paper_and_trading_pulse():
    owner_row = {
        "binding_id": "binding-distinct", "runtime_id": "runtime-distinct",
        "deployment_mode": "paper", "status": "active",
        "metadata": {"strategy_id": "strategy-distinct", "persona_id": "persona-distinct"},
    }
    port = RuntimePort(runtime_bindings_provider=lambda: [owner_row])
    binding = port.get_runtime_binding("binding-distinct")
    assert binding["strategy_id"] == "strategy-distinct"
    assert binding["persona_id"] == "persona-distinct"
    assert "strategy_id" not in owner_row  # read adapter must not mutate owner state
    store = SimpleNamespace(list_runtime_bindings=port.list_runtime_bindings, list_telemetry_events=lambda: [])
    paper = get_paper_telemetry_read_model(store=store, strategy_id="strategy-distinct")
    assert [r["strategy_id"] for r in paper["items"]] == ["strategy-distinct"]
    assert paper["items"][0]["persona_id"] == "persona-distinct"
    row = _project_operator_runtime_state_row(None, binding, prefetched=True)
    comparison = _build_trading_pulse_baseline_comparison(None, row, prefetched=True)
    assert comparison["strategyId"] == comparison["strategy_id"] == "strategy-distinct"
    assert comparison["runtimeId"] == "runtime-distinct"
    assert comparison["runtimeBindingId"] == "binding-distinct"
    assert comparison["status"] == "unavailable"  # no invented drift evidence


@pytest.mark.parametrize("metadata", [None, [], {}, {"unrelated": "value"}])
def test_binding_id_is_not_promoted_to_strategy_identity(metadata):
    port = RuntimePort(runtime_bindings_provider=lambda: [{"binding_id": "binding-only", "metadata": metadata}])
    assert not port.list_runtime_bindings()[0].get("strategy_id")


def test_existing_explicit_identity_is_preserved():
    port = RuntimePort(runtime_bindings_provider=lambda: [{
        "binding_id": "b", "strategy_id": "explicit", "metadata": {"strategy_id": "legacy"},
    }])
    assert port.list_runtime_bindings()[0]["strategy_id"] == "explicit"


@pytest.mark.parametrize("sessions", [[], [{"runtime_id": "r", "binding_id": "b", "active": True}]])
def test_monitoring_owner_empty_and_nonempty_are_available(sessions):
    store = create_in_memory_read_surface_ports(paper_runtime_monitoring_sessions_provider=lambda: sessions)
    assert store.list_paper_runtime_monitoring_sessions() == sessions
    assert store.dataset_source("paper_runtime_monitoring_sessions") == "service"


@pytest.mark.parametrize("result", [None, {}, "invalid"])
def test_malformed_monitoring_owner_is_unavailable(result):
    store = create_in_memory_read_surface_ports(paper_runtime_monitoring_sessions_provider=lambda: result)
    assert store.dataset_source("paper_runtime_monitoring_sessions") == "unavailable"


def test_failed_monitoring_owner_is_unavailable():
    def failed():
        raise TimeoutError("owner unavailable")
    store = create_in_memory_read_surface_ports(paper_runtime_monitoring_sessions_provider=failed)
    assert store.dataset_source("paper_runtime_monitoring_sessions") == "unavailable"


def test_unconfigured_monitoring_owner_is_missing(monkeypatch):
    store = create_in_memory_read_surface_ports()
    # The in-memory factory deliberately supplies an empty provider; remove
    # that test owner to exercise the production unconfigured case.
    store._paper_runtime_monitoring_sessions_provider = None
    store._paper_fleet_reconciler_url = None
    monkeypatch.delenv("PANTHEON_PAPER_FLEET_RECONCILER_URL", raising=False)
    assert store.dataset_source("paper_runtime_monitoring_sessions") == "missing"


@pytest.mark.parametrize("records", [[], [{"runtime_id": "r-1", "binding_id": "b-1", "deployment_stage": "live"}]])
def test_reconciliation_drift_owner_empty_and_nonempty_are_available(records):
    store = create_in_memory_read_surface_ports(reconciliation_records_provider=lambda **kw: records)
    assert store.dataset_source("paper_live_drift_reports") == "service"


def test_reconciliation_drift_unconfigured_is_unavailable(monkeypatch):
    monkeypatch.delenv("RECONCILIATION_DRIFT_URL", raising=False)
    monkeypatch.delenv("PANTHEON_RECONCILIATION_DRIFT_URL", raising=False)
    store = create_in_memory_read_surface_ports()
    store.reconciliation_drift_reads._records_provider = None
    store.reconciliation_drift_reads._base_url = ""
    assert store.dataset_source("paper_live_drift_reports") == "unavailable"


def test_reconciliation_drift_records_mapping_and_paper_filtering():
    records = [
        {
            "id": "rec-paper-1",
            "runtime_id": "rt-paper",
            "binding_id": "bind-paper",
            "deployment_stage": "paper",
            "generated_at": "2026-10-06T00:00:00Z",
            "delta_summary": {
                "baseline_metrics": {"sharpe": 1.5},
                "observed_metrics": {"sharpe": 1.4},
            },
        },
        {
            "id": "rec-live-1",
            "runtime_id": "rt-live",
            "binding_id": "bind-live",
            "deployment_stage": "live",
            "generated_at": "2026-10-06T01:00:00Z",
            "artifact_id": "art-1",
            "artifact_version": "v1.0.0",
            "plan_id": "plan-1",
            "delta_summary": {
                "baseline_metrics": {"drawdown": 0.05, "avg_slippage_bps": 2.0},
                "observed_metrics": {"drawdown": 0.08, "avg_slippage_bps": 3.5},
                "drift_checks": [
                    {
                        "metric": "drawdown",
                        "status": "warning",
                        "baseline": 0.05,
                        "observed": 0.08,
                        "relative_delta": 0.6,
                    },
                    {
                        "metric": "avg_slippage_bps",
                        "status": "breached",
                        "baseline": 2.0,
                        "observed": 3.5,
                        "relative_delta": 0.75,
                    },
                ],
            },
        },
    ]
    store = create_in_memory_read_surface_ports(reconciliation_records_provider=lambda **kw: records)
    # Paper-only runtime should return None (no live drift comparison)
    assert store.get_paper_live_drift_report("rt-paper") is None

    # Live runtime returns mapped report with baseline, observed, drift groups and threshold evaluation
    report = store.get_paper_live_drift_report("rt-live")
    assert report is not None
    assert report["runtime_id"] == "rt-live"
    assert report["binding_id"] == "bind-live"
    assert report["artifact_id"] == "art-1"
    assert report["artifact_version"] == "v1.0.0"
    assert report["plan_id"] == "plan-1"
    assert report["paper_baseline"]["deployment_stage"] == "paper"
    assert report["paper_baseline"]["metrics"]["drawdown"] == 0.05
    assert report["observed_state"]["deployment_stage"] == "live"
    assert report["observed_state"]["metrics"]["drawdown"] == 0.08
    assert report["threshold_evaluation"]["overall_status"] == "breached"
    assert "avg_slippage_bps" in report["threshold_evaluation"]["breached_metric_ids"]

    # list_paper_live_drift_reports only returns live reports
    reports = store.list_paper_live_drift_reports()
    assert len(reports) == 1
    assert reports[0]["runtime_id"] == "rt-live"


def test_pkt014_paper_live_drift_healthy_empty_when_service_available_but_no_live_report() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from services.control_plane.bff.runtime.router import create_runtime_router

    store = create_in_memory_read_surface_ports(reconciliation_records_provider=lambda **kw: [])
    store.get_runtime_binding_by_runtime_id = lambda runtime_id: {
        "id": "runtime-042",
        "runtime_id": "runtime-042",
        "deployment_stage": "paper",
        "status": "running",
        "plan_id": "plan-F-042",
        "artifact_id": "art-042",
    } if runtime_id == "runtime-042" else None
    store.get_deployment_plan = lambda plan_id: {
        "plan_id": "plan-F-042",
        "approval_decision_id": "app-042",
    } if plan_id == "plan-F-042" else None
    store.get_approval_decision = lambda decision_id: {"decision_id": "app-042"}
    store.get_telemetry_summary = lambda runtime_id: {"runtime_id": runtime_id}
    store.get_telemetry_performance = lambda artifact_id: {"metrics": {}}
    store.list_incidents = lambda **kwargs: []
    store.get_evolution_decisions_by_incident = lambda incident_id: []
    store.dataset_source = lambda dataset: {
        "paper_live_drift_reports": "service",
        "runtime_bindings": "canonical",
        "telemetry_summaries": "service",
        "telemetry_performance": "service",
        "approval_decisions": "service",
        "incidents": "service",
        "evolution_decisions": "service",
    }.get(dataset, "missing")

    def _dataset_surface_status(dataset: str, snapshot_at: Any = None, has_data: Any = None, missing_message: Any = None) -> dict[str, Any]:
        src = store.dataset_source(dataset) if hasattr(store, "dataset_source") else "canonical"
        if src == "missing" or has_data is False:
            return {"status": "unavailable", "source": src}
        elif src == "local_snapshot":
            return {"status": "degraded", "source": "local_snapshot"}
        return {"status": "ok", "source": src}

    def _aggregate_group_surface(key: str, surfaces: list[dict[str, Any]], snapshot_at: Any = None, unavailable_message: Any = None, degraded_message: Any = None) -> dict[str, Any]:
        statuses = [s.get("status", "ok") for s in surfaces]
        if all(s == "ok" for s in statuses):
            return {"status": "ok", "source": "bff_composed"}
        if all(s == "unavailable" for s in statuses):
            return {"status": "unavailable", "source": "bff_composed", "message": unavailable_message}
        return {"status": "degraded", "source": "bff_composed", "message": degraded_message}

    deps = {
        "utc_now": lambda: "2026-04-18T06:10:00Z",
        "_extract_identity": lambda auth: {"roles": ["operator"]},
        "_require_read_role": lambda id: None,
        "_dataset_surface_status": _dataset_surface_status,
        "_aggregate_group_surface": _aggregate_group_surface,
        "_snapshot_meta": lambda s: {"snapshot_at": s},
        "_alert_target_ref": lambda surface_id, label, href, target_id=None: {
            "surface_id": surface_id, "label": label, "href": href, **({"target_id": target_id} if target_id else {})
        },
        "_deployment_review_href": lambda p: f"/operator/deployment-review?plan={p}",
        "_GOVERNANCE_APPROVAL_QUEUE_ROUTE": "/governance-approval-queue",
    }
    app = FastAPI()
    app.include_router(create_runtime_router(read_surface=store, dependencies=deps))
    client = TestClient(app)

    response = client.get(
        "/api/v1/operator/paper-live-drift/runtime-042",
        headers={"Authorization": "Bearer op-2:operator"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["paper_baseline"] is None
    assert payload["observed_state"] is None
    assert payload["drift_groups"] == []
    assert payload["meta"]["surfaces"]["paper_live_drift"]["status"] == "ok"
    assert payload["meta"]["surfaces"]["paper_live_drift"]["message"] == "No paper/live telemetry metrics available."


def test_reconciliation_record_mapping_missing_values_stay_missing() -> None:
    # AC2: Owner record fields map once at read port; missing values stay missing
    record_no_drift_checks = {
        "id": "rec-resolved-1",
        "runtime_id": "rt-resolved",
        "binding_id": "bind-resolved",
        "deployment_stage": "live",
        "status": "resolved",
        "generated_at": "2026-10-06T00:00:00Z",
        "delta_summary": {
            "baseline_metrics": {"sharpe": 1.5},
            "observed_metrics": {"sharpe": 1.5},
            # drift_checks is omitted
        },
    }
    report = map_reconciliation_record_to_drift_report(record_no_drift_checks)
    assert report is not None
    assert report["threshold_evaluation"] is None  # must NOT fabricate overall_status "ok"
    assert report["drift_groups"] is None


def test_reconciliation_drift_contract_composition_and_trading_pulse() -> None:
    # AC6: Recorded owner responses in real shape from reconciliation-drift main.py
    # live/paper records through native BFF composition and existing DTO builder
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from services.control_plane.bff.management_read_models.router import (
        create_management_router,
    )
    from services.control_plane.bff.runtime.router import create_runtime_router

    recorded_live_record = {
        "id": "recon-20261006-live-01",
        "record_id": "recon-20261006-live-01",
        "tenant_id": "default",
        "recon_type": "live_run",
        "scope_ref": "binding-live-01",
        "runtime_binding_id": "binding-live-01",
        "binding_id": "binding-live-01",
        "runtime_id": "runtime-live-01",
        "deployment_stage": "live",
        "deployment_plan_id": "plan-live-01",
        "artifact_id": "artifact-live-01",
        "artifact_version": "v1.0.0",
        "capital_pool_id": "pool-live-01",
        "persona_capital_binding_id": "pcb-live-01",
        "trace_id": "trace-live-01",
        "live_run_id": "liverun-01",
        "expected_ref": "paper_baseline",
        "actual_ref": "live_telemetry",
        "delta_summary": {
            "baseline_metrics": {"drawdown": 0.05, "avg_slippage_bps": 2.0},
            "observed_metrics": {"drawdown": 0.08, "avg_slippage_bps": 3.5},
            "drift_checks": [
                {
                    "metric": "drawdown",
                    "status": "warning",
                    "baseline": 0.05,
                    "observed": 0.08,
                    "relative_delta": 0.6,
                },
                {
                    "metric": "avg_slippage_bps",
                    "status": "breached",
                    "baseline": 2.0,
                    "observed": 3.5,
                    "relative_delta": 0.75,
                },
            ],
            "reconciliation_checks": [{"check": "capital_pool", "status": "passed"}],
            "telemetry_event_count": 42,
        },
        "severity": "high",
        "status": "open",
        "evidence_refs": [
            {"type": "runtime_binding", "id": "binding-live-01"},
            {"type": "artifact", "id": "artifact-live-01", "version": "v1.0.0"},
        ],
        "generated_at": "2026-10-06T03:00:00Z",
    }

    recorded_paper_record = {
        "id": "recon-20261006-paper-01",
        "record_id": "recon-20261006-paper-01",
        "tenant_id": "default",
        "recon_type": "paper_run",
        "scope_ref": "binding-paper-01",
        "runtime_binding_id": "binding-paper-01",
        "binding_id": "binding-paper-01",
        "runtime_id": "runtime-paper-01",
        "deployment_stage": "paper",
        "deployment_plan_id": "plan-paper-01",
        "artifact_id": "artifact-paper-01",
        "artifact_version": "v1.0.0",
        "capital_pool_id": "pool-paper-01",
        "persona_capital_binding_id": "pcb-paper-01",
        "trace_id": "trace-paper-01",
        "paper_run_id": "paperrun-01",
        "expected_ref": "backtest_baseline",
        "actual_ref": "paper_telemetry",
        "delta_summary": {
            "baseline_metrics": {"sharpe": 2.0, "drawdown": 0.04},
            "observed_metrics": {"sharpe": 1.9, "drawdown": 0.045},
            "drift_checks": [
                {
                    "metric": "drawdown",
                    "status": "ok",
                    "baseline": 0.04,
                    "observed": 0.045,
                    "relative_delta": 0.125,
                }
            ],
            "reconciliation_checks": [],
            "telemetry_event_count": 15,
        },
        "severity": "none",
        "status": "resolved",
        "evidence_refs": [],
        "generated_at": "2026-10-06T02:00:00Z",
    }

    store = create_in_memory_read_surface_ports(
        reconciliation_records_provider=lambda **kw: [
            recorded_live_record,
            recorded_paper_record,
        ]
    )
    bindings = [
        {
            "id": "binding-live-01",
            "binding_id": "binding-live-01",
            "runtime_id": "runtime-live-01",
            "deployment_stage": "live",
            "status": "running",
            "strategy_id": "strategy-live",
            "plan_id": "plan-live-01",
        },
        {
            "id": "binding-paper-01",
            "binding_id": "binding-paper-01",
            "runtime_id": "runtime-paper-01",
            "deployment_stage": "paper",
            "status": "running",
            "strategy_id": "strategy-paper",
            "plan_id": "plan-paper-01",
        },
    ]
    store.persona_capital_runtime.runtime.list_runtime_bindings = lambda: list(bindings)
    store.get_runtime_binding_by_runtime_id = lambda rid: next(
        (b for b in bindings if b["runtime_id"] == rid), None
    )

    deps = {
        "utc_now": lambda: "2026-10-06T04:00:00Z",
        "_extract_identity": lambda auth: {"roles": ["operator"]},
        "_require_read_role": lambda id: None,
        "_dataset_surface_status": lambda ds, **kw: {"status": "ok", "source": "service"},
        "_aggregate_group_surface": lambda k, s, **kw: {"status": "ok", "source": "bff_composed"},
        "_snapshot_meta": lambda s: {"snapshot_at": s},
        "_alert_target_ref": lambda surface_id, label, href, target_id=None: {
            "surface_id": surface_id, "label": label, "href": href
        },
        "_deployment_review_href": lambda p: f"/operator/deployment-review?plan={p}",
        "_GOVERNANCE_APPROVAL_QUEUE_ROUTE": "/governance-approval-queue",
    }

    app = FastAPI()
    app.include_router(create_runtime_router(read_surface=store, dependencies=deps))
    app.include_router(
        create_management_router(
            read_surface=store,
            extract_identity=lambda auth: {"roles": ["operator"]},
            require_read_role=lambda id: None,
        )
    )
    client = TestClient(app)

    # 1. Operator endpoint for recorded live record:
    live_resp = client.get(
        "/api/v1/operator/paper-live-drift/runtime-live-01",
        headers={"Authorization": "Bearer op:operator"},
    )
    assert live_resp.status_code == 200
    live_dto = live_resp.json()
    assert live_dto["paper_baseline"]["deployment_stage"] == "paper"
    assert live_dto["paper_baseline"]["metrics"]["drawdown"] == 0.05
    assert live_dto["observed_state"]["deployment_stage"] == "live"
    assert live_dto["observed_state"]["metrics"]["drawdown"] == 0.08
    assert live_dto["threshold_evaluation"]["overall_status"] == "breached"
    assert live_dto["threshold_evaluation"]["breached_metric_ids"] == ["avg_slippage_bps"]
    assert live_dto["meta"]["surfaces"]["paper_live_drift"]["status"] == "ok"

    # 2. Operator endpoint for recorded paper-only record:
    paper_resp = client.get(
        "/api/v1/operator/paper-live-drift/runtime-paper-01",
        headers={"Authorization": "Bearer op:operator"},
    )
    assert paper_resp.status_code == 200
    paper_dto = paper_resp.json()
    assert paper_dto["paper_baseline"] is None
    assert paper_dto["observed_state"] is None
    assert paper_dto["drift_groups"] == []
    assert paper_dto["meta"]["surfaces"]["paper_live_drift"]["status"] == "ok"
    assert paper_dto["meta"]["surfaces"]["paper_live_drift"]["message"] == (
        "No paper/live telemetry metrics available."
    )

    # 3. Management trading pulse endpoint:
    pulse_resp = client.get(
        "/bff/management/trading-pulse",
        headers={"Authorization": "Bearer op:operator"},
    )
    assert pulse_resp.status_code == 200
    pulse_dto = pulse_resp.json()
    comparisons = {
        c["runtime_id"]: c for c in pulse_dto["data"]["baseline_comparisons"]
    }
    assert "runtime-live-01" in comparisons
    live_comp = comparisons["runtime-live-01"]
    assert live_comp["status"] == "breached"
    assert live_comp["paper_live_drift"]["available"] is True
    assert live_comp["threshold_evaluation"]["overall_status"] == "breached"

    # Paper-only runtimes produce no comparison object in baseline_comparisons:
    assert "runtime-paper-01" not in comparisons
    assert live_comp["paper_live_drift"]["status"] == "breached"


def test_paper_only_bindings_trading_pulse_healthy_empty() -> None:
    # AC5: With only paper bindings the management trading pulse returns no comparison
    # with a healthy empty status rather than fabricated or zero values
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from services.control_plane.bff.management_read_models.router import (
        create_management_router,
    )

    recorded_paper_record = {
        "id": "recon-20261006-paper-02",
        "record_id": "recon-20261006-paper-02",
        "tenant_id": "default",
        "recon_type": "paper_run",
        "scope_ref": "binding-paper-02",
        "runtime_binding_id": "binding-paper-02",
        "binding_id": "binding-paper-02",
        "runtime_id": "runtime-paper-02",
        "deployment_stage": "paper",
        "deployment_plan_id": "plan-paper-02",
        "artifact_id": "artifact-paper-02",
        "artifact_version": "v1.0.0",
        "capital_pool_id": "pool-paper-02",
        "persona_capital_binding_id": "pcb-paper-02",
        "trace_id": "trace-paper-02",
        "paper_run_id": "paperrun-02",
        "expected_ref": "backtest_baseline",
        "actual_ref": "paper_telemetry",
        "delta_summary": {
            "baseline_metrics": {"sharpe": 2.0},
            "observed_metrics": {"sharpe": 1.9},
            "drift_checks": [
                {
                    "metric": "sharpe",
                    "status": "ok",
                    "baseline": 2.0,
                    "observed": 1.9,
                    "relative_delta": 0.05,
                }
            ],
            "reconciliation_checks": [],
            "telemetry_event_count": 5,
        },
        "severity": "none",
        "status": "resolved",
        "evidence_refs": [],
        "generated_at": "2026-10-06T02:00:00Z",
    }

    store = create_in_memory_read_surface_ports(
        reconciliation_records_provider=lambda **kw: [recorded_paper_record]
    )
    paper_bindings = [
        {
            "id": "binding-paper-02",
            "binding_id": "binding-paper-02",
            "runtime_id": "runtime-paper-02",
            "deployment_stage": "paper",
            "status": "running",
            "strategy_id": "strategy-paper",
            "plan_id": "plan-paper-02",
        }
    ]
    store.persona_capital_runtime.runtime.list_runtime_bindings = lambda: list(paper_bindings)
    store.get_runtime_binding_by_runtime_id = lambda rid: next(
        (b for b in paper_bindings if b["runtime_id"] == rid), None
    )

    app = FastAPI()
    app.include_router(
        create_management_router(
            read_surface=store,
            extract_identity=lambda auth: {"roles": ["operator"]},
            require_read_role=lambda id: None,
        )
    )
    client = TestClient(app)

    pulse_resp = client.get(
        "/bff/management/trading-pulse",
        headers={"Authorization": "Bearer op:operator"},
    )
    assert pulse_resp.status_code == 200
    pulse_dto = pulse_resp.json()
    comparisons = pulse_dto["data"]["baseline_comparisons"]
    assert comparisons == []

    # AC5: healthy empty status on both surfaces
    assert pulse_dto["meta"]["surfaces"]["paper_live_drift"]["status"] == "ok"
    assert pulse_dto["meta"]["surfaces"]["paper_live_drift"]["message"] == (
        "No live runtimes require paper/live baseline comparison."
    )
    assert pulse_dto["meta"]["surfaces"]["baseline_comparison"]["status"] == "ok"


def test_reconciliation_drift_tenant_derivation_uses_bound_tenant_and_fails_closed_403():
    # AC1: Tenant derivation in reconciliation read port uses bound_tenant and fails closed with 403
    import base64
    import json
    from fastapi import HTTPException
    from services.control_plane.bff.core.owner_reads import authorization, selected_tenant
    from services.control_plane.bff.ports.reconciliation_drift_reads import ReconciliationDriftReadsPort

    port = ReconciliationDriftReadsPort(base_url="http://test-recon")

    # 1. Unresolvable tenant without auth fails closed with 403
    authorization.set(None)
    selected_tenant.set("unverified-tenant")
    with pytest.raises(HTTPException) as exc_info:
        port._resolve_tenant_id()
    assert exc_info.value.status_code == 403

    # 2. Token tenant mismatch fails closed with 403
    header_b64 = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    payload_b64 = base64.urlsafe_b64encode(json.dumps({"tenant_id": "tenant-alpha"}).encode()).decode().rstrip("=")
    token = f"{header_b64}.{payload_b64}."
    authorization.set(f"Bearer {token}")
    selected_tenant.set("tenant-beta")  # mismatch!
    with pytest.raises(HTTPException) as exc_info:
        port._resolve_tenant_id()
    assert exc_info.value.status_code == 403

    # 3. Matching tenant succeeds
    selected_tenant.set("tenant-alpha")
    assert port._resolve_tenant_id() == "tenant-alpha"

    # Clean up context vars
    authorization.set(None)
    selected_tenant.set(None)


def test_reconciliation_drift_http_client_url_bearer_headers_and_401():
    # AC7: Tests cover HTTP client URL, bearer, tenant header, 401 handling, and owner runtime_id query filter
    import io
    import json
    import urllib.error
    from unittest.mock import patch, MagicMock
    from fastapi import HTTPException
    from services.control_plane.bff.core.owner_reads import authorization, selected_tenant
    from services.control_plane.bff.ports.reconciliation_drift_reads import ReconciliationDriftReadsPort

    port = ReconciliationDriftReadsPort(
        base_url="http://recon-drift.internal:8000",
        auth_token="secret-token-xyz",
        default_tenant_id="tenant-prod-1",
    )

    recorded_output = [{
        "runtime_id": "rt-live-1",
        "deployment_stage": "live",
        "generated_at": "2026-10-06T05:00:00Z",
        "expected_ref": "paper_baseline",
        "delta_summary": {},
    }]

    captured_req = None
    def mock_urlopen(req, timeout=None):
        nonlocal captured_req
        captured_req = req
        resp = MagicMock()
        resp.read.return_value = json.dumps(recorded_output).encode("utf-8")
        resp.__enter__.return_value = resp
        return resp

    authorization.set(None)
    selected_tenant.set(None)
    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        report = port.get_paper_live_drift_report(runtime_id="rt-live-1")

    assert report is not None
    assert captured_req is not None
    assert captured_req.full_url == "http://recon-drift.internal:8000/api/reconciliation-drift/reconciliation-records?runtime_id=rt-live-1"
    assert captured_req.headers["Authorization"] == "Bearer secret-token-xyz"
    assert captured_req.headers["X-tenant-id"] == "tenant-prod-1"
    assert captured_req.headers["Accept"] == "application/json"

    def mock_401(req, timeout=None):
        fp = io.BytesIO(b'{"detail":"Unauthorized"}')
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, fp)

    with patch("urllib.request.urlopen", side_effect=mock_401):
        with pytest.raises(HTTPException) as exc_info:
            port.get_paper_live_drift_report(runtime_id="rt-live-1")
        assert exc_info.value.status_code == 401
        assert port.get_surface_status() == "unavailable"


def test_availability_check_health_probe_without_downloading_records():
    # AC8: Availability check does not download whole tenant records; operator endpoint makes at most one owner records fetch per request
    from unittest.mock import patch, MagicMock
    from services.control_plane.bff.ports.reconciliation_drift_reads import ReconciliationDriftReadsPort

    port = ReconciliationDriftReadsPort(
        base_url="http://recon-drift.internal:8000",
        default_tenant_id="tenant-prod-1",
    )

    requested_urls = []
    def mock_urlopen(req, timeout=None):
        url = getattr(req, "full_url", str(req))
        requested_urls.append(url)
        resp = MagicMock()
        resp.status = 200
        resp.read.return_value = b'{"status":"healthy"}'
        resp.__enter__.return_value = resp
        return resp

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        status = port.get_surface_status()

    assert status == "service"
    assert len(requested_urls) == 1
    assert requested_urls[0] == "http://recon-drift.internal:8000/health"
    assert "/api/reconciliation-drift/reconciliation-records" not in requested_urls[0]


def test_canary_record_reading_and_mapping_against_stated_baseline():
    # AC3 & AC4: Canary records read and compared against stated baseline; helper keys filtered, owner units/sign kept
    from services.control_plane.bff.ports.reconciliation_drift_reads import map_reconciliation_record_to_drift_report

    canary_owner_record = {
        "id": "recon-canary-01",
        "runtime_id": "rt-canary-01",
        "deployment_stage": "canary",
        "recon_type": "canary_run",
        "expected_ref": "paper_baseline",
        "actual_ref": "canary_telemetry",
        "generated_at": "2026-10-06T06:00:00Z",
        "delta_summary": {
            "baseline_metrics": {
                "drawdown": 0.05,
                "drawdown_sample_count": 50,
                "drawdown_latest": 0.05,
            },
            "observed_metrics": {
                "drawdown": 0.08,
                "drawdown_sample_count": 50,
                "drawdown_latest": 0.08,
            },
            "drift_checks": [
                {
                    "metric": "drawdown",
                    "status": "warning",
                    "baseline": 0.05,
                    "observed": 0.08,
                    "relative_delta": 0.6,
                    "unit": "pct_ratio",
                },
                {
                    "metric": "pnl",
                    "status": "warning",
                    "baseline": 1.0,
                    "observed": 0.8,
                    "relative_delta": 0.2,
                    "unit": "ratio",
                }
            ],
        },
    }

    report = map_reconciliation_record_to_drift_report(canary_owner_record)
    assert report is not None
    assert report["deployment_stage"] == "canary"
    assert report["paper_baseline"]["deployment_stage"] == "paper"
    assert "captured_at" not in report["paper_baseline"]
    assert report["paper_baseline"]["metrics"] == {"drawdown": 0.05}
    assert report["observed_state"]["metrics"] == {"drawdown": 0.08}
    assert "drawdown_sample_count" not in report["observed_state"]["metrics"]
    assert "drawdown_latest" not in report["observed_state"]["metrics"]
    metrics = {m["metric_id"]: m for m in report["drift_groups"][0]["metrics"]}
    assert metrics["drawdown"]["delta"] == 0.6
    assert metrics["drawdown"]["unit"] == "pct_ratio"
    assert metrics["pnl"]["delta"] == -0.2
    assert metrics["pnl"]["unit"] == "ratio"


def test_operator_paper_live_drift_endpoint_degraded_for_live_without_record_and_preserves_degraded():
    # AC6: Operator paper-live-drift endpoint does not force aggregate status to ok over degraded supporting surfaces;
    # agrees with trading pulse (degraded) for live runtime without record.
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from services.control_plane.bff.ports import create_in_memory_read_surface_ports
    from services.control_plane.bff.runtime.router import create_runtime_router

    store = create_in_memory_read_surface_ports(reconciliation_records_provider=lambda **kw: [])
    live_binding = {
        "id": "runtime-live-missing",
        "runtime_id": "runtime-live-missing",
        "deployment_stage": "live",
        "status": "running",
        "plan_id": "plan-live-m",
    }
    store.get_runtime_binding_by_runtime_id = lambda rid: live_binding if rid == "runtime-live-missing" else None
    store.get_deployment_plan = lambda pid: {"plan_id": "plan-live-m", "approval_decision_id": None}
    store.get_approval_decision = lambda did: None
    store.get_telemetry_summary = lambda rid: None
    store.get_telemetry_performance = lambda aid: None
    store.list_incidents = lambda **kwargs: []
    store.get_evolution_decisions_by_incident = lambda iid: []

    deps = {
        "utc_now": lambda: "2026-10-06T07:00:00Z",
        "_extract_identity": lambda auth: {"roles": ["operator"]},
        "_require_read_role": lambda id: None,
        "_dataset_surface_status": lambda ds, **kw: {"status": "ok", "source": "service"},
        "_aggregate_group_surface": lambda k, s, **kw: {
            "status": "degraded" if any(x.get("status") == "degraded" for x in s) else "ok",
            "source": "bff_composed",
        },
        "_snapshot_meta": lambda s: {"snapshot_at": s},
        "_alert_target_ref": lambda surface_id, label, href, target_id=None: {"surface_id": surface_id, "label": label, "href": href},
        "_deployment_review_href": lambda p: f"/operator/deployment-review?plan={p}",
        "_GOVERNANCE_APPROVAL_QUEUE_ROUTE": "/governance-approval-queue",
    }

    app = FastAPI()
    app.include_router(create_runtime_router(read_surface=store, dependencies=deps))
    client = TestClient(app)

    resp = client.get(
        "/api/v1/operator/paper-live-drift/runtime-live-missing",
        headers={"Authorization": "Bearer op:operator"},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["meta"]["surfaces"]["paper_live_drift"]["status"] == "degraded"


def test_reconciliation_drift_owner_url_setting_name(monkeypatch):
    # AC9: Owner URL setting name matches PANTHEON_RECONCILIATION_DRIFT_API_URL
    from services.control_plane.bff.ports.reconciliation_drift_reads import ReconciliationDriftReadsPort

    monkeypatch.setenv("PANTHEON_RECONCILIATION_DRIFT_API_URL", "http://primary-api-url:9000")
    monkeypatch.setenv("RECONCILIATION_DRIFT_URL", "http://fallback-url:9000")

    port = ReconciliationDriftReadsPort()
    assert port._base_url == "http://primary-api-url:9000"


