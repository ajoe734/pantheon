import json
from datetime import datetime, timezone

from services.trade_journey.dashboard import load_dashboard, render_dashboard_snapshot, validate_dashboard
from services.trade_journey.materializer import JourneyMaterializer
from services.trade_journey.slo_data_quality import compute_data_quality_metrics, load_slo_targets


def test_load_dashboard_has_panels():
    dashboard = load_dashboard()
    assert dashboard["schema_version"] == "trade_journey_slo_dashboard.v1"
    assert len(dashboard["panels"]) > 0


def test_dashboard_panels_all_reference_real_metrics_and_target_fields():
    dashboard = load_dashboard()
    assert validate_dashboard(dashboard) == ()


def test_validate_dashboard_flags_an_unknown_metric(tmp_path):
    path = tmp_path / "dashboard.json"
    path.write_text(json.dumps({
        "schema_version": "trade_journey_slo_dashboard.v1",
        "panels": [{"panel_id": "bogus", "title": "Bogus", "metric": "not_a_real_field", "unit": "count", "target_field": None}],
    }))
    dashboard = load_dashboard(path)
    assert validate_dashboard(dashboard) == ("bogus",)


def test_render_dashboard_snapshot_populates_live_values_and_targets():
    materializer = JourneyMaterializer()
    materializer.rebuild([{
        "event_id": "e1", "journey_id": "tj_1", "tenant_id": "tenant-a", "environment": "paper",
        "occurred_at": "2026-07-12T00:00:00Z", "source": "test",
        "stage": "signal_generation", "stage_status": "succeeded", "signal_id": "sig-1",
    }])
    now = datetime(2026, 7, 12, 0, 0, 5, tzinfo=timezone.utc)
    targets = load_slo_targets("paper")
    metrics = compute_data_quality_metrics(
        materializer.projections, environment="paper",
        source_watermarks=materializer.source_watermarks, now=now,
        stalled_after_seconds=targets.stalled_after_seconds,
    )
    snapshot = render_dashboard_snapshot(load_dashboard(), metrics, targets)
    assert snapshot["environment"] == "paper"
    panels_by_id = {panel["panel_id"]: panel for panel in snapshot["panels"]}
    assert panels_by_id["materializer_lag"]["value"] == metrics.materializer_lag_seconds
    assert panels_by_id["materializer_lag"]["target_value"] == targets.producer_to_read_model_p95_seconds
    assert panels_by_id["stalled_count"]["target_value"] is None


def test_render_dashboard_snapshot_applies_ms_target_unit_multiplier():
    materializer = JourneyMaterializer()
    materializer.rebuild([{
        "event_id": "e1", "journey_id": "tj_1", "tenant_id": "tenant-a", "environment": "paper",
        "occurred_at": "2026-07-12T00:00:00Z", "source": "test",
        "stage": "signal_generation", "stage_status": "succeeded", "signal_id": "sig-1",
    }])
    now = datetime(2026, 7, 12, 0, 0, 5, tzinfo=timezone.utc)
    targets = load_slo_targets("paper")
    metrics = compute_data_quality_metrics(
        materializer.projections, environment="paper",
        source_watermarks=materializer.source_watermarks, now=now,
        stalled_after_seconds=targets.stalled_after_seconds,
        detail_api_latencies_ms=[100.0],
    )
    snapshot = render_dashboard_snapshot(load_dashboard(), metrics, targets)
    panels_by_id = {panel["panel_id"]: panel for panel in snapshot["panels"]}
    assert panels_by_id["detail_api_p95"]["value"] == 100.0
    assert panels_by_id["detail_api_p95"]["target_value"] == targets.detail_api_p95_seconds * 1000
