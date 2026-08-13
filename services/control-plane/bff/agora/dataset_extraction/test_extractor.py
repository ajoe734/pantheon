"""Unit tests for AgoraDatasetStore and extract_evidence.

Covers:
  - Routing: each interaction kind reaches the correct dataset
  - Idempotency: duplicate evidence_id returns existing record unmodified
  - Governance proof: records carry required boundary fields
  - Listing: scoped by tenant/user
  - Concurrent safety: multiple threads do not corrupt the store
"""
from __future__ import annotations

import os
import uuid
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from .extractor import (
    AgoraDatasetStore,
    ClaimConflictError,
    HandoffConflictError,
    IdempotencyConflictError,
    PrivacyConsentError,
    acknowledgement_request_digest,
    admit_evidence,
    evidence_request_digest,
    extract_evidence,
    sanitize_content_payload,
)
from .models import (
    AgoraInteractionEvidenceRequest,
    DatasetKind,
    InteractionKind,
    route_to_dataset,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_evidence(
    evidence_id: str = "ev-001",
    interaction_kind: str = "ask",
    persona_id: str = "persona-a",
    session_id: str | None = "sess-001",
    content: dict | None = None,
    captured_at: str = "2026-06-27T10:00:00Z",
    consent_granted: bool = True,
    purpose: str = "policy_learning",
    retention_days: int = 30,
    is_raw_conversation: bool = False,
    explicit_conversation_consent: bool = False,
) -> AgoraInteractionEvidenceRequest:
    return AgoraInteractionEvidenceRequest(
        evidence_id=evidence_id,
        interaction_kind=interaction_kind,  # type: ignore[arg-type]
        persona_id=persona_id,
        session_id=session_id,
        content=content or {"text": "question text"},
        source_refs=["session://sess-001"],
        captured_at=captured_at,
        consent_granted=consent_granted,
        purpose=purpose,
        retention_days=retention_days,
        is_raw_conversation=is_raw_conversation,
        explicit_conversation_consent=explicit_conversation_consent,
    )


def _extract(
    evidence: AgoraInteractionEvidenceRequest,
    store: AgoraDatasetStore,
    *,
    tenant_id: str = "t1",
    user_id: str = "u1",
    extracted_at: str = "2026-06-27T10:01:00Z",
) -> tuple[Any, bool]:
    return extract_evidence(
        evidence,
        tenant_id=tenant_id,
        user_id=user_id,
        idempotency_key=f"key:{tenant_id}:{user_id}:{evidence.evidence_id}",
        request_digest=evidence_request_digest(evidence),
        extracted_at=extracted_at,
        store=store,
    )


# ---------------------------------------------------------------------------
# Routing tests
# ---------------------------------------------------------------------------

class TestRouteToDataset:
    def test_ask_routes_to_observe(self) -> None:
        assert route_to_dataset("ask") == DatasetKind.OBSERVE

    def test_journal_routes_to_observe(self) -> None:
        assert route_to_dataset("journal") == DatasetKind.OBSERVE

    def test_note_routes_to_observe(self) -> None:
        assert route_to_dataset("note") == DatasetKind.OBSERVE

    def test_insight_routes_to_observe(self) -> None:
        assert route_to_dataset("insight") == DatasetKind.OBSERVE

    def test_feedback_routes_to_learn(self) -> None:
        assert route_to_dataset("feedback") == DatasetKind.LEARN

    def test_training_example_routes_to_learn(self) -> None:
        assert route_to_dataset("training_example") == DatasetKind.LEARN

    def test_unknown_kind_defaults_to_observe(self) -> None:
        assert route_to_dataset("unknown_future_kind") == DatasetKind.OBSERVE


# ---------------------------------------------------------------------------
# Extraction tests
# ---------------------------------------------------------------------------

class TestExtractEvidence:
    def test_first_extraction_is_new(self) -> None:
        store = AgoraDatasetStore()
        evidence = _make_evidence()
        record, is_new = _extract(evidence, store)
        assert is_new is True

    def test_record_has_correct_dataset_kind(self) -> None:
        store = AgoraDatasetStore()
        record, _ = _extract(_make_evidence(interaction_kind="ask"), store)
        assert record.dataset_kind == DatasetKind.OBSERVE

        store2 = AgoraDatasetStore()
        record2, _ = _extract(_make_evidence(interaction_kind="feedback"), store2)
        assert record2.dataset_kind == DatasetKind.LEARN

    def test_record_carries_governance_proof(self) -> None:
        store = AgoraDatasetStore()
        record, _ = _extract(_make_evidence(), store)
        assert record.governance_boundary == "observe_or_learn_only"
        assert record.no_promote_proof == "agora_observe_learn_only"
        assert record.no_runtime_mutation_proof == "agora_evidence_extract_only"

    def test_record_not_idempotent_on_first_call(self) -> None:
        store = AgoraDatasetStore()
        record, _ = _extract(_make_evidence(), store)
        assert record.idempotent is False

    def test_duplicate_evidence_id_is_idempotent(self) -> None:
        store = AgoraDatasetStore()
        evidence = _make_evidence(evidence_id="ev-dup")
        first, is_new_first = _extract(evidence, store)
        second, is_new_second = _extract(evidence, store)

        assert is_new_first is True
        assert is_new_second is False
        assert second.idempotent is True
        assert second.evidence_id == "ev-dup"

    def test_duplicate_does_not_overwrite_extracted_at(self) -> None:
        store = AgoraDatasetStore()
        evidence = _make_evidence(evidence_id="ev-ts")
        _extract(evidence, store, extracted_at="2026-06-27T10:00:00Z")
        second, _ = _extract(evidence, store, extracted_at="2026-06-27T11:00:00Z")
        # Second call returns original extracted_at
        assert second.extracted_at == "2026-06-27T10:00:00Z"

    def test_record_stores_content_and_source_refs(self) -> None:
        store = AgoraDatasetStore()
        evidence = _make_evidence(
            content={"text": "how does the strategy work?"},
            session_id="sess-xyz",
        )
        record, _ = _extract(evidence, store)
        assert record.content == {"text": "how does the strategy work?"}
        assert record.source_refs == ["session://sess-001"]
        assert record.session_id == "sess-xyz"

    def test_record_tenant_and_user_from_context(self) -> None:
        store = AgoraDatasetStore()
        record, _ = _extract(
            _make_evidence(),
            store,
            tenant_id="tenant-abc",
            user_id="user-xyz",
        )
        assert record.tenant_id == "tenant-abc"
        assert record.user_id == "user-xyz"

    def test_concurrent_duplicate_extraction_returns_one_version_without_500_window(self) -> None:
        store = AgoraDatasetStore()
        evidence = _make_evidence(evidence_id="ev-concurrent-duplicate")
        results: list[tuple[Any, bool]] = []
        errors: list[Exception] = []

        def submit() -> None:
            try:
                results.append(_extract(evidence, store))
            except Exception as exc:
                errors.append(exc)

        workers = [threading.Thread(target=submit) for _ in range(10)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()

        assert errors == []
        assert len(results) == 10
        assert sum(1 for _, is_new in results if is_new) == 1
        assert {record.dataset_version_id for record, _ in results} == {
            results[0][0].dataset_version_id
        }


# ---------------------------------------------------------------------------
# Store listing tests
# ---------------------------------------------------------------------------

class TestAgoraDatasetStore:
    def test_get_returns_none_for_missing_id(self) -> None:
        store = AgoraDatasetStore()
        assert store.get("missing", tenant_id="t1", user_id="u1") is None

    def test_get_returns_record_after_add(self) -> None:
        store = AgoraDatasetStore()
        evidence = _make_evidence(evidence_id="ev-get")
        _extract(evidence, store)
        record = store.get("ev-get", tenant_id="t1", user_id="u1")
        assert record is not None
        assert record.evidence_id == "ev-get"

    def test_list_by_dataset_observe_only(self) -> None:
        store = AgoraDatasetStore()
        _extract(_make_evidence("ask-1", "ask"), store)
        _extract(_make_evidence("fb-1", "feedback"), store)
        _extract(_make_evidence("note-1", "note"), store)
        observe = store.list_by_dataset(
            DatasetKind.OBSERVE,
            tenant_id="t1",
            user_id="u1",
        )
        assert len(observe) == 2
        assert all(r.dataset_kind == DatasetKind.OBSERVE for r in observe)

    def test_list_by_dataset_learn_only(self) -> None:
        store = AgoraDatasetStore()
        _extract(_make_evidence("fb-1", "feedback"), store)
        _extract(_make_evidence("te-1", "training_example"), store)
        _extract(_make_evidence("ask-1", "ask"), store)
        learn = store.list_by_dataset(
            DatasetKind.LEARN,
            tenant_id="t1",
            user_id="u1",
        )
        assert len(learn) == 2
        assert all(r.dataset_kind == DatasetKind.LEARN for r in learn)

    def test_list_scoped_by_tenant(self) -> None:
        store = AgoraDatasetStore()
        _extract(_make_evidence("ev-t1"), store, tenant_id="t1")
        _extract(_make_evidence("ev-t2"), store, tenant_id="t2")
        result = store.list_by_dataset(
            DatasetKind.OBSERVE,
            tenant_id="t1",
            user_id="u1",
        )
        assert len(result) == 1
        assert result[0].tenant_id == "t1"

    def test_list_scoped_by_user(self) -> None:
        store = AgoraDatasetStore()
        _extract(_make_evidence("ev-ua"), store, user_id="ua")
        _extract(_make_evidence("ev-ub"), store, user_id="ub")
        result = store.list_by_dataset(
            DatasetKind.OBSERVE,
            tenant_id="t1",
            user_id="ua",
        )
        assert len(result) == 1
        assert result[0].user_id == "ua"

    def test_page_size_limits_results(self) -> None:
        store = AgoraDatasetStore()
        for i in range(10):
            _extract(_make_evidence(f"ev-{i}", "ask"), store)
        result = store.list_by_dataset(
            DatasetKind.OBSERVE,
            tenant_id="t1",
            user_id="u1",
            page_size=3,
        )
        assert len(result) == 3

    def test_concurrent_writes_are_safe(self) -> None:
        store = AgoraDatasetStore()
        errors: list[Exception] = []

        def add(i: int) -> None:
            try:
                _extract(_make_evidence(f"ev-{i}"), store)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=add, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert len(
            store.list_by_dataset(
                DatasetKind.OBSERVE,
                tenant_id="t1",
                user_id="u1",
            )
        ) == 50


# ---------------------------------------------------------------------------
# Model field tests
# ---------------------------------------------------------------------------

class TestInteractionKindEnum:
    def test_all_kinds_are_routed(self) -> None:
        for kind in InteractionKind:
            dataset = route_to_dataset(kind.value)
            assert dataset in (DatasetKind.OBSERVE, DatasetKind.LEARN)

    def test_feedback_and_training_example_are_learn(self) -> None:
        assert route_to_dataset(InteractionKind.FEEDBACK.value) == DatasetKind.LEARN
        assert route_to_dataset(InteractionKind.TRAINING_EXAMPLE.value) == DatasetKind.LEARN

    def test_remaining_kinds_are_observe(self) -> None:
        observe_kinds = {InteractionKind.ASK, InteractionKind.JOURNAL, InteractionKind.NOTE, InteractionKind.INSIGHT}
        for kind in observe_kinds:
            assert route_to_dataset(kind.value) == DatasetKind.OBSERVE


class TestAgoraInteractionEvidenceRequest:
    def test_valid_request_constructs(self) -> None:
        req = AgoraInteractionEvidenceRequest(
            evidence_id="ev-valid",
            interaction_kind="feedback",
            persona_id="persona-1",
            captured_at="2026-06-27T00:00:00Z",
        )
        assert req.spec_version == "1.0"
        assert req.learning_eligible is True

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(Exception):
            AgoraInteractionEvidenceRequest(
                evidence_id="ev-x",
                interaction_kind="ask",
                persona_id="p",
                captured_at="2026-06-27T00:00:00Z",
                unknown_field="bad",  # type: ignore[call-arg]
            )

    def test_evidence_id_required_non_empty(self) -> None:
        with pytest.raises(Exception):
            AgoraInteractionEvidenceRequest(
                evidence_id="",
                interaction_kind="ask",
                persona_id="p",
                captured_at="2026-06-27T00:00:00Z",
            )


class TestDurableInboxAndHandoffWorker:
    def test_backlog_worker_and_handoff_lifecycle_memory(self) -> None:
        store = AgoraDatasetStore(backend="memory")
        self._run_lifecycle_test(store)

    def test_backlog_worker_and_handoff_lifecycle_postgres(self) -> None:
        dsn = os.getenv("TEST_DATABASE_URL")
        if not dsn:
            pytest.skip("TEST_DATABASE_URL is not set")
        schema = f"agora_ds_{uuid.uuid4().hex[:12]}"
        store = AgoraDatasetStore(backend="postgres", dsn=dsn, schema=schema)
        try:
            self._run_lifecycle_test(store)
        finally:
            with store._connect() as conn:
                conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")

    def _run_lifecycle_test(self, store: AgoraDatasetStore) -> None:
        # Add a record to inbox but do NOT run worker yet
        evidence = _make_evidence(evidence_id="ev-backlog-1", interaction_kind="ask")
        store.add_to_inbox(
            evidence,
            "tenant-test",
            "user-test",
            "2026-06-27T10:00:00Z",
            idempotency_key="backlog-key",
            request_digest=evidence_request_digest(evidence),
        )

        # Verify it is in backlog
        backlog = store.get_backlog("tenant-test", "user-test")
        assert len(backlog) == 1
        assert backlog[0]["evidence_id"] == "ev-backlog-1"
        assert backlog[0]["status"] == "pending"

        # Verify dataset records doesn't have it yet
        assert (
            store.get(
                "ev-backlog-1",
                tenant_id="tenant-test",
                user_id="user-test",
            )
            is None
        )

        # Run worker
        result = store.process_inbox(tenant_id="tenant-test", user_id="user-test")
        assert result["processed"] == 1
        assert result["failed"] == 0
        assert result["handoffs_created"] == 1

        # Verify backlog is empty
        assert len(store.get_backlog("tenant-test", "user-test")) == 0

        # Verify dataset record exists with version 1
        record = store.get(
            "ev-backlog-1",
            tenant_id="tenant-test",
            user_id="user-test",
        )
        assert record is not None
        assert record.version == 1
        assert record.dataset_kind == DatasetKind.OBSERVE

        # Verify handoff generated
        handoffs = store.list_handoffs(tenant_id="tenant-test", user_id="user-test")
        assert len(handoffs) == 1
        assert handoffs[0]["dataset_kind"] == "observe"
        assert handoffs[0]["authority_limit"] == "Observe/Learn"
        assert "ev-backlog-1" in handoffs[0]["evidence_ids"]

        # Test DLQ and replay
        if store.backend == "memory":
            store._inbox[("tenant-test", "user-test", "ev-fail")] = {
                "evidence_id": "ev-fail", "tenant_id": "tenant-test", "user_id": "user-test",
                "idempotency_key": "fail-key", "request_digest": "fail-digest",
                "interaction_kind": "invalid-kind", "persona_id": "p", "session_id": None,
                "content": {}, "source_refs": [], "learning_eligible": True,
                "captured_at": "2026", "status": "pending", "extracted_at": "2026",
                "error_message": None, "created_at": "2026", "processed_at": None,
                "lease_owner": None, "lease_token": None, "lease_expires_at": None,
                "attempt_count": 0,
            }
        else:
            with store._connect() as conn:
                conn.execute(
                    f"INSERT INTO {store._inbox_table} (evidence_id, tenant_id, user_id, idempotency_key, request_digest, interaction_kind, "
                    f"persona_id, content, source_refs, learning_eligible, captured_at, extracted_at, status) "
                    f"VALUES ('ev-fail', 'tenant-test', 'user-test', 'fail-key', 'fail-digest', 'invalid-kind', 'p', '{{}}', '[]', true, '2026', '2026', 'pending')"
                )
        
        # Run worker - it should fail to process and land in DLQ
        result2 = store.process_inbox(tenant_id="tenant-test", user_id="user-test")
        assert result2["failed"] == 1

        # Check DLQ
        dlq = store.get_dlq("tenant-test", "user-test")
        assert len(dlq) == 1
        assert dlq[0]["evidence_id"] == "ev-fail"
        assert dlq[0]["status"] == "failed"
        assert dlq[0]["error_message"] is not None

        # Replay DLQ item
        replayed = store.replay_dlq_item(
            "ev-fail",
            tenant_id="tenant-test",
            user_id="user-test",
        )
        assert replayed is True
        
        # Check that it's back in backlog
        assert len(store.get_dlq("tenant-test", "user-test")) == 0
        assert len(store.get_backlog("tenant-test", "user-test")) == 1

    def test_concurrent_add_to_inbox_postgres(self) -> None:
        dsn = os.getenv("TEST_DATABASE_URL")
        if not dsn:
            pytest.skip("TEST_DATABASE_URL is not set")
        schema = f"agora_ds_{uuid.uuid4().hex[:12]}"
        store = AgoraDatasetStore(backend="postgres", dsn=dsn, schema=schema)
        try:
            self._run_concurrent_add_test(store)
        finally:
            with store._connect() as conn:
                conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")

    def test_concurrent_add_to_inbox_memory(self) -> None:
        store = AgoraDatasetStore(backend="memory")
        self._run_concurrent_add_test(store)

    def _run_concurrent_add_test(self, store: AgoraDatasetStore) -> None:
        import concurrent.futures
        evidence = _make_evidence(evidence_id="ev-concurrent-1", interaction_kind="ask")
        
        # We will submit the same evidence concurrently in multiple threads
        def submit():
            return store.add_to_inbox(
                evidence,
                "tenant-test",
                "user-test",
                "2026-06-27T10:00:00Z",
                idempotency_key="concurrent-key",
                request_digest=evidence_request_digest(evidence),
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(submit) for _ in range(10)]
            results = [f.result() for f in futures]

        # Verify exactly one returned True (is_new) and others returned False
        true_count = sum(1 for res in results if res[1] is True)
        false_count = sum(1 for res in results if res[1] is False)
        assert true_count == 1
        assert false_count == 9


class TestLeasedExtractionOwnership:
    @staticmethod
    def _enqueue(
        store: AgoraDatasetStore,
        evidence_id: str,
        *,
        tenant_id: str = "tenant-lease",
        user_id: str = "user-lease",
    ) -> None:
        evidence = _make_evidence(evidence_id=evidence_id)
        store.add_to_inbox(
            evidence,
            tenant_id,
            user_id,
            "2026-07-26T00:00:00Z",
            idempotency_key=f"lease-key:{tenant_id}:{user_id}:{evidence_id}",
            request_digest=evidence_request_digest(evidence),
        )

    def test_claim_is_bounded_and_exclusive(self) -> None:
        store = AgoraDatasetStore()
        for index in range(7):
            self._enqueue(store, f"ev-claim-{index}")
        now = datetime(2026, 7, 26, tzinfo=timezone.utc)

        first = store.claim_inbox(
            tenant_id="tenant-lease",
            user_id="user-lease",
            worker_id="worker-a",
            batch_size=3,
            lease_seconds=30,
            now=now,
        )
        second = store.claim_inbox(
            tenant_id="tenant-lease",
            user_id="user-lease",
            worker_id="worker-b",
            batch_size=100,
            lease_seconds=30,
            now=now,
        )
        assert len(first) == 3
        assert len(second) == 4
        assert {item["evidence_id"] for item in first}.isdisjoint(
            {item["evidence_id"] for item in second}
        )

    def test_expired_claim_is_recovered_and_stale_owner_cannot_commit(self) -> None:
        store = AgoraDatasetStore()
        self._enqueue(store, "ev-crash")
        started = datetime(2026, 7, 26, tzinfo=timezone.utc)
        stale_claim = store.claim_inbox(
            tenant_id="tenant-lease",
            user_id="user-lease",
            worker_id="worker-crashed",
            lease_seconds=5,
            now=started,
        )[0]

        result = store.process_inbox(
            tenant_id="tenant-lease",
            user_id="user-lease",
            worker_id="worker-recovery",
            now=started + timedelta(seconds=6),
        )
        assert result["processed"] == 1
        assert result["handoffs_created"] == 1
        with pytest.raises(ClaimConflictError):
            store._complete_claim(stale_claim, now=started + timedelta(seconds=7))

        record = store.get(
            "ev-crash",
            tenant_id="tenant-lease",
            user_id="user-lease",
        )
        assert record is not None
        assert record.version == 1
        assert len(
            store.list_handoffs(
                tenant_id="tenant-lease",
                user_id="user-lease",
            )
        ) == 1

    def test_concurrent_processors_create_one_version_and_handoff_each(self) -> None:
        store = AgoraDatasetStore()
        for index in range(40):
            self._enqueue(store, f"ev-worker-{index}")
        results: list[dict[str, Any]] = []

        def process(worker_id: str) -> None:
            results.append(
                store.process_inbox(
                    tenant_id="tenant-lease",
                    user_id="user-lease",
                    worker_id=worker_id,
                    batch_size=10,
                )
            )

        workers = [threading.Thread(target=process, args=(f"worker-{i}",)) for i in range(4)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()

        assert sum(item["processed"] for item in results) == 40
        records = store.list_by_dataset(
            DatasetKind.OBSERVE,
            tenant_id="tenant-lease",
            user_id="user-lease",
            page_size=100,
        )
        assert len(records) == 40
        assert len({record.dataset_version_id for record in records}) == 40
        assert len(
            store.list_handoffs(
                tenant_id="tenant-lease",
                user_id="user-lease",
            )
        ) == 40

    def test_processing_one_scope_does_not_drain_another_tenant(self) -> None:
        store = AgoraDatasetStore()
        self._enqueue(store, "shared-id", tenant_id="tenant-a", user_id="user-a")
        self._enqueue(store, "shared-id", tenant_id="tenant-b", user_id="user-b")

        result = store.process_inbox(
            tenant_id="tenant-a",
            user_id="user-a",
            worker_id="worker-a",
        )
        assert result["processed"] == 1
        assert (
            store.get("shared-id", tenant_id="tenant-b", user_id="user-b")
            is None
        )
        assert len(store.get_backlog("tenant-b", "user-b")) == 1

    def test_concurrent_processors_use_postgres_skip_locked(self) -> None:
        dsn = os.getenv("TEST_DATABASE_URL")
        if not dsn:
            pytest.skip("TEST_DATABASE_URL is not set")
        schema = f"agora_lease_{uuid.uuid4().hex[:12]}"
        store = AgoraDatasetStore(backend="postgres", dsn=dsn, schema=schema)
        try:
            for index in range(30):
                self._enqueue(store, f"ev-pg-worker-{index}")
            results: list[dict[str, Any]] = []

            def process(worker_id: str) -> None:
                results.append(
                    store.process_inbox(
                        tenant_id="tenant-lease",
                        user_id="user-lease",
                        worker_id=worker_id,
                        batch_size=10,
                    )
                )

            workers = [
                threading.Thread(target=process, args=(f"pg-worker-{index}",))
                for index in range(3)
            ]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join()

            assert sum(item["processed"] for item in results) == 30
            assert sum(item["lost_claims"] for item in results) == 0
            assert len(
                store.list_handoffs(
                    tenant_id="tenant-lease",
                    user_id="user-lease",
                )
            ) == 30
        finally:
            with store._connect() as conn:
                conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")


class TestPostgresScopeMigration:
    def test_prior_global_primary_keys_migrate_to_tenant_user_scope(self) -> None:
        dsn = os.getenv("TEST_DATABASE_URL")
        if not dsn:
            pytest.skip("TEST_DATABASE_URL is not set")
        schema = f"agora_migrate_{uuid.uuid4().hex[:12]}"
        quoted = f'"{schema}"'
        with __import__("psycopg").connect(dsn) as conn:
            conn.execute(f"CREATE SCHEMA {quoted}")
            conn.execute(
                f"""
                CREATE TABLE {quoted}.agora_evidence_inbox (
                    evidence_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    interaction_kind TEXT NOT NULL,
                    persona_id TEXT NOT NULL,
                    session_id TEXT,
                    content JSONB NOT NULL,
                    source_refs JSONB NOT NULL,
                    learning_eligible BOOLEAN NOT NULL,
                    captured_at TEXT NOT NULL,
                    extracted_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    error_message TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    processed_at TIMESTAMPTZ
                )
                """
            )
            conn.execute(
                f"""
                CREATE TABLE {quoted}.agora_dataset_records (
                    evidence_id TEXT PRIMARY KEY
                      REFERENCES {quoted}.agora_evidence_inbox(evidence_id) ON DELETE CASCADE,
                    dataset_kind TEXT NOT NULL,
                    interaction_kind TEXT NOT NULL,
                    persona_id TEXT NOT NULL,
                    session_id TEXT,
                    tenant_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    content JSONB NOT NULL,
                    source_refs JSONB NOT NULL,
                    learning_eligible BOOLEAN NOT NULL,
                    governance_boundary TEXT NOT NULL DEFAULT 'observe_or_learn_only',
                    no_promote_proof TEXT NOT NULL DEFAULT 'agora_observe_learn_only',
                    no_runtime_mutation_proof TEXT NOT NULL DEFAULT 'agora_evidence_extract_only',
                    captured_at TEXT NOT NULL,
                    extracted_at TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1
                )
                """
            )
            conn.execute(
                f"""
                CREATE TABLE {quoted}.agora_evidence_handoffs (
                    handoff_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    dataset_kind TEXT NOT NULL,
                    evidence_ids JSONB NOT NULL,
                    summary TEXT NOT NULL,
                    authority_limit TEXT NOT NULL DEFAULT 'Observe/Learn',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            conn.execute(
                f"""
                INSERT INTO {quoted}.agora_evidence_inbox (
                    evidence_id, tenant_id, user_id, interaction_kind, persona_id,
                    content, source_refs, learning_eligible, captured_at,
                    extracted_at, status
                ) VALUES (
                    'legacy-evidence', 'tenant-legacy', 'user-legacy', 'ask',
                    'persona-legacy', '{{}}', '[]', true, '2026', '2026', 'pending'
                )
                """
            )
        store = AgoraDatasetStore(backend="postgres", dsn=dsn, schema=schema)
        try:
            assert len(store.get_backlog("tenant-legacy", "user-legacy")) == 1
            evidence = _make_evidence(evidence_id="shared-after-migration")
            for tenant_id, user_id in (
                ("tenant-a", "user-a"),
                ("tenant-b", "user-b"),
            ):
                store.add_to_inbox(
                    evidence,
                    tenant_id,
                    user_id,
                    "2026-07-26T00:00:00Z",
                    idempotency_key=f"migrate-key:{tenant_id}",
                    request_digest=evidence_request_digest(evidence),
                )
            assert len(store.get_backlog("tenant-a", "user-a")) == 1
            assert len(store.get_backlog("tenant-b", "user-b")) == 1
        finally:
            with store._connect() as conn:
                conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")


class TestPrivacyAndConsentRules:
    def test_consent_revoked_rejected_at_admission(self) -> None:
        store = AgoraDatasetStore(backend="memory")
        evidence = _make_evidence(evidence_id="ev-no-consent", consent_granted=False)
        with pytest.raises(PrivacyConsentError, match="consent not granted"):
            admit_evidence(
                evidence,
                tenant_id="tenant-p",
                user_id="user-p",
                idempotency_key="key-no-consent",
                request_digest=evidence_request_digest(evidence),
                admitted_at="2026-08-13T10:00:00Z",
                store=store,
            )

    def test_raw_conversation_without_explicit_consent_fails_to_dlq(self) -> None:
        store = AgoraDatasetStore(backend="memory")
        evidence = _make_evidence(
            evidence_id="ev-raw-conv",
            is_raw_conversation=True,
            explicit_conversation_consent=False,
        )
        entry, _ = admit_evidence(
            evidence,
            tenant_id="tenant-p",
            user_id="user-p",
            idempotency_key="key-raw-conv",
            request_digest=evidence_request_digest(evidence),
            admitted_at="2026-08-13T10:00:00Z",
            store=store,
        )
        result = store.process_inbox(tenant_id="tenant-p", user_id="user-p")
        assert result["processed"] == 0
        assert result["failed"] == 1

        dlq = store.get_dlq("tenant-p", "user-p")
        assert len(dlq) == 1
        assert "Raw private conversation excluded" in (dlq[0]["error_message"] or "")

    def test_raw_conversation_with_explicit_consent_succeeds_and_redacts(self) -> None:
        store = AgoraDatasetStore(backend="memory")
        evidence = _make_evidence(
            evidence_id="ev-raw-ok",
            is_raw_conversation=True,
            explicit_conversation_consent=True,
            content={
                "raw_transcript": "User asked about portfolio",
                "api_key": "secret-live-key",
                "nested": {"password": "admin-password", "symbol": "BTC-USD"},
            },
        )
        admit_evidence(
            evidence,
            tenant_id="tenant-p",
            user_id="user-p",
            idempotency_key="key-raw-ok",
            request_digest=evidence_request_digest(evidence),
            admitted_at="2026-08-13T10:00:00Z",
            store=store,
        )
        result = store.process_inbox(tenant_id="tenant-p", user_id="user-p")
        assert result["processed"] == 1
        assert result["failed"] == 0

        rec = store.get("ev-raw-ok", tenant_id="tenant-p", user_id="user-p")
        assert rec is not None
        assert rec.redaction_applied is True
        assert rec.content["api_key"] == "[REDACTED]"
        assert rec.content["nested"]["password"] == "[REDACTED]"
        assert rec.content["nested"]["symbol"] == "BTC-USD"

    def test_redaction_sanitizes_credentials_and_tokens(self) -> None:
        raw_content = {
            "auth_token": "bearer-12345",
            "db_secret": "my-secret",
            "credit_card": "4111-2222-3333-4444",
            "persona_text": "Observational signal",
        }
        sanitized, applied = sanitize_content_payload(raw_content)
        assert applied is True
        assert sanitized["auth_token"] == "[REDACTED]"
        assert sanitized["db_secret"] == "[REDACTED]"
        assert sanitized["credit_card"] == "[REDACTED]"
        assert sanitized["persona_text"] == "Observational signal"


class TestCrashRecoveryMatrix:
    def test_crash_recovery_before_dataset_creation(self) -> None:
        """Item claimed by worker A that crashes before completion is safely picked up by worker B."""
        store = AgoraDatasetStore(backend="memory")
        evidence = _make_evidence(evidence_id="ev-crash-before")
        admit_evidence(
            evidence,
            tenant_id="tenant-cr",
            user_id="user-cr",
            idempotency_key="key-cr-1",
            request_digest=evidence_request_digest(evidence),
            admitted_at="2026-08-13T10:00:00Z",
            store=store,
        )

        t0 = datetime(2026, 8, 13, 10, 0, 0, tzinfo=timezone.utc)
        claims = store.claim_inbox(
            tenant_id="tenant-cr",
            user_id="user-cr",
            worker_id="worker-crash",
            lease_seconds=5,
            now=t0,
        )
        assert len(claims) == 1

        # Worker crashes without completing claim.
        # Worker B runs after lease expiration.
        t1 = t0 + timedelta(seconds=10)
        res = store.process_inbox(
            tenant_id="tenant-cr",
            user_id="user-cr",
            worker_id="worker-recovery",
            now=t1,
        )
        assert res["processed"] == 1
        assert res["handoffs_created"] == 1

        # Crashed worker tries to complete stale claim -> rejected
        with pytest.raises(ClaimConflictError):
            store._complete_claim(claims[0], now=t1 + timedelta(seconds=1))

    def test_duplicate_replay_and_readback(self) -> None:
        """Duplicate submissions produce exact idempotent readback."""
        store = AgoraDatasetStore(backend="memory")
        evidence = _make_evidence(evidence_id="ev-dup-replay")
        e1, is_new1 = admit_evidence(
            evidence,
            tenant_id="tenant-cr",
            user_id="user-cr",
            idempotency_key="key-dup-replay",
            request_digest=evidence_request_digest(evidence),
            admitted_at="2026-08-13T10:00:00Z",
            store=store,
        )
        assert is_new1 is True

        e2, is_new2 = admit_evidence(
            evidence,
            tenant_id="tenant-cr",
            user_id="user-cr",
            idempotency_key="key-dup-replay",
            request_digest=evidence_request_digest(evidence),
            admitted_at="2026-08-13T10:00:00Z",
            store=store,
        )
        assert is_new2 is False
        assert e1["evidence_id"] == e2["evidence_id"]
        assert e1["admission_receipt_id"] == e2["admission_receipt_id"]


class TestSingleItemOrderedHandoffAck:
    def test_ack_ordered_single_item_exact_match(self) -> None:
        store = AgoraDatasetStore(backend="memory")
        evidence1 = _make_evidence(evidence_id="ev-ack-1")
        evidence2 = _make_evidence(evidence_id="ev-ack-2")

        extract_evidence(
            evidence1,
            tenant_id="tenant-ack",
            user_id="user-ack",
            idempotency_key="key-ack-1",
            request_digest=evidence_request_digest(evidence1),
            extracted_at="2026-08-13T10:00:00Z",
            store=store,
        )
        extract_evidence(
            evidence2,
            tenant_id="tenant-ack",
            user_id="user-ack",
            idempotency_key="key-ack-2",
            request_digest=evidence_request_digest(evidence2),
            extracted_at="2026-08-13T10:00:00Z",
            store=store,
        )

        handoffs = store.list_handoffs(tenant_id="tenant-ack", user_id="user-ack")
        assert len(handoffs) == 2

        h1 = next(h for h in handoffs if "ev-ack-1" in h["evidence_ids"])
        h2 = next(h for h in handoffs if "ev-ack-2" in h["evidence_ids"])

        # Acknowledge handoff 1 with structured dictionary downstream_ref (from drainer)
        digest1 = acknowledgement_request_digest(
            handoff_id=h1["handoff_id"],
            acknowledgement_id="ack-policy-001",
            dataset_version_id=h1["dataset_version_id"],
            downstream_ref={"drainer": "L12-MFC-R4-AGORA-001", "partition": 1},
        )
        ack_res, acknowledged = store.acknowledge_handoff(
            h1["handoff_id"],
            tenant_id="tenant-ack",
            user_id="user-ack",
            acknowledgement_id="ack-policy-001",
            dataset_version_id=h1["dataset_version_id"],
            downstream_ref={"drainer": "L12-MFC-R4-AGORA-001", "partition": 1},
            acknowledged_by="policy_drainer",
            request_digest=digest1,
            acknowledged_at="2026-08-13T10:05:00Z",
        )
        assert acknowledged is True
        assert ack_res["ack_status"] == "acknowledged"

        # Verify handoff 2 is NOT bulk-acked
        updated_handoffs = store.list_handoffs(tenant_id="tenant-ack", user_id="user-ack")
        h2_current = next(h for h in updated_handoffs if h["handoff_id"] == h2["handoff_id"])
        assert h2_current["ack_status"] == "pending"

    def test_ack_mismatched_version_fails(self) -> None:
        store = AgoraDatasetStore(backend="memory")
        evidence = _make_evidence(evidence_id="ev-ack-mismatch")
        extract_evidence(
            evidence,
            tenant_id="tenant-ack",
            user_id="user-ack",
            idempotency_key="key-ack-mismatch",
            request_digest=evidence_request_digest(evidence),
            extracted_at="2026-08-13T10:00:00Z",
            store=store,
        )
        h = store.list_handoffs(tenant_id="tenant-ack", user_id="user-ack")[0]
        with pytest.raises(HandoffConflictError):
            store.acknowledge_handoff(
                h["handoff_id"],
                tenant_id="tenant-ack",
                user_id="user-ack",
                acknowledgement_id="ack-wrong",
                dataset_version_id="dsv-invalid-version",
                downstream_ref="test-ref",
                acknowledged_by="test-op",
                request_digest="digest-test",
                acknowledged_at="2026-08-13T10:05:00Z",
            )

