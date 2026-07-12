"""TJ-E2E-011 failure injection drills for SLO and data-quality alerts.

Each scenario builds a small synthetic event batch, feeds it through a fresh
``JourneyMaterializer`` (services/trade_journey/materializer.py), computes
data-quality metrics, and evaluates them against the paper/canary SLO
targets (services/trade_journey/slo_data_quality.py). The drill asserts the
expected alert code fired, matching the TJ-E2E-011 acceptance criterion:
"故障注入可觸發 stalled/orphan/conflict/lag 告警" (failure injection must be
able to trigger stalled/orphan/conflict/lag alerts) plus the required-work
line "Add failure injection for lag, orphan, conflict, stale SSE and
mismatch".

This harness never calls a broker, never writes to a shared store, and never
touches production telemetry: it only exercises the pure materializer and
evaluator with in-memory synthetic events, so it is safe to run in CI and in
an operator drill.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from services.trade_journey.materializer import JourneyMaterializer
from services.trade_journey.slo_data_quality import (
    compute_data_quality_metrics,
    evaluate_data_quality,
    incident_to_dict,
    load_slo_targets,
    metrics_to_dict,
)


HARNESS_VERSION = "2026-07-12.TJ-E2E-011.failure-injection"
BASE_TIME = datetime(2026, 7, 12, tzinfo=timezone.utc)


class FailureInjectionError(ValueError):
    """The drill did not trigger the expected alert code."""


def _event(event_id: str, journey_id: str, stage: str, status: str, occurred_at: datetime, **extra: Any) -> dict[str, Any]:
    return {
        "event_id": event_id, "journey_id": journey_id, "tenant_id": "tenant-drill",
        "environment": "paper", "occurred_at": occurred_at.isoformat().replace("+00:00", "Z"),
        "source": "failure-injection-drill", "stage": stage, "stage_status": status, **extra,
    }


def _materializer_lag_scenario() -> tuple[list[dict[str, Any]], datetime, int]:
    events = [_event("lag-1", "tj-lag-1", "signal_generation", "succeeded", BASE_TIME)]
    now = BASE_TIME + timedelta(seconds=120)  # exceeds the 10s paper target
    return events, now, 0


def _orphan_identifier_scenario() -> tuple[list[dict[str, Any]], datetime, int]:
    events = [_event(
        "orphan-1", "tj-orphan-1", "order_submission", "succeeded", BASE_TIME,
        order_id="order-without-client-id",
    )]
    return events, BASE_TIME + timedelta(seconds=1), 0


def _conflicting_terminal_scenario() -> tuple[list[dict[str, Any]], datetime, int]:
    events = [
        _event("conflict-1", "tj-conflict-1", "reconciliation", "succeeded", BASE_TIME),
        _event(
            "conflict-2", "tj-conflict-1", "reconciliation", "succeeded",
            BASE_TIME + timedelta(seconds=1), event_type="cancel",
        ),
    ]
    return events, BASE_TIME + timedelta(seconds=2), 0


def _stalled_scenario() -> tuple[list[dict[str, Any]], datetime, int]:
    events = [_event("stalled-1", "tj-stalled-1", "risk_evaluation", "succeeded", BASE_TIME)]
    now = BASE_TIME + timedelta(seconds=DEFAULT_STALLED_DRILL_SECONDS)
    return events, now, 0


def _reconciliation_mismatch_scenario() -> tuple[list[dict[str, Any]], datetime, int]:
    events = [
        _event("mismatch-1", "tj-mismatch-1", "order_submission", "succeeded", BASE_TIME, client_order_id="c-1"),
        _event(
            "mismatch-2", "tj-mismatch-1", "reconciliation", "failed",
            BASE_TIME + timedelta(seconds=1), reconciliation_id="r-1",
        ),
    ]
    return events, BASE_TIME + timedelta(seconds=2), 0


def _stale_sse_scenario() -> tuple[list[dict[str, Any]], datetime, int]:
    events = [_event("sse-1", "tj-sse-1", "signal_generation", "succeeded", BASE_TIME)]
    return events, BASE_TIME + timedelta(seconds=1), 3


DEFAULT_STALLED_DRILL_SECONDS = 1200  # > canary (300s) and > paper (900s) thresholds

SCENARIOS: Mapping[str, tuple[str, Any]] = {
    "materializer_lag": ("materializer_lag_breach", _materializer_lag_scenario),
    "orphan_identifier": ("orphan_identifier", _orphan_identifier_scenario),
    "conflicting_terminal": ("conflicting_terminal_states", _conflicting_terminal_scenario),
    "stalled": ("journey_stalled", _stalled_scenario),
    "reconciliation_mismatch": ("reconciliation_mismatch", _reconciliation_mismatch_scenario),
    "stale_sse": ("sse_disconnect", _stale_sse_scenario),
}


def run_failure_injection_drill(scenario: str, *, environment: str = "canary") -> dict[str, Any]:
    """Run one named scenario and return the metrics/incidents it produced."""
    if scenario not in SCENARIOS:
        raise FailureInjectionError(f"unknown failure injection scenario: {scenario}")
    expected_code, build = SCENARIOS[scenario]
    events, now, sse_disconnect_count = build()

    materializer = JourneyMaterializer()
    materializer.rebuild(events)
    projections = materializer.projections

    targets = load_slo_targets(environment)
    metrics = compute_data_quality_metrics(
        projections,
        environment=environment,
        source_watermarks=materializer.source_watermarks,
        now=now,
        stalled_after_seconds=targets.stalled_after_seconds,
        sse_disconnect_count=sse_disconnect_count,
    )
    incidents = evaluate_data_quality(metrics, targets, projections, now=now)
    triggered_codes = {incident.code for incident in incidents}
    triggered = expected_code in triggered_codes

    return {
        "harness_version": HARNESS_VERSION,
        "scenario": scenario,
        "environment": environment,
        "expected_code": expected_code,
        "triggered": triggered,
        "triggered_codes": sorted(triggered_codes),
        "metrics": metrics_to_dict(metrics),
        "incidents": [incident_to_dict(incident) for incident in incidents],
    }


def run_all_scenarios(*, environment: str = "canary") -> list[dict[str, Any]]:
    return [run_failure_injection_drill(scenario, environment=environment) for scenario in SCENARIOS]


def assert_drill_triggers_alert(scenario: str, *, environment: str = "canary") -> dict[str, Any]:
    result = run_failure_injection_drill(scenario, environment=environment)
    if not result["triggered"]:
        raise FailureInjectionError(
            f"scenario {scenario!r} did not trigger expected alert "
            f"{result['expected_code']!r}; got {result['triggered_codes']}"
        )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run TJ-E2E-011 failure injection drills")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), help="Run a single scenario; default runs all")
    parser.add_argument("--environment", default="canary", choices=("paper", "canary", "live"))
    args = parser.parse_args(argv)

    scenarios = [args.scenario] if args.scenario else list(SCENARIOS)
    results = [run_failure_injection_drill(name, environment=args.environment) for name in scenarios]
    print(json.dumps(results, indent=2, sort_keys=True))
    return 0 if all(result["triggered"] for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
