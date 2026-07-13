"""Threshold-breach producer: paper telemetry aggregates -> incidents consumer.

Periodically reads per-binding/per-persona paper performance summaries from
the telemetry read path (the same summaries that feed the performance
console: ``GET {telemetry}/api/telemetry/runtime-summaries``), evaluates them
against governance-schema thresholds (``ThresholdSnapshot`` shape, see
``services/control-plane/governance/evolution_decision.py``) loaded from live
config, and POSTs canonical breach payloads to the Incident Evidence Service
(``POST {incidents}/api/incidents/consume-threshold``).

Fail-closed: a threshold or telemetry value that cannot be unambiguously
evaluated is skipped with a diagnostic. This worker never fabricates a
breach.

Idempotent: the dedupe key is (binding_id, metric_name, threshold window,
UTC day bucket). It is baked into a deterministic telemetry event_id, which
the incidents consumer (``services/incidents/consumer.py``) turns into a
stable incident_id — reruns within the same window never open a duplicate
open incident.

This module never imports services.incidents/services.incident directly; it
only talks to the Incident Evidence Service over its HTTP contract, per the
service's own write-authority rule.
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
)

_COMPARATORS: dict[str, Callable[[float, float], bool]] = {
    "gt": lambda observed, threshold: observed > threshold,
    "gte": lambda observed, threshold: observed >= threshold,
    "lt": lambda observed, threshold: observed < threshold,
    "lte": lambda observed, threshold: observed <= threshold,
    "eq": lambda observed, threshold: observed == threshold,
    "neq": lambda observed, threshold: observed != threshold,
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
    the worker loop.
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
        valid.append(dict(entry))
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


def evaluate_breaches(
    summaries: Sequence[Mapping[str, Any]],
    thresholds: Sequence[Mapping[str, Any]],
    *,
    window_bucket: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Evaluate runtime performance summaries against live-config thresholds.

    Returns ``(payloads, diagnostics)``. ``payloads`` are ready to POST to
    ``/api/incidents/consume-threshold``. Anything that cannot be
    unambiguously evaluated is dropped into ``diagnostics`` instead of being
    treated as a breach.
    """
    diagnostics: list[str] = []
    payloads: list[dict[str, Any]] = []

    for summary in summaries:
        if not isinstance(summary, Mapping):
            diagnostics.append("skip summary: not an object")
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
            observed = summary.get(field)
            if isinstance(observed, bool) or not isinstance(observed, (int, float)):
                diagnostics.append(
                    f"skip {identity['binding_id']}/{metric_name}: "
                    f"telemetry field {field!r} missing or non-numeric"
                )
                continue

            comparator = str(threshold["comparator"])
            threshold_value = float(threshold["threshold_value"])
            if not _COMPARATORS[comparator](float(observed), threshold_value):
                continue

            window_label = f"{threshold.get('window') or 'sweep'}:{window_bucket}"
            dedupe_key = f"{identity['binding_id']}:{metric_name}:{window_label}"
            event_id = f"tel-threshold-sweep-{uuid.uuid5(uuid.NAMESPACE_URL, dedupe_key).hex[:16]}"
            trace_id = f"trace-threshold-sweep-{uuid.uuid5(uuid.NAMESPACE_URL, dedupe_key + ':trace').hex[:16]}"

            payloads.append(
                {
                    "telemetry_event": {
                        "event_id": event_id,
                        "event_type": "threshold_sweep_snapshot",
                        "created_at": _utc_now(),
                        "runtime_binding_id": identity["binding_id"],
                        "deployment_stage": identity["deployment_stage"],
                        "deployment_plan_id": identity["deployment_plan_id"],
                        "capital_pool_id": identity["capital_pool_id"],
                        "persona_capital_binding_id": identity["persona_capital_binding_id"],
                        "artifact_id": identity["artifact_id"],
                        "artifact_version": identity["artifact_version"],
                        "runtime_id": identity["runtime_id"],
                        "trace_id": trace_id,
                        "metrics": {field: observed},
                        "description": (
                            f"Threshold sweep: {metric_name} observed={observed} "
                            f"{comparator} threshold={threshold_value}"
                        ),
                    },
                    "threshold_snapshot": {
                        "policy_source": threshold["policy_source"],
                        "signal_type": threshold["signal_type"],
                        "metric_name": metric_name,
                        "comparator": comparator,
                        "observed_value": observed,
                        "threshold_value": threshold_value,
                        "window": window_label,
                        "breached": True,
                        "note": f"dedupe_key={dedupe_key}",
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
    thresholds: Sequence[Mapping[str, Any]] | None = None,
    timeout: float = 30.0,
    now: datetime | None = None,
    fetch_summaries: Callable[..., list[dict[str, Any]]] = default_fetch_summaries,
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

    try:
        summaries = fetch_summaries(telemetry_api_url, timeout=timeout)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        result["diagnostics"].append(f"telemetry fetch failed, skipping tick (fail-closed): {exc}")
        return result

    result["summaries_evaluated"] = len(summaries)
    candidates, diagnostics = evaluate_breaches(summaries, active_thresholds, window_bucket=window_bucket)
    result["candidates"] = len(candidates)
    result["diagnostics"].extend(diagnostics)

    for payload in candidates:
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
    interval_seconds = _env_int("EVOCHAIN_THRESHOLD_SWEEP_INTERVAL_SECONDS", 86400, minimum=1)
    max_ticks = _env_int("EVOCHAIN_THRESHOLD_SWEEP_MAX_TICKS", 0, minimum=0)

    tick = 0
    while True:
        tick += 1
        result = run_tick(
            telemetry_api_url=telemetry_api_url,
            incidents_api_url=incidents_api_url,
            config_path=config_path,
        )
        print(json.dumps({"tick": tick, "result": result}, sort_keys=True), flush=True)
        if max_ticks and tick >= max_ticks:
            return 0
        time.sleep(interval_seconds)


if __name__ == "__main__":  # pragma: no cover - exercised through compose/smoke worker.
    raise SystemExit(main())
