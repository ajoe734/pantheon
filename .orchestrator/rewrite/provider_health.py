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

import enum


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
