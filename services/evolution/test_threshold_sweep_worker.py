"""Tests for the threshold-breach producer (EVOCHAIN-001).

Covers:
- live-config loading is fail-closed on missing/malformed thresholds and
  drops entries that are disabled (uncalibrated / unapproved) or declare a
  telemetry_event_type outside the schema enum
- baseline loading is fail-closed on missing/malformed baseline config
- breach evaluation is fail-closed on missing identity fields / metric
  fields / missing per-artifact baseline, and on non-paper or stale/degraded
  summaries
- the raw runtime-summary metric (produced by the real
  RuntimeSummaryProjectionStore, not a hand-baked ratio) is turned into a
  unit-consistent multiple via the live-config baseline before comparison
- breach evaluation is idempotent: same summary + window bucket -> same
  telemetry event_id (and therefore the same downstream incident_id)
- the derived telemetry_event is schema-valid (services/telemetry/
  telemetry_event.schema.json) and passes the real ingest evidence-contract
  checks, unlike the original synthetic envelope
- the built payload is accepted end-to-end by the real
  ThresholdTelemetryIncidentConsumer + IncidentStore, and a rerun does not
  duplicate the open incident
- the built payload passes the real (unmocked) CanonicalReferenceValidator
  over the real /api/incidents/consume-threshold route, given lineage/binding
  lookups that reflect what the canonical stores would return once wired to
  this binding/event (see EVOCHAIN-001-threshold-breach-producer.md "Known
  platform gap" for why fakes are required today)
- run_tick fails closed when telemetry cannot be fetched, no thresholds are
  configured, or telemetry ingest rejects the derived event, and never calls
  post_incident in those cases
"""
from __future__ import annotations

import json
import sys
import urllib.error
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi.testclient import TestClient

# NOTE: services.telemetry must be imported before services.incidents.main —
# services/incidents/main.py inserts the bare `services/` directory onto
# sys.path (to support its own fallback bare imports), which shadows
# services/telemetry/feedback_adapter.py's `import feedback` resolution
# (it wants services/control-plane/feedback, not services/feedback) unless
# services.telemetry has already been imported and cached first.
from services.telemetry.ingest_svc import TelemetryIngestService
from services.telemetry.runtime_summary import RuntimeSummaryProjectionStore

from services.evolution.threshold_sweep_worker import (
    DEFAULT_BASELINES_PATH,
    DEFAULT_CONFIG_PATH,
    evaluate_breaches,
    load_baselines,
    load_thresholds,
    run_tick,
)
from services.incident.incident import IncidentStore
from services.incident.reference_validation import CanonicalReferenceValidator
from services.incidents.consumer import ThresholdTelemetryIncidentConsumer
from services.incidents.main import app

_SCHEMA_PATH = str(
    Path(__file__).resolve().parents[1] / "telemetry" / "telemetry_event.schema.json"
)

THRESHOLDS = [
    {
        "metric_name": "rolling_drawdown_multiple",
        "signal_type": "performance_degradation",
        "policy_source": "EVOLUTION_REVIEW_AND_THRESHOLDS.md section 7.1",
        "summary_field": "drawdown",
        "ratio_baseline_key": "expected_drawdown",
        "telemetry_event_type": "drawdown_snapshot",
        "comparator": "gt",
        "threshold_value": 1.25,
        "window": "paper-daily-sweep",
        "enabled": True,
    },
    {
        "metric_name": "rolling_pnl_floor",
        "signal_type": "performance_degradation",
        "policy_source": "EVOLUTION_REVIEW_AND_THRESHOLDS.md section 7.1",
        "summary_field": "pnl",
        "ratio_baseline_key": None,
        "telemetry_event_type": "pnl_snapshot",
        "comparator": "lt",
        "threshold_value": -500.0,
        "window": "paper-daily-sweep",
        "enabled": True,
    },
]

BASELINES = {"artifact-evochain-001": {"expected_drawdown": 0.12}}

# Fixed reference "now" for freshness checks: 30 seconds after the fixtures'
# heartbeat/metric as-of times below — within RuntimeSummaryProjectionStore's
# default 90s heartbeat-staleness window *and* this worker's metric-freshness
# window, and deterministic across real test-run times.
_NOW = datetime(2026, 7, 13, 0, 0, 30, tzinfo=timezone.utc)


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
        "drawdown": 0.18,  # raw fraction; ratio vs 0.12 baseline = 1.5 > 1.25 -> breach
        "pnl": -120.0,
        "last_heartbeat_at": "2026-07-13T00:00:00Z",
        "drawdown_at": "2026-07-13T00:00:00Z",
        "pnl_at": "2026-07-13T00:00:00Z",
    }
    base.update(overrides)
    return base


def _seed_real_summary(store: RuntimeSummaryProjectionStore, **event_overrides) -> dict:
    """Project real telemetry-shaped events and return the store's summary.

    Used so tests exercise the actual RuntimeSummaryProjectionStore projection
    instead of hand-crafting a summary dict with the ratio (or freshness
    markers) already baked in. Seeds a heartbeat event first (affirmative
    freshness signal) and then the metric event.
    """
    heartbeat_event = {
        "runtime_id": "runtime-evochain-001",
        "binding_id": "rb-evochain-001",
        "deployment_stage": "paper",
        "capital_pool_id": "pool-evochain-001",
        "artifact_id": "artifact-evochain-001",
        "artifact_version": "1.0.0",
        "plan_id": "plan-evochain-001",
        "persona_capital_binding_id": "pcb-evochain-001",
        "event_id": f"evt-seed-heartbeat-{uuid.uuid4().hex[:8]}",
        "event_type": "heartbeat",
        "created_at": "2026-07-13T00:00:00Z",
        "metadata": {"connectivity_status": "connected"},
        "metrics": {"heartbeat": 1},
    }
    store.project_event(heartbeat_event)

    event = {
        "runtime_id": "runtime-evochain-001",
        "binding_id": "rb-evochain-001",
        "deployment_stage": "paper",
        "capital_pool_id": "pool-evochain-001",
        "artifact_id": "artifact-evochain-001",
        "artifact_version": "1.0.0",
        "plan_id": "plan-evochain-001",
        "persona_capital_binding_id": "pcb-evochain-001",
        "event_id": f"evt-seed-{uuid.uuid4().hex[:8]}",
        "event_type": "drawdown_snapshot",
        "created_at": "2026-07-13T00:00:00Z",
        "metrics": {"drawdown_pct": 0.18, "pnl": -120.0},
    }
    event.update(event_overrides)
    store.project_event(event)
    return store.get(event["runtime_id"], now=_NOW)


