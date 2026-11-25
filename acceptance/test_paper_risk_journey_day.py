"""Daily deadline judgement with a fixed clock and a fixed calendar (including a holiday)."""
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import test_paper_risk_journey as rj

spec = importlib.util.spec_from_file_location("risk_journey_day", Path(__file__).with_name("paper_risk_journey_day.py"))
day = importlib.util.module_from_spec(spec)
spec.loader.exec_module(day)
CUTOFF = datetime(2026, 11, 22, 16, tzinfo=timezone.utc)
# Taiwan sessions close 05:30Z; 11/26 is a holiday and 11/28 a weekend day that is simply absent.
CALENDAR = {"data": {"sessions": [
    {"session_date": "2026-11-23", "closes_at": "2026-11-23T05:30:00Z"},
    {"session_date": "2026-11-24", "closes_at": "2026-11-24T05:30:00Z"},
    {"session_date": "2026-11-25", "closes_at": "2026-11-25T05:30:00Z"},
    {"session_date": "2026-11-26", "closes_at": "2026-11-26T05:30:00Z", "is_trading_day": False},
    {"session_date": "2026-11-27", "closes_at": "2026-11-27T05:30:00Z"},
    {"session_date": "2026-11-30", "closes_at": "2026-11-30T05:30:00Z"},
    {"session_date": "2026-12-01", "closes_at": "2026-12-01T05:30:00Z"},
    {"session_date": "2026-12-02", "closes_at": "2026-12-02T05:30:00Z"},
]}}


def sessions_at(now):
    return day.complete_sessions(CALENDAR, CUTOFF, now)


def report(failed):
    return {"failed": failed, "deployment_cutoff": CUTOFF.isoformat()}


def at(text):
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def test_holiday_and_unfinished_session_do_not_count():
    assert len(sessions_at(at("2026-11-27T05:29:59"))) == 3  # 11/26 holiday skipped, 11/27 not yet closed
    assert len(sessions_at(at("2026-11-27T05:30:00"))) == 4
    assert len(sessions_at(at("2026-11-23T05:29:59"))) == 0


def test_passed_when_every_sample_is_present():
    assert day.judge(report([]), sessions_at(at("2026-12-02T12:00:00")), at("2026-12-02T12:00:00"))["verdict"] == "passed"


def test_pending_before_any_complete_session_names_the_missing_samples():
    now = at("2026-11-23T04:00:00")
    out = day.judge(report(rj.NO_SAMPLE), sessions_at(now), now)
    assert out["verdict"] == "pending" and out["pending"] == rj.NO_SAMPLE and out["sessions_after_cutoff"] == 0


def test_failed_when_a_complete_session_has_no_governed_decision():
    now = at("2026-11-23T06:00:00")
    out = day.judge(report(rj.NO_SAMPLE), sessions_at(now), now)
    assert out["verdict"] == "failed" and out["reasons"] == ["no_governed_decision_after_a_complete_session"]


def test_missing_refusal_or_fill_is_pending_until_five_complete_sessions():
    only = rj.NO_SAMPLE[1:]
    now = at("2026-11-30T06:00:00")  # 11/23 24 25 27 30; 11/26 is a holiday
    assert len(sessions_at(now)) == 5
    out = day.judge(report(only), sessions_at(now), now)
    assert out["verdict"] == "failed" and out["reasons"][0].startswith("no_refusal_or_reconciled_fill")
    earlier = at("2026-11-27T06:00:00")
    assert len(sessions_at(earlier)) == 4
    assert day.judge(report(only), sessions_at(earlier), earlier)["verdict"] == "pending"


def test_non_sample_finding_fails_regardless_of_sessions():
    now = at("2026-11-23T04:00:00")
    out = day.judge(report(["served_pair_mismatch"] + rj.NO_SAMPLE), sessions_at(now), now)
    assert out["verdict"] == "failed" and out["reasons"] == ["non_sample_findings"]


def test_unreadable_calendar_is_pending_then_failed_after_seven_days():
    early, late = CUTOFF + timedelta(days=6, hours=23), CUTOFF + timedelta(days=7)
    assert day.judge(report(rj.NO_SAMPLE), None, early)["verdict"] == "pending"
    out = day.judge(report(rj.NO_SAMPLE), None, late)
    assert out["verdict"] == "failed" and "unreadable" in out["reasons"][0]


@pytest.mark.parametrize("bad", [None, {}, {"data": {"sessions": "x"}}, {"data": {"sessions": [{"closes_at": "nope"}]}}])
def test_malformed_calendar_is_unreadable(bad):
    assert day.complete_sessions(bad, CUTOFF, at("2026-12-02T12:00:00")) is None


def evaluate(state, now, calendar):
    payload, manifest, history, rows, details, sources = state
    base = rj.run  # reuse the fixture fetch through a thin wrapper
    def fetch(path):
        if path == day.CALENDAR_PATH:
            if calendar is None:
                raise RuntimeError("http_404")
            return calendar
        return fetch_inner(path)
    from urllib.parse import urlsplit, parse_qs
    def fetch_inner(path):
        if path == "/bff/version":
            return {"source_commit_sha": rj.SHA}
        parsed = urlsplit(path)
        if parsed.path == rj.probe.BASE:
            return {"data": {"items": rows}, "page_info": {"total": len(rows)}}
        if parsed.path.startswith(rj.probe.BASE + "/"):
            return {"data": (history | details)[parsed.path.rsplit("/", 1)[1]]}
        return payload[path]
    return day.evaluate(fetch, lambda: manifest, lambda eid, tenant: sources[eid],
                        expected=("fixture-persona",), now=now)


def test_evaluate_passes_pending_and_failed_end_to_end():
    now = at("2026-11-22T18:00:00")
    assert evaluate(rj.fixture(), now, CALENDAR)["verdict"] == "passed"
    state = rj.fixture()
    state[3].clear()
    out = evaluate(state, now, CALENDAR)
    assert out["verdict"] == "pending" and out["exit_code"] == 0 and out["passed"] is False
    late = at("2026-11-23T06:00:00")
    state = rj.fixture()
    state[3].clear()
    out = evaluate(state, late, CALENDAR)
    assert out["verdict"] == "failed" and out["exit_code"] == 1
    state = rj.fixture()
    state[3].clear()
    out = evaluate(state, now, None)
    assert out["verdict"] == "pending" and out["day"]["calendar_error"] == "http_404"


def test_sample_checks_are_unchanged_by_the_day_wrapper():
    state = rj.fixture()
    state[5]["fixture-fill-reconciliation"]["metadata"]["reason_detail"][0].update(actual=3, difference=1, status="failed")
    out = evaluate(state, at("2026-11-22T18:00:00"), CALENDAR)
    assert not out["fresh_reconciled_fills"] and out["pending"] == [rj.NO_SAMPLE[2]]
    # The reconciliation check is not relaxed: the fill stays unproven, so the deadline applies.
    assert evaluate(state, at("2026-11-30T06:00:00"), CALENDAR)["verdict"] == "failed"


def test_missing_configuration_is_skip(monkeypatch, capsys):
    monkeypatch.delenv("ACCEPTANCE_FE_URL", raising=False)
    assert day.main() == 77
