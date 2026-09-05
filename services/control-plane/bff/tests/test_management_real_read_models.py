"""Contract tests for Management read-model routes using typed reader ports.

These tests deliberately inject the five narrow reader callables accepted by
``management_read_models.router``.  They exercise the public envelopes and
degradation behavior without constructing the retired aggregate read store.
"""
from __future__ import annotations

import os
import sys
from typing import Any, Callable

from fastapi import FastAPI
from fastapi.testclient import TestClient


from services.control_plane.bff.management_read_models.router import (  # noqa: E402
    create_management_read_models_router,
    get_activity_read_model,
    get_formula_jobs_read_model,
    get_paper_telemetry_read_model,
    get_postmortems_read_model,
)


_NOW = "2026-08-20T12:00:00Z"

_SAMPLE_FORMULA_JOBS = [
    {
        "job_id": "job-f1-001",
        "formula_id": "form-sharpe-v1",
        "formula_version": "1.0.0",
        "owner_id": "user-quant-01",
        "status": "completed",
        "submitted_at": "2026-08-20T10:00:00Z",
        "started_at": "2026-08-20T10:00:01Z",
        "finished_at": "2026-08-20T10:00:15Z",
        "metrics": {"ic": 0.05, "sharpe": 1.8},
        "chart_lineage": [{"step": "calc", "duration_ms": 14000}],
        "source_identity": "formula_job_executor",
        "freshness": "2026-08-20T10:00:15Z",
    }
]

_SAMPLE_ACTIVITIES = [
    {
        "event_id": "evt-act-001",
        "event_type": "formula.submitted",
        "aggregate_id": "form-sharpe-v1",
        "actor_id": "user-quant-01",
        "timestamp": "2026-08-20T09:59:50Z",
        "summary": "Formula form-sharpe-v1 submitted for evaluation",
        "details": {"version": "1.0.0"},
        "source_identity": "activity_audit_store",
        "freshness": "2026-08-20T09:59:50Z",
    }
]

_SAMPLE_PAPER_TELEMETRY = [
    {
        "strategy_id": "strat-momentum-01",
        "persona_id": "persona-alpha",
        "paper_ledger_id": "ledger-paper-01",
        "status": "active",
        "last_signal_at": "2026-08-20T11:00:00Z",
        "series": [
            {
                "timestamp": "2026-08-20T11:00:00Z",
                "equity": 105000.0,
                "drawdown_pct": 0.02,
                "open_positions": 3,
                "daily_pnl": 1200.0,
            }
        ],
        "metrics": {"total_trades": 45, "win_rate": 0.62},
        "source_identity": "paper_telemetry_store",
        "freshness": "2026-08-20T11:00:00Z",
    }
]

_SAMPLE_POSTMORTEMS = [
    {
        "postmortem_id": "pm-inc-001",
        "incident_id": "inc-20260819-01",
        "title": "Paper signal producer latency spike",
        "severity": "high",
        "status": "resolved",
        "created_at": "2026-08-19T14:00:00Z",
        "resolved_at": "2026-08-19T15:30:00Z",
        "root_cause": "Unbounded lifecycle outbox scanning",
        "impact_summary": "Paper worker CPU bound for 90 minutes",
        "action_items": [{"id": "act-1", "desc": "Implement cursor retention"}],
        "source_identity": "postmortem_store",
        "freshness": "2026-08-19T15:30:00Z",
    }
]


def _identity(_: str | None) -> dict[str, str]:
    return {"role": "operator"}


def _require_read_role(_: dict[str, str]) -> None:
    return None


def _snapshot_meta(snapshot_at: str) -> dict[str, str]:
    return {"snapshot_at": snapshot_at}


def _reader(records: list[dict[str, Any]], available: bool = True) -> Callable[[], tuple[bool, list[dict[str, Any]]]]:
    return lambda: (available, [dict(record) for record in records])


def _telemetry_reader(
    records: list[dict[str, Any]], source: str = "telemetry"
) -> Callable[[], tuple[str, list[dict[str, Any]]]]:
    return lambda: (source, [dict(record) for record in records])


