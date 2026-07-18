from __future__ import annotations

import copy
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from .models import canonical_sha256
from .store import CandidateDecisionConflict, CandidateDecisionStore
from ..interaction.store import InteractionLifecycleStore, InteractionConflict


def _candidate(proposal_id: str = "proposal-pg") -> dict:
    record = {
        "proposal_id": proposal_id,
        "revision": 1,
        "state": "draft",
        "tenant_id": "tenant-a",
        "owner_user_id": "user-a",
        "proposer_id": "operator-a",
        "interaction_id": "interaction-a",
        "opinion_id": "opinion-a",
        "opinion_sha256": "a" * 64,
        "measure_id": "measure-a",
        "measure_sha256": "b" * 64,
        "proposed_value": 0.08,
        "created_at": "2026-07-17T12:00:00+00:00",
        "updated_at": "2026-07-17T12:00:00+00:00",
        "expires_at": "2026-07-24T12:00:00+00:00",
        "execution_authority": "none",
        "audit": [],
    }
    record["proposal_digest"] = canonical_sha256(record)
    return record


def _next(candidate: dict, suffix: str) -> tuple[dict, dict]:
    revised = {
        **copy.deepcopy(candidate),
        "revision": candidate["revision"] + 1,
        "state": "deferred",
        "updated_at": "2026-07-17T12:01:00+00:00",
    }
    revised["proposal_digest"] = canonical_sha256(revised)
    decision = {
        "decision_id": f"decision-{suffix}",
        "proposal_id": candidate["proposal_id"],
        "revision": revised["revision"],
        "proposal_digest": revised["proposal_digest"],
        "action": "deferred",
        "execution_authority": "none",
    }
    return revised, decision


def _interaction_store(store: CandidateDecisionStore) -> InteractionLifecycleStore:
    lifecycle = InteractionLifecycleStore(
        backend="postgres", dsn=store.dsn, schema=store.schema
    )
    lifecycle.create_request(
        {
            "interaction_id": "interaction-a",
            "tenant_id": "tenant-a",
            "owner_user_id": "user-a",
            "workshop_id": "workshop-a",
            "human_request": {"operator_id": "operator-a"},
            "created_at": "2026-07-17T12:00:00+00:00",
            "updated_at": "2026-07-17T12:00:00+00:00",
        },
        idempotency_scope="tenant-a:user-a",
        idempotency_key="interaction-a",
        fingerprint=canonical_sha256({"interaction": "a"}),
        trace_id="trace-a",
    )
    return lifecycle


def _atomic_inputs(candidate: dict) -> tuple[dict, list[dict]]:
    link = {
        "proposal_id": candidate["proposal_id"],
        "interaction_id": candidate["interaction_id"],
        "opinion_id": candidate["opinion_id"],
        "opinion_sha256": candidate["opinion_sha256"],
        "measure_id": candidate["measure_id"],
        "measure_sha256": candidate["measure_sha256"],
        "revision": 1,
        "proposal_digest": candidate["proposal_digest"],
        "state": "draft",
        "created_at": candidate["created_at"],
        "execution_authority": "none",
    }
    outbox = [{
        "outbox_id": f"outbox:{candidate['proposal_id']}:sse",
        "projection_kind": "workshop_sse",
        "payload": {"event_type": "candidate.created", "proposal_id": candidate["proposal_id"]},
    }]
    return link, outbox


@pytest.fixture
def postgres_store_pair():
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is not set")
    schema = f"agora_candidate_{uuid.uuid4().hex[:12]}"
    first = CandidateDecisionStore(backend="postgres", dsn=dsn, schema=schema)
    second = CandidateDecisionStore(backend="postgres", dsn=dsn, schema=schema)
    try:
        yield first, second
    finally:
        with first._connect() as conn:
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


