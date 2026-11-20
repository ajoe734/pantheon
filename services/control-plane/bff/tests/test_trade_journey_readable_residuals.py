"""BFF-TRADE-JOURNEY-READABLE-RESIDUALS-20261120: tests on the recorded post-deploy hosted bodies."""
from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

from services.control_plane.bff import trade_journeys as tj
from services.control_plane.bff.trade_journey_projection_store import TradeJourneyProjectionStore
from services.trade_journey.materializer import JourneyProjection

FIXTURE = json.loads(
    (Path(__file__).resolve().parents[4] / "acceptance" / "fixtures" / "hosted_trade_journeys_post_deploy_20261120.json").read_text()
)
NOW = datetime(2026, 11, 20, 12, 42, tzinfo=timezone.utc)
REJECTED = "tj_79726064-4cc2-5510-8e11-7f5e59ff89bd"
VARIANCE = "tj_29473e86-63ff-5384-bda1-23eef61cebb2"
HOLD = "tj_efaa5c30-7feb-5359-8873-4993ca16edb3"


def stored_timeline(journey_id):
    """Recorded timeline items joined to the stored events (which carry the envelope)."""
    detail = FIXTURE["detail"][journey_id]["data"]
    stored = {e["event_id"]: e for e in detail["stage_events"].values()}
    items = []
    for item in FIXTURE["timeline"][journey_id]["data"]["items"]:
        merged = {**stored.get(item["event_id"], {}), **item}
        items.append(merged)
    return items


def projection(journey_id, mutate=None):
    d = copy.deepcopy(FIXTURE["detail"][journey_id]["data"])
    timeline = stored_timeline(journey_id)
    if mutate:
        mutate(d, timeline)
    snapshot = {k: d[k] for k in ("journey_id", "tenant_id", "environment", "status", "stages", "identifiers", "completeness", "revision", "created_at", "updated_at")}
    return JourneyProjection(journey_id, d["tenant_id"], d["environment"], timeline, snapshot, [], d["diagnostics"])


def test_reconciliation_only_failure_is_mismatch_not_rejected():
    d = FIXTURE["detail"][VARIANCE]["data"]
    assert d["stages"]["reconciliation"]["status"] == "failed"
    flags = tj._flags(d, d["diagnostics"])
    assert flags["rejected"] is False and flags["mismatch"] is True
    row = tj._list_row(projection(VARIANCE), now=NOW)
    assert row["flags"]["rejected"] is False and row["flags"]["mismatch"] is True
    assert row["outcome"]["kind"] == "variance"


def test_order_or_broker_failure_still_sets_rejected():
    for stage in ("risk_evaluation", "order_submission", "broker_acknowledgement"):
        flags = tj._flags({"stages": {stage: {"status": "failed"}}}, [])
        assert flags["rejected"] is True, stage
    assert tj._flags({"stages": {"fill_management": {"status": "failed"}}}, [])["rejected"] is False


def test_risk_gate_rejection_yields_risk_evaluation_rejected_and_not_partial():
    row = tj._list_row(projection(REJECTED), now=NOW)
    assert row["read_state"] != "partial"
    assert "risk_evaluation" not in row["completeness"]["missing_stages"]
    assert row["outcome"]["kind"] == "rejected" and row["outcome"]["rejected_stage"] == "risk_evaluation"
    assert row["outcome"]["reason_code"] == "reason_not_recorded"  # stored long_cash is an expression code
    assert row["flags"]["rejected"] is True
    detail = tj._detail(projection(REJECTED), now=NOW)
    assert detail["stages"]["risk_evaluation"]["status"] == "rejected"
    assert detail["stages"]["risk_evaluation"]["reason_code"] == "long_cash"
    assert [d for d in detail["diagnostics"] if d["code"] == "missing_stages"] == []


def test_explicit_risk_markers_also_infer_the_stage_and_real_reason_is_kept():
    def mutate(d, timeline):
        for e in timeline:
            if e["stage"] == "order_submission":
                e.pop("submitted_to_broker", None)
                e.update(risk_gate_evaluated=True, reason_code="insufficient_cash")
    p = projection(REJECTED, mutate)
    row = tj._list_row(p, now=NOW)
    assert row["outcome"]["reason_code"] == "insufficient_cash" and row["outcome"]["rejected_stage"] == "risk_evaluation"


