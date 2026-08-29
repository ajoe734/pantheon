"""
Tests for Agora Decision Journal merge patch, versioning, diff, and idempotency.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import uuid

import pytest

from services.agora.service import AgoraWriteService
from services.agora.store import AgoraStore


def _hash_payload(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def test_decision_journal_create_and_patch_persistence() -> None:
    entry_id = f"entry-{uuid.uuid4().hex[:8]}"

    # Write initial journal entry with Store 1
    store1 = AgoraStore()
    entry = store1.create_journal_entry(
        entry_id=entry_id,
        title="Initial Trading Hypothesis",
        body="Momentum factor expected to outperform value in current regime.",
        tags=["momentum", "macro"],
        linked_strategy_ids=["strat-001"],
        linked_persona_ids=["persona-a"],
        visibility="public",
        actor_id="trader-alice",
    )
    assert entry.id == entry_id
    assert entry.version == 1

    # First merge patch
    patch1 = {"title": "Updated Trading Hypothesis", "tags": ["momentum", "growth"]}
    hash1 = _hash_payload(patch1)
    res1 = store1.patch_journal_entry(
        entry_id,
        patch=patch1,
        actor_id="trader-alice",
        correlation_id="corr-001",
        idempotency_key="idem-key-001",
        request_hash=hash1,
    )
    assert res1 is not None
    assert res1.status == "updated"
    assert res1.entry["title"] == "Updated Trading Hypothesis"
    assert res1.entry["tags"] == ["momentum", "growth"]
    assert res1.entry["version"] == 2
    assert "title" in res1.audit["diff"]["changedFields"]
    assert "tags" in res1.audit["diff"]["changedFields"]

    # Replay with same idempotency key and same hash
    replay_res = store1.patch_journal_entry(
        entry_id,
        patch=patch1,
        actor_id="trader-alice",
        correlation_id="corr-001",
        idempotency_key="idem-key-001",
        request_hash=hash1,
    )
    assert replay_res is not None
    assert replay_res.status == "replayed"
    assert replay_res.entry["version"] == 2

    # Conflict with same idempotency key but different hash
    conflict_res = store1.patch_journal_entry(
        entry_id,
        patch={"title": "Different Title"},
        actor_id="trader-alice",
        correlation_id="corr-001",
        idempotency_key="idem-key-001",
        request_hash="different-hash-value",
    )
    assert conflict_res is not None
    assert conflict_res.status == "conflict"

    # Second merge patch with new idempotency key
    patch2 = {"body": "Revised: Momentum fading, transitioning to defensive posture."}
    hash2 = _hash_payload(patch2)
    res2 = store1.patch_journal_entry(
        entry_id,
        patch=patch2,
        actor_id="trader-alice",
        correlation_id="corr-002",
        idempotency_key="idem-key-002",
        request_hash=hash2,
    )
    assert res2 is not None
    assert res2.status == "updated"
    assert res2.entry["version"] == 3

    # Destroy Store 1
    del store1

    # Read with Fresh Store 2
    store2 = AgoraStore()
    fresh_entry = store2.get_journal_entry(entry_id)
    assert fresh_entry is not None
    assert fresh_entry.title == "Updated Trading Hypothesis"
    assert fresh_entry.body == "Revised: Momentum fading, transitioning to defensive posture."
    assert fresh_entry.tags == ["momentum", "growth"]
    assert fresh_entry.version == 3