def test_postgres_restart_scope_etag_idempotency_and_receipt_readback(postgres_store_pair) -> None:
    first, restarted = postgres_store_pair
    candidate = _candidate()
    fp = canonical_sha256({"create": "candidate"})
    saved = first.create_candidate(candidate, idempotency_key="create-1", fingerprint=fp)
    replay = restarted.create_candidate(
        _candidate("different-random-id"), idempotency_key="create-1", fingerprint=fp
    )
    assert replay.replayed is True
    assert replay.resource == saved.resource
    assert restarted.get(candidate["proposal_id"], "tenant-a", "user-a") == candidate
    assert restarted.get(candidate["proposal_id"], "tenant-other", "user-a") is None
    assert restarted.get(candidate["proposal_id"], "tenant-a", "user-other") is None

    revised, decision = _next(candidate, "one")
    mutated = first.append_decision(
        current=candidate,
        expected_etag=first.etag(candidate),
        next_record=revised,
        decision=decision,
        idempotency_key="decision-1",
        fingerprint=canonical_sha256({"decision": "defer"}),
    )
    assert mutated.resource["candidate"] == revised
    assert restarted.history(candidate["proposal_id"], "tenant-a", "user-a") == [candidate, revised]
    assert restarted.decisions(candidate["proposal_id"], "tenant-a", "user-a") == [decision]
    with pytest.raises(CandidateDecisionConflict, match="ETag"):
        restarted.append_decision(
            current=revised,
            expected_etag='"stale"',
            next_record={**revised, "revision": 3},
            decision={**decision, "decision_id": "decision-stale", "revision": 3},
            idempotency_key="decision-stale",
            fingerprint=canonical_sha256({"decision": "stale"}),
        )

    validation = {
        "validation_receipt_id": "validation-pg",
        "proposal_id": revised["proposal_id"],
        "revision": revised["revision"],
        "proposal_digest": revised["proposal_digest"],
        "tenant_id": "tenant-a",
        "authority": "canonical_validation_service",
        "outcome": "passed",
        "execution_authority": "none",
    }
    first.record_validation(
        current=revised,
        expected_etag=first.etag(revised),
        receipt=validation,
        idempotency_key="validation-1",
        fingerprint=canonical_sha256({"validation": "one"}),
    )
    approval = {
        "approval_decision_id": "approval-pg",
        "proposal_id": revised["proposal_id"],
        "revision": revised["revision"],
        "proposal_digest": revised["proposal_digest"],
        "tenant_id": "tenant-a",
        "authority": "canonical_approval_decision_store",
        "self_approval": False,
        "execution_authority": "none",
    }
    first.record_approval(
        current=revised,
        expected_etag=first.etag(revised),
        receipt=approval,
        idempotency_key="approval-1",
        fingerprint=canonical_sha256({"approval": "one"}),
    )
    assert restarted.validation_receipts(revised["proposal_id"], "tenant-a", "user-a") == [validation]
    assert restarted.approval_receipts(revised["proposal_id"], "tenant-a", "user-a") == [approval]
    assert restarted.validation_receipts(revised["proposal_id"], "tenant-other", "user-a") == []


def test_postgres_candidate_link_and_sse_outbox_are_atomic_and_restart_safe(
    postgres_store_pair,
) -> None:
    first, restarted = postgres_store_pair
    lifecycle = _interaction_store(first)
    restarted_lifecycle = InteractionLifecycleStore(
        backend="postgres", dsn=restarted.dsn, schema=restarted.schema
    )
    candidate = _candidate("proposal-atomic")
    link, outbox = _atomic_inputs(candidate)
    saved = first.create_candidate(
        candidate,
        idempotency_key="candidate-atomic",
        fingerprint=canonical_sha256({"candidate": "atomic"}),
        interaction_store=lifecycle,
        candidate_link=link,
        workshop_outbox=outbox,
    )
    assert restarted.get("proposal-atomic", "tenant-a", "user-a") == saved.resource
    interaction = restarted_lifecycle.get("interaction-a", "tenant-a", "user-a")
    assert interaction is not None
    assert interaction["candidate_proposal_links"] == [link]
    timeline = restarted_lifecycle.timeline("interaction-a", "tenant-a", "user-a")
    assert timeline is not None
    assert [item["payload"] for item in timeline] == [outbox[0]["payload"]]


def test_postgres_atomic_candidate_rolls_back_on_outbox_identity_conflict(
    postgres_store_pair,
) -> None:
    first, _ = postgres_store_pair
    lifecycle = _interaction_store(first)
    candidate = _candidate("proposal-atomic-rollback")
    link, outbox = _atomic_inputs(candidate)
    lifecycle.enqueue("interaction-a", {
        **outbox[0],
        "payload": {"event_type": "conflicting.event"},
    })
    with pytest.raises(InteractionConflict, match="outbox identity"):
        first.create_candidate(
            candidate,
            idempotency_key="candidate-atomic-rollback",
            fingerprint=canonical_sha256({"candidate": "atomic-rollback"}),
            interaction_store=lifecycle,
            candidate_link=link,
            workshop_outbox=outbox,
        )
    assert first.get(candidate["proposal_id"], "tenant-a", "user-a") is None
    interaction = lifecycle.get("interaction-a", "tenant-a", "user-a")
    assert interaction is not None
    assert interaction["candidate_proposal_links"] == []


def test_postgres_concurrent_identical_decision_is_one_revision_one_receipt(postgres_store_pair) -> None:
    first, second = postgres_store_pair
    candidate = _candidate("proposal-race")
    first.create_candidate(
        candidate,
        idempotency_key="create-race",
        fingerprint=canonical_sha256({"create": "race"}),
    )
    fingerprint = canonical_sha256({"decision": "same-request"})

    def mutate(store: CandidateDecisionStore, suffix: str):
        revised, decision = _next(candidate, suffix)
        return store.append_decision(
            current=candidate,
            expected_etag=store.etag(candidate),
            next_record=revised,
            decision=decision,
            idempotency_key="decision-race",
            fingerprint=fingerprint,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda pair: mutate(*pair), [(first, "a"), (second, "b")]))
    assert {result.replayed for result in results} == {False, True}
    assert results[0].resource == results[1].resource
    assert len(first.history(candidate["proposal_id"], "tenant-a", "user-a")) == 2
    assert len(first.decisions(candidate["proposal_id"], "tenant-a", "user-a")) == 1
    assert first.decisions(candidate["proposal_id"], "tenant-a", "user-a")[0]["execution_authority"] == "none"


