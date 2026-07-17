from __future__ import annotations

import copy
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from .models import canonical_sha256
from .store import CandidateDecisionConflict, CandidateDecisionStore


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
