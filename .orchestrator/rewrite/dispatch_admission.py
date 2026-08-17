"""Pure execution-admission decision for one canonical task intent.

This module deliberately has no supervisor, filesystem, subprocess, clock, or
runtime-state dependencies.  The supervisor builds one immutable
``AdmissionSnapshot`` per cycle and both planning and queue delivery ask this
same function whether an intent may run.  That prevents the planner accepting
a task which the delivery path later rejects using a different auth/capacity
ladder.

It owns only execution admission.  Task lifecycle remains in ``task_machine``;
workers, leases, probes, retries, and canonical writes stay at the imperative
boundary.
"""
from __future__ import annotations

from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping

from .provider_health import DeliveryHealthState as HealthState
from .task_machine import DispatchReason, dispatch_reason


class HealthScope(str, Enum):
    ENDPOINT = "endpoint"
    ACCOUNT = "account"


class DispatchBlockReason(str, Enum):
    HUMAN_HOLD = "human_hold"
    TASK_NOT_DISPATCHABLE = "task_not_dispatchable"
    TASK_LEASED = "task_leased"
    TASK_PENDING = "task_pending"
    GLOBAL_CAPACITY_REACHED = "global_capacity_reached"
    LANE_CAPACITY_REACHED = "lane_capacity_reached"
    NO_DELIVERY_ENDPOINT = "no_delivery_endpoint"
    ENDPOINT_STATIC_UNAVAILABLE = "endpoint_static_unavailable"
    ENDPOINT_BUSY = "endpoint_busy"
    ACCOUNT_CAPACITY_REACHED = "account_capacity_reached"
    ENDPOINT_HEALTH_UNAVAILABLE = "endpoint_health_unavailable"
    ACCOUNT_HEALTH_UNAVAILABLE = "account_health_unavailable"
    ENDPOINT_RETRY_AFTER = "endpoint_retry_after"
    ACCOUNT_RETRY_AFTER = "account_retry_after"
    HEALTH_REFRESH_REQUIRED = "health_refresh_required"


@dataclass(frozen=True)
class TaskIntent:
    """The canonical task fields relevant to an execution decision."""

    task_id: str
    status: str
    owner: str
    reviewer: str
    dependencies_satisfied: bool
    human_ops_hold: bool = False
    review_binding_current: bool = True


@dataclass(frozen=True)
class DeliveryEndpoint:
    """Static topology for one exact worker delivery endpoint."""

    endpoint_id: str
    provider_id: str
    account_id: str
    enabled: bool = True
    can_auto_deliver: bool = True
    exclusive: bool = True


@dataclass(frozen=True)
class DispatchLane:
    """One logical task owner/reviewer lane and its physical endpoints."""

    lane_id: str
    assignment_identity: str
    max_parallel: int
    endpoints: tuple[DeliveryEndpoint, ...]
    enabled: bool = True


@dataclass(frozen=True)
class HealthRecord:
    """A durable health observation; freshness is expressed by ``refresh_at``.

    ``UNKNOWN`` means evidence is absent or expired.  ``RETRY_AFTER`` blocks
    until ``retry_at`` and then asks the observer to refresh.  ``UNAVAILABLE``
    remains closed, but becomes refreshable at ``refresh_at``.  The health
    observer owns writing the next refresh time after it spends a probe.
    """

    state: HealthState | str
    retry_at: datetime | None = None
    refresh_at: datetime | None = None


@dataclass(frozen=True)
class HealthRefreshTarget:
    scope: HealthScope
    identifier: str


