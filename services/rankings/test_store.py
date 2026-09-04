"""Fresh-instance tests for the generation-3 immutable Rankings records.

Scope: ``RankingSnapshotRecord`` and ``AllocationEvaluationRecord`` on
``services/rankings/store.py``. Uses the same fake-psycopg-connection
pattern as ``tests/ranking_write_owner/test_rankings_write_owner.py`` (and
``services/foundation/tests/test_control_plane_postgres_owner_stores.py``):
every assertion reads through a *new* store instance pointed at the same
fake table, so nothing here can pass off an in-process cache as durability.

The legacy ``RankingRecord`` CRUD surface has its own suite at
``tests/ranking_write_owner/test_rankings_write_owner.py`` and is
intentionally left untouched by this file.
"""
from __future__ import annotations

import json
import re
import sys
import threading
from collections.abc import Mapping
from types import MappingProxyType, SimpleNamespace
from unittest import mock

import pytest

from services.rankings.store import (
    AllocationEvaluationRecord,
    RankingConflictError,
    RankingRecord,
    RankingSnapshotRecord,
    RankingWriteOwnerError,
    RankingWriteStore,
)


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeConnection:
    rows: dict[str, dict[str, dict]] = {}
    statements: list[str] = []
    _table_lock = threading.Lock()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, sql, params=()):
        self.statements.append(sql)
        normalized = " ".join(sql.split()).upper()
        table = self._table_name(sql)
        if normalized.startswith("CREATE"):
            return _FakeCursor([])
        if normalized.startswith("LOCK TABLE"):
            return _FakeCursor([])
        if normalized.startswith("INSERT INTO"):
            payload = json.loads(params[1]) if isinstance(params[1], str) else params[1]
            record_id = str(params[0])
            # Serialize the read-decide-write sequence the same way a real
            # Postgres unique-constraint insert would, so two threads racing
            # on the same record_id deterministically produce exactly one
            # winner instead of a lost update from interleaved dict writes.
            with self._table_lock:
                table_rows = self.rows.setdefault(table, {})
                if "DO NOTHING" in normalized and record_id in table_rows:
                    return _FakeCursor([])
                table_rows[record_id] = payload
                return _FakeCursor([(payload,)] if "RETURNING PAYLOAD" in normalized else [])
        if normalized.startswith("UPDATE"):
            payload = json.loads(params[0]) if isinstance(params[0], str) else params[0]
            record_id = str(params[1])
            expected = json.loads(params[2]) if isinstance(params[2], str) else params[2]
            with self._table_lock:
                table_rows = self.rows.setdefault(table, {})
                if table_rows.get(record_id) != expected:
                    return _FakeCursor([])
                table_rows[record_id] = payload
                return _FakeCursor([(payload,)])
        if normalized.startswith("DELETE FROM"):
            record_id = str(params[0])
            expected = json.loads(params[1]) if isinstance(params[1], str) else params[1]
            with self._table_lock:
                table_rows = self.rows.setdefault(table, {})
                if table_rows.get(record_id) != expected:
                    return _FakeCursor([])
                removed = table_rows.pop(record_id)
                return _FakeCursor([(removed,)])
        if normalized.startswith("SELECT PAYLOAD") and "WHERE RECORD_ID" in normalized:
            record = self.rows.get(table, {}).get(str(params[0]))
            if "AND PAYLOAD" in normalized and record is not None:
                expected = json.loads(params[1]) if isinstance(params[1], str) else params[1]
                if record != expected:
                    record = None
            return _FakeCursor([(record,)] if record is not None else [])
        if normalized.startswith("SELECT PAYLOAD"):
            return _FakeCursor([(payload,) for payload in self.rows.get(table, {}).values()])
        return _FakeCursor([])

    def rollback(self):
        pass

    @staticmethod
    def _table_name(sql: str) -> str:
        match = re.search(
            r"(?:FROM|INTO|UPDATE)\s+((?:\"[^\"]+\"\.)?\"[^\"]+\")",
            sql,
            re.IGNORECASE,
        )
        return match.group(1) if match else "<unknown>"


