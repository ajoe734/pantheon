import json

import pytest

from services.trade_journey.alert_rules import AlertRule, evaluate_aggregate_rules, load_alert_rules
from services.trade_journey.slo_data_quality import compute_data_quality_metrics, load_slo_targets
from services.trade_journey.materializer import JourneyMaterializer
from datetime import datetime, timezone


def _clean_metrics():
    materializer = JourneyMaterializer()
    materializer.rebuild([{
        "event_id": "e1", "journey_id": "tj_1", "tenant_id": "tenant-a", "environment": "paper",
        "occurred_at": "2026-07-12T00:00:00Z", "source": "test",
        "stage": "signal_generation", "stage_status": "succeeded", "signal_id": "sig-1",
    }])
    now = datetime(2026, 7, 12, 0, 0, 1, tzinfo=timezone.utc)
    targets = load_slo_targets("paper")
    metrics = compute_data_quality_metrics(
        materializer.projections, environment="paper",
        source_watermarks=materializer.source_watermarks, now=now,
        stalled_after_seconds=targets.stalled_after_seconds,
    )
    return metrics, targets


def test_load_alert_rules_returns_six_declarative_rules():
    rules = load_alert_rules()
    assert {rule.code for rule in rules} == {
        "materializer_lag_breach", "correlation_completeness_breach",
        "reconciliation_completeness_breach", "sse_disconnect",
        "detail_api_p95_breach", "resolve_api_p95_breach",
    }


def test_load_alert_rules_rejects_invalid_comparator(tmp_path):
    path = tmp_path / "rules.json"
    path.write_text(json.dumps({"rules": [{
        "code": "bad", "metric_name": "materializer_lag_seconds", "comparator": "eq",
        "target_field": "producer_to_read_model_p95_seconds", "severity": "critical",
        "event_type": "trade_journey.slo.bad",
    }]}))
    with pytest.raises(ValueError, match="invalid comparator"):
        load_alert_rules(path)


def test_load_alert_rules_rejects_rule_without_threshold_source(tmp_path):
    path = tmp_path / "rules.json"
    path.write_text(json.dumps({"rules": [{
        "code": "bad", "metric_name": "materializer_lag_seconds", "comparator": "gt",
        "severity": "critical", "event_type": "trade_journey.slo.bad",
    }]}))
    with pytest.raises(ValueError, match="target_field or a threshold"):
        load_alert_rules(path)


def test_evaluate_aggregate_rules_is_driven_by_the_json_threshold():
    metrics, targets = _clean_metrics()
    default_firings = evaluate_aggregate_rules(metrics, targets)
    default_codes = {firing.rule.code for firing in default_firings}

    # Editing the rule threshold (not the Python) changes what fires: this
    # is the "executable alert-rule artifact" the TJ-E2E-011 review required.
    tightened = tuple(
        AlertRule(
            code=rule.code, metric_name=rule.metric_name, comparator=rule.comparator,
            severity=rule.severity, event_type=rule.event_type,
            target_field=None, threshold=-1.0,
        )
        if rule.code == "materializer_lag_breach" else rule
        for rule in load_alert_rules()
    )
    tightened_firings = evaluate_aggregate_rules(metrics, targets, tightened)
    tightened_codes = {firing.rule.code for firing in tightened_firings}
    assert "materializer_lag_breach" not in default_codes
    assert "materializer_lag_breach" in tightened_codes


def test_evaluate_aggregate_rules_skips_none_valued_metrics():
    metrics, targets = _clean_metrics()
    assert metrics.detail_api_p95_ms is None
    firings = evaluate_aggregate_rules(metrics, targets)
    assert "detail_api_p95_breach" not in {firing.rule.code for firing in firings}