@dataclass(frozen=True)
class AdmissionSnapshot:
    """All mutable runtime facts needed for a deterministic admission check.

    Counts must include both active workers and previously queued intents so a
    planner cannot reserve more global/account/lane capacity than delivery can
    honour.  ``reserved_endpoint_ids`` likewise includes active and pending
    ownership of a physical slot.
    """

    now: datetime
    endpoint_health: Mapping[str, HealthRecord] = field(default_factory=dict)
    account_health: Mapping[str, HealthRecord] = field(default_factory=dict)
    global_reserved: int = 0
    global_limit: int | None = None
    lane_reserved: Mapping[str, int] = field(default_factory=dict)
    account_reserved: Mapping[str, int] = field(default_factory=dict)
    account_limits: Mapping[str, int] = field(default_factory=dict)
    reserved_endpoint_ids: frozenset[str] = frozenset()
    leased_task_ids: frozenset[str] = frozenset()
    pending_task_ids: frozenset[str] = frozenset()


@dataclass(frozen=True)
class DispatchDecision:
    """The sole planner/delivery result for one task and logical lane."""

    eligible: bool
    reason: DispatchBlockReason | None
    task_reason: DispatchReason | None
    logical_lane_id: str
    endpoint_id: str | None = None
    provider_id: str | None = None
    account_id: str | None = None
    health_refresh_targets: tuple[HealthRefreshTarget, ...] = ()

    @property
    def needs_health_refresh(self) -> bool:
        return bool(self.health_refresh_targets)


def _key(value: object) -> str:
    return str(value or "").strip().casefold()


def _same_identity(left: object, right: object) -> bool:
    return bool(_key(left) and _key(left) == _key(right))


def _mapping_value(mapping: Mapping[str, object], identifier: str) -> object | None:
    target = _key(identifier)
    for raw_key, value in mapping.items():
        if _key(raw_key) == target:
            return value
    return None


def _health_record(
    mapping: Mapping[str, HealthRecord],
    identifier: str,
) -> HealthRecord:
    value = _mapping_value(mapping, identifier)
    return value if isinstance(value, HealthRecord) else HealthRecord(HealthState.UNKNOWN)


def _health_state(record: HealthRecord) -> HealthState:
    if isinstance(record.state, HealthState):
        return record.state
    try:
        return HealthState(str(record.state).strip().lower())
    except ValueError:
        return HealthState.UNKNOWN


def _health_gate(
    record: HealthRecord,
    *,
    scope: HealthScope,
    identifier: str,
    now: datetime,
) -> tuple[DispatchBlockReason | None, HealthRefreshTarget | None]:
    """Return the closed gate for one health record without doing any I/O."""

    state = _health_state(record)
    if state is HealthState.HEALTHY:
        return None, None
    if state is HealthState.UNKNOWN:
        return (
            DispatchBlockReason.HEALTH_REFRESH_REQUIRED,
            HealthRefreshTarget(scope, identifier),
        )
    if state is HealthState.RETRY_AFTER:
        if record.retry_at is not None and record.retry_at > now:
            return (
                DispatchBlockReason.ENDPOINT_RETRY_AFTER
                if scope is HealthScope.ENDPOINT
                else DispatchBlockReason.ACCOUNT_RETRY_AFTER,
                None,
            )
        return (
            DispatchBlockReason.HEALTH_REFRESH_REQUIRED,
            HealthRefreshTarget(scope, identifier),
        )
    # ``UNAVAILABLE`` never reopens on time alone.  The observer may ask for
    # another proof only once its stored refresh deadline has arrived.
    if record.refresh_at is None or record.refresh_at <= now:
        return (
            DispatchBlockReason.HEALTH_REFRESH_REQUIRED,
            HealthRefreshTarget(scope, identifier),
        )
    return (
        DispatchBlockReason.ENDPOINT_HEALTH_UNAVAILABLE
        if scope is HealthScope.ENDPOINT
        else DispatchBlockReason.ACCOUNT_HEALTH_UNAVAILABLE,
        None,
    )