def _fake_psycopg():
    conn = _FakeConnection()
    return SimpleNamespace(connect=lambda dsn: conn)


@pytest.fixture(autouse=True)
def _reset_fake_connection():
    _FakeConnection.rows = {}
    _FakeConnection.statements = []


@pytest.fixture
def fake_psycopg_module():
    fake_psycopg = _fake_psycopg()
    with mock.patch.dict(sys.modules, {"psycopg": fake_psycopg}):
        yield fake_psycopg


@pytest.fixture
def store(fake_psycopg_module):
    return RankingWriteStore(dsn="postgresql://owner@example/db")


def _snapshot(ranking_snapshot_id: str = "snap-001", **overrides) -> RankingSnapshotRecord:
    payload = {
        "ranking_snapshot_id": ranking_snapshot_id,
        "surface": "persona_league",
        "period": "2026-W36",
        "formula_version": "v3",
        "content_digest": "sha256:snap-digest-1",
        "items": [{"persona_id": "persona-a", "rank": 1, "score": 1.42}],
        "evidence_assertion_digests": {
            "persona-a": ["sha256:evidence-1", "sha256:evidence-2"],
        },
        "created_at": "2026-09-04T18:00:00Z",
    }
    payload.update(overrides)
    return RankingSnapshotRecord(**payload)


class _CustomMapping(Mapping):
    """A ``collections.abc.Mapping`` implementation that is not a ``dict``.

    Used to prove ``_deep_freeze`` copies any mapping (not just ``dict``)
    field-by-field into immutable state, rather than only recognizing the
    concrete ``dict`` type.
    """

    def __init__(self, data):
        self._data = dict(data)

    def __getitem__(self, key):
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)


def _evaluation(allocation_evaluation_id: str = "eval-001", **overrides) -> AllocationEvaluationRecord:
    payload = {
        "allocation_evaluation_id": allocation_evaluation_id,
        "ranking_snapshot_id": "snap-001",
        "allocation_policy_version": "policy-v2",
        "content_digest": "sha256:eval-digest-1",
        "lines": [{"persona_id": "persona-a", "weight": 0.6}],
        "created_at": "2026-09-04T18:05:00Z",
        "applied": False,
    }
    payload.update(overrides)
    return AllocationEvaluationRecord(**payload)


# --------------------------------------------------------------------------- #
# RankingSnapshotRecord
# --------------------------------------------------------------------------- #


def test_create_ranking_snapshot_then_fresh_read_round_trips_every_field(store) -> None:
    store.create_ranking_snapshot(_snapshot())

    reader = RankingWriteStore(dsn="postgresql://reader@example/db")
    fresh = reader.get_ranking_snapshot("snap-001")

    assert fresh is not None
    assert fresh.ranking_snapshot_id == "snap-001"
    assert fresh.surface == "persona_league"
    assert fresh.period == "2026-W36"
    assert fresh.formula_version == "v3"
    assert fresh.content_digest == "sha256:snap-digest-1"
    assert fresh.items == ({"persona_id": "persona-a", "rank": 1, "score": 1.42},)
    assert fresh.evidence_assertion_digests == {"persona-a": ("sha256:evidence-1", "sha256:evidence-2")}
    assert fresh.created_at == "2026-09-04T18:00:00Z"


def test_ranking_snapshot_deep_immutability(store) -> None:
    created = store.create_ranking_snapshot(_snapshot())

    assert isinstance(created.items, tuple)
    assert isinstance(created.items[0], MappingProxyType)
    with pytest.raises(TypeError):
        created.items[0]["rank"] = 99
    with pytest.raises(AttributeError):
        created.items.append({"persona_id": "persona-b"})  # tuples have no append
    with pytest.raises(AttributeError):
        created.ranking_snapshot_id = "mutated"
    assert isinstance(created.evidence_assertion_digests, MappingProxyType)
    with pytest.raises(TypeError):
        created.evidence_assertion_digests["persona-a"] = ["mutated"]
    with pytest.raises(AttributeError):
        created.evidence_assertion_digests["persona-a"].append("mutated")


