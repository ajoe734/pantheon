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
    assert row["current_stage"] == "risk_evaluation"
    assert row["outcome"]["kind"] == "rejected" and row["outcome"]["rejected_stage"] == "risk_evaluation"
    assert row["outcome"]["reason_code"] == "reason_not_recorded"  # stored long_cash is an expression code
    assert row["outcome"]["expression_reason_code"] == "long_cash"
    assert row["outcome"]["submitted_to_broker"] is False
    assert row["outcome"]["order_status"] == "not_submitted"
    assert row["outcome"]["source_event_id"] == "91d55953-b575-4fe8-8d39-6e0cade58d59"
    assert row["outcome"]["missing_evidence"] == ["reject_reason"]
    assert row["flags"]["rejected"] is True
    detail = tj._detail(projection(REJECTED), now=NOW)
    assert detail["stages"]["risk_evaluation"]["status"] == "rejected"
    assert detail["stages"]["risk_evaluation"]["reason_code"] == "reason_not_recorded"
    assert detail["stages"]["risk_evaluation"]["expression_reason_code"] == "long_cash"
    assert detail["stages"]["risk_evaluation"]["source_event_id"] == "91d55953-b575-4fe8-8d39-6e0cade58d59"
    assert detail["stages"]["risk_evaluation"]["missing_evidence"] == ["reject_reason"]
    assert detail["stages"]["order_submission"]["status"] == "not_submitted"
    assert detail["stages"]["order_submission"]["order_status"] == "not_submitted"
    assert detail["stages"]["order_submission"]["submitted_to_broker"] is False
    assert "reason_code" not in detail["stages"]["order_submission"]
    assert detail["stage_events"]["risk_evaluation"]["reason_code"] == "reason_not_recorded"
    assert detail["stage_events"]["risk_evaluation"]["source_event_id"] == "91d55953-b575-4fe8-8d39-6e0cade58d59"
    assert detail["stage_events"]["order_submission"]["stage_status"] == "not_submitted"
    assert "reason_code" not in detail["stage_events"]["order_submission"]
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