def health_gate_for_endpoint(
    *,
    endpoint_id: str,
    account_id: str,
    endpoint_health: Mapping[str, HealthRecord],
    account_health: Mapping[str, HealthRecord],
    now: datetime,
) -> tuple[DispatchBlockReason | None, HealthRefreshTarget | None]:
    """Return the closed gate for one endpoint+account pair, account-first.

    The sole shared health-admission predicate. Account capacity availability
    takes precedence over endpoint credentials: an account has no
    independently probeable credential, so a refresh request caused by the
    account gate always targets the current endpoint instead (there is
    nothing else to probe). Used by both plan/delivery admission
    (``evaluate_dispatch_intent``) and reassignment-recovery's refresh-demand
    scan (``unavailable_assignment_fallback_refresh_targets`` in
    supervisor.py) so a fallback candidate's health is judged by exactly one
    rule instead of two independently-maintained copies that can silently
    disagree -- as they did until 2026-08-17
    (OPS-HEALTH-GATE-ACCOUNT-PRECEDENCE-20260817 fixed the two copies'
    diverged precedence; this change removes the second copy entirely).
    """

    account_gate, account_refresh = _health_gate(
        _health_record(account_health, account_id),
        scope=HealthScope.ACCOUNT,
        identifier=account_id,
        now=now,
    )
    if account_gate is not None:
        refresh = (
            HealthRefreshTarget(HealthScope.ENDPOINT, endpoint_id)
            if account_refresh is not None
            else None
        )
        return account_gate, refresh

    endpoint_gate, endpoint_refresh = _health_gate(
        _health_record(endpoint_health, endpoint_id),
        scope=HealthScope.ENDPOINT,
        identifier=endpoint_id,
        now=now,
    )
    if endpoint_gate is not None:
        return endpoint_gate, endpoint_refresh

    return None, None


def _count(mapping: Mapping[str, int], identifier: str) -> int:
    value = _mapping_value(mapping, identifier)
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _contains(values: frozenset[str], identifier: str) -> bool:
    needle = _key(identifier)
    return any(_key(value) == needle for value in values)


_FINAL_REASON_ORDER = (
    DispatchBlockReason.ACCOUNT_RETRY_AFTER,
    DispatchBlockReason.ENDPOINT_RETRY_AFTER,
    DispatchBlockReason.ACCOUNT_HEALTH_UNAVAILABLE,
    DispatchBlockReason.ENDPOINT_HEALTH_UNAVAILABLE,
    DispatchBlockReason.ACCOUNT_CAPACITY_REACHED,
    DispatchBlockReason.ENDPOINT_BUSY,
    DispatchBlockReason.ENDPOINT_STATIC_UNAVAILABLE,
)


def _first_reason(reasons: list[DispatchBlockReason]) -> DispatchBlockReason:
    for preferred in _FINAL_REASON_ORDER:
        if preferred in reasons:
            return preferred
    return DispatchBlockReason.NO_DELIVERY_ENDPOINT