def test_ranking_snapshot_same_id_identical_payload_is_idempotent_replay(store) -> None:
    first = store.create_ranking_snapshot(_snapshot())
    second = store.create_ranking_snapshot(_snapshot())

    assert second.ranking_snapshot_id == first.ranking_snapshot_id
    assert second.items == first.items


def test_ranking_snapshot_same_id_same_digest_different_items_is_conflict(store) -> None:
    store.create_ranking_snapshot(_snapshot())

    with pytest.raises(RankingConflictError):
        store.create_ranking_snapshot(
            _snapshot(items=[{"persona_id": "persona-b", "rank": 1, "score": 9.99}])
        )


def test_ranking_snapshot_concurrent_divergent_creators_one_creates_one_conflicts(store) -> None:
    """A genuine two-thread race on the same id: exactly one create, one conflict.

    Both threads target distinct ``RankingWriteStore`` instances (so the
    store's own in-process lock cannot serialize them) pointed at the same
    fake table, and a barrier holds both until they are ready to call
    ``create_ranking_snapshot`` at effectively the same instant. The table's
    own compare-and-set semantics (see ``_FakeConnection.execute`` INSERT
    branch, which mirrors Postgres's ``ON CONFLICT ... DO NOTHING``) decide
    the winner, not test-side sequencing.
    """

    writer_a = store
    writer_b = RankingWriteStore(dsn="postgresql://writer-b@example/db")
    barrier = threading.Barrier(2)
    results: dict[str, object] = {}

    def _attempt(name: str, writer: RankingWriteStore, **overrides) -> None:
        record = _snapshot(**overrides)
        barrier.wait(timeout=5)
        try:
            results[name] = writer.create_ranking_snapshot(record)
        except RankingConflictError as exc:
            results[name] = exc

    thread_a = threading.Thread(target=_attempt, args=("a", writer_a))
    thread_b = threading.Thread(
        target=_attempt,
        args=("b", writer_b),
        kwargs={"content_digest": "sha256:snap-digest-1", "surface": "other"},
    )
    thread_a.start()
    thread_b.start()
    thread_a.join(timeout=5)
    thread_b.join(timeout=5)

    outcomes = [results["a"], results["b"]]
    created = [outcome for outcome in outcomes if isinstance(outcome, RankingSnapshotRecord)]
    conflicted = [outcome for outcome in outcomes if isinstance(outcome, RankingConflictError)]
    assert len(created) == 1
    assert len(conflicted) == 1

    reader = RankingWriteStore(dsn="postgresql://reader@example/db")
    durable = reader.get_ranking_snapshot("snap-001")
    assert durable is not None
    assert durable.items == created[0].items


def test_ranking_snapshot_create_rejects_plain_mapping(store) -> None:
    with pytest.raises(RankingWriteOwnerError):
        store.create_ranking_snapshot({"ranking_snapshot_id": "snap-002"})  # type: ignore[arg-type]


def test_ranking_snapshot_rejects_non_json_compatible_items(store) -> None:
    with pytest.raises(RankingWriteOwnerError):
        store.create_ranking_snapshot(_snapshot(items=[{"persona_id": "persona-a", "tags": {1, 2, 3}}]))


def test_ranking_snapshot_rejects_flat_list_evidence_assertion_digests(store) -> None:
    with pytest.raises(RankingWriteOwnerError):
        store.create_ranking_snapshot(
            _snapshot(evidence_assertion_digests=["sha256:evidence-1", "sha256:evidence-2"])
        )


