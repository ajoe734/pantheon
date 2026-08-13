"""Behavioral tests for the governed AlphaReplicationQueue."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from .queue import AlphaReplicationQueue, REVIEWABLE_STATES


NOW = datetime(2026, 7, 26, 10, 0, tzinfo=timezone.utc)


def _approved_spec(
    *,
    tenant_id: str = "tenant-a",
    strategy_spec_id: str = "reg-strategy-spec-alpha-1.0.0",
    strategy_id: str = "strat-alpha",
    spec_version: str = "1.0.0",
    artifact_state: str = "approved",
    checksum: str = "sha256:approved-alpha",
    approval_decision_id: str = "approval-alpha-001",
) -> dict:
    return {
        "tenant_id": tenant_id,
        "strategy_spec_id": strategy_spec_id,
        "strategy_id": strategy_id,
        "spec_version": spec_version,
        "artifact_state": artifact_state,
        "checksum": checksum,
        "approval_decision_id": approval_decision_id,
        "approver": "research-reviewer",
        "approved_at": "2026-07-26T09:00:00Z",
    }


def _claim(
    queue: AlphaReplicationQueue,
    *,
    tenant_id: str = "tenant-a",
    now: datetime = NOW,
    lease_seconds: int = 60,
) -> dict:
    claimed = queue.claim_next_pending(
        tenant_id,
        claimant="worker-a",
        lease_seconds=lease_seconds,
        now=now,
    )
    assert claimed is not None
    return claimed


def test_only_explicit_approved_review_enters_queue(tmp_path) -> None:
    queue = AlphaReplicationQueue(tmp_path)
    assert REVIEWABLE_STATES == {"approved"}
    assert queue.enqueue(_approved_spec(), now=NOW) is not None

    for state in ("review", "candidate", "draft"):
        with pytest.raises(ValueError, match="Only approved reviewed"):
            queue.enqueue(
                _approved_spec(
                    strategy_spec_id=f"reg-{state}",
                    artifact_state=state,
                ),
                now=NOW,
            )

    for missing in ("approval_decision_id", "approver", "approved_at", "checksum"):
        payload = _approved_spec(strategy_spec_id=f"reg-missing-{missing}")
        payload.pop(missing)
        with pytest.raises(ValueError, match=missing):
            queue.enqueue(payload, now=NOW)


def test_tenant_and_canonical_strategy_spec_id_form_the_only_queue_key(tmp_path) -> None:
    queue = AlphaReplicationQueue(tmp_path)
    first = queue.enqueue(_approved_spec(tenant_id="tenant-a"), now=NOW)
    second_tenant = queue.enqueue(_approved_spec(tenant_id="tenant-b"), now=NOW)

    assert first is not None
    assert second_tenant is not None
    assert queue.enqueue(_approved_spec(tenant_id="tenant-a"), now=NOW) is None
    assert len(queue.list_all()) == 2


def test_duplicate_key_with_changed_immutable_binding_fails_closed(tmp_path) -> None:
    queue = AlphaReplicationQueue(tmp_path)
    queue.enqueue(_approved_spec(), now=NOW)

    with pytest.raises(ValueError, match="immutable binding conflict.*checksum"):
        queue.enqueue(_approved_spec(checksum="sha256:changed"), now=NOW)
    with pytest.raises(ValueError, match="immutable binding conflict.*approval_decision_id"):
        queue.enqueue(
            _approved_spec(approval_decision_id="approval-alpha-002"),
            now=NOW,
        )


def test_claim_lease_expires_and_stale_token_cannot_ack(tmp_path) -> None:
    queue = AlphaReplicationQueue(tmp_path)
    queue.enqueue(_approved_spec(), now=NOW)
    first = _claim(queue, now=NOW, lease_seconds=30)

    assert (
        queue.claim_next_pending(
            "tenant-a",
            claimant="worker-b",
            lease_seconds=30,
            now=NOW + timedelta(seconds=29),
        )
        is None
    )
    reclaimed = queue.claim_next_pending(
        "tenant-a",
        claimant="worker-b",
        lease_seconds=30,
        now=NOW + timedelta(seconds=31),
    )
    assert reclaimed is not None
    assert reclaimed["claim_generation"] == 2
    assert reclaimed["reclaimed_count"] == 1
    assert reclaimed["claim_token"] != first["claim_token"]

    assert (
        queue.mark_revalidated(
            "tenant-a",
            first["strategy_spec_id"],
            claim_token=first["claim_token"],
            authority_task_id="rtask-stale",
            authority_run_id="rrun-stale",
            experiment_task_id="etask-stale",
            experiment_run_id="erun-stale",
            now=NOW + timedelta(seconds=32),
        )
        is False
    )
    assert queue.mark_revalidated(
        "tenant-a",
        reclaimed["strategy_spec_id"],
        claim_token=reclaimed["claim_token"],
        authority_task_id="rtask-current",
        authority_run_id="rrun-current",
        experiment_task_id="etask-current",
        experiment_run_id="erun-current",
        now=NOW + timedelta(seconds=32),
    )

    entry = queue.list_all()[0]
    assert entry["status"] == "completed"
    assert entry["authority_task_id"] == "rtask-current"
    assert entry["authority_run_ids"] == ["rrun-current"]
    assert entry["experiment_task_id"] == "etask-current"
    assert entry["experiment_run_ids"] == ["erun-current"]


def test_explicit_recovery_and_renewal_are_fenced(tmp_path) -> None:
    queue = AlphaReplicationQueue(tmp_path)
    queue.enqueue(_approved_spec(), now=NOW)
    claim = _claim(queue, now=NOW, lease_seconds=30)

    assert queue.renew_claim(
        "tenant-a",
        claim["strategy_spec_id"],
        claim_token=claim["claim_token"],
        lease_seconds=60,
        now=NOW + timedelta(seconds=20),
    )
    assert queue.recover_expired_claims(
        "tenant-a",
        now=NOW + timedelta(seconds=50),
    ) == 0
    assert queue.recover_expired_claims(
        "tenant-a",
        now=NOW + timedelta(seconds=81),
    ) == 1
    assert queue.list_all()[0]["status"] == "pending"


def test_failures_reach_dlq_and_replay_is_idempotent_and_tenant_scoped(tmp_path) -> None:
    queue = AlphaReplicationQueue(tmp_path)
    queue.enqueue(_approved_spec(tenant_id="tenant-a"), now=NOW)
    queue.enqueue(_approved_spec(tenant_id="tenant-b"), now=NOW)

    for attempt in range(1, 4):
        claim = _claim(
            queue,
            tenant_id="tenant-a",
            now=NOW + timedelta(minutes=attempt),
        )
        assert queue.mark_failed(
            "tenant-a",
            claim["strategy_spec_id"],
            claim_token=claim["claim_token"],
            error=f"failure {attempt}",
            max_retries=3,
            now=NOW + timedelta(minutes=attempt, seconds=1),
        )

    tenant_a = next(
        entry for entry in queue.list_all() if entry["tenant_id"] == "tenant-a"
    )
    tenant_b = next(
        entry for entry in queue.list_all() if entry["tenant_id"] == "tenant-b"
    )
    assert tenant_a["status"] == "dlq"
    assert tenant_a["attempt_count"] == 3
    assert tenant_b["status"] == "pending"

    assert queue.replay_dlq(
        "tenant-a",
        tenant_a["strategy_spec_id"],
        replay_id="replay-alpha-001",
        replayed_by="operator-a",
        reason="dependency repaired",
        now=NOW + timedelta(hours=1),
    )
    assert (
        queue.replay_dlq(
            "tenant-a",
            tenant_a["strategy_spec_id"],
            replay_id="replay-alpha-001",
            replayed_by="operator-a",
            reason="duplicate request",
            now=NOW + timedelta(hours=1, seconds=1),
        )
        is False
    )
    replayed = next(
        entry for entry in queue.list_all() if entry["tenant_id"] == "tenant-a"
    )
    assert replayed["status"] == "pending"
    assert replayed["attempt_count"] == 0
    assert replayed["replay_count"] == 1
    assert replayed["consumed_replay_ids"] == ["replay-alpha-001"]


def test_replay_id_aba_reuse_is_always_a_noop_after_restart(tmp_path) -> None:
    queue = AlphaReplicationQueue(tmp_path)
    payload = _approved_spec()
    queue.enqueue(payload, now=NOW)

    def fail_to_dlq(at: datetime) -> None:
        claim = _claim(queue, now=at)
        assert queue.mark_failed(
            "tenant-a",
            claim["strategy_spec_id"],
            claim_token=claim["claim_token"],
            error="operator replay regression",
            max_retries=1,
            now=at + timedelta(seconds=1),
        )
        assert queue.list_all()[0]["status"] == "dlq"

    fail_to_dlq(NOW + timedelta(minutes=1))
    assert queue.replay_dlq(
        "tenant-a",
        payload["strategy_spec_id"],
        replay_id="replay-A",
        replayed_by="operator-a",
        reason="first repair",
        now=NOW + timedelta(minutes=2),
    )

    fail_to_dlq(NOW + timedelta(minutes=3))
    assert queue.replay_dlq(
        "tenant-a",
        payload["strategy_spec_id"],
        replay_id="replay-B",
        replayed_by="operator-b",
        reason="second repair",
        now=NOW + timedelta(minutes=4),
    )

    fail_to_dlq(NOW + timedelta(minutes=5))
    restarted = AlphaReplicationQueue(tmp_path)
    assert (
        restarted.replay_dlq(
            "tenant-a",
            payload["strategy_spec_id"],
            replay_id="replay-A",
            replayed_by="operator-a",
            reason="stale ABA retry",
            now=NOW + timedelta(minutes=6),
        )
        is False
    )

    entry = restarted.list_all()[0]
    assert entry["status"] == "dlq"
    assert entry["last_replay_id"] == "replay-B"
    assert entry["consumed_replay_ids"] == ["replay-A", "replay-B"]
    assert entry["replay_count"] == 2


def test_queue_state_survives_process_restart(tmp_path) -> None:
    first = AlphaReplicationQueue(tmp_path)
    first.enqueue(_approved_spec(), now=NOW)
    claim = _claim(first, now=NOW)
    assert first.mark_failed(
        "tenant-a",
        claim["strategy_spec_id"],
        claim_token=claim["claim_token"],
        error="transient",
        max_retries=3,
        now=NOW + timedelta(seconds=1),
    )

    restarted = AlphaReplicationQueue(tmp_path)
    entry = restarted.list_all()[0]
    assert entry["tenant_id"] == "tenant-a"
    assert entry["strategy_spec_id"] == "reg-strategy-spec-alpha-1.0.0"
    assert entry["attempt_count"] == 1
    assert entry["status"] == "pending"


def test_get_entry_retrieves_entry_by_tenant_and_spec_id(tmp_path) -> None:
    queue = AlphaReplicationQueue(tmp_path)
    spec = _approved_spec(tenant_id="tenant-a", strategy_spec_id="spec-001")
    queue.enqueue(spec, now=NOW)

    fetched = queue.get_entry("tenant-a", "spec-001")
    assert fetched is not None
    assert fetched["tenant_id"] == "tenant-a"
    assert fetched["strategy_spec_id"] == "spec-001"
    assert fetched["status"] == "pending"

    assert queue.get_entry("tenant-a", "non-existent") is None
    assert queue.get_entry("tenant-b", "spec-001") is None