def test_postgres_different_key_same_etag_race_is_governed_conflict(postgres_store_pair) -> None:
    first, second = postgres_store_pair
    candidate = _candidate("proposal-different-key-race")
    first.create_candidate(
        candidate,
        idempotency_key="create-different-key-race",
        fingerprint=canonical_sha256({"create": "different-key-race"}),
    )

    def mutate(store: CandidateDecisionStore, suffix: str):
        revised, decision = _next(candidate, suffix)
        return store.append_decision(
            current=candidate,
            expected_etag=store.etag(candidate),
            next_record=revised,
            decision=decision,
            idempotency_key=f"different-key-{suffix}",
            fingerprint=canonical_sha256({"decision": suffix}),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(mutate, first, "a"), pool.submit(mutate, second, "b")]
    outcomes = []
    for future in futures:
        try:
            outcomes.append(("saved", future.result()))
        except CandidateDecisionConflict as exc:
            outcomes.append(("conflict", exc))
    assert [kind for kind, _ in outcomes].count("saved") == 1
    assert [kind for kind, _ in outcomes].count("conflict") == 1
    assert len(first.history(candidate["proposal_id"], "tenant-a", "user-a")) == 2
    assert len(first.decisions(candidate["proposal_id"], "tenant-a", "user-a")) == 1


def test_postgres_multiple_validation_runs_have_unique_receipts(postgres_store_pair) -> None:
    first, second = postgres_store_pair
    candidate = _candidate("proposal-validation-runs")
    first.create_candidate(
        candidate,
        idempotency_key="create-validation-runs",
        fingerprint=canonical_sha256({"create": "validation-runs"}),
    )

    def validate(store: CandidateDecisionStore, suffix: str):
        receipt = {
            "validation_receipt_id": f"validation-{suffix}",
            "proposal_id": candidate["proposal_id"],
            "revision": candidate["revision"],
            "proposal_digest": candidate["proposal_digest"],
            "tenant_id": "tenant-a",
            "authority": "canonical_validation_service",
            "outcome": "passed",
            "execution_authority": "none",
        }
        return store.record_validation(
            current=candidate,
            expected_etag=store.etag(candidate),
            receipt=receipt,
            idempotency_key=f"validation-key-{suffix}",
            fingerprint=canonical_sha256({"validation": suffix}),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda pair: validate(*pair), [(first, "a"), (second, "b")]))
    assert all(result.replayed is False for result in results)
    receipts = first.validation_receipts(candidate["proposal_id"], "tenant-a", "user-a")
    assert {item["validation_receipt_id"] for item in receipts} == {"validation-a", "validation-b"}


@pytest.mark.parametrize(
    ("kind", "id_field"),
    [("validation", "validation_receipt_id"), ("approval", "approval_decision_id")],
)
def test_postgres_same_canonical_receipt_new_key_replays_or_conflicts(
    postgres_store_pair, kind: str, id_field: str
) -> None:
    first, restarted = postgres_store_pair
    candidate = _candidate(f"proposal-{kind}-canonical-identity")
    first.create_candidate(
        candidate,
        idempotency_key=f"create-{kind}-canonical-identity",
        fingerprint=canonical_sha256({"create": kind}),
    )
    receipt = {
        id_field: f"{kind}-canonical-id",
        "proposal_id": candidate["proposal_id"],
        "revision": candidate["revision"],
        "proposal_digest": candidate["proposal_digest"],
        "tenant_id": "tenant-a",
        "authority": f"canonical_{kind}_service",
        "execution_authority": "none",
    }
    recorder = first.record_validation if kind == "validation" else first.record_approval
    restarted_recorder = (
        restarted.record_validation if kind == "validation" else restarted.record_approval
    )
    recorder(
        current=candidate,
        expected_etag=first.etag(candidate),
        receipt=receipt,
        idempotency_key=f"{kind}-first-key",
        fingerprint=canonical_sha256({kind: "first"}),
    )
    replay = restarted_recorder(
        current=candidate,
        expected_etag=restarted.etag(candidate),
        receipt=receipt,
        idempotency_key=f"{kind}-second-key",
        fingerprint=canonical_sha256({kind: "second"}),
    )
    assert replay.replayed is True
    conflicting = {**receipt, "execution_authority": "forbidden"}
    with pytest.raises(CandidateDecisionConflict, match="different bytes"):
        restarted_recorder(
            current=candidate,
            expected_etag=restarted.etag(candidate),
            receipt=conflicting,
            idempotency_key=f"{kind}-conflict-key",
            fingerprint=canonical_sha256({kind: "conflict"}),
        )
