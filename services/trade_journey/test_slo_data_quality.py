from datetime import datetime, timezone

import pytest

from services.trade_journey.capacity_baseline import measure_rebuild_baseline
from services.trade_journey.failure_injection import (
    SCENARIOS,
    FailureInjectionError,
    assert_drill_triggers_alert,
    run_failure_injection_drill,
)
from services.trade_journey.materializer import JourneyMaterializer
from services.trade_journey.slo_data_quality import (
    compute_data_quality_metrics,
    evaluate_data_quality,
    journey_evidence_ref,
    load_slo_targets,
)


def event(event_id, journey_id, stage, status, minute, **extra):
    return {
        "event_id": event_id, "journey_id": journey_id, "tenant_id": "tenant-a",
        "environment": "paper", "occurred_at": f"2026-07-12T00:{minute:02d}:00Z",
        "source": "test", "stage": stage, "stage_status": status, **extra,
    }


def test_load_slo_targets_paper_and_canary_and_live_alias():
    paper = load_slo_targets("paper")
    canary = load_slo_targets("canary")
    live = load_slo_targets("live")
    assert paper.producer_to_read_model_p95_seconds == 10
    assert canary.producer_to_read_model_p95_seconds == 3
    assert live.producer_to_read_model_p95_seconds == canary.producer_to_read_model_p95_seconds
    assert canary.correlation_completeness_min > paper.correlation_completeness_min


def test_load_slo_targets_rejects_unknown_environment():
    with pytest.raises(ValueError, match="unknown SLO target environment"):
        load_slo_targets("staging")


def test_journey_evidence_ref_matches_bff_evidence_route():
    assert journey_evidence_ref("tj_1") == "/bff/management/trade-journeys/tj_1/evidence"


def test_clean_paper_flow_has_no_incidents_and_full_completeness():
    materializer = JourneyMaterializer()
    materializer.rebuild([
        event("e1", "tj_1", "signal_generation", "succeeded", 0, signal_id="sig-1"),
        event("e2", "tj_1", "trade_decision", "succeeded", 1, decision_id="dec-1"),
        event("e3", "tj_1", "risk_evaluation", "succeeded", 2, risk_decision_id="risk-1"),
        event("e4", "tj_1", "order_submission", "succeeded", 3, client_order_id="c-1"),
        event("e5", "tj_1", "broker_acknowledgement", "succeeded", 4, broker_order_id="b-1"),
        event("e6", "tj_1", "fill_management", "succeeded", 5, broker_trade_id="t-1"),
        event("e7", "tj_1", "ledger_booking", "succeeded", 6, ledger_entry_id="l-1"),
        event("e8", "tj_1", "reconciliation", "succeeded", 7, reconciliation_id="r-1"),
    ])
    now = datetime(2026, 7, 12, 0, 7, 5, tzinfo=timezone.utc)  # 5s after last event, within the 10s paper target
    targets = load_slo_targets("paper")
    metrics = compute_data_quality_metrics(
        materializer.projections, environment="paper",
        source_watermarks=materializer.source_watermarks, now=now,
        stalled_after_seconds=targets.stalled_after_seconds,
    )
    assert metrics.total_journeys == 1
    assert metrics.correlation_completeness_rate == 1.0
    assert metrics.reconciliation_completeness_rate == 1.0
    assert metrics.materializer_lag_seconds == pytest.approx(5.0)

    incidents = evaluate_data_quality(metrics, targets, materializer.projections, now=now)
    assert incidents == ()


def test_orphan_and_missing_identifier_journey_links_to_exact_journey_evidence():
    materializer = JourneyMaterializer()
    materializer.rebuild([
        event("e1", "tj_orphan", "order_submission", "succeeded", 0, order_id="order-only"),
    ])
    now = datetime(2026, 7, 12, 0, 1, tzinfo=timezone.utc)
    targets = load_slo_targets("canary")
    metrics = compute_data_quality_metrics(
        materializer.projections, environment="canary",
        source_watermarks=materializer.source_watermarks, now=now,
        stalled_after_seconds=targets.stalled_after_seconds,
    )
    assert metrics.orphan_event_rate == 1.0
    assert metrics.missing_identifier_rate == 1.0

    incidents = evaluate_data_quality(metrics, targets, materializer.projections, now=now)
    by_code = {incident.code: incident for incident in incidents}
    assert "orphan_identifier" in by_code
    orphan = by_code["orphan_identifier"]
    assert orphan.journey_id == "tj_orphan"
    assert orphan.evidence_ref == "/bff/management/trade-journeys/tj_orphan/evidence"
    assert orphan.alert_path.runbook == "docs/operations/trade_journey_slo_runbook.md"


def test_reconciliation_mismatch_ages_and_alerts():
    materializer = JourneyMaterializer()
    materializer.rebuild([
        event("e1", "tj_mismatch", "order_submission", "succeeded", 0, client_order_id="c-1"),
        event("e2", "tj_mismatch", "reconciliation", "failed", 1, reconciliation_id="r-1"),
    ])
    now = datetime(2026, 7, 12, 1, 1, tzinfo=timezone.utc)  # ~1 hour after updated_at
    targets = load_slo_targets("paper")
    metrics = compute_data_quality_metrics(
        materializer.projections, environment="paper",
        source_watermarks=materializer.source_watermarks, now=now,
        stalled_after_seconds=targets.stalled_after_seconds,
    )
    assert metrics.reconciliation_mismatch_max_age_seconds == pytest.approx(3600.0)

    incidents = evaluate_data_quality(metrics, targets, materializer.projections, now=now)
    mismatch = next(item for item in incidents if item.code == "reconciliation_mismatch")
    assert mismatch.journey_id == "tj_mismatch"
    assert mismatch.severity == "critical"


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_failure_injection_scenario_triggers_expected_alert(scenario):
    result = assert_drill_triggers_alert(scenario)
    assert result["triggered"] is True
    assert result["expected_code"] in result["triggered_codes"]


def test_failure_injection_unknown_scenario_raises():
    with pytest.raises(FailureInjectionError, match="unknown failure injection scenario"):
        run_failure_injection_drill("not-a-real-scenario")


def test_capacity_baseline_reports_positive_throughput():
    result = measure_rebuild_baseline(journeys=25, stages_per_journey=5)
    assert result["event_count"] == 125
    assert result["projection_count"] == 25
    assert result["rebuild_status"] == "complete"
    assert result["events_per_second"] > 0
