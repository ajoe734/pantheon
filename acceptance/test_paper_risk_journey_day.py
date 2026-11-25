"""Daily deadline judgement from released decisions, with a fixed clock and recorded hosted shapes."""
import copy
import importlib.util
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import test_paper_risk_journey as rj

spec = importlib.util.spec_from_file_location("risk_journey_day", Path(__file__).with_name("paper_risk_journey_day.py"))
day = importlib.util.module_from_spec(spec)
spec.loader.exec_module(day)
CUTOFF = datetime(2026, 11, 22, 16, tzinfo=timezone.utc)
PERSONAS = ("persona-2448c7053e33ea48da47", "persona-6507750312870d03a241", "persona-ee7112a829d38b9135ac")
POLICY = {"risk_policy_id": "p", "version": 1}
SIZING = {"binding_limit": "cash", "permitted_units": 2, "fee_inclusive_required_cash": 22, "remaining_cash": 78}


def at(text):
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def event(eid="d1", decision_time="2026-11-23T10:00:00+00:00", **meta):
    base = {"decision_time": decision_time, "trade_date": decision_time[:10]}
    return {"event_id": eid, "event_type": "trade_decision", "metadata": base | meta}


def sized_event(**kw):
    policy = {**POLICY, "risk_policy_digest": "x"}
    ev = event(view_direction="LONG", pre_trade_sizing=dict(SIZING), reason_code="long_cash", **kw)
    return ev


@pytest.fixture
def sized_is_valid(monkeypatch):
    # Policy binding of sizing is the probe's own `sized` (covered by test_paper_risk_journey).
    monkeypatch.setattr(day.probe, "sized", lambda ev, policy, key: ev["metadata"].get("risk_policy_ok", True))


def test_hold_by_strategy_counts_as_neither_pass_nor_failure(sized_is_valid):
    out = day.classify(event(view_direction="FLAT", reason_code="flat_view",
                             pre_trade_sizing={"binding_limit": "flat_view"}), POLICY, CUTOFF)
    assert out["class"] == "held" and out["reason"] == "flat_view" and out["trading_day"] == "2026-11-23"


def test_hold_by_budget_unavailable_is_an_immediate_failure(sized_is_valid):
    # Recorded 2026-11-24 parked decision of persona-6507750312870d03a241.
    ev = event(eid="d-6507", decision_time="2026-11-24T10:00:00+00:00", view_direction="LONG",
               reason_code="candidates_unavailable", detail="budget_unavailable: gross_limit")
    out = day.classify(ev, POLICY, CUTOFF)
    assert out["class"] == "failed" and out["reason"] == "budget_unavailable"
    assert day.classify(event(reason_code="candidates_unavailable", view_direction="FLAT"), POLICY, CUTOFF)["reason"] == "candidates_unavailable"


def test_directional_view_without_sizing_fails_and_sized_decision_passes(sized_is_valid):
    assert day.classify(event(view_direction="SHORT"), POLICY, CUTOFF)["reason"] == "directional_view_without_pre_trade_sizing"
    assert day.classify(sized_event(), POLICY, CUTOFF)["class"] == "sized"
    bad = day.classify(sized_event(risk_policy_ok=False), POLICY, CUTOFF)
    assert bad["reason"] == "pre_trade_sizing_not_bound_to_policy"


def test_decision_before_the_cutoff_released_after_it_is_ignored(sized_is_valid):
    out = day.classify(event(decision_time="2026-11-20T10:00:00+00:00", view_direction="LONG"), POLICY, CUTOFF)
    assert out["class"] == "before_cutoff"
    assert day.classify({"event_id": "x", "metadata": {}}, POLICY, CUTOFF)["reason"] == "decision_time_unreadable"


def found(*rows, ignored=(), unreadable=()):
    return {"decisions": list(rows), "ignored_before_cutoff": list(ignored), "unreadable": list(unreadable)}


def row(pid, d, cls="sized", **kw):
    return {"persona_id": pid, "journey_id": "j-" + pid + d, "event_id": "e-" + pid + d, "class": cls,
            "trading_day": d, **kw}


def report(failed=(), personas=PERSONAS):
    return {"failed": list(failed), "deployment_cutoff": CUTOFF.isoformat(),
            "policy_readbacks": {"personas": {p: {} for p in personas}}}


DAYS = ["2026-11-23", "2026-11-24", "2026-11-25", "2026-11-27", "2026-11-30"]


def all_sized(days):
    return [row(p, d) for p in PERSONAS for d in days]