def test_end_to_end_tj_79726064_producer_to_projection_to_bff_surfaces():
    """Trace producer -> canonical event -> stage projection -> BFF list/detail/timeline/attention for tj_79726064."""
    from services.trade_journey.lifecycle_projector import LifecycleProjector
    from services.trade_journey.materializer import JourneyMaterializer

    identity = {
        "journey_id": "tj_79726064-4cc2-5510-8e11-7f5e59ff89bd",
        "tenant_id": "tenant-dev",
        "environment": "paper",
        "run_id": "run-rb-a2cb762c5aeb42ebbcf8f2e6894a1bc4-7ed048e23fd14b14546cfb09",
        "loop_run_id": "lr-run-rb-a2cb762c5aeb42ebbcf8f2e6894a1bc4-7ed048e23fd14b14546cfb09",
        "signal_id": "sig-7ed048e23fd14b14546cfb098409e33d",
        "strategy_id": "strategy-persona-039ad01f8bcd2b4aa466",
        "runtime_id": "rt-3252fa75",
        "binding_id": "rb-a2cb762c5aeb42ebbcf8f2e6894a1bc4",
        "capital_pool_id": "pool-persona-paper-039ad01f8bcd2b4aa466",
        "persona_id": "persona-6507750312870d03a241",
        "persona_capital_binding_id": "pcb-persona-paper-039ad01f8bcd2b4aa466",
        "artifact_id": "artifact-persona-pool-039ad01f8bcd2b4aa466-v2",
        "artifact_version": "1.0.0",
        "plan_id": "plan-persona-paper-039ad01f8bcd2b4aa466",
        "trace_id": "c727932f-4e11-5790-9f43-65f1cbcf899e",
    }
    producer_events = [
        (
            1,
            {
                "event_id": "9f10b727-7903-59bb-9f69-4fd8d8997eb9",
                "event_type": "signal_generation",
                "created_at": "2026-11-19T23:46:15.000000Z",
                "symbol": "2317.TWSE",
                "side": "buy",
                "quantity": 452.0,
                "price": 221.0,
                "decision_id": "dec-rb-a2cb762c5aeb42ebbcf8f2e6894a1bc4-7ed048e23fd14b14546cfb09",
                "correlation_envelope": {"producer": "execution.paper_runtime"},
            },
        ),
        (
            2,
            {
                "event_id": "2c852171-3d1d-517c-8038-829098f0fd74",
                "event_type": "trade_decision",
                "created_at": "2026-11-19T23:46:16.000000Z",
                "symbol": "2317.TWSE",
                "side": "buy",
                "quantity": 452.0,
                "price": 221.0,
                "decision_id": "dec-rb-a2cb762c5aeb42ebbcf8f2e6894a1bc4-7ed048e23fd14b14546cfb09",
                "metadata": {"reason_code": "long_cash"},
                "correlation_envelope": {"producer": "execution.paper_runtime"},
            },
        ),
        (
            3,
            {
                "event_id": "91d55953-b575-4fe8-8d39-6e0cade58d59",
                "event_type": "order_rejection",
                "created_at": "2026-11-19T23:46:17.000000Z",
                "symbol": "2317.TWSE",
                "side": "buy",
                "order_id": "paper-order-91d55953-b575-4fe8-8d39-6e0cade58d59",
                "decision_id": "dec-rb-a2cb762c5aeb42ebbcf8f2e6894a1bc4-7ed048e23fd14b14546cfb09",
                "submitted_to_broker": False,
                "broker_submission_status": "insufficient_cash",
                "metrics": {"fill_quantity": 0.0, "submitted_to_broker": False},
                "metadata": {
                    "reason_code": "long_cash",
                    "reason": "insufficient_cash",
                    "reject_reason": "insufficient_cash",
                    "risk_reason": "insufficient_cash",
                    "risk_status": "rejected",
                    "risk_gate_evaluated": True,
                    "required": 100034.3461,
                    "available": 100000.0,
                    "limit": 100000.0,
                    "requested_quantity": 452.0,
                },
                "correlation_envelope": {"producer": "execution.paper_runtime"},
            },
        ),
    ]
    projected_events = []
    for seq_no, src in producer_events:
        projected_events.extend(
            LifecycleProjector._journey_events(
                {
                    "event": src,
                    "identity": identity,
                    "source_mode": "live",
                    "sequence_no": seq_no,
                }
            )
        )
    materializer = JourneyMaterializer()
    materializer.rebuild(projected_events)
    proj = materializer.get(identity["journey_id"], tenant_id="tenant-dev", environment="paper")
    assert proj is not None

    personas = {"persona-6507750312870d03a241": {"name": "澄川｜2317 台股 Paper 學習 R2", "is_probe": False}}
    list_row = tj._list_row(proj, now=NOW, personas=personas)
    assert list_row["current_stage"] == "risk_evaluation"
    assert list_row["outcome"] == {
        "kind": "rejected",
        "reason_code": "insufficient_cash",
        "reason_detail": {
            "required": 100034.3461,
            "available": 100000.0,
            "limit": 100000.0,
            "requested_quantity": 452.0,
        },
        "rejected_stage": "risk_evaluation",
        "source_event_id": "91d55953-b575-4fe8-8d39-6e0cade58d59",
        "submitted_to_broker": False,
        "order_status": "not_submitted",
        "expression_reason_code": "long_cash",
    }

    detail = tj._detail(proj, now=NOW, personas=personas)
    assert detail["stages"]["risk_evaluation"]["status"] == "rejected"
    assert detail["stages"]["risk_evaluation"]["reason_code"] == "insufficient_cash"
    assert detail["stages"]["risk_evaluation"]["reason_detail"] == {
        "required": 100034.3461,
        "available": 100000.0,
        "limit": 100000.0,
        "requested_quantity": 452.0,
    }
    assert detail["stages"]["risk_evaluation"]["source_event_id"] == "91d55953-b575-4fe8-8d39-6e0cade58d59"
    assert detail["stages"]["order_submission"]["status"] == "not_submitted"
    assert detail["stages"]["order_submission"]["order_status"] == "not_submitted"
    assert detail["stages"]["order_submission"]["submitted_to_broker"] is False
    assert "reason_code" not in detail["stages"]["order_submission"]
    assert detail["stage_events"]["risk_evaluation"]["reason_code"] == "insufficient_cash"
    assert detail["stage_events"]["order_submission"]["stage_status"] == "not_submitted"
    assert "reason_code" not in detail["stage_events"]["order_submission"]

    timeline_items = [tj._timeline_event(e) for e in proj.timeline]
    rej_item = timeline_items[-1]
    assert rej_item["stage"] == "risk_evaluation"
    assert rej_item["stage_status"] == "rejected"
    assert rej_item["status"] == "rejected"
    assert rej_item["rejected_stage"] == "risk_evaluation"
    assert rej_item["source_event_id"] == "91d55953-b575-4fe8-8d39-6e0cade58d59"
    assert rej_item["reason_code"] == "insufficient_cash"
    assert rej_item["expression_reason_code"] == "long_cash"
    assert rej_item["submitted_to_broker"] is False
    assert rej_item["order_status"] == "not_submitted"
    assert rej_item["reason_detail"] == {
        "required": 100034.3461,
        "available": 100000.0,
        "limit": 100000.0,
        "requested_quantity": 452.0,
    }
    assert tj._attention_item(proj, now=NOW) is None


