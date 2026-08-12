"""Provider failure-response and account health for Supervisor Authority V2.
anti-pattern G).

The incumbent scatters the "what do we do when a worker fails on this account"
decision across a ladder duplicated in several places (classify → is_auth /
is_terminal_quota / is_retryable_capacity → should_pause_dispatch_for_failure_kind
→ maybe_rotate_provider_model → pause/reassign), and auth death is only inferred
*after* a task burns, by log-scraping into a hand-maintained sticky-marker list.

This module makes the decision one pure function over the already-classified
failure *kind* (the kinds `classify_worker_failure` emits) plus the model-rotation
outcome, and gives account health a first-class probed state. It stays pure — the
caller does the log classification / rotation probe once and passes the result —
so it is independently testable and shadow-proven equal to the incumbent
`should_pause_dispatch_for_failure_kind` before anything routes through it.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import enum
from typing import Any, Mapping


class FailureResponse(enum.Enum):
    """What to do with an account/worker after a classified failure."""
    ROTATE = "rotate"      # model rotation absorbed it — re-dispatch the SAME provider
    PAUSE = "pause"        # pause dispatch to this account (quota/capacity/auth)
    RETRY = "retry"        # transient — the same worker/account may be retried
    REASSIGN = "reassign"  # terminal for this attempt — hand the task to another agent


class AccountHealth(enum.Enum):
    """Probed liveness of the account behind a provider (plan §3.6)."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"  # quota/capacity/rate-limit — recovers on its own (retry_at)
    REVOKED = "revoked"    # auth/credential — needs a probe or human, never auto-resumes


class DeliveryHealthState(enum.Enum):
    """Dispatch admission state for one exact endpoint or capacity account.

    ``AccountHealth`` above remains the small compatibility classification used
    by the incumbent failure ladder.  New dispatch code must use this enum and
    the snapshot helpers below instead: an endpoint credential and the account
    which supplies its shared capacity are separate failure domains.
    """

    HEALTHY = "healthy"
    UNKNOWN = "unknown"
    RETRY_AFTER = "retry_after"
    UNAVAILABLE = "unavailable"


DELIVERY_HEALTH_VERSION = 1
_ENDPOINT_SCOPE = "endpoint"
_ACCOUNT_SCOPE = "account"
_DELIVERY_HEALTH_STATES = frozenset(state.value for state in DeliveryHealthState)