def test_ranking_snapshot_rejects_non_string_digest_entries(store) -> None:
    with pytest.raises(RankingWriteOwnerError):
        store.create_ranking_snapshot(_snapshot(evidence_assertion_digests={"persona-a": [1, 2]}))


def test_ranking_snapshot_has_no_update_or_delete() -> None:
    for verb in ("update_ranking_snapshot", "delete_ranking_snapshot", "put_ranking_snapshot"):
        assert not hasattr(RankingWriteStore, verb)


# --------------------------------------------------------------------------- #
# AllocationEvaluationRecord
# --------------------------------------------------------------------------- #


def test_create_allocation_evaluation_then_fresh_read_round_trips_every_field(store) -> None:
    store.create_allocation_evaluation(_evaluation())

    reader = RankingWriteStore(dsn="postgresql://reader@example/db")
    fresh = reader.get_allocation_evaluation("eval-001")

    assert fresh is not None
    assert fresh.allocation_evaluation_id == "eval-001"
    assert fresh.ranking_snapshot_id == "snap-001"
    assert fresh.allocation_policy_version == "policy-v2"
    assert fresh.content_digest == "sha256:eval-digest-1"
    assert fresh.lines == ({"persona_id": "persona-a", "weight": 0.6},)
    assert fresh.created_at == "2026-09-04T18:05:00Z"
    assert fresh.applied is False
    assert fresh.authority_mode is None
    assert fresh.promotion_review_id is None


def test_allocation_evaluation_optional_fields_omitted_when_absent_from_durable_payload(store) -> None:
    created = store.create_allocation_evaluation(_evaluation())
    record_id = "allocation-evaluation::eval-001"
    durable = store._records_table.get(record_id)

    assert "authority_mode" not in durable
    assert "promotion_review_id" not in durable
    assert created.authority_mode is None
    assert created.promotion_review_id is None


def test_allocation_evaluation_optional_fields_round_trip(store) -> None:
    store.create_allocation_evaluation(
        _evaluation(authority_mode="advisory", promotion_review_id="review-77")
    )

    reader = RankingWriteStore(dsn="postgresql://reader@example/db")
    fresh = reader.get_allocation_evaluation("eval-001")

    assert fresh.authority_mode == "advisory"
    assert fresh.promotion_review_id == "review-77"


def test_allocation_evaluation_decodes_each_allowed_optional_subset(store) -> None:
    only_authority = _evaluation(allocation_evaluation_id="eval-authority", authority_mode="advisory")
    only_review = _evaluation(allocation_evaluation_id="eval-review", promotion_review_id="review-1")
    both = _evaluation(
        allocation_evaluation_id="eval-both", authority_mode="binding", promotion_review_id="review-2"
    )
    neither = _evaluation(allocation_evaluation_id="eval-neither")

    store.create_allocation_evaluation(only_authority)
    store.create_allocation_evaluation(only_review)
    store.create_allocation_evaluation(both)
    store.create_allocation_evaluation(neither)

    reader = RankingWriteStore(dsn="postgresql://reader@example/db")
    fresh_authority = reader.get_allocation_evaluation("eval-authority")
    fresh_review = reader.get_allocation_evaluation("eval-review")
    fresh_both = reader.get_allocation_evaluation("eval-both")
    fresh_neither = reader.get_allocation_evaluation("eval-neither")

    assert (fresh_authority.authority_mode, fresh_authority.promotion_review_id) == ("advisory", None)
    assert (fresh_review.authority_mode, fresh_review.promotion_review_id) == (None, "review-1")
    assert (fresh_both.authority_mode, fresh_both.promotion_review_id) == ("binding", "review-2")
    assert (fresh_neither.authority_mode, fresh_neither.promotion_review_id) == (None, None)


def test_allocation_evaluation_deep_immutability(store) -> None:
    created = store.create_allocation_evaluation(_evaluation())

    assert isinstance(created.lines, tuple)
    assert isinstance(created.lines[0], MappingProxyType)
    with pytest.raises(TypeError):
        created.lines[0]["weight"] = 1.0
    with pytest.raises(AttributeError):
        created.applied = True


