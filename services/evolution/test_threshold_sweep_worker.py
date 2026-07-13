"""Tests for the threshold-breach producer (EVOCHAIN-001).

Covers:
- live-config loading is fail-closed on missing/malformed thresholds
- breach evaluation is fail-closed on missing identity fields / metric fields
- breach evaluation is idempotent: same summary + window bucket -> same
  telemetry event_id (and therefore the same downstream incident_id)
- the built payload is accepted end-to-end by the real
  ThresholdTelemetryIncidentConsumer + IncidentStore, and a rerun does not
  duplicate the open incident
- run_tick fails closed when telemetry cannot be fetched or no thresholds
  are configured, and never calls post_incident in that case
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.evolution.threshold_sweep_worker import (
    DEFAULT_CONFIG_PATH,
    evaluate_breaches,
    load_thresholds,
    run_tick,
)
from services.incident.incident import IncidentStore
from services.incidents.consumer import ThresholdTelemetryIncidentConsumer

THRESHOLDS = [
    {
        "metric_name": "rolling_drawdown_multiple",
        "signal_type": "performance_degradation",
        "policy_source": "EVOLUTION_REVIEW_AND_THRESHOLDS.md section 7.1",
        "summary_field": "drawdown",
        "comparator": "gt",
        "threshold_value": 1.25,
        "window": "paper-daily-sweep",
    },
    {
        "metric_name": "rolling_pnl_floor",
        "signal_type": "performance_degradation",
        "policy_source": "EVOLUTION_REVIEW_AND_THRESHOLDS.md section 7.1",
        "summary_field": "pnl",
        "comparator": "lt",
        "threshold_value": -500.0,
        "window": "paper-daily-sweep",
    },
]


def _summary(**overrides) -> dict:
    base = {
        "runtime_id": "runtime-evochain-001",
        "binding_id": "rb-evochain-001",
        "deployment_stage": "paper",
        "deployment_plan_id": "plan-evochain-001",
        "capital_pool_id": "pool-evochain-001",
        "persona_capital_binding_id": "pcb-evochain-001",
        "artifact_id": "artifact-evochain-001",
        "artifact_version": "1.0.0",
        "drawdown": 1.42,
        "pnl": -120.0,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# load_thresholds — fail-closed on bad live config
# ---------------------------------------------------------------------------

def test_default_config_file_loads_valid_thresholds():
    thresholds = load_thresholds(DEFAULT_CONFIG_PATH)
    assert thresholds
    metric_names = {t["metric_name"] for t in thresholds}
    assert "rolling_drawdown_multiple" in metric_names


def test_load_thresholds_missing_file_fails_closed():
    assert load_thresholds("/nonexistent/path/thresholds.json") == []


def test_load_thresholds_malformed_json_fails_closed(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load_thresholds(str(bad)) == []


def test_load_thresholds_drops_entries_missing_required_keys(tmp_path):
    cfg = tmp_path / "cfg.json"
    cfg.write_text(
        json.dumps({"thresholds": [{"metric_name": "x"}, THRESHOLDS[0]]}),
        encoding="utf-8",
    )
    loaded = load_thresholds(str(cfg))
    assert len(loaded) == 1
    assert loaded[0]["metric_name"] == "rolling_drawdown_multiple"


def test_load_thresholds_drops_unknown_comparator(tmp_path):
    cfg = tmp_path / "cfg.json"
    bad_entry = dict(THRESHOLDS[0])
    bad_entry["comparator"] = "worse_than"
    cfg.write_text(json.dumps({"thresholds": [bad_entry]}), encoding="utf-8")
    assert load_thresholds(str(cfg)) == []


# ---------------------------------------------------------------------------
# evaluate_breaches — fail-closed, never fabricates a breach
# ---------------------------------------------------------------------------

def test_evaluate_breaches_detects_drawdown_breach():
    payloads, diagnostics = evaluate_breaches([_summary()], THRESHOLDS, window_bucket="2026-07-13")
    assert len(payloads) == 1
    snapshot = payloads[0]["threshold_snapshot"]
    assert snapshot["metric_name"] == "rolling_drawdown_multiple"
    assert snapshot["observed_value"] == 1.42
    assert not diagnostics


def test_evaluate_breaches_no_breach_when_within_threshold():
    payloads, diagnostics = evaluate_breaches(
        [_summary(drawdown=1.0, pnl=10.0)], THRESHOLDS, window_bucket="2026-07-13"
    )
    assert payloads == []
    assert diagnostics == []


def test_evaluate_breaches_missing_identity_field_is_diagnostic_only():
    incomplete = _summary()
    del incomplete["capital_pool_id"]
    payloads, diagnostics = evaluate_breaches([incomplete], THRESHOLDS, window_bucket="2026-07-13")
    assert payloads == []
    assert any("missing identity fields" in d for d in diagnostics)


def test_evaluate_breaches_missing_metric_field_is_diagnostic_only():
    incomplete = _summary()
    del incomplete["drawdown"]
    del incomplete["pnl"]
    payloads, diagnostics = evaluate_breaches([incomplete], THRESHOLDS, window_bucket="2026-07-13")
    assert payloads == []
    assert len(diagnostics) == 2


def test_evaluate_breaches_non_numeric_metric_is_diagnostic_only():
    payloads, diagnostics = evaluate_breaches(
        [_summary(drawdown="not-a-number")], THRESHOLDS, window_bucket="2026-07-13"
    )
    assert all(p["threshold_snapshot"]["metric_name"] != "rolling_drawdown_multiple" for p in payloads)
    assert any("non-numeric" in d for d in diagnostics)


def test_evaluate_breaches_dedupe_key_stable_across_reruns():
    first, _ = evaluate_breaches([_summary()], THRESHOLDS, window_bucket="2026-07-13")
    second, _ = evaluate_breaches([_summary()], THRESHOLDS, window_bucket="2026-07-13")
    assert first[0]["telemetry_event"]["event_id"] == second[0]["telemetry_event"]["event_id"]


def test_evaluate_breaches_dedupe_key_changes_across_window_bucket():
    day1, _ = evaluate_breaches([_summary()], THRESHOLDS, window_bucket="2026-07-13")
    day2, _ = evaluate_breaches([_summary()], THRESHOLDS, window_bucket="2026-07-14")
    assert day1[0]["telemetry_event"]["event_id"] != day2[0]["telemetry_event"]["event_id"]


# ---------------------------------------------------------------------------
# End-to-end against the real incidents consumer — idempotent creation
# ---------------------------------------------------------------------------

def test_payload_accepted_by_real_consumer_and_idempotent_on_rerun():
    store = IncidentStore(path=None)
    consumer = ThresholdTelemetryIncidentConsumer(incident_store=store)

    payloads, _ = evaluate_breaches([_summary()], THRESHOLDS, window_bucket="2026-07-13")
    drawdown_payload = next(
        p for p in payloads if p["threshold_snapshot"]["metric_name"] == "rolling_drawdown_multiple"
    )

    first = consumer.consume(drawdown_payload)
    assert first.created is True
    assert first.incident.binding_id == "rb-evochain-001"
    assert first.incident.status == "open"

    second = consumer.consume(drawdown_payload)
    assert second.created is False
    assert second.incident.incident_id == first.incident.incident_id

    assert len(store.find_open_incidents()) == 1


def test_two_breached_metrics_on_same_binding_open_two_distinct_incidents():
    store = IncidentStore(path=None)
    consumer = ThresholdTelemetryIncidentConsumer(incident_store=store)

    payloads, _ = evaluate_breaches(
        [_summary(drawdown=1.42, pnl=-600.0)], THRESHOLDS, window_bucket="2026-07-13"
    )
    assert len(payloads) == 2

    results = [consumer.consume(p) for p in payloads]
    assert all(r.created for r in results)
    assert len({r.incident.incident_id for r in results}) == 2


# ---------------------------------------------------------------------------
# run_tick — fail-closed orchestration
# ---------------------------------------------------------------------------

def test_run_tick_fails_closed_when_no_thresholds_configured():
    calls = []

    def fetch(*_args, **_kwargs):
        calls.append("fetch")
        return [_summary()]

    result = run_tick(
        telemetry_api_url="http://telemetry.test",
        incidents_api_url="http://incidents.test",
        thresholds=[],
        fetch_summaries=fetch,
    )
    assert result["candidates"] == 0
    assert not calls
    assert any("no valid thresholds" in d for d in result["diagnostics"])


def test_run_tick_fails_closed_when_telemetry_fetch_errors():
    def fetch(*_args, **_kwargs):
        raise OSError("connection refused")

    def post(*_args, **_kwargs):  # pragma: no cover - must never be called
        raise AssertionError("post_incident must not be called when telemetry fetch fails")

    result = run_tick(
        telemetry_api_url="http://telemetry.test",
        incidents_api_url="http://incidents.test",
        thresholds=THRESHOLDS,
        fetch_summaries=fetch,
        post_incident=post,
    )
    assert result["incidents_created"] == 0
    assert result["errors"] == 0
    assert any("telemetry fetch failed" in d for d in result["diagnostics"])


def test_run_tick_creates_then_dedupes_on_rerun_via_real_consumer():
    store = IncidentStore(path=None)
    consumer = ThresholdTelemetryIncidentConsumer(incident_store=store)

    def fetch(*_args, **_kwargs):
        return [_summary()]

    def post(_url, payload, **_kwargs):
        result = consumer.consume(payload)
        return {"status": 201 if result.created else 200, "body": {}}

    now = datetime(2026, 7, 13, tzinfo=timezone.utc)

    first = run_tick(
        telemetry_api_url="http://telemetry.test",
        incidents_api_url="http://incidents.test",
        thresholds=THRESHOLDS,
        fetch_summaries=fetch,
        post_incident=post,
        now=now,
    )
    assert first["incidents_created"] == 1
    assert first["incidents_deduped"] == 0

    second = run_tick(
        telemetry_api_url="http://telemetry.test",
        incidents_api_url="http://incidents.test",
        thresholds=THRESHOLDS,
        fetch_summaries=fetch,
        post_incident=post,
        now=now,
    )
    assert second["incidents_created"] == 0
    assert second["incidents_deduped"] == 1
    assert len(store.find_open_incidents()) == 1