def evaluate_dispatch_intent(
    intent: TaskIntent,
    lane: DispatchLane,
    snapshot: AdmissionSnapshot,
    *,
    requested_endpoint_id: str | None = None,
) -> DispatchDecision:
    """Evaluate one task intent against one logical lane.

    The result is suitable for both planning and late queue revalidation.  A
    planner calls it with no requested endpoint and persists the returned exact
    endpoint.  Delivery calls it with that endpoint id, so it cannot silently
    select a different slot behind a durable intent.
    """

    lane_id = str(lane.lane_id or "").strip()
    if not lane_id or not str(intent.task_id or "").strip():
        return DispatchDecision(
            False,
            DispatchBlockReason.TASK_NOT_DISPATCHABLE,
            None,
            lane_id,
        )
    if intent.human_ops_hold:
        return DispatchDecision(False, DispatchBlockReason.HUMAN_HOLD, None, lane_id)
    if not lane.enabled:
        return DispatchDecision(
            False,
            DispatchBlockReason.ENDPOINT_STATIC_UNAVAILABLE,
            None,
            lane_id,
        )

    task_reason = dispatch_reason(
        intent.status,
        is_owner=_same_identity(intent.owner, lane.assignment_identity),
        is_reviewer=_same_identity(intent.reviewer, lane.assignment_identity),
        deps_satisfied=bool(intent.dependencies_satisfied),
    )
    if task_reason is None:
        return DispatchDecision(
            False,
            DispatchBlockReason.TASK_NOT_DISPATCHABLE,
            None,
            lane_id,
        )
    if task_reason is DispatchReason.REVIEW_READY and not intent.review_binding_current:
        return DispatchDecision(
            False,
            DispatchBlockReason.TASK_NOT_DISPATCHABLE,
            None,
            lane_id,
        )
    if _contains(snapshot.leased_task_ids, intent.task_id):
        return DispatchDecision(False, DispatchBlockReason.TASK_LEASED, task_reason, lane_id)
    if _contains(snapshot.pending_task_ids, intent.task_id):
        return DispatchDecision(False, DispatchBlockReason.TASK_PENDING, task_reason, lane_id)
    if snapshot.global_limit is not None and snapshot.global_reserved >= max(0, snapshot.global_limit):
        return DispatchDecision(
            False,
            DispatchBlockReason.GLOBAL_CAPACITY_REACHED,
            task_reason,
            lane_id,
        )
    if _count(snapshot.lane_reserved, lane_id) >= max(0, lane.max_parallel):
        return DispatchDecision(
            False,
            DispatchBlockReason.LANE_CAPACITY_REACHED,
            task_reason,
            lane_id,
        )

    endpoints = lane.endpoints
    if requested_endpoint_id is not None:
        endpoints = tuple(
            endpoint
            for endpoint in endpoints
            if _same_identity(endpoint.endpoint_id, requested_endpoint_id)
        )
    if not endpoints:
        return DispatchDecision(
            False,
            DispatchBlockReason.NO_DELIVERY_ENDPOINT,
            task_reason,
            lane_id,
        )

    reasons: list[DispatchBlockReason] = []
    refresh_targets: list[HealthRefreshTarget] = []
    for endpoint in endpoints:
        endpoint_id = str(endpoint.endpoint_id or "").strip()
        provider_id = str(endpoint.provider_id or "").strip()
        account_id = str(endpoint.account_id or "").strip()
        if not endpoint_id or not provider_id or not account_id or not endpoint.enabled or not endpoint.can_auto_deliver:
            reasons.append(DispatchBlockReason.ENDPOINT_STATIC_UNAVAILABLE)
            continue
        if endpoint.exclusive and _contains(snapshot.reserved_endpoint_ids, endpoint_id):
            reasons.append(DispatchBlockReason.ENDPOINT_BUSY)
            continue

        health_reason, health_refresh = health_gate_for_endpoint(
            endpoint_id=endpoint_id,
            account_id=account_id,
            endpoint_health=snapshot.endpoint_health,
            account_health=snapshot.account_health,
            now=snapshot.now,
        )
        if health_reason is not None:
            reasons.append(health_reason)
            if health_refresh is not None:
                refresh_targets.append(health_refresh)
            continue

        account_limit = _mapping_value(snapshot.account_limits, account_id)
        try:
            normalized_limit = max(0, int(account_limit))
        except (TypeError, ValueError):
            normalized_limit = 0
        if _count(snapshot.account_reserved, account_id) >= normalized_limit:
            reasons.append(DispatchBlockReason.ACCOUNT_CAPACITY_REACHED)
            continue

        return DispatchDecision(
            True,
            None,
            task_reason,
            lane_id,
            endpoint_id=endpoint_id,
            provider_id=provider_id,
            account_id=account_id,
        )

    unique_targets = tuple(dict.fromkeys(refresh_targets))
    if unique_targets:
        return DispatchDecision(
            False,
            DispatchBlockReason.HEALTH_REFRESH_REQUIRED,
            task_reason,
            lane_id,
            health_refresh_targets=unique_targets,
        )
    return DispatchDecision(False, _first_reason(reasons), task_reason, lane_id)
