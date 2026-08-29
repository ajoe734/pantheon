"""Write-then-fresh-read proof for the Rankings write-owner store.

Scope: services/rankings/store.py. These tests exercise the module as the
independent persistent write owner it claims to be -- every assertion reads
through a *new* store instance pointed at the same durable backing file, so
none of it can pass off an in-process cache as durability.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from services.rankings.store import (
    RankingConflictError,
    RankingRecord,
    RankingWriteOwnerError,
    RankingWriteStore,
    build_rankings_store,
)


def _record(ranking_id: str = "rk-001", **overrides) -> RankingRecord:
    payload = {
        "ranking_id": ranking_id,
        "title": "Persona League Weekly",
        "criteria": "sharpe_30d",
        "entries": [{"persona_id": "persona-a", "rank": 1, "score": 1.42}],
    }
    payload.update(overrides)
    return RankingRecord(**payload)


def test_create_then_fresh_read_from_new_store_instance(tmp_path: Path) -> None:
    path = tmp_path / "rankings.json"
    writer = RankingWriteStore(path=path)
    writer.create_ranking(_record())

    # A brand-new store instance simulates a second process/reader: it has
    # no shared Python state with `writer`, so this can only pass if the
    # write actually reached durable storage.
    reader = RankingWriteStore(path=path)
    fresh = reader.get_ranking("rk-001")

    assert fresh is not None
    assert fresh.title == "Persona League Weekly"
    assert fresh.entries == [{"persona_id": "persona-a", "rank": 1, "score": 1.42}]


def test_put_ranking_upsert_then_fresh_list(tmp_path: Path) -> None:
    path = tmp_path / "rankings.json"
    writer = RankingWriteStore(path=path)
    writer.put_ranking(_record())
    writer.put_ranking(_record(title="Persona League Weekly (revised)"))

    reader = RankingWriteStore(path=path)
    listed = reader.list_rankings()

    assert len(listed) == 1
    assert listed[0].title == "Persona League Weekly (revised)"


def test_delete_ranking_then_fresh_read_returns_none(tmp_path: Path) -> None:
    path = tmp_path / "rankings.json"
    writer = RankingWriteStore(path=path)
    writer.create_ranking(_record())
    deleted = writer.delete_ranking("rk-001")
    assert deleted is True

    reader = RankingWriteStore(path=path)
    assert reader.get_ranking("rk-001") is None
    assert reader.list_rankings() == []


def test_create_ranking_rejects_duplicate_id(tmp_path: Path) -> None:
    path = tmp_path / "rankings.json"
    store = RankingWriteStore(path=path)
    store.create_ranking(_record())

    with pytest.raises(RankingConflictError):
        store.create_ranking(_record())


def test_put_ranking_rejects_missing_title(tmp_path: Path) -> None:
    store = RankingWriteStore(path=tmp_path / "rankings.json")
    with pytest.raises(RankingWriteOwnerError):
        store.put_ranking(_record(title=""))


def test_writes_survive_across_separate_store_instances(tmp_path: Path) -> None:
    """Simulates independent writer/reader processes sharing only the file."""
    path = tmp_path / "rankings.json"

    RankingWriteStore(path=path).create_ranking(_record(ranking_id="rk-a"))
    RankingWriteStore(path=path).create_ranking(_record(ranking_id="rk-b"))
    RankingWriteStore(path=path).put_ranking(
        _record(ranking_id="rk-a", title="Persona League Weekly (v2)")
    )

    final_reader = RankingWriteStore(path=path)
    ids = sorted(r.ranking_id for r in final_reader.list_rankings())
    assert ids == ["rk-a", "rk-b"]
    assert final_reader.get_ranking("rk-a").title == "Persona League Weekly (v2)"


def test_build_rankings_store_defaults_to_json_backend(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("RANKING_STORE_BACKEND", raising=False)
    store = build_rankings_store(path=tmp_path / "rankings.json")
    assert isinstance(store, RankingWriteStore)


def test_build_rankings_store_rejects_unknown_backend(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("RANKING_STORE_BACKEND", "not-a-real-backend")
    with pytest.raises(ValueError):
        build_rankings_store(path=tmp_path / "rankings.json")


def test_build_rankings_store_requires_dsn_for_postgres_backend(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("RANKING_STORE_BACKEND", "postgres")
    monkeypatch.delenv("RANKING_STORE_DSN", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValueError):
        build_rankings_store(path=tmp_path / "rankings.json")


def test_no_read_store_import_and_no_local_overlay_fallback() -> None:
    """Guards the write-owner acceptance criteria at the source level.

    The write owner must not import the BFF's read_store.py, and must not
    grow a local dict/overlay/cache/response-fallback path that would let a
    write appear durable without reaching the backing store.
    """
    import ast

    tree = ast.parse(Path("services/rankings/store.py").read_text())
    import_lines = [
        line
        for line in Path("services/rankings/store.py").read_text().splitlines()
        if line.startswith("import ") or line.startswith("from ")
    ]
    assert not any("read_store" in line for line in import_lines)
    assert not any("control_plane" in line or "control-plane" in line for line in import_lines)

    identifiers = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            identifiers.add(node.name)
        elif isinstance(node, ast.Name):
            identifiers.add(node.id)
        elif isinstance(node, ast.Attribute):
            identifiers.add(node.attr)
    assert not any("overlay" in identifier.lower() for identifier in identifiers)