def test_allocation_evaluation_same_id_identical_payload_is_idempotent_replay(store) -> None:
    first = store.create_allocation_evaluation(_evaluation())
    second = store.create_allocation_evaluation(_evaluation())

    assert second.allocation_evaluation_id == first.allocation_evaluation_id
    assert second.lines == first.lines


def test_allocation_evaluation_same_digest_different_lines_is_conflict(store) -> None:
    store.create_allocation_evaluation(_evaluation())

    with pytest.raises(RankingConflictError):
        store.create_allocation_evaluation(
            _evaluation(lines=[{"persona_id": "persona-z", "weight": 0.1}])
        )


def test_allocation_evaluation_rejects_non_json_compatible_lines(store) -> None:
    with pytest.raises(RankingWriteOwnerError):
        store.create_allocation_evaluation(_evaluation(lines=[{"persona_id": "persona-a", "tags": {1, 2}}]))


def test_allocation_evaluation_has_no_update_or_delete() -> None:
    for verb in (
        "update_allocation_evaluation",
        "delete_allocation_evaluation",
        "put_allocation_evaluation",
    ):
        assert not hasattr(RankingWriteStore, verb)


# --------------------------------------------------------------------------- #
# Namespacing, explicit envelope, and legacy CRUD isolation
# --------------------------------------------------------------------------- #


def test_snapshot_and_evaluation_ids_are_namespaced_against_legacy_and_each_other(store) -> None:
    # A snapshot and an evaluation sharing the same raw id as a legacy ranking
    # must not collide: each kind is stored under its own record-id prefix.
    shared_raw_id = "shared-001"
    store.create_ranking(
        RankingRecord(ranking_id=shared_raw_id, title="Legacy Ranking", criteria="sharpe_30d")
    )
    store.create_ranking_snapshot(_snapshot(ranking_snapshot_id=shared_raw_id))
    store.create_allocation_evaluation(
        _evaluation(allocation_evaluation_id=shared_raw_id, ranking_snapshot_id=shared_raw_id)
    )

    legacy = store.get_ranking(shared_raw_id)
    snapshot = store.get_ranking_snapshot(shared_raw_id)
    evaluation = store.get_allocation_evaluation(shared_raw_id)

    assert legacy is not None and legacy.title == "Legacy Ranking"
    assert snapshot is not None and snapshot.ranking_snapshot_id == shared_raw_id
    assert evaluation is not None and evaluation.allocation_evaluation_id == shared_raw_id


def test_legacy_list_rankings_ignores_snapshot_and_evaluation_rows(store) -> None:
    store.create_ranking(
        RankingRecord(ranking_id="legacy-only", title="Legacy Ranking", criteria="sharpe_30d")
    )
    store.create_ranking_snapshot(_snapshot(ranking_snapshot_id="snap-in-same-table"))
    store.create_allocation_evaluation(_evaluation(allocation_evaluation_id="eval-in-same-table"))

    reader = RankingWriteStore(dsn="postgresql://reader@example/db")
    listed = reader.list_rankings()

    assert [r.ranking_id for r in listed] == ["legacy-only"]


def test_durable_snapshot_and_evaluation_rows_carry_explicit_record_type(store) -> None:
    store.create_ranking_snapshot(_snapshot(ranking_snapshot_id="snap-envelope"))
    store.create_allocation_evaluation(_evaluation(allocation_evaluation_id="eval-envelope"))

    snapshot_row = store._records_table.get("ranking-snapshot::snap-envelope")
    evaluation_row = store._records_table.get("allocation-evaluation::eval-envelope")

    assert snapshot_row["record_type"] == "ranking_snapshot"
    assert evaluation_row["record_type"] == "allocation_evaluation"


