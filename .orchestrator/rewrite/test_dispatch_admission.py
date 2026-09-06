from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rewrite.dispatch_admission import (
    AdmissionSnapshot,
    DeliveryEndpoint,
    DispatchBlockReason,
    DispatchLane,
    HealthRecord,
    HealthRefreshTarget,
    HealthScope,
    HealthState,
    TaskIntent,
    evaluate_dispatch_intent,
    health_gate_for_endpoint,
)
from rewrite.task_machine import DispatchReason
import rewrite.provider_health as provider_health


NOW = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)


def _health_records(snapshot, bucket, *, now):
    """Raw snapshot bucket -> {identity: HealthRecord}, one call site's worth.

    Mirrors supervisor.py's _admission_health_records for this module's own
    tests; production code keeps that conversion in supervisor.py since only
    it reads live runtime state.
    """

    reader = (
        provider_health.endpoint_health_entry
        if bucket == "endpoints"
        else provider_health.account_health_entry
    )
    records = {}
    for identity in provider_health.normalize_delivery_health(snapshot)[bucket]:
        entry = reader(snapshot, identity, now=now)
        records[identity] = HealthRecord(
            state=entry.get("state") or "unknown",
            retry_at=provider_health._parse_time(entry.get("retry_at")),
            refresh_at=provider_health._parse_time(entry.get("retry_at")),
        )
    return records


def _gate_from_snapshot(snapshot, *, endpoint_id, account_id, now):
    return health_gate_for_endpoint(
        endpoint_id=endpoint_id,
        account_id=account_id,
        endpoint_health=_health_records(snapshot, "endpoints", now=now),
        account_health=_health_records(snapshot, "accounts", now=now),
        now=now,
    )


def endpoint(
    endpoint_id: str = "codex-1",
    *,
    account: str = "codex-account",
    enabled: bool = True,
    exclusive: bool = True,
) -> DeliveryEndpoint:
    return DeliveryEndpoint(
        endpoint_id=endpoint_id,
        provider_id=endpoint_id,
        account_id=account,
        enabled=enabled,
        exclusive=exclusive,
    )


def lane(*endpoints: DeliveryEndpoint, identity: str = "Codex", max_parallel: int = 2) -> DispatchLane:
    return DispatchLane(
        lane_id="codex",
        assignment_identity=identity,
        max_parallel=max_parallel,
        endpoints=tuple(endpoints),
    )


def intent(**overrides: object) -> TaskIntent:
    values: dict[str, object] = {
        "task_id": "TASK-1",
        "status": "todo",
        "owner": "Codex",
        "reviewer": "Claude",
        "dependencies_satisfied": True,
    }
    values.update(overrides)
    return TaskIntent(**values)  # type: ignore[arg-type]


def healthy_snapshot(**overrides: object) -> AdmissionSnapshot:
    values: dict[str, object] = {
        "now": NOW,
        "endpoint_health": {"codex-1": HealthRecord(HealthState.HEALTHY)},
        "account_health": {"codex-account": HealthRecord(HealthState.HEALTHY)},
        "global_limit": 8,
        "account_limits": {"codex-account": 2},
    }
    values.update(overrides)
    return AdmissionSnapshot(**values)  # type: ignore[arg-type]