# ---------------------------------------------------------------------------
# load_thresholds — fail-closed on bad live config; disabled entries dropped
# ---------------------------------------------------------------------------

def test_default_config_file_loads_only_enabled_thresholds():
    thresholds = load_thresholds(DEFAULT_CONFIG_PATH)
    metric_names = {t["metric_name"] for t in thresholds}
    assert "rolling_drawdown_multiple" in metric_names
    # The PnL floor is an unapproved placeholder and must not be default-on.
    assert "rolling_pnl_floor" not in metric_names


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


def test_load_thresholds_drops_unknown_telemetry_event_type(tmp_path):
    cfg = tmp_path / "cfg.json"
    bad_entry = dict(THRESHOLDS[0])
    bad_entry["telemetry_event_type"] = "threshold_sweep_snapshot"
    cfg.write_text(json.dumps({"thresholds": [bad_entry]}), encoding="utf-8")
    assert load_thresholds(str(cfg)) == []


def test_load_thresholds_drops_disabled_entries(tmp_path):
    cfg = tmp_path / "cfg.json"
    disabled_entry = dict(THRESHOLDS[0])
    disabled_entry["enabled"] = False
    cfg.write_text(json.dumps({"thresholds": [disabled_entry]}), encoding="utf-8")
    assert load_thresholds(str(cfg)) == []


def test_load_thresholds_drops_truthy_non_bool_enabled(tmp_path):
    """`"false"` is truthy in Python; a live-config typo must not activate an
    uncalibrated/unapproved threshold (round-2 review: "`enabled` is
    truthiness-based (`"false"` enables an entry)")."""
    cfg = tmp_path / "cfg.json"
    entry = dict(THRESHOLDS[0])
    entry["enabled"] = "false"
    cfg.write_text(json.dumps({"thresholds": [entry]}), encoding="utf-8")
    assert load_thresholds(str(cfg)) == []


def test_load_thresholds_drops_unhashable_comparator_without_raising(tmp_path):
    """An unhashable JSON value for `comparator` must be dropped fail-closed
    at load time, not raise TypeError and restart-loop the default-on worker
    (round-2 review point 5)."""
    cfg = tmp_path / "cfg.json"
    entry = dict(THRESHOLDS[0])
    entry["comparator"] = ["gt"]
    cfg.write_text(json.dumps({"thresholds": [entry]}), encoding="utf-8")
    assert load_thresholds(str(cfg)) == []


def test_load_thresholds_drops_unhashable_telemetry_event_type_without_raising(tmp_path):
    cfg = tmp_path / "cfg.json"
    entry = dict(THRESHOLDS[0])
    entry["telemetry_event_type"] = {"nested": "value"}
    cfg.write_text(json.dumps({"thresholds": [entry]}), encoding="utf-8")
    assert load_thresholds(str(cfg)) == []


def test_load_thresholds_drops_unhashable_ratio_baseline_key_without_raising(tmp_path):
    """A list `ratio_baseline_key` would raise TypeError from
    `dict.get(unhashable)` in evaluate_breaches() if it reached that far;
    it must be dropped here instead."""
    cfg = tmp_path / "cfg.json"
    entry = dict(THRESHOLDS[0])
    entry["ratio_baseline_key"] = ["expected_drawdown"]
    cfg.write_text(json.dumps({"thresholds": [entry]}), encoding="utf-8")
    assert load_thresholds(str(cfg)) == []


def test_load_thresholds_drops_non_finite_threshold_value(tmp_path):
    cfg = tmp_path / "cfg.json"
    entry = dict(THRESHOLDS[0])
    entry["threshold_value"] = float("nan")
    cfg.write_text(json.dumps({"thresholds": [entry]}, allow_nan=True), encoding="utf-8")
    assert load_thresholds(str(cfg)) == []


def test_load_thresholds_drops_huge_integer_threshold_value_without_raising(tmp_path):
    """A JSON integer with no fixed size (e.g. 10**1000) is valid JSON and a
    valid Python int, but math.isfinite() raises OverflowError converting it
    to a float; it must be dropped fail-closed here instead of crashing the
    worker (round-3 review point 4)."""
    cfg = tmp_path / "cfg.json"
    entry = dict(THRESHOLDS[0])
    entry["threshold_value"] = 10**1000
    cfg.write_text(json.dumps({"thresholds": [entry]}), encoding="utf-8")
    assert load_thresholds(str(cfg)) == []


# ---------------------------------------------------------------------------
# load_baselines — fail-closed on missing/malformed live config
# ---------------------------------------------------------------------------

def test_default_baselines_file_loads_empty_mapping():
    # Ships with no approved baselines; the drawdown threshold fails closed
    # until an operator adds one.
    assert load_baselines(DEFAULT_BASELINES_PATH) == {}


def test_load_baselines_missing_file_fails_closed():
    assert load_baselines("/nonexistent/path/baselines.json") == {}