def test_legacy_list_rankings_raises_on_unrecognized_row_missing_ranking_id_and_record_type(store) -> None:
    # A row with neither ranking_id nor a recognized record_type is a data
    # integrity problem, not a payload the legacy surface may silently skip.
    store._records_table.put("mystery-row", {"some_other_field": "x"})

    with pytest.raises(RankingWriteOwnerError):
        store.list_rankings()


# --------------------------------------------------------------------------- #
# Correction wave: collections.abc.Mapping, mapping-collection shape,
# non-finite floats, decode-time validation, and record_type-first legacy
# masquerade detection.
# --------------------------------------------------------------------------- #


def test_ranking_snapshot_copies_a_custom_mapping_item_into_immutable_state(store) -> None:
    source_item = _CustomMapping({"persona_id": "persona-a", "rank": 1, "score": 1.0})
    created = store.create_ranking_snapshot(_snapshot(items=[source_item]))

    assert isinstance(created.items, tuple)
    assert isinstance(created.items[0], MappingProxyType)
    assert dict(created.items[0]) == {"persona_id": "persona-a", "rank": 1, "score": 1.0}

    # Mutating the original custom mapping after the fact must not reach the
    # durable/frozen copy: proves the mapping was copied, not referenced.
    source_item._data["rank"] = 99
    assert created.items[0]["rank"] == 1


def test_ranking_snapshot_copies_a_custom_mapping_evidence_digest_value_into_immutable_state(store) -> None:
    created = store.create_ranking_snapshot(
        _snapshot(evidence_assertion_digests=_CustomMapping({"persona-a": ["sha256:e1"]}))
    )

    assert isinstance(created.evidence_assertion_digests, MappingProxyType)
    assert dict(created.evidence_assertion_digests) == {"persona-a": ("sha256:e1",)}


def test_ranking_snapshot_rejects_scalar_items(store) -> None:
    with pytest.raises(RankingWriteOwnerError):
        store.create_ranking_snapshot(_snapshot(items="not-a-list"))


def test_ranking_snapshot_rejects_scalar_entry_inside_items(store) -> None:
    with pytest.raises(RankingWriteOwnerError):
        store.create_ranking_snapshot(_snapshot(items=["not-a-mapping"]))


def test_ranking_snapshot_rejects_non_finite_float_in_items(store) -> None:
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(RankingWriteOwnerError):
            store.create_ranking_snapshot(
                _snapshot(items=[{"persona_id": "persona-a", "rank": 1, "score": bad}])
            )


def test_allocation_evaluation_rejects_scalar_lines(store) -> None:
    with pytest.raises(RankingWriteOwnerError):
        store.create_allocation_evaluation(_evaluation(lines="not-a-list"))


def test_allocation_evaluation_rejects_scalar_entry_inside_lines(store) -> None:
    with pytest.raises(RankingWriteOwnerError):
        store.create_allocation_evaluation(_evaluation(lines=[123]))


def test_allocation_evaluation_rejects_non_finite_float_in_lines(store) -> None:
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(RankingWriteOwnerError):
            store.create_allocation_evaluation(
                _evaluation(lines=[{"persona_id": "persona-a", "weight": bad}])
            )


def test_get_ranking_snapshot_raises_on_corrupted_persisted_items_shape(store) -> None:
    # Bypass normal create validation entirely: write a corrupted row
    # straight to the backing table, the way an out-of-band writer or a
    # pre-correction row would. The decoder must still fail closed on read.
    store._records_table.put(
        "ranking-snapshot::corrupt-items",
        {
            "record_type": "ranking_snapshot",
            "ranking_snapshot_id": "corrupt-items",
            "surface": "persona_league",
            "period": "2026-W36",
            "formula_version": "v3",
            "content_digest": "sha256:corrupt",
            "items": "not-a-list",
            "evidence_assertion_digests": {"persona-a": ["sha256:e1"]},
            "created_at": "2026-09-04T18:00:00Z",
        },
    )

    with pytest.raises(RankingWriteOwnerError):
        store.get_ranking_snapshot("corrupt-items")