class DispatchAdmissionTests(unittest.TestCase):
    def test_owned_ready_selects_a_healthy_exact_endpoint(self) -> None:
        decision = evaluate_dispatch_intent(intent(), lane(endpoint()), healthy_snapshot())

        self.assertTrue(decision.eligible)
        self.assertIsNone(decision.reason)
        self.assertEqual(decision.task_reason, DispatchReason.OWNED_READY)
        self.assertEqual(decision.endpoint_id, "codex-1")
        self.assertEqual(decision.account_id, "codex-account")

    def test_unauthorized_privileged_task_is_denied_before_capacity(self) -> None:
        decision = evaluate_dispatch_intent(
            intent(execution_authorized=False),
            lane(endpoint()),
            healthy_snapshot(global_reserved=0),
        )

        self.assertFalse(decision.eligible)
        self.assertEqual(
            decision.reason, DispatchBlockReason.EXECUTION_AUTHORIZATION_REQUIRED
        )
        self.assertEqual(decision.task_reason, DispatchReason.OWNED_READY)

    def test_unauthorized_privileged_task_review_dispatch_is_unaffected(self) -> None:
        # OPS-PRIVILEGED-TASK-EXECUTION-AUTH-001 (Codex2 exact-head review
        # finding 7, 2026-09-06): the execution-authorization gate is scoped
        # to owner-execution purposes only. A reviewer's read-only dispatch
        # of a pending/expired/revoked privileged task must still work --
        # review never acquires or clears the mutation grant.
        decision = evaluate_dispatch_intent(
            intent(
                status="review",
                owner="Codex",
                reviewer="Claude",
                execution_authorized=False,
            ),
            lane(endpoint(), identity="Claude"),
            healthy_snapshot(),
        )

        self.assertTrue(decision.eligible)
        self.assertEqual(decision.task_reason, DispatchReason.REVIEW_READY)

    def test_unauthorized_privileged_task_finalize_dispatch_is_unaffected(self) -> None:
        # Same as above for owner closeout/finalize dispatch of an
        # already-approved task: bookkeeping only, no new privileged effect.
        decision = evaluate_dispatch_intent(
            intent(
                status="review_approved",
                owner="Codex",
                reviewer="Claude",
                execution_authorized=False,
            ),
            lane(endpoint(), identity="Codex"),
            healthy_snapshot(),
        )

        self.assertTrue(decision.eligible)
        self.assertEqual(decision.task_reason, DispatchReason.OWNED_FINALIZE)

    def test_unauthorized_privileged_task_in_progress_dispatch_is_denied(self) -> None:
        decision = evaluate_dispatch_intent(
            intent(status="in_progress", execution_authorized=False),
            lane(endpoint()),
            healthy_snapshot(),
        )

        self.assertFalse(decision.eligible)
        self.assertEqual(
            decision.reason, DispatchBlockReason.EXECUTION_AUTHORIZATION_REQUIRED
        )
        self.assertEqual(decision.task_reason, DispatchReason.OWNED_IN_PROGRESS)

    def test_authorized_privileged_task_dispatches_normally(self) -> None:
        decision = evaluate_dispatch_intent(
            intent(execution_authorized=True),
            lane(endpoint()),
            healthy_snapshot(),
        )

        self.assertTrue(decision.eligible)

    def test_review_uses_reviewer_assignment_identity(self) -> None:
        decision = evaluate_dispatch_intent(
            intent(status="review", owner="Codex", reviewer="Claude"),
            lane(endpoint(), identity="Claude"),
            healthy_snapshot(),
        )

        self.assertTrue(decision.eligible)
        self.assertEqual(decision.task_reason, DispatchReason.REVIEW_READY)

    def test_task_lifecycle_mismatch_is_closed_before_health_or_capacity(self) -> None:
        decision = evaluate_dispatch_intent(
            intent(owner="Claude"),
            lane(endpoint()),
            healthy_snapshot(global_reserved=8),
        )

        self.assertFalse(decision.eligible)
        self.assertEqual(decision.reason, DispatchBlockReason.TASK_NOT_DISPATCHABLE)
        self.assertIsNone(decision.task_reason)

    def test_unknown_exact_endpoint_health_requests_refresh_without_launch(self) -> None:
        decision = evaluate_dispatch_intent(
            intent(),
            lane(endpoint()),
            healthy_snapshot(endpoint_health={}),
        )

        self.assertFalse(decision.eligible)
        self.assertEqual(decision.reason, DispatchBlockReason.HEALTH_REFRESH_REQUIRED)
        self.assertTrue(decision.needs_health_refresh)
        self.assertEqual(
            decision.health_refresh_targets,
            (HealthRefreshTarget(HealthScope.ENDPOINT, "codex-1"),),
        )

    def test_healthy_second_slot_is_selected_when_first_slot_is_unavailable(self) -> None:
        slot_one = endpoint("codex-1")
        slot_two = endpoint("codex-2")
        decision = evaluate_dispatch_intent(
            intent(),
            lane(slot_one, slot_two),
            healthy_snapshot(
                endpoint_health={
                    "codex-1": HealthRecord(
                        HealthState.UNAVAILABLE,
                        refresh_at=NOW + timedelta(minutes=5),
                    ),
                    "codex-2": HealthRecord(HealthState.HEALTHY),
                },
            ),
        )

        self.assertTrue(decision.eligible)
        self.assertEqual(decision.endpoint_id, "codex-2")

    def test_account_retry_window_blocks_without_refreshing_early(self) -> None:
        decision = evaluate_dispatch_intent(
            intent(),
            lane(endpoint()),
            healthy_snapshot(
                account_health={
                    "codex-account": HealthRecord(
                        HealthState.RETRY_AFTER,
                        retry_at=NOW + timedelta(minutes=5),
                    )
                }
            ),
        )

        self.assertFalse(decision.eligible)
        self.assertEqual(decision.reason, DispatchBlockReason.ACCOUNT_RETRY_AFTER)
        self.assertFalse(decision.needs_health_refresh)

    def test_due_unavailable_account_requests_the_current_endpoint_refresh(self) -> None:
        decision = evaluate_dispatch_intent(
            intent(),
            lane(endpoint()),
            healthy_snapshot(
                account_health={
                    "codex-account": HealthRecord(
                        HealthState.UNAVAILABLE,
                        refresh_at=NOW,
                    )
                }
            ),
        )

        self.assertFalse(decision.eligible)
        self.assertEqual(decision.reason, DispatchBlockReason.HEALTH_REFRESH_REQUIRED)
        self.assertEqual(
            decision.health_refresh_targets,
            (HealthRefreshTarget(HealthScope.ENDPOINT, "codex-1"),),
        )

    def test_account_gate_is_checked_before_endpoint_gate(self) -> None:
        """A durably blocked account must win over an unprobed endpoint.

        provider_health.delivery_health_block_reason (used by reassignment
        recovery) documents "account capacity availability takes precedence
        over endpoint credentials" and checks account before endpoint. This
        module previously checked endpoint first, so a lane with an unprobed
        endpoint (UNKNOWN, itself harmless) alongside a durably
        quota-exhausted account reported HEALTH_REFRESH_REQUIRED for the
        endpoint instead of ACCOUNT_RETRY_AFTER -- the two admission paths
        disagreed about which fact mattered, and the account exhaustion took
        an extra cycle to surface. Diagnosed 2026-08-17.
        """

        decision = evaluate_dispatch_intent(
            intent(),
            lane(endpoint()),
            healthy_snapshot(
                endpoint_health={"codex-1": HealthRecord(HealthState.UNKNOWN)},
                account_health={
                    "codex-account": HealthRecord(
                        HealthState.RETRY_AFTER,
                        retry_at=NOW + timedelta(days=3),
                    )
                },
            ),
        )

        self.assertFalse(decision.eligible)
        self.assertEqual(decision.reason, DispatchBlockReason.ACCOUNT_RETRY_AFTER)
        self.assertEqual(decision.health_refresh_targets, ())

    def test_account_capacity_and_task_lease_are_distinct_closed_gates(self) -> None:
        capacity = evaluate_dispatch_intent(
            intent(),
            lane(endpoint()),
            healthy_snapshot(account_reserved={"codex-account": 2}),
        )
        leased = evaluate_dispatch_intent(
            intent(),
            lane(endpoint()),
            healthy_snapshot(leased_task_ids=frozenset({"TASK-1"})),
        )

        self.assertEqual(capacity.reason, DispatchBlockReason.ACCOUNT_CAPACITY_REACHED)
        self.assertEqual(leased.reason, DispatchBlockReason.TASK_LEASED)

    def test_late_revalidation_is_bound_to_the_planned_exact_endpoint(self) -> None:
        slot_one = endpoint("codex-1")
        slot_two = endpoint("codex-2")
        decision = evaluate_dispatch_intent(
            intent(),
            lane(slot_one, slot_two),
            healthy_snapshot(
                endpoint_health={
                    "codex-1": HealthRecord(HealthState.HEALTHY),
                    "codex-2": HealthRecord(HealthState.HEALTHY),
                },
                reserved_endpoint_ids=frozenset({"codex-1"}),
            ),
            requested_endpoint_id="codex-1",
        )

        self.assertFalse(decision.eligible)
        self.assertEqual(decision.reason, DispatchBlockReason.ENDPOINT_BUSY)
        self.assertIsNone(decision.endpoint_id)

    def test_parallel_lane_endpoint_uses_lane_and_account_capacity(self) -> None:
        shared_endpoint = endpoint(exclusive=False)
        decision = evaluate_dispatch_intent(
            intent(),
            lane(shared_endpoint, max_parallel=4),
            healthy_snapshot(
                lane_reserved={"codex": 1},
                account_reserved={"codex-account": 1},
                account_limits={"codex-account": 4},
                reserved_endpoint_ids=frozenset({"codex-1"}),
            ),
        )

        self.assertTrue(decision.eligible)
        self.assertEqual(decision.endpoint_id, "codex-1")

    def test_pantheon_dev_resource_is_admitted_when_capacity_free(self) -> None:
        decision = evaluate_dispatch_intent(
            intent(execution_resources=("pantheon-dev",)),
            lane(endpoint()),
            healthy_snapshot(resource_reserved={"pantheon-dev": 0}),
        )

        self.assertTrue(decision.eligible)
        self.assertEqual(decision.endpoint_id, "codex-1")

    def test_pantheon_dev_resource_is_blocked_when_capacity_reached(self) -> None:
        decision = evaluate_dispatch_intent(
            intent(execution_resources=("pantheon-dev",)),
            lane(endpoint()),
            healthy_snapshot(resource_reserved={"pantheon-dev": 1}),
        )

        self.assertFalse(decision.eligible)
        self.assertEqual(decision.reason, DispatchBlockReason.RESOURCE_CAPACITY_REACHED)

    def test_worktree_only_task_is_admitted_when_pantheon_dev_capacity_reached(self) -> None:
        decision = evaluate_dispatch_intent(
            intent(execution_resources=()),
            lane(endpoint()),
            healthy_snapshot(resource_reserved={"pantheon-dev": 1}),
        )

        self.assertTrue(decision.eligible)
        self.assertEqual(decision.endpoint_id, "codex-1")

    def test_parallel_functional_tasks_fill_free_lanes_regardless_of_resource_reservation(self) -> None:
        shared_endpoint = endpoint(exclusive=False)
        decision = evaluate_dispatch_intent(
            intent(execution_resources=()),
            lane(shared_endpoint, max_parallel=4),
            healthy_snapshot(
                lane_reserved={"codex": 2},
                resource_reserved={"pantheon-dev": 1},
                reserved_endpoint_ids=frozenset({"codex-1"}),
            ),
        )

        self.assertTrue(decision.eligible)
        self.assertEqual(decision.endpoint_id, "codex-1")


