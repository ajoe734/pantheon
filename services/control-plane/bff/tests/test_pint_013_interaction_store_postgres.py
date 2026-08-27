from __future__ import annotations

import concurrent.futures
import hashlib
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Iterator

import pytest

from agora.interaction.store import InteractionLifecycleStore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@pytest.fixture
def postgres_stores() -> Iterator[tuple[InteractionLifecycleStore, InteractionLifecycleStore]]:
    dsn = os.getenv("TEST_DATABASE_URL") or os.getenv("AGORA_GOVERNANCE_STORE_DSN")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL or AGORA_GOVERNANCE_STORE_DSN is not configured")
    pytest.importorskip("psycopg")

    schema = f"pint013_{uuid.uuid4().hex[:20]}"
    first = InteractionLifecycleStore(backend="postgres", dsn=dsn, schema=schema)
    second = InteractionLifecycleStore(backend="postgres", dsn=dsn, schema=schema)
    try:
        yield first, second
    finally:
        with first._connect() as conn:
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


def _request(interaction_id: str, *, request_id: str = "request-1") -> dict[str, Any]:
    timestamp = _now()
    return {
        "interaction_id": interaction_id,
        "tenant_id": "tenant-pint013",
        "owner_user_id": "operator-pint013",
        "workshop_id": "workshop-pint013",
        "status": "queued",
        "mode": "challenge",
        "human_request": {
            "request_id": request_id,
            "operator_id": "operator-pint013",
            "mode": "challenge",
            "request_text": "Challenge the position sizing thesis",
            "submitted_at": timestamp,
            "request_sha256": hashlib.sha256(
                b"Challenge the position sizing thesis"
            ).hexdigest(),
        },
        "context_snapshot": {
            "tenant_id": "tenant-pint013",
            "source_route": "/management/personas/risk-critic",
            "focused_object": {"kind": "persona", "id": "risk-critic"},
            "context_refs": [{"kind": "persona", "id": "risk-critic"}],
            "evidence_cutoff": timestamp,
            "selected_persona_ids": ["risk-critic"],
            "initial_mode": "challenge",
            "return_route": "/management/personas/risk-critic",
            "captured_at": timestamp,
        },
        "participants": [_participant()],
        "provider_invocations": [],
        "opinions": [],
        "synthesis": None,
        "missing_participant_ids": [],
        "degraded_participant_ids": [],
        "candidate_proposal_links": [],
        "audit_refs": [],
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def _participant() -> dict[str, Any]:
    return {
        "persona_id": "risk-critic",
        "persona_version": "persona-v7",
        "session_persona_id": "session-risk-critic",
        "provider_agent_id": "agent-risk-critic",
        "workspace_id": "workspace-risk-critic",
        "environment_ceiling": "research",
        "capability_snapshot": ["persona_opinion"],
        "captured_at": _now(),
    }


def _context_binding(name: str, resolved_at: str) -> dict[str, Any]:
    return {
        "binding_id": f"binding-{name}",
        "tenant_id": "tenant-pint013",
        "workshop_id": "workshop-pint013",
        "context_digest": f"digest-{name}",
        "resolved_at": resolved_at,
    }


def _invocation(interaction_id: str, *, status: str = "queued") -> dict[str, Any]:
    return {
        "invocation_id": f"invoke:{interaction_id}:risk-critic",
        "interaction_id": interaction_id,
        "participant": _participant(),
        "status": status,
        "attempt": 1,
        "started_at": _now(),
        "completed_at": None,
    }


def _create(
    store: InteractionLifecycleStore,
    interaction_id: str,
    *,
    idempotency_key: str | None = None,
    fingerprint: str | None = None,
) -> tuple[dict[str, Any], bool]:
    return store.create_request(
        _request(interaction_id),
        idempotency_scope="tenant-pint013:operator-pint013",
        idempotency_key=idempotency_key or f"submit:{interaction_id}",
        fingerprint=fingerprint or f"fingerprint:{interaction_id}",
        trace_id=f"trace:{interaction_id}",
    )


def _make_retryable_failure(store: InteractionLifecycleStore, interaction_id: str) -> None:
    invocation = _invocation(interaction_id)
    _, claimed = store.claim_invocation(interaction_id, invocation, lease_owner="worker-before-retry")
    assert claimed is True
    failed = {
        **invocation,
        "status": "failed",
        "completed_at": _now(),
        "error": {"code": "provider_unavailable", "retryable": True},
    }
    store.finish_invocation(
        interaction_id,
        invocation=failed,
        opinion=None,
        error={"code": "provider_unavailable", "retryable": True},
        outbox=[],
    )
    store.finalize(
        interaction_id,
        status="failed",
        synthesis=None,
        missing_participant_ids=["risk-critic"],
        degraded_participant_ids=[],
        outbox=[],
    )


def test_two_store_instances_make_concurrent_submit_idempotency_exactly_once(
    postgres_stores: tuple[InteractionLifecycleStore, InteractionLifecycleStore],
) -> None:
    first, second = postgres_stores
    worker_count = 8
    barrier = threading.Barrier(worker_count)

    def submit(index: int) -> tuple[dict[str, Any], bool]:
        barrier.wait(timeout=10)
        store = first if index % 2 == 0 else second
        return _create(
            store,
            f"interaction-race-{index}",
            idempotency_key="submit-race-key",
            fingerprint="same-logical-submit",
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
        results = list(executor.map(submit, range(worker_count)))

    interaction_ids = {resource["interaction_id"] for resource, _created in results}
    assert len(interaction_ids) == 1
    assert sum(created for _resource, created in results) == 1
    assert len(first.list("tenant-pint013", "operator-pint013")) == 1


def test_restart_readback_preserves_committed_lifecycle_with_rpo_zero(
    postgres_stores: tuple[InteractionLifecycleStore, InteractionLifecycleStore],
) -> None:
    first, _second = postgres_stores
    interaction_id = "interaction-rpo0"
    created, was_created = _create(first, interaction_id)
    assert was_created is True
    assert created["status"] == "queued"

    invocation = _invocation(interaction_id)
    first.mark_running(interaction_id)
    _, claimed = first.claim_invocation(interaction_id, invocation, lease_owner="worker-rpo0")
    assert claimed is True
    opinion = {
        "opinion_id": "opinion-rpo0",
        "persona_id": "risk-critic",
        "summary": "Cap the risk before expanding the entry.",
    }
    succeeded = {**invocation, "status": "succeeded", "completed_at": _now()}
    first.finish_invocation(
        interaction_id,
        invocation=succeeded,
        opinion=opinion,
        error=None,
        outbox=[{
            "outbox_id": "outbox-rpo0-opinion",
            "projection_kind": "sse",
            "payload": {"event": "opinion_ready", "opinion_id": "opinion-rpo0"},
        }],
    )
    synthesis = {"summary": "Reduce exposure before adding risk."}
    first.finalize(
        interaction_id,
        status="completed",
        synthesis=synthesis,
        missing_participant_ids=[],
        degraded_participant_ids=[],
        outbox=[{
            "outbox_id": "outbox-rpo0-final",
            "projection_kind": "sse",
            "payload": {"event": "interaction_completed"},
        }],
    )
    first.link_candidate(
        interaction_id,
        {"proposal_id": "proposal-rpo0", "source": "recommended_measure"},
    )

    restarted = InteractionLifecycleStore(
        backend="postgres", dsn=first.dsn, schema=first.schema
    )
    loaded = restarted.get(interaction_id, "tenant-pint013", "operator-pint013")
    assert loaded is not None
    assert loaded["status"] == "completed"
    assert loaded["provider_invocations"] == [succeeded]
    assert loaded["opinions"] == [opinion]
    assert loaded["synthesis"] == synthesis
    assert loaded["candidate_proposal_links"] == [
        {"proposal_id": "proposal-rpo0", "source": "recommended_measure"}
    ]
    assert {
        "audit:interaction-rpo0:submitted",
        "audit:invoke:interaction-rpo0:risk-critic:succeeded",
        "audit:interaction-rpo0:attempt:0:final:completed",
    }.issubset(set(loaded["audit_refs"]))
    timeline = restarted.timeline(interaction_id, "tenant-pint013", "operator-pint013")
    assert timeline is not None
    assert [item["outbox_id"] for item in timeline] == [
        f"iob:interaction_queued:{interaction_id}",
        "outbox-rpo0-opinion",
        "outbox-rpo0-final",
    ]
    assert all(item["state"] == "pending" for item in timeline)


def test_postgres_context_binding_replay_does_not_add_rows_or_rewind_latest(
    postgres_stores: tuple[InteractionLifecycleStore, InteractionLifecycleStore],
) -> None:
    first, restarted = postgres_stores
    original = _context_binding("original", "2026-07-18T00:00:00Z")
    later = _context_binding("later", "2026-07-18T00:00:02Z")

    first.save_context_binding(original, owner_user_id="operator-pint013")
    restarted.save_context_binding(original, owner_user_id="operator-pint013")
    with first._connect() as conn:
        assert conn.execute(
            f"SELECT count(*) FROM {first._context_table}"
        ).fetchone()[0] == 1
    assert restarted.latest_context_binding(
        "tenant-pint013", "operator-pint013", "workshop-pint013"
    ) == original

    first.save_context_binding(later, owner_user_id="operator-pint013")
    restarted.save_context_binding(original, owner_user_id="operator-pint013")
    with first._connect() as conn:
        assert conn.execute(
            f"SELECT count(*) FROM {first._context_table}"
        ).fetchone()[0] == 2
    assert restarted.latest_context_binding(
        "tenant-pint013", "operator-pint013", "workshop-pint013"
    ) == later


def test_expired_invocation_lease_is_recovered_once_after_restart(
    postgres_stores: tuple[InteractionLifecycleStore, InteractionLifecycleStore],
) -> None:
    first, second = postgres_stores
    interaction_id = "interaction-lease-recovery"
    _create(first, interaction_id)
    invocation = _invocation(interaction_id)

    first_claim, claimed = first.claim_invocation(
        interaction_id, invocation, lease_owner="crashed-worker"
    )
    assert claimed is True
    assert first_claim["attempt"] == 1
    live_claim, claimed_live = second.claim_invocation(
        interaction_id, invocation, lease_owner="early-worker"
    )
    assert claimed_live is False
    assert live_claim["lease_owner"] == "crashed-worker"

    with first._connect() as conn:
        conn.execute(
            f"UPDATE {first._invocation_table} "
            "SET lease_until=now()-interval '1 second' WHERE invocation_id=%s",
            (invocation["invocation_id"],),
        )

    restarted = InteractionLifecycleStore(
        backend="postgres", dsn=first.dsn, schema=first.schema
    )
    recovered, recovered_claim = restarted.claim_invocation(
        interaction_id, invocation, lease_owner="recovery-worker"
    )
    assert recovered_claim is True
    assert recovered["attempt"] == 2
    assert recovered["lease_owner"] == "recovery-worker"

    succeeded = {**invocation, "status": "succeeded", "completed_at": _now()}
    restarted.finish_invocation(
        interaction_id,
        invocation=succeeded,
        opinion={"opinion_id": "opinion-recovered", "persona_id": "risk-critic"},
        error=None,
        outbox=[],
    )
    terminal, claimed_terminal = first.claim_invocation(
        interaction_id, invocation, lease_owner="late-worker"
    )
    assert claimed_terminal is False
    assert terminal["status"] == "succeeded"
    assert terminal["attempt"] == 2


def test_recovery_filters_status_before_limit_so_old_queued_work_is_not_starved(
    postgres_stores: tuple[InteractionLifecycleStore, InteractionLifecycleStore],
) -> None:
    first, restarted = postgres_stores
    old_id = "interaction-old-queued"
    _create(first, old_id)
    for index in range(25):
        interaction_id = f"interaction-new-completed-{index:02d}"
        _create(first, interaction_id)
        first.finalize(
            interaction_id,
            status="completed",
            synthesis=None,
            missing_participant_ids=[],
            degraded_participant_ids=[],
            outbox=[],
        )

    recoverable = restarted.recoverable(
        "tenant-pint013", "operator-pint013", limit=25,
    )
    assert [item["interaction_id"] for item in recoverable] == [old_id]


def test_running_recovery_respects_unexpired_invocation_lease_without_provider_dispatch(
    postgres_stores: tuple[InteractionLifecycleStore, InteractionLifecycleStore],
) -> None:
    first, restarted = postgres_stores
    interaction_id = "interaction-running-live-lease"
    _create(first, interaction_id)
    first.mark_running(interaction_id)
    invocation = _invocation(interaction_id, status="running")
    claimed_row, claimed = first.claim_invocation(
        interaction_id, invocation, lease_owner="active-provider-worker",
    )
    assert claimed is True
    assert claimed_row["attempt"] == 1

    recoverable = restarted.recoverable(
        "tenant-pint013", "operator-pint013", limit=25,
    )
    assert [item["interaction_id"] for item in recoverable] == [interaction_id]

    provider_dispatches: list[str] = []
    recovered_row, recovery_claimed = restarted.claim_invocation(
        interaction_id, invocation, lease_owner="recovery-worker",
    )
    if recovery_claimed:
        provider_dispatches.append(invocation["invocation_id"])
    assert recovery_claimed is False
    assert provider_dispatches == []
    assert recovered_row["lease_owner"] == "active-provider-worker"
    assert recovered_row["attempt"] == 1


def test_postgres_recovery_takes_deterministic_oldest_queued_batch(
    postgres_stores: tuple[InteractionLifecycleStore, InteractionLifecycleStore],
) -> None:
    first, restarted = postgres_stores
    for index in range(30):
        interaction_id = f"interaction-queued-{index:02d}"
        _create(first, interaction_id)
        with first._connect() as conn:
            conn.execute(
                f"UPDATE {first._request_table} SET created_at=%s,updated_at=%s "
                "WHERE interaction_id=%s",
                (f"2026-07-17T00:00:{index:02d}Z", f"2026-07-17T00:00:{index:02d}Z", interaction_id),
            )
    assert [
        item["interaction_id"]
        for item in restarted.recoverable("tenant-pint013", "operator-pint013", limit=25)
    ] == [f"interaction-queued-{index:02d}" for index in range(25)]


def test_failed_outbox_dispatch_replays_after_restart_then_stays_completed(
    postgres_stores: tuple[InteractionLifecycleStore, InteractionLifecycleStore],
) -> None:
    first, _second = postgres_stores
    interaction_id = "interaction-outbox-replay"
    _create(first, interaction_id)
    item = {
        "outbox_id": "outbox-replay-1",
        "projection_kind": "sse",
        "payload": {"event": "opinion_ready", "sequence": 7},
    }
    first.enqueue(interaction_id, item)

    attempted: list[tuple[str, dict[str, Any]]] = []

    def fail_dispatch(kind: str, payload: dict[str, Any]) -> None:
        attempted.append((kind, payload))
        raise RuntimeError("projection target temporarily unavailable")

    with pytest.raises(RuntimeError, match="temporarily unavailable"):
        first.drain_outbox(fail_dispatch)
    assert len(attempted) == 1
    assert attempted[0][0] == "interaction_queued"
    assert attempted[0][1]["interaction_id"] == interaction_id

    restarted = InteractionLifecycleStore(
        backend="postgres", dsn=first.dsn, schema=first.schema
    )
    delivered: list[tuple[str, dict[str, Any]]] = []
    assert restarted.drain_outbox(
        lambda kind, payload: delivered.append((kind, payload))
    ) == 2
    assert len(delivered) == 2
    assert delivered[0][0] == "interaction_queued"
    assert delivered[0][1]["interaction_id"] == interaction_id
    assert delivered[1] == ("sse", item["payload"])
    assert restarted.drain_outbox(
        lambda kind, payload: delivered.append((kind, payload))
    ) == 0
    assert len(delivered) == 2
    timeline = restarted.timeline(interaction_id, "tenant-pint013", "operator-pint013")
    assert timeline is not None
    assert len(timeline) == 2
    assert timeline[0]["outbox_id"] == f"iob:interaction_queued:{interaction_id}"
    assert timeline[0]["state"] == "completed"
    assert timeline[0]["attempt"] == 2
    assert timeline[1]["outbox_id"] == item["outbox_id"]
    assert timeline[1]["state"] == "completed"
    assert timeline[1]["attempt"] == 1


def test_concurrent_retry_with_same_key_is_one_durable_command(
    postgres_stores: tuple[InteractionLifecycleStore, InteractionLifecycleStore],
) -> None:
    first, second = postgres_stores
    interaction_id = "interaction-retry-race"
    _create(first, interaction_id)
    _make_retryable_failure(first, interaction_id)
    barrier = threading.Barrier(2)

    def retry(store: InteractionLifecycleStore) -> tuple[dict[str, Any], bool]:
        barrier.wait(timeout=10)
        return store.prepare_retry(
            interaction_id,
            "tenant-pint013",
            "operator-pint013",
            idempotency_key="retry-race-key",
            fingerprint="retry-race-fingerprint",
            actor_id="operator-pint013",
            reason="Provider recovered; retry the failed Persona only.",
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(retry, (first, second)))

    assert sorted(replayed for _resource, replayed in results) == [False, True]
    restarted = InteractionLifecycleStore(
        backend="postgres", dsn=first.dsn, schema=first.schema
    )
    loaded = restarted.get(interaction_id, "tenant-pint013", "operator-pint013")
    assert loaded is not None
    assert loaded["status"] == "queued"
    assert loaded["retry_count"] == 1
    # Retry starts a new attempt-scoped invocation in the runner.  The failed
    # invocation is immutable provider history and must remain available for
    # exact-once replay and audit readback.
    assert loaded["provider_invocations"][0]["status"] == "failed"
    assert loaded["provider_invocations"][0]["error"] == {
        "code": "provider_unavailable",
        "retryable": True,
    }
    assert loaded["audit_refs"].count(
        "audit:retry:interaction-retry-race:retry-race-key"
    ) == 1


def test_postgres_stale_lease_holder_finish_invocation_fenced_after_reclaim(
    postgres_stores: tuple[InteractionLifecycleStore, InteractionLifecycleStore],
) -> None:
    first, second = postgres_stores
    interaction_id = "interaction-pg-stale-invoke"
    _create(first, interaction_id)
    invocation = _invocation(interaction_id)

    # Worker A claims invocation
    first_claim, claimed = first.claim_invocation(
        interaction_id, invocation, lease_owner="worker-A", lease_duration_seconds=1
    )
    assert claimed is True
    assert first_claim["lease_owner"] == "worker-A"
    assert first_claim["attempt"] == 1

    # Expire Worker A lease
    with first._connect() as conn:
        conn.execute(
            f"UPDATE {first._invocation_table} "
            "SET lease_until=now()-interval '1 second' WHERE invocation_id=%s",
            (invocation["invocation_id"],),
        )

    # Worker B reclaims invocation
    second_claim, reclaimed = second.claim_invocation(
        interaction_id, invocation, lease_owner="worker-B", lease_duration_seconds=300
    )
    assert reclaimed is True
    assert second_claim["lease_owner"] == "worker-B"
    assert second_claim["attempt"] == 2

    # Stale Worker A attempts to finish invocation with failure
    failed_A = {
        **invocation,
        "status": "failed",
        "completed_at": _now(),
        "error": {"code": "stale_error", "retryable": False},
    }
    applied_A = first.finish_invocation(
        interaction_id,
        invocation=failed_A,
        opinion=None,
        error=failed_A["error"],
        outbox=[],
        lease_owner="worker-A",
    )
    assert applied_A is False

    # Verify Worker B's active lease is preserved
    inv_row = second.get(interaction_id, "tenant-pint013", "operator-pint013")
    assert inv_row is not None
    assert inv_row["provider_invocations"][0]["status"] == "running"

    # Worker B finishes invocation with success
    succeeded_B = {
        **invocation,
        "status": "succeeded",
        "completed_at": _now(),
    }
    opinion_B = {"opinion_id": "opinion-B", "persona_id": "risk-critic"}
    applied_B = second.finish_invocation(
        interaction_id,
        invocation=succeeded_B,
        opinion=opinion_B,
        error=None,
        outbox=[],
        lease_owner="worker-B",
    )
    assert applied_B is True

    # Verify final state matches Worker B's work
    loaded = first.get(interaction_id, "tenant-pint013", "operator-pint013")
    assert loaded is not None
    assert loaded["provider_invocations"][0]["status"] == "succeeded"
    assert loaded["opinions"][0]["opinion_id"] == "opinion-B"


def test_postgres_stale_lease_holder_finalize_fenced_after_reclaim(
    postgres_stores: tuple[InteractionLifecycleStore, InteractionLifecycleStore],
) -> None:
    first, second = postgres_stores
    interaction_id = "interaction-pg-stale-finalize"
    _create(first, interaction_id)

    # Worker A claims interaction
    claimed_A = first.claim_interaction(
        lease_owner="worker-A",
        lease_duration_seconds=1,
        interaction_id=interaction_id,
    )
    assert claimed_A is not None
    assert claimed_A["lease_owner"] == "worker-A"

    # Expire Worker A lease
    with first._connect() as conn:
        conn.execute(
            f"UPDATE {first._request_table} "
            "SET lease_until=now()-interval '1 second' WHERE interaction_id=%s",
            (interaction_id,),
        )

    # Worker B reclaims interaction
    claimed_B = second.claim_interaction(
        lease_owner="worker-B",
        lease_duration_seconds=300,
        interaction_id=interaction_id,
    )
    assert claimed_B is not None
    assert claimed_B["lease_owner"] == "worker-B"

    # Stale Worker A attempts to finalize as failed
    applied_A = first.finalize(
        interaction_id,
        status="failed",
        synthesis=None,
        missing_participant_ids=["risk-critic"],
        degraded_participant_ids=[],
        outbox=[],
        lease_owner="worker-A",
    )
    assert applied_A is False

    # Verify interaction record is still running under Worker B
    req_mid = second.get(interaction_id, "tenant-pint013", "operator-pint013")
    assert req_mid is not None
    assert req_mid["status"] == "running"
    assert req_mid["lease_owner"] == "worker-B"

    # Worker B finalizes as completed
    synthesis_B = {"summary": "Worker B successfully completed synthesis"}
    applied_B = second.finalize(
        interaction_id,
        status="completed",
        synthesis=synthesis_B,
        missing_participant_ids=[],
        degraded_participant_ids=[],
        outbox=[],
        lease_owner="worker-B",
    )
    assert applied_B is True

    # Verify final record is completed with Worker B's synthesis
    final_req = first.get(interaction_id, "tenant-pint013", "operator-pint013")
    assert final_req is not None
    assert final_req["status"] == "completed"
    assert final_req["synthesis"]["summary"] == "Worker B successfully completed synthesis"

