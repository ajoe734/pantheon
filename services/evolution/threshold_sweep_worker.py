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
import math
import os
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config", "threshold_sweep_thresholds.json")
DEFAULT_BASELINES_PATH = os.path.join(os.path.dirname(__file__), "config", "threshold_sweep_baselines.json")

# Durable record of the exact evidence payload admitted through telemetry
# ingest for each dedupe-key event_id, keyed by event_id. Retried ticks reuse
# this frozen payload instead of recomputing `created_at`/observed values from
# a possibly-changed live summary, so the telemetry event content and the
# incident evidence citing it can never diverge for the same event_id (see
# EVOCHAIN-001-threshold-breach-producer.md round-3 review point 3).
# Resolves to a persistent volume when EVOLUTION_DATA_DIR is set (e.g. inside
# compose), fallback to /tmp/pantheon/evolution when run locally.
DEFAULT_STATE_PATH = os.path.join(
    os.getenv("EVOLUTION_DATA_DIR", "/tmp/pantheon/evolution"),
    "threshold_sweep_state.json"
)

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

# Maximum age of a per-metric value (`RuntimeSummaryProjectionStore`'s
# `f"{field}_at"` companion timestamp) before it is treated as stale and
# skipped as a diagnostic. A fresh heartbeat never masks an old metric: each
# metric field is checked against its own as-of time, not the summary's
# overall last-event time. Generous relative to the default daily sweep
# cadence (`EVOCHAIN_THRESHOLD_SWEEP_INTERVAL_SECONDS`), tight relative to a
# genuinely abandoned metric (e.g. a value last refreshed many days ago).
_DEFAULT_METRIC_MAX_AGE_SECONDS = 172800  # 2 days


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
        # Strict scalar/enum typing so a malformed-but-JSON-valid entry (e.g.
        # a list where a string is expected) is dropped here, fail-closed,
        # instead of raising TypeError later in evaluate_breaches() when it
        # is used as a dict key or comparator lookup.
        if not all(
            isinstance(entry.get(key), str)
            for key in ("metric_name", "signal_type", "policy_source", "summary_field")
        ):
            continue
        comparator = entry["comparator"]
        if not isinstance(comparator, str) or comparator not in _COMPARATORS:
            continue
        threshold_value = entry["threshold_value"]
        if isinstance(threshold_value, bool) or not isinstance(threshold_value, (int, float)):
            continue
        try:
            # A JSON integer with no fixed size (e.g. 10**1000) is valid JSON
            # and a valid Python int, but math.isfinite() raises OverflowError
            # converting it to a float rather than returning False: catch it
            # here so a malformed-but-parseable live-config edit is dropped
            # fail-closed instead of crash-looping the worker.
            if not math.isfinite(threshold_value):
                continue
        except OverflowError:
            continue
        telemetry_event_type = entry["telemetry_event_type"]
        if not isinstance(telemetry_event_type, str) or telemetry_event_type not in _TELEMETRY_EVENT_TYPES:
            continue
        ratio_baseline_key = entry.get("ratio_baseline_key")
        if ratio_baseline_key is not None and not isinstance(ratio_baseline_key, str):
            continue
        window = entry.get("window")
        if window is not None and not isinstance(window, str):
            continue
        # `enabled` must be a literal bool: a truthy non-bool JSON value
        # (e.g. the string "false", which is truthy in Python) must not
        # silently activate an uncalibrated/unapproved threshold.
        if entry.get("enabled") is not True:
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
    # Require an affirmative freshness signal rather than only rejecting
    # explicit bad markers: a summary that has never received a heartbeat
    # carries no `staleness`/`state`/`connectivity_status` markers at all
    # (RuntimeSummaryProjectionStore only sets them once a heartbeat event
    # has been projected), so treat "no heartbeat on record" itself as
    # ambiguous/fail-closed instead of implicitly healthy.
    if not summary.get("last_heartbeat_at"):
        return True
    if summary.get("staleness"):
        return True
    if str(summary.get("state") or "").strip().lower() == "degraded":
        return True
    if str(summary.get("connectivity_status") or "").strip().lower() in {"degraded", "disconnected"}:
        return True
    return False


