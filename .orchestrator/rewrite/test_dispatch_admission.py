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
)
from rewrite.task_machine import DispatchReason


NOW = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)


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


if __name__ == "__main__":
    unittest.main()
