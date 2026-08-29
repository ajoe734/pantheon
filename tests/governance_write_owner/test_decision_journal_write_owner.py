"""ACG-WRITE-OWNER-GOVERNANCE-20260829: Decision Journal write-owner tests.

These tests prove services.governance.decision_journal is a real,
independent, persistent write owner for Decision Journal entries: writes
survive a brand-new store instance pointed at the same on-disk location
(fresh-read proof), the module never imports the BFF's read_store, and
concurrent-safe compare-and-set is exercised end to end.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from services.governance import decision_journal
from services.governance.decision_journal import (
    DecisionJournalConcurrencyError,
    DecisionJournalValidationError,
    build_decision_journal_stores,
    create_entry,
    get_entry,
    list_audit_events,
    list_entries,
    patch_entry,
)


def test_module_does_not_import_read_store_or_use_local_dict_fallback() -> None:
    """Static guard for the acceptance criterion: no read_store import."""

    source = Path(decision_journal.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.add(node.module)
    assert not any("read_store" in name for name in imported_names)
    assert not any("bff" in name for name in imported_names)


def test_create_entry_persists_across_a_brand_new_store_instance(tmp_path) -> None:
    stores = build_decision_journal_stores(tmp_path)

    created = create_entry(
        stores,
        entry_id="entry-1",
        title="Freeze persona-alpha ahead of promotion",
        body="Rationale for the freeze decision.",
        actor_id="governance_reviewer:alice",
        created_at="2026-08-29T00:00:00Z",
        tags=["freeze", "persona-alpha"],
        linked_strategy_ids=["strategy-1"],
        linked_persona_ids=["persona-alpha"],
        visibility="desk",
    )
    assert created["id"] == "entry-1"
    assert created["version"] == 1
    assert created["canonicalWriteAuthority"] == "governance-decision-journal-svc"

    # Fresh instance, same directory: this is the write-then-fresh-read proof.
    # If persistence were an in-memory dict/local overlay, this would be empty.
    fresh_stores = build_decision_journal_stores(tmp_path)
    fresh_read = get_entry(fresh_stores, "entry-1")
    assert fresh_read == created

    fresh_list = list_entries(fresh_stores)
    assert [entry["id"] for entry in fresh_list] == ["entry-1"]


def test_create_entry_is_idempotent_by_id_and_does_not_clobber(tmp_path) -> None:
    stores = build_decision_journal_stores(tmp_path)

    first = create_entry(
        stores,
        entry_id="entry-dup",
        title="Original title",
        body="Original body",
        actor_id="actor-a",
        created_at="2026-08-29T00:00:00Z",
    )
    second = create_entry(
        stores,
        entry_id="entry-dup",
        title="A different title that should not win",
        body="Different body",
        actor_id="actor-b",
        created_at="2026-08-29T01:00:00Z",
    )
    assert second == first

    fresh_stores = build_decision_journal_stores(tmp_path)
    assert get_entry(fresh_stores, "entry-dup") == first


def test_create_entry_rejects_invalid_title_and_body(tmp_path) -> None:
    stores = build_decision_journal_stores(tmp_path)

    with pytest.raises(DecisionJournalValidationError):
        create_entry(
            stores,
            entry_id="entry-bad-title",
            title="   ",
            body="body",
            actor_id="actor-a",
            created_at="2026-08-29T00:00:00Z",
        )

    with pytest.raises(DecisionJournalValidationError):
        create_entry(
            stores,
            entry_id="entry-bad-body",
            title="ok title",
            body="x" * 20001,
            actor_id="actor-a",
            created_at="2026-08-29T00:00:00Z",
        )

    fresh_stores = build_decision_journal_stores(tmp_path)
    assert list_entries(fresh_stores) == []


def test_patch_entry_versions_diffs_and_persists_across_fresh_store(tmp_path) -> None:
    stores = build_decision_journal_stores(tmp_path)
    create_entry(
        stores,
        entry_id="entry-patch",
        title="Initial title",
        body="Initial body",
        actor_id="actor-a",
        created_at="2026-08-29T00:00:00Z",
        tags=["draft"],
    )

    result = patch_entry(
        stores,
        "entry-patch",
        patch={"title": "Updated title", "tags": ["final"]},
        actor_id="actor-b",
        idempotency_key="idem-1",
        request_hash="hash-1",
        patched_at="2026-08-29T02:00:00Z",
        correlation_id="corr-1",
    )
    assert result["status"] == "updated"
    assert result["entry"]["title"] == "Updated title"
    assert result["entry"]["tags"] == ["final"]
    assert result["entry"]["version"] == 2
    assert result["audit"]["diff"]["changedFields"] == ["title", "tags"]

    fresh_stores = build_decision_journal_stores(tmp_path)
    fresh_entry = get_entry(fresh_stores, "entry-patch")
    assert fresh_entry["title"] == "Updated title"
    assert fresh_entry["version"] == 2

    fresh_audit = list_audit_events(fresh_stores, entry_id="entry-patch")
    assert len(fresh_audit) == 1
    assert fresh_audit[0]["idempotencyKey"] == "idem-1"


def test_patch_entry_replays_identical_idempotency_key(tmp_path) -> None:
    stores = build_decision_journal_stores(tmp_path)
    create_entry(
        stores,
        entry_id="entry-replay",
        title="Title",
        body="Body",
        actor_id="actor-a",
        created_at="2026-08-29T00:00:00Z",
    )
    first = patch_entry(
        stores,
        "entry-replay",
        patch={"title": "Patched"},
        actor_id="actor-b",
        idempotency_key="idem-replay",
        request_hash="hash-replay",
        patched_at="2026-08-29T02:00:00Z",
    )

    fresh_stores = build_decision_journal_stores(tmp_path)
    replayed = patch_entry(
        fresh_stores,
        "entry-replay",
        patch={"title": "Patched"},
        actor_id="actor-b",
        idempotency_key="idem-replay",
        request_hash="hash-replay",
        patched_at="2026-08-29T03:00:00Z",
    )
    assert replayed["status"] == "replayed"
    assert replayed["entry"] == first["entry"]


def test_patch_entry_detects_idempotency_key_reuse_with_different_payload(tmp_path) -> None:
    stores = build_decision_journal_stores(tmp_path)
    create_entry(
        stores,
        entry_id="entry-conflict",
        title="Title",
        body="Body",
        actor_id="actor-a",
        created_at="2026-08-29T00:00:00Z",
    )
    patch_entry(
        stores,
        "entry-conflict",
        patch={"title": "First patch"},
        actor_id="actor-b",
        idempotency_key="idem-conflict",
        request_hash="hash-a",
        patched_at="2026-08-29T02:00:00Z",
    )
    conflict = patch_entry(
        stores,
        "entry-conflict",
        patch={"title": "Second patch, different payload"},
        actor_id="actor-b",
        idempotency_key="idem-conflict",
        request_hash="hash-b",
        patched_at="2026-08-29T03:00:00Z",
    )
    assert conflict["status"] == "conflict"


def test_patch_entry_returns_none_for_missing_entry(tmp_path) -> None:
    stores = build_decision_journal_stores(tmp_path)
    result = patch_entry(
        stores,
        "does-not-exist",
        patch={"title": "x"},
        actor_id="actor-a",
        idempotency_key="idem-missing",
        request_hash="hash-missing",
        patched_at="2026-08-29T00:00:00Z",
    )
    assert result is None


def test_patch_entry_retries_on_a_lost_compare_and_set_race(tmp_path) -> None:
    """A writer whose compare-and-set loses a race must re-read the durable
    store and retry instead of raising or silently overwriting the winner
    (proves this is real compare-and-set persistence, not a local write)."""

    stores = build_decision_journal_stores(tmp_path)
    create_entry(
        stores,
        entry_id="entry-race",
        title="Title",
        body="Body",
        actor_id="actor-a",
        created_at="2026-08-29T00:00:00Z",
    )

    real_compare_and_set = stores.entries.compare_and_set
    attempts = {"count": 0}

    def flaky_compare_and_set(expected_record, record):
        attempts["count"] += 1
        if attempts["count"] == 1:
            # Simulate another writer having already committed a change to
            # the durable store between this caller's read and its write.
            winner_record = dict(expected_record)
            winner_record["title"] = "Won the race"
            winner_record["version"] = int(expected_record.get("version") or 0) + 1
            updated, _ = real_compare_and_set(expected_record, winner_record)
            assert updated
            return False, winner_record
        return real_compare_and_set(expected_record, record)

    stores.entries.compare_and_set = flaky_compare_and_set

    result = patch_entry(
        stores,
        "entry-race",
        patch={"body": "Race loser body update"},
        actor_id="actor-c",
        idempotency_key="idem-race-loser",
        request_hash="hash-race-loser",
        patched_at="2026-08-29T02:00:00Z",
    )
    assert attempts["count"] == 2
    assert result["status"] == "updated"
    assert result["entry"]["title"] == "Won the race"
    assert result["entry"]["body"] == "Race loser body update"
    assert result["entry"]["version"] == 3

    fresh_stores = build_decision_journal_stores(tmp_path)
    fresh_entry = get_entry(fresh_stores, "entry-race")
    assert fresh_entry["title"] == "Won the race"
    assert fresh_entry["body"] == "Race loser body update"
    assert fresh_entry["version"] == 3


def test_patch_entry_raises_after_exhausting_compare_and_set_retries(tmp_path) -> None:
    stores = build_decision_journal_stores(tmp_path)
    create_entry(
        stores,
        entry_id="entry-contended",
        title="Title",
        body="Body",
        actor_id="actor-a",
        created_at="2026-08-29T00:00:00Z",
    )
    stores.entries.compare_and_set = lambda expected_record, record: (False, expected_record)

    with pytest.raises(DecisionJournalConcurrencyError):
        patch_entry(
            stores,
            "entry-contended",
            patch={"title": "Never lands"},
            actor_id="actor-a",
            idempotency_key="idem-contended",
            request_hash="hash-contended",
            patched_at="2026-08-29T02:00:00Z",
        )


def test_postgres_backend_is_used_when_configured(tmp_path, monkeypatch) -> None:
    """No local-dict fallback: selecting the postgres backend must route
    every decision-journal write through the shared owner-store primitive,
    not a JSON file."""

    from services.governance import record_store as record_store_module

    class FakePostgresJsonOwnerStore:
        instances: list = []

        def __init__(self, *, dsn, table, owner_service, bootstrap):
            self.dsn = dsn
            self.table = table
            self.owner_service = owner_service
            self.bootstrap = bootstrap
            self.records: dict = {}
            type(self).instances.append(self)

        def put(self, record_id, payload):
            self.records[record_id] = payload

        def get(self, record_id):
            return self.records.get(record_id)

        def list_all(self):
            return list(self.records.values())

        def insert_if_absent(self, record_id, payload):
            if record_id in self.records:
                return False, self.records[record_id]
            self.records[record_id] = payload
            return True, payload

        def compare_and_set(self, record_id, expected_payload, payload):
            current = self.records.get(record_id)
            if current != expected_payload:
                return False, current
            self.records[record_id] = payload
            return True, payload

    monkeypatch.setenv("GOVERNANCE_STORE_BACKEND", "postgres")
    monkeypatch.setenv("GOVERNANCE_STORE_DSN", "postgresql://governance-owner/pantheon")
    monkeypatch.setattr(record_store_module, "PostgresJsonOwnerStore", FakePostgresJsonOwnerStore)

    stores = build_decision_journal_stores(tmp_path)
    created = create_entry(
        stores,
        entry_id="entry-pg",
        title="Postgres-backed entry",
        body="Body",
        actor_id="actor-a",
        created_at="2026-08-29T00:00:00Z",
    )
    assert created["persistenceMode"] == "governance_postgres_store"
    assert sorted(instance.table for instance in FakePostgresJsonOwnerStore.instances) == [
        "governance.decision_journal_audit",
        "governance.decision_journal_entries",
        "governance.decision_journal_idempotency",
    ]
    assert not tmp_path.joinpath("decision_journal_entries.json").exists()