def _tj_57418ba7_projection(recon_overrides=None):
    """Build JourneyProjection for recorded journey tj_57418ba7-ef37-5015-9b5e-b6423872da89."""
    recon_event = {
        "event_id": "48c946c8-39d1-52bd-b1a1-ce72dc7f6e14",
        "canonical_event_id": "48c946c8-39d1-52bd-b1a1-ce72dc7f6e14",
        "source_event_id": "48c946c8-39d1-52bd-b1a1-ce72dc7f6e14",
        "journey_id": "tj_57418ba7-ef37-5015-9b5e-b6423872da89",
        "tenant_id": "tenant-dev",
        "environment": "paper",
        "stage": "reconciliation",
        "event_type": "reconciliation_failed",
        "stage_status": "failed",
        "occurred_at": "2026-11-16T06:58:29.000000Z",
        "recorded_at": "2026-11-16T06:58:29.106651Z",
        "persona_id": "persona-6507750312870d03a241",
        "strategy_id": "strategy-persona-039ad01f8bcd2b4aa466",
        "artifact_id": "artifact-persona-paper-039ad01f8bcd2b4aa466",
        "decision_id": "dec-rb-7f3c1be55a224fdaa36bbdd7a7532b4b-6946b513a0bb1ce4650793a4",
        "capital_pool_id": "pool-persona-paper-039ad01f8bcd2b4aa466",
        "runtime_id": "rt-3252fa75",
        "symbol": "2317.TWSE",
        "side": "sell",
        "quantity": 1.0,
        "requested_quantity": 1.0,
        "price": 218.5,
        "order_id": "b00575cfc62f44d18ed052dad34d23ea",
        "broker_order_id": "b00575cfc62f44d18ed052dad34d23ea",
        "reconciliation_id": "48c946c8-39d1-52bd-b1a1-ce72dc7f6e14",
        "correlation_envelope": {"producer": "reconciliation-drift.scheduled"},
    }
    if recon_overrides:
        recon_event.update(recon_overrides)
    fill_event = {
        "event_id": "000600fb-6496-47d9-a550-1fa23449c1e2",
        "journey_id": "tj_57418ba7-ef37-5015-9b5e-b6423872da89",
        "tenant_id": "tenant-dev",
        "environment": "paper",
        "stage": "fill_management",
        "event_type": "paper_fill_simulated",
        "stage_status": "succeeded",
        "occurred_at": "2026-11-16T06:52:04.000000Z",
        "recorded_at": "2026-11-16T06:52:04.500000Z",
        "symbol": "2317.TWSE",
        "side": "sell",
        "quantity": 1.0,
        "fill_quantity": 1.0,
        "requested_quantity": 1.0,
        "price": 218.5,
        "order_id": "b00575cfc62f44d18ed052dad34d23ea",
        "broker_order_id": "b00575cfc62f44d18ed052dad34d23ea",
        "correlation_envelope": {"producer": "execution.paper_runtime"},
    }
    timeline = [fill_event, recon_event]
    snapshot = {
        "journey_id": "tj_57418ba7-ef37-5015-9b5e-b6423872da89",
        "tenant_id": "tenant-dev",
        "environment": "paper",
        "status": "completed_with_variance",
        "stages": {
            "signal_generation": {"status": "succeeded", "updated_at": "2026-11-16T06:52:04.000000Z", "revision": 1},
            "trade_decision": {"status": "succeeded", "updated_at": "2026-11-16T06:52:04.000000Z", "revision": 1},
            "risk_evaluation": {"status": "succeeded", "updated_at": "2026-11-16T06:52:04.000000Z", "revision": 1},
            "order_submission": {"status": "succeeded", "updated_at": "2026-11-16T06:52:04.000000Z", "revision": 1},
            "broker_acknowledgement": {"status": "succeeded", "updated_at": "2026-11-16T06:52:04.000000Z", "revision": 1},
            "fill_management": {"status": "succeeded", "updated_at": "2026-11-16T06:52:04.000000Z", "revision": 1},
            "ledger_booking": {"status": "succeeded", "updated_at": "2026-11-16T06:52:04.000000Z", "revision": 1},
            "reconciliation": {"status": "failed", "updated_at": "2026-11-16T06:58:29.000000Z", "revision": 1},
        },
        "identifiers": {
            "binding_id": ["rb-7f3c1be55a224fdaa36bbdd7a7532b4b"],
            "persona_id": ["persona-6507750312870d03a241"],
            "order_id": ["b00575cfc62f44d18ed052dad34d23ea"],
            "broker_order_id": ["b00575cfc62f44d18ed052dad34d23ea"],
            "reconciliation_id": ["48c946c8-39d1-52bd-b1a1-ce72dc7f6e14"],
        },
        "completeness": {"missing_stages": [], "complete": True},
        "revision": 2722464,
        "created_at": "2026-11-16T06:52:04.000000Z",
        "updated_at": "2026-11-16T06:58:29.000000Z",
    }
    return JourneyProjection(
        "tj_57418ba7-ef37-5015-9b5e-b6423872da89",
        "tenant-dev",
        "paper",
        timeline,
        snapshot,
        [],
        [],
    )


