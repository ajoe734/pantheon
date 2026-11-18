"""Steady-tick overhead: JsonlConfiguredConnectorStore incremental journal reads.

Every result must equal what a full replay (a fresh store on the same journal) returns.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

import pytest

from services.source_ingestion import configured
from services.source_ingestion.configured import JsonlConfiguredConnectorStore
from services.source_ingestion.connectors import SourceConnector, SourceEvidenceError


def _connector(connector_id: str) -> SourceConnector:
    return SourceConnector(
        connector_id=connector_id, source_type="internal_note",
        provider="Pantheon steady tick test", license_scope="internal",
    )


def _fetch(tag: str) -> dict:
    return {
        "mode": "static_records", "next_watermark": tag,
        "records": [{"source_id": f"src-{tag}", "title": tag, "content_ref": f"memory://steady/{tag}"}],
    }


def _view(store: JsonlConfiguredConnectorStore):
    configs, states = store.read_snapshot()
    return (
        [c.to_dict() for c in store.list_configs()],
        [c.to_dict() for c in configs],
        states,
        {c.connector.connector_id: store.get_config(c.connector.connector_id).to_dict() for c in configs},
        {c.connector.connector_id: store.get_fetch_state(c.connector.connector_id) for c in configs},
    )


def _assert_equals_full_replay(store: JsonlConfiguredConnectorStore, path: Path) -> None:
    assert _view(store) == _view(JsonlConfiguredConnectorStore(path))


def test_incremental_reads_equal_full_replay_across_own_and_foreign_appends(tmp_path: Path) -> None:
    path = tmp_path / "connector_config.jsonl"
    store = JsonlConfiguredConnectorStore(path)
    other = JsonlConfiguredConnectorStore(path)  # stands for another process
    for i in range(5):
        store.upsert_config(_connector(f"c{i}"), _fetch(f"t{i}"))
    _assert_equals_full_replay(store, path)
    for n in range(4):
        store.record_fetch_attempt("c1", success=n % 2 == 0, error="boom", collector_outcome={"n": n})
    _assert_equals_full_replay(store, path)
    other.upsert_config(_connector("c9"), _fetch("t9"))
    other.record_fetch_attempt("c2", success=True)
    assert store.get_config("c9") is not None
    assert store.get_fetch_state("c2")["attempts"] == 1
    _assert_equals_full_replay(store, path)
    _assert_equals_full_replay(other, path)


def test_unchanged_journal_is_not_read_again_and_appends_read_only_the_tail(tmp_path: Path) -> None:
    path = tmp_path / "connector_config.jsonl"
    store = JsonlConfiguredConnectorStore(path)
    for i in range(20):
        store.upsert_config(_connector(f"c{i}"), _fetch(f"t{i}"))
    real_loads = json.loads
    with mock.patch.object(configured.json, "loads", side_effect=real_loads) as loads:
        store.list_configs()  # may re-read this process's own last append once
        loads.reset_mock()
        store.list_configs()
        store.read_snapshot()
        store.get_fetch_state("c1")
        assert loads.call_count == 0
        JsonlConfiguredConnectorStore(path).record_fetch_attempt("c3", success=True)
        loads.reset_mock()
        store.read_snapshot()
        assert loads.call_count == 1  # only the appended line


def test_replaced_truncated_and_rewritten_journals_fall_back_to_full_replay(tmp_path: Path) -> None:
    path = tmp_path / "connector_config.jsonl"
    store = JsonlConfiguredConnectorStore(path)
    for i in range(4):
        store.upsert_config(_connector(f"c{i}"), _fetch(f"t{i}"))
    store.record_fetch_attempt("c0", success=True)
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    # truncated (same inode, shorter)
    path.write_text("".join(lines[:2]), encoding="utf-8")
    _assert_equals_full_replay(store, path)
    assert len(store.list_configs()) == 2
    # replaced by a different file with more content
    replacement = tmp_path / "new.jsonl"
    replacement.write_text("".join(lines), encoding="utf-8")
    os.replace(replacement, path)
    _assert_equals_full_replay(store, path)
    assert len(store.list_configs()) == 4
    # same size rewritten in place with different content
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("t1", "x1"), encoding="utf-8")
    _assert_equals_full_replay(store, path)
    assert store.get_config("c1").fetch["next_watermark"] == "x1"
    # removed
    path.unlink()
    assert store.list_configs() == []
    _assert_equals_full_replay(store, path)


def test_unterminated_tail_matches_full_replay_and_invalid_rows_still_fail(tmp_path: Path) -> None:
    path = tmp_path / "connector_config.jsonl"
    store = JsonlConfiguredConnectorStore(path)
    store.upsert_config(_connector("c0"), _fetch("t0"))
    JsonlConfiguredConnectorStore(path).upsert_config(_connector("c1"), _fetch("t1"))
    path.write_bytes(path.read_bytes().rstrip(b"\n"))  # last row without newline
    _assert_equals_full_replay(store, path)
    with path.open("ab") as handle:
        handle.write(b"\n")
    _assert_equals_full_replay(store, path)
    with path.open("ab") as handle:
        handle.write(b"{not json}\n")
    with pytest.raises(SourceEvidenceError):
        store.list_configs()
    with pytest.raises(SourceEvidenceError):
        JsonlConfiguredConnectorStore(path)


def test_returned_values_do_not_alias_the_cache(tmp_path: Path) -> None:
    path = tmp_path / "connector_config.jsonl"
    store = JsonlConfiguredConnectorStore(path)
    store.upsert_config(_connector("c0"), _fetch("t0"))
    store.record_fetch_attempt("c0", success=True, collector_outcome={"k": [1]})
    store.get_config("c0").fetch["records"].append({"mutated": True})
    store.get_fetch_state("c0")["collector_outcome"]["k"].append(2)
    store.list_configs()[0].fetch["records"].clear()
    _assert_equals_full_replay(store, path)