def test_get_ranking_snapshot_raises_on_corrupted_persisted_field_type(store) -> None:
    store._records_table.put(
        "ranking-snapshot::corrupt-field",
        {
            "record_type": "ranking_snapshot",
            "ranking_snapshot_id": "corrupt-field",
            "surface": "",
            "period": "2026-W36",
            "formula_version": "v3",
            "content_digest": "sha256:corrupt",
            "items": [{"persona_id": "persona-a", "rank": 1, "score": 1.0}],
            "evidence_assertion_digests": {"persona-a": ["sha256:e1"]},
            "created_at": "2026-09-04T18:00:00Z",
        },
    )

    with pytest.raises(RankingWriteOwnerError):
        store.get_ranking_snapshot("corrupt-field")


def test_get_allocation_evaluation_raises_on_corrupted_persisted_lines_shape(store) -> None:
    store._records_table.put(
        "allocation-evaluation::corrupt-lines",
        {
            "record_type": "allocation_evaluation",
            "allocation_evaluation_id": "corrupt-lines",
            "ranking_snapshot_id": "snap-001",
            "allocation_policy_version": "policy-v2",
            "content_digest": "sha256:corrupt",
            "lines": ["not-a-mapping"],
            "created_at": "2026-09-04T18:05:00Z",
            "applied": False,
        },
    )

    with pytest.raises(RankingWriteOwnerError):
        store.get_allocation_evaluation("corrupt-lines")


def test_legacy_list_rankings_raises_on_mixed_envelope_masquerading_as_legacy(store) -> None:
    # A row carrying both a recognized record_type and a ranking_id is a
    # mixed envelope, not a legacy row that happens to have an extra field:
    # record_type must be inspected before ranking_id, not after.
    store._records_table.put(
        "ranking-snapshot::masquerade",
        {
            "record_type": "ranking_snapshot",
            "ranking_id": "masquerade",
            "ranking_snapshot_id": "masquerade",
            "surface": "persona_league",
            "period": "2026-W36",
            "formula_version": "v3",
            "content_digest": "sha256:masquerade",
            "items": [{"persona_id": "persona-a", "rank": 1, "score": 1.0}],
            "evidence_assertion_digests": {"persona-a": ["sha256:e1"]},
            "created_at": "2026-09-04T18:00:00Z",
        },
    )

    with pytest.raises(RankingWriteOwnerError):
        store.list_rankings()


def test_legacy_list_rankings_raises_on_unrecognized_record_type(store) -> None:
    store._records_table.put(
        "future-kind::unknown-001",
        {"record_type": "some_future_kind", "payload": "opaque"},
    )

    with pytest.raises(RankingWriteOwnerError):
        store.list_rankings()


def test_legacy_list_rankings_raises_on_null_record_type_with_ranking_id(store) -> None:
    # Key presence of record_type must be checked, not just a truthy/non-None value:
    # an envelope where the record_type key exists with null alongside ranking_id
    # must fail integrity as a mixed envelope and never decode as legacy.
    store._records_table.put(
        "ranking-null-record-type-mixed",
        {
            "record_type": None,
            "ranking_id": "null-record-type-spoof",
            "title": "Spoofed Legacy",
            "criteria": "sharpe_30d",
        },
    )

    with pytest.raises(RankingWriteOwnerError):
        store.list_rankings()

    with pytest.raises(RankingWriteOwnerError):
        store.get_ranking("null-record-type-spoof")


def test_legacy_list_rankings_raises_on_null_record_type_without_ranking_id(store) -> None:
    # An envelope carrying record_type: None without ranking_id is an
    # unrecognized record_type, not an ignorable non-legacy kind.
    store._records_table.put(
        "null-record-type-row",
        {"record_type": None, "payload": "opaque"},
    )

    with pytest.raises(RankingWriteOwnerError):
        store.list_rankings()

