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
        if normalized.startswith("INSERT INTO"):
            payload = json.loads(params[1]) if isinstance(params[1], str) else params[1]
            table_rows = self.rows.setdefault(table, {})
            record_id = str(params[0])
            if "DO NOTHING" in normalized and record_id in table_rows:
                return _FakeCursor([])
            table_rows[record_id] = payload
            return _FakeCursor([(payload,)] if "RETURNING PAYLOAD" in normalized else [])
        if normalized.startswith("UPDATE"):
            payload = json.loads(params[0]) if isinstance(params[0], str) else params[0]
            record_id = str(params[1])
            expected = json.loads(params[2]) if isinstance(params[2], str) else params[2]
            table_rows = self.rows.setdefault(table, {})
            if table_rows.get(record_id) != expected:
                return _FakeCursor([])
            table_rows[record_id] = payload
            return _FakeCursor([(payload,)])
        if normalized.startswith("DELETE FROM"):
            record_id = str(params[0])
            expected = json.loads(params[1]) if isinstance(params[1], str) else params[1]
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
        "evidence_assertion_digests": ["sha256:evidence-1", "sha256:evidence-2"],
        "created_at": "2026-09-04T18:00:00Z",
    }
    payload.update(overrides)
    return RankingSnapshotRecord(**payload)


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
    assert fresh.evidence_assertion_digests == ("sha256:evidence-1", "sha256:evidence-2")
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


def test_ranking_snapshot_competing_writers_one_creates_one_replays(store) -> None:
    winner = store.create_ranking_snapshot(_snapshot())

    other_writer = RankingWriteStore(dsn="postgresql://writer-b@example/db")
    replay = other_writer.create_ranking_snapshot(_snapshot())

    assert replay.ranking_snapshot_id == winner.ranking_snapshot_id
    assert replay.items == winner.items

    with pytest.raises(RankingConflictError):
        other_writer.create_ranking_snapshot(_snapshot(content_digest="sha256:snap-digest-1", surface="other"))


def test_ranking_snapshot_create_rejects_plain_mapping(store) -> None:
    with pytest.raises(RankingWriteOwnerError):
        store.create_ranking_snapshot({"ranking_snapshot_id": "snap-002"})  # type: ignore[arg-type]


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


def test_allocation_evaluation_optional_fields_round_trip(store) -> None:
    store.create_allocation_evaluation(
        _evaluation(authority_mode="advisory", promotion_review_id="review-77")
    )

    reader = RankingWriteStore(dsn="postgresql://reader@example/db")
    fresh = reader.get_allocation_evaluation("eval-001")

    assert fresh.authority_mode == "advisory"
    assert fresh.promotion_review_id == "review-77"


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


def test_allocation_evaluation_has_no_update_or_delete() -> None:
    for verb in (
        "update_allocation_evaluation",
        "delete_allocation_evaluation",
        "put_allocation_evaluation",
    ):
        assert not hasattr(RankingWriteStore, verb)


# --------------------------------------------------------------------------- #
# Namespacing against the legacy RankingRecord id space
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
