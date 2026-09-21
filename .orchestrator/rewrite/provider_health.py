"""The single delivery-health snapshot used by Supervisor Authority V2.

Endpoint credentials and shared-account capacity are distinct failure domains.
The snapshot is pure data: the scheduler consumes it for both planning and late
delivery validation, while bounded live probes refresh it after a cycle.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import enum
import re
from typing import Any, Mapping


class DeliveryHealthState(enum.Enum):
    """Dispatch admission state for one exact endpoint or capacity account.

    An endpoint credential and the account which supplies its shared capacity
    are separate failure domains.
    """

    HEALTHY = "healthy"
    UNKNOWN = "unknown"
    RETRY_AFTER = "retry_after"
    UNAVAILABLE = "unavailable"


DELIVERY_HEALTH_VERSION = 1
_ENDPOINT_SCOPE = "endpoint"
_ACCOUNT_SCOPE = "account"
_DELIVERY_HEALTH_STATES = frozenset(state.value for state in DeliveryHealthState)


_QUOTA_PROBE_STATUS_MARKERS = ("quota",)
_CAPACITY_PROBE_STATUS_MARKERS = (
    "capacity",
    "rate",
    "rotation_models_cooling",
    "timeout",
    "probe_error",
    "cli_missing",
    "exit_",
)
_AUTH_ONLY_PROBE_METHODS = frozenset({
    "claude_auth_status",
    "claude_auth_status_refresh",
    "gemini_auth_material",
    "copilot_auth_material",
    "codex_auth_file",
    "antigravity_auth_material",
})
_RESET_DATETIME_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?:reset(?:s)?(?:[_\s-]*at)?|try\s+again\s+at)\s*(?:on\s+)?"
    r"(?P<timestamp>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:\s*(?:Z|UTC|[+-]\d{2}:?\d{2}))?)",
    re.IGNORECASE,
)
_RESET_TS_COMPONENTS_PATTERN = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})[T ](?P<time>\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?)(?:\s*(?P<tz>Z|UTC|[+-]\d{2}:?\d{2}))?$",
    re.IGNORECASE,
)
_RESET_EPOCH_PATTERN = re.compile(
    r'"?resets?[_-]?at"?\s*[:=]\s*"?(?P<epoch>\d{10})"?',
    re.IGNORECASE,
)
_RESET_DURATION_PATTERN = re.compile(
    r"\breset(?:s)?\s+in\s*(?P<duration>"
    r"(?:\d+\s*(?:days?|d|hours?|hrs?|h|minutes?|mins?|m)\s*)+)",
    re.IGNORECASE,
)
_RESET_DURATION_COMPONENT_PATTERN = re.compile(
    r"(?P<amount>\d+)\s*(?P<unit>days?|d|hours?|hrs?|h|minutes?|mins?|m)\b",
    re.IGNORECASE,
)


def is_auth_only_probe(probe: Mapping[str, Any]) -> bool:
    """True when probe evidence only exercises credentials, not model capacity."""
    if str(probe.get("probe_kind") or "").strip().lower() == "auth":
        return True
    if probe.get("capacity_checked") is False:
        return True
    method = str(probe.get("method") or "").strip().lower()
    if not method:
        return False
    if method in _AUTH_ONLY_PROBE_METHODS:
        return True
    if any(
        method.endswith(suffix)
        for suffix in (
            "_auth_status",
            "_auth_status_refresh",
            "_auth_material",
            "_auth_file",
        )
    ):
        return True
    return False


def _extract_reset_timestamp(
    text: str | None, *, now: datetime | None = None
) -> str | None:
    """Extract a reset timestamp string from freeform provider failure detail."""
    if not text:
        return None
    raw = str(text)
    m = _RESET_DATETIME_PATTERN.search(raw)
    if m:
        raw_ts = m.group("timestamp").strip()
        tm = _RESET_TS_COMPONENTS_PATTERN.match(raw_ts)
        if tm:
            date_part = tm.group("date")
            time_part = tm.group("time")
            tz_part = (tm.group("tz") or "").strip().upper()
            if len(time_part) == 5:
                time_part += ":00"
            if not tz_part or tz_part in ("Z", "UTC"):
                tz_norm = "+00:00"
            else:
                tz_norm = tm.group("tz").strip()
                if len(tz_norm) == 5 and (tz_norm.startswith("+") or tz_norm.startswith("-")) and ":" not in tz_norm:
                    tz_norm = f"{tz_norm[:3]}:{tz_norm[3:]}"
            try:
                dt = datetime.fromisoformat(f"{date_part}T{time_part}{tz_norm}")
                return _iso(dt)
            except ValueError:
                pass

    em = _RESET_EPOCH_PATTERN.search(raw)
    if em:
        try:
            dt = datetime.fromtimestamp(int(em.group("epoch")), tz=timezone.utc)
            return _iso(dt)
        except (OverflowError, OSError, ValueError):
            pass

    duration_match = _RESET_DURATION_PATTERN.search(raw)
    if duration_match:
        seconds = 0
        for component in _RESET_DURATION_COMPONENT_PATTERN.finditer(
            duration_match.group("duration")
        ):
            amount = int(component.group("amount"))
            unit = component.group("unit").lower()
            if unit in {"d", "day", "days"}:
                seconds += amount * 24 * 60 * 60
            elif unit in {"h", "hr", "hrs", "hour", "hours"}:
                seconds += amount * 60 * 60
            else:
                seconds += amount * 60
        if seconds > 0:
            return _iso((now or _utc_now()) + timedelta(seconds=seconds))
    return None


def _future_reset_timestamp(value: object, *, now: datetime) -> str | None:
    parsed = _parse_time(value)
    return _iso(parsed) if parsed is not None and parsed > now else None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_time(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _state(value: object) -> DeliveryHealthState:
    raw = _norm(value)
    for state in DeliveryHealthState:
        if state.value == raw:
            return state
    return DeliveryHealthState.UNKNOWN


def empty_delivery_health() -> dict[str, Any]:
    """Create the only mutable runtime health document used for dispatch."""

    return {
        "version": DELIVERY_HEALTH_VERSION,
        "endpoints": {},
        "accounts": {},
    }


def normalize_delivery_health(snapshot: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a safe, detached V2 delivery-health snapshot.

    Health is deliberately independent of the provider-capabilities report:
    that report is topology/telemetry and can legitimately be stale.  Unknown
    keys are preserved only inside an individual entry, making schema upgrades
    forward-compatible without permitting malformed buckets to affect routing.
    """

    raw = snapshot if isinstance(snapshot, Mapping) else {}
    result = empty_delivery_health()
    for key in ("endpoints", "accounts"):
        values = raw.get(key)
        if not isinstance(values, Mapping):
            continue
        normalized: dict[str, dict[str, Any]] = {}
        for identity, entry in values.items():
            identity_s = str(identity or "").strip()
            if not identity_s or not isinstance(entry, Mapping):
                continue
            item = deepcopy(dict(entry))
            item["state"] = _state(item.get("state")).value
            for timestamp_key in ("observed_at", "valid_until", "retry_at", "quota_reset_at"):
                value = item.get(timestamp_key)
                if value is not None and not str(value).strip():
                    item.pop(timestamp_key, None)
            normalized[identity_s] = item
        result[key] = normalized
    return result


