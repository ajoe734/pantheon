"""Threshold-breach producer: paper telemetry aggregates -> incidents consumer.

Periodically reads per-binding/per-persona paper performance summaries from
the telemetry read path (the same summaries that feed the performance
console: ``GET {telemetry}/api/telemetry/runtime-summaries``), evaluates them
against governance-schema thresholds (``ThresholdSnapshot`` shape, see
``services/control-plane/governance/evolution_decision.py``) loaded from live
config, and POSTs canonical breach payloads to the Incident Evidence Service
(``POST {incidents}/api/incidents/consume-threshold``).

Fail-closed: a threshold, baseline, or telemetry value that cannot be
unambiguously evaluated is skipped with a diagnostic. This worker never
fabricates a breach. Only ``paper``-stage, non-stale/non-degraded summaries
are eligible, per the task's declared scope.

Schema-valid evidence: before a breach is cited in an incident, the worker
admits a schema-valid derived telemetry event (``services/telemetry/
telemetry_event.schema.json``) through the real telemetry ingest route
(``POST {telemetry}/api/telemetry/ingest``). If telemetry rejects the derived
event, the candidate incident is skipped (fail-closed) instead of being
posted with unadmitted evidence.

Idempotent: the dedupe key is (binding_id, metric_name, threshold window,
UTC day bucket). It is baked into a deterministic telemetry event_id, which
the incidents consumer (``services/incidents/consumer.py``) turns into a
stable incident_id — reruns within the same window never open a duplicate
open incident.

This module never imports services.incidents/services.incident directly; it
only talks to the Incident Evidence Service and the Telemetry Ingest Service
over their HTTP contracts, per the Incident service's own write-authority
rule.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config", "threshold_sweep_thresholds.json")
DEFAULT_BASELINES_PATH = os.path.join(os.path.dirname(__file__), "config", "threshold_sweep_baselines.json")

# (identity field on the built payload, candidate keys to read from a runtime summary)
_IDENTITY_FIELDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("binding_id", ("binding_id", "runtime_binding_id")),
    ("runtime_id", ("runtime_id",)),
    ("deployment_stage", ("deployment_stage",)),
    ("deployment_plan_id", ("deployment_plan_id", "plan_id")),
    ("capital_pool_id", ("capital_pool_id",)),
    ("persona_capital_binding_id", ("persona_capital_binding_id",)),
    ("artifact_id", ("artifact_id",)),
    ("artifact_version", ("artifact_version",)),
)

_REQUIRED_THRESHOLD_KEYS = (
    "metric_name",
    "signal_type",
    "policy_source",
    "summary_field",
    "comparator",
    "threshold_value",
    "telemetry_event_type",
)

_COMPARATORS: dict[str, Callable[[float, float], bool]] = {
    "gt": lambda observed, threshold: observed > threshold,
    "gte": lambda observed, threshold: observed >= threshold,
    "lt": lambda observed, threshold: observed < threshold,
    "lte": lambda observed, threshold: observed <= threshold,
    "eq": lambda observed, threshold: observed == threshold,
    "neq": lambda observed, threshold: observed != threshold,
}

# Mirrors services/telemetry/telemetry_event.schema.json `event_type` enum.
# Kept in sync manually: a threshold whose telemetry_event_type falls outside
# this set is dropped at load time (fail-closed) rather than admitted through
# ingest with a type telemetry itself would reject.
_TELEMETRY_EVENT_TYPES = frozenset(
    {
        "pnl_snapshot",
        "drawdown_snapshot",
        "slippage_observation",
        "fill_observation",
        "paper_order_simulated",
        "paper_fill_simulated",
        "fill_received",
        "order_submitted",
        "order_accepted",
        "order_partially_filled",
        "order_filled",
        "order_canceled",
        "order_cancelled",
        "order_rejection",
        "order_rejection_simulated",
        "position_snapshot",
        "position_snapshot_received",
        "broker_position_snapshot",
        "deploy_started",
        "deploy_completed",
        "runtime_health",
        "rollback_started",
        "rollback_completed",
        "pause_triggered",
        "liquidate_triggered",
        "governance_decision",
        "approval_action",
        "manual_override",
        "kill_switch_action",
        "heartbeat",
        "bracket_order_logged",
        "telemetry_mirror_mismatch",
    }
)

# Runtime summaries this worker considers eligible for the paper threshold
# sweep. Canary/live/frozen summaries are diagnostic-skipped: this task's
# declared scope is paper-only (§ task brief), and each other stage has its
# own deployment-stage-specific governance path.
_ELIGIBLE_STAGE = "paper"

# Metric summary field -> the schema-blessed telemetry_event.metrics key used
# when admitting the derived event through telemetry ingest.
_METRICS_KEY_ALIASES: dict[str, str] = {
    "drawdown": "drawdown_pct",
}


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    value = int(os.getenv(name, str(default)))
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_thresholds(path: str | None = None) -> list[dict[str, Any]]:
    """Load threshold definitions from live config.

    Fail-closed: a missing, unreadable, or malformed config file yields an
    empty list rather than raising or falling back to hardcoded values, so a
    bad live-config edit stops new breaches from firing instead of crashing
    the worker loop. Entries with ``enabled: false`` are dropped so an
    uncalibrated/unapproved threshold never fires by default.
    """
    config_path = path or DEFAULT_CONFIG_PATH
    try:
        with open(config_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return []

    raw_thresholds = data.get("thresholds") if isinstance(data, Mapping) else None
    if not isinstance(raw_thresholds, list):
        return []

    valid: list[dict[str, Any]] = []
    for entry in raw_thresholds:
        if not isinstance(entry, Mapping):
            continue
        if not all(key in entry for key in _REQUIRED_THRESHOLD_KEYS):
            continue
        if entry["comparator"] not in _COMPARATORS:
            continue
        if isinstance(entry["threshold_value"], bool) or not isinstance(entry["threshold_value"], (int, float)):
            continue
        if entry["telemetry_event_type"] not in _TELEMETRY_EVENT_TYPES:
            continue
        if not entry.get("enabled", False):
            continue
        valid.append(dict(entry))
    return valid


def load_baselines(path: str | None = None) -> dict[str, dict[str, Any]]:
    """Load per-artifact research-approved baseline values from live config.

    Fail-closed: a missing, unreadable, or malformed baselines file yields an
    empty mapping. A threshold that needs a baseline for an artifact not
    present here is skipped as a diagnostic (never fabricated) by
    ``evaluate_breaches``.
    """
    baselines_path = path or DEFAULT_BASELINES_PATH
    try:
        with open(baselines_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}

    raw_baselines = data.get("baselines") if isinstance(data, Mapping) else None
    if not isinstance(raw_baselines, Mapping):
        return {}

    valid: dict[str, dict[str, Any]] = {}
    for artifact_id, values in raw_baselines.items():
        if isinstance(values, Mapping):
            valid[str(artifact_id)] = dict(values)
    return valid


def _extract_identity(summary: Mapping[str, Any]) -> tuple[dict[str, str], list[str]]:
    identity: dict[str, str] = {}
    missing: list[str] = []
    for target_key, source_keys in _IDENTITY_FIELDS:
        value = None
        for key in source_keys:
            candidate = summary.get(key)
            if candidate not in (None, ""):
                value = str(candidate)
                break
        if value is None:
            missing.append(target_key)
        else:
            identity[target_key] = value
    return identity, missing


def _is_stale_or_degraded(summary: Mapping[str, Any]) -> bool:
    if summary.get("staleness"):
        return True
    if str(summary.get("state") or "").strip().lower() == "degraded":
        return True
    if str(summary.get("connectivity_status") or "").strip().lower() in {"degraded", "disconnected"}:
        return True
    return False


def evaluate_breaches(
    summaries: Sequence[Mapping[str, Any]],
    thresholds: Sequence[Mapping[str, Any]],
    *,
    window_bucket: str,
    baselines: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Evaluate runtime performance summaries against live-config thresholds.

    Returns ``(payloads, diagnostics)``. ``payloads`` are ready to be admitted
    through telemetry ingest and then POSTed to
    ``/api/incidents/consume-threshold``. Anything that cannot be
    unambiguously evaluated is dropped into ``diagnostics`` instead of being
    treated as a breach.
    """
    diagnostics: list[str] = []
    payloads: list[dict[str, Any]] = []
    active_baselines = baselines or {}

    for summary in summaries:
        if not isinstance(summary, Mapping):
            diagnostics.append("skip summary: not an object")
            continue

        stage = str(summary.get("deployment_stage") or "").strip().lower()
        if stage != _ELIGIBLE_STAGE:
            diagnostics.append(
                f"skip runtime_id={summary.get('runtime_id')!r}: "
                f"deployment_stage {stage!r} is not eligible for the paper threshold sweep"
            )
            continue

        if _is_stale_or_degraded(summary):
            diagnostics.append(
                f"skip runtime_id={summary.get('runtime_id')!r}: "
                "summary is stale/degraded; refusing to evaluate ambiguous telemetry (fail-closed)"
            )
            continue

        identity, missing = _extract_identity(summary)
        if missing:
            diagnostics.append(
                f"skip runtime_id={summary.get('runtime_id')!r}: missing identity fields {missing}"
            )
            continue

        for threshold in thresholds:
            metric_name = str(threshold["metric_name"])
            field = str(threshold["summary_field"])
            observed_raw = summary.get(field)
            if isinstance(observed_raw, bool) or not isinstance(observed_raw, (int, float)):
                diagnostics.append(
                    f"skip {identity['binding_id']}/{metric_name}: "
                    f"telemetry field {field!r} missing or non-numeric"
                )
                continue

            baseline_key = threshold.get("ratio_baseline_key")
            if baseline_key:
                artifact_baselines = active_baselines.get(identity["artifact_id"])
                baseline_value = (
                    artifact_baselines.get(baseline_key) if isinstance(artifact_baselines, Mapping) else None
                )
                if (
                    isinstance(baseline_value, bool)
                    or not isinstance(baseline_value, (int, float))
                    or baseline_value <= 0
                ):
                    diagnostics.append(
                        f"skip {identity['binding_id']}/{metric_name}: no approved "
                        f"{baseline_key!r} baseline for artifact_id={identity['artifact_id']!r} "
                        "(fail-closed; add one to threshold_sweep_baselines.json)"
                    )
                    continue
                observed = float(observed_raw) / float(baseline_value)
            else:
                baseline_value = None
                observed = float(observed_raw)

            comparator = str(threshold["comparator"])
            threshold_value = float(threshold["threshold_value"])
            if not _COMPARATORS[comparator](observed, threshold_value):
                continue

            window_label = f"{threshold.get('window') or 'sweep'}:{window_bucket}"
            dedupe_key = f"{identity['binding_id']}:{metric_name}:{window_label}"
            event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, dedupe_key))
            trace_id = str(uuid.uuid5(uuid.NAMESPACE_URL, dedupe_key + ":trace"))
            metrics_key = _METRICS_KEY_ALIASES.get(field, field)

            note_parts = [f"dedupe_key={dedupe_key}"]
            if baseline_key:
                note_parts.append(f"{baseline_key}={baseline_value}")

            payloads.append(
                {
                    "telemetry_event": {
                        "event_id": event_id,
                        "event_type": str(threshold["telemetry_event_type"]),
                        "created_at": _utc_now(),
                        "execution_mode": identity["deployment_stage"],
                        "binding_id": identity["binding_id"],
                        "runtime_id": identity["runtime_id"],
                        "capital_pool_id": identity["capital_pool_id"],
                        "artifact_id": identity["artifact_id"],
                        "artifact_version": identity["artifact_version"],
                        "deployment_stage": identity["deployment_stage"],
                        "plan_id": identity["deployment_plan_id"],
                        "persona_capital_binding_id": identity["persona_capital_binding_id"],
                        "trace_id": trace_id,
                        "target": {"strategy_id": identity["artifact_id"]},
                        "metrics": {metrics_key: observed_raw},
                    },
                    "threshold_snapshot": {
                        "policy_source": threshold["policy_source"],
                        "signal_type": threshold["signal_type"],
                        "metric_name": metric_name,
                        "comparator": comparator,
                        "raw_observed_value": observed_raw,
                        "observed_value": observed,
                        "threshold_value": threshold_value,
                        "window": window_label,
                        "breached": True,
                        "note": "; ".join(note_parts),
                    },
                }
            )

    return payloads, diagnostics