def test_passed_pending_and_failed_by_sessions():
    now = at("2026-12-01T12:00:00")
    assert day.judge(report(), found(*all_sized(DAYS)), now)["verdict"] == "passed"
    pending = day.judge(report(rj.NO_SAMPLE[:1]), found(*all_sized(DAYS[:2])), now)
    assert pending["verdict"] == "pending" and pending["pending"] == rj.NO_SAMPLE[:1]
    late = day.judge(report(rj.NO_SAMPLE[1:]), found(*all_sized(DAYS)), now)
    assert late["verdict"] == "failed" and late["reasons"][0].startswith("no_refusal_or_reconciled_fill")
    early = day.judge(report(rj.NO_SAMPLE[1:]), found(*all_sized(DAYS[:4])), now)
    assert early["verdict"] == "pending" and early["trading_sessions"] == 4


def test_immediate_failure_names_persona_decision_and_reason():
    bad = row(PERSONAS[1], "2026-11-24", "failed", reason="budget_unavailable")
    out = day.judge(report(), found(*all_sized(DAYS[:1]), bad), at("2026-11-25T06:00:00"))
    assert out["verdict"] == "failed" and out["reasons"] == [PERSONAS[1] + ":e-" + PERSONAS[1] + "2026-11-24:budget_unavailable"]


def test_five_trading_days_without_a_judgeable_decision_fails_that_persona_only():
    held = [row(PERSONAS[0], d, "held", reason="flat_view") for d in DAYS]
    rest = [row(p, d) for p in PERSONAS[1:] for d in DAYS]
    out = day.judge(report(rj.NO_SAMPLE[:1]), found(*held, *rest), at("2026-12-01T12:00:00"))
    assert out["verdict"] == "failed" and out["reasons"] == [PERSONAS[0] + ":no_judgeable_decision_after_5_trading_days"]
    four = day.judge(report(rj.NO_SAMPLE[:1]), found(*held[:4], *rest[:4], *rest[5:9]), at("2026-12-01T12:00:00"))
    assert four["verdict"] == "pending"  # held decisions are neither pass nor failure; the deadline is five days


def test_seven_days_without_any_released_decision_fails():
    assert day.judge(report(rj.NO_SAMPLE), found(), CUTOFF + timedelta(days=6, hours=23))["verdict"] == "pending"
    out = day.judge(report(rj.NO_SAMPLE), found(), CUTOFF + timedelta(days=7))
    assert out["verdict"] == "failed" and out["reasons"] == ["no_released_decision_after_7_days"]


def test_unreadable_reads_are_pending_then_failed_and_non_sample_findings_fail():
    bad = [{"persona_id": PERSONAS[0], "error": "dev_owner_read_failed"}]
    assert day.judge(report(), found(unreadable=bad), CUTOFF + timedelta(days=1))["verdict"] == "pending"
    out = day.judge(report(), found(*all_sized(DAYS[:1]), unreadable=bad), CUTOFF + timedelta(days=7))
    assert out["verdict"] == "failed" and out["reasons"] == ["journey_reads_unreadable_after_7_days"]
    out = day.judge(report(["served_pair_mismatch"]), found(*all_sized(DAYS[:1])), CUTOFF + timedelta(days=1))
    assert out["verdict"] == "failed" and out["reasons"] == ["non_sample_findings"]


def decorate(state, **meta):
    """Give the fixture's decisions the stored fields the check reads (recorded hosted event shape)."""
    for eid, src in state[5].items():
        if src["event_type"] == "trade_decision":
            src["metadata"].update({"decision_time": "2026-11-22T17:00:00+00:00", "trade_date": "2026-11-22",
                                    "view_direction": "LONG", "reason_code": "long_cash"} | meta)


def run(state, now, paths=None, drop=(), list_fails_on=0):
    payload, manifest, history, rows, details, sources = state

    lists = []

    def fetch(path):
        if paths is not None:
            paths.append(path)
        if urlsplit(path).path == rj.probe.BASE:
            lists.append(path)
            if list_fails_on and len(lists) == list_fails_on:
                raise RuntimeError("http_500")
        if path == "/bff/version":
            return {"source_commit_sha": rj.SHA}
        parsed = urlsplit(path)
        if parsed.path == rj.probe.BASE:
            return {"data": {"items": rows}, "page_info": {"total": len(rows)}}
        if parsed.path.startswith(rj.probe.BASE + "/"):
            return {"data": (history | details)[parsed.path.rsplit("/", 1)[1]]}
        return payload[path]

    def source_read(eid, tenant):
        if eid in drop:
            raise RuntimeError("dev_owner_read_failed")
        return sources[eid]
    return day.evaluate(fetch, lambda: manifest, source_read, expected=("fixture-persona",), now=now)