def test_tj_57418ba7_classifies_unverified_runtime_broker_and_proven_accounting_variance_distinctly():
    personas = {"persona-6507750312870d03a241": {"name": "澄川｜2317 台股 Paper 學習 R2", "is_probe": False}}

    # 1. Recorded un-repaired event 48c946c8-39d1-52bd-b1a1-ce72dc7f6e14 (no reason_code/reason_detail)
    unverified_proj = _tj_57418ba7_projection()
    unverified_row = tj._list_row(unverified_proj, now=NOW, personas=personas)
    assert unverified_row["outcome"] == {
        "kind": "variance",
        "classification": "unverified_evidence",
        "accounting_variance": False,
        "comparison_completed": False,
        "reason_code": "reason_not_recorded",
        "reason_detail": None,
        "discrepancies": [],
        "source_event_id": "48c946c8-39d1-52bd-b1a1-ce72dc7f6e14",
        "missing_evidence": ["reconciliation_reason", "booking_comparison"],
    }

    # 2. Runtime health at reconciliation
    runtime_proj = _tj_57418ba7_projection(
        {
            "reason_code": "runtime_unhealthy_at_reconciliation",
            "reason_detail": [
                {
                    "check": "runtime_health_summary",
                    "status": "critical",
                    "detail": "Runtime heartbeat stale during deployment window",
                    "observed": "unhealthy",
                    "threshold": "healthy",
                }
            ],
        }
    )
    runtime_row = tj._list_row(runtime_proj, now=NOW, personas=personas)
    assert runtime_row["outcome"]["kind"] == "reconciliation_deferred"
    assert runtime_row["outcome"]["classification"] == "runtime_health"
    assert runtime_row["outcome"]["accounting_variance"] is False
    assert runtime_row["outcome"]["reason_code"] == "runtime_unhealthy_at_reconciliation"
    assert runtime_row["flags"]["mismatch"] is False

    # 3. Broker unavailability at reconciliation
    broker_unavail_proj = _tj_57418ba7_projection(
        {
            "reason_code": "broker_unavailable_at_reconciliation",
            "reason_detail": [
                {
                    "check": "broker_record",
                    "status": "unavailable",
                    "broker_order_id": "b00575cfc62f44d18ed052dad34d23ea",
                    "error": "paper broker status 503",
                }
            ],
        }
    )
    broker_row = tj._list_row(broker_unavail_proj, now=NOW, personas=personas)
    assert broker_row["outcome"]["kind"] == "reconciliation_deferred"
    assert broker_row["outcome"]["classification"] == "broker_unavailable"
    assert broker_row["outcome"]["accounting_variance"] is False
    assert broker_row["outcome"]["reason_code"] == "broker_unavailable_at_reconciliation"
    assert broker_row["flags"]["mismatch"] is False

    # 4. Upstream omission on a claimed cash_variance (actual is None -> unverified_evidence, not accounting_variance)
    incomplete_proj = _tj_57418ba7_projection(
        {
            "reason_code": "cash_variance",
            "reason_detail": [
                {
                    "check": "cash_change",
                    "status": "failed",
                    "expected": 217.5331375,
                    "actual": None,
                    "difference": None,
                }
            ],
        }
    )
    incomplete_row = tj._list_row(incomplete_proj, now=NOW, personas=personas)
    assert incomplete_row["outcome"]["classification"] == "unverified_evidence"
    assert incomplete_row["outcome"]["accounting_variance"] is False
    assert incomplete_row["outcome"]["comparison_completed"] is False
    assert incomplete_row["outcome"]["reason_code"] == "reconciliation_evidence_incomplete"
    assert incomplete_row["outcome"]["unverified_reason_code"] == "cash_variance"
    assert incomplete_row["outcome"]["discrepancies"] == []
    assert incomplete_row["outcome"]["missing_evidence"] == ["cash_change:actual"]

    # 5. Proven accounting variance (completed trade-specific comparison with expected, actual, delta)
    proven_proj = _tj_57418ba7_projection(
        {
            "reason_code": "cash_variance",
            "reason_detail": [
                {
                    "check": "position_change",
                    "status": "passed",
                    "expected": -1.0,
                    "actual": -1.0,
                    "difference": 0.0,
                },
                {
                    "check": "cash_change",
                    "status": "failed",
                    "expected": 217.5331375,
                    "actual": 172.5331375,
                    "difference": -45.0,
                },
            ],
        }
    )
    proven_detail = tj._detail(proven_proj, now=NOW, personas=personas)
    assert proven_detail["outcome"]["kind"] == "variance"
    assert proven_detail["outcome"]["classification"] == "accounting_variance"
    assert proven_detail["outcome"]["accounting_variance"] is True
    assert proven_detail["outcome"]["comparison_completed"] is True
    assert proven_detail["outcome"]["reason_code"] == "cash_variance"
    assert proven_detail["outcome"]["discrepancies"] == [
        {
            "check": "cash_change",
            "status": "failed",
            "expected": 217.5331375,
            "actual": 172.5331375,
            "difference": -45.0,
            "delta": -45.0,
        }
    ]
    assert proven_detail["stages"]["reconciliation"]["classification"] == "accounting_variance"
    assert proven_detail["stages"]["reconciliation"]["accounting_variance"] is True
    assert proven_detail["stages"]["reconciliation"]["discrepancies"][0]["delta"] == -45.0
    recon_tl = tj._timeline_event(proven_proj.timeline[-1])
    assert recon_tl["classification"] == "accounting_variance"
    assert recon_tl["accounting_variance"] is True
    assert recon_tl["discrepancies"][0]["delta"] == -45.0
    assert recon_tl["source_event_id"] == "48c946c8-39d1-52bd-b1a1-ce72dc7f6e14"


def test_recon_mismatch_sql_excludes_broker_unavailable_and_unverified_reasons():
    store = TradeJourneyProjectionStore("dsn", token_secret="s" * 16, connect=lambda *_: None)
    clauses, _ = store._journey_where(tenant_id="tenant-dev", environment="paper", filters={"attention": "recon_mismatch"})
    sql = " ".join(clauses)
    for deferred_code in (
        "runtime_unhealthy_at_reconciliation",
        "broker_unavailable_at_reconciliation",
        "reconciliation_identity_unverified",
        "reconciliation_evidence_incomplete",
    ):
        assert deferred_code in sql
