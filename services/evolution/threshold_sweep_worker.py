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

import http.client
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Iterator, Mapping, Sequence

from services.evolution.worker_health import (
    healthcheck as check_worker_health,
    write_health,
)

try:  # pragma: no cover - fcntl is present on the Linux runtime.
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

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
# Restricted to safe, non-side-effecting passive observation types for the sweep worker.
_TELEMETRY_EVENT_TYPES = frozenset(
    {
        "pnl_snapshot",
        "drawdown_snapshot",
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
_WORKER_NAME = "evolution-threshold-sweep-producer"


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    value = int(os.getenv(name, str(default)))
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def healthcheck() -> int:
    return check_worker_health(
        health_file=os.getenv("EVOCHAIN_THRESHOLD_SWEEP_HEALTH_FILE", ""),
        interval_seconds=_env_int(
            "EVOCHAIN_THRESHOLD_SWEEP_INTERVAL_SECONDS",
            86400,
            minimum=1,
        ),
        worker_name=_WORKER_NAME,
    )


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
    except (OSError, ValueError):
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
        # Verify that metric_name, signal_type, policy_source, and summary_field are non-empty strings.
        if not all(
            isinstance(entry.get(key), str) and entry.get(key).strip() != ""
            for key in ("metric_name", "signal_type", "policy_source", "summary_field")
        ):
            continue
        # Verify signal_type is a governance-valid ThresholdSignalType
        if entry["signal_type"] not in {
            "performance_degradation",
            "execution_drift",
            "feature_drift",
            "human_correction",
            "governance_incident",
            "manual_review",
        }:
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

    A baseline only counts as *approved* when its artifact entry carries a
    non-empty ``policy_source`` naming the governance decision that set it.
    An entry without that provenance is dropped here rather than being used as
    the denominator of a ratio threshold: an unapproved number is exactly the
    fabricated-baseline case this worker must never turn into a breach
    (L12-EVO-001 acceptance: "All monitored artifacts have numeric drawdown and
    approved expected baselines").
    """
    baselines_path = path or DEFAULT_BASELINES_PATH
    try:
        with open(baselines_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}

    raw_baselines = data.get("baselines") if isinstance(data, Mapping) else None
    if not isinstance(raw_baselines, Mapping):
        return {}

    valid: dict[str, dict[str, Any]] = {}
    for artifact_id, values in raw_baselines.items():
        if not isinstance(values, Mapping):
            continue
        policy_source = values.get("policy_source")
        if not isinstance(policy_source, str) or not policy_source.strip():
            continue
        valid[str(artifact_id)] = dict(values)
    return valid


def approved_baseline_value(
    baselines: Mapping[str, Mapping[str, Any]],
    artifact_id: str,
    baseline_key: str,
) -> float | None:
    """Return an artifact's approved positive baseline, or ``None``.

    ``None`` means "no usable approved baseline" for every reason that must
    fail closed: no entry, non-numeric, boolean, non-positive, or non-finite.
    A single reader keeps ``evaluate_breaches`` and the input-coverage report
    from disagreeing about whether an artifact is governed.
    """
    artifact_baselines = baselines.get(artifact_id)
    if not isinstance(artifact_baselines, Mapping):
        return None
    value = artifact_baselines.get(baseline_key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(numeric) or numeric <= 0:
        return None
    return numeric


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


def _parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _is_stale_or_degraded(summary: Mapping[str, Any], *, now: datetime) -> bool:
    # Require an affirmative freshness signal rather than only rejecting
    # explicit bad markers: a summary that has never received a heartbeat
    # carries no `staleness`/`state`/`connectivity_status` markers at all
    # (RuntimeSummaryProjectionStore only sets them once a heartbeat event
    # has been projected), so treat "no heartbeat on record" itself as
    # ambiguous/fail-closed instead of implicitly healthy. A heartbeat
    # timestamp that cannot be parsed, or that lies in the future, is
    # likewise ambiguous telemetry and must not read as fresh just because
    # the field is present/truthy.
    heartbeat_raw = summary.get("last_heartbeat_at")
    if not heartbeat_raw:
        return True
    heartbeat_at = _parse_utc(heartbeat_raw)
    if heartbeat_at is None or heartbeat_at > now:
        return True
    if summary.get("staleness"):
        return True
    if str(summary.get("state") or "").strip().lower() == "degraded":
        return True
    if str(summary.get("connectivity_status") or "").strip().lower() in {"degraded", "disconnected"}:
        return True
    return False


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

        if _is_stale_or_degraded(summary, now=moment):
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

            # Validate metric provenance to avoid rollover mismatch. Missing
            # provenance is ambiguous telemetry, not implicitly trustworthy:
            # a metric with no `<field>_binding_id` on record must be
            # diagnostic-only, the same as an explicit mismatch, or a
            # rollover could silently evaluate a stale/foreign metric.
            metric_binding_id = summary.get(f"{field}_binding_id")
            current_binding_id = summary.get("binding_id") or summary.get("runtime_binding_id")
            if not metric_binding_id:
                diagnostics.append(
                    f"skip {identity['binding_id']}/{metric_name}: "
                    f"metric provenance missing for telemetry field {field!r}; "
                    "ambiguous telemetry (fail-closed)"
                )
                continue
            if current_binding_id and metric_binding_id != current_binding_id:
                diagnostics.append(
                    f"skip {identity['binding_id']}/{metric_name}: "
                    f"metric provenance mismatch: metric binding {metric_binding_id!r} "
                    f"does not match current summary binding {current_binding_id!r} (rollover)"
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
                    baseline_value = approved_baseline_value(
                        active_baselines, identity["artifact_id"], str(baseline_key)
                    )
                    if baseline_value is None:
                        diagnostics.append(
                            f"skip {identity['binding_id']}/{metric_name}: no approved "
                            f"{baseline_key!r} baseline for artifact_id={identity['artifact_id']!r} "
                            "(fail-closed; add a positive value plus its approving policy_source "
                            "to threshold_sweep_baselines.json)"
                        )
                        continue
                    observed = observed_raw_float / baseline_value
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
            # A colon-joined string is not an injective encoding of the
            # (binding_id, metric_name, window_label) tuple: distinct tuples
            # that only differ in where a colon falls can collide (e.g.
            # metric_name="a:b", window_label="c" vs metric_name="a",
            # window_label="b:c" both joined to "...:a:b:c..."), silently
            # suppressing one candidate under the other's event_id. A JSON
            # array of the same fixed arity is unambiguous because each
            # element is quoted/escaped independently.
            dedupe_key = json.dumps(
                [identity["binding_id"], metric_name, window_label], separators=(",", ":")
            )
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


def assess_input_coverage(
    summaries: Sequence[Mapping[str, Any]],
    thresholds: Sequence[Mapping[str, Any]],
    *,
    baselines: Mapping[str, Mapping[str, Any]] | None = None,
    now: datetime | None = None,
    metric_max_age_seconds: int = _DEFAULT_METRIC_MAX_AGE_SECONDS,
) -> dict[str, Any]:
    """Report whether every monitored artifact has usable Evolution inputs.

    ``evaluate_breaches`` fails closed per metric, which is correct but silent:
    an artifact whose ``drawdown`` never arrives, or whose expected baseline was
    never approved, simply stops producing candidates and looks identical to a
    healthy artifact that is not breaching.  This function makes that
    distinction observable and auditable.

    A monitored artifact is one that reaches the eligible deployment stage with
    a fresh, non-degraded summary — the same admission gate ``evaluate_breaches``
    applies — so the report never claims coverage for a runtime the sweep would
    have refused to evaluate anyway.  For each enabled threshold it records
    whether the artifact has a numeric, fresh, correctly-attributed metric and,
    when the threshold is a ratio, an approved positive baseline.

    ``coverage_complete`` is the acceptance-level answer: it is true only when
    at least one artifact is monitored and every monitored artifact has every
    input its thresholds require.  Zero monitored artifacts is reported as
    incomplete, not vacuously complete.
    """
    moment = now or datetime.now(timezone.utc)
    active_baselines = baselines or {}

    artifacts: list[dict[str, Any]] = []
    ineligible: list[dict[str, Any]] = []

    for summary in summaries:
        if not isinstance(summary, Mapping):
            ineligible.append({"reason": "summary is not an object"})
            continue

        stage = str(summary.get("deployment_stage") or "").strip().lower()
        runtime_id = summary.get("runtime_id")
        if stage != _ELIGIBLE_STAGE:
            ineligible.append(
                {
                    "runtime_id": runtime_id,
                    "deployment_stage": stage or None,
                    "reason": f"deployment_stage is not {_ELIGIBLE_STAGE!r}",
                }
            )
            continue
        if _is_stale_or_degraded(summary, now=moment):
            ineligible.append(
                {
                    "runtime_id": runtime_id,
                    "deployment_stage": stage,
                    "reason": "summary is stale or degraded",
                }
            )
            continue
        identity, missing = _extract_identity(summary)
        if missing:
            ineligible.append(
                {
                    "runtime_id": runtime_id,
                    "deployment_stage": stage,
                    "reason": f"missing identity fields {missing}",
                }
            )
            continue

        metrics: list[dict[str, Any]] = []
        gaps: list[str] = []
        for threshold in thresholds:
            metric_name = str(threshold["metric_name"])
            field = str(threshold["summary_field"])
            baseline_key = threshold.get("ratio_baseline_key")

            observed_raw = summary.get(field)
            has_numeric = not isinstance(observed_raw, bool) and isinstance(
                observed_raw, (int, float)
            )
            if has_numeric:
                try:
                    has_numeric = math.isfinite(float(observed_raw))
                except (TypeError, ValueError, OverflowError):
                    has_numeric = False

            metric_binding_id = summary.get(f"{field}_binding_id")
            current_binding_id = summary.get("binding_id") or summary.get("runtime_binding_id")
            provenance_ok = bool(metric_binding_id) and (
                not current_binding_id or metric_binding_id == current_binding_id
            )
            fresh = not _metric_is_stale(
                summary, field, now=moment, max_age_seconds=metric_max_age_seconds
            )

            baseline_value = (
                approved_baseline_value(
                    active_baselines, identity["artifact_id"], str(baseline_key)
                )
                if baseline_key
                else None
            )
            baseline_ok = baseline_value is not None if baseline_key else True

            entry = {
                "metric_name": metric_name,
                "summary_field": field,
                "has_numeric_value": has_numeric,
                "metric_provenance_ok": provenance_ok,
                "metric_fresh": fresh,
                "requires_baseline": bool(baseline_key),
                "baseline_key": str(baseline_key) if baseline_key else None,
                "approved_baseline_value": baseline_value,
                "has_approved_baseline": baseline_ok,
                "evaluable": bool(has_numeric and provenance_ok and fresh and baseline_ok),
            }
            metrics.append(entry)

            if not has_numeric:
                gaps.append(f"{metric_name}: telemetry field {field!r} missing or non-numeric")
            if not provenance_ok:
                gaps.append(f"{metric_name}: telemetry field {field!r} has missing/mismatched provenance")
            if not fresh:
                gaps.append(f"{metric_name}: telemetry field {field!r} has no fresh as-of time")
            if baseline_key and not baseline_ok:
                gaps.append(
                    f"{metric_name}: no approved {str(baseline_key)!r} baseline for "
                    f"artifact_id={identity['artifact_id']!r}"
                )

        artifacts.append(
            {
                "artifact_id": identity["artifact_id"],
                "artifact_version": identity["artifact_version"],
                "binding_id": identity["binding_id"],
                "runtime_id": identity["runtime_id"],
                "deployment_stage": identity["deployment_stage"],
                "metrics": metrics,
                "gaps": gaps,
                "complete": not gaps,
            }
        )

    complete = [item for item in artifacts if item["complete"]]
    incomplete = [item for item in artifacts if not item["complete"]]
    return {
        "assessed_at": _utc_now(),
        "eligible_stage": _ELIGIBLE_STAGE,
        "thresholds_considered": [str(item["metric_name"]) for item in thresholds],
        "monitored_artifacts": len(artifacts),
        "complete_artifacts": len(complete),
        "incomplete_artifacts": len(incomplete),
        "coverage_complete": bool(artifacts) and not incomplete,
        "artifacts": artifacts,
        "ineligible_summaries": ineligible,
    }


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


def _load_pending_evidence(path: str) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Load previously-admitted evidence payloads, keyed by event_id.

    Returns ``(valid, quarantined)``, where ``quarantined`` maps event_id to
    the exact raw record read from disk. Fail-closed: unreadable, malformed,
    or non-UTF8 state needs an explicit fail-closed diagnostic and must raise
    ValueError. A structurally invalid *record* inside an otherwise-readable
    WAL must never raise, but its raw value is reserved in ``quarantined``
    rather than being silently forgotten: since the dedupe key hashes
    deterministically to this same event_id, forgetting it would let a later
    tick recompute a brand-new payload (from whatever the live summary says
    right now) and cite it as if it were the original frozen evidence,
    silently losing the corrupt record's real history. Callers must fold
    ``quarantined`` back into every subsequent ``_save_pending_evidence``
    write (see ``_full_state``) so an unrelated prune/candidate/delivery save
    never drops the tombstone.
    """
    if not os.path.exists(path):
        return {}, {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            content = handle.read()
            data = json.loads(content)
    except (OSError, json.JSONDecodeError, UnicodeError) as exc:
        raise ValueError(f"WAL load failed: unreadable/malformed/non-UTF8 state: {exc}")

    if not isinstance(data, Mapping):
        raise ValueError("WAL load failed: state JSON root is not a mapping")

    valid: dict[str, dict[str, Any]] = {}
    quarantined: dict[str, Any] = {}
    for event_id, record in data.items():
        try:
            if not isinstance(record, Mapping):
                quarantined[str(event_id)] = record
                continue
            tel_event = record.get("telemetry_event")
            snap = record.get("threshold_snapshot")

            structurally_valid = (
                isinstance(tel_event, Mapping)
                and isinstance(snap, Mapping)
                and isinstance(record.get("window_bucket"), str)
                and "delivered" in record
                and isinstance(record["delivered"], bool)
                and "event_id" in tel_event
                and str(tel_event["event_id"]) == str(event_id)
            )
            if not structurally_valid:
                quarantined[str(event_id)] = record
                continue

            # Enforce type/payload validation regardless of delivered state. A
            # malformed delivered=true record (e.g. missing event_type/metrics)
            # must never be trusted as "valid" evidence: a genuine current
            # candidate that recomputes this same deterministic event_id would
            # otherwise be silently deduped against a record that was never
            # actually delivered evidence (round-9 review point 2).
            event_type = tel_event.get("event_type")
            metrics = tel_event.get("metrics")
            if not isinstance(event_type, str) or not isinstance(metrics, Mapping):
                quarantined[str(event_id)] = record
                continue
            valid[str(event_id)] = dict(record)
        except Exception:
            # Structurally invalid records must never raise; reserve the raw value.
            quarantined[str(event_id)] = record
    return valid, quarantined


def _full_state(
    pending: Mapping[str, dict[str, Any]], quarantined: Mapping[str, Any]
) -> dict[str, Any]:
    """Merge live pending records with quarantined tombstones for a durable write.

    ``pending`` and ``quarantined`` are always keyed by disjoint event_ids
    (an id is either currently valid or currently quarantined), so this is a
    plain union. Every ``_save_pending_evidence`` call must go through this
    merge; writing ``pending`` alone silently deletes quarantine tombstones
    from disk on the very next unrelated save.
    """
    merged = dict(quarantined)
    merged.update(pending)
    return merged


def _save_pending_evidence(path: str, state: Mapping[str, dict[str, Any]]) -> None:
    """Crash-durable write: fsync the temp file and its parent directory before
    and after the atomic rename, mirroring
    ``services/foundation/reliable_delivery.py``'s ``AtomicJsonRecordStore``.
    A bare ``os.replace()`` only guarantees the new name is visible; without an
    fsync of the temp file's data and of the directory entry, a host/volume
    crash right after this call can resurrect the previous WAL contents even
    though ``run_tick`` already treated the write as durable authorization to
    admit telemetry and post an incident. Raises OSError on failure
    (fail-closed).
    """
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    tmp_path = f"{path}.{uuid.uuid4().hex}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(dict(state), handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        directory_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


@contextmanager
def _wal_lock(path: str) -> Iterator[None]:
    """Serialize the full WAL read-modify-write transaction across processes.

    Without this, two overlapping worker instances can each load the same
    on-disk state, evaluate independently, and last-writer-win away the
    other's pending record or frozen delivered payload (round-9 review point
    1). An ``flock`` held for the entire tick — not just around individual
    saves — makes each tick's WAL load/prune/evaluate/deliver/save sequence a
    single-writer transaction, equivalent in strength to
    ``AtomicJsonRecordStore``'s exclusive lock.
    """
    lock_path = f"{path}.lock"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if fcntl is not None:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        if fcntl is not None:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


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
        "input_coverage": None,
    }

    # 1. Load pending evidence (WAL state) first, as required by fail-closed rules.
    active_state_path = state_path if state_path is not None else DEFAULT_STATE_PATH
    with _wal_lock(active_state_path):
        try:
            pending, quarantined = _load_pending_evidence(active_state_path)
        except ValueError as exc:
            result["errors"] += 1
            result["diagnostics"].append(f"fail-closed: {exc}")
            return result

        if quarantined:
            result["diagnostics"].append(
                f"warning: {len(quarantined)} WAL record(s) are structurally invalid and "
                f"quarantined (reserved, never recomputed): {sorted(quarantined)}"
            )

        # 2. Prune old records that were successfully delivered.
        # Keep undelivered ones across days. Quarantined tombstones are never
        # pruned by window/delivery state; they persist until the WAL is hand-repaired.
        state_pruned = False
        filtered_pending = {}
        for event_id, record in pending.items():
            if record.get("window_bucket") == window_bucket or not record.get("delivered", False):
                filtered_pending[event_id] = record
            else:
                state_pruned = True
        pending = filtered_pending
        if state_pruned:
            try:
                _save_pending_evidence(active_state_path, _full_state(pending, quarantined))
            except OSError as exc:
                result["diagnostics"].append(f"warning: failed to prune pending state: {exc}")

        # 3. Determine if we should evaluate new breaches.
        # Even if thresholds cannot be loaded or summaries cannot be fetched, we proceed to retry undelivered records!
        should_evaluate = True

        active_thresholds = list(thresholds) if thresholds is not None else load_thresholds(config_path)
        if not active_thresholds:
            result["diagnostics"].append("no valid thresholds loaded from live config; skipping new breach evaluation (fail-closed)")
            should_evaluate = False

        if should_evaluate:
            # Group by (metric_name, window) identity. JSON ordering must
            # never decide whether a breach exists: keeping "whichever
            # definition loaded first" (round-9 review point 3) let the same
            # observed value breach or not breach depending on config key
            # order. Byte-identical duplicates are safe to coalesce (a
            # harmless repeated entry); anything else is a genuinely
            # conflicting definition for the same identity and the whole
            # identity is disabled fail-closed until the live config is fixed.
            grouped_thresholds: dict[tuple[Any, Any], list[Mapping[str, Any]]] = {}
            key_order: list[tuple[Any, Any]] = []
            for t in active_thresholds:
                key = (t.get("metric_name"), t.get("window"))
                if key not in grouped_thresholds:
                    grouped_thresholds[key] = []
                    key_order.append(key)
                grouped_thresholds[key].append(t)

            unique_thresholds = []
            for key in key_order:
                entries = grouped_thresholds[key]
                if len(entries) == 1:
                    unique_thresholds.append(entries[0])
                    continue
                first = entries[0]
                if all(entry == first for entry in entries[1:]):
                    result["diagnostics"].append(
                        f"warning: duplicate identical threshold entry for metric_name={key[0]!r} "
                        f"window={key[1]!r} coalesced"
                    )
                    unique_thresholds.append(first)
                else:
                    result["diagnostics"].append(
                        f"fail-closed: conflicting threshold entries for metric_name={key[0]!r} "
                        f"window={key[1]!r}; disabling this identity until live config is fixed"
                    )
            active_thresholds = unique_thresholds

        active_baselines = baselines if baselines is not None else load_baselines(baselines_path)

        summaries = []
        if should_evaluate:
            try:
                summaries = fetch_summaries(telemetry_api_url, timeout=timeout)
            except (urllib.error.URLError, TimeoutError, ValueError, OSError, http.client.HTTPException) as exc:
                result["diagnostics"].append(f"telemetry fetch failed, skipping new breach evaluation (fail-closed): {exc}")
                should_evaluate = False

        candidates = []
        if should_evaluate:
            if not summaries:
                result["diagnostics"].append("telemetry fetch returned zero active runtime summaries; nothing to evaluate")

            result["summaries_evaluated"] = len(summaries)
            # Report input completeness before evaluating. A monitored artifact
            # missing its drawdown value or its approved baseline produces no
            # candidate and would otherwise be indistinguishable from a healthy
            # non-breaching artifact.
            coverage = assess_input_coverage(
                summaries,
                active_thresholds,
                baselines=active_baselines,
                now=moment,
                metric_max_age_seconds=metric_max_age_seconds,
            )
            result["input_coverage"] = coverage
            if not coverage["coverage_complete"]:
                result["diagnostics"].append(
                    "input coverage incomplete: "
                    f"{coverage['incomplete_artifacts']}/{coverage['monitored_artifacts']} "
                    "monitored artifact(s) lack a numeric metric or an approved baseline"
                )
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

        # 4. Collect all items to process.
        to_process = []

        # Add existing undelivered ones
        for event_id, record in pending.items():
            if not record.get("delivered", False):
                to_process.append({
                    "telemetry_event": record["telemetry_event"],
                    "threshold_snapshot": record["threshold_snapshot"],
                    "is_retry": True
                })

        # Add new candidates
        for payload in candidates:
            event = payload["telemetry_event"]
            event_id = event["event_id"]
            if event_id in quarantined:
                result["errors"] += 1
                result["diagnostics"].append(
                    f"fail-closed: event_id {event_id} has a corrupt/unreadable prior WAL record; "
                    "refusing to recompute a new payload under the same deterministic id"
                )
                continue
            if event_id in pending:
                record = pending[event_id]
                if record.get("delivered", False):
                    # Already successfully posted before
                    result["incidents_deduped"] += 1
                    continue
                # If it's in pending and delivered is False, it's already in to_process via the loop above!
            else:
                # New candidate! Add to pending as undelivered and write-ahead log it.
                record = {
                    "window_bucket": window_bucket,
                    "telemetry_event": event,
                    "threshold_snapshot": payload["threshold_snapshot"],
                    "delivered": False
                }
                pending[event_id] = record
                try:
                    _save_pending_evidence(active_state_path, _full_state(pending, quarantined))
                except OSError as exc:
                    result["errors"] += 1
                    result["diagnostics"].append(
                        f"fail-closed: write-ahead log failed for event {event_id}: {exc}"
                    )
                    # Remove from pending so we don't think it's saved
                    pending.pop(event_id)
                    continue

                to_process.append({
                    "telemetry_event": event,
                    "threshold_snapshot": payload["threshold_snapshot"],
                    "is_retry": False
                })

        for item in to_process:
            try:
                event = item["telemetry_event"]
                event_id = event["event_id"]
                payload = {
                    "telemetry_event": event,
                    "threshold_snapshot": item["threshold_snapshot"],
                }

                try:
                    admit_response = admit_telemetry_event(telemetry_api_url, event, timeout=timeout)
                except urllib.error.HTTPError as exc:
                    result["errors"] += 1
                    result["diagnostics"].append(f"telemetry ingest rejected derived event status={exc.code}")
                    continue
                except (urllib.error.URLError, TimeoutError, OSError, ValueError, http.client.HTTPException) as exc:
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
                except (urllib.error.URLError, TimeoutError, OSError, ValueError, http.client.HTTPException) as exc:
                    result["errors"] += 1
                    result["diagnostics"].append(f"post_incident network error: {exc}")
                    continue

                status = response.get("status")
                if status == 201:
                    result["incidents_created"] += 1
                    pending[event_id]["delivered"] = True
                    try:
                        _save_pending_evidence(active_state_path, _full_state(pending, quarantined))
                    except OSError as exc:
                        result["diagnostics"].append(
                            f"warning: failed to save post-delivery state for event {event_id}: {exc}"
                        )
                elif status == 200:
                    result["incidents_deduped"] += 1
                    pending[event_id]["delivered"] = True
                    try:
                        _save_pending_evidence(active_state_path, _full_state(pending, quarantined))
                    except OSError as exc:
                        result["diagnostics"].append(
                            f"warning: failed to save post-delivery state for event {event_id}: {exc}"
                        )
                else:
                    result["errors"] += 1
                    result["diagnostics"].append(f"post_incident unexpected status={status}")
            except Exception as exc:
                result["errors"] += 1
                result["diagnostics"].append(f"unexpected error processing event: {exc}")

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
    health_file = os.getenv("EVOCHAIN_THRESHOLD_SWEEP_HEALTH_FILE", "")
    metric_max_age_seconds = _env_int(
        "EVOCHAIN_THRESHOLD_SWEEP_METRIC_MAX_AGE_SECONDS",
        _DEFAULT_METRIC_MAX_AGE_SECONDS,
        minimum=1,
    )

    health: dict[str, Any] = {
        "worker_name": _WORKER_NAME,
        "status": "starting",
        "ticks": 0,
        "last_tick_at": None,
        "last_success_at": None,
        "last_failure_at": None,
        "last_failure_reason": None,
        "total_candidates": 0,
        "total_incidents_created": 0,
        "total_errors": 0,
    }
    write_health(health_file, health)

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
        health["ticks"] = tick
        health["last_tick_at"] = _utc_now()
        health["total_candidates"] += int(result.get("candidates") or 0)
        health["total_incidents_created"] += int(
            result.get("incidents_created") or 0
        )
        tick_errors = int(result.get("errors") or 0)
        health["total_errors"] += tick_errors
        if tick_errors:
            health["status"] = "degraded"
            health["last_failure_at"] = health["last_tick_at"]
            diagnostics = result.get("diagnostics")
            health["last_failure_reason"] = (
                "; ".join(str(item) for item in diagnostics)
                if isinstance(diagnostics, list)
                else f"{tick_errors} threshold sweep error(s)"
            )
        else:
            health["status"] = "ok"
            health["last_success_at"] = health["last_tick_at"]
            health["last_failure_reason"] = None
        write_health(health_file, health)
        print(
            json.dumps(
                {"tick": tick, "health": health, "result": result},
                sort_keys=True,
            ),
            flush=True,
        )
        if max_ticks and tick >= max_ticks:
            return 0
        time.sleep(interval_seconds)


if __name__ == "__main__":  # pragma: no cover - exercised through compose/smoke worker.
    if sys.argv[1:] == ["healthcheck"]:
        raise SystemExit(healthcheck())
    if sys.argv[1:]:
        print(
            "usage: python -m services.evolution.threshold_sweep_worker [healthcheck]",
            file=sys.stderr,
        )
        raise SystemExit(2)
    raise SystemExit(main())
