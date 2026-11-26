import importlib.util
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("pool_risk_snapshot_day", Path(__file__).with_name("pool_risk_snapshot_day.py"))
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)

NOW = datetime(2026, 11, 25, 11, 30, tzinfo=timezone.utc)
KNOWN = {"status": "known", "value": 0.0, "reason": None}
UNKNOWN = {"status": "unknown", "value": None, "reason": "health_window_missing_or_invalid"}
SESSION = "2026-11-24"


def row(day, equity=100.0, gross=0.1, nulls=None):
    return {"as_of": day, "pool_id": "p", "equity": equity, "gross_exposure": gross, "nulls": nulls or {}}


def health(known=True):
    return {m: (KNOWN if known else UNKNOWN) for m in check.MEASURES}


def journey(updated_at="2026-11-24T06:00:00Z", status="succeeded"):
    return {"stages": {"trade_decision": {"status": status, "updated_at": updated_at}}}


def fetcher(rows, *, count=None, known=True, journeys=None, personas=1, unreadable=None):
    def fetch(path):
        if unreadable and path.startswith(unreadable):
            raise RuntimeError("boom")
        if path.startswith("/bff/personas?"):
            return {"data": [{"id": f"persona-{i}", "owner_lifecycle_state": "paper_running", "status": "active",
                              "tenant_id": "t"} for i in range(personas)]}
        if path == "/api/v1/runtime-bindings":
            return {"data": [{"persona_id": f"persona-{i}", "runtime_id": f"rt-{i}", "deployment_stage": "paper",
                              "status": "active"} for i in range(personas)]}
        if path.startswith("/api/v1/telemetry/"):
            return {"data": {"pool_risk_snapshots": rows,
                             "pool_risk_snapshot_retained_days": len(rows) if count is None else count}}
        if path.startswith("/bff/management/trade-journeys?"):
            return {"data": {"items": [journey()] if journeys is None else journeys}}
        return {"data": {"strategyPool": {"health": health(known)}}}
    return fetch


def days(n):
    return [row(f"2026-10-{i + 1:02d}") for i in range(n - 1)] + [row(SESSION)]


def pool(report):
    return report["pools"][0]


def test_one_row_with_unknown_health_is_pending_and_names_the_row_count():
    report = check.run(fetcher([row(SESSION)], known=False), NOW)
    assert report["verdict"] == "pending" and pool(report)["pending"] == ["pool_health_pending:rows=1/20"]
    assert report["last_completed_session"] == SESSION and report["session_source"] == "released_decisions"


def test_19_rows_unknown_health_is_pending():
    report = check.run(fetcher(days(19), known=False), NOW)
    assert report["verdict"] == "pending" and pool(report)["pending"] == ["pool_health_pending:rows=19/20"]


def test_20_rows_unknown_health_fails():
    report = check.run(fetcher(days(20), known=False), NOW)
    assert report["verdict"] == "failed" and pool(report)["failures"] == ["pool_health_unknown:rows=20"]


def test_20_rows_known_health_passes():
    assert check.run(fetcher(days(20)), NOW)["verdict"] == "passed"


def test_missing_row_for_last_session_fails():
    report = check.run(fetcher([row("2026-11-23")]), NOW)
    assert report["verdict"] == "failed" and pool(report)["failures"] == ["no_row_for_last_session"]


def test_null_equity_fails_with_the_rows_own_reason():
    report = check.run(fetcher([row(SESSION, equity=None, gross=None, nulls={"equity": "no_known_owner_valuation", "gross_exposure": "no_known_owner_valuation"})]), NOW)
    assert pool(report)["failures"] == ["equity_null:no_known_owner_valuation", "gross_exposure_null:no_known_owner_valuation"]


@pytest.mark.parametrize("source", ["/api/v1/telemetry/", "/bff/management/trade-journeys", "/api/v1/runtime-bindings"])
def test_unreadable_source_fails_with_its_name(source):
    report = check.run(fetcher([row(SESSION)], unreadable=source), NOW)
    name = {"/api/v1/telemetry/": "telemetry_summary", "/bff/management/trade-journeys": "trade_journeys",
            "/api/v1/runtime-bindings": "runtime_bindings"}[source]
    assert report["verdict"] == "failed" and report["reason"] == f"{name}_unreadable:RuntimeError"


def test_no_trading_persona_fails():
    report = check.run(fetcher([], personas=0), NOW)
    assert report["verdict"] == "failed" and report["reason"] == "no_active_trading_persona"