def _client(*, available: bool = True) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_management_read_models_router(
            extract_identity=_identity,
            require_read_role=_require_read_role,
            snapshot_meta=_snapshot_meta,
            utc_now=lambda: _NOW,
            jobs_reader=_reader(_SAMPLE_FORMULA_JOBS, available),
            activity_audit_reader=_reader(_SAMPLE_ACTIVITIES, available),
            governance_audit_reader=_reader([], available),
            telemetry_events_reader=_telemetry_reader([], "missing"),
            paper_telemetry_reader=_reader(_SAMPLE_PAPER_TELEMETRY, available),
            runtime_bindings_reader=_reader([], available),
            postmortems_reader=_reader(_SAMPLE_POSTMORTEMS, available),
        )
    )
    return TestClient(app)


def test_management_read_model_endpoints_use_injected_reader_ports() -> None:
    client = _client()

    formula = client.get("/bff/management/formula-jobs")
    assert formula.status_code == 200
    formula_item = formula.json()["data"]["items"][0]
    assert formula_item["job_id"] == "job-f1-001"
    assert formula_item["source_identity"] == "formula_job_executor"

    activity = client.get("/bff/management/activity")
    assert activity.status_code == 200
    assert activity.json()["data"]["items"][0]["event_id"] == "evt-act-001"

    telemetry = client.get("/bff/management/paper-telemetry")
    assert telemetry.status_code == 200
    assert telemetry.json()["data"]["items"][0]["strategy_id"] == "strat-momentum-01"

    postmortems = client.get("/bff/management/postmortems")
    assert postmortems.status_code == 200
    assert postmortems.json()["data"]["items"][0]["postmortem_id"] == "pm-inc-001"

    detail = client.get("/bff/management/postmortems/pm-inc-001")
    assert detail.status_code == 200
    assert detail.json()["data"]["postmortem_id"] == "pm-inc-001"
    assert client.get("/bff/management/postmortems/missing").status_code == 404


def test_reader_ports_preserve_filters_and_normalized_governance_activity() -> None:
    formulas = get_formula_jobs_read_model(
        status="completed",
        formula_id="form-sharpe-v1",
        jobs_reader=_reader(_SAMPLE_FORMULA_JOBS),
        utc_now=lambda: _NOW,
    )
    assert formulas["source"] == "service"
    assert [item["job_id"] for item in formulas["items"]] == ["job-f1-001"]

    activities = get_activity_read_model(
        governance_audit_reader=_reader(
            [
                {
                    "entry_id": "audit-001",
                    "action_type": "ApproveDecision",
                    "actor": "governance-lead",
                    "timestamp": _NOW,
                }
            ]
        ),
        telemetry_events_reader=_telemetry_reader([], "missing"),
        utc_now=lambda: _NOW,
    )
    assert activities["source"] == "audit"
    assert activities["items"][0]["event_id"] == "audit-001"
    assert activities["items"][0]["source_identity"] == "governance_audit_store"


def test_reader_ports_cover_direct_paper_and_postmortem_normalization() -> None:
    telemetry = get_paper_telemetry_read_model(
        paper_telemetry_reader=_reader(_SAMPLE_PAPER_TELEMETRY),
        utc_now=lambda: _NOW,
    )
    assert telemetry["source"] == "service"
    assert telemetry["items"][0]["series"][0]["equity"] == 105000.0

    postmortems = get_postmortems_read_model(
        postmortems_reader=_reader(_SAMPLE_POSTMORTEMS),
        utc_now=lambda: _NOW,
    )
    assert postmortems["source"] == "store"
    assert postmortems["items"][0]["action_items"] == [
        {"id": "act-1", "desc": "Implement cursor retention"}
    ]


def test_unavailable_reader_ports_return_degradation_envelopes() -> None:
    client = _client(available=False)
    for endpoint in (
        "/bff/management/formula-jobs",
        "/bff/management/activity",
        "/bff/management/paper-telemetry",
        "/bff/management/postmortems",
    ):
        response = client.get(endpoint)
        assert response.status_code == 200
        body = response.json()
        assert body["data"]["status"] == "unavailable"
        assert body["data"]["items"] == []
        assert body["meta"]["status"] == "unavailable"
        assert "degradation" in body["meta"]
