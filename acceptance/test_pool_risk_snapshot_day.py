import json
from datetime import datetime, timezone
from pathlib import Path

import importlib.util

_spec = importlib.util.spec_from_file_location("pool_risk_snapshot_day", Path(__file__).with_name("pool_risk_snapshot_day.py"))
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)

NOW = datetime(2026, 11, 25, 11, 30, tzinfo=timezone.utc)
CALENDAR = {"data": {"sessions": [
    {"date": "2026-11-23", "is_trading_day": True, "closes_at": "2026-11-23T05:30:00Z"},
    {"date": "2026-11-24", "is_trading_day": True, "closes_at": "2026-11-24T05:30:00Z"},
    {"date": "2026-11-25", "is_trading_day": True, "closes_at": "2026-11-25T05:30:00Z"},
    {"date": "2026-11-26", "is_trading_day": True, "closes_at": "2026-11-26T05:30:00Z"}]}}
RECORDED = json.loads((Path(__file__).parent / "fixtures" / "hosted_persona_pool_20261119.json").read_text())
KNOWN = {"status": "known", "value": 0.0, "reason": None}


def health(end, **over):
    return {"window_end": end, **{m: KNOWN for m in check.MEASURES}, **over}


def fetcher(detail_health):
    def fetch(path):
        if path.startswith("/bff/personas?"):
            return {"data": [{"id": "persona-a", "owner_lifecycle_state": "paper_running", "status": "active",
                              "tenant_id": "t"}]}
        if path == check.CALENDAR_PATH:
            return CALENDAR
        return {"data": {"strategyPool": {"health": detail_health}}}
    return fetch


def test_last_completed_session_uses_calendar_close():
    assert check.last_completed_session(CALENDAR, NOW).isoformat() == "2026-11-25"


def test_known_health_with_last_session_snapshot_passes():
    assert check.run(fetcher(health("2026-11-25")), NOW)["verdict"] == "passed"


def test_missing_snapshot_for_last_session_fails():
    report = check.run(fetcher(health("2026-11-24")), NOW)
    assert report["verdict"] == "failed" and "no_snapshot_for_last_session" in report["pools"][0]["failures"]


def test_unknown_health_fails_with_reason_using_the_recorded_real_unknown_shape():
    decision = RECORDED["details"]["pass"]["data"]["strategyPool"]["current"]["decision"]
    report = check.run(fetcher(decision["health"]), NOW)
    pool = report["pools"][0]
    assert report["verdict"] == "failed"
    assert "pool_health_unknown" in pool["failures"] and "no_snapshot_for_last_session" in pool["failures"]
    assert pool["unknown_reasons"]["gross_utilisation"] == "health_window_missing_or_invalid"


def test_idle_pool_is_known_not_unknown():
    idle = health("2026-11-25", gross_utilisation={"status": "known", "value": 0.0, "reason": None})
    assert check.run(fetcher(idle), NOW)["verdict"] == "passed"


def test_unreadable_calendar_and_no_health_fail_closed():
    def fetch(path):
        if path == check.CALENDAR_PATH:
            raise RuntimeError("boom")
        return fetcher(None)(path)
    report = check.run(fetch, NOW)
    assert report["verdict"] == "failed" and report["calendar_error"] == "RuntimeError"


def test_read_only_get_and_viewer_identity_only():
    text = Path(check.__file__).read_text()
    assert "method=\"POST\"" not in text and "ACCEPTANCE_VIEWER_CLIENT_ID" in text