def test_session_falls_back_to_other_pools_snapshot_dates_when_no_released_decision():
    def two(path):
        if path.startswith("/api/v1/telemetry/rt-0"):
            return {"data": {"pool_risk_snapshots": [row("2026-11-21")], "pool_risk_snapshot_retained_days": 1}}
        return fetcher([row(SESSION)], journeys=[journey(status="failed")], personas=2)(path)
    report = check.run(two, NOW)
    by = {r["persona_id"]: r for r in report["pools"]}
    assert report["session_source"] == "other_pool_snapshot_rows"
    # persona-0's session is the other pool's newest mark date (11-24), which its own 11-21 row lacks
    assert by["persona-0"]["failures"] == ["no_row_for_last_session"]
    # persona-1's session is the other pool's 11-21; its own newest row is later, but 11-21 is absent
    assert by["persona-1"]["failures"] == ["no_row_for_last_session"]


def test_no_session_source_fails_closed():
    report = check.run(fetcher([], journeys=[]), NOW)
    assert pool(report)["failures"][0] == "last_session_unknown"


def test_decision_day_is_the_taipei_day():
    assert check.decision_day(journey("2026-11-24T17:00:00Z")).isoformat() == "2026-11-25"


def test_pending_exits_zero(monkeypatch, capsys):
    monkeypatch.setenv("ACCEPTANCE_BFF_URL", "http://x")
    monkeypatch.setenv("ACCEPTANCE_VIEWER_CLIENT_ID", "v")
    monkeypatch.setenv("ACCEPTANCE_VIEWER_CLIENT_SECRET", "s")
    monkeypatch.setattr(check.persona_day, "_login", lambda *a: "t")
    monkeypatch.setattr(check, "_get_capped", lambda base, path, token: fetcher([row(SESSION)], known=False)(path))
    assert check.main() == 0


def test_read_only_get_and_viewer_identity_only():
    text = Path(check.__file__).read_text()
    assert "method=\"POST\"" not in text and "ACCEPTANCE_VIEWER_CLIENT_ID" in text


def test_every_route_the_check_reads_is_registered_in_the_bff_application():
    os.environ.setdefault("RANKING_STORE_DSN", "postgresql://test:test@localhost:5432/test")
    os.environ.setdefault("RANKING_STORE_BOOTSTRAP", "0")
    from services.control_plane.bff import main as bff_main

    from services.control_plane.bff.test_normalized_route_uniqueness import scan_fastapi_routes

    registered = {(e.method, e.raw_path) for e in scan_fastapi_routes(bff_main.app)}
    # Every read goes through fetch(): collect the literal templates the module issues.
    source = Path(check.__file__).read_text()
    issued = {"/bff/personas", "/api/v1/runtime-bindings", "/api/v1/telemetry/{runtime_id}/summary",
              "/bff/management/trade-journeys", "/bff/personas/{persona_id}"}
    assert issued == set(check.READ_ROUTES)
    for template in check.READ_ROUTES:
        assert re.search(re.escape(template.split("{")[0]), source), template
        assert ("GET", template) in registered, f"{template} is not registered"


def test_never_filled_runtime_with_heartbeats_and_one_state_snapshot_passes_check(tmp_path):
    from services.telemetry.runtime_summary import RuntimeSummaryProjectionStore
    from services.telemetry.test_runtime_summary_projection import _event

    store = RuntimeSummaryProjectionStore(path=str(tmp_path / "summary.json"))
    snap = _event("runtime_state_snapshot", created_at="2026-11-24T01:00:00Z")
    snap.update(runtime_id="rt-0", event_id="rss-0")
    snap["metadata"].update({
        "account_facts": {"initial_cash": 100000.0, "cash": 100000.0, "fill_count": 0},
        "risk_policy_facts": {
            "risk_policy_id": "rp-low-v2",
            "risk_policy_version": "2.0",
            "risk_policy_digest": "d" * 64,
            "gross_limit": 0.10,
            "max_leverage": 1.0,
            "drawdown_risk_off": 0.10,
            "drawdown_warn": 0.05,
        },
    })
    hb = _event("heartbeat", created_at="2026-11-24T06:00:00Z")
    hb.update(runtime_id="rt-0", event_id="hb-0")
    store.project_event(snap)
    store.project_event(hb)

    served = store.get("rt-0")
    rows = served["pool_risk_snapshots"]
    assert len(rows) == 1
    assert rows[0]["as_of"] == SESSION
    assert rows[0]["equity"] == 100000.0
    assert rows[0]["gross_exposure"] == 0.0
    assert rows[0]["gross_limit"] == 0.10

    report = check.run(
        fetcher(rows, count=served["pool_risk_snapshot_retained_days"], known=False),
        NOW,
    )
    assert report["verdict"] == "pending"
    assert pool(report)["passed"] is True
    assert pool(report)["failures"] == []