class HealthGateForEndpointRawSnapshotTests(unittest.TestCase):
    """Migrated from test_provider_health.py's delivery_health_block_reason
    tests: health_gate_for_endpoint is now the sole shared predicate, so
    these realistic apply_probe()-built snapshots exercise it directly
    instead of the deleted provider_health.delivery_health_block_reason.
    """

    def setUp(self) -> None:
        self.now = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)

    def test_live_success_marks_exact_endpoint_and_capacity_account_healthy(self) -> None:
        snapshot = provider_health.apply_probe(
            provider_health.empty_delivery_health(),
            endpoint_id="codex1-2",
            account_id="codex1",
            probe={"source": "live", "ready": True, "status": "ready"},
            observed_at=self.now,
            valid_for_seconds=300,
        )

        reason, refresh = _gate_from_snapshot(
            snapshot, endpoint_id="codex1-2", account_id="codex1", now=self.now
        )
        self.assertIsNone(reason)
        self.assertIsNone(refresh)

    def test_auth_failure_is_endpoint_local_even_when_capacity_account_is_shared(self) -> None:
        snapshot = provider_health.apply_probe(
            provider_health.empty_delivery_health(),
            endpoint_id="claude",
            account_id="claude-shared",
            probe={"source": "live", "ready": True, "status": "ready"},
            observed_at=self.now,
        )
        snapshot = provider_health.apply_probe(
            snapshot,
            endpoint_id="claude2",
            account_id="claude-shared",
            probe={"source": "live", "ready": False, "status": "auth_not_ready"},
            observed_at=self.now,
            retry_after_seconds=60,
        )

        reason, refresh = _gate_from_snapshot(
            snapshot, endpoint_id="claude", account_id="claude-shared", now=self.now
        )
        self.assertIsNone(reason)
        self.assertIsNone(refresh)

    def test_quota_failure_blocks_account_but_preserves_endpoint_auth(self) -> None:
        reset = (self.now + timedelta(hours=2)).isoformat().replace("+00:00", "Z")
        snapshot = provider_health.apply_probe(
            provider_health.empty_delivery_health(),
            endpoint_id="codex1-2",
            account_id="codex1",
            probe={
                "source": "live",
                "ready": False,
                "status": "quota_reached",
                "quota_reset_at": reset,
            },
            observed_at=self.now,
        )

        reason, refresh = _gate_from_snapshot(
            snapshot, endpoint_id="codex1-2", account_id="codex1", now=self.now
        )
        self.assertEqual(reason, DispatchBlockReason.ACCOUNT_RETRY_AFTER)
        self.assertIsNone(refresh)

    def test_expired_or_missing_evidence_demands_one_fresh_observation(self) -> None:
        snapshot = provider_health.apply_probe(
            provider_health.empty_delivery_health(),
            endpoint_id="codex1-2",
            account_id="codex1",
            probe={"source": "live", "ready": True, "status": "ready"},
            observed_at=self.now,
            valid_for_seconds=60,
        )
        later = self.now + timedelta(seconds=61)

        reason, refresh = _gate_from_snapshot(
            snapshot, endpoint_id="codex1-2", account_id="codex1", now=later
        )
        self.assertEqual(reason, DispatchBlockReason.HEALTH_REFRESH_REQUIRED)
        self.assertEqual(refresh, HealthRefreshTarget(HealthScope.ENDPOINT, "codex1-2"))

    def test_cached_probe_never_becomes_delivery_evidence(self) -> None:
        snapshot = provider_health.apply_probe(
            provider_health.empty_delivery_health(),
            endpoint_id="codex1-2",
            account_id="codex1",
            probe={"source": "cached", "ready": True, "status": "ready"},
            observed_at=self.now,
        )

        reason, refresh = _gate_from_snapshot(
            snapshot, endpoint_id="codex1-2", account_id="codex1", now=self.now
        )
        self.assertEqual(reason, DispatchBlockReason.HEALTH_REFRESH_REQUIRED)
        self.assertEqual(refresh, HealthRefreshTarget(HealthScope.ENDPOINT, "codex1-2"))


if __name__ == "__main__":
    unittest.main()
