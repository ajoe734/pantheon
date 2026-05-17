from __future__ import annotations

import sys
from pathlib import Path

import pytest


SERVICE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SERVICE_DIR))

from policy_lineage import (  # noqa: E402
    InMemoryLineageReadStore,
    JsonlLineageReadStore,
    SEMANTIC_EDGE_ID,
    SOURCE_TYPE,
    TARGET_TYPE,
    StaticPersonaPolicyResolver,
    UnknownPersonaPolicyError,
    record_commit,
)


def test_record_commit_writes_one_policy_lineage_edge_per_teaching_event(tmp_path: Path) -> None:
    store_path = tmp_path / "policy-lineage.jsonl"
    store = JsonlLineageReadStore(store_path)
    event_ids = ["tevt-001", "tevt-002", "tevt-003"]

    edges = record_commit(
        "trn-005-session",
        event_ids,
        "persona-alpha",
        store=store,
        persona_policy_resolver=StaticPersonaPolicyResolver(
            {"persona-alpha": "persona-policy-artifact-alpha-v2"}
        ),
        commit_at="2026-05-16T23:30:00Z",
    )

    assert len(edges) == len(event_ids)
    assert store_path.exists()
    reloaded_edges = JsonlLineageReadStore(store_path).list_edges()
    assert reloaded_edges == edges

    assert [edge["teaching_event_id"] for edge in edges] == event_ids
    for edge in edges:
        assert edge["semantic_edge_id"] == SEMANTIC_EDGE_ID
        assert edge["edge_type"] == SEMANTIC_EDGE_ID
        assert edge["source"] == SOURCE_TYPE
        assert edge["source_type"] == SOURCE_TYPE
        assert edge["source_id"] == "trn-005-session"
        assert edge["producer"] == "trn-005-session"
        assert edge["producer_id"] == "trn-005-session"
        assert edge["target"] == TARGET_TYPE
        assert edge["target_type"] == TARGET_TYPE
        assert edge["target_id"] == "persona-policy-artifact-alpha-v2"
        assert edge["persona_id"] == "persona-alpha"
        assert edge["teaching_event_ids"] == event_ids
        assert edge["commit_at"] == "2026-05-16T23:30:00Z"


def test_record_commit_fails_fast_for_unknown_persona() -> None:
    store = InMemoryLineageReadStore()
    resolver = StaticPersonaPolicyResolver({"persona-alpha": "persona-policy-artifact-alpha-v2"})

    with pytest.raises(UnknownPersonaPolicyError):
        record_commit(
            "trn-005-session",
            ["tevt-001"],
            "persona-missing",
            store=store,
            persona_policy_resolver=resolver,
            commit_at="2026-05-16T23:30:00Z",
        )
    assert store.list_edges() == []