# The incumbent's pause-triggering kinds (should_pause_dispatch_for_failure_kind =
# is_terminal_quota OR is_retryable_capacity OR is_auth). "capacity" is the legacy
# spelling is_retryable_capacity_failure_kind also accepts.
_PAUSE_KINDS = frozenset({"quota_terminal", "capacity", "capacity_retryable", "auth"})
# Kinds eligible for model rotation instead of a pause (model_rotation.
# ROTATION_ELIGIBLE_FAILURE_KINDS).
_ROTATION_ELIGIBLE_KINDS = frozenset({"quota_terminal", "capacity_retryable"})
_AUTH_KINDS = frozenset({"auth", "tool_auth"})
_TRANSIENT_KINDS = frozenset({"transient"})
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
    """Return a safe, detached V1 delivery-health snapshot.

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
    failure_kind = classify_probe_failure_kind(readiness, status=probe.get("status"))
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
        return _write_entry(
            snapshot,
            bucket="endpoints",
            identity=endpoint_id,
            state=DeliveryHealthState.UNAVAILABLE,
            observed_at=now,
            retry_at=_retry_time(None, now=now, default_retry_seconds=retry_after_seconds),
            reason_kind="auth",
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
    return _write_entry(
        result,
        bucket="accounts",
        identity=account_id,
        state=DeliveryHealthState.RETRY_AFTER,
        observed_at=now,
        retry_at=_retry_time(retry_at, now=now, default_retry_seconds=retry_after_seconds),
        reason_kind=kind,
        source="worker_failure",
        evidence_endpoint=endpoint_id,
        detail=detail,
    )


def _refresh_required(entry: Mapping[str, Any], *, now: datetime) -> bool:
    state = _state(entry.get("state"))
    if state is DeliveryHealthState.HEALTHY:
        return False
    retry_at = _parse_time(entry.get("retry_at"))
    return retry_at is None or retry_at <= now


def delivery_health_block_reason(
    snapshot: Mapping[str, Any] | None,
    *,
    endpoint_id: str,
    account_id: str,
    now: datetime | None = None,
) -> tuple[str | None, bool]:
    """Return ``(block_reason, needs_live_refresh)`` for one exact endpoint.

    Account capacity availability takes precedence over endpoint credentials.
    This function intentionally has no knowledge of task status, leases, or
    capacity counts; planner and delivery may therefore share it without
    recreating a second scheduler policy.
    """

    current = now or _utc_now()
    normalized = normalize_delivery_health(snapshot)
    account_raw = normalized["accounts"].get(str(account_id or "").strip(), {})
    endpoint_raw = normalized["endpoints"].get(str(endpoint_id or "").strip(), {})
    account = account_health_entry(normalized, account_id, now=current)
    endpoint = endpoint_health_entry(normalized, endpoint_id, now=current)
    account_state_value = _state(account.get("state"))
    endpoint_state_value = _state(endpoint.get("state"))
    if account_state_value is not DeliveryHealthState.HEALTHY:
        if account_state_value is DeliveryHealthState.RETRY_AFTER:
            return "account_retry_after", _refresh_required(account_raw, now=current)
        if account_state_value is DeliveryHealthState.UNAVAILABLE:
            return "account_unavailable", _refresh_required(account_raw, now=current)
        return "account_health_stale", True
    if endpoint_state_value is not DeliveryHealthState.HEALTHY:
        if endpoint_state_value is DeliveryHealthState.RETRY_AFTER:
            return "endpoint_retry_after", _refresh_required(endpoint_raw, now=current)
        if endpoint_state_value is DeliveryHealthState.UNAVAILABLE:
            return "endpoint_unavailable", _refresh_required(endpoint_raw, now=current)
        return "endpoint_health_stale", True
    return None, False


def _norm(kind: object) -> str:
    return str(kind or "").strip().lower()


def should_pause(kind: object) -> bool:
    """Exactly reproduces should_pause_dispatch_for_failure_kind."""
    return _norm(kind) in _PAUSE_KINDS


def is_rotation_eligible(kind: object) -> bool:
    """Whether model rotation may absorb this kind (before deciding to pause)."""
    return _norm(kind) in _ROTATION_ELIGIBLE_KINDS


def decide_failure_response(kind: object, *, rotation_outcome: object = None) -> FailureResponse:
    """The single account failure-response decision (plan §3.6).

    `rotation_outcome` is maybe_rotate_provider_model's return ("rotated",
    "exhausted", "ineligible", "disabled", or None when not probed). A successful
    rotation short-circuits the pause: the exhausted model is cooled and the same
    provider is re-dispatched on an alternate model.
    """
    if _norm(rotation_outcome) == "rotated":
        return FailureResponse.ROTATE
    kind_n = _norm(kind)
    if kind_n in _PAUSE_KINDS:
        return FailureResponse.PAUSE
    if kind_n in _TRANSIENT_KINDS:
        return FailureResponse.RETRY
    return FailureResponse.REASSIGN


def classify_health(kind: object) -> AccountHealth:
    """Probed account health implied by a failure kind (plan §3.6).

    auth/credential ⇒ REVOKED (a first-class state that must be probed/cleared,
    not silently timed-out then dispatched into again); quota/capacity ⇒ DEGRADED
    (self-recovering); anything else is not an account-health signal ⇒ HEALTHY.
    """
    kind_n = _norm(kind)
    if kind_n in _AUTH_KINDS:
        return AccountHealth.REVOKED
    if kind_n in {"quota_terminal", "capacity", "capacity_retryable"}:
        return AccountHealth.DEGRADED
    return AccountHealth.HEALTHY


def classify_probe(ready: object, *, status: object = None) -> AccountHealth | None:
    """Map an authoritative pre-dispatch auth probe to account health.

    ``None`` means the adapter has no supported probe and must not be treated as
    revoked.  A quota/capacity/timeout/tooling failure is degraded; an explicit
    credential failure is revoked immediately, before a worker is launched and
    forced to discover the dead credential from its log.
    """
    failure_kind = classify_probe_failure_kind(ready, status=status)
    if ready is True:
        return AccountHealth.HEALTHY
    if failure_kind is None:
        return None
    return classify_health(failure_kind)


def classify_probe_failure_kind(ready: object, *, status: object = None) -> str | None:
    """Translate one authoritative probe result into the failure vocabulary.

    Probe adapters exercise both credentials and provider capacity.  A false
    result therefore is not automatically an authentication failure:
    ``quota_reached`` is terminal quota, transient capacity/tooling statuses are
    retryable capacity, and only the remaining explicit not-ready results are
    credential failures.  Keeping this classification beside ``classify_probe``
    prevents callers from turning a degraded account into a sticky auth lane.
    """

    if ready is True or ready is None:
        return None
    status_n = _norm(status)
    if any(marker in status_n for marker in _QUOTA_PROBE_STATUS_MARKERS):
        return "quota_terminal"
    if any(marker in status_n for marker in _CAPACITY_PROBE_STATUS_MARKERS):
        return "capacity_retryable"
    return "auth"