def test_broker_submitted_rejection_is_not_inferred_as_risk_stage():
    def mutate(d, timeline):
        for e in timeline:
            if e["stage"] == "order_submission":
                e["submitted_to_broker"] = True
    row = tj._list_row(projection(REJECTED, mutate), now=NOW)
    assert row["outcome"]["rejected_stage"] == "order_submission"
    assert "risk_evaluation" in row["completeness"]["missing_stages"]


def test_hold_journey_is_unchanged():
    recorded = FIXTURE["detail"][HOLD]["data"]
    row = tj._list_row(projection(HOLD), now=NOW)
    assert row["flags"]["rejected"] is False
    assert row["outcome"] == recorded["outcome"]
    assert row["read_state"] == recorded["read_state"]


def test_timeline_maps_expression_reason_and_keeps_stored_value():
    events = {e["event_id"]: tj._timeline_event(e) for e in stored_timeline(REJECTED)}
    rejection = events["91d55953-b575-4fe8-8d39-6e0cade58d59"]
    assert rejection["reason_code"] == "reason_not_recorded" and rejection["expression_reason_code"] == "long_cash"
    stored = next(e for e in stored_timeline(REJECTED) if e["event_type"] == "order_rejection")
    assert stored["reason_code"] == "long_cash"  # stored rows are not rewritten
    for code in ("long_lever", "short_via_future", "close_leg"):
        assert tj._timeline_event({"reason_code": code})["expression_reason_code"] == code
    hold = [tj._timeline_event(e) for e in stored_timeline(HOLD)][-1]
    assert hold["reason_code"] == "hold_signal" and "expression_reason_code" not in hold


def test_timeline_carries_source_from_envelope_producer():
    sources = {(e["stage"], tj._timeline_event(e).get("source")) for e in stored_timeline(REJECTED)}
    assert sources == {("signal_generation", "execution.paper_runtime"), ("trade_decision", "execution.paper_runtime"), ("order_submission", "execution.paper_runtime")}
    variance = {tj._timeline_event(e)["event_type"]: tj._timeline_event(e).get("source") for e in stored_timeline(VARIANCE)}
    assert variance["reconciliation_failed"] == "reconciliation-drift.scheduled"
    assert "source" not in tj._timeline_event({"event_id": "x", "stage": "trade_decision"})


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def counting_owner(records, fail=False):
    calls = []

    def owner():
        calls.append(1)
        if owner.fail:
            raise RuntimeError("owner down")
        return records

    owner.fail = fail
    owner.calls = calls
    return owner


def test_persona_directory_is_read_once_per_60s_and_refreshed_after():
    owner = counting_owner([{"persona_id": "p1", "name": "one"}])
    clock = Clock()
    first = tj._persona_directory(owner, clock=clock)
    clock.now += 59
    assert tj._persona_directory(owner, clock=clock) is first  # list and detail share it
    assert len(owner.calls) == 1
    clock.now += 2
    tj._persona_directory(owner, clock=clock)
    assert len(owner.calls) == 2


def test_failed_refresh_keeps_previous_directory_for_300s_only():
    owner = counting_owner([{"persona_id": "p1", "name": "one"}])
    clock = Clock()
    tj._persona_directory(owner, clock=clock)
    owner.fail = True
    clock.now += 120
    assert tj._persona_directory(owner, clock=clock)["p1"]["name"] == "one"
    clock.now += 200  # 320s since the last good read
    assert tj._persona_directory(owner, clock=clock) == {}


def test_persona_filter_uses_summary_containment_not_link_subquery():
    store = TradeJourneyProjectionStore("dsn", token_secret="s" * 16, connect=lambda *_: None)
    clauses, params = store._journey_where(tenant_id="t", environment="paper", filters={"persona_id": "persona-x"})
    sql = " ".join(clauses)
    assert "@>" in sql and "-> 'persona_id'" not in sql and "ANY(ARRAY(SELECT" in sql  # no per-row EXISTS
    assert json.loads(params[2]) == {"persona_id": ["persona-x"]}
    assert json.loads(params[3]) == {"persona_id": "persona-x"}