def default_fetch_summaries(telemetry_api_url: str, *, timeout: float) -> list[dict[str, Any]]:
    request = urllib.request.Request(
        telemetry_api_url.rstrip("/") + "/api/telemetry/runtime-summaries",
        headers={"Accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8")
    data = json.loads(body) if body else {}
    summaries = data.get("summaries") if isinstance(data, Mapping) else None
    return list(summaries) if isinstance(summaries, list) else []


def default_admit_telemetry_event(
    telemetry_api_url: str, event: Mapping[str, Any], *, timeout: float
) -> dict[str, Any]:
    """Admit a schema-valid derived telemetry event through real ingest.

    This proves the event is canonical (schema + evidence-contract valid)
    before it is ever cited as ``telemetry_event_id`` evidence in an
    incident, instead of positing a synthetic envelope the ingest route
    would reject.
    """
    body = json.dumps(dict(event)).encode("utf-8")
    request = urllib.request.Request(
        telemetry_api_url.rstrip("/") + "/api/telemetry/ingest",
        data=body,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        status = response.status
        response_body = response.read().decode("utf-8")
    return {"status": status, "body": json.loads(response_body) if response_body else {}}


def default_post_incident(incidents_api_url: str, payload: Mapping[str, Any], *, timeout: float) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        incidents_api_url.rstrip("/") + "/api/incidents/consume-threshold",
        data=body,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        status = response.status
        response_body = response.read().decode("utf-8")
    return {"status": status, "body": json.loads(response_body) if response_body else {}}


def run_tick(
    *,
    telemetry_api_url: str,
    incidents_api_url: str,
    config_path: str | None = None,
    baselines_path: str | None = None,
    thresholds: Sequence[Mapping[str, Any]] | None = None,
    baselines: Mapping[str, Mapping[str, Any]] | None = None,
    timeout: float = 30.0,
    now: datetime | None = None,
    fetch_summaries: Callable[..., list[dict[str, Any]]] = default_fetch_summaries,
    admit_telemetry_event: Callable[..., dict[str, Any]] = default_admit_telemetry_event,
    post_incident: Callable[..., dict[str, Any]] = default_post_incident,
) -> dict[str, Any]:
    """Run one sweep tick. Always returns a result dict; never raises."""
    moment = now or datetime.now(timezone.utc)
    window_bucket = moment.date().isoformat()

    result: dict[str, Any] = {
        "tick_at": _utc_now(),
        "window_bucket": window_bucket,
        "summaries_evaluated": 0,
        "candidates": 0,
        "incidents_created": 0,
        "incidents_deduped": 0,
        "errors": 0,
        "diagnostics": [],
    }

    active_thresholds = list(thresholds) if thresholds is not None else load_thresholds(config_path)
    if not active_thresholds:
        result["diagnostics"].append("no valid thresholds loaded from live config; skipping tick (fail-closed)")
        return result

    active_baselines = baselines if baselines is not None else load_baselines(baselines_path)

    try:
        summaries = fetch_summaries(telemetry_api_url, timeout=timeout)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        result["diagnostics"].append(f"telemetry fetch failed, skipping tick (fail-closed): {exc}")
        return result

    result["summaries_evaluated"] = len(summaries)
    candidates, diagnostics = evaluate_breaches(
        summaries, active_thresholds, window_bucket=window_bucket, baselines=active_baselines
    )
    result["candidates"] = len(candidates)
    result["diagnostics"].extend(diagnostics)

    for payload in candidates:
        event = payload["telemetry_event"]
        try:
            admit_response = admit_telemetry_event(telemetry_api_url, event, timeout=timeout)
        except urllib.error.HTTPError as exc:
            result["errors"] += 1
            result["diagnostics"].append(f"telemetry ingest rejected derived event status={exc.code}")
            continue
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            result["errors"] += 1
            result["diagnostics"].append(f"telemetry ingest network error: {exc}")
            continue

        if admit_response.get("status") != 202:
            result["errors"] += 1
            result["diagnostics"].append(
                f"telemetry ingest unexpected status={admit_response.get('status')}; "
                "not citing unadmitted evidence in an incident (fail-closed)"
            )
            continue

        try:
            response = post_incident(incidents_api_url, payload, timeout=timeout)
        except urllib.error.HTTPError as exc:
            result["errors"] += 1
            result["diagnostics"].append(f"post_incident rejected status={exc.code}")
            continue
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            result["errors"] += 1
            result["diagnostics"].append(f"post_incident network error: {exc}")
            continue

        status = response.get("status")
        if status == 201:
            result["incidents_created"] += 1
        elif status == 200:
            result["incidents_deduped"] += 1
        else:
            result["errors"] += 1
            result["diagnostics"].append(f"post_incident unexpected status={status}")

    return result


def main() -> int:
    telemetry_api_url = (
        os.getenv("EVOCHAIN_TELEMETRY_API_URL")
        or os.getenv("PANTHEON_TELEMETRY_API_URL")
        or os.getenv("PANTHEON_TELEMETRY_URL", "http://telemetry:8083")
    )
    incidents_api_url = (
        os.getenv("EVOCHAIN_INCIDENTS_API_URL")
        or os.getenv("PANTHEON_INCIDENTS_API_URL", "http://incidents:8090")
    )
    config_path = os.getenv("EVOCHAIN_THRESHOLD_SWEEP_CONFIG_PATH") or None
    baselines_path = os.getenv("EVOCHAIN_THRESHOLD_SWEEP_BASELINES_PATH") or None
    interval_seconds = _env_int("EVOCHAIN_THRESHOLD_SWEEP_INTERVAL_SECONDS", 86400, minimum=1)
    max_ticks = _env_int("EVOCHAIN_THRESHOLD_SWEEP_MAX_TICKS", 0, minimum=0)

    tick = 0
    while True:
        tick += 1
        result = run_tick(
            telemetry_api_url=telemetry_api_url,
            incidents_api_url=incidents_api_url,
            config_path=config_path,
            baselines_path=baselines_path,
        )
        print(json.dumps({"tick": tick, "result": result}, sort_keys=True), flush=True)
        if max_ticks and tick >= max_ticks:
            return 0
        time.sleep(interval_seconds)


if __name__ == "__main__":  # pragma: no cover - exercised through compose/smoke worker.
    raise SystemExit(main())