def _parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _metric_is_stale(
    summary: Mapping[str, Any],
    field: str,
    *,
    now: datetime,
    max_age_seconds: int,
) -> bool:
    """A fresh heartbeat must never mask an old metric value.

    ``RuntimeSummaryProjectionStore`` stamps each metric field with its own
    ``f"{field}_at"`` as-of time. A metric with no as-of time on record, or
    one whose as-of time is missing/unparseable/too old/in the future, is
    ambiguous and must not be evaluated (fail-closed).
    """
    as_of = _parse_utc(summary.get(f"{field}_at"))
    if as_of is None:
        return True
    age_seconds = (now - as_of).total_seconds()
    return age_seconds < 0 or age_seconds > max_age_seconds


def evaluate_breaches(
    summaries: Sequence[Mapping[str, Any]],
    thresholds: Sequence[Mapping[str, Any]],
    *,
    window_bucket: str,
    baselines: Mapping[str, Mapping[str, Any]] | None = None,
    now: datetime | None = None,
    metric_max_age_seconds: int = _DEFAULT_METRIC_MAX_AGE_SECONDS,
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
    moment = now or datetime.now(timezone.utc)

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

            if _metric_is_stale(summary, field, now=moment, max_age_seconds=metric_max_age_seconds):
                diagnostics.append(
                    f"skip {identity['binding_id']}/{metric_name}: "
                    f"telemetry field {field!r} has no fresh as-of time; a fresh heartbeat does not "
                    "make an old metric value evaluable (fail-closed)"
                )
                continue

            try:
                observed_raw_float = float(observed_raw)
                if not math.isfinite(observed_raw_float):
                    diagnostics.append(
                        f"skip {identity['binding_id']}/{metric_name}: observed raw value is not finite"
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
                    observed = observed_raw_float / float(baseline_value)
                else:
                    baseline_value = None
                    observed = observed_raw_float

                if not math.isfinite(observed):
                    diagnostics.append(
                        f"skip {identity['binding_id']}/{metric_name}: observed ratio/value is not finite"
                    )
                    continue

                comparator = str(threshold["comparator"])
                threshold_value = float(threshold["threshold_value"])
                if not _COMPARATORS[comparator](observed, threshold_value):
                    continue
            except (OverflowError, ZeroDivisionError) as exc:
                diagnostics.append(
                    f"skip {identity['binding_id']}/{metric_name}: overflow or division error during evaluation: {exc}"
                )
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
                        # Marks this event as a threshold-derived echo of an
                        # existing metric (admitted through ingest only to
                        # prove it is schema/evidence-valid before being
                        # cited as incident evidence). RuntimeSummaryProjectionStore
                        # must not treat this as a fresh observation of the
                        # metric, or a stale value could be laundered fresh by
                        # the very sweep that cited it as a breach.
                        "metadata": {"derived_from_threshold_evaluation": True},
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


def _load_pending_evidence(path: str) -> dict[str, dict[str, Any]]:
    """Load previously-admitted evidence payloads, keyed by event_id.

    Fail-closed like the other live-config loaders: a missing, unreadable, or
    malformed state file yields an empty mapping (the worker simply loses its
    retry-immutability guarantee for this tick, it never crashes or
    fabricates a breach).
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, Mapping):
        return {}
    valid: dict[str, dict[str, Any]] = {}
    for event_id, record in data.items():
        if (
            isinstance(record, Mapping)
            and isinstance(record.get("telemetry_event"), Mapping)
            and isinstance(record.get("threshold_snapshot"), Mapping)
            and isinstance(record.get("window_bucket"), str)
        ):
            valid[str(event_id)] = dict(record)
    return valid


def _save_pending_evidence(path: str, state: Mapping[str, dict[str, Any]]) -> None:
    """Best-effort durable write; a failure here degrades to non-immutable
    retry behavior next tick rather than raising (never crash the worker)."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp_path = f"{path}.{uuid.uuid4().hex}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(dict(state), handle, sort_keys=True)
        os.replace(tmp_path, path)
    except OSError:
        pass


def run_tick(
    *,
    telemetry_api_url: str,
    incidents_api_url: str,
    config_path: str | None = None,
    baselines_path: str | None = None,
    state_path: str | None = None,
    thresholds: Sequence[Mapping[str, Any]] | None = None,
    baselines: Mapping[str, Mapping[str, Any]] | None = None,
    timeout: float = 30.0,
    now: datetime | None = None,
    metric_max_age_seconds: int = _DEFAULT_METRIC_MAX_AGE_SECONDS,
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

    # Filter out duplicate (metric_name, window) entries to avoid silent collision on event_id.
    seen_threshold_keys = set()
    unique_thresholds = []
    for t in active_thresholds:
        key = (t.get("metric_name"), t.get("window"))
        if key in seen_threshold_keys:
            result["diagnostics"].append(
                f"warning: duplicate threshold entry for metric_name={key[0]!r} window={key[1]!r} ignored"
            )
            continue
        seen_threshold_keys.add(key)
        unique_thresholds.append(t)
    active_thresholds = unique_thresholds

    active_baselines = baselines if baselines is not None else load_baselines(baselines_path)

    try:
        summaries = fetch_summaries(telemetry_api_url, timeout=timeout)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        result["diagnostics"].append(f"telemetry fetch failed, skipping tick (fail-closed): {exc}")
        return result

    if not summaries:
        result["diagnostics"].append("telemetry fetch returned zero active runtime summaries; nothing to evaluate")

    result["summaries_evaluated"] = len(summaries)
    candidates, diagnostics = evaluate_breaches(
        summaries,
        active_thresholds,
        window_bucket=window_bucket,
        baselines=active_baselines,
        now=moment,
        metric_max_age_seconds=metric_max_age_seconds,
    )
    result["candidates"] = len(candidates)
    result["diagnostics"].extend(diagnostics)

    active_state_path = state_path if state_path is not None else DEFAULT_STATE_PATH
    pending = _load_pending_evidence(active_state_path)
    # Drop evidence recorded for a prior dedupe window: only the active
    # window's event_ids can still legitimately retry.
    state_pruned = any(record.get("window_bucket") != window_bucket for record in pending.values())
    pending = {
        event_id: record for event_id, record in pending.items() if record.get("window_bucket") == window_bucket
    }
    if state_pruned:
        _save_pending_evidence(active_state_path, pending)

    for payload in candidates:
        event = payload["telemetry_event"]
        event_id = event["event_id"]
        frozen = pending.get(event_id)
        if frozen is not None:
            # This event_id was already admitted through telemetry ingest on
            # an earlier tick. Reuse that exact evidence instead of the
            # freshly recomputed `created_at`/observed values above, so a
            # retry can never post different content under the same
            # event_id than what telemetry already durably recorded.
            payload = {
                "telemetry_event": frozen["telemetry_event"],
                "threshold_snapshot": frozen["threshold_snapshot"],
            }
            event = payload["telemetry_event"]

        try:
            admit_response = admit_telemetry_event(telemetry_api_url, event, timeout=timeout)
        except urllib.error.HTTPError as exc:
            result["errors"] += 1
            result["diagnostics"].append(f"telemetry ingest rejected derived event status={exc.code}")
            continue
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
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

        if frozen is None:
            pending[event_id] = {
                "window_bucket": window_bucket,
                "telemetry_event": event,
                "threshold_snapshot": payload["threshold_snapshot"],
            }
            # Write-ahead log: immediately save state to disk before sending
            # the incident payload to the incident consumer.
            _save_pending_evidence(active_state_path, pending)

        try:
            response = post_incident(incidents_api_url, payload, timeout=timeout)
        except urllib.error.HTTPError as exc:
            result["errors"] += 1
            result["diagnostics"].append(f"post_incident rejected status={exc.code}")
            continue
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
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
    state_path = os.getenv("EVOCHAIN_THRESHOLD_SWEEP_STATE_PATH") or DEFAULT_STATE_PATH
    interval_seconds = _env_int("EVOCHAIN_THRESHOLD_SWEEP_INTERVAL_SECONDS", 86400, minimum=1)
    max_ticks = _env_int("EVOCHAIN_THRESHOLD_SWEEP_MAX_TICKS", 0, minimum=0)
    metric_max_age_seconds = _env_int(
        "EVOCHAIN_THRESHOLD_SWEEP_METRIC_MAX_AGE_SECONDS",
        _DEFAULT_METRIC_MAX_AGE_SECONDS,
        minimum=1,
    )

    tick = 0
    while True:
        tick += 1
        result = run_tick(
            telemetry_api_url=telemetry_api_url,
            incidents_api_url=incidents_api_url,
            config_path=config_path,
            baselines_path=baselines_path,
            state_path=state_path,
            metric_max_age_seconds=metric_max_age_seconds,
        )
        print(json.dumps({"tick": tick, "result": result}, sort_keys=True), flush=True)
        if max_ticks and tick >= max_ticks:
            return 0
        time.sleep(interval_seconds)


if __name__ == "__main__":  # pragma: no cover - exercised through compose/smoke worker.
    raise SystemExit(main())