def test_load_baselines_malformed_json_fails_closed(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load_baselines(str(bad)) == {}


def test_load_baselines_reads_per_artifact_values(tmp_path):
    cfg = tmp_path / "baselines.json"
    cfg.write_text(json.dumps({"baselines": BASELINES}), encoding="utf-8")
    assert load_baselines(str(cfg)) == BASELINES


# ---------------------------------------------------------------------------
# evaluate_breaches — fail-closed, never fabricates a breach
# ---------------------------------------------------------------------------

def test_evaluate_breaches_detects_drawdown_breach_from_real_projection():
    projection_store = RuntimeSummaryProjectionStore(path=None)
    summary = _seed_real_summary(projection_store)

    payloads, diagnostics = evaluate_breaches(
        [summary], THRESHOLDS, window_bucket="2026-07-13", baselines=BASELINES, now=_NOW
    )

    drawdown_payloads = [p for p in payloads if p["threshold_snapshot"]["metric_name"] == "rolling_drawdown_multiple"]
    assert len(drawdown_payloads) == 1
    snapshot = drawdown_payloads[0]["threshold_snapshot"]
    # Raw projected value stays the raw metric; observed_value is the
    # baseline-normalized multiple actually compared to threshold_value.
    assert snapshot["raw_observed_value"] == 0.18
    assert snapshot["observed_value"] == 1.5
    assert not any("rolling_drawdown_multiple" in d for d in diagnostics)


def test_evaluate_breaches_missing_baseline_is_diagnostic_only_fail_closed():
    payloads, diagnostics = evaluate_breaches(
        [_summary()], THRESHOLDS, window_bucket="2026-07-13", baselines={}, now=_NOW
    )
    assert all(p["threshold_snapshot"]["metric_name"] != "rolling_drawdown_multiple" for p in payloads)
    assert any("no approved" in d and "expected_drawdown" in d for d in diagnostics)


def test_evaluate_breaches_no_breach_when_within_threshold():
    payloads, diagnostics = evaluate_breaches(
        [_summary(drawdown=0.10, pnl=10.0)], THRESHOLDS, window_bucket="2026-07-13", baselines=BASELINES, now=_NOW
    )
    assert payloads == []
    assert diagnostics == []


def test_evaluate_breaches_skips_non_paper_stage():
    payloads, diagnostics = evaluate_breaches(
        [_summary(deployment_stage="canary")], THRESHOLDS, window_bucket="2026-07-13", baselines=BASELINES, now=_NOW
    )
    assert payloads == []
    assert any("not eligible for the paper threshold sweep" in d for d in diagnostics)


def test_evaluate_breaches_skips_stale_summary():
    projection_store = RuntimeSummaryProjectionStore(path=None, heartbeat_stale_after_seconds=90)
    heartbeat_event = {
        "runtime_id": "runtime-evochain-001",
        "binding_id": "rb-evochain-001",
        "deployment_stage": "paper",
        "capital_pool_id": "pool-evochain-001",
        "artifact_id": "artifact-evochain-001",
        "artifact_version": "1.0.0",
        "plan_id": "plan-evochain-001",
        "persona_capital_binding_id": "pcb-evochain-001",
        "event_id": "evt-heartbeat-001",
        "event_type": "heartbeat",
        "created_at": "2026-07-13T00:00:00Z",
        "metadata": {"connectivity_status": "connected"},
        "metrics": {"heartbeat": 1},
    }
    projection_store.project_event(heartbeat_event)
    _seed_real_summary(projection_store)
    far_future = datetime(2026, 7, 13, 2, 0, 0, tzinfo=timezone.utc)
    stale_summary = projection_store.get("runtime-evochain-001", now=far_future)

    payloads, diagnostics = evaluate_breaches(
        [stale_summary], THRESHOLDS, window_bucket="2026-07-13", baselines=BASELINES, now=_NOW
    )
    assert payloads == []
    assert any("stale/degraded" in d for d in diagnostics)


def test_evaluate_breaches_missing_heartbeat_is_diagnostic_only_fail_closed():
    """A real summary with no heartbeat/state/connectivity must not read as
    healthy just because it carries no explicit bad marker (round-2 review:
    "a real summary with no heartbeat/state/connectivity is accepted")."""
    projection_store = RuntimeSummaryProjectionStore(path=None)
    drawdown_event = {
        "runtime_id": "runtime-evochain-001",
        "binding_id": "rb-evochain-001",
        "deployment_stage": "paper",
        "capital_pool_id": "pool-evochain-001",
        "artifact_id": "artifact-evochain-001",
        "artifact_version": "1.0.0",
        "plan_id": "plan-evochain-001",
        "persona_capital_binding_id": "pcb-evochain-001",
        "event_id": "evt-no-heartbeat-001",
        "event_type": "drawdown_snapshot",
        "created_at": "2026-07-13T00:00:00Z",
        "metrics": {"drawdown_pct": 0.30, "pnl": -120.0},  # would breach if evaluated
    }
    no_heartbeat_summary = projection_store.project_event(drawdown_event)
    assert "last_heartbeat_at" not in no_heartbeat_summary

    payloads, diagnostics = evaluate_breaches(
        [no_heartbeat_summary], THRESHOLDS, window_bucket="2026-07-13", baselines=BASELINES, now=_NOW
    )
    assert payloads == []
    assert any("stale/degraded" in d for d in diagnostics)


def test_evaluate_breaches_old_metric_with_fresh_heartbeat_is_diagnostic_only_fail_closed():
    """A fresh heartbeat must not mask an old metric value (round-2 review:
    "a fresh heartbeat masks an arbitrarily old drawdown value... a probe
    with a 12-day-old drawdown plus a fresh heartbeat produced one breach
    and no diagnostic")."""
    projection_store = RuntimeSummaryProjectionStore(path=None)
    old_drawdown_event = {
        "runtime_id": "runtime-evochain-001",
        "binding_id": "rb-evochain-001",
        "deployment_stage": "paper",
        "capital_pool_id": "pool-evochain-001",
        "artifact_id": "artifact-evochain-001",
        "artifact_version": "1.0.0",
        "plan_id": "plan-evochain-001",
        "persona_capital_binding_id": "pcb-evochain-001",
        "event_id": "evt-old-drawdown-001",
        "event_type": "drawdown_snapshot",
        "created_at": "2026-07-01T00:00:00Z",  # 12 days before the fresh heartbeat below
        "metrics": {"drawdown_pct": 0.30, "pnl": -120.0},  # would breach if evaluated
    }
    projection_store.project_event(old_drawdown_event)

    fresh_heartbeat_event = {
        "runtime_id": "runtime-evochain-001",
        "binding_id": "rb-evochain-001",
        "deployment_stage": "paper",
        "capital_pool_id": "pool-evochain-001",
        "artifact_id": "artifact-evochain-001",
        "artifact_version": "1.0.0",
        "plan_id": "plan-evochain-001",
        "persona_capital_binding_id": "pcb-evochain-001",
        "event_id": "evt-fresh-heartbeat-001",
        "event_type": "heartbeat",
        "created_at": "2026-07-13T00:00:00Z",
        "metadata": {"connectivity_status": "connected"},
        "metrics": {"heartbeat": 1},
    }
    projection_store.project_event(fresh_heartbeat_event)

    summary = projection_store.get("runtime-evochain-001", now=_NOW)
    assert "staleness" not in summary  # heartbeat itself is fresh

    payloads, diagnostics = evaluate_breaches(
        [summary], THRESHOLDS, window_bucket="2026-07-13", baselines=BASELINES, now=_NOW
    )
    assert all(p["threshold_snapshot"]["metric_name"] != "rolling_drawdown_multiple" for p in payloads)
    assert any("no fresh as-of time" in d for d in diagnostics)


def test_evaluate_breaches_missing_identity_field_is_diagnostic_only():
    incomplete = _summary()
    del incomplete["capital_pool_id"]
    payloads, diagnostics = evaluate_breaches(
        [incomplete], THRESHOLDS, window_bucket="2026-07-13", baselines=BASELINES, now=_NOW
    )
    assert payloads == []
    assert any("missing identity fields" in d for d in diagnostics)


def test_evaluate_breaches_missing_metric_field_is_diagnostic_only():
    incomplete = _summary()
    del incomplete["drawdown"]
    del incomplete["pnl"]
    payloads, diagnostics = evaluate_breaches(
        [incomplete], THRESHOLDS, window_bucket="2026-07-13", baselines=BASELINES, now=_NOW
    )
    assert payloads == []
    assert len(diagnostics) == 2


def test_evaluate_breaches_non_numeric_metric_is_diagnostic_only():
    payloads, diagnostics = evaluate_breaches(
        [_summary(drawdown="not-a-number")], THRESHOLDS, window_bucket="2026-07-13", baselines=BASELINES, now=_NOW
    )
    assert all(p["threshold_snapshot"]["metric_name"] != "rolling_drawdown_multiple" for p in payloads)
    assert any("non-numeric" in d for d in diagnostics)


def test_evaluate_breaches_dedupe_key_stable_across_reruns():
    first, _ = evaluate_breaches([_summary()], THRESHOLDS, window_bucket="2026-07-13", baselines=BASELINES, now=_NOW)
    second, _ = evaluate_breaches([_summary()], THRESHOLDS, window_bucket="2026-07-13", baselines=BASELINES, now=_NOW)
    assert first[0]["telemetry_event"]["event_id"] == second[0]["telemetry_event"]["event_id"]


def test_evaluate_breaches_dedupe_key_changes_across_window_bucket():
    day1, _ = evaluate_breaches([_summary()], THRESHOLDS, window_bucket="2026-07-13", baselines=BASELINES, now=_NOW)
    day2, _ = evaluate_breaches([_summary()], THRESHOLDS, window_bucket="2026-07-14", baselines=BASELINES, now=_NOW)
    assert day1[0]["telemetry_event"]["event_id"] != day2[0]["telemetry_event"]["event_id"]


# ---------------------------------------------------------------------------
# Derived telemetry_event is schema-valid and passes ingest evidence checks
# ---------------------------------------------------------------------------

def test_derived_telemetry_event_is_schema_valid_and_ingest_admissible():
    payloads, _ = evaluate_breaches([_summary()], THRESHOLDS, window_bucket="2026-07-13", baselines=BASELINES, now=_NOW)
    drawdown_event = next(
        p for p in payloads if p["threshold_snapshot"]["metric_name"] == "rolling_drawdown_multiple"
    )["telemetry_event"]

    ingest = TelemetryIngestService(schema_path=_SCHEMA_PATH)
    schema_ok, schema_err = ingest._validate_event(drawdown_event)
    assert schema_ok, schema_err
    evidence_ok, evidence_err, _ = ingest._validate_evidence_contract(drawdown_event)
    assert evidence_ok, evidence_err

    # uuid5-derived event_id must be a real RFC4122 UUID string, not a
    # prefixed/truncated hex fragment.
    assert str(uuid.UUID(drawdown_event["event_id"])) == drawdown_event["event_id"]


def test_original_synthetic_envelope_shape_would_have_failed_ingest():
    """Regression guard: the pre-fix envelope shape is rejected by real ingest."""
    synthetic_event = {
        "event_id": "tel-threshold-sweep-deadbeefcafef00d",
        "event_type": "threshold_sweep_snapshot",
        "created_at": "2026-07-13T00:00:00Z",
        "runtime_binding_id": "rb-evochain-001",
        "deployment_stage": "paper",
        "deployment_plan_id": "plan-evochain-001",
        "capital_pool_id": "pool-evochain-001",
        "persona_capital_binding_id": "pcb-evochain-001",
        "artifact_id": "artifact-evochain-001",
        "artifact_version": "1.0.0",
        "runtime_id": "runtime-evochain-001",
        "trace_id": "trace-threshold-sweep-deadbeefcafef00d",
        "metrics": {"drawdown": 1.42},
        "description": "Threshold sweep: rolling_drawdown_multiple observed=1.42 gt threshold=1.25",
    }
    ingest = TelemetryIngestService(schema_path=_SCHEMA_PATH)
    ok, err = ingest._validate_event(synthetic_event)
    assert not ok
    assert err


# ---------------------------------------------------------------------------
# End-to-end against the real incidents consumer — idempotent creation
# ---------------------------------------------------------------------------

def test_payload_accepted_by_real_consumer_and_idempotent_on_rerun():
    store = IncidentStore(path=None)
    consumer = ThresholdTelemetryIncidentConsumer(incident_store=store)

    payloads, _ = evaluate_breaches([_summary()], THRESHOLDS, window_bucket="2026-07-13", baselines=BASELINES, now=_NOW)
    drawdown_payload = next(
        p for p in payloads if p["threshold_snapshot"]["metric_name"] == "rolling_drawdown_multiple"
    )

    first = consumer.consume(drawdown_payload)
    assert first.created is True
    assert first.incident.binding_id == "rb-evochain-001"
    assert first.incident.status == "open"
    # dedupe_key is the operator-facing audit trail proving why a rerun
    # deduped instead of opening a second incident; it must survive into
    # canonical incident evidence, not be dropped by the consumer.
    assert "dedupe_key=" in (first.incident.evidence_summary or "")

    second = consumer.consume(drawdown_payload)
    assert second.created is False
    assert second.incident.incident_id == first.incident.incident_id

    assert len(store.find_open_incidents()) == 1


def test_two_breached_metrics_on_same_binding_open_two_distinct_incidents():
    store = IncidentStore(path=None)
    consumer = ThresholdTelemetryIncidentConsumer(incident_store=store)

    payloads, _ = evaluate_breaches(
        [_summary(drawdown=0.30, pnl=-600.0)], THRESHOLDS, window_bucket="2026-07-13", baselines=BASELINES, now=_NOW
    )
    assert len(payloads) == 2

    results = [consumer.consume(p) for p in payloads]
    assert all(r.created for r in results)
    assert len({r.incident.incident_id for r in results}) == 2


# ---------------------------------------------------------------------------
# Real HTTP route with canonical reference validation enabled (not bypassed)
# ---------------------------------------------------------------------------

class _FakeBindingLookup:
    def __init__(self, binding: dict) -> None:
        self._binding = binding

    def get_binding(self, binding_id: str):
        if self._binding.get("binding_id") != binding_id:
            return None
        return dict(self._binding)


class _FakeTelemetryLookup:
    def __init__(self, *, event_traces: dict, binding_projection: dict) -> None:
        self._event_traces = event_traces
        self._binding_projection = binding_projection

    def telemetry_event_trace(self, event_id: str):
        return self._event_traces.get(event_id)

    def runtime_binding_projection(self, binding_id: str):
        if self._binding_projection.get("target_id") != binding_id:
            return None
        return self._binding_projection


client = TestClient(app)


def test_consume_threshold_route_passes_real_canonical_reference_validator(monkeypatch):
    """Real /api/incidents/consume-threshold route, real CanonicalReferenceValidator.

    Uses injected fake binding/telemetry lookups (the same pattern
    services/incident/test_reference_validation.py uses) shaped exactly like
    what the canonical RuntimeBinding + telemetry lineage stores would return
    once this binding/event is registered there. This proves the worker's
    payload is reference-shape-consistent with the real validator's matching
    rules, which is the part this task owns; see
    EVOCHAIN-001-threshold-breach-producer.md for the pre-existing platform
    gap (LIN-001A/LIN-002 lineage is a static benchmark corpus, not a live
    index of ingested events) that keeps this from resolving against the
    *default* unmocked validator today.

    Uses an injected in-memory IncidentStore (not the module-level `store`,
    which persists to the developer/runtime-shared
    `/tmp/pantheon/incidents/incidents.json` file) so this test cannot leak a
    write into that file.
    """
    monkeypatch.setattr("services.incidents.main.store", IncidentStore(path=None))

    payloads, _ = evaluate_breaches([_summary()], THRESHOLDS, window_bucket="2026-07-13", baselines=BASELINES, now=_NOW)
    drawdown_payload = next(
        p for p in payloads if p["threshold_snapshot"]["metric_name"] == "rolling_drawdown_multiple"
    )
    event = drawdown_payload["telemetry_event"]
    artifact_ref = f"{event['artifact_id']}@{event['artifact_version']}"

    binding_record = {
        "binding_id": event["binding_id"],
        "runtime_id": event["runtime_id"],
        "capital_pool_id": event["capital_pool_id"],
        "artifact_id": event["artifact_id"],
        "artifact_version": event["artifact_version"],
        "deployment_mode": event["deployment_stage"],
        "effective_at": "2026-01-01T00:00:00Z",
        "retired_at": None,
        "plan_id": event["plan_id"],
        "persona_capital_binding_id": event["persona_capital_binding_id"],
    }
    event_trace = {
        "target_type": "telemetry_event",
        "target_id": event["event_id"],
        "refs": {
            "runtime_binding_ids": [event["binding_id"]],
            "deployment_plan_ids": [event["plan_id"]],
            "capital_pool_ids": [event["capital_pool_id"]],
            "persona_capital_binding_ids": [event["persona_capital_binding_id"]],
            "artifact_refs": [artifact_ref],
            "trace_ids": [event["trace_id"]],
        },
        "upstream_chain": [],
        "downstream_chain": [{"type": "runtime_ref", "id": event["runtime_id"]}],
        "conflict_markers": [],
    }
    binding_projection = {
        "target_type": "runtime_binding",
        "target_id": event["binding_id"],
        "refs": {"artifact_refs": [artifact_ref]},
        "upstream_chain": [],
        "downstream_chain": [],
        "conflict_markers": [],
    }

    validator = CanonicalReferenceValidator(
        binding_lookup=_FakeBindingLookup(binding_record),
        telemetry_lookup=_FakeTelemetryLookup(
            event_traces={event["event_id"]: event_trace},
            binding_projection=binding_projection,
        ),
    )
    monkeypatch.setattr("services.incidents.main.reference_validator", validator)

    r = client.post("/api/incidents/consume-threshold", json=drawdown_payload)
    assert r.status_code == 201, r.text
    assert r.json()["binding_id"] == event["binding_id"]


def test_consume_threshold_route_succeeds_against_default_reference_validator(monkeypatch):
    """Real route, real DEFAULT (unmocked) reference_validator.

    Now that LIN-003 has landed, live-ingested telemetry events successfully resolve
    through the lineage read write path. We verify that the default
    CanonicalReferenceValidator returns 201 when the binding and telemetry
    data are present.
    """
    monkeypatch.setattr("services.incidents.main.store", IncidentStore(path=None))

    binding_record = {
        "binding_id": "rb-paper-1",
        "runtime_id": "rt-1",
        "deployment_mode": "paper",
        "plan_id": "plan-1",
        "capital_pool_id": "pool-1",
        "persona_capital_binding_id": "pcb-1",
        "artifact_id": "art-1",
        "artifact_version": "1.0.0",
        "effective_at": "2026-01-01T00:00:00Z",
        "retired_at": None,
    }
    event_trace = {
        "target_type": "telemetry_event",
        "target_id": "evt-1",
        "refs": {
            "runtime_binding_ids": ["rb-paper-1"],
            "deployment_plan_ids": ["plan-1"],
            "capital_pool_ids": ["pool-1"],
            "persona_capital_binding_ids": ["pcb-1"],
            "artifact_refs": ["art-1@1.0.0"],
            "trace_ids": ["trace-1"],
        },
        "upstream_chain": [],
        "downstream_chain": [{"type": "runtime_ref", "id": "rt-1"}],
        "conflict_markers": [],
    }
    binding_projection = {
        "target_type": "runtime_binding",
        "target_id": "rb-paper-1",
        "refs": {"artifact_refs": ["art-1@1.0.0"]},
        "upstream_chain": [],
        "downstream_chain": [],
        "conflict_markers": [],
    }

    monkeypatch.setattr(
        "services.incident.reference_validation._RuntimeBindingLookup.get_binding",
        lambda self, bid: binding_record if bid == "rb-paper-1" else None,
    )
    monkeypatch.setattr(
        "services.incident.reference_validation._TelemetryLineageLookup.telemetry_event_trace",
        lambda self, eid: event_trace if eid == "evt-1" else None,
    )
    monkeypatch.setattr(
        "services.incident.reference_validation._TelemetryLineageLookup.runtime_binding_projection",
        lambda self, bid: binding_projection if bid == "rb-paper-1" else None,
    )

    monkeypatch.setattr(
        "services.incidents.main.reference_validator",
        CanonicalReferenceValidator(),
    )

    # First, test the failure (422) case if a telemetry event is not found (simulating no ingest).
    bad_payload = {
        "incident_id": "inc-1",
        "title": "drawdown breach",
        "telemetry_event": {
            "event_id": "evt-missing",
            "event_type": "drawdown_snapshot",
            "created_at": _NOW.isoformat().replace("+00:00", "Z"),
            "execution_mode": "paper",
            "binding_id": "rb-paper-1",
            "runtime_id": "rt-1",
            "capital_pool_id": "pool-1",
            "artifact_id": "art-1",
            "artifact_version": "1.0.0",
            "deployment_stage": "paper",
            "plan_id": "plan-1",
            "persona_capital_binding_id": "pcb-1",
            "trace_id": "trace-1",
            "target": {"strategy_id": "art-1"},
            "metrics": {"drawdown_pct": 1.5},
        },
        "threshold_snapshot": {
            "policy_source": "EVOLUTION_REVIEW_AND_THRESHOLDS.md section 7.1",
            "signal_type": "performance_degradation",
            "metric_name": "rolling_drawdown_multiple",
            "comparator": "gt",
            "raw_observed_value": 1.5,
            "observed_value": 1.5,
            "threshold_value": 1.25,
            "window": "paper-daily-sweep:2026-07-13",
            "breached": True,
            "note": "dedupe_key=rb-paper-1:rolling_drawdown_multiple:paper-daily-sweep:2026-07-13",
        },
    }
    r = client.post("/api/incidents/consume-threshold", json=bad_payload)
    assert r.status_code == 422, r.text
    assert "reference_errors" in r.json()["detail"]

    # Now, test the success case when the event is found (simulating ingest).
    good_payload = dict(bad_payload)
    good_payload["telemetry_event"] = dict(bad_payload["telemetry_event"])
    good_payload["telemetry_event"]["event_id"] = "evt-1"
    r = client.post("/api/incidents/consume-threshold", json=good_payload)
    assert r.status_code == 201, r.text
    assert r.json()["binding_id"] == "rb-paper-1"


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

    def admit(*_args, **_kwargs):  # pragma: no cover - must never be called
        raise AssertionError("admit_telemetry_event must not be called when telemetry fetch fails")

    def post(*_args, **_kwargs):  # pragma: no cover - must never be called
        raise AssertionError("post_incident must not be called when telemetry fetch fails")

    result = run_tick(
        telemetry_api_url="http://telemetry.test",
        incidents_api_url="http://incidents.test",
        thresholds=THRESHOLDS,
        baselines=BASELINES,
        fetch_summaries=fetch,
        admit_telemetry_event=admit,
        post_incident=post,
    )
    assert result["incidents_created"] == 0
    assert result["errors"] == 0
    assert any("telemetry fetch failed" in d for d in result["diagnostics"])


def test_run_tick_fails_closed_when_telemetry_ingest_rejects_derived_event():
    def fetch(*_args, **_kwargs):
        return [_summary()]

    def admit(*_args, **_kwargs):
        return {"status": 400, "body": {"status": "rejected"}}

    def post(*_args, **_kwargs):  # pragma: no cover - must never be called
        raise AssertionError("post_incident must not be called when telemetry ingest rejects the derived event")

    result = run_tick(
        telemetry_api_url="http://telemetry.test",
        incidents_api_url="http://incidents.test",
        thresholds=THRESHOLDS,
        baselines=BASELINES,
        fetch_summaries=fetch,
        admit_telemetry_event=admit,
        post_incident=post,
        now=_NOW,
    )
    assert result["incidents_created"] == 0
    assert result["errors"] >= 1
    assert any("not citing unadmitted evidence" in d for d in result["diagnostics"])


def test_run_tick_creates_then_dedupes_on_rerun_via_real_consumer(tmp_path):
    store = IncidentStore(path=None)
    consumer = ThresholdTelemetryIncidentConsumer(incident_store=store)
    # Isolated per-test state path: the default path is a developer/runtime
    # -shared file (same class of hazard the round-2 review flagged for the
    # incident store), and must not leak writes across test runs.
    state_path = str(tmp_path / "threshold_sweep_state.json")

    def fetch(*_args, **_kwargs):
        return [_summary()]

    def admit(*_args, **_kwargs):
        return {"status": 202, "body": {"status": "accepted"}}

    def post(_url, payload, **_kwargs):
        result = consumer.consume(payload)
        return {"status": 201 if result.created else 200, "body": {}}

    now = datetime(2026, 7, 13, tzinfo=timezone.utc)

    first = run_tick(
        telemetry_api_url="http://telemetry.test",
        incidents_api_url="http://incidents.test",
        thresholds=THRESHOLDS,
        baselines=BASELINES,
        fetch_summaries=fetch,
        admit_telemetry_event=admit,
        post_incident=post,
        state_path=state_path,
        now=now,
    )
    assert first["incidents_created"] == 1
    assert first["incidents_deduped"] == 0

    second = run_tick(
        telemetry_api_url="http://telemetry.test",
        incidents_api_url="http://incidents.test",
        thresholds=THRESHOLDS,
        baselines=BASELINES,
        fetch_summaries=fetch,
        admit_telemetry_event=admit,
        post_incident=post,
        state_path=state_path,
        now=now,
    )
    assert second["incidents_created"] == 0
    assert second["incidents_deduped"] == 1
    assert len(store.find_open_incidents()) == 1


# ---------------------------------------------------------------------------
# run_tick — response parsing is fail-closed (round-3 review point 4)
# ---------------------------------------------------------------------------

def test_run_tick_fails_closed_when_telemetry_ingest_returns_malformed_json():
    """A 2xx response with a malformed JSON body must not raise out of
    run_tick, contradicting its "never raises" contract."""

    def fetch(*_args, **_kwargs):
        return [_summary()]

    def admit(*_args, **_kwargs):
        raise ValueError("Expecting value: line 1 column 1 (char 0)")

    def post(*_args, **_kwargs):  # pragma: no cover - must never be called
        raise AssertionError("post_incident must not be called when ingest response parsing fails")

    result = run_tick(
        telemetry_api_url="http://telemetry.test",
        incidents_api_url="http://incidents.test",
        thresholds=THRESHOLDS,
        baselines=BASELINES,
        fetch_summaries=fetch,
        admit_telemetry_event=admit,
        post_incident=post,
        now=_NOW,
    )
    assert result["incidents_created"] == 0
    assert result["errors"] >= 1
    assert any("telemetry ingest network error" in d for d in result["diagnostics"])


def test_run_tick_fails_closed_when_post_incident_returns_malformed_json(tmp_path):
    def fetch(*_args, **_kwargs):
        return [_summary()]

    def admit(*_args, **_kwargs):
        return {"status": 202, "body": {}}

    def post(*_args, **_kwargs):
        raise ValueError("Expecting value: line 1 column 1 (char 0)")

    result = run_tick(
        telemetry_api_url="http://telemetry.test",
        incidents_api_url="http://incidents.test",
        thresholds=THRESHOLDS,
        baselines=BASELINES,
        fetch_summaries=fetch,
        admit_telemetry_event=admit,
        post_incident=post,
        state_path=str(tmp_path / "state.json"),
        now=_NOW,
    )
    assert result["incidents_created"] == 0
    assert result["errors"] >= 1
    assert any("post_incident network error" in d for d in result["diagnostics"])


# ---------------------------------------------------------------------------
# run_tick — retried evidence must never diverge from what telemetry already
# durably admitted (round-3 review point 3)
# ---------------------------------------------------------------------------

def test_run_tick_retry_reuses_frozen_evidence_when_incident_post_previously_failed(tmp_path):
    """Once telemetry has durably admitted an event_id (202), a later retry
    for the same dedupe key/day must reuse that exact evidence rather than
    posting different content under the same event_id to incidents — even
    if the live summary drifted between the two attempts."""
    state_path = str(tmp_path / "state.json")
    admitted: dict[str, dict] = {}

    def admit(_url, event, **_kwargs):
        admitted.setdefault(event["event_id"], event)
        return {"status": 202, "body": {}}

    def fetch_first(*_args, **_kwargs):
        return [_summary(drawdown=0.30)]

    def post_fails(*_args, **_kwargs):
        raise urllib.error.URLError("incidents unreachable")

    first = run_tick(
        telemetry_api_url="http://telemetry.test",
        incidents_api_url="http://incidents.test",
        thresholds=THRESHOLDS,
        baselines=BASELINES,
        fetch_summaries=fetch_first,
        admit_telemetry_event=admit,
        post_incident=post_fails,
        state_path=state_path,
        now=_NOW,
    )
    assert first["incidents_created"] == 0
    assert first["errors"] >= 1
    first_admitted = dict(admitted)
    assert first_admitted

    # A later retry (same day/dedupe window): the live metric has since
    # drifted to a different reading before the incident was ever created.
    def fetch_second(*_args, **_kwargs):
        return [_summary(drawdown=0.45)]

    posted_payloads = []

    def post_ok(_url, payload, **_kwargs):
        posted_payloads.append(payload)
        return {"status": 201, "body": {}}

    second = run_tick(
        telemetry_api_url="http://telemetry.test",
        incidents_api_url="http://incidents.test",
        thresholds=THRESHOLDS,
        baselines=BASELINES,
        fetch_summaries=fetch_second,
        admit_telemetry_event=admit,
        post_incident=post_ok,
        state_path=state_path,
        now=_NOW,
    )
    assert second["incidents_created"] >= 1
    drawdown_posted = next(
        p for p in posted_payloads if p["threshold_snapshot"]["metric_name"] == "rolling_drawdown_multiple"
    )
    event_id = drawdown_posted["telemetry_event"]["event_id"]
    assert drawdown_posted["telemetry_event"] == first_admitted[event_id]
    assert drawdown_posted["threshold_snapshot"]["raw_observed_value"] == 0.30


# ---------------------------------------------------------------------------
# Derived evidence must not launder a stale metric fresh (round-3 review
# point 2)
# ---------------------------------------------------------------------------

def test_derived_threshold_evidence_does_not_refresh_stale_metric_across_days(tmp_path):
    """A threshold-derived echo (admitted through ingest only to prove it is
    schema/evidence-valid) must not refresh the source metric's own as-of
    time. Otherwise a genuinely abandoned drawdown value can keep
    re-triggering a "fresh" breach every day forever under fresh heartbeats
    alone — the exact six-day loop the round-3 review reproduced."""
    projection_store = RuntimeSummaryProjectionStore(path=None)
    state_path = str(tmp_path / "state.json")
    day0 = datetime(2026, 7, 1, 0, 0, 0, tzinfo=timezone.utc)

    def _iso(dt: datetime) -> str:
        return dt.isoformat().replace("+00:00", "Z")

    identity = {
        "runtime_id": "runtime-evochain-001",
        "binding_id": "rb-evochain-001",
        "deployment_stage": "paper",
        "capital_pool_id": "pool-evochain-001",
        "artifact_id": "artifact-evochain-001",
        "artifact_version": "1.0.0",
        "plan_id": "plan-evochain-001",
        "persona_capital_binding_id": "pcb-evochain-001",
    }

    # The one genuine drawdown observation, seeded on day 0.
    projection_store.project_event(
        {
            **identity,
            "event_id": "evt-genuine-drawdown",
            "event_type": "drawdown_snapshot",
            "created_at": _iso(day0),
            "metrics": {"drawdown_pct": 0.18},
        }
    )

    candidates_by_day: dict[int, int] = {}
    for day_offset in range(7):
        moment = day0 + timedelta(days=day_offset)

        # A fresh heartbeat every day, but never a new real drawdown value.
        projection_store.project_event(
            {
                **identity,
                "event_id": f"evt-heartbeat-{day_offset}",
                "event_type": "heartbeat",
                "created_at": _iso(moment),
                "metadata": {"connectivity_status": "connected"},
                "metrics": {"heartbeat": 1},
            }
        )

        def fetch(*_args, **_kwargs):
            return [projection_store.get("runtime-evochain-001", now=moment)]

        def admit(_url, event, **_kwargs):
            # Mirrors what the real telemetry ingest route does on accept:
            # projects the admitted (derived) event into the same summary
            # store the worker just read from.
            dated_event = dict(event)
            dated_event["created_at"] = _iso(moment)
            projection_store.project_event(dated_event)
            return {"status": 202, "body": {}}

        def post(_url, _payload, **_kwargs):
            return {"status": 201, "body": {}}

        result = run_tick(
            telemetry_api_url="http://telemetry.test",
            incidents_api_url="http://incidents.test",
            thresholds=THRESHOLDS,
            baselines=BASELINES,
            fetch_summaries=fetch,
            admit_telemetry_event=admit,
            post_incident=post,
            state_path=state_path,
            now=moment,
        )
        candidates_by_day[day_offset] = result["candidates"]

    # Fresh for day offsets 0-2 (age <= metric_max_age_seconds default 2 days).
    assert candidates_by_day[0] >= 1
    assert candidates_by_day[1] >= 1
    assert candidates_by_day[2] >= 1
    # Stale from day offset 3 onward: without the fix, the worker's own
    # derived echo would keep restamping drawdown_at fresh and this would
    # incorrectly still be >= 1 every day.
    assert candidates_by_day[3] == 0
    assert candidates_by_day[6] == 0


def test_evaluate_breaches_handles_overflow_error():
    """Verify that evaluate_breaches() does not crash on huge integers or float overflow
    but instead logs a diagnostic and fails closed."""
    huge_summary = {
        "binding_id": "rb-paper-1",
        "runtime_id": "rt-1",
        "deployment_stage": "paper",
        "plan_id": "plan-1",
        "capital_pool_id": "pool-1",
        "persona_capital_binding_id": "pcb-1",
        "artifact_id": "artifact-evochain-001",
        "artifact_version": "1.0.0",
        "drawdown": 10**1000,  # huge integer
        "drawdown_at": _NOW.isoformat().replace("+00:00", "Z"),
        "last_heartbeat_at": _NOW.isoformat().replace("+00:00", "Z"),
    }
    payloads, diagnostics = evaluate_breaches(
        [huge_summary],
        THRESHOLDS,
        window_bucket="2026-07-13",
        baselines=BASELINES,
        now=_NOW,
    )
    assert not payloads
    assert any("overflow or division error" in d for d in diagnostics)


def test_run_tick_implements_write_ahead_log(tmp_path):
    """Verify that run_tick() saves the telemetry event to state immediately
    upon successful ingest, before attempting the incident POST (write-ahead log)."""
    state_path = str(tmp_path / "state.json")
    summary = _summary()

    def fetch(*_args, **_kwargs):
        return [summary]

    ingest_called = False
    post_called = False

    def admit(*_args, **_kwargs):
        nonlocal ingest_called
        ingest_called = True
        return {"status": 202, "body": {}}

    def post(*_args, **_kwargs):
        nonlocal post_called
        post_called = True
        # Verify that the state file was ALREADY written by this point
        with open(state_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            assert len(data) == 1
        return {"status": 201, "body": {}}

    result = run_tick(
        telemetry_api_url="http://telemetry.test",
        incidents_api_url="http://incidents.test",
        thresholds=THRESHOLDS,
        baselines=BASELINES,
        fetch_summaries=fetch,
        admit_telemetry_event=admit,
        post_incident=post,
        state_path=state_path,
        now=_NOW,
    )

    assert result["incidents_created"] == 1
    assert ingest_called
    assert post_called


def test_run_tick_reports_empty_telemetry_diagnostic():
    """Verify that run_tick() appends an explicit diagnostic if telemetry fetch
    returns zero active runtime summaries."""
    def fetch(*_args, **_kwargs):
        return []

    result = run_tick(
        telemetry_api_url="http://telemetry.test",
        incidents_api_url="http://incidents.test",
        thresholds=THRESHOLDS,
        baselines=BASELINES,
        fetch_summaries=fetch,
        now=_NOW,
    )

    assert result["summaries_evaluated"] == 0
    assert any("zero active runtime summaries" in d for d in result["diagnostics"])


def test_run_tick_filters_duplicate_thresholds_with_diagnostic():
    """Verify that run_tick() ignores duplicate (metric_name, window) threshold entries
    and records an explicit diagnostic warning."""
    duplicate_thresholds = THRESHOLDS + [THRESHOLDS[0]]  # Add duplicate of THRESHOLDS[0]

    def fetch(*_args, **_kwargs):
        return [_summary()]

    def admit(*_args, **_kwargs):
        return {"status": 202, "body": {}}

    def post(*_args, **_kwargs):
        return {"status": 201, "body": {}}

    result = run_tick(
        telemetry_api_url="http://telemetry.test",
        incidents_api_url="http://incidents.test",
        thresholds=duplicate_thresholds,
        baselines=BASELINES,
        fetch_summaries=fetch,
        admit_telemetry_event=admit,
        post_incident=post,
        state_path=None,  # Use default or none (will be handled by test)
        now=_NOW,
    )

    # Check that duplicates were filtered: candidates should not be duplicated
    assert result["candidates"] == 1
    # Check that warning was logged
    assert any("warning: duplicate threshold entry" in d for d in result["diagnostics"])