def test_evaluate_end_to_end_on_recorded_shapes():
    now = at("2026-11-22T18:00:00")
    state = rj.fixture()
    decorate(state)
    out = run(state, now)
    assert out["verdict"] == "passed" and out["day"]["trading_days"] == ["2026-11-22"]
    state = rj.fixture()
    decorate(state, view_direction="FLAT", reason_code="flat_view", pre_trade_sizing={"binding_limit": "flat_view"})
    out = run(state, now)
    assert out["verdict"] == "pending" and {d["class"] for d in out["day"]["decisions"]} == {"held"}
    state = rj.fixture()
    decorate(state, reason_code="candidates_unavailable", detail="budget_unavailable: gross_limit")
    out = run(state, now)
    assert out["verdict"] == "failed" and out["exit_code"] == 1
    assert out["day"]["reasons"][0].startswith("fixture-persona:fixture-fill-trade_decision:budget_unavailable")
    state = rj.fixture()
    decorate(state, decision_time="2026-11-20T10:00:00+00:00", trade_date="2026-11-20")
    out = run(state, now)
    assert out["day"]["decisions"] == [] and len(out["day"]["ignored_before_cutoff"]) == 2


def test_unreadable_reads_never_pass():
    state = rj.fixture()
    decorate(state)
    out = run(state, at("2026-11-22T18:00:00"), list_fails_on=2)  # the scan's own journey list read
    assert out["verdict"] == "pending" and out["day"]["unreadable"][0]["error"] == "http_500"
    assert run(state, CUTOFF + timedelta(days=7), list_fails_on=2)["verdict"] == "failed"
    # An unreadable immutable decision read is a probe finding (source_read_failed): failed, never pending.
    out = run(state, at("2026-11-22T18:00:00"), drop={"fixture-fill-trade_decision"})
    assert out["verdict"] == "failed" and out["day"]["reasons"] == ["non_sample_findings"]


def test_every_route_and_read_the_check_needs_is_registered():
    state = rj.fixture()
    decorate(state)
    paths = []
    run(state, at("2026-11-22T18:00:00"), paths)
    bff = (Path(__file__).resolve().parent.parent / "services/control-plane/bff/trade_journeys.py").read_text()
    registered = set(re.findall(r'@router\.get\(\s*"([^"]+)"', bff))
    own = {urlsplit(p).path for p in paths if "trade-journeys" in p}
    normalized = {re.sub(r"/(fixture|tj)[^/]*$", "/{journey_id}", p) for p in own}
    assert normalized == {"/bff/management/trade-journeys", "/bff/management/trade-journeys/{journey_id}"}
    for route in normalized:
        assert route in registered, route
    assert not any("calendar" in p for p in paths)
    assert "calendar" not in Path(day.__file__).read_text().split("def classify")[1]
    # The immutable decision read is the existing telemetry owner read of the shared transport.
    assert "kind in ('capital', 'telemetry')" in day.binding.DEV_OWNER_READ and "/api/telemetry/events/" in day.binding.DEV_OWNER_READ
    assert 'owner_read("telemetry"' in Path(day.__file__).read_text()


def test_missing_configuration_is_skip(monkeypatch, capsys):
    monkeypatch.delenv("ACCEPTANCE_FE_URL", raising=False)
    assert day.main() == 77


@pytest.mark.parametrize("module,verdict_code", [("probe", 77), ("day", 0)])
def test_main_exit_code_follows_the_verdict_for_pending(monkeypatch, module, verdict_code):
    from urllib.parse import urlsplit
    payload, manifest, history, rows, details, sources = state = rj.fixture()
    rows.clear()
    target = rj.probe if module == "probe" else day
    for key, value in {"ACCEPTANCE_FE_URL": "https://fe", "ACCEPTANCE_BFF_URL": "https://bff",
                       "ACCEPTANCE_VIEWER_CLIENT_ID": "i", "ACCEPTANCE_VIEWER_CLIENT_SECRET": "s",
                       "ACCEPTANCE_BACKEND_SHA": rj.SHA, "ACCEPTANCE_FRONTEND_SHA": rj.FE}.items():
        monkeypatch.setenv(key, value)

    def fetch(path):
        parsed = urlsplit(path)
        if path == "/bff/version":
            return {"source_commit_sha": rj.SHA}
        if parsed.path == rj.probe.BASE:
            return {"data": {"items": rows}}
        if parsed.path.startswith(rj.probe.BASE + "/"):
            return {"data": (history | details)[parsed.path.rsplit("/", 1)[1]]}
        return payload[path]

    def fake_main(runner):
        report = runner(fetch, ("fixture-persona",), None, lambda: manifest, lambda *a: None)
        return 0 if report["passed"] else 1  # what the shared main returns: never 77
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return at("2026-11-22T18:00:00")
    monkeypatch.setattr(day, "datetime", Clock)
    monkeypatch.setattr(target.binding, "main", fake_main)
    assert target.main() == verdict_code
