"""Ranking GET replay against the real immutable owner (isolated fake SQL).

The old dictionary test ports silently overwrote creation timestamps. Reuse
the owner suite's SQL fake so repeated reads and competing creators exercise
the same immutable compare-and-set implementation as hosted Postgres.
"""
from concurrent.futures import ThreadPoolExecutor
import sys
import threading

import pytest

from services.control_plane.bff.personas import service as personas
from services.control_plane.bff.ports.rankings import RankingSnapshotWriteOwnerPort
from services.rankings.store import RankingConflictError, RankingWriteStore
from services.rankings.test_store import _FakeConnection, _fake_psycopg, _snapshot


@pytest.fixture
def owner(monkeypatch):
    monkeypatch.setattr(_FakeConnection, "rows", {})
    monkeypatch.setattr(_FakeConnection, "statements", [])
    monkeypatch.setitem(sys.modules, "psycopg", _fake_psycopg())
    return RankingSnapshotWriteOwnerPort(RankingWriteStore(dsn="postgresql://isolated/test"))


@pytest.mark.parametrize("surface,period", [("rolling", "short-cycle"), ("quarterly", "2026-Q3")])
def test_ranking_read_at_later_time_reuses_durable_snapshot(owner, monkeypatch, surface, period):
    monkeypatch.setattr(personas, "_get_ranking_write_owner", lambda: owner)
    clock = iter(["2026-09-13T06:51:27Z", "2026-09-13T06:53:38Z"])
    monkeypatch.setattr(personas, "utc_now", lambda: next(clock))
    items = [{"persona_id": "paper-persona", "rank": 1, "score": 0.5,
              "evidence_refs": [{"refId": "same-evidence"}]}]
    first = personas._pm12_attach_ranking_snapshot(items, surface=surface, period=period)
    second = personas._pm12_attach_ranking_snapshot(items, surface=surface, period=period)
    assert second == first
    fresh = RankingWriteStore(dsn="postgresql://isolated/new-reader").get_ranking_snapshot(first[1])
    assert fresh is not None
    assert fresh.created_at == "2026-09-13T06:51:27Z"
    assert fresh.items[0]["persona_id"] == "paper-persona"
    assert not any(sql.lstrip().upper().startswith(("UPDATE", "DELETE")) for sql in _FakeConnection.statements)


@pytest.mark.parametrize("changed", [
    {"surface": "different"}, {"period": "different"},
    {"formula_version": "different"}, {"content_digest": "different"},
    {"items": [{"persona_id": "different", "rank": 2}]},
    {"evidence_assertion_digests": {"persona-a": ["different-evidence"]}},
])
def test_replay_never_adopts_different_snapshot_content(owner, changed):
    first = owner.put_ranking_snapshot(_snapshot().to_canonical_dict())
    with pytest.raises(RankingConflictError):
        owner.put_ranking_snapshot({**first, **changed, "created_at": "2026-09-13T07:00:00Z"})
    fresh = RankingWriteStore(dsn="postgresql://isolated/new-reader").get_ranking_snapshot(first["ranking_snapshot_id"])
    assert fresh.to_canonical_dict() == first


def test_competing_identical_creators_keep_one_original_timestamp(owner):
    writers = [owner, RankingSnapshotWriteOwnerPort(RankingWriteStore(dsn="postgresql://isolated/other"))]
    barrier = threading.Barrier(2)

    def create(index):
        payload = _snapshot(created_at=f"2026-09-13T06:53:0{index}Z").to_canonical_dict()
        barrier.wait(timeout=5)
        return writers[index].put_ranking_snapshot(payload)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, (0, 1)))
    assert results[0] == results[1]
    fresh = RankingWriteStore(dsn="postgresql://isolated/new-reader").get_ranking_snapshot("snap-001")
    assert fresh.to_canonical_dict() == results[0]


def test_competing_different_creators_still_reject_one(owner):
    writers = [owner, RankingSnapshotWriteOwnerPort(RankingWriteStore(dsn="postgresql://isolated/other"))]
    barrier = threading.Barrier(2)

    def create(index):
        payload = _snapshot(items=[{"persona_id": f"persona-{index}"}],
                            created_at=f"2026-09-13T06:53:0{index}Z").to_canonical_dict()
        barrier.wait(timeout=5)
        try:
            return writers[index].put_ranking_snapshot(payload)
        except RankingConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, (0, 1)))
    assert results.count("conflict") == 1
    winner = next(result for result in results if isinstance(result, dict))
    fresh = RankingWriteStore(dsn="postgresql://isolated/new-reader").get_ranking_snapshot("snap-001")
    assert fresh.to_canonical_dict() == winner