def _health_entry(
    snapshot: Mapping[str, Any] | None,
    *,
    scope: str,
    identity: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    normalized = normalize_delivery_health(snapshot)
    bucket_name = "endpoints" if scope == _ENDPOINT_SCOPE else "accounts"
    entry = deepcopy(normalized[bucket_name].get(str(identity or "").strip()) or {})
    state = _state(entry.get("state"))
    current = now or _utc_now()
    if state is DeliveryHealthState.HEALTHY:
        valid_until = _parse_time(entry.get("valid_until"))
        if valid_until is None or valid_until <= current:
            state = DeliveryHealthState.UNKNOWN
    elif state is DeliveryHealthState.RETRY_AFTER:
        retry_at = _parse_time(entry.get("retry_at"))
        if retry_at is not None and retry_at <= current:
            state = DeliveryHealthState.UNKNOWN
    entry["state"] = state.value
    return entry


def endpoint_health_entry(
    snapshot: Mapping[str, Any] | None,
    endpoint_id: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Read one exact provider/slot health entry with expiry applied."""

    return _health_entry(snapshot, scope=_ENDPOINT_SCOPE, identity=endpoint_id, now=now)


def account_health_entry(
    snapshot: Mapping[str, Any] | None,
    account_id: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Read one shared capacity-account entry with expiry applied."""

    return _health_entry(snapshot, scope=_ACCOUNT_SCOPE, identity=account_id, now=now)


def endpoint_state(
    snapshot: Mapping[str, Any] | None,
    endpoint_id: str,
    *,
    now: datetime | None = None,
) -> DeliveryHealthState:
    return _state(endpoint_health_entry(snapshot, endpoint_id, now=now).get("state"))


def account_state(
    snapshot: Mapping[str, Any] | None,
    account_id: str,
    *,
    now: datetime | None = None,
) -> DeliveryHealthState:
    return _state(account_health_entry(snapshot, account_id, now=now).get("state"))


def _write_entry(
    snapshot: Mapping[str, Any] | None,
    *,
    bucket: str,
    identity: str,
    state: DeliveryHealthState,
    observed_at: datetime,
    valid_until: datetime | None = None,
    retry_at: datetime | None = None,
    reason_kind: str | None = None,
    source: str,
    evidence_endpoint: str,
    detail: str | None = None,
    quota_reset_at: str | None = None,
) -> dict[str, Any]:
    result = normalize_delivery_health(snapshot)
    identity = str(identity or "").strip()
    if not identity:
        return result
    entry: dict[str, Any] = {
        "state": state.value,
        "observed_at": _iso(observed_at),
        "valid_until": _iso(valid_until) if valid_until is not None else None,
        "retry_at": _iso(retry_at) if retry_at is not None else None,
        "reason_kind": str(reason_kind or "") or None,
        "source": source,
        "evidence_endpoint": str(evidence_endpoint or "") or None,
    }
    if detail:
        entry["detail"] = detail
    if quota_reset_at:
        entry["quota_reset_at"] = quota_reset_at
    result[bucket][identity] = entry
    return result


def _retry_time(
    value: object,
    *,
    now: datetime,
    default_retry_seconds: int,
) -> datetime:
    parsed = _parse_time(value)
    if parsed is not None and parsed > now:
        return parsed
    return now + timedelta(seconds=max(1, int(default_retry_seconds)))


def apply_probe(
    snapshot: Mapping[str, Any] | None,
    *,
    endpoint_id: str,
    account_id: str,
    probe: Mapping[str, Any],
    observed_at: datetime | None = None,
    valid_for_seconds: int = 300,
    retry_after_seconds: int = 60,
) -> dict[str, Any]:
    """Apply one *live* exact-endpoint probe without mutating its input.

    Credential failures belong to the endpoint; quota/capacity failures belong
    to the shared account.  A capacity failure still proves the exact
    credential worked, so it refreshes that endpoint to healthy while the
    account gate remains closed.
    """

    if str(probe.get("source") or "").strip().lower() != "live":
        return normalize_delivery_health(snapshot)
    now = observed_at or _parse_time(probe.get("checked_at") or probe.get("last_auth_probe_at")) or _utc_now()
    endpoint_id = str(endpoint_id or "").strip()
    account_id = str(account_id or "").strip()
    readiness = probe.get("ready")
    failure_kind = _probe_failure_kind(readiness, status=probe.get("status"))
    detail = str(probe.get("error") or probe.get("status") or "").strip() or None
    if readiness is True:
        result = _write_entry(
            snapshot,
            bucket="endpoints",
            identity=endpoint_id,
            state=DeliveryHealthState.HEALTHY,
            observed_at=now,
            valid_until=now + timedelta(seconds=max(1, int(valid_for_seconds))),
            source="live_probe",
            evidence_endpoint=endpoint_id,
        )
        if is_auth_only_probe(probe):
            return result
        return _write_entry(
            result,
            bucket="accounts",
            identity=account_id,
            state=DeliveryHealthState.HEALTHY,
            observed_at=now,
            valid_until=now + timedelta(seconds=max(1, int(valid_for_seconds))),
            source="live_probe",
            evidence_endpoint=endpoint_id,
        )
    if failure_kind is None:
        return normalize_delivery_health(snapshot)
    if failure_kind == "auth":
        retryable = probe.get("status") == "auth_retry_after"
        return _write_entry(
            snapshot,
            bucket="endpoints",
            identity=endpoint_id,
            state=DeliveryHealthState.RETRY_AFTER if retryable else DeliveryHealthState.UNAVAILABLE,
            observed_at=now,
            retry_at=_retry_time(probe.get("retry_at"), now=now, default_retry_seconds=retry_after_seconds),
            reason_kind="auth_retryable" if retryable else "auth",
            source="live_probe",
            evidence_endpoint=endpoint_id,
            detail=detail,
        )
    result = _write_entry(
        snapshot,
        bucket="endpoints",
        identity=endpoint_id,
        state=DeliveryHealthState.HEALTHY,
        observed_at=now,
        valid_until=now + timedelta(seconds=max(1, int(valid_for_seconds))),
        source="live_probe",
        evidence_endpoint=endpoint_id,
    )
    quota_reset = str(probe.get("quota_reset_at") or "").strip() or None
    if quota_reset is None and detail:
        quota_reset = _extract_reset_timestamp(detail, now=now)
    observed_reset = quota_reset is not None
    existing_account = account_health_entry(snapshot, account_id, now=now)
    if quota_reset is None:
        quota_reset = _future_reset_timestamp(
            existing_account.get("quota_reset_at"), now=now
        )
    if quota_reset is not None and not _future_reset_timestamp(quota_reset, now=now):
        quota_reset = None
    if quota_reset is not None and not observed_reset:
        # A generic capacity probe can prove that the account remains closed,
        # but it cannot replace a prior provider-supplied reset horizon with a
        # one-minute fallback. Keep the original quota diagnostic until a
        # capacity-success probe proves the account available.
        prior_kind = str(existing_account.get("reason_kind") or "").strip()
        if prior_kind:
            failure_kind = prior_kind
        detail = str(existing_account.get("detail") or "").strip() or detail
    return _write_entry(
        result,
        bucket="accounts",
        identity=account_id,
        state=DeliveryHealthState.RETRY_AFTER,
        observed_at=now,
        retry_at=_retry_time(quota_reset, now=now, default_retry_seconds=retry_after_seconds),
        reason_kind=failure_kind,
        source="live_probe",
        evidence_endpoint=endpoint_id,
        detail=detail,
        quota_reset_at=quota_reset,
    )


def apply_failure(
    snapshot: Mapping[str, Any] | None,
    *,
    endpoint_id: str,
    account_id: str,
    failure_kind: object,
    observed_at: datetime | None = None,
    retry_at: object = None,
    valid_for_seconds: int = 300,
    retry_after_seconds: int = 60,
    detail: str | None = None,
    quota_reset_at: str | None = None,
) -> dict[str, Any]:
    """Project one classified worker failure into the same health snapshot."""

    now = observed_at or _utc_now()
    kind = _norm(failure_kind)
    endpoint_id = str(endpoint_id or "").strip()
    account_id = str(account_id or "").strip()
    if kind == "auth":
        return _write_entry(
            snapshot,
            bucket="endpoints",
            identity=endpoint_id,
            state=DeliveryHealthState.UNAVAILABLE,
            observed_at=now,
            retry_at=_retry_time(retry_at, now=now, default_retry_seconds=retry_after_seconds),
            reason_kind=kind,
            source="worker_failure",
            evidence_endpoint=endpoint_id,
            detail=detail,
        )
    if kind not in {"quota_terminal", "capacity", "capacity_retryable"}:
        return normalize_delivery_health(snapshot)
    result = _write_entry(
        snapshot,
        bucket="endpoints",
        identity=endpoint_id,
        state=DeliveryHealthState.HEALTHY,
        observed_at=now,
        valid_until=now + timedelta(seconds=max(1, int(valid_for_seconds))),
        source="worker_failure",
        evidence_endpoint=endpoint_id,
    )
    quota_reset = str(quota_reset_at or "").strip() or None
    if quota_reset is None:
        parsed_retry = _parse_time(retry_at)
        if parsed_retry is not None:
            quota_reset = _iso(parsed_retry)
        elif detail:
            quota_reset = _extract_reset_timestamp(detail, now=now)
    effective_retry = quota_reset or retry_at
    return _write_entry(
        result,
        bucket="accounts",
        identity=account_id,
        state=DeliveryHealthState.RETRY_AFTER,
        observed_at=now,
        retry_at=_retry_time(effective_retry, now=now, default_retry_seconds=retry_after_seconds),
        reason_kind=kind,
        source="worker_failure",
        evidence_endpoint=endpoint_id,
        detail=detail,
        quota_reset_at=quota_reset,
    )


def _norm(kind: object) -> str:
    return str(kind or "").strip().lower()


def _probe_failure_kind(ready: object, *, status: object = None) -> str | None:
    """Translate one authoritative probe result into the failure vocabulary.

    Probe adapters exercise both credentials and provider capacity.  A false
    result therefore is not automatically an authentication failure:
    ``quota_reached`` is terminal quota, transient capacity/tooling statuses are
    retryable capacity, and only the remaining explicit not-ready results are
    credential failures.  It stays private so no legacy pause/reassignment
    policy can reappear through this module.
    """

    if ready is True or ready is None:
        return None
    status_n = _norm(status)
    if any(marker in status_n for marker in _QUOTA_PROBE_STATUS_MARKERS):
        return "quota_terminal"
    if any(marker in status_n for marker in _CAPACITY_PROBE_STATUS_MARKERS):
        return "capacity_retryable"
    return "auth"
